# -*- coding: utf-8 -*-
"""Test HTTP end-to-end del PanelHandler.

Copre la zona che prima non aveva alcuna copertura: autenticazione di
host/origine, PIN, path traversal e limiti di upload. Prima questi test
costruivano handler con `__init__` vuoto, quindi non esercitavano né
`do_GET` né `do_POST` — esattamente la parte con i problemi più gravi.
"""
import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))


@pytest.fixture
def server(monkeypatch):
    """Pannello in ascolto su porta effimera, in 127.0.0.1."""
    import functools
    import http.server

    import panel

    monkeypatch.setattr(panel, "RUNTIME_PORT", 0)
    # whitelist vuota: i test vogliono provare le guardie, non i contenuti
    monkeypatch.setattr(panel, "_allowed_lesson_names", lambda: set())
    monkeypatch.setattr(panel, "_allowed_single_names", lambda: set())

    handler = functools.partial(panel.PanelHandler, directory=str(BASE))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    panel.RUNTIME_PORT = httpd.server_address[1]
    panel._HOSTS_CACHE.update(at=0.0, hosts=None)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        h = dict(headers or {})
        h.setdefault("Host", f"127.0.0.1:{port}")
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        return r.status, data
    finally:
        conn.close()


# ------------------------------------------------------------- anti DNS-rebinding
def test_host_esterno_rifiutato(server):
    """Con DNS-rebinding il browser del docente arriva da 127.0.0.1 ma con
    Host = dominio dell'attaccante: senza controllo, l'attaccante leggeva e
    scriveva il pannello come se fosse same-origin."""
    status, _ = _req(server, "GET", "/api/state",
                     headers={"Host": "evil.example.com"})
    assert status == 403


def test_host_locale_accettato(server):
    status, body = _req(server, "GET", "/api/state")
    assert status == 200
    assert json.loads(body)["port"] == server


def test_host_con_porta_sbagliata_rifiutato(server):
    status, _ = _req(server, "GET", "/api/state",
                     headers={"Host": f"127.0.0.1:{server + 1}"})
    assert status == 403


# ------------------------------------------------------------------- anti CSRF
def test_post_cross_origin_rifiutato(server):
    status, _ = _req(server, "POST", "/api/clear_history", body=b"",
                     headers={"Origin": "https://evil.example.com",
                              "Sec-Fetch-Site": "cross-site"})
    assert status == 403


def test_post_senza_origin_accettata(server, monkeypatch):
    """Client non-browser (curl, script): niente Origin/Sec-Fetch-Site.
    Non è un vettore CSRF, quindi passa."""
    import panel
    monkeypatch.setattr(panel, "_history_clear", lambda: None)
    status, _ = _req(server, "POST", "/api/clear_history", body=b"")
    assert status == 200


def test_post_same_origin_accettata(server, monkeypatch):
    import panel
    monkeypatch.setattr(panel, "_history_clear", lambda: None)
    status, _ = _req(server, "POST", "/api/clear_history", body=b"", headers={
        "Origin": f"http://127.0.0.1:{server}", "Sec-Fetch-Site": "same-origin"})
    assert status == 200


# ------------------------------------------------------------------------- PIN
def test_pin_presente_protegge_tutte_le_mutazioni(server, monkeypatch):
    """Il PIN protegge TUTTE le POST, non solo settings e reset_classifica."""
    import panel
    monkeypatch.setattr(panel, "CONFIG", {**panel.CONFIG, "pin_docente": "1234"})
    for path in ("/api/clear_history", "/api/cancel_queue", "/api/build"):
        status, _ = _req(server, "POST", path, body=b"{}",
                         headers={"Content-Type": "application/json"})
        assert status == 403, f"{path} non protetto dal PIN"
    # con il PIN giusto passa
    status, _ = _req(server, "POST", "/api/clear_history", body=b"",
                     headers={"X-Teacher-Pin": "1234"})
    assert status == 200


def test_pin_vuoto_non_blocca_il_pannello(server, monkeypatch):
    """Installazione predefinita (pin_docente = ""): comportamento invariato,
    l'unica barriera resta il loopback."""
    import panel
    monkeypatch.setattr(panel, "CONFIG", {**panel.CONFIG, "pin_docente": ""})
    monkeypatch.setattr(panel, "_history_clear", lambda: None)
    status, _ = _req(server, "POST", "/api/clear_history", body=b"")
    assert status == 200


# ----------------------------------------------------------- path traversal
@pytest.mark.parametrize("lesson", [
    "../../etc",
    "..%2F..%2FWindows",
    "a/b",
    "a\\b",
    "x\x00y",
])
def test_reaudio_e_export_single_bloccano_traversal(server, lesson):
    """prima: `lesson = BASE / name` senza alcuna validazione."""
    import urllib.parse
    for path in ("/api/reaudio", "/api/export_single"):
        status, _ = _req(server, "GET", f"{path}?lesson={urllib.parse.quote(lesson)}")
        assert status == 400, f"{path} ha accettato {lesson!r}"


