#!/usr/bin/env bash
# Levanta el servidor local. Crea el venv la primera vez.
set -euo pipefail
cd "$(dirname "$0")"

PY=./.venv/bin/python
MARCA=./.venv/.instalado

suma_requirements() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum requirements.txt | cut -d' ' -f1
  else
    shasum -a 256 requirements.txt | cut -d' ' -f1
  fi
}
REQ_SUM="$(suma_requirements)"

# El venv se creaba una sola vez y nunca se volvía a mirar: cambiar
# requirements.txt no reinstalaba nada. La marca guarda el hash del archivo con
# el que se instaló, así que un cambio se detecta solo.
instalar=1
if [ -x "$PY" ] && [ -f "$MARCA" ] && [ "$(cat "$MARCA")" = "$REQ_SUM" ]; then
  instalar=0
elif [ -x "$PY" ] && [ -f "$MARCA" ]; then
  echo "requirements.txt cambió: reinstalando dependencias..."
fi

if [ ! -x "$PY" ]; then
  echo "Creando entorno virtual..."
  # En Debian/Ubuntu, python3-venv puede faltar y entonces ensurepip falla
  # dejando un .venv a medias. En ese caso lo rehacemos sin pip y lo
  # bootstrapeamos, que no necesita sudo.
  if ! python3 -m venv .venv 2>/dev/null; then
    echo "ensurepip no disponible — bootstrapeando pip a mano..."
    rm -rf .venv
    python3 -m venv --without-pip .venv
    # -f: que un 404 o un 503 salgan como error en vez de guardar la página de
    # error y ejecutarla como Python (el SyntaxError resultante no dice nada).
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o ./get-pip.py
    "$PY" ./get-pip.py
    rm -f ./get-pip.py
  fi
  instalar=1
fi

if [ "$instalar" = "1" ]; then
  # Un venv a medias es peor que ninguno: si la instalación falla, se borra la
  # marca para que el próximo arranque reintente en vez de darlo por bueno.
  rm -f "$MARCA"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r requirements.txt
  printf '%s' "$REQ_SUM" > "$MARCA"
  echo "Dependencias instaladas."
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo
  echo "AVISO: ffmpeg no está instalado."
  echo "  Sin él NO se puede: unir video+audio (calidades altas) ni convertir a mp3."
  echo "  Sí funcionan las descargas de archivo único."
  echo "  Instalalo con: sudo apt install ffmpeg"
  echo
fi

exec "$PY" app.py
