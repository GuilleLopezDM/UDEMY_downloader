"""Batería de peticiones para saber POR QUÉ Udemy responde 403.

Existe porque el 403 de Udemy Business se resistió a tres hipótesis seguidas
—sesión vencida, límite de ritmo, host de la API— y cada una costó un parche.
Razonar sobre el código no alcanzaba: hacía falta ver qué contesta el sitio.

Esto no descarga nada ni sube nada: pide, mira el código de estado y lo informa.
Los valores de las cookies **nunca** salen de acá; de un token sólo se publica una
huella de 8 caracteres, que sirve para comparar dos tokens entre sí y para nada más.
"""

import hashlib
import json
import urllib.error
import urllib.request
from typing import Iterable, Mapping
from urllib.parse import urlsplit

TIEMPO_LIMITE = 20

# Los que manda yt-dlp (`yt_dlp/utils/networking.py:161-166`). Se replican tal
# cual para poder comparar contra un navegador completo: si con estos falla y con
# los otros anda, el problema son los headers y no la sesión.
HEADERS_YTDLP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-us,en;q=0.5",
    "Sec-Fetch-Mode": "navigate",
}

# El que `UdemyIE._download_webpage_handle` escribe a mano (`udemy.py:125`):
# Chrome 72, de enero de 2019. Se prueba tal cual para saber si la protección
# anti-bot de Udemy lo rechaza — que es la única forma de saberlo con certeza.
HEADERS_UA_2019 = {
    **HEADERS_YTDLP,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/72.0.3626.109 Safari/537.36"
    ),
}

