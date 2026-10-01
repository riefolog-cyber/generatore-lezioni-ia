# -*- coding: utf-8 -*-
"""L'interfaccia del pannello deve stare in file ispezionabili.

Storia: HTML, CSS e JavaScript del pannello (60 KB) vivevano dentro una
stringa Python di 1220 righe in `tools/panel_ui.py`. Nessun editor di sintassi
poteva controllarli, nessun lint segnalava un errore, e un errore di sintassi
nel JavaScript arrivava in produzione senza essere notato: i test esistenti
verificavano solo che la pagina contenesse certe stringhe.

Ora i tre pezzi sono file separati, il JavaScript passa `node --check`, e
questi test impediscono che qualcuno torni indietro.
"""
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
UI = BASE / "tools" / "panel_ui"
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))


def test_i_file_del_pannello_esistono_e_non_sono_vuoti():
    for nome in ("index.html", "panel.css", "panel.js", "__init__.py"):
        p = UI / nome
        assert p.is_file(), f"manca tools/panel_ui/{nome}"
        assert p.stat().st_size > 0, f"tools/panel_ui/{nome} e' vuoto"


def test_il_vecchio_blob_non_e_tornato():
    """Il file unico da 1220 righe non deve ricomparire: e' il formato che
    rendeva il pannello non ispezionabile."""
    vecchio = BASE / "tools" / "panel_ui.py"
    assert not vecchio.exists(), (
        "tools/panel_ui.py sta tornando: HTML/CSS/JS devono stare in "
        "tools/panel_ui/ come file separati")
    if vecchio.exists():                      # solo per un messaggio utile
        righe = sum(1 for _ in io.open(vecchio, encoding="utf-8"))
        pytest.fail(f"il blob e' tornato con {righe} righe")


