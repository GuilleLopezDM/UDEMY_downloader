"""yt-dlp wrapper: one job per download, progress tracked in memory.

Las descargas se serializan (un worker) y se separan con una pausa aleatoria,
según el perfil de `throttle`. Ver README, sección "Ritmo de descarga".
"""

import os
import random
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import yt_dlp

import throttle as throttle_cfg
import upload_config
import uploader
from cookies import discard, write_netscape_cookiefile
from naming import carpeta_para
from urls import motivo_no_soportado, normalize


# Estados de los que un job ya no sale. Se listan una sola vez: estaban
# repetidos en tres métodos, y agregar `upload_error` habría dejado esos
# jobs sin poder podarse ni limpiarse desde el popup.
TERMINALES = ("done", "skipped", "error", "upload_error", "cancelled")

# Parámetros que delatan una URL de CDN firmada. Si aparecen, lo que tenemos no
# es la página de la clase sino el media: vence en horas, el extractor del sitio
# no la reconoce, y el `download_archive` terminaría guardando la clave de otro
# extractor — con lo cual "reencolar baja solo lo que falta" deja de ser cierto.
_FIRMA_EN_URL = ("signature=", "policy=", "key-pair-id=", "x-amz-signature=")


class Cancelled(Exception):
    """Raised from inside a progress hook to abort a running download."""


def url_de_entrada(entrada: dict) -> str | None:
    """URL de la clase a encolar, o None si la entrada no sirve.

    `url` es la URL de la *página* solo mientras la entrada no se haya resuelto
    (`_type` url / url_transparent), que es justo lo que garantiza el
    `extract_flat` de `probe()`. En ese estado lleva adjunto el course_id vía
    `smuggle_url`, y ese dato SÓLO viaja ahí: sin él cada clase tiene que
    re-deducir el curso leyendo una página que en los portales de Udemy Business
    no existe -> 403.

    Si la entrada YA está resuelta, `url` es lo contrario de lo que queremos:
    `YoutubeDL.py:1983` excluye `url` al propagar una entrada `url_transparent`
    y `:3155` la pisa con la URL del formato elegido. Ahí sirven `original_url`
    o `webpage_url`.

    No se filtra por host a propósito. La herramienta acepta cualquier sitio que
    soporte yt-dlp; exigir `udemy.com` dejaría una playlist de otro sitio en
    `count=0, skipped=N`, sin error y sin nada encolado.
    """
    tipo = entrada.get("_type")
    if tipo in ("url", "url_transparent"):
        destino = entrada.get("url") or entrada.get("webpage_url")
    else:
        destino = (
            entrada.get("original_url")
            or entrada.get("webpage_url")
            or entrada.get("url")
        )
    if not destino:
        return None
    bajo = destino.lower()
    if any(marca in bajo for marca in _FIRMA_EN_URL):
        return None
    return destino


@dataclass
class Job:
    id: str
    url: str
    # queued | waiting | running | postprocessing | uploading | done
    # | skipped | error | upload_error | cancelled
    status: str = "queued"
    title: str | None = None
    percent: float = 0.0
    speed: str | None = None
    eta: int | None = None
    filepath: str | None = None
    error: str | None = None
    wait_seconds: int | None = None
    # Posición dentro del curso, 1-based. None para una descarga suelta.
    position: int | None = None
    total: int | None = None
    # Subcarpeta del curso. None = va a la raíz de downloads/.
    subfolder: str | None = None
    # Ruta en el remoto de rclone, si se subió.
    remote_path: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    # Las cookies viajan como DATOS hasta que el job arranca. Materializarlas al
    # encolar significaba escribir una copia de la sesión viva por clase —200 en
    # un curso— y dejarlas en disco durante las horas que dura la cola.
    _cookies: list[dict] | None = field(default=None, repr=False)
    _cookiefile: str | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        # Built by hand rather than with dataclasses.asdict(): asdict deepcopies
        # every field, and _cancel is a threading.Event holding an uncopyable lock.
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "title": self.title,
            "percent": self.percent,
            "speed": self.speed,
            "eta": self.eta,
            "filepath": self.filepath,
            "error": self.error,
            "wait_seconds": self.wait_seconds,
            "position": self.position,
            "total": self.total,
            "subfolder": self.subfolder,
            "remote_path": self.remote_path,
        }


