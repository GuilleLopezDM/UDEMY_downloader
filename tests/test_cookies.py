"""El jar de cookies: formato, permisos y barrido.

Tampoco es puro: escribe un archivo real. El fixture redirige `cookie_dir` a
`tmp_path` — un `pytest` que tocara el directorio de verdad podría borrarle los
jars a un servidor que esté bajando un curso, que es justo el fallo que este
módulo arregla.
"""

import os
import re

import pytest

import cookies as mod


@pytest.fixture
def jaula(tmp_path, monkeypatch):
    destino = tmp_path / "jars"
    destino.mkdir()
    monkeypatch.setattr(mod, "cookie_dir", lambda: str(destino))
    return destino


COOKIES = [
    {"name": "access_token", "value": "secreto", "domain": "www.udemy.com", "hostOnly": True,
     "path": "/", "secure": True, "expirationDate": 1893456000.0},
    {"name": "client_id", "value": "abc", "domain": ".udemy.com", "hostOnly": False},
]


def test_formato_netscape(jaula):
    ruta = mod.write_netscape_cookiefile(COOKIES)
    lineas = [l for l in open(ruta, encoding="utf-8").read().splitlines() if l and not l.startswith("#")]
    assert len(lineas) == 2
    for linea in lineas:
        assert len(linea.split("\t")) == 7


def test_hostonly_es_el_inverso_de_la_columna(jaula):
    """Chrome informa `hostOnly`; el formato pide "incluye subdominios"."""
    ruta = mod.write_netscape_cookiefile(COOKIES)
    texto = open(ruta, encoding="utf-8").read()
    assert "www.udemy.com\tFALSE\t" in texto   # hostOnly=True  -> no subdominios
    assert ".udemy.com\tTRUE\t" in texto       # hostOnly=False -> sí subdominios


def test_el_punto_inicial_se_agrega_solo(jaula):
    ruta = mod.write_netscape_cookiefile([{"name": "a", "value": "1", "domain": "udemy.com"}])
    assert open(ruta, encoding="utf-8").read().count("\t.udemy.com") == 0
    assert ".udemy.com\tTRUE" in open(ruta, encoding="utf-8").read()


def test_sin_dominio_no_entra(jaula):
    ruta = mod.write_netscape_cookiefile([{"name": "a", "value": "1", "domain": ""}])
    cuerpo = [l for l in open(ruta, encoding="utf-8").read().splitlines() if l and not l.startswith("#")]
    assert cuerpo == []


def test_cookie_de_sesion_expira_en_cero(jaula):
    ruta = mod.write_netscape_cookiefile([{"name": "a", "value": "1", "domain": "x.com"}])
    assert "\t0\ta\t1" in open(ruta, encoding="utf-8").read()


@pytest.mark.skipif(os.name == "nt", reason="chmod es casi no-op en Windows")
def test_modo_600(jaula):
    """Lleva una sesión viva adentro."""
    ruta = mod.write_netscape_cookiefile(COOKIES)
    assert oct(os.stat(ruta).st_mode & 0o777) == "0o600"


def test_el_nombre_lleva_el_pid(jaula):
    """Es lo que permite no barrerle los jars a otra instancia del servidor."""
    ruta = mod.write_netscape_cookiefile(COOKIES)
    assert re.match(rf"^cookies-{os.getpid()}-", os.path.basename(ruta))


def test_discard_no_se_queja_si_ya_no_esta(jaula):
    ruta = mod.write_netscape_cookiefile(COOKIES)
    mod.discard(ruta)
    mod.discard(ruta)      # segunda vez: no debe lanzar
    mod.discard(None)
    assert not os.path.exists(ruta)


def test_el_barrido_respeta_los_jars_de_un_proceso_vivo(jaula):
    """Arrancar un segundo servidor le borraba los jars al que estaba bajando
    un curso: las clases siguientes fallaban con "Hace falta sesión iniciada" y
    no había ninguna pista de por qué."""
    vivo = jaula / f"cookies-{os.getpid()}-vivo.txt"
    vivo.write_text("x", encoding="utf-8")
    muerto = jaula / "cookies-999999999-muerto.txt"
    muerto.write_text("x", encoding="utf-8")

    barridos = mod.sweep_orphans()

    assert vivo.exists()
    assert not muerto.exists()
    assert str(muerto) in barridos


def test_el_barrido_se_lleva_los_nombres_viejos_sin_pid(jaula):
    """Los de versiones anteriores al fix: son huérfanos por definición."""
    viejo = jaula / "cookies-abc123.txt"
    viejo.write_text("x", encoding="utf-8")
    assert str(viejo) in mod.sweep_orphans()
    assert not viejo.exists()


# ---- resumen para el log ---------------------------------------------------


def test_summarize_no_expone_ningun_valor():
    """Un `access_token` en el log es tan bueno como la sesión misma."""
    salida = mod.summarize(COOKIES)
    assert "secreto" not in salida
    assert "abc" not in salida


def test_summarize_avisa_si_falta_la_auth_de_udemy():
    assert "FALTAN" in mod.summarize([{"name": "otra", "domain": "udemy.com"}])
    assert "ok" in mod.summarize(COOKIES)


def test_summarize_sin_cookies():
    assert mod.summarize([]) == "sin cookies"
    assert mod.summarize(None) == "sin cookies"
