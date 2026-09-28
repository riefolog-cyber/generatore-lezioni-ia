# -*- coding: utf-8 -*-
"""Runner delle generazioni: stato, coda FIFO e log in memoria."""
from __future__ import annotations

import collections
import contextlib
import sys
import threading
import time


class LogWriter:
    """Writer che instrada stdout/stderr del job nel log del pannello."""

    def __init__(self, log):
        self.log = log

    def write(self, text):
        # contratto file-like: restituire i caratteri davvero scritti.
        # Prima si restituiva len(text)+1 DOPO il rstrip, cioè un numero
        # diverso dai caratteri emessi: trappola per chi riusa la classe.
        if not text:
            return 0
        stripped = text.rstrip()
        if stripped:
            with self.log.lock:
                self.log.append(stripped)
        return len(text)


class PanelLog(collections.deque):
    """Log circolare con contatore assoluto di righe emesse.

    Il contatore (`n_lines`) è un CURSORE MONOTONO: il client lo usa come
    posizione assoluta. Con il vecchio schema (indice dentro la finestra
    scorrevole) le righe saltavano o si duplicavano appena il deque scorreva
    oltre i 500 elementi, cioè in tutte le build lunghe.
    """

    def __init__(self):
        super().__init__(maxlen=500)
        self.lock = threading.RLock()
        self.n_lines = 0

    def append(self, item):
        super().append(item)
        self.n_lines += 1

    def since(self, cursor, limit=200):
        """Righe emesse dopo `cursor`, con il nuovo cursore.

        Se il cursore è troppo vecchio (oltre la finestra) si restituisce
        comunque tutto quello che resta, con un flag `truncated` che il client
        può ignorare: nessuna riga viene inventata o contata due volte.
        """
        with self.lock:
            total = self.n_lines
            if cursor is None or cursor < 0:
                return list(self)[-limit:], total, False
            have = len(self)
            oldest = total - have            # cursore assoluto del primo elem.
            if cursor >= total:
                return [], total, False
            if cursor < oldest:
                return list(self)[-limit:], total, True
            start = cursor - oldest
            return list(self)[start:start + limit], total, False


class JobManager:
    def __init__(self, history_append, invalidate_cache, file_log):
        self.log = PanelLog()
        self.state = {"running": False, "kind": None, "source": None,
                      "error": None, "done_at": None, "ok": None,
                      "started_at": None, "progress": ""}
        self.queue = collections.deque(maxlen=5)
        self.lock = threading.RLock()
        self.history_append = history_append
        self.invalidate_cache = invalidate_cache
        self.file_log = file_log

    def _log(self, text):
        with self.log.lock:
            self.log.append(str(text).rstrip())

    def snapshot(self):
        """Copia coerente di stato + coda, sotto lock.

        Senza, /api/log leggeva JOB e QUEUE senza lock e faceva due letture
        indipendenti di QUEUE: poteva rispondere "coda: 3" a coda vuota.
        """
        with self.lock:
            s = dict(self.state)
            q = list(self.queue)
        s["queue"] = [item[2] for item in q]
        return s

    def pending_source(self, source_name):
        with self.lock:
            return (self.state["running"] and self.state["source"] == source_name) or any(
                str(item[2]) == source_name for item in self.queue)

    def start(self, fn, kind, source):
        with self.lock:
            if self.state["running"]:
                if len(self.queue) >= self.queue.maxlen:
                    return False, False
                self.queue.append((fn, kind, source))
                self._log(f"⏳ in coda [{kind}] {source} (posizione {len(self.queue)})")
                return True, True
            self.state.update(running=True, kind=kind, source=source, error=None,
                              done_at=None, ok=None, started_at=time.time(), progress="")
        self._log(f"▶ [{kind}] {source}")

        def work():
            # kind/source/ok/started_at catturati SUBITO: nel finally precedente
            # venivano riletti da self.state DOPO aver rilasciato il lock, e un
            # altro thread poteva aver già avviato il job successivo: la
            # cronologia registrava kind/source del job sbagliato.
            j_kind, j_source = kind, source
            j_started = time.time()
            j_ok = None
            try:
                try:
                    from sources import begin_transcription
                    begin_transcription()
                except Exception:
                    pass
                writer = LogWriter(self.log)
                # NB: contextlib.redirect_stdout è GLOBALE al processo, non
                # thread-local. Per tutta la durata della generazione (minuti)
                # qualsiasi altro thread che stampa finisce qui — compreso
                # l'errore "disco pieno" di _flog, che è proprio il caso in cui
                # servirebbe leggerlo in console. Ripristinato sempre nel finally.
                old_out, old_err = sys.stdout, sys.stderr
                try:
                    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                        fn()
                finally:
                    sys.stdout, sys.stderr = old_out, old_err
                j_ok = True
                with self.lock:
                    self.state["ok"] = True
                self._log("✔ JOB COMPLETATO")
            except Exception as exc:  # noqa: BLE001
                j_ok = False
                with self.lock:
                    self.state["error"] = str(exc)
                    self.state["ok"] = False
                self._log(f"✗ ERRORE: {exc}")
                try:
                    import traceback
                    self.file_log(f"JOB {kind} {source} failed: {exc}\n{traceback.format_exc()}")
                except Exception:
                    pass
            finally:
                with self.lock:
                    self.state["running"] = False
                    self.state["done_at"] = time.time()
                    j_done = self.state["done_at"]
                try:
                    self.history_append(j_kind, j_source, j_ok, j_done - j_started)
                except Exception:
                    pass
                self.invalidate_cache()
                self.run_next()

        threading.Thread(target=work, daemon=True).start()
        return True, False

    def run_next(self):
        with self.lock:
            item = self.queue.popleft() if self.queue else None
        if item:
            self.start(*item)

    def cancel_queue(self):
        with self.lock:
            count = len(self.queue)
            self.queue.clear()
        if count:
            self._log(f"✕ coda svuotata ({count} job rimossi)")
        return count
