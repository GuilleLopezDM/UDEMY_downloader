#!/usr/bin/env bash
# Levanta el servidor local. Crea el venv la primera vez.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creando entorno virtual..."
  # En Debian/Ubuntu, python3-venv puede faltar y entonces ensurepip falla
  # dejando un .venv a medias. En ese caso lo rehacemos sin pip y lo
  # bootstrapeamos, que no necesita sudo.
  if ! python3 -m venv .venv 2>/dev/null; then
    echo "ensurepip no disponible — bootstrapeando pip a mano..."
    rm -rf .venv
    python3 -m venv --without-pip .venv
    curl -sS https://bootstrap.pypa.io/get-pip.py -o ./get-pip.py
    ./.venv/bin/python ./get-pip.py
    rm -f ./get-pip.py
  fi
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo
  echo "AVISO: ffmpeg no está instalado."
  echo "  Sin él NO se puede: unir video+audio (calidades altas) ni convertir a mp3."
  echo "  Sí funcionan las descargas de archivo único."
  echo "  Instalalo con: sudo apt install ffmpeg"
  echo
fi

exec ./.venv/bin/python app.py
