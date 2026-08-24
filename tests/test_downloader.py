"""Piezas puras de `downloader`: contrato con la UI, plantillas y traducciones."""

import pytest

import uploader
from downloader import Job, JobManager, url_de_entrada


# ---- contrato con la extensión -------------------------------------------

# Estas claves las lee `popup.js`. `Job.public()` se arma a mano y no con
# `dataclasses.asdict` —asdict hace deepcopy y `_cancel` es un Event con un lock
# que no se puede copiar; ese fue un bug real que reventaba /jobs entero—, así
# que agregar un campo al dataclass NO lo agrega a la respuesta. Este test es lo
# único que avisa si los dos lados se separan.
CLAVES_PUBLICAS = {
    "id", "url", "status", "title", "percent", "speed", "eta", "filepath",
    "error", "error_detalle", "wait_seconds", "position", "total", "subfolder",
    "cookie_url", "remote_path",
}


def test_contrato_de_job_public():
    assert set(Job(id="a", url="u").public()) == CLAVES_PUBLICAS


def test_public_no_filtra_los_campos_privados():
    publico = Job(id="a", url="u").public()
    assert not any(k.startswith("_") for k in publico)


def test_public_es_serializable():
    """/jobs serializa 200 de estos por segundo: nada raro adentro."""
    import json

    json.dumps(Job(id="a", url="u").public())


# ---- elección de la URL de cada clase ------------------------------------


def test_entrada_sin_resolver_usa_url_con_el_smuggle():
    """El course_id viaja SOLO ahí, y sin él cada clase da 403."""
    smuggled = "https://www.udemy.com/x/learn/v4/t/lecture/1#__youtubedl_smuggle=%7B%22course_id%22%3A9%7D"
    assert url_de_entrada({"_type": "url", "url": smuggled}) == smuggled


def test_entrada_ya_resuelta_prefiere_la_pagina():
    """Con la entrada procesada, `url` es la del formato, no la de la clase.

    `YoutubeDL.py:1983` excluye `url` al propagar un `url_transparent` y `:3155`
    la pisa con el formato elegido.
    """
    entrada = {
        "_type": None,
        "url": "https://cdn.net/v.mp4?Policy=A&Signature=B",
        "webpage_url": "https://www.udemy.com/x/lecture/1",
    }
    assert url_de_entrada(entrada) == "https://www.udemy.com/x/lecture/1"


@pytest.mark.parametrize(
    "firmada",
    [
        "https://cdn.net/v.mp4?Signature=B",
        "https://cdn.net/v.mp4?Policy=A",
        "https://cdn.net/v.mp4?Key-Pair-Id=K",
        "https://s3.net/v.mp4?X-Amz-Signature=abc",
    ],
)
def test_descarta_las_urls_firmadas(firmada):
    """Vencen en horas y el archive guardaría la clave del extractor equivocado."""
    assert url_de_entrada({"_type": "url", "url": firmada}) is None


def test_entrada_sin_url_se_descarta():
    assert url_de_entrada({"_type": "url"}) is None
    assert url_de_entrada({}) is None


def test_no_filtra_por_host():
    """Exigir udemy.com dejaría cualquier otra playlist en count=0 sin error."""
    yt = "https://www.youtube.com/watch?v=abc"
    assert url_de_entrada({"_type": "url", "url": yt}) == yt


# ---- plantilla de nombre --------------------------------------------------


def test_sin_posicion_el_id_va_adelante():
    """`trim_file_name` recorta por la DERECHA: un id al final es lo primero que
    se pierde. Medido en Windows, un título largo dejaba
    `001 - Introduction to System Design and Architectu.mp4`, sin id."""
    assert JobManager._outtmpl(Job(id="a", url="u")) == "[%(id)s] %(title)s.%(ext)s"


@pytest.mark.parametrize("job", [
    Job(id="a", url="u"),                              # descarga suelta
    Job(id="a", url="u", position=7, total=200),       # clase de un curso
])
def test_lo_que_distingue_sobrevive_al_recorte(job):
    """Lo único que separa dos archivos tiene que quedar a la izquierda del
    título, porque el título es lo que se corta."""
    tmpl = JobManager._outtmpl(job)
    antes_del_titulo = tmpl.split("%(title)s")[0]
    assert "%(id)s" in antes_del_titulo or antes_del_titulo.strip(" -").isdigit()