class JobManager:
    # Cuántos jobs terminados se conservan para mostrar en el historial del popup.
    MAX_FINISHED = 50

    # Windows corta las rutas completas en 260 caracteres. `LongPathsEnabled` lo
    # levanta para los procesos con el manifest correspondiente —Python lo
    # tiene—, pero **ffmpeg no**, y ffmpeg es quien hace el merge del selector
    # por defecto. Resultado sin este tope: los dos streams se bajan enteros y
    # falla el último paso. yt-dlp no mitiga nada, le pasa la ruta cruda con
    # prefijo `file:` (postprocessor/ffmpeg.py:369-377).
    LARGO_MAXIMO_RUTA = 260
    # Peor sufijo temporal que yt-dlp llega a agregar: `.f137.mp4.part`.
    MARGEN_SUFIJO = 20
    # Piso: por debajo de esto el nombre deja de identificar a la clase.
    LARGO_MINIMO_NOMBRE = 40

    # Pausa para los trabajos que yt-dlp salteó por el `download_archive`. No es
    # cero a propósito: el atajo pre-extracción de yt-dlp (`YoutubeDL.py:1713`)
    # compara el id que sale de la URL contra un archive que guarda otro id (en
    # Udemy, lecture id vs. `asset['id']`), así que cada clase salteada IGUAL
    # hace su request a la API antes de descubrir el skip. Con pausa cero,
    # reencolar un curso de 200 serían ~180 requests en ráfaga — el mismo patrón
    # que todo este módulo existe para evitar.
    GAP_SALTEADO_MIN = 2.0
    GAP_SALTEADO_MAX = 5.0

    def __init__(self, download_dir: str, max_workers: int = 1):
        self.download_dir = os.path.abspath(os.path.expanduser(download_dir))
        os.makedirs(self.download_dir, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Un solo worker a propósito: dos descargas en paralelo duplican el
        # ritmo de requests, que es justo lo que el throttling busca evitar.
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._gate = threading.Lock()
        self._next_start_at = 0.0  # time.monotonic() antes del cual no arranca nada

    # ---- public API -------------------------------------------------

    def probe(self, url: str, cookies: list[dict] | None = None) -> dict[str, Any]:
        """Fetch metadata without downloading. Used to preview a URL."""
        # Adelantarse a yt-dlp con las URLs que no dan video: si no, un quiz
        # matchea igual `UdemyCourseIE` y sale como un 403 indescifrable.
        motivo = motivo_no_soportado(url)
        if motivo:
            raise ValueError(motivo)
        cookiefile = write_netscape_cookiefile(cookies) if cookies else None
        opts = self._base_opts(cookiefile)
        opts["skip_download"] = True
        # Sin esto yt-dlp resuelve las N clases una por una, y eso rompe tres
        # cosas a la vez:
        #  * una ráfaga de N requests a la API del sitio, sin pasar por el gate
        #    de ritmo — el patrón exacto que `throttle` existe para evitar;
        #  * con `ignoreerrors: False`, UNA clase rota (DRM, asset borrado)
        #    aborta la enumeración entera y no se encola nada;
        #  * las entradas dejan de tener la URL de la clase y pasan a tener la
        #    del formato elegido, firmada y con vencimiento (ver `url_de_entrada`).
        # Se pierde `duration` por clase, que la extensión no muestra.
        opts["extract_flat"] = "in_playlist"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(normalize(url), download=False)
        finally:
            discard(cookiefile)

        # `extract_info` puede devolver None: `YoutubeDL.py:1713-1719` hace
        # `break` sin `return` cuando el id de la URL ya está en el archive.
        if not isinstance(info, dict):
            raise ValueError(
                "yt-dlp no devolvió información para esa URL. "
                "Puede que ya esté en el registro de descargados."
            )

        # A playlist/course extracts as a container with an `entries` list.
        # `multi_video` es el mismo caso: un contenedor con varias partes que
        # sin el fan-out bajaría todo seguido, salteándose el gate de ritmo.
        if info.get("_type") in ("playlist", "multi_video"):
            entries = [e for e in (info.get("entries") or []) if e]
            return {
                "kind": "playlist",
                "title": info.get("title"),
                "count": len(entries),
                "entries": [
                    {
                        "title": e.get("title"),
                        "duration": e.get("duration"),
                        # `_type` viaja para que `url_de_entrada` pueda decidir
                        # qué campo de URL es el bueno; sin él la guarda no
                        # tendría con qué.
                        "_type": e.get("_type"),
                        "url": e.get("url"),
                        "webpage_url": e.get("webpage_url"),
                        "original_url": e.get("original_url"),
                    }
                    for e in entries
                ],
            }
        return {
            "kind": "video",
            "title": info.get("title"),
            "duration": info.get("duration"),
            "extractor": info.get("extractor_key"),
            "formats": self._summarize_formats(info.get("formats") or []),
        }

    def enqueue(
        self,
        url: str,
        cookies: list[dict] | None = None,
        quality: str = "best",
        subtitles: bool = False,
        audio_only: bool = False,
        title: str | None = None,
        position: int | None = None,
        total: int | None = None,
        subfolder: str | None = None,
    ) -> Job:
        motivo = motivo_no_soportado(url)
        if motivo:
            raise ValueError(motivo)
        # `title` viene del probe cuando expandimos una playlist: así el popup
        # muestra el nombre de la clase desde el primer segundo, en vez de una
        # URL cruda hasta que yt-dlp llegue a leer los metadatos.
        job = Job(
            id=uuid.uuid4().hex[:12], url=url, title=title,
            position=position, total=total, subfolder=subfolder,
        )
        # Guardadas, no escritas: el jar se materializa en `_run`.
        job._cookies = list(cookies) if cookies else None
        with self._lock:
            self._jobs[job.id] = job
            self._prune_finished()
        self._pool.submit(self._run, job, quality, subtitles, audio_only)
        return job

    def enqueue_playlist(
        self,
        url: str,
        cookies: list[dict] | None = None,
        quality: str = "best",
        subtitles: bool = False,
        audio_only: bool = False,
    ) -> dict[str, Any]:
        """Expande un curso/playlist a un job por clase.

        El fan-out no es cosmético: la pausa entre trabajos de `throttle` sólo
        se aplica ENTRE jobs. Un curso entero como un único job bajaría las 40
        clases casi seguidas, saltándose justamente el freno que se le pidió.

        Además da progreso y cancelación por clase, y permite reencolar el curso
        para que baje sólo lo que falte (ver `archive_path`).
        """
        info = self.probe(url, cookies)

        if info.get("kind") != "playlist":
            job = self.enqueue(url, cookies, quality, subtitles, audio_only)
            return {"kind": "video", "count": 1, "skipped": 0, "jobs": [job.public()]}

        entradas = info.get("entries") or []
        # Las clases sin URL usable se descartan ANTES de numerar: si no, la
        # numeración saltearía números y dejaría huecos en los nombres.
        utiles = [(e, u) for e in entradas if (u := url_de_entrada(e))]

        # Todo el curso a una carpeta propia. Si el título no da un nombre
        # usable, `carpeta_para` devuelve None y las clases van a la raíz.
        carpeta = carpeta_para(info.get("title"))

        creados = []
        for indice, (entrada, destino) in enumerate(utiles, start=1):
            # La URL va tal cual la eligió `url_de_entrada`: lleva el course_id
            # adjunto y reescribirla lo destruye. El host www.udemy.com no
            # molesta porque con el course_id adjunto el extractor ni siquiera
            # pide la página.
            creados.append(
                self.enqueue(
                    destino, cookies, quality, subtitles, audio_only,
                    title=entrada.get("title"),
                    position=indice, total=len(utiles),
                    subfolder=carpeta,
                ).public()
            )

        return {
            "kind": "playlist",
            "title": info.get("title"),
            "folder": carpeta,
            "count": len(creados),
            "skipped": len(entradas) - len(creados),
            "espacio_libre_gb": self.espacio_libre_gb(),
            "jobs": creados,
        }

    def espacio_libre_gb(self) -> float | None:
        """GB libres en el disco de las descargas, o None si no se puede saber.

        Un curso son decenas de GB y el destino puede ser un disco chico. Es un
        dato, no un bloqueo: quién decide es el usuario.
        """
        try:
            return round(shutil.disk_usage(self.download_dir).free / 1024**3, 1)
        except OSError:
            return None

    def _prune_finished(self) -> None:
        """Descarta los jobs terminados más viejos por encima del tope.

        Sin esto el diccionario sólo se vaciaba con el botón "Limpiar
        terminadas": en una sesión larga acumulaba en memoria la URL y el título
        de todo lo descargado. Se llama con `self._lock` ya tomado.
        """
        terminados = [
            jid for jid, j in self._jobs.items()
            if j.status in TERMINALES
        ]
        # dict conserva el orden de inserción, así que los primeros son los más viejos.
        for jid in terminados[: max(0, len(terminados) - self.MAX_FINISHED)]:
            del self._jobs[jid]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [j.public() for j in self._jobs.values()]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status in TERMINALES:
                return False
            job._cancel.set()
            if job.status == "queued":
                # Never started, so no hook will ever fire to notice the flag.
                job.status = "cancelled"
                job._cookies = None
        return True

    def cancel_all(self) -> int:
        """Cancela todo lo que no haya terminado. Devuelve cuántos alcanzó.

        Cancelar un curso de 200 clases de a una es 200 clics, cada uno sobre
        una lista que se repinta sola.
        """
        with self._lock:
            pendientes = [j for j in self._jobs.values() if j.status not in TERMINALES]
            for job in pendientes:
                job._cancel.set()
                if job.status == "queued":
                    job.status = "cancelled"
                    job._cookies = None
        return len(pendientes)

    def shutdown(self) -> None:
        """Corta la cola entera. Se llama al apagar el servidor.

        Sin esto, Ctrl-C con un curso encolado NO termina el proceso: el
        `ThreadPoolExecutor` no es daemon y su `atexit` espera a que se drenen
        los 200 futures, cada uno con su pausa de throttle. O sea, horas —
        justo cuando el usuario apretó Ctrl-C porque quería que pare.
        """
        self.cancel_all()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def clear_finished(self) -> int:
        with self._lock:
            finished = [k for k, j in self._jobs.items() if j.status in TERMINALES]
            for k in finished:
                del self._jobs[k]
        return len(finished)

    # ---- internals --------------------------------------------------

    def _base_opts(self, cookiefile: str | None) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "ignoreerrors": False,
            # Course pages hand back long titles with slashes and colons.
            "restrictfilenames": False,
            "windowsfilenames": True,
        }
        if cookiefile:
            opts["cookiefile"] = cookiefile
        return opts

    @property
    def archive_path(self) -> str:
        """Registro de lo ya descargado, para poder cortar y retomar.

        Un curso de 40 clases con pausas de 20-60s son horas. Sin este archivo,
        cualquier corte —reinicio, cierre de la terminal, corte de luz— obliga a
        bajar todo de nuevo: los jobs viven en memoria y no sobreviven.

        Vive junto a las descargas a propósito: si se borra o se mueve esa
        carpeta, el registro se va con ella y vuelve a bajar todo, que es el
        comportamiento correcto.
        """
        return os.path.join(self.download_dir, ".video-dl-descargados.txt")

    @staticmethod
    def _outtmpl(job: Job) -> str:
        """Plantilla de nombre de archivo, con prefijo de orden si es un curso.

        Devuelve SOLO el nombre, sin directorio: el directorio va por la opción
        `paths` de yt-dlp. No es un detalle de estilo — ver `_recorte_de_nombre`.

        Sin el prefijo, una carpeta con 40 clases queda ordenada alfabéticamente
        por título y no hay forma de saber en qué orden mirarlas.

        No se usa `%(playlist_index)s` de yt-dlp porque el fan-out convierte cada
        clase en una descarga independiente: para yt-dlp no hay playlist, así que
        ese campo vendría vacío. El número lo ponemos nosotros.
        """
        base = "%(title)s [%(id)s].%(ext)s"
        if job.position is None:
            return base
        # El ancho sale del total: 40 clases -> "01", 150 clases -> "001".
        ancho = max(2, len(str(job.total or job.position)))
        return f"{job.position:0{ancho}d} - {base}"

    @classmethod
    def _recorte_de_nombre(cls, directorio: str) -> int:
        """Cuántos caracteres puede ocupar el nombre del archivo.

        `trim_file_name` de yt-dlp recorta el string COMPLETO que produce la
        plantilla (`YoutubeDL.py:1546-1549`), así que solo sirve con un outtmpl
        relativo: con uno absoluto cortaría el directorio y el archivo iría a
        parar a cualquier lado. Por eso el directorio viaja en `paths` —que se
        aplica DESPUÉS del recorte, en `get_output_path` (`:1216-1223`)— y el
        cálculo se hace contra el destino final, subcarpeta del curso incluida.

        Ojo con hacerlo a medias: si el outtmpl queda absoluto, yt-dlp **ignora
        `paths`** con un warning (`:1570`) que `no_warnings: True` se traga. Todo
        parece andar y el recorte no se aplica.
        """
        if os.name == "nt":
            disponible = cls.LARGO_MAXIMO_RUTA - len(directorio) - 1 - cls.MARGEN_SUFIJO
        else:
            # POSIX no limita la ruta completa, pero sí cada componente a 255.
            disponible = 255 - cls.MARGEN_SUFIJO
        return max(cls.LARGO_MINIMO_NOMBRE, disponible)

    @staticmethod
    def _format_selector(quality: str, audio_only: bool) -> str:
        if audio_only:
            return "bestaudio/best"
        if quality in ("best", "", None):
            return "bestvideo*+bestaudio/best"
        # quality is a max height, e.g. "720"
        return f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"

    @staticmethod
    def _summarize_formats(formats: list[dict]) -> list[dict]:
        seen, out = set(), []
        for f in formats:
            h = f.get("height")
            if not h or h in seen:
                continue
            seen.add(h)
            out.append({"height": h, "ext": f.get("ext"), "note": f.get("format_note")})
        return sorted(out, key=lambda x: x["height"], reverse=True)

    def _run(self, job: Job, quality: str, subtitles: bool, audio_only: bool) -> None:
        if job._cancel.is_set():
            job.status = "cancelled"
            job._cookies = None
            return

        if not self._await_turn(job):
            job.status = "cancelled"
            job._cookies = None
            # No descargó nada ni tocó la red: no hay por qué frenar al siguiente.
            self._schedule_next(reducido=True)
            return

        job.status = "running"
        # Solo se pone en True si yt-dlp salteó la descarga por el archive.
        salteado = False
        tuning = throttle_cfg.get()

        try:
            # El jar se materializa recién acá. Encolar un curso escribía una
            # copia de la sesión viva por clase —200 archivos de golpe— y todas
            # quedaban en disco hasta que su job terminara, o sea horas. Así hay
            # como mucho una a la vez.
            if job._cookies:
                job._cookiefile = write_netscape_cookiefile(job._cookies)

            destino = os.path.join(self.download_dir, job.subfolder or "")
            opts = self._base_opts(job._cookiefile)
            opts.update(
                {
                    "format": self._format_selector(quality, audio_only),
                    # El directorio va acá y NO en el outtmpl: ver
                    # `_recorte_de_nombre`. yt-dlp crea lo que falte al escribir.
                    "paths": {"home": destino},
                    "outtmpl": self._outtmpl(job),
                    "trim_file_name": self._recorte_de_nombre(destino),
                    "progress_hooks": [lambda d: self._on_progress(job, d)],
                    "postprocessor_hooks": [lambda d: self._on_postprocess(job, d)],
                }
            )
            # Ritmo: tope de velocidad, pausa aleatoria antes de cada video de una
            # playlist, y un solo fragmento a la vez (HLS/DASH abren varios si no).
            if tuning.rate_limit_kbps > 0:
                opts["ratelimit"] = tuning.rate_limit_kbps * 1024
            if tuning.video_sleep_max > 0:
                opts["sleep_interval"] = tuning.video_sleep_min
                opts["max_sleep_interval"] = tuning.video_sleep_max
            opts["concurrent_fragment_downloads"] = 1
            # Con throttling activo un 429 es más raro, pero si llega conviene
            # reintentar en vez de dar el job por perdido.
            opts["retries"] = 10
            opts["fragment_retries"] = 10
            # Lo ya descargado se saltea: reencolar un curso entero sólo baja lo
            # que falta. Ver `archive_path`.
            opts["download_archive"] = self.archive_path

            if subtitles:
                opts.update({"writesubtitles": True, "subtitleslangs": ["all"], "writeautomaticsub": False})
            if audio_only:
                opts["postprocessors"] = [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
                ]
            else:
                opts["merge_output_format"] = "mp4"

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(normalize(job.url), download=True)

            if isinstance(info, dict):
                job.title = job.title or info.get("title")

            # `__real_download` NO está en el nivel de arriba de lo que devuelve
            # `extract_info`: se setea sobre `new_info`, que es una COPIA
            # (`YoutubeDL.py:3118`) que termina dentro de `requested_downloads`
            # (`:3150`). Buscarlo arriba da siempre None —o sea "no descargó
            # nunca"— y eso apagaría la pausa entre TODOS los trabajos.
            # Cuando el archive machea, `process_video_result` corta en `:3041`
            # antes de elegir formatos, así que `requested_downloads` ni existe.
            descargas = (info if isinstance(info, dict) else {}).get("requested_downloads") or []
            bajo_algo = any(d.get("__real_download") for d in descargas)

            if job.status != "cancelled":
                self._subir(job)
            if job.status not in ("cancelled", "upload_error"):
                if bajo_algo:
                    job.status = "done"
                    job.percent = 100.0
                else:
                    # Salteada por el archive. Decir "Listo" al 100% mentía sobre
                    # un archivo que puede no existir: si el usuario lo borró, el
                    # registro igual lo da por descargado.
                    salteado = True
                    job.status = "skipped"
                    job.percent = 0.0
        except Cancelled:
            job.status = "cancelled"
        except yt_dlp.utils.DownloadError as e:
            job.status = "error"
            job.error = self._explain(str(e))
        except Exception as e:  # noqa: BLE001 - surface anything else to the UI
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            discard(job._cookiefile)
            job._cookiefile = None
            job._cookies = None
            job.wait_seconds = None
            self._schedule_next(reducido=salteado)

    def _subir(self, job: Job) -> None:
        """Sube el archivo al remoto configurado, y opcionalmente borra el local.

        Ante cualquier falla deja el job en `upload_error` y **no borra nada**:
        el video está bajado y perderlo por un error de red sería peor que
        quedarse sin subir.
        """
        conf = upload_config.get()
        if not conf.activo:
            return

        if not job.filepath or not os.path.isfile(job.filepath):
            # Sin archivo no hay nada que subir. Pasa cuando `download_archive`
            # salteó la descarga porque ya la teníamos de una corrida anterior.
            return

        job.status = "uploading"
        job.percent = 0.0
        try:
            job.remote_path = uploader.subir(
                job.filepath,
                conf.remote,
                conf.folder,
                job.subfolder,
                on_progress=lambda p: setattr(job, "percent", float(p)),
                cancelado=job._cancel,
            )
        except uploader.ErrorDeSubida as e:
            if job._cancel.is_set():
                # Cancelar es una decisión del usuario, no una falla. Dejarlo en
                # `upload_error` con un mensaje de error hacía pensar que algo
                # se había roto o perdido.
                job.status = "cancelled"
                return
            job.status = "upload_error"
            job.error = f"Se descargó bien, pero falló la subida: {e}"
            return

        if conf.delete_after:
            try:
                os.unlink(job.filepath)
                job.filepath = None
            except OSError as e:
                # El objetivo —tener el archivo en el remoto— se cumplió, así
                # que esto es un aviso, no un fallo del job.
                job.error = f"Subido, pero no se pudo borrar el local: {e}"

    def _await_turn(self, job: Job) -> bool:
        """Bloquea hasta que pase el hueco entre trabajos.

        Devuelve False si el job se canceló mientras esperaba. Duerme de a un
        segundo para que el contador de la UI avance y el cancelar responda.
        """
        while True:
            if job._cancel.is_set():
                return False
            with self._gate:
                remaining = self._next_start_at - time.monotonic()
            if remaining <= 0:
                job.wait_seconds = None
                return True
            job.status = "waiting"
            job.wait_seconds = max(0, int(round(remaining)))
            time.sleep(min(1.0, remaining))

    def _schedule_next(self, reducido: bool = False) -> None:
        """Corre el reloj de la cola: nada arranca antes de este momento.

        `reducido` es para los trabajos que no descargaron nada. Sin esto,
        reencolar un curso donde 180 de 200 clases ya están pagaba la pausa
        completa por cada una: 180 x 20-60s son ~2 horas de espera pura.
        """
        tuning = throttle_cfg.get()
        if reducido:
            # Nunca más lento que lo configurado: con el preset "rapido"
            # (0-2s) un piso de 2-5s sería un freno, no un atajo.
            minimo = min(self.GAP_SALTEADO_MIN, tuning.gap_min)
            maximo = min(self.GAP_SALTEADO_MAX, tuning.gap_max)
        else:
            minimo, maximo = tuning.gap_min, tuning.gap_max
        maximo = max(minimo, maximo)
        with self._gate:
            self._next_start_at = time.monotonic() + random.uniform(minimo, maximo)

    def _on_progress(self, job: Job, d: dict) -> None:
        if job._cancel.is_set():
            raise Cancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            job.percent = round(done / total * 100, 1) if total else 0.0
            job.speed = d.get("_speed_str", "").strip() or None
            job.eta = d.get("eta")
            info = d.get("info_dict") or {}
            job.title = job.title or info.get("title")
        elif d.get("status") == "finished":
            job.percent = 100.0
            job.speed = None
            job.filepath = d.get("filename")

    def _on_postprocess(self, job: Job, d: dict) -> None:
        if job._cancel.is_set():
            raise Cancelled()
        if d.get("status") == "started":
            job.status = "postprocessing"
        elif d.get("status") == "finished":
            info = d.get("info_dict") or {}
            job.filepath = info.get("filepath") or job.filepath


    @classmethod
    def _explain(cls, message: str) -> str:
        """Alias histórico. La lógica vive en `explicar`, a nivel de módulo,
        porque `app.py` la necesita para los errores de /probe."""
        return explicar(message)


