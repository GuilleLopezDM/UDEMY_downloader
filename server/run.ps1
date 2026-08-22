# Levanta el servidor local en Windows. Crea el venv la primera vez.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    Write-Host "Creando entorno virtual..."
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "ffmpeg no esta instalado. Sin el no se pueden unir video+audio ni convertir a mp3."
    Write-Warning "Instalalo con: winget install Gyan.FFmpeg"
}

& .\.venv\Scripts\python.exe app.py