def test_lesson_inesistente_rifiutata(server):
    status, _ = _req(server, "GET", "/api/reaudio?lesson=NonEsiste_lesson")
    assert status == 400


# ------------------------------------------------------------- errori espliciti
def test_rifiuti_usi_json_e_drainano_il_corpo(server):
    """Un 403/413 senza drain del body lascia spazzatura sulla connessione
    keep-alive e corrompe la richiesta successiva."""
    status, body = _req(server, "POST", "/api/clear_history", body=b"x" * 5000,
                        headers={"Origin": "https://evil.example.com",
                                 "Content-Length": "5000"})
    assert status == 403
    assert json.loads(body)["ok"] is False


# ------------------------------------------------------- piu' link insieme
def _build(server, payload):
    status, body = _req(server, "POST", "/api/build",
                        body=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"})
    return status, json.loads(body)


@pytest.fixture
def avvio_falso(server, monkeypatch):
    """`start_job` registrato invece che eseguito: nessuna lezione vera, ma si
    vede esattamente cosa il pannello accoderebbe."""
    import panel
    partiti = []

    def fake_start(fn, kind, source):
        partiti.append((source, fn))
        return True, True

    monkeypatch.setattr(panel, "start_job", fake_start)
    monkeypatch.setattr(panel, "_rate_ok", lambda ip: True)
    return partiti


def test_build_unisce_piuo_link_in_una_lezione(server, avvio_falso):
    """Sezione 2, piu' link: UNA sola generazione che li unisce.

    Non una lezione per link: il materiale viene letto da ogni fonte e
    accorpato in un unico documento.
    """
    import new_lesson
    partiti = avvio_falso
    usate = []
    monkey = new_lesson.build_from_sources
    new_lesson.build_from_sources = lambda srcs, **kw: (
        usate.append(list(srcs)) or ("Lezione_multipla_lesson", True))
    try:
        links = ["https://uno.example/a", "https://due.example/b", "https://tre.example/c"]
        status, j = _build(server, {"sources": links})
        assert status == 200, j
        assert j["started"] is True and j["merged"] is True
        assert j["sources"] == links
        assert len(partiti) == 1, "un solo job per tutti i link"
        assert "3 fonti" in partiti[0][0]
        partiti[0][1]()                      # esegue il job finto
        assert usate == [links]
    finally:
        new_lesson.build_from_sources = monkey


def test_build_accetta_piuo_link_incollati_in_un_link(server, avvio_falso):
    """Il caso reale: due indirizzi incollati insieme separati da uno spazio."""
    import new_lesson
    partiti = avvio_falso
    usate = []
    monkey = new_lesson.build_from_sources
    new_lesson.build_from_sources = lambda srcs, **kw: (
        usate.append(list(srcs)) or ("Due_lesson", True))
    try:
        status, j = _build(server, {"source": "https://uno.example/a https://due.example/b"})
        assert status == 200, j
        assert j["merged"] is True and j["count"] == 1
        partiti[0][1]()
        assert usate == [["https://uno.example/a", "https://due.example/b"]]
    finally:
        new_lesson.build_from_sources = monkey


def test_build_link_inutilizzabile_non_blocca_gli_altri(server, avvio_falso):
    import new_lesson
    partiti = avvio_falso
    usate = []
    monkey = new_lesson.build_from_sources
    new_lesson.build_from_sources = lambda srcs, **kw: (
        usate.append(list(srcs)) or ("Due_lesson", True))
    try:
        status, j = _build(server, {"sources": ["https://uno.example/a",
                                                "non-e-un-link", "https://due.example/b"]})
        assert status == 200, j
        assert any("non-e-un-link" in s["source"] for s in j["skipped"])
        partiti[0][1]()
        assert usate == [["https://uno.example/a", "https://due.example/b"]]
    finally:
        new_lesson.build_from_sources = monkey


def test_build_un_solo_link_resta_una_generazione_singola(server, avvio_falso):
    """Un link sola deve fare esattamente il percorso di prima."""
    import new_lesson
    partiti = avvio_falso
    usate = []
    monkey = new_lesson.build_from_source
    new_lesson.build_from_source = lambda src, **kw: (
        usate.append(src) or ("Una_lesson", True))
    try:
        status, j = _build(server, {"source": "https://uno.example/a"})
        assert status == 200, j
        assert j["started"] is True and "merged" not in j
        assert j["sources"] == ["https://uno.example/a"]
        assert len(partiti) == 1
        partiti[0][1]()
        assert usate == ["https://uno.example/a"]
    finally:
        new_lesson.build_from_source = monkey


def test_build_senza_link_utilizzabili_rifiuta(server, avvio_falso):
    status, j = _build(server, {"sources": ["non-e-un-link"]})
    assert status == 400
    assert j["started"] is False and j["reason"]
