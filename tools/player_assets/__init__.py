"""CSS e JavaScript del player, in file reali invece che in stringhe Python.

Storia: dentro `player_template.py` c'erano una stringa CSS da ~970 righe e una
stringa JS da ~2.450 righe, dentro un file Python di 3.657 righe. Nessun editor
di sintassi poteva controllarle, nessun lint segnalava un errore, e un mistype
nel JavaScript arrivava in produzione senza che nessuno se ne accorgesse. E' la
stessa lezione che aveva gia' fatto dividere `tools/panel_ui.py` in tre file.

Qui i file sono:
  - `base.css`  il foglio di stile stabile (il colore dipende dal tema)
  - `main.js`   tutta la logica del player

I due blocchi di tema in testa al CSS restano generati in Python
(`_theme_block` in player_template.py) perche' dipendono dall'accent, che
deriva dal titolo della lezione.

`main.js` viene anche validato con `node --check` dalla suite di test.
"""
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def _leggi(nome):
    return (_DIR / nome).read_text(encoding="utf-8")


def base_css():
    """Il corpo stabile del foglio di stile, senza i blocchi di tema."""
    return _leggi("base.css")


def main_js():
    """Lo script del player, esattamente come viene scritto in main.js."""
    return _leggi("main.js")


__all__ = ["base_css", "main_js"]
