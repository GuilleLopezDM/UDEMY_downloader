#!/usr/bin/env python3
"""Arma el zip para llevar el proyecto a la máquina donde corre el navegador.

Usa una LISTA BLANCA, no una lista negra.

El motivo es un incidente real: el empaquetado anterior excluía por nombre
(`.token`, `settings.json`, …) y cuando apareció en el proyecto un `.har` de
diagnóstico —con la sesión de Udemy viva adentro— la lista no lo cubría y el
archivo entraba al paquete. Una lista negra sólo protege de lo que alguien se
acordó de anotar; una lista blanca falla del lado seguro: lo que no está
declarado, no viaja.

Uso:  python3 tools/package.py
"""

import fnmatch
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "video-dl-windows.zip"
PREFIJO = "video-dl"

# Lo único que se empaqueta. Cualquier archivo nuevo hay que declararlo acá.
INCLUIR = [
    "README.md",
    # Viaja a propósito: sin él, un `git init` en la máquina destino podría
    # commitear el .token o las descargas.
    ".gitignore",
    "server/*.py",
    "server/requirements.txt",
    "server/run.sh",
    "server/run.ps1",
    "extension/*.js",
    "extension/*.json",
    "extension/*.html",
    "extension/*.css",
    "extension/icons/*.png",
    "downloads/.gitkeep",
]

# Red de seguridad: aunque un patrón de arriba llegara a matchear algo de esto,
# el empaquetado aborta en vez de publicar un secreto.
NUNCA = [
    "*.har",       # capturas de red: sesión viva
    ".token",      # token del servidor
    "settings.json",
    "cookies-*.txt",
    "*.key", "*.pem", "*.env", ".env",
    "*.sqlite", "*.db",           # bases de cookies de navegador
    "*.mp4", "*.mkv", "*.webm", "*.m4a", "*.mp3",
]


def es_prohibido(ruta: Path) -> bool:
    return any(fnmatch.fnmatch(ruta.name, patron) for patron in NUNCA)


def recolectar() -> list[Path]:
    encontrados: list[Path] = []
    for patron in INCLUIR:
        coincidencias = sorted(RAIZ.glob(patron))
        if not coincidencias:
            print(f"  aviso: el patrón '{patron}' no coincidió con nada")
        encontrados.extend(c for c in coincidencias if c.is_file())
    # dedup preservando orden
    vistos, unicos = set(), []
    for f in encontrados:
        if f not in vistos:
            vistos.add(f)
            unicos.append(f)
    return unicos


def main() -> int:
    archivos = recolectar()

    prohibidos = [f for f in archivos if es_prohibido(f)]
    if prohibidos:
        print("ABORTADO: la lista blanca capturó archivos prohibidos:")
        for f in prohibidos:
            print(f"  {f.relative_to(RAIZ)}")
        return 1

    if not archivos:
        print("ABORTADO: no hay nada que empaquetar")
        return 1

    with zipfile.ZipFile(SALIDA, "w", zipfile.ZIP_DEFLATED) as z:
        for f in archivos:
            z.write(f, f"{PREFIJO}/{f.relative_to(RAIZ).as_posix()}")

    print(f"{SALIDA.name}: {len(archivos)} archivos, {SALIDA.stat().st_size // 1024} KB")

    # Verificación independiente sobre el zip ya escrito, no sobre la lista en
    # memoria: si el empaquetado se rompe, esto lo detecta igual.
    with zipfile.ZipFile(SALIDA) as z:
        filtrados = [
            n for n in z.namelist()
            if any(fnmatch.fnmatch(Path(n).name, p) for p in NUNCA)
        ]
    if filtrados:
        print("ERROR: el zip contiene archivos prohibidos:")
        for n in filtrados:
            print(f"  {n}")
        SALIDA.unlink()
        print("zip borrado.")
        return 1

    print("verificación: sin archivos sensibles ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