# yt-dlp colorea su salida con secuencias ANSI. En una terminal se ven como
# color; en el popup y en un archivo de log quedan como basura tipo
# `\x1b[0;31mERROR:\x1b[0m`, que además estorba al leer el mensaje real.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def explicar(message: str) -> str:
    """Turn the most common yt-dlp failures into something actionable."""
    message = _ANSI.sub("", message).strip()
    low = message.lower()
    if "drm" in low or "widevine" in low:
        return (
            "El video está protegido con DRM (Widevine). yt-dlp no puede "
            "descifrarlo — no hay forma de descargarlo con esta herramienta."
        )
    # 429 y 403 antes que el bloque de login: los dos mencionan sesión y
    # cookies en su texto, así que caían en el mensaje genérico de "logueate"
    # — que para un 429 es directamente el consejo equivocado.
    if "429" in low or "too many requests" in low:
        return (
            "El sitio te está frenando por exceso de pedidos (429). Bajá el "
            "ritmo en Opciones (probá 'Paranoico'), esperá un rato y reencolá "
            "el curso: lo ya descargado no se vuelve a bajar."
        )
    if "403" in low or "forbidden" in low:
        return (
            "El sitio respondió 403. Suele ser una de tres: no estás inscripto "
            "en ese curso, la sesión venció (reabrí el sitio y volvé a "
            "intentar), o el enlace del video ya expiró."
        )
    if "login" in low or "cookies" in low or "account" in low or "private" in low:
        return (
            "Hace falta sesión iniciada. Abrí el sitio en el navegador, "
            "logueate, y volvé a intentar con 'Usar cookies' activado."
        )
    if "ffmpeg" in low or "ffprobe" in low:
        return "Falta ffmpeg. Instalalo y reiniciá el server (ver README)."
    if "unsupported url" in low:
        return "yt-dlp no tiene extractor para este sitio."
    return message
