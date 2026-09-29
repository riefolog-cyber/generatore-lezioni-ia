import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

import pytest  # noqa: E402

import player_template as pt  # noqa: E402

ASSETS = BASE / "tools" / "player_assets"


# ------------------------------------------------------------------ i file
def test_i_file_del_player_esistono():
    """CSS e JS devono stare in file reali, non in stringhe Python.

    Prima erano una stringa CSS da ~970 righe e una stringa JS da ~2.450 righe
    dentro player_template.py: nessun editor di sintassi le controllava e un
    errore JavaScript arrivava in produzione senza essere notato. Stessa lezione
    che aveva già fatto dividere tools/panel_ui.py in file separati.
    """
    for nome in ("base.css", "main.js", "__init__.py"):
        p = ASSETS / nome
        assert p.is_file(), f"manca tools/player_assets/{nome}"
        assert p.stat().st_size > 0, f"tools/player_assets/{nome} e' vuoto"


def test_player_template_non_contiene_piu_i_grossi_blocchi():
    """Il file Python non deve più crescere con il CSS e il JS del player."""
    righe = (BASE / "tools" / "player_template.py").read_text(encoding="utf-8").count("\n")
    assert righe < 600, (
        f"player_template.py e' tornato a {righe} righe: CSS e JS devono stare "
        "in tools/player_assets/, non in stringhe Python")


def test_gli_asset_non_tornano_una_stringa_unica():
    """Il file monolitico con CSS+JS dentro non deve ricomparire."""
    css = (ASSETS / "base.css").read_text(encoding="utf-8")
    js = (ASSETS / "main.js").read_text(encoding="utf-8")
    assert "<style" not in css and "function" not in css, "il CSS contiene JavaScript"
    assert "window.LESSON_API" in js
    # il corpo del CSS non deve ricominciare con i blocchi di tema (sono generati
    # in Python, perché dipendono dall'accent che deriva dal titolo)
    assert css.lstrip().startswith("/*"), (
        "base.css deve contenere SOLO il corpo stabile del foglio")


# ------------------------------------------------------------- byte identici
def test_gli_asset_producono_lo_stesso_css_e_js():
    """Il CSS e il JS generati devono combaciare con quelli dei file.

    Se i segnaposto o i confini fossero sbagliati, ogni lezione già generata
    cambierebbe byte e l'impronta del player cambierebbe a vuoto.
    """
    t = dict(pt.THEMES["dark"])
    t["accent"], t["accent2"], t["accentink"] = pt._accent_from_title("Prova Asset")
    css = pt._css(t)
    js = pt._js()
    # i due blocchi di tema generati + il corpo letto dal file
    assert ":root, [data-theme=\"dark\"]" in css
    assert "[data-theme=\"light\"]" in css
    assert css.endswith((ASSETS / "base.css").read_text(encoding="utf-8"))
    assert js == (ASSETS / "main.js").read_text(encoding="utf-8")


def test_player_version_cambia_se_il_player_cambia():
    """L'impronta del player deve dipendere dal contenuto reale dei file."""
    prima = pt.player_version()
    assert len(prima) == 8
    # il CSS entra con temi neutri: un accento diverso non deve cambiare l'impronta
    t = dict(pt.THEMES["dark"], accent="#123456", accent2="#654321", accentink="#000000")
    assert pt._css(t) != pt._css(dict(pt.THEMES["dark"],
                                      accent="#abcdef", accent2="#fedcba",
                                      accentink="#ffffff"))


# ------------------------------------------------------------------ node --check
def test_main_js_e_valido_javascript():
    """`node --check` sul player: l'errore di sintassi non deve arrivare in classe."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node non disponibile")
    r = subprocess.run([node, "--check", str(ASSETS / "main.js")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert r.returncode == 0, f"main.js non valido:\n{r.stderr}"


# ------------------------------------------------------- lezione generata
def test_il_player_scritto_usa_gli_asset(tmp_path):
    """write_player() deve copiare nel pacchetto gli stessi file degli asset."""
    d = Path(tmp_path)
    pt.write_player(d, "Prova Asset", tema="dark")
    assert (d / "main.js").read_text(encoding="utf-8") == (ASSETS / "main.js").read_text(encoding="utf-8")
    css = (d / "main.css").read_text(encoding="utf-8")
    assert css == pt._css(dict(pt.THEMES["dark"],
                               **{k: v for k, v in zip(
                                   ("accent", "accent2", "accentink"),
                                   pt._accent_from_title("Prova Asset"))}))
    assert (d / "index.html").is_file()
    assert json.loads((d / "manifest.json").read_text(encoding="utf-8"))["name"] == "Prova Asset"
