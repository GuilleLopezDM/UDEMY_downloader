"""Redacción de URLs para el log.

Vive en su propio módulo y no en `app.py` por una razón de tests: importar `app`
tiene efectos de disco desde el import (crea el `.token` y barre los jars de
cookies de la corrida anterior), así que un test que solo quiera comprobar la
redacción no puede tocarlo.
"""

from urllib.parse import urlsplit, urlunsplit

# Parámetros de query que son credenciales. Los de CloudFront (Policy,
# Signature, Key-Pair-Id) firman las URLs de los segmentos de Udemy: quien los
# tenga puede bajar el video mientras no expiren.
CLAVES_SENSIBLES = frozenset({
    "policy", "signature", "sig", "key-pair-id", "token", "access_token",
    "auth", "authorization", "expires", "x-amz-signature", "x-amz-credential",
    "x-amz-security-token", "hdnts", "__hdnea__",
})
LARGO_MAXIMO_VALOR = 24


def _netloc_sin_credenciales(netloc: str) -> str:
    """Saca el `usuario:contraseña@` del host.

    `urlsplit` deja el userinfo dentro del netloc, así que reconstruir con
    `urlunsplit` lo devolvía intacto: una URL con contraseña se logueaba
    entera. Y como el early return de abajo salta cuando no hay query, ni
    siquiera hacía falta que la URL tuviera parámetros.
    """
    if "@" not in netloc:
        return netloc
    return "<credenciales>@" + netloc.rsplit("@", 1)[1]


def _redactar_pares(texto: str) -> str:
    """Redacta un `a=1&b=2`, conservando los nombres y no los valores."""
    limpios = []
    for par in texto.split("&"):
        clave, sep, valor = par.partition("=")
        if not sep:
            limpios.append(clave)
        elif clave.lower() in CLAVES_SENSIBLES:
            limpios.append(f"{clave}=<omitido>")
        elif len(valor) > LARGO_MAXIMO_VALOR:
            limpios.append(f"{clave}={valor[:LARGO_MAXIMO_VALOR]}…<truncado>")
        else:
            limpios.append(par)
    return "&".join(limpios)


def redact_url(raw: str) -> str:
    """URL apta para un log: conserva los nombres de parámetro, no los valores.

    Los nombres ayudan a diagnosticar y no son secretos. Los valores sí pueden
    serlo, así que las claves conocidas se omiten enteras y el resto se trunca
    — una firma es siempre larga, un `v=Tr2Y9hBtTjE` de YouTube no.

    El fragmento pasa por la misma redacción en vez de descartarse. Descartarlo
    entero era seguro pero dejaba el log inservible acá: en Udemy la clase vive
    justamente ahí (`#/lecture/37411654`), así que todas las líneas quedaban
    iguales. Con la redacción por pares, `#/lecture/123` sobrevive y un
    `#access_token=…` no.

    Antes el fragmento sólo se perdía cuando la URL tenía query, porque el
    camino sin parámetros devolvía la URL cruda: un `https://sitio/x#access_token=…`
    se logueaba completo.
    """
    try:
        partes = urlsplit(raw)
    except ValueError:
        return "(url ilegible)"

    netloc = _netloc_sin_credenciales(partes.netloc)
    query = _redactar_pares(partes.query) if partes.query else ""
    fragmento = _redactar_pares(partes.fragment) if partes.fragment else ""

    if netloc == partes.netloc and query == partes.query and fragmento == partes.fragment:
        return raw

    return urlunsplit((partes.scheme, netloc, partes.path, query, fragmento))