@pytest.mark.parametrize(
    ("position", "total", "prefijo"),
    [(1, 9, "01 - "), (1, 40, "01 - "), (9, 40, "09 - "), (1, 200, "001 - "), (150, 200, "150 - ")],
)
def test_el_ancho_del_prefijo_sale_del_total(position, total, prefijo):
    """Sin prefijo, 40 clases quedan ordenadas alfabéticamente y no por orden."""
    tmpl = JobManager._outtmpl(Job(id="a", url="u", position=position, total=total))
    assert tmpl.startswith(prefijo)


def test_el_outtmpl_es_relativo():
    """Con uno absoluto, yt-dlp IGNORA `paths` y `trim_file_name` corta el directorio.

    Y el warning que lo avisaría se lo traga `no_warnings: True`.
    """
    import os

    tmpl = JobManager._outtmpl(Job(id="a", url="u", position=1, total=10))
    assert not os.path.isabs(tmpl)
    assert os.sep not in tmpl


# ---- tope de largo de nombre ---------------------------------------------


def test_el_recorte_deja_menos_lugar_cuanto_mas_hondo_el_directorio(monkeypatch):
    monkeypatch.setattr("os.name", "nt")
    corto = JobManager._recorte_de_nombre("C:\\d")
    hondo = JobManager._recorte_de_nombre("C:\\" + "x" * 150)
    assert hondo < corto


def test_el_recorte_respeta_max_path(monkeypatch):
    """ffmpeg NO es long-path-aware: el merge falla tras bajar los dos streams."""
    monkeypatch.setattr("os.name", "nt")
    directorio = "C:\\Users\\Guillermo Lopez\\Desktop\\FindTek\\proyectos\\UDEMY_downloader\\downloads"
    recorte = JobManager._recorte_de_nombre(directorio)
    total = len(directorio) + 1 + recorte + JobManager.MARGEN_SUFIJO
    assert total <= JobManager.LARGO_MAXIMO_RUTA


def test_el_recorte_nunca_baja_del_piso(monkeypatch):
    """Por debajo del piso el nombre deja de identificar la clase."""
    monkeypatch.setattr("os.name", "nt")
    assert JobManager._recorte_de_nombre("C:\\" + "x" * 300) == JobManager.LARGO_MINIMO_NOMBRE


# ---- selector de formato --------------------------------------------------


def test_audio_only_ignora_la_calidad():
    assert JobManager._format_selector("720", audio_only=True) == "bestaudio/best"


@pytest.mark.parametrize("q", ["best", "", None])
def test_mejor_disponible(q):
    assert JobManager._format_selector(q, audio_only=False) == "bestvideo*+bestaudio/best"


def test_calidad_acotada_tiene_fallback():
    """Sin el `/best` final, un video sin formato bajo esa altura no baja nada."""
    sel = JobManager._format_selector("720", audio_only=False)
    assert "height<=720" in sel
    assert sel.endswith("/best")


# ---- traducción de errores ------------------------------------------------


def test_saca_las_secuencias_ansi():
    """En el popup y en un log quedaban como basura `\\x1b[0;31m`."""
    assert "\x1b[" not in JobManager._explain("\x1b[0;31mERROR:\x1b[0m algo raro")


@pytest.mark.parametrize(
    ("crudo", "pista"),
    [
        ("ERROR: [udemy] 1: This video is DRM protected", "DRM"),
        ("ERROR: unable to download: HTTP Error 429: Too Many Requests", "429"),
        ("ERROR: [udemy] 1: HTTP Error 403: Forbidden", "403"),
        (
            "ERROR: [udemy] 1: This video is only available for registered users. Use --cookies",
            "sesión iniciada",
        ),
        ("ERROR: You have requested merging but ffmpeg is not installed", "ffmpeg"),
        ("ERROR: Unsupported URL: https://x.com/y", "extractor"),
    ],
)
def test_traduce_los_fallos_comunes(crudo, pista):
    assert pista in JobManager._explain(crudo)


def test_el_429_no_se_lo_come_el_mensaje_de_login():
    """El texto del 429 de yt-dlp menciona cookies: caía en "logueate", que acá
    es el consejo equivocado — hay sesión, lo que falta es bajar el ritmo."""
    salida = JobManager._explain(
        "ERROR: HTTP Error 429: Too Many Requests. Use --cookies-from-browser"
    )
    assert "ritmo" in salida
    assert "logueate" not in salida


def test_lo_que_no_reconoce_pasa_tal_cual():
    assert JobManager._explain("ERROR: algo nuevo y raro") == "ERROR: algo nuevo y raro"


# ---- ruta remota de rclone ------------------------------------------------


