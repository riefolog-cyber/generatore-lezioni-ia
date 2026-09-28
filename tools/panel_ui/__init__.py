# -*- coding: utf-8 -*-
"""Interfaccia del pannello: HTML, CSS e JavaScript in file reali.

Prima era una stringa Python di ~1120 righe dentro `panel_ui.py`: nessun
editor di sintassi poteva controllarla, nessun lint poteva segnalare un errore,
e un mistype nel JavaScript arrivava in produzione. Ora i tre pezzi sono file
separati e ispezionabili, e questo modulo li ricompone per chi chiama
`PANEL_HTML` (pannello e test non cambiano).

`panel.js` viene anche validato con `node --check` dalla suite di test.
"""
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def _leggi(nome):
    return (_DIR / nome).read_text(encoding="utf-8")


def build_html():
    """Il pannello completo, identico a prima: gli stessi byte."""
    return (_leggi("index.html")
            .replace("@@CSS@@", "<style>\n" + _leggi("panel.css") + "\n</style>")
            .replace("@@JS@@", "<script>\n" + _leggi("panel.js") + "\n</script>"))


PANEL_HTML = build_html()

__all__ = ["PANEL_HTML", "build_html"]
