# -*- coding: utf-8 -*-
"""Il CSS deve arrivare al browser INTATTO.

Regressione: la compressione gzip era implementata costruendo le intestazioni
a mano, bypassando `SimpleHTTPRequestHandler.send_head`. Il foglio di stile
arrivava al browser con ZERO regole applicate — la pagina intera risultava
bianca e senza formattazione — mentre la stessa risposta letta con `fetch`
era perfetta e i byte sul disco erano corretti. Nessuno dei 102 test
esistenti lo vedeva: verificavano i FILE, non quello che il browser ne fa.

Questi test caricano davvero la lezione in Chrome headless e contano le
regole CSS applicate, che è la misura che conta.
"""
import functools
import http.server
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

from tools.selftest import _find_chrome, chrome_args  # noqa: E402

CHROME = _find_chrome()

# In locale il browser puo' non esserci (si salta). In CI la sua assenza
# deve essere un errore: altrimenti il test che presidia la pagina bianca
# verrebbe saltato proprio dove serve di piu'.
CI_BROWSER = os.environ.get("CI_BROWSER_REQUIRED") == "1"


def _browser_obligatorio():
    if not CHROME and CI_BROWSER:
        pytest.fail("CI_BROWSER_REQUIRED=1 ma nessun Chrome/Edge trovato: "
                    "i test del rendering non possono essere saltati")
    if not CHROME:
        pytest.skip("Chrome/Edge non trovato")
    return CHROME

