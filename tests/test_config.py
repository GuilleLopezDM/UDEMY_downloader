"""`_coerce()` de throttle y upload_config: la frontera con JSON no confiable.

El `settings.json` lo escribe el servidor pero lo puede editar cualquiera, y los
valores llegan además desde la extensión. Un valor raro no puede ni reventar el
arranque ni apagar el freno en silencio.
"""

import pytest

import throttle
import upload_config


def test_ignora_claves_desconocidas():
    t = throttle._coerce({"gap_min": 5, "inventado": "x", "__class__": "no"})
    assert t.gap_min == 5


@pytest.mark.parametrize("basura", ["abc", None, [], {}, "12abc"])
def test_valores_no_numericos_caen_al_default_sin_lanzar(basura):
    """`int("abc")` reventaba. `load()` lo atrapaba, `save()` no: era un 500."""
    t = throttle._coerce({"gap_min": basura})
    assert t.gap_min == throttle.Throttle().gap_min


def test_negativos_se_llevan_a_cero():
    t = throttle._coerce({"gap_min": -5, "gap_max": -9, "rate_limit_kbps": -1})
    assert (t.gap_min, t.gap_max, t.rate_limit_kbps) == (0, 0, 0)


def test_maximo_menor_que_minimo_se_normaliza():
    """`random.uniform` con el rango invertido no falla, devuelve cualquier cosa."""
    t = throttle._coerce({"gap_min": 30, "gap_max": 10})
    assert t.gap_min <= t.gap_max


def test_hay_tope_superior():
    """Los valores se PERSISTEN: un cero de más congelaba la cola por horas."""
    t = throttle._coerce({"gap_min": 10**9, "gap_max": 10**9, "video_sleep_min": 10**9})
    assert t.gap_min <= throttle.TOPE_PAUSA
    assert t.gap_max <= throttle.TOPE_PAUSA
    assert t.video_sleep_min <= throttle.TOPE_PAUSA


def test_cero_significa_sin_limite_y_se_respeta():
    assert throttle._coerce({"rate_limit_kbps": 0}).rate_limit_kbps == 0


def test_los_presets_sobreviven_al_coerce():
    """Un preset que se deformara al guardarse dejaría de ser el que dice ser."""
    for nombre, preset in throttle.PRESETS.items():
        assert throttle._coerce(preset.as_dict()).as_dict() == preset.as_dict(), nombre


def test_subida_requiere_remoto_para_estar_activa():
    """Tener "subir" tildado sin remoto sólo produce descargas que fallan al final."""
    assert not upload_config._coerce({"enabled": True, "remote": ""}).activo
    assert upload_config._coerce({"enabled": True, "remote": "gdrive"}).activo
    assert not upload_config._coerce({"enabled": False, "remote": "gdrive"}).activo


def test_subida_limpia_el_remoto():
    s = upload_config._coerce({"remote": "  gdrive:  ", "folder": "/Cursos/"})
    assert s.remote == "gdrive"
    assert s.folder == "Cursos"