def test_il_javascript_del_pannello_e_valido():
    """Prima non era verificabile: era dentro una stringa Python."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node non disponibile")
    r = subprocess.run([node, "--check", str(UI / "panel.js")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert r.returncode == 0, f"panel.js non valido:\n{r.stderr[:400]}"


def test_il_css_del_pannello_e_bilanciato():
    css = io.open(UI / "panel.css", encoding="utf-8").read()
    # i commenti CSS possono contenere graffe: vanno rimossi prima di contare
    pulito = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert pulito.count("{") == pulito.count("}"), (
        f"graffe non bilanciate: {pulito.count('{')} aperte, "
        f"{pulito.count('}')} chiuse")
    assert "{" in css, "il foglio di stile sembra vuoto"


def test_il_pannello_si_ricompone_completo():
    """La ricomposizione deve contenere le tre parti: se un segnaposto non
    viene sostituito, il pannello si aprirebbe senza stili e senza script."""
    from panel_ui import PANEL_HTML, build_html

    assert "@@CSS@@" not in PANEL_HTML, "segnaposto del CSS non sostituito"
    assert "@@JS@@" not in PANEL_HTML, "segnaposto del JS non sostituito"
    assert "<style>" in PANEL_HTML and "<script>" in PANEL_HTML
    assert "function api(" in PANEL_HTML, "il JavaScript del pannello manca"
    assert build_html() == PANEL_HTML, "build_html() e PANEL_HTML divergono"
    # l'HTML deve essere completo: apertura e chiusura dei tag principali
    for tag in ("<html", "</html>", "<body", "</body>"):
        assert tag in PANEL_HTML, f"manca {tag}"


def test_il_css_della_pagina_indice_e_in_un_file():
    """Stessa cosa per la pagina indice delle lezioni (`start_lesson`)."""
    css = BASE / "tools" / "hub.css"
    assert css.is_file(), "manca tools/hub.css"
    testo = css.read_text(encoding="utf-8")
    assert ".card" in testo, "hub.css non contiene le regole della pagina indice"
    pulito = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    assert pulito.count("{") == pulito.count("}")
    import start_lesson
    assert ".card" in start_lesson._hub_page()


# ------------------------------------------------------- Assistenza rapida
def test_il_risultato_della_diagnostica_e_visibile():
    """Il bug: `runDiagnostica` apriva il riquadro con `box.hidden = false`,
    ma il `<pre>` aveva `style="display:none"` inline. L'attributo `hidden` e lo
    stile inline sono due cose diverse: togliere il primo NON annulla il
    secondo, quindi il computed display restava `none` e il docente premeva il
    bottone e non vedeva NULLA. Verificato in Chrome: altezza 0px.

    La correzione e' `hidden` nel markup (che `box.hidden = false` rimuove
    davvero) piu' una regola `[hidden]{display:none}` che non dipende da
    attributi inline.
    """
    html = io.open(UI / "index.html", encoding="utf-8").read()
    m = re.search(r'<pre id="diagnostica"[^>]*>', html)
    assert m, "il riquadro della diagnostica non c'e' piu'"
    tag = m.group(0)
    assert "display:none" not in tag.replace(" ", ""), (
        f"il riquadro ha uno stile inline che sopravvive a `hidden`: {tag}")
    assert "hidden" in tag, (
        f"serve l'attributo `hidden`, non uno stile inline: {tag}")
    css = io.open(UI / "panel.css", encoding="utf-8").read()
    assert "#diagnostica" in css, "manca il CSS del riquadro diagnostica"
    assert re.search(r"#diagnostica\s*\[hidden\]\s*\{[^}]*display:none", css), (
        "manca la regola che nasconde il riquadro quando e' chiuso")


def test_la_diagnostica_usa_un_solo_handler():
    """Il bottone aveva un `onclick` inline E `$('#btnDiagnostica').onclick`:
    due registrazioni dello stesso gesto. Rimosso l'inline, resta un solo
    handler in panel.js, e il pulsante torna utilizzabile dopo il controllo.
    """
    html = io.open(UI / "index.html", encoding="utf-8").read()
    m = re.search(r'<button[^>]*id="btnDiagnostica"[^>]*>', html)
    assert m and "onclick" not in m.group(0), (
        f"handler inline duplicato sul bottone: {m.group(0) if m else 'assente'}")
    js = io.open(UI / "panel.js", encoding="utf-8").read()
    assert "$('#btnDiagnostica').onclick = runDiagnostica;" in js
    assert "btn.disabled = false" in js, (
        "il bottone deve tornare attivo dopo il controllo, altrimenti la "
        "diagnostica si puo' eseguire una volta sola")


def test_la_diagnostica_mostra_le_cause_e_non_solo_lo_stato():
    """`netdiag` calcolava `deps` e `lezioni` e il pannello non le mostrava:
    il docente leggeva "tutto ok" mentre mancava, per dire, ffmpeg e i PDF non
    si potevano leggere. Sono le informazioni per cui si apre 'Assistenza'.
    """
    js = io.open(UI / "panel.js", encoding="utf-8").read()
    corpo = js.split("async function runDiagnostica")[1].split("\nasync function")[0] \
        if "\nasync function" in js else js.split("async function runDiagnostica")[1][:2000]
    assert "d.deps" in corpo, "le dipendenze mancanti non vengono mostrate"
    assert "d.lezioni" in corpo, "il numero di lezioni non viene mostrato"
    assert "d.same_configured_ip" in corpo, (
        "l'IP configurato sbagliato e' la prima causa del telefono che non "
        "si collega: deve essere segnalato")
    # le dipendenze mancanti vanno nominate in chiaro, non come chiavi grezze
    assert "nomi" in corpo and "ffmpeg" in corpo


def test_il_frontend_usa_solo_variabili_css_esistenti():
    """Un `var(--accent)` inesistente rendeva il testo TRASPARENTE: il
    riquadro della diagnostica sarebbe stato illeggibile senza errori in
    console. Ogni variabile usata deve essere definita da qualche parte."""
    css = io.open(UI / "panel.css", encoding="utf-8").read()
    definite = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    usate = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    mancanti = usate - definite
    assert not mancanti, f"variabili CSS usate ma non definite: {sorted(mancanti)}"


def test_la_diagnostica_senza_javascript_e_indicata():
    """Se panel.js non parte, il bottone non fa nulla e il docente resta senza
    risposta: il <noscript> gli dice dove andare a leggerla a mano."""
    html = io.open(UI / "index.html", encoding="utf-8").read()
    assert "<noscript>" in html, "manca il ripiego senza JavaScript"
    assert "/api/diagnostica" in html, "il ripiego deve indicare la rotta della diagnostica"


# ------------------------------------- BES/DSA: solo lato studente
def test_il_pannello_non_ha_piu_la_voce_accessibilita():
    """La scelta BES/DSA e' stata tolta dal pannello e lasciata allo studente.

    Motivo: dal pannello serviva a cambiare il TESTO generato e costringeva a
    rigenerare la lezione; nello studente (menu Attivita') riduce subito
    opzioni e attivita', senza toccare i dati. Il pannello deve quindi
    mandare `standard`, ma senza crashare se il selettore non c'e' piu'.
    """
    html = io.open(UI / "index.html", encoding="utf-8").read()
    assert 'id="profAccess"' not in html, (
        "il selettore Accessibilita' e' tornato nel pannello")
    assert "Accessibilità <select" not in html, (
        "l'etichetta Accessibilita' e' tornata nel pannello")
    # gli altri tre parametri del profilo restano
    for s in ("profDurata", "profLivello", "profObiettivo"):
        assert f'id="{s}"' in html, f"il selettore {s} e' sparito per errore"
    js = io.open(UI / "panel.js", encoding="utf-8").read()
    k = js.find("function profilo()")
    assert k > 0
    corpo = js[k:k + 600]
    # con il selettore assente `$("#profAccess").value` darebbe TypeError
    assert "acc ? acc.value : 'standard'" in corpo, (
        "profilo() deve tollerare l'assenza del selettore, altrimenti la "
        "generazione dal pannello va in crash")


# ------------------------------- Whisper nel pannello: backend, non solo pip
def test_il_pannello_riconosce_whisper_anche_su_arm():
    """Il pannello diceva "Whisper ✗" su Windows ARM anche quando la
    trascrizione funzionava: `_deps()` guardava SOLO il modulo `faster_whisper`,
    che su ARM non e' installabile, e ignorava whisper.cpp (il backend usato
    l'). Falso negativo: si perdeva tempo a credere che l'audio non fosse
    supportato. Ora un backend qualsiasi basta.
    """
    src = io.open(BASE / "panel.py", encoding="utf-8").read()
    i = src.find("def whisper_disponibile()")
    assert i > 0, "manca il controllo whisper che accetta anche whisper.cpp"
    corpo = src[i:src.find("return {", i)]
    assert "faster_whisper" in corpo and "whisper_cpp" in corpo, (
        "il controllo deve accettare SIA faster-whisper SIA whisper.cpp")
    # la chiave esposta e' generica: il pannello non deve dipendere dal backend
    assert '"whisper": whisper_disponibile()' in src, (
        "la chiave 'whisper' (generica) non e' quella esposta")
    assert '"faster_whisper": have("faster_whisper")' not in src, (
        "la chiave faster_whisper continua a legarsi al solo modulo pip")
    # il frontend deve leggere la chiave generica
    js = io.open(UI / "panel.js", encoding="utf-8").read()
    assert "['whisper', 'Whisper (trascrizione audio locale)']" in js, (
        "i chip del pannello devono leggere la chiave 'whisper'")
    assert "faster_whisper" not in js, (
        "il frontend non deve piu' conoscere il backend pip")
