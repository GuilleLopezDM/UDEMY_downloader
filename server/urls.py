"""Normalización de URLs antes de entregárselas a yt-dlp.

Existe por un caso concreto: el extractor de Udemy de yt-dlp sólo reconoce
formatos de URL antiguos para una CLASE, y con los actuales elige el extractor
equivocado.

    _VALID_URL de UdemyIE (lecture):
        [^#]+#/lecture/<id>  |  lecture/view/?lectureId=<id>  |  [^/]+/learn/v4/t/lecture/<id>

    _VALID_URL de UdemyCourseIE:
        https?://(?:[^/]+\\.)?udemy\\.com/(?P<id>[^/?#&]+)

Una URL moderna de clase —`/course/<slug>/learn/lecture/<id>`— no matchea
ninguna forma del primero, así que cae en el segundo y yt-dlp baja el CURSO
ENTERO en vez de esa clase. Por eso se traduce a `#/lecture/<id>`, que UdemyIE
sí reconoce. Como el fragmento no viaja en el pedido HTTP, la página que se
termina descargando es `/course/<slug>/`, que es la que existe hoy.

⚠ **El curso NO se traduce, y la versión anterior de este módulo se equivocaba.**

Este archivo decía que una URL de curso `/course/<slug>/` hacía que «yt-dlp pida
la página del curso llamado "course"», y por eso la reescribía a la forma vieja
`/<slug>/`. Eso es falso: `UdemyCourseIE._real_extract` hace
`_download_webpage(url, course_path)` —descarga **la URL tal cual se la das**— y
`course_path` es sólo la etiqueta que aparece en los mensajes de error.

La reescritura era, ella misma, la causa del 403: `/<slug>/` es el formato viejo
y Udemy lo rechaza, mientras que `/course/<slug>/` responde 200. Se comprobó con
las descargas de clases sueltas, que funcionan justamente porque terminan
pidiendo esa segunda URL.

Que `course_path` quede valiendo la palabra literal `course` no rompe nada: sólo
se usa para armar las URLs de cada clase
(`https://www.udemy.com/{course_path}/learn/v4/t/lecture/<id>`), y esas llevan el
`course_id` adjunto con `smuggle_url`, así que `UdemyIE._real_extract` corta
antes de pedir ninguna página (`if not course_id`).
"""

import re

# /course/<slug>/learn/lecture/<id>  → el formato que usa Udemy hoy
_UDEMY_LECTURE = re.compile(
    r"^(?P<base>https?://(?:[^/]+\.)?udemy\.com)/course/(?P<slug>[^/?#]+)"
    r"/learn/lecture/(?P<lecture>\d+)"
)

# /course/<slug>[/...] — se usa sólo para detectar lo que NO es descargable.
_UDEMY_COURSE = re.compile(
    r"^(?P<base>https?://(?:[^/]+\.)?udemy\.com)/course/(?P<slug>[^/?#]+)"
    r"(?P<resto>[/?#].*)?$"
)

# Lo que viene después de `/learn/` cuando la URL apunta a UNA cosa concreta del
# curso que no es una clase de video. Sin esta guarda, una URL de quiz cae en
# UdemyCourseIE y encola el CURSO ENTERO: pegás el link de un examen y te bajás
# 200 clases. `_UDEMY_LECTURE` no salva el caso porque exige `/learn/lecture/<n>`.
_UDEMY_NO_ES_CURSO = re.compile(r"^/learn/(?!lecture/\d)", re.IGNORECASE)


def motivo_no_soportado(url: str) -> str | None:
    """Mensaje explicando por qué esa URL no da un video, o None si sí puede darlo."""
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


def nombre_desde_url(url: str) -> str | None:
    """Nombre legible del curso sacado del slug de la URL, o None.

    Hace falta porque el título del curso puede venir vacío:
    `_extract_course_info` de yt-dlp lo saca de un bloque `ng-init="…course={…}"`
    que las páginas modernas de Udemy ya no traen. El `course_id` sobrevive por
    una regex de respaldo (`data-course-id`), pero el título no, y entonces
    `carpeta_para(None)` devuelve None y las 200 clases del curso caen sueltas en
    la raíz de `downloads/`.

    El slug siempre está: es parte de la URL que pegó el usuario.
    """
    m = _UDEMY_COURSE.match(url)
    if not m:
        return None
    legible = m["slug"].replace("-", " ").replace("_", " ").strip()
    return legible[:1].upper() + legible[1:] if legible else None


# `https://<org>.udemy.com` — el portal de una organización de Udemy Business.
_PORTAL = re.compile(r"^(?P<origen>https?://(?P<sub>[^/.]+)\.udemy\.com)", re.IGNORECASE)


def portal_udemy(url: str) -> str | None:
    """Origen del portal de Udemy Business, o None si es www / no es Udemy.

    Hace falta porque yt-dlp tiene el host de la API clavado en `www.udemy.com`
    (`udemy.py:106`). En Udemy Business el curso lo da la organización y la
    inscripción vive en SU portal: www no la conoce y responde 403 en todas las
    clases, aunque la sesión sea válida.

    El temario sí se lee bien desde www —`api-2.0/courses/<id>/…` no depende de
    la inscripción—, y por eso el curso se enumera entero y después falla clase
    por clase: es el síntoma que más despista de todo esto.
    """
    m = _PORTAL.match(url or "")
    if not m or m["sub"].lower() == "www":
        return None
    return m["origen"]


def normalize(url: str) -> str:
    """Devuelve la URL que yt-dlp entiende. Si no hay nada que traducir, la misma."""
    m = _UDEMY_LECTURE.match(url)
    if m:
        # Forma `#/lecture/<id>`: la que UdemyIE reconoce para una clase suelta.
        # El fragmento no viaja por HTTP, así que la página que se pide es
        # `/course/<slug>/` — la que existe.
        return f"{m['base']}/course/{m['slug']}/#/lecture/{m['lecture']}"

    # El curso va TAL CUAL. Ver el encabezado del módulo: reescribirlo a la forma
    # vieja `/<slug>/` era lo que producía el 403.
    return url
