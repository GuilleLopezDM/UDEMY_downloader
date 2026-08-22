"""Configuración de la subida automática a un remoto de rclone.

Vive aparte de `throttle.py` —y en su propio archivo— porque son decisiones
distintas: el ritmo se toca seguido para evitar baneos, esto se configura una vez.
"""

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent / "upload.json"


@dataclass
class Subida:
    enabled: bool = False
    remote: str = ""          # nombre del remoto de rclone, ej. "gdrive"
    folder: str = "Cursos"    # carpeta base dentro del remoto
    # Borrar el archivo local después de subirlo. Por defecto NO: borrar datos
    # del usuario es irreversible y tiene que ser una decisión explícita.
    delete_after: bool = False

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def activo(self) -> bool:
        """Sólo sube si además de estar habilitado hay un remoto elegido."""
        return bool(self.enabled and self.remote)


_lock = threading.Lock()
_actual = Subida()


def _coerce(data: dict) -> Subida:
    validos = {f.name for f in fields(Subida)}
    limpio = {k: v for k, v in data.items() if k in validos}
    s = Subida(**{**asdict(Subida()), **limpio})
    s.enabled = bool(s.enabled)
    s.delete_after = bool(s.delete_after)
    s.remote = str(s.remote or "").strip().rstrip(":")
    s.folder = str(s.folder or "").strip().strip("/")
    return s


def load() -> Subida:
    global _actual
    with _lock:
        if ARCHIVO.exists():
            try:
                _actual = _coerce(json.loads(ARCHIVO.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                _actual = Subida()
        else:
            _actual = Subida()
        return _actual


def get() -> Subida:
    with _lock:
        return _actual


def save(data: dict) -> Subida:
    global _actual
    s = _coerce(data)
    with _lock:
        _actual = s
        tmp = ARCHIVO.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(s.as_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, ARCHIVO)  # escritura atómica
    return s
