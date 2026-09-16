# -*- coding: utf-8 -*-
"""Pannello di controllo web (locale).

Sostituisce l'avvio da terminale con una pagina grafica nel browser:
  - caricamento materiale via upload (drag & drop o Sfoglia: .docx/.pdf/
    .txt/.md/.html) con pulsante "Carica e genera" — SOLO upload, nessuna
    scansione della cartella progetto;
  - generazione da URL (sito web o video YouTube);
  - opzioni: rigenera anche se esiste (--force), bozza senza LLM (--bozza),
    rigenera solo l'audio di una lezione già generata (--reaudio);
  - console di log in tempo reale durante la generazione;
  - elenco delle lezioni generate con pulsante "Apri".

Sicurezza:
  - il pannello e le API /api/* rispondono SOLO alle richieste da localhost;
  - chiunque nella LAN vede solo l'indice delle lezioni (come prima);
  - i file del progetto (config.json, sorgenti, .git…) non vengono mai serviti.

Uso:  python panel.py        (avvia il pannello e apre il browser)
      python panel.py --port 9000
"""
import collections
import contextlib
import functools
import html
import http.server
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

from common import load_config  # noqa: E402
from sources import SUPPORTED_EXT, is_url  # noqa: E402
from start_lesson import _RangeHandler, _hub_page, find_port, list_lessons  # noqa: E402

CONFIG = load_config()
DEFAULT_PORT = int(CONFIG.get("porta", 8341))
MAX_UPLOAD_MB = 100        # limite per i file caricati dal pannello
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# Il pannello importa new_lesson UNA volta: i moduli restano in memoria anche
# se i file cambiano su disco (es. aggiornamenti del codice). Se uno dei file
# della pipeline cambia dopo l'avvio, il codice caricato è "stantio": /api/state
# lo segnala e l'interfaccia mostra l'avviso di riavvio, così una generazione
# non parte mai silenziosamente con il codice vecchio.
_PIPELINE_FILES = ("new_lesson.py", Path("tools") / "sources.py",
                   Path("tools") / "common.py", Path("tools") / "player_template.py")


def _pipeline_mtime():
    tot = 0.0
    for rel in _PIPELINE_FILES:
        try:
            tot += (BASE / rel).stat().st_mtime
        except OSError:
            pass
    return tot


_PIPELINE_MTIME_START = _pipeline_mtime()

# ------------------------------------------------------------------ job runner
LOG = collections.deque(maxlen=500)
JOB = {"running": False, "kind": None, "source": None, "error": None,
       "done_at": None, "ok": None, "started_at": None, "progress": ""}
QUEUE = collections.deque(maxlen=5)  # coda FIFO: (fn, kind, source)
JOB_LOCK = threading.RLock()
LOG_LOCK = threading.RLock()
HISTORY_FILE = BASE / "job_history.json"

# rate-limit build: max 20 build/ora per IP (rete scolastica / click multipli)
_RATE = {}
RATE_MAX = 20
RATE_WINDOW = 3600


def _rate_ok(ip):
    now = time.time()
    lst = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
    if len(lst) >= RATE_MAX:
        _RATE[ip] = lst
        return False
    lst.append(now)
    _RATE[ip] = lst
    return True


