"""`redact_url()` es lo único que separa el log de la sesión del usuario.

Las URLs de los segmentos de Udemy vienen firmadas por CloudFront: quien tenga
`Policy`/`Signature`/`Key-Pair-Id` puede bajar el video mientras no expiren.
"""

from redaccion import LARGO_MAXIMO_VALOR, redact_url


def test_omite_las_firmas_de_cloudfront():
    salida = redact_url("https://cdn.udemy.com/v.mp4?Policy=AAA&Signature=BBB&Key-Pair-Id=CCC")
    assert "AAA" not in salida and "BBB" not in salida and "CCC" not in salida
    # Los nombres se conservan: ayudan a diagnosticar y no son secretos.
    assert "Policy=<omitido>" in salida
    assert "Signature=<omitido>" in salida
    assert "Key-Pair-Id=<omitido>" in salida


def test_no_depende_de_las_mayusculas():
    assert "<omitido>" in redact_url("https://x.com/v?SIGNATURE=abc")
    assert "<omitido>" in redact_url("https://x.com/v?x-amz-signature=abc")


def test_trunca_los_valores_largos_desconocidos():
    largo = "z" * 200
    salida = redact_url(f"https://x.com/v?desconocido={largo}")
    assert largo not in salida
    assert "<truncado>" in salida


def test_deja_pasar_los_valores_cortos():
    """Un `v=Tr2Y9hBtTjE` de YouTube no es un secreto y sirve para diagnosticar."""
    assert redact_url("https://youtube.com/watch?v=Tr2Y9hBtTjE") == (
        "https://youtube.com/watch?v=Tr2Y9hBtTjE"
    )
    assert len("Tr2Y9hBtTjE") <= LARGO_MAXIMO_VALOR


def test_el_userinfo_no_sale_en_el_log():
    """`urlsplit` deja `usuario:contraseña@` dentro del netloc.

    Reconstruir con `urlunsplit` lo devolvía intacto, y el early return de "sin
    query" hacía que ni siquiera hiciera falta que la URL tuviera parámetros.
    """
    assert "contrasena" not in redact_url("https://usuario:contrasena@x.com/v")
    assert "contrasena" not in redact_url("https://usuario:contrasena@x.com/v?a=1")


def test_el_fragmento_sensible_no_sale():
    """El fragmento sólo se descartaba cuando había query, por el early return."""
    assert "SECRETO" not in redact_url("https://x.com/v#access_token=SECRETO")
    assert "SECRETO" not in redact_url("https://x.com/v?a=1#access_token=SECRETO")


def test_el_fragmento_util_sobrevive():
    """En Udemy la clase vive en el fragmento: tirarlo dejaba el log inservible.

    Todas las líneas del log quedaban como `https://www.udemy.com/course/x/`,
    sin forma de saber de qué clase hablaban.
    """
    url = "https://www.udemy.com/course/sys/#/lecture/37411654"
    assert redact_url(url) == url


def test_url_ilegible_no_revienta():
    assert redact_url("http://[::1") == "(url ilegible)"


def test_sin_nada_que_redactar_devuelve_la_misma():
    url = "https://www.udemy.com/course/system-design/"
    assert redact_url(url) is url or redact_url(url) == url
