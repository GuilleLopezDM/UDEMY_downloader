"""Deja importable `server/` sin instalarlo como paquete.

Los tests NO importan `app`: importarlo tiene efectos de disco desde el import
—crea el `.token` y barre los jars de cookies de la corrida anterior—, así que
un `pytest` corriendo mientras el servidor trabaja le rompería la descarga. Por
eso `redact_url` vive en `redaccion.py` y no en `app.py`.
"""

import sys
from pathlib import Path

SERVIDOR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVIDOR))
