# -*- coding: utf-8 -*-
"""Runner delle generazioni: stato, coda FIFO e log in memoria."""
from __future__ import annotations

import collections
import contextlib
import threading
import time


class LogWriter:
    """Writer che instrada stdout/stderr del job nel log del pannello."""

    def __init__(self, log):
        self.log = log

    def write(self, text):
        text = text.rstrip()
        if text:
            with self.log.lock:
                self.log.append(text)
        return len(text) + 1

    def flush(self):
        pass


class PanelLog(collections.deque):
    def __init__(self):
        super().__init__(maxlen=500)
        self.lock = threading.RLock()


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
            try:
                try:
                    from sources import begin_transcription
                    begin_transcription()
                except Exception:
                    pass
                writer = LogWriter(self.log)
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    fn()
                with self.lock:
                    self.state["ok"] = True
                self._log("✔ JOB COMPLETATO")
            except Exception as exc:  # noqa: BLE001
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
                try:
                    self.history_append(
                        self.state.get("kind"), self.state.get("source"), self.state.get("ok"),
                        (self.state.get("done_at") or time.time()) -
                        (self.state.get("started_at") or time.time()))
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
