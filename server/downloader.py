"""yt-dlp wrapper: one job per download, progress tracked in memory.

Las descargas se serializan (un worker) y se separan con una pausa aleatoria,
según el perfil de `throttle`. Ver README, sección "Ritmo de descarga".
"""

import os
import random
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
from urls import normalize


# Estados de los que un job ya no sale. Se listan una sola vez: estaban
# repetidos en tres métodos, y agregar `upload_error` habría dejado esos
# jobs sin poder podarse ni limpiarse desde el popup.
TERMINALES = ("done", "error", "upload_error", "cancelled")


class Cancelled(Exception):
    """Raised from inside a progress hook to abort a running download."""


@dataclass
class Job:
    id: str
    url: str
    # queued | waiting | running | postprocessing | uploading | done
    # | error | upload_error | cancelled
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
        cookiefile = write_netscape_cookiefile(cookies) if cookies else None
        opts = self._base_opts(cookiefile)
        opts["skip_download"] = True
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(normalize(url), download=False)
        finally:
            discard(cookiefile)

        # A playlist/course extracts as a container with an `entries` list.
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            return {
                "kind": "playlist",
                "title": info.get("title"),
                "count": len(entries),
                "entries": [
                    {"title": e.get("title"), "duration": e.get("duration"), "url": e.get("webpage_url")}
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
        # `title` viene del probe cuando expandimos una playlist: así el popup
        # muestra el nombre de la clase desde el primer segundo, en vez de una
        # URL cruda hasta que yt-dlp llegue a leer los metadatos.
        job = Job(
            id=uuid.uuid4().hex[:12], url=url, title=title,
            position=position, total=total, subfolder=subfolder,
        )
        if cookies:
            job._cookiefile = write_netscape_cookiefile(cookies)
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
            return {"kind": "video", "count": 1, "jobs": [job.public()]}

        entradas = info.get("entries") or []
        # Las clases sin URL se descartan ANTES de numerar: si no, la
        # numeración saltearía números y dejaría huecos en los nombres.
        utiles = [e for e in entradas if e.get("url")]

        # Todo el curso a una carpeta propia. Si el título no da un nombre
        # usable, `carpeta_para` devuelve None y las clases van a la raíz.
        carpeta = carpeta_para(info.get("title"))

        creados = []
        for indice, entrada in enumerate(utiles, start=1):
            creados.append(
                self.enqueue(
                    entrada["url"], cookies, quality, subtitles, audio_only,
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
            "skipped": len(info.get("entries") or []) - len(creados),
            "jobs": creados,
        }

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
        job = self.get(job_id)
        if not job or job.status in TERMINALES:
            return False
        job._cancel.set()
        if job.status == "queued":
            # Never started, so no hook will ever fire to notice the flag.
            job.status = "cancelled"
            discard(job._cookiefile)
            job._cookiefile = None
        return True

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
            discard(job._cookiefile)
            job._cookiefile = None
            return

        if not self._await_turn(job):
            job.status = "cancelled"
            discard(job._cookiefile)
            job._cookiefile = None
            self._schedule_next()
            return

        job.status = "running"
        tuning = throttle_cfg.get()
        opts = self._base_opts(job._cookiefile)
        opts.update(
            {
                "format": self._format_selector(quality, audio_only),
                # La subcarpeta va en la plantilla: yt-dlp crea los directorios
                # que falten al escribir el archivo.
                "outtmpl": os.path.join(
                    self.download_dir, job.subfolder or "", self._outtmpl(job)
                ),
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

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(normalize(job.url), download=True)
            job.title = job.title or (info.get("title") if isinstance(info, dict) else None)
            if job.status != "cancelled":
                self._subir(job)
            if job.status not in ("cancelled", "upload_error"):
                job.status = "done"
                job.percent = 100.0
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
            job.wait_seconds = None
            self._schedule_next()

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

    def _schedule_next(self) -> None:
        """Corre el reloj de la cola: nada arranca antes de este momento."""
        tuning = throttle_cfg.get()
        with self._gate:
            self._next_start_at = time.monotonic() + random.uniform(
                tuning.gap_min, tuning.gap_max
            )

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

    @staticmethod
    def _explain(message: str) -> str:
        """Turn the most common yt-dlp failures into something actionable."""
        low = message.lower()
        if "drm" in low or "widevine" in low:
            return (
                "El video está protegido con DRM (Widevine). yt-dlp no puede "
                "descifrarlo — no hay forma de descargarlo con esta herramienta."
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
