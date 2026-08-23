"""El fan-out de un curso: `enqueue_playlist`.

NO es una función pura, y el fixture lo declara en vez de esconderlo:
`JobManager.__init__` crea el directorio de descargas y arma el pool, y
`enqueue` hace `_pool.submit(self._run, ...)`, que arrancaría un worker real con
red. Se usa `tmp_path` y se intercepta el `submit`; mockear sólo `probe` no
alcanza.
"""

import pytest

from downloader import JobManager


@pytest.fixture
def gestor(tmp_path, monkeypatch):
    jm = JobManager(str(tmp_path / "descargas"))
    enviados = []
    # Sin esto cada `enqueue` lanza un hilo que entra en yt-dlp y sale a la red.
    monkeypatch.setattr(jm._pool, "submit", lambda *a, **k: enviados.append(a))
    jm.enviados = enviados
    return jm


def curso(titulo, clases, extra=()):
    entradas = [
        {"_type": "url", "url": f"https://www.udemy.com/c/learn/v4/t/lecture/{i}", "title": t}
        for i, t in enumerate(clases, start=1)
    ]
    entradas.extend(extra)
    return {"kind": "playlist", "title": titulo, "count": len(entradas), "entries": entradas}


def test_un_job_por_clase(gestor, monkeypatch):
    """Un curso como job único bajaría las 40 clases seguidas: la pausa de
    `throttle` sólo se aplica ENTRE jobs."""
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("Mi Curso", ["a", "b", "c"]))
    r = gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    assert r["count"] == 3
    assert len(gestor.enviados) == 3


def test_numeracion_sin_huecos(gestor, monkeypatch):
    """Las entradas inservibles se descartan ANTES de numerar; si no, los
    nombres de archivo saltean números."""
    basura = [
        {"_type": "url", "title": "sin url"},
        {"_type": "url", "url": "https://cdn.net/v.mp4?Signature=B", "title": "firmada"},
    ]
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("C", ["a", "b", "c"], basura))
    r = gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    assert r["count"] == 3
    assert r["skipped"] == 2
    assert [j["position"] for j in r["jobs"]] == [1, 2, 3]
    # `total` uniforme: es el denominador que muestra el popup.
    assert {j["total"] for j in r["jobs"]} == {3}


def test_todo_el_curso_a_una_subcarpeta(gestor, monkeypatch):
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("Curso: Diseño", ["a", "b"]))
    r = gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    assert r["folder"] == "Curso： Diseño"
    assert {j["subfolder"] for j in r["jobs"]} == {"Curso： Diseño"}


def test_sin_titulo_usable_van_a_la_raiz(gestor, monkeypatch):
    """Mejor la raíz que una carpeta con un nombre inventado."""
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("   ", ["a"]))
    r = gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    assert r["folder"] is None


def test_el_titulo_de_cada_clase_viaja(gestor, monkeypatch):
    """Así el popup muestra el nombre desde el primer segundo en vez de la URL."""
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("C", ["Intro", "Diseño"]))
    r = gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    assert [j["title"] for j in r["jobs"]] == ["Intro", "Diseño"]


def test_la_url_encolada_conserva_el_smuggle(gestor, monkeypatch):
    """Reescribirla destruye el course_id, y sin él cada clase da 403."""
    smuggled = "https://www.udemy.com/c/learn/v4/t/lecture/7#__youtubedl_smuggle=%7B%22course_id%22%3A9%7D"
    info = {
        "kind": "playlist",
        "title": "C",
        "entries": [{"_type": "url", "url": smuggled, "title": "x"}],
    }
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: info)
    r = gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    assert r["jobs"][0]["url"] == smuggled


def test_un_video_suelto_no_se_expande(gestor, monkeypatch):
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: {"kind": "video", "title": "x"})
    r = gestor.enqueue_playlist("https://youtu.be/x")
    assert r["kind"] == "video"
    assert r["count"] == 1
    assert r["skipped"] == 0


def test_informa_el_espacio_libre(gestor, monkeypatch):
    """Un curso son decenas de GB: es un dato, no un bloqueo."""
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("C", ["a"]))
    r = gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    assert r["espacio_libre_gb"] is None or r["espacio_libre_gb"] > 0


def test_las_cookies_no_se_escriben_a_disco_al_encolar(gestor, monkeypatch, tmp_path):
    """Encolar 200 clases escribía 200 copias de la sesión viva de golpe, y
    quedaban en disco durante las horas que dura la cola. Ahora el jar se
    materializa dentro de `_run`, o sea de a uno."""
    def explotar(*a, **k):
        raise AssertionError("no se debe escribir el jar al encolar")

    monkeypatch.setattr("downloader.write_netscape_cookiefile", explotar)
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("C", ["a", "b"]))
    cookies = [{"name": "access_token", "value": "secreto", "domain": ".udemy.com"}]
    r = gestor.enqueue_playlist("https://www.udemy.com/course/c/", cookies=cookies)
    assert r["count"] == 2


def test_cancel_all_alcanza_a_todo_lo_pendiente(gestor, monkeypatch):
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("C", ["a", "b", "c"]))
    gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    assert gestor.cancel_all() == 3
    assert all(j["status"] == "cancelled" for j in gestor.all())
    # Y no quedan cookies en memoria de un job que ya no va a correr.
    assert all(j._cookies is None for j in gestor._jobs.values())


def test_los_salteados_se_pueden_podar(gestor, monkeypatch):
    """`skipped` es terminal: si no, ni el prune ni "Limpiar terminadas" lo
    alcanzan y quedan 200 filas pegadas en el popup."""
    from downloader import TERMINALES

    assert "skipped" in TERMINALES
    monkeypatch.setattr(gestor, "probe", lambda *a, **k: curso("C", ["a"]))
    gestor.enqueue_playlist("https://www.udemy.com/course/c/")
    for j in gestor._jobs.values():
        j.status = "skipped"
    assert gestor.clear_finished() == 1
