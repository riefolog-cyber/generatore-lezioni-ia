import json
import re
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


# ------------------------------------------------------- menu ATTIVITA'
def test_l_accessibilita_e_nel_menu_attivita_e_non_piu_nel_menu_header(tmp_path):
    """Menu Accessibilita' spostato dal menu header (☰) a un menu ATTIVITA'.

    Nel menu header finiva in fondo alla lista (dopo Stampa/Tema) e sul
    telefono non era raggiungibile. La regressione da presidiare: `btnAcc`
    non deve ricomparire e i tre strumenti devono stare nel nuovo pannello.
    """
    pt.write_player(Path(tmp_path), "Prova Menu", tema="dark")
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'id="btnAtt"' in html and 'id="actdrop"' in html
    assert 'id="btnAcc"' not in html, "il vecchio bottone Accessibilita' e' tornato"
    # i tre strumenti sono dentro #actdrop
    i = html.index('id="actdrop"')
    blocco = html[i:html.index("</header>", i)]
    for b in ("btnZoom", "btnContrast", "btnSpeed"):
        assert b in blocco, f"{b} non e' nel menu Attivita'"
    assert "accMenu" in blocco


def test_il_menu_attivita_e_collegato_nel_js():
    js = (ASSETS / "main.js").read_text(encoding="utf-8")
    assert "btnAcc" not in js, "main.js cerca ancora il bottone rimosso"
    assert "actdrop" in js and "btnAtt" in js
    css = (ASSETS / "base.css").read_text(encoding="utf-8")
    assert "#actdrop" in css, "manca il CSS del menu Attivita'"


def test_workbook_e_un_tipo_di_attivita_del_player():
    """Un blocco nuovo deve essere dichiarato in TUTTI i posti che contano:
    `ACT_TYPES` (il conteggio delle attivita' e i punti di navigazione),
    `renderBlock` (la resa) e il punteggio."""
    js = (ASSETS / "main.js").read_text(encoding="utf-8")
    assert "'workbook'" in js, "workbook non e' in ACT_TYPES"
    assert "if (b.workbook) return blockWorkbook" in js, "manca la resa"
    assert "function blockWorkbook" in js
    assert "r.workbook" in js, "workbook non entra nel punteggio"
    assert "b.workbook" in js.split("function slideTotal")[1].split("}")[0] + "}", (
        "workbook non conta nel totale delle attivita' della slide")
    assert ".wbkta" in (ASSETS / "base.css").read_text(encoding="utf-8"), (
        "manca il CSS del quaderno")


# ------------------------------------- barra audio e scrollbar sul telefono
def test_su_telefono_la_barra_audio_e_compatta_e_il_play_tocabile():
    """La barra audio e' fissa in basso e ruba altezza ALL'ATTIVITA', che e'
    la parte da leggere: misurata a 62px con il pulsante "Attiva audio" aperto.

    Il play passa a 36px ma l'area di tocco deve restare 44px, altrimenti
    il dito lo manca: si ottiene con uno pseudo-elemente invisibile che non
    occupa spazio nel flusso (la tecnica gia' usata per i pallini).
    """
    css = (ASSETS / "base.css").read_text(encoding="utf-8")
    mob = re.search(r"@media \(max-width: 760px\) \{\s*\n\s*#audioBar[^\n]*\n"
                    r".*?#btnPlay::after \{[^}]*width: 44px;[^}]*\}", css, re.S)
    assert mob, (
        "manca il blocco 'telefono' che compatta la barra audio e garantisce "
        "l'area di tocco da 44px con #btnPlay::after")
    # il play NON si gonfia piu' a 48px su touch
    assert "#btnPlay { width: 48px; height: 48px" not in css, (
        "il play torna a 48px su touch: la barra audio ruba di nuovo altezza")
    # 30px visibili, 44px di area di tocco: sotto i 30 il dito lo manca
    assert re.search(r"#btnPlay \{ width: 30px; height: 30px", css), (
        "il play da 30px e' sparito dal blocco telefono")
    assert "min-height: 0" in css, (
        "senza `min-height: 0` il min-height da 44px del blocco coarse "
        "riporterebbe il play a 44px vanificando la riduzione")
    # il pulsante "Attiva audio" era il vero occupante (153x45 misurati)
    assert "#audioUnlock { padding: 2px 5px" in css, (
        "il pulsante 'Attiva audio' non e' compattato: da solo faceva 45px")


