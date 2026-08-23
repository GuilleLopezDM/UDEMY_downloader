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


_ID_DE_CLASE = re.compile(r"/lecture/(?P<id>\d+)")


def preservar_portal(url_curso: str, url_clase: str) -> str:
    """Rearma la URL de una clase sobre el portal y el slug del curso original.

    No alcanza con cambiar el host. El extractor de cursos de yt-dlp arma cada
    clase como `www.udemy.com/<slug>/learn/v4/t/lecture/<id>` (línea 458 de
    `yt_dlp/extractor/udemy.py`): host equivocado **y** path sin `/course/`.

    Y el path importa porque el extractor de clase saca el `course_id`
    **leyendo esa página** (`_real_extract` → `_download_webpage` →
    `_extract_course_info`). Si la página no existe, se queda sin `course_id`
    y la llamada a la API devuelve 403.

    Así que se reconstruye con la forma que sí demostró funcionar en este
    portal: `https://<portal>/course/<slug>/#/lecture/<id>`.

    Si algo no se puede parsear, devuelve la URL original en vez de inventar.
    """
    m_id = _ID_DE_CLASE.search(url_clase)
    m_curso = _UDEMY_COURSE.match(url_curso)
    if not m_id or not m_curso:
        return url_clase
    return f"{m_curso['base']}/course/{m_curso['slug']}/#/lecture/{m_id['id']}"


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
