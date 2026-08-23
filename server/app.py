"""Local HTTP API that fronts yt-dlp for the browser extension.

Binds to 127.0.0.1 only. Any page you visit can also reach a localhost port,
so every endpoint requires a shared token that lives in server/.token and is
pasted into the extension once.

La única excepción es `/health`, público a propósito: la extensión lo consulta
para el indicador de conexión, antes de tener token configurado. Por eso mismo
no devuelve más que `{"ok": true}` — cualquier dato ahí queda sin proteger.
"""

import logging
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import throttle as throttle_cfg
import upload_config
import uploader
from cookies import cookie_dir, summarize, sweep_orphans
from downloader import JobManager

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / ".token"
DOWNLOAD_DIR = os.environ.get("VIDEO_DL_DIR", str(HERE.parent / "downloads"))


def load_or_create_token() -> str:
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(tok + "\n", encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o600)
    return tok


TOKEN = load_or_create_token()
TUNING = throttle_cfg.load()
SUBIDA = upload_config.load()
jobs = JobManager(DOWNLOAD_DIR)

# Los jars de cookies se borran en un `finally`, que un SIGKILL —o cerrar la
# ventana de la terminal— nunca ejecuta. Barrer al arrancar es la única red de
# seguridad. Se incluye DOWNLOAD_DIR porque las versiones anteriores a este fix
# los escribían ahí: quien actualice puede tener uno viejo con sesión viva.
ORPHANS_SWEPT = sweep_orphans(DOWNLOAD_DIR)

# Parámetros de query que son credenciales. Los de CloudFront (Policy,
# Signature, Key-Pair-Id) firman las URLs de los segmentos de Udemy: quien los
# tenga puede bajar el video mientras no expiren.
CLAVES_SENSIBLES = frozenset({
    "policy", "signature", "sig", "key-pair-id", "token", "access_token",
    "auth", "authorization", "expires", "x-amz-signature", "x-amz-credential",
    "x-amz-security-token", "hdnts", "__hdnea__",
})
LARGO_MAXIMO_VALOR = 24


def redact_url(raw: str) -> str:
    """URL apta para un log: conserva los nombres de parámetro, no los valores.

    Los nombres ayudan a diagnosticar y no son secretos. Los valores sí pueden
    serlo, así que las claves conocidas se omiten enteras y el resto se trunca
    — una firma es siempre larga, un `v=Tr2Y9hBtTjE` de YouTube no.
    """
    try:
        partes = urlsplit(raw)
    except ValueError:
        return "(url ilegible)"
    if not partes.query:
        return raw

    limpios = []
    for par in partes.query.split("&"):
        clave, sep, valor = par.partition("=")
        if not sep:
            limpios.append(clave)
        elif clave.lower() in CLAVES_SENSIBLES:
            limpios.append(f"{clave}=<omitido>")
        elif len(valor) > LARGO_MAXIMO_VALOR:
            limpios.append(f"{clave}={valor[:LARGO_MAXIMO_VALOR]}…<truncado>")
        else:
            limpios.append(par)

    return urlunsplit((partes.scheme, partes.netloc, partes.path, "&".join(limpios), ""))


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

app = FastAPI(title="video-dl", docs_url=None, redoc_url=None)

# Only the extension talks to this API. chrome-extension:// origins are not
# matched by a wildcard list, so allow them by regex.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension|moz-extension)://.*$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def require_token(x_auth_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_auth_token, TOKEN):
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
    return {"download_dir": jobs.download_dir, "cookie_dir": cookie_dir()}


@app.post("/probe", dependencies=[Depends(require_token)])
def probe(req: ProbeRequest) -> dict:
    cookies = [c.model_dump() for c in req.cookies] if req.cookies else None
    print(f"[probe]    {redact_url(req.url)}\n           cookies: {summarize(cookies)}", flush=True)
    try:
        return jobs.probe(req.url, cookies)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/download", dependencies=[Depends(require_token)])
def download(req: DownloadRequest) -> dict:
    cookies = [c.model_dump() for c in req.cookies] if req.cookies else None
    print(f"[download] {redact_url(req.url)}\n           cookies: {summarize(cookies)}", flush=True)
    job = jobs.enqueue(
        req.url,
        cookies=cookies,
        quality=req.quality,
        subtitles=req.subtitles,
        audio_only=req.audio_only,
    )
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
        raise HTTPException(status_code=400, detail=str(e)) from e
    print(f"           encoladas {resultado['count']} clases", flush=True)
    return resultado


@app.get("/jobs", dependencies=[Depends(require_token)])
def list_jobs() -> dict:
    return {"jobs": jobs.all()}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job inexistente")
    return job.public()


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
    banner(f"  Token (pegalo en la extensión): {TOKEN}")
    _t = throttle_cfg.get()
    _limit = f"{_t.rate_limit_kbps} KB/s" if _t.rate_limit_kbps else "sin límite"
    _gap = f"pausa {_t.gap_min}-{_t.gap_max}s entre trabajos"
    _vid = (f", {_t.video_sleep_min}-{_t.video_sleep_max}s entre videos"
            if _t.video_sleep_max else "")
    banner(f"  Ritmo: {_t.preset} ({_limit}, {_gap}{_vid})")
    banner(f"  Cookies temporales en: {cookie_dir()}")
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
    banner("=" * 62)
    # Por defecto sólo loopback. `VIDEO_DL_HOST` permite exponerlo en una IP
    # concreta —típicamente la de Tailscale— para depurar desde otra máquina.
    # Se usa una IP puntual y no 0.0.0.0 a propósito: con 0.0.0.0 quedaría
    # escuchando también en el WiFi y en cualquier otra red donde esté el equipo.
    host = os.environ.get("VIDEO_DL_HOST", "127.0.0.1")
    if host != "127.0.0.1":
        banner(f"  ⚠ Escuchando en {host} — accesible desde otras máquinas de esa red")
    uvicorn.run(app, host=host, port=int(os.environ.get("VIDEO_DL_PORT", 3210)))
