"""Local HTTP API that fronts yt-dlp for the browser extension.

Binds to 127.0.0.1 only. Any page you visit can also reach a localhost port,
so every endpoint requires a shared token that lives in server/.token and is
pasted into the extension once.

La única excepción es `/health`, público a propósito: la extensión lo consulta
para el indicador de conexión, antes de tener token configurado. Por eso mismo
no devuelve más que `{"ok": true}` — cualquier dato ahí queda sin proteger.
"""

import contextlib
import logging
import os
import secrets
import sys
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import throttle as throttle_cfg
import upload_config
import uploader
from cookies import cookie_dir, summarize, sweep_orphans
from diagnostico import diagnosticar
from downloader import JobManager, explicar
from redaccion import redact_url

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / ".token"
DOWNLOAD_DIR = os.environ.get("VIDEO_DL_DIR", str(HERE.parent / "downloads"))


def _preparar_salida() -> None:
    """Fuerza UTF-8 en stdout/stderr.

    En Windows, `sys.stdout.encoding` es la code page del sistema (cp1252 en un
    equipo en español). A una consola real no molesta porque Python usa
    `WriteConsoleW`, pero **redirigido a un archivo revienta**: el `⚠` del
    banner no existe en cp1252 y el arranque muere con UnicodeEncodeError.
    Y correr el servidor con la salida a un archivo es el caso normal.

    Lo mismo vale para cualquier error de yt-dlp con un título en CJK o con los
    homoglifos que mete `sanitize_filename` (`：`, `⧸`), que tampoco están.
    """
    for flujo in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            flujo.reconfigure(encoding="utf-8", errors="replace")


_preparar_salida()


def load_or_create_token() -> str:
    tok = ""
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not tok:
        tok = secrets.token_urlsafe(32)
        TOKEN_FILE.write_text(tok + "\n", encoding="utf-8")
    # Fuera del `if`: el chmod se aplicaba sólo al crearlo, así que un `.token`
    # que quedara en 644 —copiado, restaurado de un backup, generado por una
    # versión vieja— se quedaba así para siempre. En Windows es casi no-op, pero
    # ahí el archivo vive en el perfil del usuario.
    with contextlib.suppress(OSError):
        os.chmod(TOKEN_FILE, 0o600)
    return tok


TOKEN = load_or_create_token()
# El resultado no se guarda: los módulos exponen `get()` y es de ahí de donde lo
# leen `downloader` y los endpoints. Lo que importa acá es el efecto — dejar la
# config del disco cargada antes de atender el primer request.
throttle_cfg.load()
upload_config.load()
jobs = JobManager(DOWNLOAD_DIR)

# Los jars de cookies se borran en un `finally`, que un SIGKILL —o cerrar la
# ventana de la terminal— nunca ejecuta. Barrer al arrancar es la única red de
# seguridad. Se incluye DOWNLOAD_DIR porque las versiones anteriores a este fix
# los escribían ahí: quien actualice puede tener uno viejo con sesión viva.
# El barrido respeta los jars de un servidor que siga vivo (ver `cookies.py`).
ORPHANS_SWEPT = sweep_orphans(DOWNLOAD_DIR)

# Descargas cortadas que quedaron a medias. NO se borran: yt-dlp las reanuda si
# se vuelve a encolar la clase. Sólo se avisa, porque si no son bytes ocupando
# disco que nadie sabe que están.
EDAD_MINIMA_PARCIAL = 6 * 3600


def parciales_viejos() -> list[str]:
    try:
        raiz = Path(jobs.download_dir)
        ahora = time.time()
        return sorted(
            str(p.relative_to(raiz))
            for p in raiz.rglob("*.part")
            if ahora - p.stat().st_mtime > EDAD_MINIMA_PARCIAL
        )
    except OSError:
        return []


class _SilenciarPolling(logging.Filter):
    """Saca del log de acceso el polling del popup.

    /jobs y /health se consultan una vez por segundo mientras el popup está
    abierto: dejan la consola inservible para diagnosticar, que es justo para
    lo que sirve. Los endpoints que importan (/probe, /download, /settings)
    siguen apareciendo.
    """

    RUIDO = ("/jobs", "/health")

    def filter(self, record: logging.LogRecord) -> bool:
        mensaje = record.getMessage()
        return not any(ruta in mensaje for ruta in self.RUIDO)


logging.getLogger("uvicorn.access").addFilter(_SilenciarPolling())


