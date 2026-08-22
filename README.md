# video-dl

Extensión de navegador + servidor local que usa [yt-dlp](https://github.com/yt-dlp/yt-dlp)
para descargar videos. Pensada para Udemy, pero funciona con cualquiera de los miles
de sitios que yt-dlp soporta (Odysee, Vimeo, etc.).

Uso personal. No está pensada para publicarse en ninguna store.

## Cómo está armado

```
  Navegador                          Servidor local (Python)
  ┌─────────────────┐                ┌──────────────────────┐
  │ extensión MV3   │  HTTP + token  │ FastAPI              │
  │  · lee la URL   │ ──────────────►│  · /probe            │
  │  · lee cookies  │   127.0.0.1    │  · /download         │
  │  · muestra      │ ◄──────────────│  · /jobs (progreso)  │
  │    progreso     │                │         │            │
  └─────────────────┘                │      yt-dlp          │
                                     │         │            │
                                     │      ffmpeg          │
                                     └──────────────────────┘
                                              ▼
                                          downloads/
```

Una extensión no puede ejecutar Python ni binarios: por eso yt-dlp vive en un
servidor aparte y la extensión solo le habla por HTTP.

**El servidor tiene que correr en la misma máquina que el navegador**, porque la
extensión le pega a `127.0.0.1`. Si el navegador está en tu PC, el servidor va en
tu PC. (Alternativa si querés que corra en un VPS: túnel SSH
`ssh -L 3210:127.0.0.1:3210 usuario@vps`, y los archivos quedan en el VPS.)

## Requisitos

| | |
|---|---|
| Python | 3.10 o superior |
| ffmpeg | **Necesario** para unir video+audio y para mp3. Sin él solo andan las descargas de archivo único |

Instalar ffmpeg:
- Linux: `sudo apt install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`

## Puesta en marcha

### 1. Servidor

```bash
cd server
./run.sh          # Linux/macOS
```
```powershell
cd server
powershell -ExecutionPolicy Bypass -File .\run.ps1    # Windows
```

> En Windows, PowerShell bloquea la ejecucion de scripts por defecto. Por eso el
> `-ExecutionPolicy Bypass`: sin el, `.\run.ps1` falla con *"running scripts is
> disabled on this system"*. El flag aplica solo a esa invocacion, no cambia nada
> del sistema.

La primera vez crea el venv e instala las dependencias. Al arrancar imprime:

```
  video-dl server
  Descargas en: .../downloads
  Token (pegalo en la extensión): <token>
```

Copiá ese token. También queda guardado en `server/.token`.

### 2. Extensión

1. Abrí `chrome://extensions`
2. Activá **Modo desarrollador**
3. **Cargar descomprimida** → elegí la carpeta `extension/`
4. Entrá a **Opciones** de la extensión y pegá el token
5. Listo: el punto del popup queda verde cuando el servidor responde

## Uso

Abrí el video, hacé clic en el ícono y:

- **Analizar** — muestra título, extractor y calidades sin descargar nada
- **Descargar** — encola el trabajo; el progreso se ve en el popup y llega una
  notificación al terminar
- **Usar cookies del navegador** — manda tus cookies del sitio al servidor.
  **Imprescindible en Udemy**: sin sesión iniciada, el curso no se puede leer

Los archivos van a `downloads/`. Se cambia con la variable `VIDEO_DL_DIR`.
El puerto se cambia con `VIDEO_DL_PORT`.

## Udemy

- Requiere **cookies** (dejá el check activado y tené la sesión abierta en el navegador).
- Pegando la URL del curso, yt-dlp lo trata como playlist y baja todas las clases.
- **Los cursos con DRM (Widevine) no se pueden descargar.** yt-dlp no descifra DRM:
  no es un bug ni algo que se arregle con configuración. El servidor te lo dice con
  ese mensaje explícito cuando pasa.

## Ritmo de descarga

Las descargas se hacen **de a una** (nunca dos en paralelo), con una pausa de
duración aleatoria entre trabajos y otra entre videos de un mismo curso. Se
configura en **Opciones** de la extensión.

| Perfil | Velocidad | Pausa entre trabajos | Entre videos |
|---|---|---|---|
| Rápido | sin límite | 0-2 s | — |
| Normal | 2 MB/s | 5-15 s | 3-8 s |
| **Cauteloso** (default) | 800 KB/s | 20-60 s | 10-30 s |
| Paranoico | 400 KB/s | 60-180 s | 30-90 s |

Se guarda en `server/settings.json`. También se puede ajustar campo por campo
desde "Ajuste manual".

### Qué resuelve y qué no

Sirve para dos cosas concretas: **evitar los errores 429** (que cortan una
descarga a la mitad) y **no saturar tu conexión** con un curso entero.

Como mitigación de riesgo de cuenta es **parcial**. Hay tres señales que te
identifican y el ritmo toca una sola:

| Señal | ¿La cubre? |
|---|---|
| Ritmo de requests | Sí — es exactamente para esto |
| Identidad: las cookies son tu sesión | No |
| Cliente: yt-dlp no es el reproductor del sitio | No |

Y no cambia el **volumen total**: un curso entero bajado despacio sigue siendo
un curso entero. Lo que más pesa es cuánto descargás, después en qué plataforma,
y recién tercero a qué ritmo.

## Cursos completos

Pegá la URL del curso (no la de una clase suelta) y dale **Analizar**. Si detecta
varias clases, aparece **Descargar curso completo (N)**: encola **un job por
clase**, no un job gigante.

El fan-out importa para el ritmo: la pausa entre trabajos sólo se aplica **entre
jobs**. Un curso entero como un único job bajaría las 40 clases casi seguidas,
salteándose el freno.

Cuánto tarda, con 40 clases:

| Perfil | Sólo las pausas |
|---|---|
| Normal (5-15 s) | ~7 min |
| Cauteloso (20-60 s) | ~27 min |

Más el tiempo de descarga real de cada video.

### Cortar y retomar

Lo ya descargado queda registrado en `downloads/.video-dl-descargados.txt`. Si el
servidor se reinicia a mitad de un curso, **volvé a encolarlo**: baja sólo lo que
falta. Los jobs viven en memoria y no sobreviven a un reinicio, pero el registro sí.

> **Ojo:** si borrás un video ya descargado y reencolás el curso, **no vuelve a
> bajarlo** — para el registro ya lo tenés. Para forzar que lo baje de nuevo,
> borrá su línea de `.video-dl-descargados.txt`, o el archivo entero para
> reempezar de cero.

## Seguridad

Decisiones deliberadas, no las aflojes sin pensarlo:

- El servidor **bindea solo a `127.0.0.1`**. Nunca a `0.0.0.0`: quedaría expuesto.
- Todos los endpoints salvo `/health` **exigen el token**. Hace falta porque
  cualquier página web que visites también puede hacer `fetch` a tu localhost.
- CORS acepta únicamente orígenes `chrome-extension://` y `moz-extension://`.
- Las cookies se escriben a un archivo temporal en modo `600` y **se borran apenas
  termina el job**. Nunca se loguean.
- `server/.token` está en modo `600`. Si lo compartís, compartís el acceso.

## Problemas comunes

| Síntoma | Causa |
|---|---|
| Punto rojo en el popup | El servidor no está corriendo, o el puerto no coincide con Opciones |
| `Token inválido` | El token de Opciones no es el de `server/.token` |
| `Hace falta sesión iniciada` | Activá "Usar cookies" y verificá que estés logueado en ese sitio |
| `El video está protegido con DRM` | No hay solución; el contenido no es descargable |
| `Falta ffmpeg` | Instalalo y reiniciá el servidor |
| Falla al crear el venv | Falta `python3-venv`. `run.sh` ya lo esquiva bootstrapeando pip sin sudo |
| `running scripts is disabled` | PowerShell bloquea scripts. Usá `powershell -ExecutionPolicy Bypass -File .\run.ps1` |
| `python` no se reconoce | Cerrá y reabrí la terminal despues de instalar Python (PATH) |

## Mantenimiento

yt-dlp se rompe seguido porque los sitios cambian. Cuando algo deje de andar,
antes que nada actualizalo:

```bash
./server/.venv/bin/python -m pip install --upgrade yt-dlp
```
