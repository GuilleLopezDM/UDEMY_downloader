"""Normalización de URLs antes de entregárselas a yt-dlp.

Existe por un caso concreto: el extractor de Udemy de yt-dlp sólo reconoce
formatos de URL antiguos, y con los actuales elige el extractor equivocado.

    _VALID_URL de UdemyIE (lecture):
        [^#]+#/lecture/<id>  |  lecture/view/?lectureId=<id>  |  [^/]+/learn/v4/t/lecture/<id>

    _VALID_URL de UdemyCourseIE:
        https?://(?:[^/]+\\.)?udemy\\.com/(?P<id>[^/?#&]+)

Una URL moderna —`/course/<slug>/learn/lecture/<id>`— no matchea ninguna forma
del primero, así que cae en el segundo, cuyo `(?P<id>[^/?#&]+)` se queda con el
primer segmento del path: la palabra literal `course`. yt-dlp entonces pide la
página del curso llamado "course", que no existe, y Udemy responde 403.

El 403 no tenía nada que ver con las cookies ni con los subdominios de Udemy
Business, que fue donde se buscó primero.
"""

import re

# /course/<slug>/learn/lecture/<id>  → el formato que usa Udemy hoy
_UDEMY_LECTURE = re.compile(
    r"^(?P<base>https?://(?:[^/]+\.)?udemy\.com)/course/(?P<slug>[^/?#]+)"
    r"/learn/lecture/(?P<lecture>\d+)"
)

# /course/<slug>[/...] sin lecture → el curso entero
_UDEMY_COURSE = re.compile(
    r"^(?P<base>https?://(?:[^/]+\.)?udemy\.com)/course/(?P<slug>[^/?#]+)"
    r"(?P<resto>[/?#].*)?$"
)

# Lo que viene después de `/learn/` cuando la URL apunta a UNA cosa concreta del
# curso que no es una clase de video. `_UDEMY_COURSE` matchea cualquier
# `/course/<slug>/...`, así que sin esta guarda una URL de quiz se degradaba en
# silencio al curso ENTERO: pegabas el link de un examen y te encolaba 200
# descargas. `_UDEMY_LECTURE` no salva el caso porque exige `/learn/lecture/<n>`.
_UDEMY_NO_ES_CURSO = re.compile(r"^/learn/(?!lecture/\d)", re.IGNORECASE)


def motivo_no_soportado(url: str) -> str | None:
    """Mensaje explicando por qué esa URL no da un video, o None si sí puede darlo.

    Vale la pena adelantarse a yt-dlp. Una URL de quiz no la traduce `normalize`,
    pero igual matchea el `_VALID_URL` de `UdemyCourseIE` —que acepta cualquier
    `udemy.com/<algo>`—, así que yt-dlp se queda con la palabra literal `course`
    como slug, pide una página que no existe y devuelve **403**: exactamente el
    error indescifrable que motivó este módulo.
    """
    m = _UDEMY_COURSE.match(url)
    if not m:
        return None
    resto = m["resto"] or ""
    if not _UDEMY_NO_ES_CURSO.match(resto):
        return None
    partes = [p for p in resto.split("/") if p]
    seccion = partes[1] if len(partes) > 1 else "esa sección"
    return (
        f"Esa URL apunta a «{seccion}» dentro del curso, y ahí no hay ningún video. "
        "Pegá la URL de una clase (.../learn/lecture/...) o la del curso "
        f"(https://www.udemy.com/course/{m['slug']}/) y usá «Analizar»."
    )


def normalize(url: str) -> str:
    """Devuelve la URL que yt-dlp entiende. Si no hay nada que traducir, la misma."""
    m = _UDEMY_LECTURE.match(url)
    if m:
        # Forma `#/lecture/<id>`: la que UdemyIE reconoce para una clase suelta.
        return f"{m['base']}/course/{m['slug']}/#/lecture/{m['lecture']}"

    m = _UDEMY_COURSE.match(url)
    if m:
        resto = m["resto"] or ""
        if _UDEMY_NO_ES_CURSO.match(resto):
            # Quiz, práctica, tarea, artículo: no es el curso. Se devuelve tal
            # cual para que yt-dlp falle con "Unsupported URL" —un error honesto—
            # en vez de encolar el curso completo que nadie pidió.
            return url
        # UdemyCourseIE espera el slug como primer segmento, sin `/course/`.
        return f"{m['base']}/{m['slug']}/"

    return url
