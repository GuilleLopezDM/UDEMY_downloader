"""El parche que manda la API de Udemy al portal de la organización.

Es la diferencia entre bajar un curso de Udemy Business y no bajar ninguno:
yt-dlp pide las clases a `www.udemy.com` con el host clavado, y ahí la
inscripción de la organización no existe.
"""

import pytest
import yt_dlp
from yt_dlp.extractor.udemy import UdemyIE

import downloader
from urls import portal_udemy


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("https://personalpy.udemy.com/course/x/", "https://personalpy.udemy.com"),
        ("https://ACME.udemy.com/course/x/learn/lecture/1", "https://ACME.udemy.com"),
        ("http://org.udemy.com/x", "http://org.udemy.com"),
        # www NO es un portal: ahí el comportamiento por defecto ya es correcto.
        ("https://www.udemy.com/course/x/", None),
        ("https://WWW.udemy.com/course/x/", None),
        ("https://www.youtube.com/watch?v=a", None),
        ("", None),
    ],
)
def test_portal_udemy(url, esperado):
    assert portal_udemy(url) == esperado


@pytest.fixture
def capturar(monkeypatch):
    """Intercepta la petición justo antes de que salga a la red."""
    from yt_dlp.extractor.common import InfoExtractor

    vistas = []

    def falso(self, url_or_request, *a, **k):
        vistas.append(getattr(url_or_request, "url", url_or_request))
        return {}

    monkeypatch.setattr(InfoExtractor, "_download_json", falso)
    return vistas


def extractor(params):
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, **params})
    ie = UdemyIE()
    ie.set_downloader(ydl)
    return ie


API_CLASE = "https://www.udemy.com/api-2.0/users/me/subscribed-courses/4696146/lectures/32312110?"
API_TEMARIO = "https://www.udemy.com/api-2.0/courses/4696146/cached-subscriber-curriculum-items"


def test_la_clase_se_pide_al_portal(capturar):
    """Es el 403 que fallaba en TODAS las clases con la sesión válida."""
    downloader.parchear_udemy_business()
    extractor({"portal_udemy": "https://personalpy.udemy.com"})._download_json(API_CLASE, "1")
    assert capturar[0].startswith("https://personalpy.udemy.com/api-2.0/users/me/")
    assert "4696146/lectures/32312110" in capturar[0]


def test_sin_portal_no_se_toca_nada(capturar):
    """Un curso de www.udemy.com tiene que seguir andando igual que antes."""
    downloader.parchear_udemy_business()
    extractor({})._download_json(API_CLASE, "1")
    assert capturar[0].startswith("https://www.udemy.com/api-2.0/users/me/")


def test_el_temario_NO_se_redirige(capturar):
    """Desde www funciona —no depende de la inscripción— y no se toca lo que anda.
    Por eso el curso se enumera entero y recién después falla clase por clase."""
    downloader.parchear_udemy_business()
    extractor({"portal_udemy": "https://personalpy.udemy.com"})._download_json(API_TEMARIO, "1")
    assert capturar[0] == API_TEMARIO


def test_parchear_dos_veces_no_apila_envoltorios(capturar):
    """Si se aplicara en cada import, cada capa reescribiría de nuevo."""
    for _ in range(3):
        downloader.parchear_udemy_business()
    extractor({"portal_udemy": "https://p.udemy.com"})._download_json(API_CLASE, "1")
    assert capturar[0].count("p.udemy.com") == 1


# ---- el User-Agent de 2019 -------------------------------------------------


class _YDLEspia(yt_dlp.YoutubeDL):
    """Corta justo antes de la red y guarda los headers finales."""

    capturado: dict = {}

    def urlopen(self, req):
        _YDLEspia.capturado = dict(req.headers)
        raise _Cortar()


class _Cortar(Exception):
    pass


def _ua_que_sale_a_la_red():
    ydl = _YDLEspia({"quiet": True, "no_warnings": True})
    ie = UdemyIE()
    ie.set_downloader(ydl)
    try:
        ie._download_webpage_handle("https://personalpy.udemy.com/course/x", "id")
    except _Cortar:
        pass
    h = _YDLEspia.capturado
    return h.get("User-agent") or h.get("User-Agent") or ""


def test_no_sale_el_user_agent_de_2019():
    """`UdemyIE._download_webpage_handle` (udemy.py:123-126) pisa el UA con
    Chrome 72.0.3626.109 —de enero de 2019— DESPUÉS de copiar los headers, así
    que gana sobre cualquier configuración. Udemy tiene protección anti-bot (el
    propio yt-dlp busca los marcadores de PerimeterX en udemy.py:132-135) y un
    Chrome de seis años atrás es lo que esa protección marca.

    Medido con la sesión real: la MISMA página con las MISMAS cookies devolvió
    200 con Chrome 120 y 403 con el de 2019."""
    downloader.parchear_udemy_business()
    assert "Chrome/72" not in _ua_que_sale_a_la_red()


def test_el_user_agent_que_sale_es_el_actual_de_yt_dlp():
    """No uno inventado: el que mantiene yt-dlp, para no tener que actualizarlo acá."""
    from yt_dlp.utils.networking import random_user_agent

    downloader.parchear_udemy_business()
    ua = _ua_que_sale_a_la_red()
    # random_user_agent apunta a las versiones de los últimos ~6 meses.
    assert ua.startswith("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    assert "Chrome/" in ua and "Safari/537.36" in ua
    assert ua.split("Chrome/")[1].split(".")[0].isdigit()
    assert int(ua.split("Chrome/")[1].split(".")[0]) >= 100


def test_otros_extractores_no_se_ven_afectados(monkeypatch):
    """El parche vive en InfoExtractor pero sólo actúa ante el UA exacto de 2019,
    así que ningún otro extractor cambia de comportamiento."""
    from yt_dlp.extractor.common import InfoExtractor

    downloader.parchear_udemy_business()
    vistos = []
    monkeypatch.setattr(
        InfoExtractor, "_request_webpage",
        lambda self, *a, **k: vistos.append(k.get("headers")) or (_ for _ in ()).throw(_Cortar()),
    )
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    ie = InfoExtractor()
    ie.set_downloader(ydl)
    propios = {"User-Agent": "MiPropioAgente/1.0"}
    try:
        ie._download_webpage_handle("https://ejemplo.com/", "id", headers=propios)
    except _Cortar:
        pass
    assert vistos[0]["User-Agent"] == "MiPropioAgente/1.0"