def test_la_scrollbar_del_telefono_non_ruba_larghezza():
    """Chrome/Android riservano 15-17px di larghezza alla scrollbar, togliendoli
    al testo delle domande e delle opzioni. Sul telefono la barra e' gia' un
    overlay: `scrollbar-width: none` restituisce quei pixel al contenuto."""
    css = (ASSETS / "base.css").read_text(encoding="utf-8")
    assert "html::-webkit-scrollbar { width: 0; height: 0; }" in css, (
        "la scrollbar deve sparire da sola sui telefoni")
    assert re.search(r"@media \(max-width: 760px\), \(pointer: coarse\) \{\s*"
                     r"html \{ scrollbar-width: none; \}", css), (
        "manca la regola che azzera la scrollbar su schermi piccoli e touch")
    # sul PC resta sottile ma visibile
    assert "html::-webkit-scrollbar { width: 8px; height: 8px; }" in css


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
# ------------------------------------------------- pallini su schermo stretto
def test_i_pallini_non_occupano_lo_schermo_su_telefono():
    """I pallini di navigazione devono restare compatti sul telefono.

    Sul cellulare finivano alti tre righe: `@media (pointer: coarse)` dava 44px
    di altezza a ogni `#dots button`, e `nav button { flex: 1 1 0 }` li stirava
    a tutta larghezza (le famose ellissi giganti). Con 17 slide la parte bassa
    rubava metà schermo e l'attività sotto era illeggibile.
    """
    css = (ASSETS / "base.css").read_text(encoding="utf-8")

    # 1) il blocco "coarse" non deve più gonfiare i pallini
    coarse = css.split("@media (pointer: coarse)")[1].split("@media")[0]
    assert "#dots button" not in coarse.split("min-height: 44px;")[0].split("{")[-1], (
        "i pallini sono di nuovo nella lista dei 44px: sul telefono il blocco "
        "sotto va su piu righe e nasconde l'attivita'")
    assert "#dots button { min-height: 0; min-width: 0; }" in coarse, (
        "manca l'area di tocco compatta dei pallini")
    assert "#dots button::before" in coarse, (
        "manca lo pseudo-elemento che allarga l'area di tocco senza occupare spazio")

    # 2) su schermo stretto: una riga sola, scorrevole, pallini non stirati
    mobile = css.split("@media (max-width: 640px)")[1]
    assert "flex-wrap: nowrap;" in mobile, "i pallini devono stare su una riga sola"
    assert "overflow-x: auto;" in mobile, "la riga dei pallini deve essere scorrevole"
    assert "#dots button { flex: 0 0 auto; }" in mobile, (
        "senza flex: 0 0 auto i pallini prendono flex: 1 1 0 e diventano ellissi")
# ------------------------------------------------- comfort sul telefono
def test_le_barre_in_fondo_occupano_poco_altezza():
    """Audio e navigazione stanno in fondo pagina e rubano altezza all'attivita'.

    Erano alte (audio: 9px di padding, play 42px, bottoni 34px, seek 7px;
    nav: 11px di padding, bottoni 10px/20px): su una slide con attivita' il
    fondo dello schermo finiva per essere quasi tutto bottoni. Ridotto
    l'ingombro VISIVO, ma i bersagli di tocco sui schermi tattili restano
    44px: si tocca comodo senza rubare spazio all'esercizio.
    """
    css = (ASSETS / "base.css").read_text(encoding="utf-8")
    audio = css.split("audio bar */")[1].split("@media")[0]
    assert "padding: 4px 14px;" in audio, "la barra audio deve essere piatta"
    assert "width: 32px; height: 32px" in audio, "il play e' troppo grande"
    assert "height: 26px;" in audio, "i tasti audio sono troppo alti"
    assert "height: 5px;" in audio, "la seek e' troppo alta"
    nav = css.split("nav */")[1].split("@media")[0]
    assert "padding: 5px calc(18px" in nav, "la barra di navigazione e' troppo alta"
    assert "padding: 6px 14px;" in nav, "i bottoni Avanti/Indietro sono troppo alti"
    coarse = css.split("@media (pointer: coarse)")[1].split("@media")[0]
    assert "min-height: 44px;" in coarse, (
        "l'area di tocco da 44px sui tattili non si puo' perdere")
    # una riga sola: play e barra di avanzamento AFFIANCATI
    assert "flex-wrap: nowrap;" in audio, (
        "con flex-wrap la barra audio va su due-tre righe e ruba mezzo schermo "
        "all'attivita'")
    assert "text-overflow: ellipsis" in audio and "min-height" not in audio.split("#cap")[1].split("}")[0], (
        "il sottotitolo deve stare in una riga (con puntini), non far crescere la barra")
    dots = css.split("#dots {")[1].split("}")[0]
    assert "flex-wrap: nowrap;" in dots, (
        "i pallini su una lezione lunga andrebbero su tre righe: la barra in "
        "basso deve restare alta una riga")
    assert "overflow-x: auto;" in dots, (
        "la riga dei pallini deve essere scorrevole su schermo largo")
    # il pallino corrente va riportato in vista, altrimenti su slide alte
    # resterebbe fuori dal visibile
    js = (ASSETS / "main.js").read_text(encoding="utf-8")
    assert "dots.scrollLeft" in js, (
        "su una lezione lunga il pallino corrente deve scorrere in vista")
    assert "_elCap.title = capTxt" in js, (
        "il sottotitolo in una riga deve mostrare il testo intero nel tooltip")