def _history_append(kind, source, ok, secs):
    try:
        hist = []
        if HISTORY_FILE.exists():
            hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8") or "[]")
        hist.append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
                     "source": str(source)[:160], "ok": bool(ok), "secs": round(secs or 0, 1)})
        HISTORY_FILE.write_text(json.dumps(hist[-100:], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


class _UploadTooBig(Exception):
    """File caricato dal pannello oltre il limite (risponde HTTP 413)."""


# ------------------------------------------------------------------ classifica di classe
# Gli studenti (anche da tablet sulla LAN) inviano il risultato del percorso;
# il pannello lo accumula in classifica.json e mostra la classifica per lezione.
CLASSIFICA_FILE = BASE / "classifica.json"
_CLASSIFICA_MAX = 500          # righe massime su disco (le più nuove vincono)
_CLASSIFICA_PANEL = 50         # righe mostrate in classifica per lezione


def _classifica_add(lesson, studente, punti, totale, completata, tempo_min):
    """Aggiunge un risultato (chiamato anche da client LAN: nessun segreto)."""
    try:
        rows = []
        if CLASSIFICA_FILE.exists():
            rows = json.loads(CLASSIFICA_FILE.read_text(encoding="utf-8") or "[]")
    except Exception:
        rows = []
    pct = round(punti / totale * 100) if totale > 0 else 0
    rows.append({"t": time.strftime("%Y-%m-%d %H:%M"), "lesson": str(lesson)[:80],
                 "studente": str(studente)[:40] or "Anonimo",
                 "punti": punti, "totale": totale, "pct": pct,
                 "completata": bool(completata), "tempo_min": tempo_min})
    try:
        CLASSIFICA_FILE.write_text(json.dumps(rows[-_CLASSIFICA_MAX:], ensure_ascii=False),
                                   encoding="utf-8")
    except OSError:
        pass


def _classifica_view():
    """Classifica per lezione: miglior risultato per studente, ordinato per
    percentuale (decrescente) e tempo (crescente)."""
    try:
        rows = json.loads(CLASSIFICA_FILE.read_text(encoding="utf-8") or "[]") \
            if CLASSIFICA_FILE.exists() else []
    except Exception:
        rows = []
    by_lesson = {}
    for r in rows:
        if isinstance(r, dict) and r.get("lesson"):
            by_lesson.setdefault(str(r["lesson"]), []).append(r)
    out = []
    for lesson in sorted(by_lesson):
        lst = by_lesson[lesson]
        best = {}
        for r in lst:                      # migli risultato per studente
            k = r.get("studente") or "Anonimo"
            prev = best.get(k)
            if (prev is None or r.get("pct", 0) > prev.get("pct", 0)
                    or (r.get("pct", 0) == prev.get("pct", 0)
                        and r.get("tempo_min", 9999) < prev.get("tempo_min", 9999))):
                best[k] = r
        ranked = sorted(best.values(), key=lambda r: (-r.get("pct", 0),
                                                      r.get("tempo_min", 9999)))
        out.append({"lesson": lesson, "rows": ranked[:_CLASSIFICA_PANEL]})
    return {"classifiche": out}


# ------------------------------------------------------------------ cache whitelist lezioni
# La whitelist delle lezioni viene valutata a ogni richiesta (anche per gli mp3
# durante la riproduzione). Un piccolo TTL evita glob su disco a ogni asset;
# l'invalidazione esplicita (dopo upload/generazione/ri-audio) rende le novità
# subito disponibili senza aspettare la scadenza.
LESSONS_CACHE_TTL = 2.0
_lessons_cache = {"at": 0.0, "names": None, "hub": None, "singles": None}


def _bump_lessons_cache():
    now = time.time()
    if _lessons_cache["names"] is None or now - _lessons_cache["at"] > LESSONS_CACHE_TTL:
        _lessons_cache.update(at=now,
                              names={l.name for l in list_lessons()},
                              singles={p.name for p in BASE.glob("*_singola.html")
                                       if p.is_file()},
                              hub=None)


def _allowed_lesson_names():
    _bump_lessons_cache()
    return _lessons_cache["names"] or set()


def _allowed_single_names():
    _bump_lessons_cache()
    return _lessons_cache["singles"] or set()


def _hub_page_cached():
    _bump_lessons_cache()
    if _lessons_cache["hub"] is None:
        _lessons_cache["hub"] = _hub_page()
    return _lessons_cache["hub"]


def _invalidate_lessons_cache():
    _lessons_cache["names"] = None
    _lessons_cache["singles"] = None
    _lessons_cache["hub"] = None
    _lessons_cache["at"] = 0.0


class _LogWriter:
    def write(self, s):
        s = s.rstrip()
        if s:
            with LOG_LOCK:
                LOG.append(s)
        return len(s) + 1

    def flush(self):
        pass


def _log(txt):
    with LOG_LOCK:
        LOG.append(str(txt).rstrip())


def _flog(txt):
    """Log persistente su file (diagnosi errori API anche dopo il riavvio)."""
    try:
        with open(BASE / "panel_errors.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " | " + str(txt).rstrip() + "\n")
    except OSError as e:
        # se disco pieno, almeno log su stderr
        try:
            print(f"_flog failed: {e}", file=sys.stderr)
        except Exception:
            pass


def _run_next_queued():
    with JOB_LOCK:
        if QUEUE:
            fn, kind, source = QUEUE.popleft()
            start_job(fn, kind, source)


def start_job(fn, kind, source):
    """Lancia una generazione in background. Se una è in corso, accoda (max 5).
    Ritorna (started, queued): started=True se partito subito."""
    with JOB_LOCK:
        if JOB["running"]:
            if len(QUEUE) >= QUEUE.maxlen:
                return (False, False)
            QUEUE.append((fn, kind, source))
            _log(f"⏳ in coda [{kind}] {source} (posizione {len(QUEUE)})")
            return (True, True)
        JOB.update(running=True, kind=kind, source=source, error=None,
                   done_at=None, ok=None, started_at=time.time(), progress="")
    _log(f"▶ [{kind}] {source}")

    def work():
        try:
            with contextlib.redirect_stdout(_LogWriter()), \
                    contextlib.redirect_stderr(_LogWriter()):
                fn()
            with JOB_LOCK:
                JOB["ok"] = True
            _log("✔ JOB COMPLETATO")
        except Exception as e:  # noqa: BLE001
            with JOB_LOCK:
                JOB["error"] = str(e)
                JOB["ok"] = False
            _log(f"✗ ERRORE: {e}")
            import traceback as _tb
            try:
                _flog(f"JOB {kind} {source} failed: {e}\n{_tb.format_exc()}")
            except Exception:
                pass
        finally:
            with JOB_LOCK:
                JOB["running"] = False
                JOB["done_at"] = time.time()
            try:
                _history_append(JOB.get("kind"), JOB.get("source"), JOB.get("ok"),
                                (JOB.get("done_at") or time.time()) - (JOB.get("started_at") or time.time()))
            except Exception:
                pass
            _invalidate_lessons_cache()   # una nuova lezione è (forse) pronta
            _run_next_queued()

    threading.Thread(target=work, daemon=True).start()
    return (True, False)


def cancel_queue():
    n = len(QUEUE)
    QUEUE.clear()
    if n:
        _log(f"✕ coda svuotata ({n} job rimossi)")
    return n


# ------------------------------------------------------------------ helpers
def lan_ip():
    # IP fisso da config (rete di classe senza internet: l'auto-rilevamento
    # via 8.8.8.8 fallisce e mostrerebbe "LAN non disponibile").
    try:
        fisso = str(CONFIG.get("lan_ip_fisso") or "").strip()
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", fisso):
            return fisso
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if not ip.startswith("127.") else None
    except Exception:
        return None


def _lesson_for(filename):
    stem = Path(filename).stem
    return f"{re.sub(r'[^A-Za-z0-9_]+', '_', stem).strip('_') or 'Lezione'}_lesson"


# Solo upload: niente più scansione della cartella progetto.
# I materiali generabili sono SOLO quelli caricati dal pannello in questa
# sessione (drag&drop / Sfoglia). I file già presenti su disco non compaiono.
UPLOADED = {}


def _materials():
    out = []
    for name, info in sorted(UPLOADED.items()):
        p = BASE / name
        if not p.is_file():
            continue
        try:
            out.append({
                "name": name,
                "size": p.stat().st_size,
                "modified": p.stat().st_mtime,
                "lesson": _lesson_for(name),
            })
        except OSError:
            continue
    return out


def _register_upload(name):
    UPLOADED[Path(name).name] = {"t": time.time()}


def _deps():
    def have(m):
        try:
            __import__(m)
            return True
        except Exception:
            return False
    return {
        "python_docx": have("docx"),
        "edge_tts": have("edge_tts"),
        "pypdf": have("pypdf"),
        "youtube_transcript_api": have("youtube_transcript_api"),
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def _single_files():
    """File HTML unici generati (Nome_singola.html) con dimensione."""
    out = []
    for p in sorted(BASE.glob("*_singola.html")):
        if p.is_file():
            try:
                out.append({"name": p.name,
                            "stem": p.name.replace("_singola.html", ""),
                            "size": p.stat().st_size})
            except OSError:
                continue
    return out


def _state():
    lessons = [{"name": l.name,
                "title": l.name.replace("_lesson", "")} for l in list_lessons()]
    return {
        "materials": _materials(),
        "lessons": lessons,
        "singles": _single_files(),
        "deps": _deps(),
        "lan_ip": lan_ip(),
        "port": DEFAULT_PORT,
        "pipeline_stantia": _pipeline_mtime() != _PIPELINE_MTIME_START,
        "config": {k: CONFIG.get(k) for k in
                   ("theme", "voice", "edge_voice", "edge_rate",
                    "llm_model", "num_moduli_min", "num_moduli_max",
                    "profilo_durata", "profilo_livello", "profilo_obiettivo")},
        "voices": EDGE_VOICES,
    }


EDGE_VOICES = [
    "it-IT-GiuseppeMultilingualNeural",
    "it-IT-IsabellaNeural",
    "it-IT-DiegoNeural",
    "it-IT-ElsaNeural",
]
_VOICES_CACHE = {"at": 0.0, "names": None}


def _edge_voices_live():
    """Lista voci it-IT dal servizio (cache 1h), fallback alla lista statica."""
    import time as _t
    if _VOICES_CACHE["names"] and _t.time() - _VOICES_CACHE["at"] < 3600:
        return _VOICES_CACHE["names"]
    try:
        import asyncio
        import edge_tts

        async def _list():
            return await edge_tts.list_voices()

        vs = asyncio.run(_list())
        names = sorted(v["ShortName"] for v in vs
                       if str(v.get("Locale", "")).startswith("it"))
        if names:
            _VOICES_CACHE.update(at=_t.time(), names=names)
            return names
    except Exception:
        pass
    return list(EDGE_VOICES)


def _loopback(handler):
    return handler.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


# ------------------------------------------------------------------ handler
class PanelHandler(_RangeHandler):
    """Serve: pannello + API (solo localhost) + lezioni (tutti, whitelist)."""

    # -- API ------------------------------------------------------------------
    def _api(self, path, query):
        if path == "/api/classifica":
            return self._json(_classifica_view())
        if path == "/api/classifica_export":
            return self._classifica_export(query)
        if not _loopback(self):
            self.send_error(403, "API riservate a localhost")
            return
        if path == "/api/state":
            return self._json(_state())
        if path == "/api/log":
            elapsed = (time.time() - JOB["started_at"]) if JOB.get("started_at") and JOB["running"] else None
            return self._json({"running": JOB["running"], "kind": JOB["kind"],
                               "source": JOB["source"], "error": JOB["error"],
                               "done_at": JOB["done_at"], "ok": JOB["ok"],
                               "elapsed": round(elapsed, 1) if elapsed else None,
                               "queued": len(QUEUE),
                               "queue": [s for _, _, s in list(QUEUE)],
                               "lines": list(LOG)[-200:]})
        if path == "/api/build":
            return self._start(self._parse_build())
        if path == "/api/reaudio":
            return self._start_reaudio(query)
        if path == "/api/export_single":
            return self._export_single(query)
        if path == "/api/export_scorm":
            return self._export_scorm(query)
        if path == "/api/handout":
            return self._handout(query)
        if path == "/api/progress":
            return self._progress()
        if path == "/api/history":
            return self._history()
        if path == "/api/lan":
            return self._json({"lan_ip": lan_ip(), "port": DEFAULT_PORT,
                               "url": f"http://{lan_ip()}:{DEFAULT_PORT}/" if lan_ip() else None})
        if path == "/api/voices":
            return self._json({"voices": _edge_voices_live(),
                               "current": CONFIG.get("edge_voice")})
        if path == "/api/lesson_data":
            return self._lesson_data(query)
            return self._lesson_data(query)
        self.send_error(404, "API sconosciuta")

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2 * 1024 * 1024:
            raise ValueError("Corpo troppo grande")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _resolve_source(self, src):
        """Solo file caricati via upload in questa sessione (no scansione
        cartella) oppure URL. Niente path traversal."""
        if is_url(src):
            return src
        name = Path(urllib.parse.unquote(src)).name
        if name not in UPLOADED:
            raise ValueError(f"Materiale non caricato via pannello: {src} "
                             "(usa Trascina/Sfoglia qui sopra)")
        p = (BASE / name).resolve()
        if p.parent != BASE or not p.is_file() or p.suffix.lower() not in SUPPORTED_EXT:
            raise ValueError(f"Materiale non valido: {src}")
        return str(p)

    def _parse_build(self):
        from common import normalize_profilo
        data = self._read_json_body()
        src = self._resolve_source(str(data.get("source") or ""))
        force = bool(data.get("force"))
        bozza = bool(data.get("bozza"))
        single = bool(data.get("single"))
        profilo = normalize_profilo(data.get("profilo") or {
            "durata": data.get("durata"), "livello": data.get("livello"),
            "obiettivo": data.get("obiettivo")})
        return src, force, bozza, single, profilo

    def _start(self, parsed):
        import new_lesson
        src, force, bozza, single, profilo = parsed
        started, queued = start_job(
            lambda: new_lesson.build_from_docx(src, force=force, bozza=bozza,
                                               single=single, profilo=profilo),
            "generazione", Path(src).name if not is_url(src) else src)
        if not started:
            self._json({"started": False, "reason": "Coda piena (5 job): attendi la fine."}, 409)
            return
        self._json({"started": True, "queued": queued})

    def _start_text(self):
        import new_lesson
        from common import normalize_profilo
        data = self._read_json_body()
        text = str(data.get("text") or "")
        title = str(data.get("title") or "Materiale incollato")[:80]
        if len(text.strip()) < 50:
            raise ValueError("Testo troppo corto (min 50 caratteri).")
        force = bool(data.get("force"))
        bozza = bool(data.get("bozza"))
        single = bool(data.get("single"))
        profilo = normalize_profilo(data.get("profilo") or {})
        started, queued = start_job(
            lambda: new_lesson.build_from_text(text, title=title, force=force,
                                               bozza=bozza, single=single, profilo=profilo),
            "testo incollato", title)
        if not started:
            self._json({"started": False, "reason": "Coda piena (5 job): attendi la fine."}, 409)
            return
        self._json({"started": True, "queued": queued})

    def _move_slide(self):
        data = self._read_json_body()
        import new_lesson
        lesson = self._check_lesson(str(data.get("lesson") or ""))
        n = new_lesson.move_slide(str(lesson), int(data.get("from", -1)), int(data.get("to", -1)))
        _invalidate_lessons_cache()
        self._json({"ok": True, "slides": n})

    def _add_slide(self):
        data = self._read_json_body()
        import new_lesson
        lesson = self._check_lesson(str(data.get("lesson") or ""))
        pos = new_lesson.add_slide(str(lesson), data.get("title"), data.get("narration"))
        _invalidate_lessons_cache()
        self._json({"ok": True, "pos": pos})

    def _delete_slide(self):
        data = self._read_json_body()
        import new_lesson
        lesson = self._check_lesson(str(data.get("lesson") or ""))
        n = new_lesson.delete_slide(str(lesson), int(data.get("index", -1)))
        _invalidate_lessons_cache()
        self._json({"ok": True, "slides": n})

    def _start_reaudio(self, query):
        name = query.get("lesson", [None])[0] or ""
        lesson = BASE / name
        if not name or not lesson.is_dir() or not (lesson / "index.html").exists():
            self.send_error(400, "Lezione non trovata")
            return
        import new_lesson
        started, queued = start_job(lambda: new_lesson.regen_audio_lesson(str(lesson)),
                       "rigenerazione audio", name)
        if not started:
            self._json({"started": False, "reason": "Coda piena (5 job): attendi la fine."}, 409)
            return
        self._json({"started": True, "queued": queued})

    def _export_single(self, query):
        name = query.get("lesson", [None])[0] or ""
        lesson = BASE / name
        if not name or not lesson.is_dir() or not (lesson / "index.html").exists():
            self.send_error(400, "Lezione non trovata")
            return
        try:
            from export_single import export_single
            p = export_single(lesson)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)
            return
        self._json({"url": f"/{lesson.name}/{p.name}",
                    "size": p.stat().st_size})

    def _progress(self):
        try:
            data = json.loads((BASE / ".progress.json").read_text(encoding="utf-8"))
        except Exception:
            data = {"fase": "inattiva", "pct": 0}
        data["job_running"] = JOB["running"]
        return self._json(data)

    def _history(self):
        try:
            hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8") or "[]")
        except Exception:
            hist = []
        return self._json({"history": hist[-20:]})

    def _export_scorm(self, query):
        name = query.get("lesson", [None])[0] or ""
        lesson = BASE / name
        if not name or not lesson.is_dir() or not (lesson / "index.html").exists():
            self.send_error(400, "Lezione non trovata")
            return
        try:
            from export_scorm import export_scorm
            p = export_scorm(lesson)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)
            return
        self._json({"url": f"/{lesson.name}/{p.name}", "size": p.stat().st_size})

    def _handout(self, query):
        name = query.get("lesson", [None])[0] or ""
        lesson = BASE / name
        if not name or not lesson.is_dir() or not (lesson / "index.html").exists():
            self.send_error(400, "Lezione non trovata")
            return
        try:
            from export_handout import export_handout
            p = export_handout(lesson)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)
            return
        self._json({"url": f"/{p.name}", "size": p.stat().st_size})

    def _check_lesson(self, name):
        # anti-traversal: rigetta .., /, \, null byte e nomi non whitelistati
        if not name or "\x00" in name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("Lezione non valida")
        if name not in _allowed_lesson_names() and name not in _allowed_single_names():
            # fallback strict: verifica comunque il path risolto
            lesson = (BASE / name).resolve()
            try:
                lesson.relative_to(BASE.resolve())
            except Exception:
                raise ValueError("Lezione non valida")
            if not lesson.is_dir() or not (lesson / "index.html").exists():
                raise ValueError("Lezione non trovata")
            return lesson
        lesson = BASE / name
        if not lesson.is_dir() or not (lesson / "index.html").exists():
            raise ValueError("Lezione non trovata")
        try:
            # double-check che non sia symlink fuori da BASE
            lesson.resolve().relative_to(BASE.resolve())
            if lesson.resolve().parent != BASE.resolve():
                raise ValueError("Lezione non valida")
        except ValueError:
            raise
        except Exception:
            raise ValueError("Lezione non valida")
        return lesson

    def _lesson_data(self, query):
        name = query.get("lesson", [None])[0] or ""
        try:
            lesson = self._check_lesson(name)
            import new_lesson
            _, payload = new_lesson.load_lesson(str(lesson))
            slides = []
            for i, s in enumerate(payload.get("slides", [])):
                q = None
                for b in s.get("blocks", []):
                    if "quiz" in b:
                        q = b["quiz"]
                        break
                slides.append({"index": i, "title": s.get("title", ""),
                               "narration": s.get("narration", ""),
                               "duration": s.get("duration", 0),
                               "quiz": q})
            self._json({"lesson": lesson.name, "titolo": payload.get("titolo"),
                        "profilo": payload.get("profilo"), "slides": slides})
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 400)

    def _save_slide(self):
        data = self._read_json_body()
        lesson = self._check_lesson(str(data.get("lesson") or ""))
        index = int(data.get("index", -1))
        patch = data.get("patch") or {}
        import new_lesson
        out, payload = new_lesson.load_lesson(str(lesson))
        slides = payload["slides"]
        if not (0 <= index < len(slides)):
            raise ValueError("Indice slide fuori range")
        clean = new_lesson.validate_slide_edit(index, patch)
        s = slides[index]
        narration_changed = False
        if "title" in clean:
            s["title"] = clean["title"]
            # aggiorna anche l'h1 del blocco se presente
            for b in s.get("blocks", []):
                if "h1" in b:
                    b["h1"] = clean["title"]
                    break
        if "narration" in clean and clean["narration"] != s.get("narration"):
            s["narration"] = clean["narration"]
            narration_changed = True
        if "quiz" in clean:
            for b in s.get("blocks", []):
                if "quiz" in b:
                    if clean["quiz"] is None:
                        s["blocks"].remove(b)
                    else:
                        q = clean["quiz"]
                        b["quiz"] = {"q": q["domanda"],
                                     "opts": [{"t": o["testo"], "ok": o["corretta"], "fb": ""}
                                              for o in q["opzioni"]],
                                     "ok": "Esatto!", "ko": "Rileggi e riprova."}
                    break
            else:
                if clean["quiz"] is not None:
                    q = clean["quiz"]
                    s.setdefault("blocks", []).append(
                        {"quiz": {"q": q["domanda"],
                                  "opts": [{"t": o["testo"], "ok": o["corretta"], "fb": ""}
                                           for o in q["opzioni"]],
                                  "ok": "Esatto!", "ko": "Rileggi e riprova."}})
        new_lesson.save_lesson(str(out), payload)
        _invalidate_lessons_cache()
        self._json({"ok": True, "narration_changed": narration_changed,
                    "warning": ("Testo narrazione modificato: l'audio è invariato. "
                                "Usa «Rigenera audio slide» per riallinearlo."
                                if narration_changed else "")})

    def _reaudio_slide(self, query):
        name = query.get("lesson", [None])[0] or ""
        try:
            index = int(query.get("index", [-1])[0])
        except Exception:
            index = -1
        lesson = self._check_lesson(name)
        ok = start_job(lambda: self._do_reaudio_slide(str(lesson), index),
                       "audio slide", f"{lesson.name}#{index}")
        if not ok[0]:
            self._json({"started": False, "reason": "Coda piena."}, 409)
            return
        self._json({"started": True, "queued": ok[1]})

    @staticmethod
    def _do_reaudio_slide(lesson, index):
        import new_lesson
        dur, engine = new_lesson.regen_slide_audio(lesson, index)
        _log(f"✔ slide {index + 1} audio rigenerato [{engine}] {dur:.1f}s")
        _invalidate_lessons_cache()

    def _tts_preview(self):
        data = self._read_json_body()
        text = str(data.get("text") or "").strip()[:500]
        if not text:
            raise ValueError("Testo vuoto (max 500 caratteri)")
        voice = str(data.get("voice") or CONFIG.get("edge_voice"))
        if voice not in _edge_voices_live() and voice not in EDGE_VOICES:
            raise ValueError(f"Voce non supportata ({voice or 'vuota'}): "
                             "selezionane una dal menu e ricarica la pagina se è vuoto")
        rate = str(data.get("rate") or CONFIG.get("edge_rate", "-4%"))
        if not re.fullmatch(r"-?\d+%", rate):
            raise ValueError("Rate non valido (es. -4%)")
        try:
            import asyncio
            import edge_tts
            mp3 = bytearray()

            async def _run():
                comm = edge_tts.Communicate(text, voice, rate=rate)
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        mp3.extend(chunk["data"])
                    if len(mp3) > 2 * 1024 * 1024:
                        break

            asyncio.run(_run())
        except Exception as e:  # noqa: BLE001
            cause = str(e)[:150] or type(e).__name__
            _log(f"✗ anteprima voce fallita [{voice}]: {cause}")
            _flog(f"tts_preview 502 | [{voice}] {rate} | {cause}")
            body = json.dumps({"ok": False,
                               "error": f"Sintesi vocale fallita: {cause}. "
                                        "Controlla la connessione verso Microsoft."},
                              ensure_ascii=False).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if len(mp3) < 1000:
            _log("✗ anteprima voce: audio vuoto restituito dal servizio")
            self._json({"ok": False, "error": "Sintesi fallita: audio vuoto, riprova."}, 502)
            return
        body = bytes(mp3)
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- upload materiale -------------------------------------------------------
    def _read_limited(self, length):
        """Lettura a chunk 64KB con limite: evita un singolo read() enorme."""
        buf = bytearray()
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            buf.extend(chunk)
            remaining -= len(chunk)
            if len(buf) > MAX_UPLOAD_BYTES:
                raise _UploadTooBig(
                    f"File troppo grande: supera il limite di {MAX_UPLOAD_MB} MB.")
        return bytes(buf)

    def _parse_upload_body(self):
        """Estrae (filename, contenuto) dalla parte 'file' di un multipart
        form-data (quello che invia il pannello). Solleva ValueError se il
        corpo non è valido, _UploadTooBig se supera MAX_UPLOAD_MB."""
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', ctype)
        if not m:
            raise ValueError("Richiesta non multipart (manca il boundary).")
        boundary = (m.group(1) or m.group(2)).encode("utf-8")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("Richiesta senza corpo da caricare.")
        if length > MAX_UPLOAD_BYTES:
            raise _UploadTooBig(
                f"File troppo grande: supera il limite di {MAX_UPLOAD_MB} MB.")
        body = self._read_limited(length)
        sep = b"--" + boundary
        saw_file_part = False
        for part in body.split(sep):
            part = part.strip(b"\r\n")
            if not part or part == b"--":
                continue
            head_end = part.find(b"\r\n\r\n")
            if head_end < 0:
                # Parte file senza contenuto (file vuoto: il separatore finale
                # viene mangiato dallo strip) — segnalalo invece di "nessun file"
                if 'name="file"' in part.decode("utf-8", "replace"):
                    saw_file_part = True
                continue
            headers = part[:head_end].decode("utf-8", "replace")
            mf = re.search(r'filename="([^"]*)"', headers)
            fname = mf.group(1) if mf else None
            if fname is None:
                # RFC 5987: filename*=utf-8''nome%20file.docx (nomi non-ASCII)
                m2 = re.search(r"filename\*\s*=\s*[^']*''([^;\s]+)", headers)
                if m2:
                    try:
                        fname = urllib.parse.unquote(m2.group(1))
                    except Exception:
                        fname = None
            if fname is None or 'name="file"' not in headers:
                continue
            return fname, part[head_end + 4:]
        if saw_file_part:
            raise ValueError("Il file ricevuto è vuoto (0 byte).")
        raise ValueError("Nessun file ricevuto nella richiesta.")

    def _upload(self):
        raw_name, data = self._parse_upload_body()
        safe = Path(raw_name or "").name.strip()   # niente percorsi, solo nome
        if not safe:
            raise ValueError("Nome file non valido.")
        if Path(safe).suffix.lower() not in SUPPORTED_EXT:
            raise ValueError(
                f"Formato non supportato ({Path(safe).suffix or 'nessuna estensione'}): "
                "usa .docx, .pdf, .txt, .md, .html.")
        if not data:
            raise ValueError("Il file ricevuto è vuoto.")
        # Scrittura atomica: mai file troncati se il server viene killato.
        # Su Windows il file esistente può essere bloccato in transito
        # (antivirus, indicizzazione, OneDrive) o in uso (PDF/Word aperto):
        # retry con backoff, poi fallback "come copia", solo alla fine errore.
        import os as _os
        import stat as _stat
        import time as _t
        dest = BASE / safe
        replaced = dest.exists()
        final_name = safe
        import tempfile as _tf
        fd, tmp_path = _tf.mkstemp(dir=str(BASE), prefix=".upload-", suffix=".tmp")
        try:
            _os.write(fd, data)
        finally:
            _os.close(fd)
        tmp = Path(tmp_path)
        saved = False
        try:
            try:
                _os.chmod(dest, _stat.S_IWRITE)
            except OSError:
                pass
            for _attempt in range(6):
                try:
                    _os.replace(tmp, dest)
                    saved = True
                    break
                except PermissionError:
                    if _attempt < 5:
                        _t.sleep(0.5)
            if not saved:
                # Fallback: salva come copia (stem (2).ext, …) invece di fallire
                stem, ext = dest.stem, dest.suffix
                for _n in range(2, 12):
                    cand = BASE / f"{stem} ({_n}){ext}"
                    if cand.exists():
                        continue
                    try:
                        _os.replace(tmp, cand)
                    except PermissionError:
                        continue
                    dest, final_name = cand, cand.name
                    replaced, saved = False, True
                    break
            if not saved:
                raise ValueError(
                    f"«{safe}» è bloccato da un altro programma (PDF/Word aperto?) "
                    "e non riesco a salvarlo nemmeno come copia: chiudilo e "
                    "riprova, oppure rinomina il file prima di caricarlo.")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        _register_upload(final_name)
        _invalidate_lessons_cache()   # il nuovo materiale può generare una lezione
        _log(f"✔ materiale caricato dal pannello: {final_name}"
             + (" (sostituito)" if replaced else "")
             + (f" (copia: {safe} bloccato)" if final_name != safe else ""))
        self._json({"ok": True, "name": final_name, "replaced": replaced,
                    "size": len(data), "copy": final_name != safe})

    # -- GET ------------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if path.startswith("/api/"):
            return self._api(path, query)
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path in ("/", "/index.html"):
            body = PANEL_HTML.encode("utf-8") if _loopback(self) else _hub_page_cached().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # anti-traversal: decodifica, blocca .., \ e verifica path risolto dentro BASE
        try:
            dec = urllib.parse.unquote(path)
        except Exception:
            self.send_error(404, "Non disponibile")
            return
        if "\x00" in dec or "\\" in dec:
            self.send_error(404, "Non disponibile")
            return
        parts = [p for p in dec.strip("/").split("/") if p]
        if parts and ".." in parts:
            self.send_error(404, "Non disponibile")
            return
        first = parts[0] if parts else ""
        if first and first not in _allowed_lesson_names() and first not in _allowed_single_names():
            self.send_error(404, "Non disponibile")
            return
        if parts:
            try:
                target = (BASE / "/".join(parts)).resolve()
                # deve restare dentro BASE
                target.relative_to(BASE.resolve())
                # se first è una lezione, deve restare dentro BASE/first
                if first in _allowed_lesson_names():
                    target.relative_to((BASE / first).resolve())
            except Exception:
                self.send_error(404, "Non disponibile")
                return
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/classifica":
            # UNA SOLA API aperta alla LAN: gli studenti inviano il risultato
            # del percorso (nessun dato sensibile, valori sanitized e limitati)
            return self._classifica_post()
        if not _loopback(self):
            self.send_error(403, "API riservate a localhost")
            return
        if parsed.path == "/api/build":
            if not _rate_ok(self.client_address[0]):
                self._json({"started": False, "reason": "Troppe richieste: riprova tra un po'."}, 429)
                return
            try:
                self._start(self._parse_build())
            except Exception as e:  # noqa: BLE001
                self._json({"started": False, "reason": str(e)}, 400)
        elif parsed.path == "/api/build_text":
            if not _rate_ok(self.client_address[0]):
                self._json({"started": False, "reason": "Troppe richieste: riprova tra un po'."}, 429)
                return
            try:
                self._start_text()
            except Exception as e:  # noqa: BLE001
                self._json({"started": False, "reason": str(e)}, 400)
        elif parsed.path == "/api/move_slide":
            try:
                self._move_slide()
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 400)
        elif parsed.path == "/api/add_slide":
            try:
                self._add_slide()
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 400)
        elif parsed.path == "/api/delete_slide":
            try:
                self._delete_slide()
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 400)
        elif parsed.path == "/api/reaudio":
            try:
                self._start_reaudio(urllib.parse.parse_qs(parsed.query))
            except Exception as e:  # noqa: BLE001
                self._json({"started": False, "reason": str(e)}, 400)
        elif parsed.path == "/api/upload":
            try:
                self._upload()
            except _UploadTooBig as e:
                _log(f"✗ upload rifiutato (troppo grande): {e}")
                _flog(f"upload 413 | {e} | len={self.headers.get('Content-Length')} "
                      f"| ctype={self.headers.get('Content-Type', '')[:100]}")
                self._json({"ok": False, "error": str(e)}, 413)
            except Exception as e:  # noqa: BLE001
                _log(f"✗ upload fallito: {e}")
                _flog(f"upload 400 | {e} | len={self.headers.get('Content-Length')} "
                      f"| ctype={self.headers.get('Content-Type', '')[:100]}")
                self._json({"ok": False, "error": str(e)}, 400)
        elif parsed.path == "/api/cancel_queue":
            n = cancel_queue()
            self._json({"ok": True, "removed": n})
        elif parsed.path == "/api/save_slide":
            try:
                self._save_slide()
            except Exception as e:  # noqa: BLE001
                _flog(f"save_slide 400 | {e}")
                self._json({"ok": False, "error": str(e)}, 400)
        elif parsed.path == "/api/reaudio_slide":
            try:
                self._reaudio_slide(urllib.parse.parse_qs(parsed.query))
            except Exception as e:  # noqa: BLE001
                self._json({"started": False, "reason": str(e)}, 400)
        elif parsed.path == "/api/tts_preview":
            try:
                self._tts_preview()
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 400)
        else:
            self.send_error(404, "API sconosciuta")

    def _classifica_post(self):
        try:
            d = self._read_json_body()
            lesson = self._check_lesson(str(d.get("lesson") or ""))
            studente = re.sub(r"\s+", " ", str(d.get("studente") or "Anonimo")).strip()[:40] \
                or "Anonimo"
            punti = max(0, min(999, int(d.get("punti") or 0)))
            totale = max(0, min(999, int(d.get("totale") or 0)))
            tempo_min = max(0, min(600, int(d.get("tempo_min") or 0)))
            if totale <= 0:
                raise ValueError("Nessuna attività registrata")
            _classifica_add(lesson.name, studente, punti, totale,
                            bool(d.get("completata")), tempo_min)
            self._json({"ok": True})
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": str(e)}, 400)

    def _classifica_export(self, query):
        """CSV della classifica di una lezione (stesso ordine della vista)."""
        name = query.get("lesson", [None])[0] or ""
        try:
            lesson = self._check_lesson(name)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 400)
        view = _classifica_view()
        entry = next((c for c in view["classifiche"] if c["lesson"] == lesson.name), None)
        rows = entry["rows"] if entry else []
        lines = ["posizione;studente;punti;percentuale;completata;tempo_min;quando"]
        for i, r in enumerate(rows):
            lines.append(";".join(str(x) for x in (
                i + 1, r.get("studente", ""), f'{r.get("punti", 0)}/{r.get("totale", 0)}',
                f'{r.get("pct", 0)}%', "si" if r.get("completata") else "no",
                r.get("tempo_min", 0), r.get("t", ""))))
        body = ("\ufeff" + "\n".join(lines)).encode("utf-8")
        fname = f'classifica-{lesson.name.replace("_lesson", "")}.csv'
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ------------------------------------------------------------------ UI
PANEL_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pannello di controllo — Generatore lezioni</title>
<style>
:root{--bg:#0d1220;--card:#161d33;--line:#2a3554;--txt:#eef2ff;--mut:#93a0c4;
--acc:#5b7bd5;--ok:#3ecf8e;--err:#ff6b6b;--warn:#ffd166}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--txt);
margin:0;padding:26px 18px 60px}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:16px 18px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 12px;color:#c8d4f5;display:flex;align-items:center;gap:10px}
.step{flex:0 0 auto;width:26px;height:26px;border-radius:50%;font-size:13px;font-weight:800;
display:inline-flex;align-items:center;justify-content:center;color:#fff;
background:linear-gradient(135deg,var(--acc),#8a6ff0);box-shadow:0 2px 10px rgba(91,123,213,.5)}
button.primary{background:linear-gradient(135deg,var(--acc),#8a6ff0);font-size:14px;padding:10px 22px;
box-shadow:0 4px 16px rgba(91,123,213,.45)}
button.primary:hover{filter:brightness(1.12);transform:translateY(-1px)}
details.adv{margin-top:12px;font-size:13px;color:var(--mut)}
details.adv summary{cursor:pointer;padding:6px 0;user-select:none}
details.adv summary:hover{color:var(--txt)}
details.adv .opts{margin-top:6px}
.urlrow{flex-wrap:wrap}
#lanQr{width:140px;height:140px;border-radius:12px;border:1px solid var(--line);background:#fff;padding:6px}
.lanrow{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.lanrow .grow{flex:1;min-width:200px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{padding:5px 11px;border-radius:20px;font-size:12px;border:1px solid var(--line);
background:#10172a;color:var(--mut)}
.chip.ok{color:var(--ok);border-color:#1e4b3a}
.chip.no{color:var(--err);border-color:#5b2b2b}
.row{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid #1d2742;
flex-wrap:wrap}
.row:last-child{border-bottom:none}
.name{font-weight:700;flex:1;min-width:160px;word-break:break-all}
.meta{color:var(--mut);font-size:12px;min-width:110px}
.badge{font-size:11px;padding:3px 9px;border-radius:12px;background:#233054;color:#b7c6ee}
.badge.exists{background:#1e3b2e;color:var(--ok)}
.badge.link{background:#3b2f1e;color:var(--warn)}
button{background:var(--acc);color:#fff;border:none;border-radius:9px;padding:8px 15px;
font-weight:700;cursor:pointer;font-size:13px;transition:filter .15s}
button:hover{filter:brightness(1.12)}
button:disabled{opacity:.45;cursor:not-allowed}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--mut)}
button.ghost:hover{color:var(--txt);border-color:var(--acc)}
button.mini{padding:5px 11px;font-size:12px}
.urlrow{display:flex;gap:8px}
.urlrow input{flex:1;background:#0d1220;border:1px solid var(--line);color:var(--txt);
border-radius:9px;padding:9px 12px;font-size:13px}
.opts{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px;font-size:13px;color:var(--mut)}
.opts label{display:flex;gap:6px;align-items:center;cursor:pointer}
pre{background:#0a0f1c;border:1px solid var(--line);border-radius:10px;padding:12px;
height:160px;overflow:auto;font:12px/1.55 Consolas,'Cascadia Mono',monospace;margin:0;
white-space:pre-wrap;word-break:break-word}
#lan{color:var(--warn)}
a.apri{text-decoration:none;color:#8ecaff;font-weight:700}
.empty{color:var(--mut);font-size:13px;padding:8px 0}
.upzone{border:2px dashed var(--line);border-radius:14px;padding:26px 22px;
margin:0 0 14px;color:var(--mut);font-size:14px;cursor:pointer;user-select:none;text-align:center;
transition:border-color .2s,background .2s}
.upzone:hover,.upzone.drag{border-color:var(--acc);background:#18233f;color:#c8d8ff}
.upzone .big{font-size:15px;font-weight:700;color:var(--txt);display:block;margin-bottom:4px}
.upzone .sub2{font-size:12.5px}
#uprow{justify-content:center;margin-top:12px}
.uprow{display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap}
.upmsg{font-size:12px;margin:6px 0 0}
.upmsg.ok{color:var(--ok)}.upmsg.err{color:var(--err)}
</style>
</head>
<body>
<div class="wrap">
  <h1>🎛 Pannello di controllo — Generatore lezioni</h1>
  <div class="sub" id="statusline">…</div>
  <div id="stale" style="display:none;margin:10px 0;padding:10px 14px;border:1px solid #ffb020;
       background:#2a2205;color:#ffd27a;border-radius:8px;font-size:14px">
    ⚠ <b>Il codice di generazione è cambiato</b> da quando il pannello è stato avviato:
    le generazioni userebbero il codice vecchio. Chiudi il pannello e riavvialo
    (<b>AVVIA.bat</b>) per attivare gli aggiornamenti.
  </div>

  <div class="card">
    <h2>Ambiente</h2>
    <div class="chips" id="deps"></div>
  </div>

  <div class="card">
    <h2><span class="step">1</span> Carica il materiale e genera</h2>
    <div class="upzone" id="upzone" role="button" tabindex="0"
         title="Carica un file (trascinalo qui sopra o clicca per sceglierlo)">
      <input type="file" id="upfile" accept=".docx,.pdf,.txt,.md,.html,.htm" hidden>
      <span class="big">📄 Trascina qui il materiale</span>
      <span class="sub2" id="uptxt">.docx, .pdf, .txt, .md, .html — oppure clicca per sceglierlo (max 100 MB)</span>
      <div class="uprow" id="uprow" hidden>
        <span class="name" id="upname"></span>
        <span class="meta" id="upsize"></span>
        <button class="primary" id="btnUpGen" type="button">Carica e genera</button>
        <button class="mini ghost" id="btnUp" type="button" title="Solo carica, senza generare">Solo carica</button>
        <button class="mini ghost" id="btnUpX" type="button" title="Annulla">✕</button>
      </div>
      <div class="upmsg" id="upmsg" hidden></div>
    </div>
    <div id="materials"></div>
    <div class="opts">
      <label>Durata <select id="profDurata">
        <option value="breve">Breve (3-4 moduli)</option>
        <option value="standard" selected>Standard (4-7)</option>
        <option value="approfondita">Approfondita (6-8)</option>
      </select></label>
      <label>Livello <select id="profLivello">
        <option value="base">Base</option>
        <option value="intermedio" selected>Intermedio</option>
        <option value="avanzato">Avanzato</option>
      </select></label>
      <label>Obiettivo <select id="profObiettivo">
        <option value="conoscenza">Conoscenza</option>
        <option value="comprensione" selected>Comprensione</option>
        <option value="applicazione">Applicazione</option>
        <option value="analisi">Analisi</option>
      </select></label>
    </div>
    <details class="adv">
      <summary>⚙ Opzioni avanzate (rigenera, bozza, file unico)</summary>
      <div class="opts">
        <label><input type="checkbox" id="forceAll"> Rigenera anche le lezioni già esistenti</label>
        <label><input type="checkbox" id="bozzaAll"> Bozza veloce senza IA (solo struttura dal testo)</label>
        <label><input type="checkbox" id="singleAll"> Genera come file HTML unico, senza cartella</label>
      </div>
    </details>
  </div>

  <div class="card">
    <h2>Voce — anteprima</h2>
    <div class="urlrow">
      <select id="voiceSel" style="max-width:320px;background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px"></select>
      <select id="rateSel" style="background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px">
        <option value="-10%">Lenta -10%</option>
        <option value="-4%" selected>Normale -4%</option>
        <option value="+0%">+0%</option>
        <option value="+10%">Veloce +10%</option>
      </select>
      <button id="btnVoice" type="button">Prova voce</button>
    </div>
    <div class="urlrow" style="margin-top:8px">
      <input id="voiceTxt" value="Ciao! Questa è un'anteprima della voce per le lezioni." maxlength="500">
    </div>
    <audio id="voiceAudio" controls style="width:100%;margin-top:8px" hidden></audio>
    <div class="upmsg" id="voiceMsg" hidden></div>
  </div>

  <div class="card">
    <h2><span class="step">2</span> Oppure genera da un link</h2>
    <div class="urlrow">
      <input id="url" placeholder="https://it.wikipedia.org/wiki/…  oppure  https://www.youtube.com/watch?v=…">
      <button id="btnUrl" class="primary">Genera da link</button>
    </div>
  </div>

  <div class="card">
    <h2><span class="step">2</span> Oppure incolla il testo <span style="color:var(--mut);font-weight:400;font-size:12px">(appunti, dispense)</span></h2>
    <div class="urlrow"><input id="txtTitle" placeholder="Titolo lezione (es. Il sistema solare)" maxlength="80"></div>
    <div class="urlrow" style="margin-top:8px">
      <textarea id="txtBody" rows="4" style="flex:1;background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px"
        placeholder="Incolla qui il testo (min 50 caratteri)…"></textarea>
    </div>
    <div class="uprow"><button class="primary" id="btnText" type="button">Genera da testo</button></div>
  </div>

  <div class="card">
    <h2><span class="step">3</span> Lezioni generate</h2>
    <div id="lessons"></div>
    <h2 style="margin-top:16px">File unici (HTML singolo)</h2>
    <div id="singles"></div>
  </div>

  <div class="card">
    <h2><span class="step">🏆</span> Classifica di classe</h2>
    <div class="urlrow">
      <select id="claSel" onchange="loadClassifica()" style="flex:1;background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px"></select>
      <button class="mini ghost" onclick="loadClassifica()" type="button">🔄 Aggiorna</button>
      <button class="mini ghost" onclick="exportClassifica()" type="button">⬇ CSV</button>
    </div>
    <div class="urlrow" style="margin-top:8px">
      <input id="claAddName" placeholder="Nome studente (per aggiungere a mano un risultato)">
      <input id="claAddPts" placeholder="Punti (es. 7/10)" style="max-width:140px">
      <button class="mini" onclick="addManuale()" type="button">Aggiungi</button>
    </div>
    <div id="classifica" style="margin-top:10px"><span class="empty">Nessun risultato ancora: gli studenti lo inviano dal pulsante nel pannello finale della lezione.</span></div>
  </div>

  <div class="card" id="editor" hidden>
    <h2>Modifica slide <span id="edMeta" style="color:var(--mut);font-weight:400;font-size:12px"></span></h2>
    <div class="urlrow">
      <select id="edSel" onchange="ED.idx=+this.value;renderEditor()" style="flex:1;background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px"></select>
      <button class="mini ghost" onclick="document.querySelector('#editor').hidden=true" type="button">Chiudi</button>
    </div>
    <div class="urlrow" style="margin-top:8px"><input id="edTitle" placeholder="Titolo slide" maxlength="200"></div>
    <div class="urlrow" style="margin-top:8px"><input id="edNarr" placeholder="Narrazione (voce legge questo testo)" maxlength="3000"></div>
    <div class="urlrow" style="margin-top:8px"><input id="edQuizQ" placeholder="Domanda quiz (vuoto = nessun quiz)"></div>
    <div class="urlrow" style="margin-top:8px"><input id="edQuizOpts" placeholder="Opzioni, una per riga — * davanti = corretta (es. * Roma)"></div>
    <div class="uprow">
      <button class="mini" onclick="saveEditor(false)" type="button">Salva testo/quiz</button>
      <button class="mini" onclick="saveEditor(true)" type="button" title="Salva e rigenera l'audio di questa slide">Salva + rigenera audio</button>
      <button class="mini ghost" onclick="moveEditor(-1)" type="button" title="Sposta slide indietro">← Sposta</button>
      <button class="mini ghost" onclick="moveEditor(1)" type="button" title="Sposta slide avanti">Sposta →</button>
      <button class="mini ghost" onclick="addEditor()" type="button" title="Aggiungi slide contenuto">+ Aggiungi</button>
      <button class="mini ghost" onclick="delEditor()" type="button" title="Elimina questa slide">✕ Elimina</button>
    </div>
    <div class="upmsg" id="edMsg" hidden></div>
  </div>

  <div class="card">
    <h2>Log di generazione <span id="jobinfo" style="color:var(--mut);font-weight:400;font-size:12px"></span>
      <button class="mini ghost" id="btnCancelQ" type="button" hidden>Svuota coda</button></h2>
    <div class="urlrow" style="margin-bottom:8px"><span id="progTxt" style="color:var(--warn);font-size:12px"></span></div>
    <pre id="log">Il log apparirà qui durante la generazione…</pre>
  </div>

  <div class="card">
    <h2><span class="step">4</span> Condividi in classe <span style="color:var(--mut);font-weight:400;font-size:12px">(stessa Wi-Fi)</span></h2>
    <div class="lanrow">
      <img id="lanQr" hidden alt="QR per aprire la lezione dal telefono">
      <div class="grow">
        <div class="urlrow"><input id="lanUrl" readonly placeholder="Caricamento indirizzo…">
          <button class="mini" id="btnLanCopy" type="button">📋 Copia link</button>
          <button class="mini ghost" id="btnLan" type="button" title="Ricarica indirizzo e cronologia">↻</button></div>
        <div class="upmsg" style="font-size:12px">Gli studenti vedono solo l'indice delle lezioni, non questo pannello.</div>
      </div>
    </div>
    <h2 style="margin-top:14px">Cronologia generazioni</h2>
    <div id="hist" style="margin-top:8px;font-size:12px;color:var(--mut)"><div>Nessun job ancora.</div></div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
let busy = false;
const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const escAttr = s => esc(s).replace(/`/g,'&#96;');

async function api(path, opts) {
  const r = await fetch('/api/' + path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok && j.reason) throw new Error(j.reason);
  return j;
}

function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

function depChips(d) {
  const map = [
    ['python_docx', 'python-docx (.docx)'],
    ['edge_tts', 'edge-tts (voce)'],
    ['pypdf', 'pypdf (.pdf)'],
    ['youtube_transcript_api', 'youtube-transcript-api (YouTube)'],
    ['ffmpeg', 'ffmpeg (audio)'],
  ];
  return map.map(([k, label]) =>
    `<span class="chip ${d[k] ? 'ok' : 'no'}">${d[k] ? '✓' : '✗'} ${label}</span>`).join('');
}

// ---------------------------------------------------------------- classifica di classe
async function loadClassifica() {
  const box = $('#classifica');
  if (!box) return;
  try {
    const r = await fetch('/api/classifica');
    const j = await r.json();
    const lesson = $('#claSel').value;
    const entry = (j.classifiche || []).find(c => c.lesson === lesson);
    if (!lesson || !entry || !entry.rows.length) {
      box.innerHTML = '<span class="empty">Nessun risultato per questa lezione (o nessuna lezione scelta).</span>';
      return;
    }
    const medal = i => i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : (i + 1);
    box.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:13.5px">'
      + '<tr style="color:var(--mut);text-align:left"><th style="padding:6px 8px">#</th><th>Studente</th><th>Punti</th><th>%</th><th>Minuti</th><th>Quando</th></tr>'
      + entry.rows.map((r2, i) => `<tr style="border-top:1px solid var(--line)">
        <td style="padding:6px 8px">${medal(i)}</td>
        <td style="font-weight:700">${esc(r2.studente)}${r2.completata ? ' <span style="color:var(--ok)">✓</span>' : ''}</td>
        <td>${r2.punti}/${r2.totale}</td><td>${r2.pct}%</td><td>${r2.tempo_min}</td><td style="color:var(--mut)">${esc(r2.t)}</td>
      </tr>`).join('') + '</table>';
  } catch (e) {
    box.innerHTML = '<span class="empty">Errore: ' + esc(e.message) + '</span>';
  }
}
function exportClassifica() {
  const lesson = $('#claSel').value;
  if (!lesson) { alert('Scegli prima una lezione.'); return; }
  const a = document.createElement('a');
  a.href = '/api/classifica_export?lesson=' + encodeURIComponent(lesson);
  a.download = 'classifica-' + lesson.replace(/_lesson$/, '') + '.csv';
  document.body.appendChild(a); a.click(); a.remove();
}
async function addManuale() {
  const lesson = $('#claSel').value;
  const nome = $('#claAddName').value.trim();
  const pm = ($('#claAddPts').value || '').trim().match(/^\s*(\d+)\s*\/\s*(\d+)\s*$/);
  if (!lesson || !nome || !pm) {
    alert('Scegli la lezione, scrivi il nome e i punti nel formato 7/10.');
    return;
  }
  const r = await api('classifica', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson, studente: nome, punti: +pm[1], totale: +pm[2], completata: true, tempo_min: 0 }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) { alert('Errore: ' + (j.error || r.status)); return; }
  $('#claAddName').value = ''; $('#claAddPts').value = '';
  loadClassifica();
}

async function refresh() {
  try {
    const s = await api('state');
    if (s.pipeline_stantia) $('#stale').style.display = 'block';
    $('#statusline').innerHTML =
      `Porta ${esc(s.port)} · server locale` + (s.lan_ip ? ` · da tablet/telefono: <span id="lan">http://${esc(s.lan_ip)}:${esc(s.port)}/</span>` : '');
    $('#deps').innerHTML = depChips(s.deps);

    const mats = s.materials;
    $('#materials').innerHTML = mats.length ? mats.map(m => {
      const exists = s.lessons.some(l => l.name === m.lesson);
      return `<div class="row">
        <span class="name">${esc(m.name)}</span>
        <span class="meta">${fmtSize(m.size)}</span>
        <span class="badge ${exists ? 'exists' : ''}">${exists ? 'lezione esistente' : 'caricato'}</span>
        <button class="mini" onclick="gen('${escAttr(m.name)}')">Genera</button>
      </div>`;
    }).join('') : '<div class="empty">Nessun file caricato: trascina qui sopra o usa Sfoglia, poi premi «Carica e genera».</div>';

    $('#lessons').innerHTML = s.lessons.length ? s.lessons.map(l =>
      `<div class="row">
        <span class="name">${esc(l.title)}</span>
        <a class="apri" href="/${escAttr(l.name)}/index.html" target="_blank">Apri →</a>
        <button class="mini ghost" onclick="openEditor('${escAttr(l.name)}')">Modifica</button>
        <button class="mini ghost" onclick="single('${escAttr(l.name)}')">HTML singolo</button>
        <button class="mini ghost" onclick="scorm('${escAttr(l.name)}')">SCORM</button>
        <button class="mini ghost" onclick="handout('${escAttr(l.name)}')">Dispensa</button>
        <button class="mini ghost" onclick="reaudio('${escAttr(l.name)}')">Rigenera audio</button>
      </div>`).join('')
      : '<div class="empty">Nessuna lezione generata ancora.</div>';

    // classifica: opzioni lezione (mantieni la selezione corrente se c'è ancora)
    const selC = $('#claSel');
    const prevC = selC.value;
    selC.innerHTML = '<option value="">— scegli lezione —</option>' +
      s.lessons.map(l => `<option value="${escAttr(l.name)}" ${l.name === prevC ? 'selected' : ''}>${esc(l.title)}</option>`).join('');
    if (prevC && s.lessons.some(l => l.name === prevC)) loadClassifica();

    const singles = s.singles || [];
    $('#singles').innerHTML = singles.length ? singles.map(f =>
      `<div class="row">
        <span class="name">${esc(f.stem)}</span>
        <span class="meta">${fmtSize(f.size)}</span>
        <a class="apri" href="/${escAttr(f.name)}" target="_blank" download="${escAttr(f.name)}">Apri / salva →</a>
      </div>`).join('')
      : '<div class="empty">Nessun file unico: spunta "Genera come file HTML unico" qui sopra la prossima volta.</div>';
  } catch (e) {
    $('#statusline').textContent = 'Errore: ' + e.message;
  }
}

function addLog(lines) {
  const el = $('#log');
  el.textContent = lines.join('\n') + (lines.length ? '\n' : '');
  el.scrollTop = el.scrollHeight;
}

async function pollLog() {
  let last = 0;
  for (;;) {
    const s = await api('log');
    const lines = s.lines.slice(last);
    last = s.lines.length;
    if (lines.length) addLog(lines);
    const info = [];
    if (s.running && s.elapsed != null) info.push(s.elapsed + 's');
    if (s.queued) info.push('coda: ' + s.queued + (s.queue && s.queue[0] ? ' (' + s.queue[0] + ')' : ''));
    $('#jobinfo').textContent = info.length ? '· ' + info.join(' · ') : '';
    $('#btnCancelQ').hidden = !s.queued;
    if (!s.running) {
      busy = false;
      document.querySelectorAll('button').forEach(b => b.disabled = false);
      if (s.error) addLog(['✗ ' + s.error]);
      refresh();
      return;
    }
    await new Promise(r => setTimeout(r, 1200));
  }
}

async function startJob(path, payload) {
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  addLog(['— nuova richiesta: ' + path]);
  try {
    const j = await api(path, { method: 'POST', headers: {'Content-Type': 'application/json'},
                     body: JSON.stringify(payload) });
    if (j.queued) addLog(['⏳ accodato: partirà dopo quello in corso']);
  } catch (e) { addLog(['✗ ' + e.message]); busy = false;
    document.querySelectorAll('button').forEach(b => b.disabled = false); return; }
  pollLog();
}

$('#btnCancelQ').onclick = async () => {
  await fetch('/api/cancel_queue', { method: 'POST' });
};

function profilo() {
  return { durata: $('#profDurata').value, livello: $('#profLivello').value,
           obiettivo: $('#profObiettivo').value };
}

async function gen(name) {
  startJob('build', { source: name, force: $('#forceAll').checked,
                      bozza: $('#bozzaAll').checked, single: $('#singleAll').checked,
                      profilo: profilo() });
}

async function reaudio(lesson) {
  startJob('reaudio?lesson=' + encodeURIComponent(lesson), {});
}

async function single(lesson) {
  const r = await fetch('/api/export_single?lesson=' + encodeURIComponent(lesson));
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { alert('Esportazione fallita: ' + (j.error || r.status)); return; }
  const mb = (j.size / 1048576).toFixed(1);
  alert('HTML singolo pronto (' + mb + ' MB): si apre in una nuova scheda. ' +
        'Puoi salvarlo e usarlo anche senza server (pendrive/email).');
  window.open(j.url, '_blank');
}

async function scorm(lesson) {
  const r = await fetch('/api/export_scorm?lesson=' + encodeURIComponent(lesson));
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { alert('Export SCORM fallito: ' + (j.error || r.status)); return; }
  alert('Pacchetto SCORM pronto: caricalo su Moodle come "Pacchetto SCORM".');
  window.open(j.url, '_blank');
}

async function handout(lesson) {
  const r = await fetch('/api/handout?lesson=' + encodeURIComponent(lesson));
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { alert('Dispensa fallita: ' + (j.error || r.status)); return; }
  window.open(j.url, '_blank');
}

async function refreshProg() {
  try {
    const p = await api('progress');
    if (p && p.pct) $('#progTxt').textContent = 'Fase: ' + (p.fase || '') + ' — ' + p.pct + '% ' + (p.extra || '');
  } catch (e) { /* ignora */ }
}
setInterval(() => { if (busy) refreshProg(); }, 2000);

$('#btnText').onclick = () => {
  const t = $('#txtBody').value.trim();
  if (t.length < 50) { alert('Incolla almeno 50 caratteri di testo.'); return; }
  startJob('build_text', { text: t, title: $('#txtTitle').value.trim() || 'Materiale incollato',
    force: $('#forceAll').checked, bozza: $('#bozzaAll').checked,
    single: $('#singleAll').checked, profilo: profilo() });
};

async function loadLan() {
  try {
    const j = await api('lan');
    const url = j.url || '';
    $('#lanUrl').value = url || 'LAN non disponibile (stessa Wi-Fi del PC?)';
    const qr = $('#lanQr');
    if (url) {
      qr.src = 'https://api.qrserver.com/v1/create-qr-code/?size=140x140&data=' + encodeURIComponent(url);
      qr.hidden = false;
      qr.onerror = () => { qr.hidden = true; };  // offline: niente QR, resta il link
    } else { qr.hidden = true; }
  } catch (e) { $('#lanUrl').value = 'LAN non disponibile'; }
  try {
    const h = await api('history');
    $('#hist').innerHTML = (h.history || []).slice(-8).reverse().map(x =>
      `<div>${esc(x.t)} · ${esc(x.kind)} · ${esc(x.source)} — ${x.ok ? '✓' : '✗'} (${x.secs}s)</div>`).join('')
      || '<div>Nessun job ancora.</div>';
  } catch (e) { /* resta il placeholder */ }
}
$('#btnLan').onclick = loadLan;
$('#btnLanCopy').onclick = async () => {
  const v = $('#lanUrl').value;
  if (!v || v.startsWith('LAN')) return;
  try { await navigator.clipboard.writeText(v); $('#btnLanCopy').textContent = '✓ Copiato'; }
  catch (e) { $('#lanUrl').select(); document.execCommand('copy'); }
  setTimeout(() => { $('#btnLanCopy').textContent = '📋 Copia link'; }, 1600);
};

// ---------------------------------------------------- upload materiale
const upZone = $('#upzone'), upFile = $('#upfile');
let pendingFile = null;

function upMsg(text, ok) {
  const el = $('#upmsg');
  el.textContent = text;
  el.hidden = !text;
  el.className = ok === null ? 'upmsg' : ok ? 'upmsg ok' : 'upmsg err';
}

function pickUpload(f) {
  if (!f) return;
  if (!/\.(docx|pdf|txt|md|html?)$/i.test(f.name)) {
    upMsg('Formato non supportato: usa .docx, .pdf, .txt, .md, .html', false);
    return;
  }
  pendingFile = f;
  $('#upname').textContent = f.name;
  $('#upsize').textContent = fmtSize(f.size);
  $('#uprow').hidden = false;
  upMsg('', null);
}

function clearUpload() {
  pendingFile = null;
  upFile.value = '';
  $('#uprow').hidden = true;
  upMsg('', null);
}

async function doUpload(andGenerate) {
  if (!pendingFile) return null;
  upMsg('Caricamento…', null);
  const fd = new FormData();
  fd.append('file', pendingFile);
  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('Errore ' + r.status));
    const fname = j.name;
    upMsg('✔ Caricato: ' + fname + (j.replaced ? ' (sostituito)' : '') +
          (j.copy ? ' (salvato come copia: originale bloccato)' : ''), true);
    $('#uprow').hidden = true;
    pendingFile = null;
    upFile.value = '';
    await refresh();
    if (andGenerate) gen(fname);
    return fname;
  } catch (e) {
    upMsg('✗ ' + e.message, false);
    return null;
  }
}

upZone.addEventListener('click', e => {
  if (e.target.closest && e.target.closest('button')) return;   // niente dialogo sui bottoni
  upFile.click();
});
upZone.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); upFile.click(); }
});
upZone.addEventListener('dragover', e => { e.preventDefault(); upZone.classList.add('drag'); });
upZone.addEventListener('dragleave', () => upZone.classList.remove('drag'));
upZone.addEventListener('drop', e => {
  e.preventDefault();
  upZone.classList.remove('drag');
  pickUpload(e.dataTransfer.files[0]);
});
upFile.onchange = () => pickUpload(upFile.files[0]);
$('#btnUp').onclick = () => doUpload(false);
$('#btnUpGen').onclick = () => doUpload(true);
$('#btnUpX').onclick = clearUpload;

$('#btnUrl').onclick = () => {
  const u = $('#url').value.trim();
  if (!/^https?:\/\//i.test(u)) { alert('Incolla un indirizzo completo (https://…).'); return; }
  startJob('build', { source: u, force: $('#forceAll').checked,
                      bozza: $('#bozzaAll').checked, single: $('#singleAll').checked,
                      profilo: profilo() });
};

// ---------------------------------------------------- voce anteprima + editor
async function loadVoices() {
  try {
    const r = await fetch('/api/voices');
    const j = await r.json();
    $('#voiceSel').innerHTML = (j.voices || []).map(v =>
      `<option value="${v}"${v === j.current ? ' selected' : ''}>${v.replace('it-IT-', '').replace('MultilingualNeural', '')}</option>`).join('');
  } catch (e) { /* resta vuoto */ }
}

$('#btnVoice').onclick = async () => {
  const msg = $('#voiceMsg'), au = $('#voiceAudio');
  msg.hidden = true; au.hidden = true;
  msg.textContent = 'Sintesi…'; msg.hidden = false; msg.className = 'upmsg';
  try {
    const r = await fetch('/api/tts_preview', { method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: $('#voiceTxt').value, voice: $('#voiceSel').value, rate: $('#rateSel').value }) });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || ('Errore ' + r.status)); }
    const blob = await r.blob();
    au.src = URL.createObjectURL(blob);
    au.hidden = false;
    msg.hidden = true;
    au.play().catch(() => {});
  } catch (e) { msg.textContent = '✗ ' + e.message; msg.className = 'upmsg err'; }
};

let ED = { lesson: null, slides: [], idx: 0 };
async function openEditor(lesson) {
  const r = await fetch('/api/lesson_data?lesson=' + encodeURIComponent(lesson));
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) { alert('Editor fallito: ' + (j.error || r.status)); return; }
  ED = { lesson, slides: j.slides || [], idx: 0 };
  if (!ED.slides.length) { alert('Lezione senza slide.'); return; }
  renderEditor();
  $('#editor').hidden = false;
  $('#editor').scrollIntoView({ behavior: 'smooth' });
}
function renderEditor() {
  const s = ED.slides[ED.idx];
  $('#edSel').innerHTML = ED.slides.map((x, i) =>
    `<option value="${i}"${i === ED.idx ? ' selected' : ''}>${i + 1}. ${(x.title || '').slice(0, 50)}</option>`).join('');
  $('#edTitle').value = s.title || '';
  $('#edNarr').value = s.narration || '';
  const q = s.quiz || {};
  $('#edQuizQ').value = q.q || q.domanda || '';
  const opts = q.opts || q.opzioni || [];
  $('#edQuizOpts').value = opts.map(o => ((o.ok || o.corretta) ? '* ' : '') + (o.t || o.testo || '')).join('\n');
  $('#edMeta').textContent = `Slide ${ED.idx + 1}/${ED.slides.length} · durata ${s.duration || '?'}s` +
    (s.quiz ? '' : ' · (nessun quiz: compila domanda+opzioni per aggiungerlo)');
  $('#edMsg').hidden = true;
}
async function saveEditor(reaudio) {
  const msg = $('#edMsg');
  const lines = $('#edQuizOpts').value.split('\n').map(x => x.trim()).filter(Boolean);
  let quiz = null;
  if ($('#edQuizQ').value.trim() || lines.length) {
    quiz = { domanda: $('#edQuizQ').value.trim(),
             opzioni: lines.map(l => l.startsWith('* ')
               ? { testo: l.slice(2).trim(), corretta: true }
               : { testo: l, corretta: false }) };
  }
  const body = { lesson: ED.lesson, index: ED.idx,
    patch: { title: $('#edTitle').value, narration: $('#edNarr').value,
             ...(quiz ? { quiz } : {}) } };
  msg.textContent = 'Salvataggio…'; msg.hidden = false; msg.className = 'upmsg';
  try {
    const r = await fetch('/api/save_slide', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || ('Errore ' + r.status));
    msg.textContent = '✔ Salvato. ' + (j.warning || '');
    msg.className = 'upmsg ok';
    if (reaudio) {
      msg.textContent = '✔ Salvato. Rigenero audio slide…';
      const r2 = await fetch('/api/reaudio_slide?lesson=' + encodeURIComponent(ED.lesson) + '&index=' + ED.idx, { method: 'POST' });
      const j2 = await r2.json().catch(() => ({}));
      if (!r2.ok) throw new Error(j2.reason || j2.error || ('Errore ' + r2.status));
      busy = true; pollLog();
    } else {
      openEditor(ED.lesson);
    }
  } catch (e) { msg.textContent = '✗ ' + e.message; msg.className = 'upmsg err'; }
}
async function moveEditor(d) {
  const to = ED.idx + d;
  if (to < 0 || to >= ED.slides.length) return;
  const r = await fetch('/api/move_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson: ED.lesson, from: ED.idx, to }) });
  if (!r.ok) { alert('Spostamento fallito'); return; }
  ED.idx = to; openEditor(ED.lesson);
}
async function addEditor() {
  const t = prompt('Titolo nuova slide:'); if (t === null) return;
  const n = prompt('Narrazione (voce legge questo testo):') || '';
  const r = await fetch('/api/add_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson: ED.lesson, title: t, narration: n }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) { alert('Aggiunta fallita: ' + (j.error || r.status)); return; }
  ED.idx = j.pos || 0; openEditor(ED.lesson);
}
async function delEditor() {
  if (!confirm('Eliminare la slide ' + (ED.idx + 1) + '?')) return;
  const r = await fetch('/api/delete_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson: ED.lesson, index: ED.idx }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) { alert('Eliminazione fallita: ' + (j.error || r.status)); return; }
  ED.idx = 0; openEditor(ED.lesson);
}
loadVoices();

refresh();
loadLan();
setInterval(() => { if (!busy) refresh(); }, 4000);
</script>
</body>
</html>
"""


# ------------------------------------------------------------------ main
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # dipendenze base: auto-install se mancanti (come AVVIA.bat)
    def have(m):
        try:
            __import__(m)
            return True
        except Exception:
            return False

    if not have("docx"):
        print("Installo le dipendenze mancanti (python-docx, edge-tts)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                        str(BASE / "requirements.txt")], check=False)

    port = DEFAULT_PORT
    argv = sys.argv[1:]
    try:
        i = argv.index("--port")
        port = int(argv[i + 1])
    except (ValueError, IndexError):
        pass
    free = find_port(port, 10)
    if free is None:
        print(f"ERRORE: nessuna porta libera tra {port} e {port + 9} "
              "(tutte occupate). Chiudi gli altri server e riprova.")
        sys.exit(1)
    port = free

    url = f"http://localhost:{port}/"
    print("=" * 60)
    print("  PANNELLO DI CONTROLLO — generatore lezioni")
    print(f"  URL: {url}")
    print("  Premi CTRL+C per fermare il server.")
    print("=" * 60)
    ip = lan_ip()
    if ip:
        print(f"  Lezioni per tablet/telefono sulla stessa rete: http://{ip}:{port}/")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    handler = functools.partial(PanelHandler, directory=str(BASE))
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer fermato.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
