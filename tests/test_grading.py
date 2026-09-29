# -*- coding: utf-8 -*-
"""La logica di VALUTAZIONE deve essere corretta, non solo plausibile.

Questi test caricano una lezione reale in Chrome headless, impostano le
risposte e misurano i numeri che il pannello finale e l'export mostrano allo
studente e al docente. Nessuno di questi calcoli aveva copertura: sono le
modifiche più delicate fatte al progetto (il denominatore del punteggio, la
soglia dell'esame, la stabilita' dell'ordine delle opzioni) e un errore lì non
si vede guardando la pagina, cambia solo i numeri.

Copre anche i due bug che hanno reso le lezioni inutilizzabili:
  - l'opzione corretta troncata via `out[:maxn]`, che produceva quiz con sole
    opzioni sbagliate (irrisolvibili);
  - l'ordine delle opzioni rimescolato a ogni render, per cui lo studente
    rispondeva alla posizione invece che al contenuto.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import functools
import http.server
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

from tools.selftest import _find_chrome, chrome_args  # noqa: E402

CHROME = _find_chrome()
# In locale il browser puo' non esserci (si salta). In CI la sua assenza deve
# essere un errore: altrimenti i test di valutazione, che decidono i voti, non
# girerebbero affatto.
import os  # noqa: E402
CI_BROWSER = os.environ.get("CI_BROWSER_REQUIRED") == "1"


def _browser_obligatorio():
    if not CHROME and CI_BROWSER:
        pytest.fail("CI_BROWSER_REQUIRED=1 ma nessun Chrome/Edge trovato: "
                    "i test di valutazione non possono essere saltati")
    if not CHROME:
        pytest.skip("Chrome/Edge non trovato")
    return CHROME
# 3 moduli, ciascuno con quiz da 1 punto, V/F da 1 punto e 3 quesiti (3 punti)
# -> 5 punti e 2 attivita' per modulo, 6 attivita' in tutto, 15 punti.
SLIDES = []
for m in range(3):
    SLIDES.append({"title": f"Modulo {m + 1} - Argomento {m}", "icon": "📘",
                   "blocks": [{"callout": f"Modulo {m + 1}"}, {"h1": f"Modulo {m + 1}"},
                              {"p": f"Testo del modulo {m + 1}."}],
                   "narration": f"Modulo {m + 1}."})
    SLIDES.append({"title": f"Quiz modulo {m + 1}", "icon": "❓",
                   "blocks": [{"quiz": {
                       "q": f"Domanda {m + 1}", "modulo_slide": m * 2,
                       "opts": [{"t": "giusta", "ok": True, "fb": "perche si"},
                                {"t": "sbagliata A", "ok": False, "fb": ""},
                                {"t": "sbagliata B", "ok": False, "fb": ""},
                                {"t": "sbagliata C", "ok": False, "fb": ""}],
                       "ok": "Esatto!", "ko": "Rileggi."}}],
                   "narration": f"Verifica {m + 1}."})
    SLIDES.append({"title": f"Vero o falso modulo {m + 1}", "icon": "✅",
                   "blocks": [{"vf": [{"t": f"Affermazione {m + 1}a", "ok": True, "fb": "s"},
                                      {"t": f"Affermazione {m + 1}b", "ok": False, "fb": "s"},
                                      {"t": f"Affermazione {m + 1}c", "ok": True, "fb": "s"}]}],
                   "narration": "Vero o falso."})
# esame finale: 1 domanda, 1 punto, soglia ceil(1*0.7) = 1
SLIDES.append({"title": "Esame finale 1/1", "icon": "🏁",
               "blocks": [{"quiz": {"q": "Domanda d'esame", "exam": True,
                                    "opts": [{"t": "giusta", "ok": True, "fb": ""},
                                             {"t": "sbagliata", "ok": False, "fb": ""}],
                                    "ok": "Esatto!", "ko": "Rileggi.", "modulo_slide": 0}}],
               "narration": "Esame."})

PAYLOAD = {"titolo": "Prova Valutazione", "slides": SLIDES, "profilo": {}}

RISPOSTE_JS = """window.RISPOSTE = %s;
"""

# gira DOPO main.js, che espone LESSON_STATE / LESSON_API
VERIFICA_JS = r"""
window.addEventListener('load', () => setTimeout(() => {
  const out = document.getElementById('r');
  try {
    const st = window.LESSON_STATE, A = window.LESSON_API;
    if (!A || !A.scoreMetrics) throw new Error('LESSON_API non esposta');
    Object.keys(window.RISPOSTE || {}).forEach(k => {
      st.results[k] = window.RISPOSTE[k];
    });
    A.paintScore();
    const M = A.scoreMetrics(), X = A.examMetrics();
    const exp = A.buildExport();
    out.textContent = JSON.stringify({
      score: M, exam: X,
      export_punti: exp.punti, export_attivita: exp.attivita_svolte,
      export_copertura: exp.copertura, export_esame: exp.esame,
      export_precisione: exp.precisione, export_tempo_min: exp.tempo_min,
      segnalibri: exp.segnalibri
    });
  } catch (e) {
    out.textContent = 'ERRORE: ' + e.message;
  }
}, 1200));
"""


@pytest.fixture(scope="module")
def lezione():
    import player_template as pt

    d = Path(tempfile.mkdtemp())
    t = dict(pt.THEMES['dark'])
    t['accent'], t['accent2'], t['accentink'] = pt._accent_from_title("Prova Valutazione")
    pt.write_player(d, "Prova Valutazione", tema="dark")
    (d / "lesson-data.js").write_text(
        "window.LESSON_DATA = " + json.dumps(PAYLOAD, ensure_ascii=False) + ";\n",
        encoding="utf-8")
    pt.bust_cache(d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _valuta(lezione, risposte):
    """Esegue il player REALE su un insieme di risposte e restituisce i numeri.

    Si usa l'index.html autentico con due script iniettati (le risposte prima
    di lesson-data.js, la verifica dopo main.js): cosi' si misura la pagina
    vera, non una ricostruzione che potrebbe divergere.
    """
    if not CHROME:
        _browser_obligatorio()
    import re

    import start_lesson

    root = Path(tempfile.mkdtemp())
    shutil.copytree(lezione, root / "l")
    L = root / "l"
    (L / "risposte.js").write_text(RISPOSTE_JS % json.dumps(risposte), encoding="utf-8")
    (L / "verifica.js").write_text(VERIFICA_JS, encoding="utf-8")
    idx = (L / "index.html").read_text(encoding="utf-8")
    idx = idx.replace("<pre id=\"r\">", "")
    idx = idx.replace('<script src="lesson-data.js',
                      '<script src="risposte.js"></script>\n'
                      '<script src="lesson-data.js')
    idx = idx.replace('<script src="main.js',
                      '<script src="main.js')
    idx = idx.replace('</body>', '<pre id="r">…</pre>\n'
                                 '<script src="verifica.js"></script>\n</body>')
    (L / "index.html").write_text(idx, encoding="utf-8")

    handler = functools.partial(start_lesson._RangeHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    out_f = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out_f.close()
    try:
        with open(out_f.name, "w", encoding="utf-8") as fo:
            subprocess.run(
                chrome_args(7000) + ["--dump-dom",
                                 f"http://127.0.0.1:{port}/l/index.html"],
                stdout=fo, stderr=subprocess.DEVNULL, timeout=60)
    finally:
        httpd.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    dom = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
    m = re.search(r'<pre id="r">(.*?)</pre>', dom, re.S)
    testo = (m.group(1) if m else "").strip()
    assert testo and testo != "…", (
        f"il player non ha risposto (DOM {len(dom)} byte): "
        "LEARN_API mancante o errore JS in main.js")
    if testo.startswith("ERRORE"):
        pytest.fail(f"errore nel player: {testo}")
    import html as _h
    return json.loads(_h.unescape(testo))


# Struttura di `results` REALE (verificata in main.js):
#   quiz      -> booleano   (results[idx].quiz = good)
#   vf        -> array di booleani, uno per quesito
#   scenario/seq/match/flash -> booleano
#   classifica-> intero, compila -> array di booleani
# Totali di questo fixture: 3 moduli x (quiz 1 punto + V/F 3 quesiti) = 12,
# piu' l'esame da 1 punto = 13 punti su 7 attivita'.
TOTALE_PUNTI = 13
TOTALE_ATTIVITA = 7


def test_denominatore_fisso_saltare_non_alza_la_precisione(lezione):
    """Il bug piu' grave: saltare un'attivita' la escludeva dal denominatore.

    Rispondendo a 1 attivita' su 7 (= 1 punto su 1) la precisione era del
    100%: medaglia d'oro, 5 stelle, export "precisione 100%". Ora il
    denominatore e' sempre il totale del percorso.
    """
    # solo la prima attivita', tutta giusta
    r = _valuta(lezione, {"1": {"quiz": True}})
    M = r["score"]
    assert M["t"] == TOTALE_PUNTI, f"totale atteso {TOTALE_PUNTI}, risulta {M['t']}"
    assert M["e"] == 1, f"punti ottenuti errati: {M['e']}"
    assert M["totaleAtt"] == TOTALE_ATTIVITA, M["totaleAtt"]
    assert M["pct"] < 10, (
        f"con una sola attivita' svolta la precisione deve essere bassa, "
        f"risulta {M['pct']}% (denominatore selettivo reintroducito)")
    assert M["copertura"] < 20, f"copertura errata: {M['copertura']}%"
    assert M["pctSvolte"] == 100, "sulle attivita' svolte la precisione deve essere 100%"
    assert M["completo"] is False
    assert r["export_punti"] == f"1/{TOTALE_PUNTI}", r["export_punti"]
    assert r["export_attivita"] == f"1/{TOTALE_ATTIVITA}", r["export_attivita"]


def test_percorso_completo_tutto_giusto(lezione):
    risposte = {
        "1": {"quiz": True},
        "2": {"vf": [True, True, True]},
        "4": {"quiz": True},
        "5": {"vf": [True, True, True]},
        "7": {"quiz": True},
        "8": {"vf": [True, True, True]},
    }
    r = _valuta(lezione, risposte)
    M = r["score"]
    assert M["e"] == 12 and M["t"] == TOTALE_PUNTI, f"{M['e']}/{M['t']}"
    assert M["pct"] == round(12 * 100 / TOTALE_PUNTI), M["pct"]
    assert r["export_copertura"] == f"{round(12 * 100 / TOTALE_PUNTI)}%", r["export_copertura"]
    # l'esame non risolto non azzera il percorso, ma resta da fare
    assert r["exam"]["t"] == 1 and r["exam"]["e"] == 0
    assert r["exam"]["superato"] is False
    assert r["exam"]["svolte"] == 0


def test_esame_con_soglia_reale(lezione):
    """La soglia era scritta nella slide ('soglia 70%') e mai calcolata."""
    giusto = _valuta(lezione, {"9": {"quiz": True}})
    assert giusto["exam"]["e"] == 1 and giusto["exam"]["t"] == 1
    assert giusto["exam"]["soglia"] == 1, "soglia 70% di 1 punto = 1"
    assert giusto["exam"]["superato"] is True
    assert giusto["exam"]["pct"] == 100
    assert giusto["export_esame"]["superato"] is True
    assert giusto["export_esame"]["soglia"] == "1/1"

    sbagliato = _valuta(lezione, {"9": {"quiz": False}})
    assert sbagliato["exam"]["e"] == 0, f"risposta esatta contata come errore: {sbagliato['exam']}"
    assert sbagliato["exam"]["superato"] is False
    assert sbagliato["export_esame"]["superato"] is False
    # l'esame pesa sul punteggio complessivo
    assert sbagliato["score"]["e"] == 0
    assert sbagliato["score"]["pct"] == 0


def test_voto_parziale_al_vero_falso(lezione):
    """3 quesiti, 2 giusti: 2 dei 3 punti, non tutto o niente."""
    r = _valuta(lezione, {"2": {"vf": [True, False, True]}})
    assert r["score"]["e"] == 2, r["score"]["e"]
    assert r["score"]["tSvolte"] == 3
    assert r["score"]["pctSvolte"] == round(2 * 100 / 3)


def test_tempo_reale_senza_minimo_artifizioso(lezione):
    """Il tempo veniva forzato a un minimo: 3 secondi e 2 minuti risultavano
    identici in classifica."""
    r = _valuta(lezione, {"1": {"quiz": True}})
    t = r["export_tempo_min"]
    assert isinstance(t, (int, float))
    assert t < 1.0, f"una sessione di pochi secondi deve valere < 1 min, risulta {t}"


# --- il percorso vero, con i click dello studente -------------------------

CLICK_JS = r"""
// Risponde come uno studente: apre la slide e clicca l'opzione con il testo
// richiesto. Non si usa la POSIZIZIONE perche' le opzioni vengono mescolate
// (stableOrder): cliccare "la prima" non significa "la giusta", ed e' proprio
// il comportamento che i test devono verificare.
window.addEventListener('load', () => setTimeout(() => {
  const out = document.getElementById('r');
  try {
    const A = window.LESSON_API;
    for (const passo of (window.PIANO || [])) {
      A.go(passo.slide);
      const scelte = [...document.querySelectorAll('#slide .quiz .opt')];
      if (!scelte.length) throw new Error('nessuna opzione sulla slide ' + passo.slide);
      const testo = scelte.map(o => {
        // il testo dell'opzione e' quello mostrato DOPO la lettera: la
        // lettera segue la posizione, quindi cambia a ogni rimescolamento
        const l = o.querySelector('.letter');
        const t = o.textContent.trim();
        return (l ? t.slice(l.textContent.length) : t).trim();
      });
      const k = testo.findIndex(t => t === passo.testo);
      if (k < 0) {
        throw new Error('opzione "' + passo.testo + '" non presente; trovate: '
                        + JSON.stringify(testo));
      }
      scelte[k].click();
    }
    A.paintScore();
    const exp = A.buildExport();
    out.textContent = JSON.stringify({
      score: A.scoreMetrics(),
      segnalibri: exp.segnalibri,
      errori_per_tipo: exp.errori_per_tipo,
      risposte: window.LESSON_STATE.LOG.length
    });
  } catch (e) { out.textContent = 'ERRORE: ' + e.message; }
}, 1200));
"""


def _clicca(lezione, piano):
    """Esegue una sequenza di click reali e restituisce lo stato."""
    if not CHROME:
        _browser_obligatorio()
    import html as _h
    import re

    import start_lesson

    root = Path(tempfile.mkdtemp())
    shutil.copytree(lezione, root / "l")
    L = root / "l"
    (L / "piano.js").write_text("window.PIANO = %s;\n" % json.dumps(piano), encoding="utf-8")
    (L / "clicca.js").write_text(CLICK_JS, encoding="utf-8")
    idx = (L / "index.html").read_text(encoding="utf-8")
    idx = idx.replace('<script src="lesson-data.js',
                      '<script src="piano.js"></script>\n<script src="lesson-data.js')
    idx = idx.replace('</body>', '<pre id="r">…</pre>\n'
                                 '<script src="clicca.js"></script>\n</body>')
    (L / "index.html").write_text(idx, encoding="utf-8")

    handler = functools.partial(start_lesson._RangeHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    out_f = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out_f.close()
    try:
        with open(out_f.name, "w", encoding="utf-8") as fo:
            subprocess.run(
                chrome_args(8000) + ["--dump-dom",
                                 f"http://127.0.0.1:{httpd.server_address[1]}/l/index.html"],
                stdout=fo, stderr=subprocess.DEVNULL, timeout=60)
    finally:
        httpd.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    dom = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
    m = re.search(r'<pre id="r">(.*?)</pre>', dom, re.S)
    testo = (m.group(1) if m else "").strip()
    assert testo and testo != "…", f"nessun risultato (DOM {len(dom)} byte)"
    if testo.startswith("ERRORE"):
        pytest.fail(testo)
    return json.loads(_h.unescape(testo))


ORDINE_JS = r"""
// Lettura e rilettura NELLA STESSA sessione: e' il caso che conta. Fra due
// caricamenti diversi della pagina il rimescolamento e' casuale per scelta
// (uno studente che riapre la lezione trova un ordine nuovo), quindi confrontare
// due sessioni non significherebbe nulla.
window.addEventListener('load', () => setTimeout(() => {
  const out = document.getElementById('r');
  try {
    const A = window.LESSON_API;
    const leggi = () => [...document.querySelectorAll('#slide .quiz .opt')]
      .map(o => {
        const l = o.querySelector('.letter');
        const t = o.textContent.trim();
        return (l ? t.slice(l.textContent.length) : t).trim();
      });
    A.go(1);
    const primo = leggi();
    A.go(0); A.go(1);          // indietro e poi avanti, come lo studente
    const secondo = leggi();
    A.go(2); A.go(1);          // un secondo giro, per sicurezza
    const terzo = leggi();
    out.textContent = JSON.stringify({ primo: primo, secondo: secondo, terzo: terzo });
  } catch (e) { out.textContent = 'ERRORE: ' + e.message; }
}, 1300));
"""


def _ordine_stabile(lezione):
    """(primo, secondo, terzo) letti nella stessa sessione."""
    if not CHROME:
        _browser_obligatorio()
    import html as _h
    import re

    import start_lesson

    root = Path(tempfile.mkdtemp())
    shutil.copytree(lezione, root / "l")
    L = root / "l"
    (L / "ordine.js").write_text(ORDINE_JS, encoding="utf-8")
    idx = (L / "index.html").read_text(encoding="utf-8")
    idx = idx.replace('</body>', '<pre id="r">…</pre>\n'
                                 '<script src="ordine.js"></script>\n</body>')
    (L / "index.html").write_text(idx, encoding="utf-8")
    handler = functools.partial(start_lesson._RangeHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    out_f = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out_f.close()
    try:
        with open(out_f.name, "w", encoding="utf-8") as fo:
            subprocess.run(
                chrome_args(8000) + ["--dump-dom",
                                 f"http://127.0.0.1:{httpd.server_address[1]}/l/index.html"],
                stdout=fo, stderr=subprocess.DEVNULL, timeout=60)
    finally:
        httpd.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    dom = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
    m = re.search(r'<pre id="r">(.*?)</pre>', dom, re.S)
    testo = (m.group(1) if m else "").strip()
    assert testo and testo != "…", f"nessun risultato (DOM {len(dom)} byte)"
    if testo.startswith("ERRORE"):
        pytest.fail(testo)
    d = json.loads(_h.unescape(testo))
    return d["primo"], d["secondo"], d["terzo"]


def test_risposta_sbagliata_crea_il_segnalibro(lezione):
    """Il segnalibro 'da rivedere' era solo manuale (tasto S): gli errori non
    finivano mai nel percorso di ripasso, cioe' il ripasso non guardava dove
    l'errore era avvenuto. Qui si clicca davvero l'opzione sbagliata."""
    r = _clicca(lezione, [{"slide": 1, "testo": "sbagliata A"}])
    assert r["risposte"] >= 1, "la risposta non e' stata registrata"
    assert r["score"]["e"] == 0, f"la risposta sbagliata ha dato punti: {r['score']}"
    assert r["segnalibri"], "la slide con risposta errata non e' stata segnalata"
    assert r["errori_per_tipo"], "l'export non riporta gli errori per tipo di attivita'"


def test_risposta_esatta_crea_il_punto_e_non_il_segnalibro(lezione):
    r = _clicca(lezione, [{"slide": 1, "testo": "giusta"}])
    assert r["score"]["e"] == 1, f"la risposta corretta non ha dato il punto: {r['score']}"
    assert not r["segnalibri"], "una risposta corretta non deve generare un segnalibro"


AUTOAVVIO_JS = r"""
// Simula la fine dell'audio e controlla se il player cambia slide.
window.addEventListener('load', () => setTimeout(() => {
  const out = document.getElementById('r');
  try {
    const A = window.LESSON_API;
    // ATTENZIONE: LESSON_STATE e' un getter che restituisce un'istantanea,
    // quindi `cur` va riletto ogni volta, non tenuto in una variabile.
    const cur = () => window.LESSON_STATE.cur;
    const esito = {};
    // 1) slide di CONTENUTO (indice 0, nessuna attivita'): finito l'audio
    //    NON deve avanzare da solo, l'avanti e' una scelta dello studente
    A.go(0);
    esito.contenuto_parte = cur();
    A.audio.dispatchEvent(new Event('ended'));
    esito.contenuto_dopo = cur();
    // 2) slide con ATTIVITA' (indice 1, quiz): non deve avanzare
    A.go(1);
    esito.attivita_parte = cur();
    A.audio.dispatchEvent(new Event('ended'));
    esito.attivita_dopo = cur();
    // 3) il pulsante "Avanti" resta l'unico modo di proseguire
    A.go(0);
    document.getElementById('btnNext').click();
    esito.dopo_tasto_avanti = cur();
    esito.slide0_ha_attivita = A.slideHaAttivita(0);
    esito.slide1_ha_attivita = A.slideHaAttivita(1);
    out.textContent = JSON.stringify(esito);
  } catch (e) { out.textContent = 'ERRORE: ' + e.message; }
}, 1300));
"""


def _autoavvanza(lezione):
    if not CHROME:
        pytest.skip("Chrome/Edge non trovato")
    import html as _h
    import re

    import start_lesson

    root = Path(tempfile.mkdtemp())
    shutil.copytree(lezione, root / "l")
    L = root / "l"
    (L / "auto.js").write_text(AUTOAVVIO_JS, encoding="utf-8")
    idx = (L / "index.html").read_text(encoding="utf-8")
    idx = idx.replace('</body>', '<pre id="r">…</pre>\n'
                                 '<script src="auto.js"></script>\n</body>')
    (L / "index.html").write_text(idx, encoding="utf-8")
    handler = functools.partial(start_lesson._RangeHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    out_f = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out_f.close()
    try:
        with open(out_f.name, "w", encoding="utf-8") as fo:
            subprocess.run(
                chrome_args(8000) + ["--dump-dom",
                                 f"http://127.0.0.1:{httpd.server_address[1]}/l/index.html"],
                stdout=fo, stderr=subprocess.DEVNULL, timeout=60)
    finally:
        httpd.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    dom = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
    m = re.search(r'<pre id="r">(.*?)</pre>', dom, re.S)
    testo = (m.group(1) if m else "").strip()
    assert testo and testo != "…", f"nessun risultato (DOM {len(dom)} byte)"
    if testo.startswith("ERRORE"):
        pytest.fail(testo)
    return json.loads(_h.unescape(testo))


def test_l_avanzamento_automatico_e_disattivato(lezione):
    """Richiesta del docente: a fine audio il player non deve passare da solo
    alla slide successiva, nemmeno su quelle di solo contenuto.

    Prima l'avanzamento automatico saltava le attivita' (dopo un reclamo dello
    studente), ma restava attivo sul contenuto: chi ascoltava non decideva
    quando ripartire. Ora la scelta e' sempre sua e l'unico modo di proseguire
    e' il pulsante "Avanti" (o la freccia destra).
    """
    r = _autoavvanza(lezione)
    assert r["slide0_ha_attivita"] is False, "la slide 0 dovrebbe essere di solo contenuto"
    assert r["slide1_ha_attivita"] is True, "la slide 1 dovrebbe avere il quiz"

    # sul contenuto NON avanza da solo
    assert r["contenuto_dopo"] == r["contenuto_parte"], (
        f"l'avanzamento automatico e' ancora attivo sul contenuto: "
        f"{r['contenuto_parte']} -> {r['contenuto_dopo']}")
    # sull'attivita' resta fermo
    assert r["attivita_dopo"] == r["attivita_parte"], (
        f"il player e' andato avanti mentre lo studente leggeva l'attivita': "
        f"{r['attivita_parte']} -> {r['attivita_dopo']}")
    # il tasto "Avanti" porta avanti: e' l'unico modo di proseguire
    assert r["dopo_tasto_avanti"] == 1, (
        f"il pulsante Avanti non ha funzionato dalla slide 0: "
        f"si e' arrivati a {r['dopo_tasto_avanti']}")


def test_il_pulsante_avanti_e_ancora_utile_sulle_attivita(lezione):
    """Il pulsante "Avanti" e' l'unico modo di proseguire (l'avanzamento
    automatico non esiste piu'): deve funzionare anche dopo aver risposto."""
    r = _valuta_clic_e_avanti(lezione)
    assert r["cur_dopo_avanti"] == 2, (
        f"il pulsante Avanti non ha funzionato dalla slide 1: "
        f"si e' arrivati a {r['cur_dopo_avanti']}")


def _valuta_clic_e_avanti(lezione):
    import start_lesson

    JS = r"""
    window.addEventListener('load', () => setTimeout(() => {
      const out = document.getElementById('r');
      try {
        const A = window.LESSON_API;
        const cur = () => window.LESSON_STATE.cur;
        A.go(1);
        const btn = document.getElementById('btnNext');
        const prima = cur();
        btn.click();
        out.textContent = JSON.stringify({ prima: prima, cur_dopo_avanti: cur() });
      } catch (e) { out.textContent = 'ERRORE: ' + e.message; }
    }, 1300));
    """
    root = Path(tempfile.mkdtemp())
    shutil.copytree(lezione, root / "l")
    L = root / "l"
    (L / "avanti.js").write_text(JS, encoding="utf-8")
    idx = (L / "index.html").read_text(encoding="utf-8")
    idx = idx.replace('</body>', '<pre id="r">…</pre>\n'
                                 '<script src="avanti.js"></script>\n</body>')
    (L / "index.html").write_text(idx, encoding="utf-8")
    handler = functools.partial(start_lesson._RangeHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    out_f = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out_f.close()
    try:
        with open(out_f.name, "w", encoding="utf-8") as fo:
            subprocess.run(
                chrome_args(7000) + ["--dump-dom",
                                 f"http://127.0.0.1:{httpd.server_address[1]}/l/index.html"],
                stdout=fo, stderr=subprocess.DEVNULL, timeout=60)
    finally:
        httpd.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    dom = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
    import html as _h
    import re
    m = re.search(r'<pre id="r">(.*?)</pre>', dom, re.S)
    testo = (m.group(1) if m else "").strip()
    assert testo and testo != "…", f"nessun risultato (DOM {len(dom)} byte)"
    if testo.startswith("ERRORE"):
        pytest.fail(testo)
    return json.loads(_h.unescape(testo))


def test_ordine_opzioni_stabile_ma_giusta_risposta_riconosciuta(lezione):
    """Le opzioni vengono mescolate, ma l'ordine non cambia se lo studente
    torna indietro e poi avanti: altrimenti risponde alla posizione invece
    che al contenuto, e il "Rileggi" gli cambia la domanda sotto gli occhi.

    Il bug che questo presidia: shuffleOpts() veniva rieseguito a ogni render,
    quindi ogni visita alla slide presentava un ordine diverso.
    """
    primo, secondo, terzo = _ordine_stabile(lezione)
    assert primo == secondo, (
        f"l'ordine cambia tornando indietro: {primo} poi {secondo}")
    assert primo == terzo, (
        f"l'ordine cambia al secondo giro: {primo} poi {terzo}")
    assert sorted(primo) == ["giusta", "sbagliata A", "sbagliata B", "sbagliata C"], (
        f"le opzioni mostrate non sono quelle del quiz: {primo}")
    # la risposta giusta resta riconoscibile: cliccandola si ottiene il punto
    r = _clicca(lezione, [{"slide": 1, "testo": "giusta"}])
    assert r["score"]["e"] == 1, "la risposta corretta non e' stata riconosciuta"


def test_la_risposta_corretta_non_e_mai_in_prima_posizione(lezione):
    """`shuffleOpts` sposta la corretta se capita prima, per non creare il
    pattern "la risposta giusta e' sempre A".

    Il controllo e' deterministico, non statistico: la garanzia e' costruita nel
    codice, quindi basta verificare l'invariante su molti campionamenti. Senza
    la correzione il test fallirebbe con probabilita' ~1 su 10^30.
    """
    if not CHROME:
        _browser_obligatorio()
    import html as _h
    import re

    import start_lesson

    JS = r"""
    window.addEventListener('load', () => setTimeout(() => {
      const out = document.getElementById('r');
      try {
        const A = window.LESSON_API;
        const q = window.LESSON_STATE.slides[1].blocks[0].quiz;
        const good = q.opts.findIndex(o => o.ok);
        let prima = 0, distribuzione = {};
        for (let i = 0; i < 400; i++) {
          const ord = A.shuffleOpts(q.opts);
          if (ord[0] === good) prima++;
          distribuzione[ord.indexOf(good)] = (distribuzione[ord.indexOf(good)] || 0) + 1;
        }
        out.textContent = JSON.stringify({ good: good, prima: prima, dist: distribuzione });
      } catch (e) { out.textContent = 'ERRORE: ' + e.message; }
    }, 1300));
    """
    root = Path(tempfile.mkdtemp())
    shutil.copytree(lezione, root / "l")
    L = root / "l"
    (L / "guardia.js").write_text(JS, encoding="utf-8")
    idx = (L / "index.html").read_text(encoding="utf-8")
    idx = idx.replace('</body>', '<pre id="r">…</pre>\n'
                                 '<script src="guardia.js"></script>\n</body>')
    (L / "index.html").write_text(idx, encoding="utf-8")
    handler = functools.partial(start_lesson._RangeHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    out_f = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out_f.close()
    try:
        with open(out_f.name, "w", encoding="utf-8") as fo:
            subprocess.run(
                chrome_args(7000) + ["--dump-dom",
                                 f"http://127.0.0.1:{httpd.server_address[1]}/l/index.html"],
                stdout=fo, stderr=subprocess.DEVNULL, timeout=60)
    finally:
        httpd.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    dom = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
    m = re.search(r'<pre id="r">(.*?)</pre>', dom, re.S)
    testo = (m.group(1) if m else "").strip()
    assert testo and testo != "…", f"nessun risultato (DOM {len(dom)} byte)"
    if testo.startswith("ERRORE"):
        pytest.fail(testo)
    d = json.loads(_h.unescape(testo))
    assert d["prima"] == 0, (
        f"la risposta corretta e' comparsa per prima {d['prima']} volte su 400")
    assert len(d["dist"]) >= 2, (
        f"la risposta corretta compare sempre nella stessa posizione: {d['dist']}")