# Lo que agrega un navegador de verdad al pedir una página.
HEADERS_NAVEGADOR = {
    **HEADERS_YTDLP,
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Chromium";v="120", "Not:A-Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


def huella(valor: str) -> str:
    """Identifica un token sin revelarlo. Sólo sirve para comparar dos entre sí."""
    return hashlib.sha256((valor or "").encode()).hexdigest()[:8]


def _aplica(cookie: Mapping, host: str) -> bool:
    """¿El navegador mandaría esta cookie a ese host?"""
    dominio = (cookie.get("domain") or "").lower().lstrip(".")
    if not dominio:
        return False
    host = host.lower()
    if cookie.get("hostOnly"):
        return host == dominio
    return host == dominio or host.endswith("." + dominio)


def cabecera_cookies(cookies: Iterable[Mapping], host: str) -> str:
    aplicables = [c for c in (cookies or []) if _aplica(c, host)]
    return "; ".join(f"{c.get('name')}={c.get('value')}" for c in aplicables)


def tokens_por_dominio(cookies: Iterable[Mapping]) -> list[dict]:
    """Qué `access_token` hay y de qué dominio viene cada uno.

    Importa porque `UdemyIE._download_json` (`udemy.py:147-152`) recorre el jar
    **sin filtrar por dominio** para armar el `X-Udemy-Authorization`: con dos
    access_token en el jar, el que gana es el último que aparezca, que no tiene
    por qué ser el del portal.
    """
    return [
        {
            "nombre": c.get("name"),
            "dominio": c.get("domain"),
            "host_only": bool(c.get("hostOnly")),
            "huella": huella(c.get("value") or ""),
        }
        for c in (cookies or [])
        if c.get("name") in ("access_token", "client_id")
    ]


def _pedir(url: str, cookies: Iterable[Mapping], headers: dict, udemy_api: bool = False) -> dict:
    host = urlsplit(url).hostname or ""
    cab = dict(headers)
    galletas = cabecera_cookies(cookies, host)
    if galletas:
        cab["Cookie"] = galletas
    if udemy_api:
        # Los mismos que arma el extractor, pero con el token que SÍ corresponde
        # al host que se está consultando.
        cab["X-Udemy-Snail-Case"] = "true"
        cab["X-Requested-With"] = "XMLHttpRequest"
        cab["Accept"] = "application/json, text/plain, */*"
        for c in cookies or []:
            if not _aplica(c, host):
                continue
            if c.get("name") == "client_id":
                cab["X-Udemy-Client-Id"] = c.get("value")
            elif c.get("name") == "access_token":
                cab["X-Udemy-Bearer-Token"] = c.get("value")
                cab["X-Udemy-Authorization"] = f"Bearer {c.get('value')}"

    req = urllib.request.Request(url, headers=cab)
    try:
        with urllib.request.urlopen(req, timeout=TIEMPO_LIMITE) as r:
            cuerpo = r.read(4096)
            return {
                "estado": r.status,
                "bytes": len(cuerpo),
                "cookies_enviadas": len(galletas.split("; ")) if galletas else 0,
                "pista": _pista(cuerpo),
            }
    except urllib.error.HTTPError as e:
        return {
            "estado": e.code,
            "bytes": 0,
            "cookies_enviadas": len(galletas.split("; ")) if galletas else 0,
            "pista": _pista(e.read(2048)),
        }
    except Exception as e:  # noqa: BLE001
        return {"estado": None, "error": f"{type(e).__name__}: {e}"[:160]}


def _pista(cuerpo: bytes) -> str:
    """Algo del cuerpo que ayude, sin volcar la página entera."""
    try:
        texto = cuerpo.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""
    if texto.lstrip().startswith("{"):
        try:
            return json.dumps(json.loads(texto))[:220]
        except Exception:  # noqa: BLE001
            pass
    for marca in ("Access Denied", "captcha", "Request unsuccessful", "cloudflare",
                  "Attention Required", "bot", "Please enable"):
        if marca.lower() in texto.lower():
            return f"el cuerpo menciona «{marca}»"
    return texto.strip().replace("\n", " ")[:120]


def diagnosticar(url: str, cookies: list[dict] | None) -> dict:
    """Prueba las hipótesis del 403 de una, y devuelve códigos de estado."""
    cookies = cookies or []
    partes = urlsplit(url)
    portal = f"{partes.scheme}://{partes.netloc}"
    es_portal = partes.hostname and partes.hostname.lower() not in ("www.udemy.com", "udemy.com")

    resultados: dict = {}

    # ANTES que nada. La prueba equivalente venía última, después de seis
    # peticiones seguidas, así que un bloqueo por ráfaga y uno por huella de
    # cliente se veían igual. Corriéndola primera, un 200 acá culpa a la ráfaga.
    resultados["7a_yt_dlp_PRIMERO"] = _por_yt_dlp(url, cookies)
    # La misma, con el TLS amplio. Se compara contra 7a en igualdad de ráfaga.
    resultados["7b_yt_dlp_tls_amplio"] = _por_yt_dlp(url, cookies, tls_amplio=True)

    pruebas = {
        # 1 y 1b: la MISMA página, el UA moderno contra el Chrome 72 que
        # `udemy.py:125` escribe a mano. Si 1 anda y 1b no, es el User-Agent.
        "1_pagina_ua_moderno": lambda: _pedir(url, cookies, HEADERS_YTDLP),
        "1b_pagina_ua_de_2019_de_udemy_py": lambda: _pedir(url, cookies, HEADERS_UA_2019),
        "2_pagina_headers_navegador": lambda: _pedir(url, cookies, HEADERS_NAVEGADOR),
        # 3 y 4: ¿la sesión vale en el portal? ¿y en www? Es la comparación que
        # dice si el host de la API es el problema.
        "3_api_quien_soy_en_el_portal": lambda: _pedir(
            f"{portal}/api-2.0/users/me/?fields[user]=id,title", cookies, HEADERS_YTDLP, udemy_api=True),
        "4_api_quien_soy_en_www": lambda: _pedir(
            "https://www.udemy.com/api-2.0/users/me/?fields[user]=id,title",
            cookies, HEADERS_YTDLP, udemy_api=True),
        # 5 y 6: ¿figurás inscripto? Es el endpoint que falla en las clases.
        "5_mis_cursos_en_el_portal": lambda: _pedir(
            f"{portal}/api-2.0/users/me/subscribed-courses/?page_size=1", cookies,
            HEADERS_YTDLP, udemy_api=True),
        "6_mis_cursos_en_www": lambda: _pedir(
            "https://www.udemy.com/api-2.0/users/me/subscribed-courses/?page_size=1",
            cookies, HEADERS_YTDLP, udemy_api=True),
    }
    resultados.update({nombre: fn() for nombre, fn in pruebas.items()})
    # Y la misma de nuevo, ahora ÚLTIMA: si 7a pasó y ésta no, es la ráfaga.
    resultados["7c_yt_dlp_ULTIMO"] = _por_yt_dlp(url, cookies)
    return {
        "url": url,
        "portal": portal if es_portal else None,
        "tokens": tokens_por_dominio(cookies),
        "pruebas": resultados,
        "lectura": _leer(resultados),
    }


def _por_yt_dlp(url: str, cookies: list[dict], tls_amplio: bool = False) -> dict:
    """Pide la página por el stack de yt-dlp y LEE el cuerpo del 403.

    Es la única prueba que reproduce la condición real: mismo jar de cookies,
    mismos handlers, mismos parches. Y sobre todo, mira lo que el extractor no
    puede mirar: `UdemyIE._download_webpage_handle` busca los marcadores de
    PerimeterX (`udemy.py:129-139`) DESPUÉS de una respuesta exitosa, y un 403
    revienta antes en `common.py:908-920`. O sea que el propio yt-dlp nunca
    distingue "no autorizado" de "te bloqueó el anti-bot": las dos cosas salen
    como el mismo `HTTP Error 403` genérico.
    """
    jar = None
    try:
        import yt_dlp
        from yt_dlp.networking import Request
        from yt_dlp.networking.exceptions import HTTPError as YtHTTPError

        import downloader  # aplica los parches al importarse
        from cookies import discard, write_netscape_cookiefile
        from urls import portal_udemy

        downloader.parchear_udemy_business()
        jar = write_netscape_cookiefile(cookies) if cookies else None
        opciones = {"quiet": True, "no_warnings": True}
        if jar:
            opciones["cookiefile"] = jar
        if (portal := portal_udemy(url)):
            opciones["portal_udemy"] = portal
        if tls_amplio:
            # `legacyserverconnect` no sólo permite servidores viejos: cambia el
            # contexto TLS a la lista COMPLETA de cifrados (60 en vez de los 17
            # que yt-dlp usa por defecto), o sea exactamente la misma que urllib.
            # Y la petición de urllib devuelve 200. Si esto también devuelve 200,
            # lo que bloquea es la huella del handshake, no nada de HTTP.
            opciones["legacyserverconnect"] = True

        with yt_dlp.YoutubeDL(opciones) as ydl:
            try:
                r = ydl.urlopen(Request(url))
                estado, cuerpo, cabeceras = r.status, r.read(4096), dict(r.headers)
            except YtHTTPError as e:
                estado = e.status
                cuerpo = e.response.read(4096)
                cabeceras = dict(e.response.headers)

        return {
            "estado": estado,
            "bytes": len(cuerpo),
            "pista": _pista(cuerpo),
            "anti_bot": _marcas_anti_bot(cuerpo, cabeceras),
        }
    except Exception as e:  # noqa: BLE001
        texto = str(e)
        return {"estado": 403 if "403" in texto else None, "error": texto[:200]}
    finally:
        if jar:
            try:
                from cookies import discard
                discard(jar)
            except Exception:  # noqa: BLE001
                pass


def _marcas_anti_bot(cuerpo: bytes, cabeceras: dict) -> str:
    """Señales de PerimeterX u otro filtro, en el cuerpo o en los headers.

    Distingue las dos cosas que hoy se ven idénticas: un 403 de permisos trae un
    JSON de Udemy; uno de anti-bot trae un captcha o headers `x-px-*`.
    """
    encontradas = []
    texto = cuerpo.decode("utf-8", "replace").lower()
    for marca in ("_pxcaptcha", "perimeterx", "please verify you are a human",
                  "automation tools", "px-captcha", "captcha"):
        if marca in texto:
            encontradas.append(f"cuerpo:{marca}")
    for nombre, valor in (cabeceras or {}).items():
        bajo = nombre.lower()
        if bajo.startswith("x-px") or bajo == "x-cdn" or "perimeterx" in str(valor).lower():
            encontradas.append(f"header:{nombre}")
        if bajo == "set-cookie" and "_px" in str(valor).lower():
            encontradas.append("set-cookie:_px*")
    return ", ".join(encontradas) if encontradas else "sin marcas de anti-bot"


def _leer(r: dict) -> str:
    """Traduce la matriz a una conclusión, sin adivinar de más."""
    def ok(clave):
        return r.get(clave, {}).get("estado") == 200

    marcas = " ".join(str(p.get("anti_bot", "")) for p in r.values())
    hay_anti_bot = "cuerpo:" in marcas or "header:" in marcas or "set-cookie:" in marcas

    if not ok("7a_yt_dlp_PRIMERO") and ok("7b_yt_dlp_tls_amplio"):
        return ("La HUELLA TLS. La misma petición, en el mismo momento, pasa con la lista "
                "completa de cifrados y falla con la recortada que yt-dlp usa por defecto. "
                "No es HTTP: es el handshake.")
    if ok("7a_yt_dlp_PRIMERO") and not ok("7c_yt_dlp_ULTIMO"):
        return ("La RÁFAGA. La misma petición pasa si va primera y falla si va después de "
                "seis seguidas: el sitio está limitando por ritmo, no rechazando al cliente.")
    if not ok("7a_yt_dlp_PRIMERO") and not ok("7b_yt_dlp_tls_amplio") and hay_anti_bot:
        return ("ANTI-BOT. Falla con las dos variantes de TLS y la respuesta trae marcas de "
                "protección: no se arregla con headers ni con cifrados.")
    if ok("1_pagina_ua_moderno") and not ok("1b_pagina_ua_de_2019_de_udemy_py"):
        return ("El User-Agent. La MISMA página anda con un Chrome moderno y falla con el "
                "Chrome 72 de 2019 que udemy.py:125 escribe a mano.")
    if ok("5_mis_cursos_en_el_portal") and not ok("6_mis_cursos_en_www"):
        return ("El host de la API. Tu sesión vale en el portal y NO en www, que es adonde "
                "yt-dlp pide las clases con el host clavado.")
    if not ok("3_api_quien_soy_en_el_portal") and not ok("4_api_quien_soy_en_www"):
        return ("La sesión no autentica en NINGÚN lado. Las cookies llegan pero el sitio no "
                "las acepta: revisá que el navegador tenga la sesión abierta.")
    # Este va ÚLTIMO a propósito: que la página cargue no significa que el resto
    # ande, y ponerlo antes tapaba el diagnóstico del host de la API.
    if ok("7a_yt_dlp_PRIMERO") and ok("7c_yt_dlp_ULTIMO"):
        return "El stack de yt-dlp pasa. Si la descarga igual falla, el problema está más adelante."
    return "Sin patrón claro: mirá la matriz completa."
