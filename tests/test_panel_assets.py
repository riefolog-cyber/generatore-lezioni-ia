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
