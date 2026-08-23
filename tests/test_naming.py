"""`carpeta_para()`: los tres casos que `sanitize_filename` de yt-dlp deja pasar."""

import pytest

from naming import LARGO_MAXIMO, carpeta_para


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        # Nombres reservados de Windows. Son dispositivos: la carpeta no se
        # puede crear, con o sin extensión.
        ("CON", "_CON"),
        ("con", "_con"),
        ("COM1", "_COM1"),
        ("LPT9", "_LPT9"),
        # `NUL.txt` SIGUE siendo el dispositivo NUL. El check comparaba el
        # nombre completo, así que este caso se colaba.
        ("NUL.txt", "_NUL.txt"),
        ("aux.js", "_aux.js"),
        # Un nombre que sólo empieza igual no es reservado.
        ("CONTABILIDAD", "CONTABILIDAD"),
    ],
)
def test_nombres_reservados_de_windows(titulo, esperado):
    assert carpeta_para(titulo) == esperado


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        # Windows recorta puntos y espacios finales en silencio: la ruta que se
        # pidió y la que existe dejarían de coincidir.
        ("Curso....", "Curso"),
        ("Curso   ", "Curso"),
        ("Curso . . ", "Curso"),
    ],
)
def test_puntos_y_espacios_finales(titulo, esperado):
    assert carpeta_para(titulo) == esperado


@pytest.mark.parametrize("titulo", ["", None, "   ", "...", ". . ."])
def test_sin_nada_usable_devuelve_none(titulo):
    """None significa "a la raíz de downloads/", no una carpeta inventada."""
    assert carpeta_para(titulo) is None


def test_ilegales_se_reemplazan_por_homoglifos():
    """`:` y `/` no pueden ir en un nombre de archivo de Windows."""
    assert carpeta_para("Curso: Diseño / Arquitectura") == "Curso： Diseño ⧸ Arquitectura"


def test_largo_acotado():
    """La ruta completa tiene tope y acá todavía falta el nombre del archivo."""
    nombre = carpeta_para("A" * 500)
    assert len(nombre) == LARGO_MAXIMO


def test_el_corte_no_deja_un_punto_final():
    """Cortar a lo bruto puede dejar justo un punto o un espacio al final."""
    titulo = "B" * (LARGO_MAXIMO - 1) + ".. ..."
    nombre = carpeta_para(titulo)
    assert not nombre.endswith((".", " "))
