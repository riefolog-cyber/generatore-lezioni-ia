# -*- coding: utf-8 -*-
"""Il registro delle rotte del pannello deve combaciare con i metodi reali.

Storia: `panel.py` instradava le richieste con due catene di `if path == ...`
(una per GET, una per POST). Ogni endpoint era definito in un solo punto ma le
due liste potevano divergere: una rotta dimenticata in una delle due catene
rispondeva "404 API sconosciuta" invece di un errore esplicito, e le guardie
(localhost / PIN docente) erano implicite nella POSIZIONE dell'`if` invece che
dichiarate.

Ora `tools/panel_routes.py` è l'unica fonte di verità. Questi test verificano
che ogni rotta dichiarata abbia davvero il metodo corrispondente su
PanelHandler: è il controllo che mancava e che rendeva le due catene fragili.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

import panel  # noqa: E402
from tools import panel_routes  # noqa: E402


# ------------------------------------------------------------- completezza
@pytest.mark.parametrize("metodo", ["GET", "POST"])
def test_ogni_rotta_ha_il_suo_metodo(metodo):
    """Ogni rotta dichiarata deve avere il metodo che il dispatch invoca."""
    mancanti = []
    for path, rotta in (panel_routes.GET_ROUTES if metodo == "GET"
                        else panel_routes.POST_ROUTES).items():
        if rotta.guardia == panel_routes.PUBBLICA:
            continue          # gestita in do_POST, chiama _classifica_post()
        prefisso = "_api_" if metodo == "GET" else "_post_"
        nome = prefisso + rotta.method
        if not callable(getattr(panel.PanelHandler, nome, None)):
            mancanti.append(f"{path} -> {nome}")
    assert not mancanti, "rotte senza implementazione: " + ", ".join(mancanti)


def test_nessun_metodo_di_rotta_e_orfano():
    """Un metodo _api_*/_post_* che non è nel registro non verrebbe mai chiamato."""
    dichiarati = {"_api_" + r.method for r in panel_routes.GET_ROUTES.values()}
    dichiarati |= {"_post_" + r.method for r in panel_routes.POST_ROUTES.values()}
    # metodi con contratto proprio, non passano dal dispatch generico
    propri = {"_post_api", "_post_settings", "_post_refresh_player",
              "_post_upload", "_post_cancel_queue", "_post_cancel_job",
              "_post_clear_history", "_post_reset_classifica"}
    orfani = set()
    for nome in dir(panel.PanelHandler):
        if not (nome.startswith("_api_") or nome.startswith("_post_")):
            continue
        if nome in propri or nome in dichiarati:
            continue
        if callable(getattr(panel.PanelHandler, nome, None)):
            orfani.add(nome)
    assert not orfani, f"metodi che nessuna rotta chiama: {sorted(orfani)}"


# ------------------------------------------------------------------ guardie
def test_la_classifica_richiede_il_pin_anche_dalla_rete():
    """I dati degli studenti non devono essere visibili senza PIN docente."""
    for path in ("/api/classifica", "/api/classifica_export"):
        assert panel_routes.GET_ROUTES[path].guardia == panel_routes.PIN_CLASSE, (
            f"{path} mostra i dati degli studenti: deve chiedere il PIN")


def test_tutte_le_altre_api_sono_riservate_a_localhost():
    """Configurazione, filesystem e QR non escono mai sulla rete di classe."""
    for path, rotta in panel_routes.GET_ROUTES.items():
        if rotta.guardia == panel_routes.PIN_CLASSE:
            continue
        assert rotta.guardia == panel_routes.LOCALHOST, f"{path} non è localhost-only"
    for path, rotta in panel_routes.POST_ROUTES.items():
        if rotta.guardia == panel_routes.PUBBLICA:
            continue          # l'unica aperta alla rete: l'invio dello studente
        assert rotta.guardia == panel_routes.LOCALHOST, f"{path} POST non è localhost-only"


def test_una_sola_rotta_e_aperta_alla_rete():
    """L'invio del risultato dello studente è l'unica API aperta alla classe.

    Ogni rotta pubblica in piu' significa dati del docente raggiungibili da
    chiunque sia sulla rete Wi-Fi: il controllo che lo impedisce.
    """
    pubbliche = [p for p, r in panel_routes.POST_ROUTES.items()
                 if r.guardia == panel_routes.PUBBLICA]
    assert pubbliche == ["/api/classifica"], pubbliche
    for tabella, nome in ((panel_routes.GET_ROUTES, "GET"),
                          (panel_routes.POST_ROUTES, "POST")):
        for path, rotta in tabella.items():
            if rotta.guardia == panel_routes.PUBBLICA:
                continue
            assert rotta.guardia in (panel_routes.LOCALHOST,
                                     panel_routes.PIN_CLASSE), \
                f"{nome} {path}: guardia sconosciuta {rotta.guardia!r}"


# ------------------------------------------------------------ rate limiting
def test_solo_le_generazioni_hanno_il_freno_anti_abusi():
    """Il rate limit serve alle rotte costose: metterlo ovunque blocca il pannello."""
    con_freno = {p for p, r in panel_routes.POST_ROUTES.items() if r.rate_limit}
    assert con_freno == {"/api/build", "/api/build_text"}, con_freno


# ------------------------------------------------------------- contratti
def test_la_forma_dell_errore_e_dichiarata():
    """Le due forme esistono perché il pannello le legge in modo diverso."""
    for path, rotta in panel_routes.POST_ROUTES.items():
        assert rotta.err in ("started", "ok", None), f"{path}: err={rotta.err!r}"
    assert panel_routes.POST_ROUTES["/api/build"].err == "started"
    assert panel_routes.POST_ROUTES["/api/add_slide"].err == "ok"


def test_nessuna_rotta_dichiarata_e_ignorata():
    """Un path presente in una delle due liste non deve mancare nell'altra.

    `reaudio` esiste in GET e in POST con lo stesso metodo. `/api/classifica` è
    l'unica sovrapposizione con metodi diversi, ed è intenzionale: in GET mostra
    la classifica al docente (con PIN), in POST riceve il risultato dello
    studente (unica rotta pubblica). Sono le due sovrapposizioni legittime.
    """
    get = panel_routes.declared("GET")
    post = panel_routes.declared("POST")
    for path in get & post:
        a, b = panel_routes.GET_ROUTES[path], panel_routes.POST_ROUTES[path]
        if b.guardia == panel_routes.PUBBLICA:
            assert a.guardia == panel_routes.PIN_CLASSE, (
                f"{path}: in GET i dati sono protetti dal PIN")
            continue
        assert a.method == b.method, f"{path}: GET e POST chiamano metodi diversi"
    assert "/api/classifica" in post, "la POST della classifica (studente) deve esistere"
