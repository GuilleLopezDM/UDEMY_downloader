"""`normalize()` es el módulo con peor historial del proyecto.

Causó el 403 de Udemy (el extractor elegía `UdemyCourseIE` y se quedaba con la
palabra literal `course` como slug) y después el churn de `preservar_portal`,
agregada y borrada en dos sesiones seguidas. Nunca tuvo un solo caso de prueba.
"""

import pytest

from urls import normalize


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        # Clase suelta: la forma `#/lecture/<id>` es la única que UdemyIE
        # reconoce (su _VALID_URL no matchea `/course/<slug>/learn/lecture/<id>`).
        (
            "https://www.udemy.com/course/system-design/learn/lecture/37411654",
            "https://www.udemy.com/course/system-design/#/lecture/37411654",
        ),
        # El portal de Udemy Business tiene que sobrevivir: ahí vive la inscripción.
        (
            "https://empresa.udemy.com/course/sys/learn/lecture/1?start=0",
            "https://empresa.udemy.com/course/sys/#/lecture/1",
        ),
        ("http://www.udemy.com/course/sys/learn/lecture/1", "http://www.udemy.com/course/sys/#/lecture/1"),
        # Curso entero: UdemyCourseIE espera el slug como primer segmento.
        ("https://www.udemy.com/course/system-design/", "https://www.udemy.com/system-design/"),
        ("https://www.udemy.com/course/system-design", "https://www.udemy.com/system-design/"),
    ],
)
def test_traduce_lo_que_yt_dlp_no_entiende(entrada, esperado):
    assert normalize(entrada) == esperado


@pytest.mark.parametrize(
    "entrada",
    [
        # Quiz, práctica y tarea NO son el curso. Antes `_UDEMY_COURSE` matcheaba
        # cualquier `/course/<slug>/...` y devolvía el curso entero: pegabas el
        # link de un examen y te encolaba 200 descargas sin avisar.
        "https://www.udemy.com/course/sys/learn/quiz/12345",
        "https://www.udemy.com/course/sys/learn/practice/999",
        "https://www.udemy.com/course/sys/learn/assignment/7",
        "https://www.udemy.com/course/sys/learn/article/42",
        # Sin `/course/` no hay nada que traducir.
        "https://www.udemy.com/user/algo/",
        # Otros sitios pasan intactos: la herramienta acepta cualquiera que
        # soporte yt-dlp.
        "https://www.youtube.com/watch?v=abc",
        "https://odysee.com/@x:1/y:2",
    ],
)
def test_devuelve_la_misma_url(entrada):
    assert normalize(entrada) == entrada


def test_una_leccion_dentro_del_slug_no_se_confunde_con_un_quiz():
    """`/learn/lecture/<n>` sigue siendo una clase aunque venga con fragmento."""
    url = "https://www.udemy.com/course/sys/learn/lecture/37411654#overview"
    assert normalize(url) == "https://www.udemy.com/course/sys/#/lecture/37411654"


@pytest.mark.parametrize(
    ("url", "palabra"),
    [
        ("https://www.udemy.com/course/sys/learn/quiz/12345", "quiz"),
        ("https://www.udemy.com/course/sys/learn/practice/9", "practice"),
        ("https://www.udemy.com/course/sys/learn/assignment/7", "assignment"),
    ],
)
def test_avisa_antes_de_que_yt_dlp_devuelva_un_403(url, palabra):
    """`normalize` deja pasar la URL de un quiz, pero yt-dlp la matchea igual con
    `UdemyCourseIE` (su _VALID_URL acepta cualquier `udemy.com/<algo>`), se queda
    con la palabra literal `course` como slug y termina en 403. Adelantarse es la
    diferencia entre un mensaje útil y el mismo error indescifrable de siempre."""
    from urls import motivo_no_soportado

    motivo = motivo_no_soportado(url)
    assert motivo is not None
    assert palabra in motivo
    # Y le dice al usuario qué pegar en su lugar.
    assert "learn/lecture" in motivo


@pytest.mark.parametrize(
    "url",
    [
        "https://www.udemy.com/course/sys/learn/lecture/37411654",
        "https://www.udemy.com/course/sys/",
        "https://www.youtube.com/watch?v=abc",
    ],
)
def test_lo_descargable_no_dispara_el_aviso(url):
    from urls import motivo_no_soportado

    assert motivo_no_soportado(url) is None