def test_la_lezione_usa_area_sicura_e_gesti_del_dito():
    """Su telefono: niente tagli sotto la tacca, niente pull-to-refresh.

    Senza `viewport-fit=cover` iPhone tronca la pagina sul bordo tondo e
    `env(safe-area-inset-*)` vale sempre 0: il titolo finiva sotto la tacca
    e i pulsanti sotto la barra Home.
    """
    css = (ASSETS / "base.css").read_text(encoding="utf-8")

    assert "env(safe-area-inset-top)" in css, (
        "manca lo spazio sopra: il titolo finisce sotto la tacca")
    assert "env(safe-area-inset-bottom)" in css, (
        "manca lo spazio sotto: il bottone Avanti finisce sotto la barra Home")
    assert "overscroll-behavior: none" in css, (
        "senza questo si tira la pagina e su Android parte il refresh")
    assert "touch-action: manipulation" in css, (
        "manca touch-action: senza, doppio tap zooma la pagina in classe")
    assert "-webkit-tap-highlight-color: transparent" in css, (
        "su Android ogni tocco lascia un rettangolo grigio")


def test_il_player_offre_lo_schermo_intero():
    """Il pulsante schermo intero deve esistere e saper spiegare iPhone.

    Su iPhone Safari la Fullscreen API non esiste: senza un avviso il
    pulsante fallirebbe in silenzio e lo studente penserebbe sia rotto.
    """
    js = (ASSETS / "main.js").read_text(encoding="utf-8")
    tpl = (BASE / "tools" / "player_template.py").read_text(encoding="utf-8")

    assert 'id="btnFull"' in tpl, "manca il pulsante schermo intero nel menu"
    assert "viewport-fit=cover" in tpl, (
        "senza viewport-fit=cover l'area sicura e' sempre zero")
    assert "_safe('btnFull')" in js, "il pulsante non e' collegato"
    assert "requestFullscreen" in js, "manca la chiamata alla Fullscreen API"
    # il prefisso webkit serve ai Safari meno recenti
    assert "webkitRequestFullscreen" in js, "manca il prefisso per Safari"
    # su iPhone non si puo' fare davvero: si spiega la strada vera
    assert "Aggiungi alla schermata Home" in js, (
        "su iPhone va spiegato 'Aggiungi alla schermata Home', non fallire")
    assert "display-mode: standalone" in js, (
        "serve per capire che la lezione e' gia' installata a schermo intero")


def test_lo_schermo_interoParte_al_primo_tocco():
    """La lezione deve aprirsi a schermo intero senza che si chieda nulla.

    Non puo' partire al caricamento: la Fullscreen API viene accettata solo
    dentro un gesto reale dell'utente, quindi chiamarla all'avvio viene
    ignorata da ogni browser. La richiesta va armata subito e lanciata al
    PRIMO tocco, che e' il momento lecito: per lo studente il risultato e'
    lo stesso, e non si producono errori in console.
    """
    js = (ASSETS / "main.js").read_text(encoding="utf-8")

    # la richiesta parte da un ascoltatore di gesto, non da un timer/setTimeout:
    # un timer NON e' user activation e il browser rifiuterebbe la richiesta
    for ev in ("pointerdown", "touchstart", "click", "keydown"):
        assert f"addEventListener('{ev}', armi" in js, (
            f"manca l'aggancio al gesto '{ev}': senza un gesto reale il "
            "browser ignora la richiesta di schermo intero")
    # ... e il gesto va rimosso subito: entra una volta sola
    assert "removeEventListener('pointerdown', armi" in js, (
        "gli ascoltatori del gesto devono staccarsi dopo il primo tocco")
    # la promessa va gestita: se il browser rifiuta, non si insiste
    assert "p.catch" in js, (
        "senza gestire il rifiuto della promessa si insiste inutilmente")
    # solo su schermi piccoli: sul PC non deve scattare nulla all'avvio
    assert "(max-width: 900px)" in js and "(pointer: coarse)" in js, (
        "il fullscreen automatico deve valere solo su telefono/tablet")
    # uscita volontaria = rinuncia definitiva, non una gabbia
    assert "localStorage.setItem(KEY, 'no')" in js, (
        "se lo studente esce deve poter stare fuori: niente reinserimento forzato")
    assert "rinunciato" in js, "la rinuncia va letta all'avvio"
    # gia' installata o gia' a schermo intero: non fare nulla
    assert "isStandalone()" in js and "attivo()" in js, (
        "non si deve richiedere il fullscreen a chi e' gia' dentro")
