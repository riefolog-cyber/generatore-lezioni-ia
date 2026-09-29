# -*- coding: utf-8 -*-
"""Registro degli endpoint del pannello: un posto solo invece di due catene di if.

Storia: `panel.py` instradava le richieste con due lunghe catene di `if path ==
"/api/..."` (una per GET dentro `_api`, una per `elif parsed.path == ...` dentro
`do_POST`), sparse su ~150 righe. Ogni endpoint era definito in un punto diverso
e le due liste potevano divergere senza che nulla lo segnalasse: aggiungere una
API richiedeva di ricordarsi di aggiungerla in due posti, e una dimenticanza
significava "404 API sconosciuta" invece di un errore esplicito in sviluppo.

Qui ogni rotta è dichiarata una volta sola, con la sua guardia esplicita:

    GET_ROUTES = {
        "/api/state": Route(handler="_stato"),
        ...
    }

Le guardie non sono un dettaglio estetico, sono il contratto di sicurezza:

  - `localhost`     l'endpoint è riservato a chi è già sul PC del docente.
                    E' il default di quasi tutte le API.
  - `pin_classe`    serve anche dalla rete di classe, ma solo con il PIN
                    docente (dati degli studenti, export della classifica).
  - `pubblica`      raggiungibile da chiunque sulla rete (hub, lezione
                    condivisa): niente dati del docente.

La tabella è l'unica fonte di verità: `panel.py` la interroga e, se un path
risulta sconosciuto, il test `test_panel_routes.py` lo segnala.
"""
from __future__ import annotations

# ------------------------------------------------------------------ guardie
LOCALHOST = "localhost"      # solo dal PC del docente (loopback)
PIN_CLASSE = "pin_classe"    # anche dalla rete, ma solo con PIN docente
PUBBLICA = "pubblica"        # raggiungibile da chiunque sulla rete


class Route:
    """Una rotta del pannello: metodo da chiamare, guardia e forma dell'errore.

    `method` è il nome del metodo di PanelHandler (senza il `_`), così il
    registro non deve conoscere la classe e resta testabile da solo.

    `err` dice come il corpo di un'eccezione deve tornare al chiamante, perché
    non è uniforme e la differenza è parte del contratto:

      "started"  {"started": false, "reason": ...}   generazione rifiutata
      "ok"       {"ok": false, "error": ...}         operazione semplice
      None       la rotta gestisce da se ogni errore (upload, PIN, 409...)

    `query` True se il metodo riceve la query string oltre al body.
    `rate_limit` True per le rotte che avviano una generazione (le sole
    costose): senza il freno anti-abusi, un doppio click ripete una build.
    """

    __slots__ = ("method", "guardia", "doc", "err", "query", "rate_limit")

    def __init__(self, method, guardia=LOCALHOST, doc="", err="ok",
                 query=False, rate_limit=False):
        self.method = method
        self.guardia = guardia
        self.doc = doc
        self.err = err
        self.query = query
        self.rate_limit = rate_limit

    def __repr__(self):
        return f"Route({self.method!r}, {self.guardia!r})"


# Rotte raggiungibili anche dalla rete di classe: sono le uniche due che
# mostrano dati degli studenti, e quindi chiedono il PIN docente.
PINNED = PIN_CLASSE
# Il resto delle API del pannello lavora sulla configurazione e sul filesystem
# del docente: mai dalla rete di classe.
LOCAL = LOCALHOST

GET_ROUTES = {
    "/api/classifica": Route("classifica", PINNED, "classifica di classe"),
    "/api/classifica_export": Route("classifica_export", PINNED, "export classifica"),
    "/api/state": Route("stato", LOCAL, "stato del lavoro in corso"),
    "/api/log": Route("log", LOCAL, "log in tempo reale"),
    "/api/build": Route("build", LOCAL, "genera da file/link"),
    "/api/reaudio": Route("reaudio", LOCAL, "rigenera l'audio"),
    "/api/export_single": Route("export_single", LOCAL, "esporta file HTML unico"),
    "/api/progress": Route("progresso", LOCAL, "avanzamento"),
    "/api/history": Route("history", LOCAL, "cronologia generazioni"),
    "/api/lan": Route("lan", LOCAL, "indirizzo LAN e lezione condivisa"),
    "/api/qr": Route("qr", LOCAL, "QR della lezione"),
    "/api/diagnostica": Route("diagnostica", LOCAL, "diagnostica"),
    "/api/logs_download": Route("logs_download", LOCAL, "scarica i log"),
    "/api/voices": Route("voices", LOCAL, "voci neurali disponibili"),
    "/api/lesson_data": Route("lesson_data", LOCAL, "dati di una slide"),
    "/api/settings": Route("settings", LOCAL, "impostazioni pubbliche"),
}

# POST: ogni rotta e' una mutazione e passa dal controllo PIN del docente
# (impostato in `do_POST`). Le rotte con `err=None` gestiscono da se ogni
# errore, perché hanno un contratto proprio (413 sull'upload, 409 su
# cancel_job, doppio controllo del PIN su settings).
POST_ROUTES = {
    # L'UNICA rotta aperta alla rete di classe: lo studente invia il proprio
    # risultato. Valori sanitizzati e limitati, nessun dato del docente, e resta
    # comunque sotto il controllo origine in do_POST.
    "/api/classifica": Route("classifica_post", PUBBLICA, "invio del risultato",
                             err=None),
    "/api/build": Route("build", LOCAL, "genera da file/link",
                        err="started", rate_limit=True),
    "/api/build_text": Route("build_text", LOCAL, "genera da testo incollato",
                             err="started", rate_limit=True),
    "/api/move_slide": Route("move_slide", LOCAL, "sposta una slide"),
    "/api/add_slide": Route("add_slide", LOCAL, "aggiungi slide"),
    "/api/delete_slide": Route("delete_slide", LOCAL, "elimina slide"),
    "/api/reaudio": Route("reaudio", LOCAL, "rigenera l'audio",
                          err="started", query=True),
    "/api/lesson_action": Route("lesson_action", LOCAL, "share/rename/elimina lezione"),
    "/api/upload": Route("upload", LOCAL, "carica materiale", err=None),
    "/api/cancel_queue": Route("cancel_queue", LOCAL, "svuota la coda", err=None),
    "/api/cancel_job": Route("cancel_job", LOCAL, "annulla il lavoro in corso", err=None),
    "/api/settings": Route("settings", LOCAL, "salva le impostazioni", err=None),
    "/api/save_slide": Route("save_slide", LOCAL, "salva una slide"),
    "/api/reaudio_slide": Route("reaudio_slide", LOCAL, "audio di una slide",
                                err="started", query=True),
    "/api/tts_preview": Route("tts_preview", LOCAL, "anteprima voce"),
    "/api/clear_history": Route("clear_history", LOCAL, "svuota la cronologia", err=None),
    "/api/refresh_player": Route("refresh_player", LOCAL, "aggiorna il player", err=None),
    "/api/reset_classifica": Route("reset_classifica", LOCAL, "azzera la classifica", err=None),
}


def lookup(method, path):
    """Rotta per metodo e path, oppure None se il path non e' dichiarato."""
    return (GET_ROUTES if method == "GET" else POST_ROUTES).get(path)


def declared(method):
    """Elenco dei path dichiarati per un metodo (per i test)."""
    return set(GET_ROUTES if method == "GET" else POST_ROUTES)


__all__ = ["Route", "GET_ROUTES", "POST_ROUTES", "LOCALHOST", "PIN_CLASSE",
           "PUBBLICA", "lookup", "declared"]