PROBE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="main.css?v=test">
</head><body><pre id="r">…</pre>
<script>
window.addEventListener('load', () => setTimeout(() => {
  const s = document.styleSheets[0];
  let n = -1, e = '';
  try { n = s.cssRules.length; } catch (x) { e = x.message; }
  document.getElementById('r').textContent = 'REGOLE=' + n + ' err=' + e
    + ' bg=' + getComputedStyle(document.body).backgroundColor;
}, 1500));
</script></body></html>"""


@pytest.fixture(scope="module")
def lezione():
    """Una lezione minima, generata con il player di progetto."""
    import json

    import player_template as pt

    d = Path(tempfile.mkdtemp())
    t = dict(pt.THEMES['dark'])
    t['accent'], t['accent2'], t['accentink'] = pt._accent_from_title("Prova Regressione")
    pt.write_player(d, "Prova Regressione", tema="dark")
    (d / "lesson-data.js").write_text(
        "window.LESSON_DATA = " + json.dumps(
            {"titolo": "Prova Regressione", "slides": [
                {"title": "Apertura", "blocks": [{"h1": "Ciao"}], "narration": "Ciao"}],
             "profilo": {}}, ensure_ascii=False) + ";\n", encoding="utf-8")
    pt.bust_cache(d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _regole_applicate(lesson_dir, etichetta):
    """Serve la lezione col server di progetto e conta le regole CSS."""
    if not CHROME:
        pytest.skip("Chrome/Edge non trovato")
    import start_lesson

    root = Path(tempfile.mkdtemp())
    shutil.copytree(lesson_dir, root / "l")
    (root / "l" / "probe.html").write_text(PROBE, encoding="utf-8")

    handler = functools.partial(start_lesson._RangeHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    out_f = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out_f.close()
    try:
        with open(out_f.name, "w", encoding="utf-8") as fo:
            subprocess.run(
                chrome_args(5000) + ["--dump-dom",
                                 f"http://127.0.0.1:{port}/l/probe.html"],
                stdout=fo, stderr=subprocess.DEVNULL, timeout=60)
    finally:
        httpd.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    dom = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
    m = re.search(r'<pre id="r">(.*?)</pre>', dom, re.S)
    return m.group(1).strip() if m else "DIAG NON TROVATO"


def test_foglio_di_stile_applicato_con_gzip(lezione):
    """Il browser deve vedere TUTTE le regole del foglio, non zero.

    Con l'implementazione gzip precedente questo era 0 e la pagina risultava
    bianca e disordinata: il sintomo che ha segnalato l'utente.
    """
    _browser_obligatorio()
    esito = _regole_applicate(lezione, "gzip")
    assert "REGOLE=0" not in esito, (
        f"il browser non ha applicato il foglio di stile ({esito}): "
        "la pagina si vedrebbe bianca e senza formattazione")
    n = int(re.search(r"REGOLE=(\d+)", esito).group(1))
    assert n > 100, f"foglio di stile quasi vuoto: {esito}"
    # il tema scuro deve essere applicato
    assert "rgb(11, 17, 29)" in esito, f"fondo non scuro: {esito}"


def test_gzip_davvero_attivo_e_corretto():
    """Il server deve comprimere, e il corpo deve essere gzip valido."""
    import gzip as _gz
    import http.client

    import start_lesson

    root = Path(tempfile.mkdtemp())
    # write_bytes: su Windows write_text tradurrebbe \n in \r\n e il confronto
    # non significherebbe nulla
    (root / "a.css").write_bytes(b"body{color:red}\n" * 200)
    handler = functools.partial(start_lesson._RangeHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/a.css", headers={"Accept-Encoding": "gzip"})
        r = c.getresponse()
        raw = r.read()
        clen = int(r.getheader("Content-Length"))
        enc = r.getheader("Content-Encoding")
        nomi = [k.lower() for k, _ in r.getheaders()]
        c.close()
    finally:
        httpd.shutdown()
        shutil.rmtree(root, ignore_errors=True)

    assert enc == "gzip", f"il server non ha compresso (Content-Encoding={enc!r})"
    assert len(raw) == clen, (
        f"Content-Length {clen} diverso dai {len(raw)} byte inviati: "
        "il browser considera la risposta non valida e scarta il foglio")
    testo = _gz.decompress(raw)
    assert testo == b"body{color:red}\n" * 200
    assert len(raw) < len(testo), "la compressione non ha ridotto nulla"

    # GUARDIA SENZA BROWSER: l'implementazione rotta mandava Cache-Control due
    # volte, perche' costruiva le intestazioni a mano chiamando _cache_headers()
    # e poi anche end_headers() (che la richiama). Un browser puo' accettare
    # un'intestazione duplicata, ma e' un difetto strutturale che va tenuto
    # fuori: questo test gira anche in CI dove il browser non c'e'.
    dup = [n for n in set(nomi) if nomi.count(n) > 1]
    assert not dup, f"intestazioni duplicate nella risposta: {dup}"


def test_la_compressione_non_bypassa_send_head():
    """La regola che ha rotto la pagina: costruire le risposte a mano invece di
    passare da `SimpleHTTPRequestHandler.send_head`.

    Il percorso gzip DEVE riusare send_head e correggere solo la lunghezza in
    `send_header`. Se un domani qualcuno reintroduce un `send_head` che scrive
    le intestazioni per conto proprio, questo test lo segnala.
    """
    import start_lesson

    assert "_QuietHandler.send_head" not in start_lesson._QuietHandler.__dict__, (
        "non sovrascrivere send_head: corregge Content-Length in send_header")
    assert "send_head" in start_lesson._QuietHandler.__dict__, (
        "send_head deve esistere per comprimere il corpo prima delle intestazioni")
    assert "copyfile" in start_lesson._QuietHandler.__dict__, (
        "copyfile deve scrivere il corpo compresso")
    assert "send_header" in start_lesson._QuietHandler.__dict__


def test_nessun_escape_ottale_nel_css_generato():
    """`content: "\\2713"` dentro una stringa Python NON raw diventa `\\271`
    (chr 185) + `3`: nel foglio finiva `content: "¹3"`. Il blocco CSS del
    player non è una stringa raw, quindi gli escape CSS vanno scritti come
    caratteri UTF-8."""
    import player_template as pt

    t = dict(pt.THEMES['dark'])
    t['accent'], t['accent2'], t['accentink'] = pt._accent_from_title("Prova")
    css = pt._css(t)
    assert 'content: "✓"' in css, "il simbolo di esito corretto non è nel CSS"
    assert 'content: "✕"' in css, "il simbolo di esito errato non è nel CSS"
    for m in re.finditer(r'content: "([^"]*)"', css):
        assert "\u00b9" not in m.group(1), f"escape ottale corrotto: {m.group()!r}"
