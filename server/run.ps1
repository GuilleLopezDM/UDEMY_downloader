# Levanta el servidor local en Windows. Crea el venv la primera vez.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# OJO: `$ErrorActionPreference = "Stop"` NO corta cuando falla un .exe — solo
# aplica a los cmdlets de PowerShell. Por eso cada llamada a python/pip revisa
# `$LASTEXITCODE` a mano. Sin esto, un `pip install` que fallara a mitad (red
# caída, disco lleno) dejaba un venv incompleto que se daba por bueno para
# siempre, y el error real aparecía después como un ImportError sin contexto.
function Fallar($mensaje) {
    Write-Host ""
    Write-Host "ERROR: $mensaje" -ForegroundColor Red
    exit 1
}

$py     = ".\.venv\Scripts\python.exe"
$marca  = ".\.venv\.instalado"
$reqSum = (Get-FileHash .\requirements.txt -Algorithm SHA256).Hash

# El venv se creaba una sola vez y nunca se volvía a mirar: cambiar
# requirements.txt no reinstalaba nada. La marca guarda el hash del archivo con
# el que se instaló, así que un cambio se detecta solo.
$instalar = $true
if ((Test-Path $py) -and (Test-Path $marca)) {
    if ((Get-Content $marca -Raw).Trim() -eq $reqSum) {
        $instalar = $false
    } else {
        Write-Host "requirements.txt cambió: reinstalando dependencias..."
    }
}

if (-not (Test-Path $py)) {
    Write-Host "Creando entorno virtual..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        # Un venv a medias es peor que ninguno: se borra para que el próximo
        # intento arranque limpio en vez de heredar el destrozo.
        Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
        Fallar "No se pudo crear el entorno virtual. ¿Está Python en el PATH? (probá 'python --version')"
    }
    $instalar = $true
}

if ($instalar) {
    & $py -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Fallar "Falló la actualización de pip." }
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -Force $marca -ErrorAction SilentlyContinue
        Fallar "Falló la instalación de dependencias. Revisá la conexión y volvé a correr este script."
    }
    Set-Content -Path $marca -Value $reqSum -NoNewline
    Write-Host "Dependencias instaladas."
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "ffmpeg no esta instalado. Sin el no se pueden unir video+audio ni convertir a mp3."
    Write-Warning "Instalalo con: winget install Gyan.FFmpeg"
}

# Cinturón y tiradores para el encoding. `app.py` ya reconfigura stdout a UTF-8,
# pero si algo falla ANTES de esa línea (un ImportError, por ejemplo) el
# traceback sale con la code page del sistema —cp1252 en un Windows en español—
# y se pierde si tiene caracteres que no entran.
$env:PYTHONUTF8 = "1"

& $py app.py
