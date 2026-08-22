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
)


def normalize(url: str) -> str:
    """Devuelve la URL que yt-dlp entiende. Si no hay nada que traducir, la misma."""
    m = _UDEMY_LECTURE.match(url)
    if m:
        # Forma `#/lecture/<id>`: la que UdemyIE reconoce para una clase suelta.
        return f"{m['base']}/course/{m['slug']}/#/lecture/{m['lecture']}"

    m = _UDEMY_COURSE.match(url)
    if m:
        # UdemyCourseIE espera el slug como primer segmento, sin `/course/`.
        return f"{m['base']}/{m['slug']}/"

    return url
