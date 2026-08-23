"""Nombres de carpeta para las descargas.

`yt_dlp.utils.sanitize_filename` resuelve la mayor parte —reemplaza los
caracteres ilegales por homoglifos que se leen igual (`:` → `：`, `/` → `⧸`)—
pero deja pasar tres casos que rompen en Windows:

* **Puntos y espacios finales**: Windows los recorta en silencio al crear la
  carpeta, así que la ruta que se pidió y la que existe dejan de coincidir.
* **Nombres reservados**: `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`,
  `LPT1`-`LPT9` son dispositivos; una carpeta con ese nombre no se puede crear.
* **Largo**: la ruta completa tiene tope, y acá sólo estamos en la parte del
  directorio — todavía falta el nombre del archivo.
"""

from yt_dlp.utils import sanitize_filename

RESERVADOS_WINDOWS = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# Deja margen para el prefijo numérico, el título de la clase y la extensión.
LARGO_MAXIMO = 120


def carpeta_para(titulo: str | None) -> str | None:
    """Nombre de carpeta seguro a partir del título del curso.

    Devuelve None si no queda nada usable, y entonces la descarga va a la raíz
    de `downloads/` en vez de a una carpeta con un nombre inventado.
    """
    if not titulo:
        return None

    nombre = sanitize_filename(titulo, restricted=False).strip()
    nombre = nombre.rstrip(". ")
    if not nombre:
        return None

    # El nombre reservado lo es CON cualquier extensión: `NUL.txt` sigue siendo
    # el dispositivo NUL. Comparar el nombre completo dejaba pasar esos casos.
    if nombre.split(".", 1)[0].upper() in RESERVADOS_WINDOWS:
        nombre = f"_{nombre}"

    if len(nombre) > LARGO_MAXIMO:
        # El rstrip se repite: cortar puede dejar un punto o espacio al final.
        nombre = nombre[:LARGO_MAXIMO].rstrip(". ")

    return nombre or None
