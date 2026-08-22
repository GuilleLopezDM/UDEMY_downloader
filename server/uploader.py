"""Subida a Drive (o cualquier remoto de rclone) después de descargar.

Se usa rclone en vez de un cliente de Drive propio por una razón concreta: los
videos de un curso son archivos grandes, y ahí lo que falla es la subida
reanudable por partes, los reintentos y el manejo de cortes. rclone ya tiene eso
resuelto; un cliente escrito a mano se rompe justo con los archivos que importan.

rclone también resuelve el OAuth: `rclone config` abre el navegador una vez,
guarda el refresh token y después funciona desatendido.
"""

import re
import shutil
import subprocess
import threading
from pathlib import Path

# `Transferred:   10.512 MiB / 100.000 MiB, 10%, 1.204 MiB/s, ETA 1m15s`
_LINEA_STATS = re.compile(r"(\d+)%")

TIMEOUT_SUBIDA = 60 * 60 * 4  # 4 h: un video grande por conexión hogareña


class ErrorDeSubida(Exception):
    """Falla al subir, con un mensaje pensado para mostrarle al usuario."""


def disponible() -> bool:
    """¿Está rclone en el PATH?"""
    return shutil.which("rclone") is not None


def remotos() -> list[str]:
    """Remotos configurados en rclone, sin los dos puntos finales."""
    if not disponible():
        return []
    try:
        salida = subprocess.run(
            ["rclone", "listremotes"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return [linea.strip().rstrip(":") for linea in salida.splitlines() if linea.strip()]


def destino(remoto: str, carpeta_base: str, subcarpeta: str | None) -> str:
    """Arma la ruta remota, sin barras duplicadas ni sobrantes."""
    partes = [p.strip("/") for p in (carpeta_base, subcarpeta) if p and p.strip("/")]
    return f"{remoto}:{'/'.join(partes)}" if partes else f"{remoto}:"


def subir(
    archivo: str,
    remoto: str,
    carpeta_base: str = "",
    subcarpeta: str | None = None,
    on_progress=None,
    cancelado: threading.Event | None = None,
) -> str:
    """Sube `archivo` y devuelve la ruta remota. Lanza ErrorDeSubida si falla.

    `on_progress` recibe el porcentaje (0-100) a medida que avanza.
    """
    if not disponible():
        raise ErrorDeSubida(
            "rclone no está instalado. Instalalo con: winget install Rclone.Rclone"
        )

    origen = Path(archivo)
    if not origen.is_file():
        raise ErrorDeSubida(f"No existe el archivo a subir: {archivo}")

    ruta_remota = destino(remoto, carpeta_base, subcarpeta)
    comando = [
        "rclone", "copy", str(origen), ruta_remota,
        "--stats", "1s",
        "--stats-one-line",
        # Sin esto rclone lista el destino entero antes de subir un solo archivo.
        "--no-traverse",
        "--retries", "5",
        "--low-level-retries", "10",
    ]

    try:
        proceso = subprocess.Popen(
            comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except OSError as e:
        raise ErrorDeSubida(f"No se pudo ejecutar rclone: {e}") from e

    ultimas = []
    try:
        for linea in proceso.stdout or []:
            linea = linea.strip()
            if linea:
                # Se guardan sólo las últimas: el error útil está al final, y
                # así no se acumula la salida entera en memoria.
                ultimas = (ultimas + [linea])[-15:]
            if on_progress:
                m = _LINEA_STATS.search(linea)
                if m:
                    on_progress(min(100, int(m.group(1))))
            if cancelado is not None and cancelado.is_set():
                proceso.kill()
                raise ErrorDeSubida("Subida cancelada")
        codigo = proceso.wait(timeout=TIMEOUT_SUBIDA)
    except subprocess.TimeoutExpired as e:
        proceso.kill()
        raise ErrorDeSubida("La subida superó el tiempo límite") from e
    finally:
        if proceso.stdout:
            proceso.stdout.close()

    if codigo != 0:
        detalle = " | ".join(ultimas[-3:]) or f"rclone salió con código {codigo}"
        raise ErrorDeSubida(f"rclone falló: {detalle}")

    return f"{ruta_remota}/{origen.name}"
