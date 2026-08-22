"""Perfiles de ritmo de descarga.

La idea es no parecer un scraper: una sola descarga a la vez, velocidad acotada
y pausas de duración aleatoria entre videos y entre trabajos. También evita que
una playlist grande te coma toda la conexión.
"""

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"


@dataclass
class Throttle:
    preset: str = "cauteloso"
    rate_limit_kbps: int = 800   # 0 = sin límite
    gap_min: int = 20            # segundos de espera entre un job y el siguiente
    gap_max: int = 60
    video_sleep_min: int = 10    # segundos entre videos dentro de una playlist
    video_sleep_max: int = 30

    def as_dict(self) -> dict:
        return asdict(self)


# Ordenados de menos a más conservador.
PRESETS: dict[str, Throttle] = {
    "rapido": Throttle("rapido", rate_limit_kbps=0, gap_min=0, gap_max=2,
                       video_sleep_min=0, video_sleep_max=0),
    "normal": Throttle("normal", rate_limit_kbps=2000, gap_min=5, gap_max=15,
                       video_sleep_min=3, video_sleep_max=8),
    "cauteloso": Throttle("cauteloso", rate_limit_kbps=800, gap_min=20, gap_max=60,
                          video_sleep_min=10, video_sleep_max=30),
    "paranoico": Throttle("paranoico", rate_limit_kbps=400, gap_min=60, gap_max=180,
                          video_sleep_min=30, video_sleep_max=90),
}

PRESET_LABELS = {
    "rapido": "Rápido — sin límites, máximo riesgo",
    "normal": "Normal — 2 MB/s, pausas cortas",
    "cauteloso": "Cauteloso — 800 KB/s, pausas de 20-60s (recomendado)",
    "paranoico": "Paranoico — 400 KB/s, pausas de 1-3 min",
    "custom": "Personalizado",
}

_lock = threading.Lock()
_current = Throttle()


def _coerce(data: dict) -> Throttle:
    """Build a Throttle from untrusted JSON, ignoring unknown keys."""
    valid = {f.name for f in fields(Throttle)}
    clean = {k: v for k, v in data.items() if k in valid}
    t = Throttle(**{**asdict(Throttle()), **clean})
    # Un gap_max menor que gap_min haría que random.uniform devuelva un rango
    # invertido; lo normalizamos en vez de fallar.
    t.rate_limit_kbps = max(0, int(t.rate_limit_kbps))
    t.gap_min = max(0, int(t.gap_min))
    t.gap_max = max(t.gap_min, int(t.gap_max))
    t.video_sleep_min = max(0, int(t.video_sleep_min))
    t.video_sleep_max = max(t.video_sleep_min, int(t.video_sleep_max))
    return t


def load() -> Throttle:
    global _current
    with _lock:
        if SETTINGS_FILE.exists():
            try:
                _current = _coerce(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                _current = PRESETS["cauteloso"]
        else:
            _current = PRESETS["cauteloso"]
        return _current


def get() -> Throttle:
    with _lock:
        return _current


def set_preset(name: str) -> Throttle:
    if name not in PRESETS:
        raise ValueError(f"Preset desconocido: {name}")
    return save(PRESETS[name].as_dict())


def save(data: dict) -> Throttle:
    global _current
    t = _coerce(data)
    with _lock:
        _current = t
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(t.as_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)  # escritura atómica
    return t
