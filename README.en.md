# video-dl

[![Tests](https://github.com/GuilleLopezDM/UDEMY_downloader/actions/workflows/tests.yml/badge.svg)](https://github.com/GuilleLopezDM/UDEMY_downloader/actions/workflows/tests.yml)

🇪🇸 [Versión en español](README.md)

Browser extension + local server that uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) to download videos. Built with Udemy in mind, but it works with any of the thousands of sites yt-dlp supports (Odysee, Vimeo, etc.).

For personal use only. It is not meant to be published in any extension store.

## Architecture

```
  Browser                            Local server (Python)
  ┌─────────────────┐                ┌──────────────────────┐
  │ MV3 extension   │  HTTP + token  │ FastAPI              │
  │  · reads URL    │ ──────────────►│  · /probe /diagnose  │
  │  · reads cookies│   127.0.0.1    │  · /download         │
  │  · shows        │ ◄──────────────│  · /jobs (progress)  │
  │    progress     │                │         │            │
  └─────────────────┘                │      yt-dlp          │
                                     │         │            │
                                     │      ffmpeg          │
                                     └──────────────────────┘
                                              ▼
                                          downloads/
```

A browser extension cannot run Python or native binaries, so yt-dlp lives in a separate server and the extension talks to it over HTTP. The server must run on the same machine as the browser, because the extension calls `127.0.0.1`.

## Features

- **Analyze** a URL (title, extractor, available qualities) before downloading.
- **Job queue** with live progress in the popup and a notification when done.
- **Full courses:** a course URL is split into **one job per lesson** (fan-out), so pacing applies between lessons.
- **Download pacing profiles** (Fast, Normal, Cautious, Paranoid) with bandwidth limits and random pauses to avoid HTTP 429 errors.
- **Resume:** already downloaded lessons are recorded, so re-queuing a course only fetches what is missing.
- **Optional upload to Google Drive** (or any remote) with [rclone](https://rclone.org/), with optional local cleanup.
- **Security:** token required on every endpoint except `/health`, loopback-only by default, CORS limited to browser extensions (optionally to a single extension ID), and cookies written to a `600` temp file that is deleted when the job ends and never logged.
- Clear error message for DRM-protected (Widevine) content, which yt-dlp cannot decrypt.

## Requirements

| | |
|---|---|
| Python | 3.10 or newer |
| ffmpeg | **Required** to merge video+audio and to produce mp3 |

## Getting started

### 1. Server

```bash
cd server
./run.sh          # Linux/macOS
```
```powershell
cd server
powershell -ExecutionPolicy Bypass -File .\run.ps1    # Windows
```

The first run creates a virtualenv, installs dependencies and prints an access token (also saved to `server/.token`).

### 2. Extension

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. **Load unpacked** → select the `extension/` folder
4. Open the extension **Options** and paste the token
5. The popup indicator turns green when the server responds

### Environment variables

| Variable | Purpose |
|---|---|
| `VIDEO_DL_DIR` | Output folder. Default: `downloads/` |
| `VIDEO_DL_PORT` | Server port. Default: `3210` |
| `VIDEO_DL_HOST` | Bind address. Default: `127.0.0.1`. Changing it exposes the server without TLS; meant for a Tailscale IP, never `0.0.0.0` |
| `VIDEO_DL_EXT_ID` | Restricts CORS to a single extension ID |

## Tests

```bash
pip install -r server/requirements-dev.txt
pytest -q
```

199 tests that run in under a second, with no network access. GitHub Actions runs them on Python 3.10 and 3.12 on every push.

## Stack

`Python` · `FastAPI` · `uvicorn` · `yt-dlp` · `ffmpeg` · `JavaScript (Manifest V3)` · `pytest` · `rclone`

For the full documentation (pacing details, Drive upload setup, troubleshooting), see the [Spanish README](README.md).