@pytest.mark.parametrize(
    ("base", "sub", "esperado"),
    [
        ("Cursos", "Mi Curso", "gdrive:Cursos/Mi Curso"),
        ("/Cursos/", "/Mi Curso/", "gdrive:Cursos/Mi Curso"),
        ("Cursos", None, "gdrive:Cursos"),
        ("", None, "gdrive:"),
        ("", "Mi Curso", "gdrive:Mi Curso"),
    ],
)
def test_destino_sin_barras_duplicadas(base, sub, esperado):
    assert uploader.destino("gdrive", base, sub) == esperado


@pytest.mark.parametrize(
    "mensaje",
    [
        # Los ids de clase de Udemy son numeros de 8 cifras: `"403" in mensaje`
        # matcheaba cualquiera que tuviera esos digitos y lo traducia como un
        # acceso denegado, que es un diagnostico equivocado y manda al usuario a
        # revisar su inscripcion en vez del problema real.
        "ERROR: [udemy] 37403060: Lecture 37403060 is not a video",
        "ERROR: [udemy] 12403999: Unable to download JSON metadata",
        "ERROR: [udemy] 42942999: algo raro",
    ],
)
def test_un_id_con_403_o_429_adentro_no_se_traduce(mensaje):
    assert JobManager._explain(mensaje) == mensaje


@pytest.mark.parametrize(
    ("mensaje", "pista"),
    [
        ("ERROR: [udemy] 1: HTTP Error 403: Forbidden", "403"),
        ("ERROR: [udemy] 1: 403: Forbidden", "403"),
        ("ERROR: unable to download: HTTP Error 429: Too Many Requests", "429"),
    ],
)
def test_los_codigos_http_de_verdad_si_se_traducen(mensaje, pista):
    assert pista in JobManager._explain(mensaje)


def test_el_mensaje_de_403_no_inventa_una_causa():
    """Decía "casi siempre es la sesión" y era falso: en un caso real la sesión
    tenía 87 días de validez y fallaban TODAS las clases del curso. Un mensaje
    traducido se cree más que uno crudo, así que no puede adivinar."""
    salida = JobManager._explain("HTTP Error 403: Forbidden")
    assert "casi siempre" not in salida
    # Enumera los tres patrones y dice dónde está el error completo.
    assert "TODAS" in salida and "últimas" in salida and "errores.log" in salida


# ---- línea de comando de rclone -------------------------------------------


def test_rclone_no_retraduce_los_homoglifos_de_yt_dlp():
    """Sin esto no se sube NINGÚN archivo con `：` o `？` en el nombre —o sea,
    cualquier clase cuyo título tenga dos puntos o un signo de pregunta—.
    rclone traduce esos caracteres en los dos sentidos y termina buscando en
    disco un nombre que no existe."""
    cmd = uploader._comando(r"C:\d\02 - Conceptos clave\uff1a x.mp4", "gdrive:Cursos")
    assert "--local-encoding" in cmd
    assert cmd[cmd.index("--local-encoding") + 1] == "None"


def test_el_comando_de_rclone_conserva_lo_que_ya_estaba():
    cmd = uploader._comando("/x/y.mp4", "gdrive:Cursos/Mi Curso")
    assert cmd[:4] == ["rclone", "copy", "/x/y.mp4", "gdrive:Cursos/Mi Curso"]
    # Sin --no-traverse rclone lista el destino entero antes de subir un archivo.
    assert "--no-traverse" in cmd
    assert cmd[cmd.index("--retries") + 1] == "5"


def test_el_error_crudo_se_conserva_junto_al_traducido(tmp_path, monkeypatch):
    """Cuando la traducción se equivoca —pasó— sin el crudo no queda NADA con
    qué diagnosticar."""
    import downloader

    monkeypatch.setattr(downloader, "ARCHIVO_ERRORES", str(tmp_path / "errores.log"))
    job = Job(id="a", url="https://x/y", position=7, total=96, title="Clase 7")
    crudo = "ERROR: [udemy] 32312110: HTTP Error 403: Forbidden"
    downloader._registrar_fallo(job, crudo)

    escrito = (tmp_path / "errores.log").read_text(encoding="utf-8")
    assert crudo in escrito
    assert "7/96" in escrito
    assert "https://x/y" in escrito


def test_registrar_un_fallo_nunca_tumba_la_descarga(tmp_path, monkeypatch):
    """Un error al escribir el log no puede costar el video."""
    import downloader

    monkeypatch.setattr(downloader, "ARCHIVO_ERRORES", str(tmp_path / "no" / "existe" / "e.log"))
    downloader._registrar_fallo(Job(id="a", url="u"), "algo")   # no debe lanzar
