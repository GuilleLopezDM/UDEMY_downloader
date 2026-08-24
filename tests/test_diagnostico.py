"""Las partes puras del diagnóstico: qué cookie va a qué host, y qué se publica."""

import pytest

import diagnostico as d


@pytest.mark.parametrize(
    ("cookie", "host", "aplica"),
    [
        # Con punto inicial (o sin hostOnly) vale para los subdominios.
        ({"domain": ".udemy.com"}, "personalpy.udemy.com", True),
        ({"domain": ".udemy.com"}, "www.udemy.com", True),
        ({"domain": "udemy.com"}, "personalpy.udemy.com", True),
        # host-only: SÓLO ese host. Es la distinción que hace que el portal y www
        # reciban sets de cookies distintos.
        ({"domain": "personalpy.udemy.com", "hostOnly": True}, "personalpy.udemy.com", True),
        ({"domain": "personalpy.udemy.com", "hostOnly": True}, "www.udemy.com", False),
        # Y no se confunde un sufijo con un subdominio.
        ({"domain": ".udemy.com"}, "notudemy.com", False),
        ({"domain": ""}, "udemy.com", False),
    ],
)
def test_a_que_host_va_cada_cookie(cookie, host, aplica):
    assert d._aplica(cookie, host) is aplica


def test_la_cabecera_solo_lleva_lo_que_corresponde():
    cookies = [
        {"name": "compartida", "value": "1", "domain": ".udemy.com"},
        {"name": "del_portal", "value": "2", "domain": "personalpy.udemy.com", "hostOnly": True},
    ]
    assert d.cabecera_cookies(cookies, "www.udemy.com") == "compartida=1"
    portal = d.cabecera_cookies(cookies, "personalpy.udemy.com")
    assert "compartida=1" in portal and "del_portal=2" in portal


def test_los_valores_de_los_tokens_nunca_salen():
    """El diagnóstico se pega en un chat: no puede llevar la sesión adentro."""
    cookies = [{"name": "access_token", "value": "SECRETO_REAL", "domain": ".udemy.com"}]
    salida = str(d.tokens_por_dominio(cookies))
    assert "SECRETO_REAL" not in salida
    assert len(d.huella("SECRETO_REAL")) == 8


def test_la_huella_distingue_dos_tokens_distintos():
    """Es para lo único que sirve: saber si el jar trae dos sesiones diferentes."""
    assert d.huella("a") != d.huella("b")
    assert d.huella("a") == d.huella("a")


def test_solo_informa_las_cookies_de_auth():
    cookies = [{"name": "otra", "value": "x", "domain": ".udemy.com"},
               {"name": "client_id", "value": "y", "domain": ".udemy.com"}]
    assert [t["nombre"] for t in d.tokens_por_dominio(cookies)] == ["client_id"]


def ok(*claves):
    todas = ["1_pagina_ua_moderno", "1b_pagina_ua_de_2019_de_udemy_py",
             "2_pagina_headers_navegador",
             "3_api_quien_soy_en_el_portal", "4_api_quien_soy_en_www",
             "5_mis_cursos_en_el_portal", "6_mis_cursos_en_www",
             "7a_yt_dlp_PRIMERO", "7b_yt_dlp_tls_amplio", "7c_yt_dlp_ULTIMO"]
    return {k: {"estado": 200 if k in claves else 403} for k in todas}


def test_lectura_user_agent():
    """La prueba 1 usaba un Chrome moderno y por eso daba 200: nunca ejercitó lo
    que yt-dlp manda de verdad. Ese hueco escondió la causa durante horas."""
    r = d._leer(ok("1_pagina_ua_moderno", "2_pagina_headers_navegador"))
    assert "User-Agent" in r and "2019" in r


def test_lectura_huella_tls():
    """El contexto TLS de yt-dlp trae 17 cifrados; el de urllib, 60. Misma
    petición, mismo momento: si pasa con la lista completa y falla con la
    recortada, lo que bloquea es el handshake y no nada de HTTP."""
    r = d._leer(ok("7b_yt_dlp_tls_amplio"))
    assert "TLS" in r and "handshake" in r


def test_lectura_rafaga():
    """La prueba equivalente venía última, después de seis peticiones seguidas:
    un bloqueo por ritmo y uno por huella de cliente se veían igual."""
    r = d._leer(ok("7a_yt_dlp_PRIMERO"))
    assert "RÁFAGA" in r


def test_lectura_anti_bot_con_los_dos_tls():
    r = dict(ok())
    r["7a_yt_dlp_PRIMERO"]["anti_bot"] = "cuerpo:_pxcaptcha"
    assert "ANTI-BOT" in d._leer(r)


def test_lectura_host_de_la_api():
    r = d._leer(ok("1_pagina_ua_moderno", "1b_pagina_ua_de_2019_de_udemy_py",
                   "3_api_quien_soy_en_el_portal", "5_mis_cursos_en_el_portal",
                   "7a_yt_dlp_PRIMERO", "7b_yt_dlp_tls_amplio", "7c_yt_dlp_ULTIMO"))
    assert "host" in r.lower() and "www" in r


def test_lectura_sesion_muerta():
    assert "NINGÚN" in d._leer(ok())


# ---- detección de anti-bot -------------------------------------------------


def test_reconoce_un_captcha_de_perimeterx_en_el_cuerpo():
    """yt-dlp busca estos marcadores (udemy.py:132-135) pero NUNCA los ve en un
    403: el chequeo corre después de una respuesta exitosa y el 403 revienta
    antes (common.py:908-920). Por eso un bloqueo de bot y un "no autorizado"
    salen los dos como el mismo `HTTP Error 403` genérico."""
    cuerpo = b'<html><body>Please verify you are a human "_pxCaptcha"</body></html>'
    r = d._marcas_anti_bot(cuerpo, {})
    assert "_pxcaptcha" in r and "please verify you are a human" in r


def test_reconoce_perimeterx_en_los_headers():
    r = d._marcas_anti_bot(b"", {"X-PX-Block": "1"})
    assert "header:X-PX-Block" in r
    assert "set-cookie:_px*" in d._marcas_anti_bot(b"", {"Set-Cookie": "_pxvid=abc; Path=/"})


def test_un_403_de_permisos_no_se_confunde_con_anti_bot():
    """El 403 de autorización de Udemy trae este JSON, y no hay que leerlo como
    un bloqueo: el arreglo es completamente distinto."""
    cuerpo = b'{"detail": "You do not have permission to perform this action."}'
    assert d._marcas_anti_bot(cuerpo, {"Content-Type": "application/json"}) == "sin marcas de anti-bot"


def test_que_la_pagina_cargue_no_tapa_el_problema_del_host():
    """El 'todo pasa' va último: si la página anda pero la API de www rechaza,
    lo útil es el host, no un 'el stack pasa' que suena a que está todo bien."""
    r = d._leer(ok("7a_yt_dlp_PRIMERO", "7b_yt_dlp_tls_amplio", "7c_yt_dlp_ULTIMO",
                   "1_pagina_ua_moderno", "1b_pagina_ua_de_2019_de_udemy_py",
                   "3_api_quien_soy_en_el_portal", "5_mis_cursos_en_el_portal"))
    assert "host de la API" in r