@contextlib.asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    """Apaga la cola al cerrar el servidor.

    Sin esto, Ctrl-C con un curso encolado no devolvía el prompt: el
    ThreadPoolExecutor no es daemon y su `atexit` esperaba a que se drenaran los
    200 trabajos pendientes, cada uno con su pausa de throttle.
    """
    yield
    jobs.shutdown()


app = FastAPI(title="video-dl", docs_url=None, redoc_url=None, lifespan=ciclo_de_vida)

# Only the extension talks to this API. chrome-extension:// origins are not
# matched by a wildcard list, so allow them by regex.
#
# El regex acepta CUALQUIER id de extensión: otra extensión instalada puede
# llegar al servidor, y ahí quien frena es el token. Si querés cerrarlo del
# todo, poné el id de tu extensión en VIDEO_DL_EXT_ID (se ve en
# chrome://extensions con el modo desarrollador activado).
_EXT_ID = os.environ.get("VIDEO_DL_EXT_ID", "").strip()
_ORIGEN_REGEX = (
    rf"^(chrome-extension|moz-extension)://{_EXT_ID}$"
    if _EXT_ID
    else r"^(chrome-extension|moz-extension)://.*$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_ORIGEN_REGEX,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def require_token(x_auth_token: str = Header(default="")) -> None:
    # `compare_digest` lanza TypeError con strings no ASCII, y eso salía como
    # 500 con traceback en vez de 401. Un header inválido es un rechazo, no un
    # error del servidor.
    try:
        valido = secrets.compare_digest(x_auth_token, TOKEN)
    except TypeError:
        valido = False
    if not valido:
        raise HTTPException(status_code=401, detail="Token inválido o ausente")


class Cookie(BaseModel):
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    hostOnly: bool = False
    expirationDate: float | None = None


class ThrottleRequest(BaseModel):
    preset: str | None = None
    rate_limit_kbps: int | None = None
    gap_min: int | None = None
    gap_max: int | None = None
    video_sleep_min: int | None = None
    video_sleep_max: int | None = None


class UploadRequest(BaseModel):
    enabled: bool | None = None
    remote: str | None = None
    folder: str | None = None
    delete_after: bool | None = None


class RefreshCookiesRequest(BaseModel):
    cookies: list[Cookie]


class ProbeRequest(BaseModel):
    url: str
    cookies: list[Cookie] | None = None


class DownloadRequest(BaseModel):
    url: str
    cookies: list[Cookie] | None = None
    quality: str = Field(default="best")
    subtitles: bool = False
    audio_only: bool = False


@app.get("/health")
def health() -> dict:
    """Sin autenticar a propósito: la extensión lo usa para el punto verde.

    Por eso mismo no devuelve NADA más que eso. Devolvía `download_dir`, que en
    Windows es `C:\\Users\\<nombre real>\\...`: cualquier otra extensión instalada
    —y cualquier proceso local, que ni pasa por CORS— podía leer el nombre del
    usuario sin credencial ninguna. La ruta ahora vive en `/paths`, con token.
    """
    return {"ok": True}


@app.get("/paths", dependencies=[Depends(require_token)])
def paths() -> dict:
    """Rutas del servidor. Detrás del token: filtran el nombre del usuario."""
    return {
        "download_dir": jobs.download_dir,
        "cookie_dir": cookie_dir(),
        "espacio_libre_gb": jobs.espacio_libre_gb(),
    }


@app.post("/probe", dependencies=[Depends(require_token)])
def probe(req: ProbeRequest) -> dict:
    cookies = [c.model_dump() for c in req.cookies] if req.cookies else None
    print(f"[probe]    {redact_url(req.url)}\n           cookies: {summarize(cookies)}", flush=True)
    try:
        return jobs.probe(req.url, cookies)
    except Exception as e:  # noqa: BLE001
        # Sin `explicar`, el popup mostraba el volcado crudo de yt-dlp: la misma
        # traducción que reciben los jobs fallidos tiene que llegar acá.
        raise HTTPException(status_code=400, detail=explicar(str(e))) from e


@app.post("/diagnose", dependencies=[Depends(require_token)])
def diagnose(req: ProbeRequest) -> dict:
    """Por qué el sitio responde 403. No descarga nada: pide y mira los estados.

    Existe porque este 403 se resistió a tres hipótesis seguidas y cada una costó
    un parche. Razonar sobre el código no alcanzaba.
    """
    cookies = [c.model_dump() for c in req.cookies] if req.cookies else None
    print(f"[diag]     {redact_url(req.url)}", flush=True)
    resultado = diagnosticar(req.url, cookies)
    for nombre, p in resultado["pruebas"].items():
        print(f"           {nombre:<32} {p.get('estado')}  {str(p.get('pista') or p.get('error') or '')[:70]}",
              flush=True)
    print(f"           => {resultado['lectura']}", flush=True)
    return resultado


@app.post("/download", dependencies=[Depends(require_token)])
def download(req: DownloadRequest) -> dict:
    cookies = [c.model_dump() for c in req.cookies] if req.cookies else None
    print(f"[download] {redact_url(req.url)}\n           cookies: {summarize(cookies)}", flush=True)
    try:
        job = jobs.enqueue(
            req.url,
            cookies=cookies,
            quality=req.quality,
            subtitles=req.subtitles,
            audio_only=req.audio_only,
        )
    except Exception as e:  # noqa: BLE001
        # Encolar puede fallar antes de crear el job (por ejemplo, si el
        # directorio de cookies no se puede preparar). Sin esto salía como 500.
        raise HTTPException(status_code=400, detail=explicar(str(e))) from e
    return job.public()


@app.post("/download-playlist", dependencies=[Depends(require_token)])
def download_playlist(req: DownloadRequest) -> dict:
    """Encola un curso entero: un job por clase, no un job gigante."""
    cookies = [c.model_dump() for c in req.cookies] if req.cookies else None
    print(f"[curso]    {redact_url(req.url)}\n           cookies: {summarize(cookies)}", flush=True)
    try:
        resultado = jobs.enqueue_playlist(
            req.url,
            cookies=cookies,
            quality=req.quality,
            subtitles=req.subtitles,
            audio_only=req.audio_only,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=explicar(str(e))) from e
    print(f"           encoladas {resultado['count']} clases", flush=True)
    return resultado


@app.get("/jobs", dependencies=[Depends(require_token)])
def list_jobs() -> dict:
    # `sesion` viaja acá y no en un endpoint aparte para no agregar otra llamada
    # al polling de 1/s del popup. Es una clave nueva del sobre, no de cada job:
    # el contrato de `Job.public()` no cambia.
    return {"jobs": jobs.all(), "sesion": jobs.sesion()}


@app.post("/jobs/refresh-cookies", dependencies=[Depends(require_token)])
def refresh_cookies(req: RefreshCookiesRequest) -> dict:
    """Reemplaza la sesión de la cola pendiente sin recrear los trabajos.

    Las cookies se capturan una sola vez, al encolar, y un curso dura horas: si
    el token de Udemy rota en el medio, todo lo que falta sale con una sesión
    muerta. Esto lo arregla sin perder la cola ni el progreso.
    """
    cookies = [c.model_dump() for c in req.cookies]
    actualizados = jobs.refrescar_cookies(cookies)
    if actualizados:
        print(
            f"[sesión]   refrescada en {actualizados} trabajo(s) pendiente(s)\n"
            f"           cookies: {summarize(cookies)}",
            flush=True,
        )
    return {"actualizados": actualizados}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job inexistente")
    return job.public()


@app.post("/jobs/cancel-all", dependencies=[Depends(require_token)])
def cancel_all_jobs() -> dict:
    """Cancela todo lo pendiente de una. Un curso son 200 clics, si no."""
    return {"cancelled": jobs.cancel_all()}


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
def cancel_job(job_id: str) -> dict:
    if not jobs.cancel(job_id):
        raise HTTPException(status_code=409, detail="El job ya terminó o no existe")
    return {"ok": True}


@app.post("/jobs/clear", dependencies=[Depends(require_token)])
def clear_jobs() -> dict:
    return {"removed": jobs.clear_finished()}


@app.get("/upload-settings", dependencies=[Depends(require_token)])
def get_upload_settings() -> dict:
    """Config de subida + estado real de rclone en esta máquina.

    Se devuelven juntos a propósito: sin saber si rclone está instalado y qué
    remotos tiene configurados, la pantalla de opciones sólo puede ofrecer un
    campo de texto a ciegas.
    """
    return {
        "upload": upload_config.get().as_dict(),
        "rclone_available": uploader.disponible(),
        "remotes": uploader.remotos(),
    }


@app.post("/upload-settings", dependencies=[Depends(require_token)])
def update_upload_settings(req: UploadRequest) -> dict:
    cambios = {k: v for k, v in req.model_dump().items() if v is not None}
    if not cambios:
        raise HTTPException(status_code=400, detail="Nada que actualizar")
    merged = upload_config.get().as_dict()
    merged.update(cambios)
    return {"upload": upload_config.save(merged).as_dict()}


@app.get("/settings", dependencies=[Depends(require_token)])
def get_settings() -> dict:
    return {
        "throttle": throttle_cfg.get().as_dict(),
        "presets": {k: v.as_dict() for k, v in throttle_cfg.PRESETS.items()},
        "labels": throttle_cfg.PRESET_LABELS,
    }


@app.post("/settings", dependencies=[Depends(require_token)])
def update_settings(req: ThrottleRequest) -> dict:
    # Un preset conocido pisa todo lo demás; si no, se aplican los campos
    # sueltos sobre la configuración vigente y queda marcada como "custom".
    if req.preset and req.preset in throttle_cfg.PRESETS:
        return {"throttle": throttle_cfg.set_preset(req.preset).as_dict()}

    merged = throttle_cfg.get().as_dict()
    overrides = {k: v for k, v in req.model_dump().items() if v is not None and k != "preset"}
    if not overrides:
        raise HTTPException(status_code=400, detail="Nada que actualizar")
    merged.update(overrides)
    merged["preset"] = "custom"
    return {"throttle": throttle_cfg.save(merged).as_dict()}


if __name__ == "__main__":
    import uvicorn

    # flush=True en todo el banner: con la salida redirigida a un archivo,
    # Python usa buffer de bloque y el banner no se escribe hasta que el
    # proceso termina — o nunca, si lo matan con SIGKILL.
    def banner(linea: str = "") -> None:
        print(linea, flush=True)

    banner("=" * 62)
    banner("  video-dl server")
    banner(f"  Descargas en: {jobs.download_dir}")
    _libre = jobs.espacio_libre_gb()
    if _libre is not None:
        banner(f"  Espacio libre: {_libre} GB")
    banner(f"  Token (pegalo en la extensión): {TOKEN}")
    _t = throttle_cfg.get()
    _limit = f"{_t.rate_limit_kbps} KB/s" if _t.rate_limit_kbps else "sin límite"
    _gap = f"pausa {_t.gap_min}-{_t.gap_max}s entre trabajos"
    _vid = (f", {_t.video_sleep_min}-{_t.video_sleep_max}s entre videos"
            if _t.video_sleep_max else "")
    banner(f"  Ritmo: {_t.preset} ({_limit}, {_gap}{_vid})")
    banner(f"  Cookies temporales en: {cookie_dir()}")
    if _EXT_ID:
        banner(f"  CORS acotado a la extensión {_EXT_ID}")
    _s = upload_config.get()
    if _s.activo:
        _borra = " y borra el local" if _s.delete_after else ""
        banner(f"  Subida: {_s.remote}:{_s.folder}{_borra}")
        if not uploader.disponible():
            banner("  ⚠ Subida habilitada pero rclone NO está instalado — va a fallar")
    else:
        banner("  Subida: desactivada")
    if ORPHANS_SWEPT:
        banner(f"  ⚠ Se borraron {len(ORPHANS_SWEPT)} archivo(s) de cookies de una corrida previa:")
        for stale in ORPHANS_SWEPT:
            banner(f"      {stale}")
    _parciales = parciales_viejos()
    if _parciales:
        banner(f"  ⚠ {len(_parciales)} descarga(s) a medias, de más de 6 h. NO se borran:")
        banner("     reencolá esa clase y yt-dlp la reanuda, o borrá el .part a mano.")
        for parcial in _parciales[:5]:
            banner(f"      {parcial}")
    banner("=" * 62)
    # Por defecto sólo loopback. `VIDEO_DL_HOST` permite exponerlo en una IP
    # concreta —típicamente la de Tailscale— para depurar desde otra máquina.
    # Se usa una IP puntual y no 0.0.0.0 a propósito: con 0.0.0.0 quedaría
    # escuchando también en el WiFi y en cualquier otra red donde esté el equipo.
    host = os.environ.get("VIDEO_DL_HOST", "127.0.0.1")
    if host != "127.0.0.1":
        banner(f"  ⚠ Escuchando en {host} — accesible desde otras máquinas de esa red")
        banner("     El tráfico va sin TLS: el token y tus cookies viajan en claro.")
    uvicorn.run(app, host=host, port=int(os.environ.get("VIDEO_DL_PORT", 3210)))
