# -*- coding: utf-8 -*-
"""Pannello di controllo web (locale).

Sostituisce l'avvio da terminale con una pagina grafica nel browser:
  - caricamento materiale via upload (drag & drop o Sfoglia: documenti,
    PPTX, EPUB, testo e audio MP3/M4A/WAV) con pulsante "Carica e genera";
  - trascrizione locale dell'audio con Whisper, se installato;
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
import functools
import http.server
import io
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import webbrowser
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

from common import load_config  # noqa: E402
from class_repository import add_result as _class_repo_add, authorized as _class_repo_authorized  # noqa: E402
from class_repository import list_results as _class_repo_list, reset as _class_repo_reset  # noqa: E402
from sources import SUPPORTED_EXT, is_url  # noqa: E402
from start_lesson import _RangeHandler, _hub_page, find_port, list_lessons  # noqa: E402

CONFIG = load_config()
DEFAULT_PORT = int(CONFIG.get("porta", 8341))
MAX_UPLOAD_MB = int(CONFIG.get("max_upload_mb", 100))  # configurabile
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
RUNTIME_PORT = DEFAULT_PORT  # valore reale, utile se find_port sceglie 8342 ecc.

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
from tools.jobs import JobManager  # noqa: E402

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


def _history_clear():
    """Svuota la cronologia generazioni (pulsante nel pannello)."""
    try:
        HISTORY_FILE.write_text("[]", encoding="utf-8")
    except OSError:
        pass


def _classifica_reset():
    """Azzera la classifica di classe (pulsante nel pannello)."""
    try:
        _class_repo_reset(CLASSIFICA_FILE)
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
    _class_repo_add(CLASSIFICA_FILE, lesson, studente, punti, totale,
                    completata, tempo_min)


def _classifica_view():
    """Classifica per lezione: miglior risultato per studente, ordinato per
    percentuale (decrescente) e tempo (crescente)."""
    try:
        rows = _class_repo_list(CLASSIFICA_FILE)
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


_JOBS = JobManager(_history_append, _invalidate_lessons_cache, _flog)
LOG = _JOBS.log
JOB = _JOBS.state
QUEUE = _JOBS.queue
JOB_LOCK = _JOBS.lock
LOG_LOCK = LOG.lock


def _log(txt):
    with LOG_LOCK:
        LOG.append(str(txt).rstrip())


def _run_next_queued():
    _JOBS.run_next()


def _material_job_pending(source_name):
    """True se lo stesso materiale è già in lavorazione o accodato."""
    return _JOBS.pending_source(source_name)


def start_job(fn, kind, source):
    """Lancia una generazione in background. Se una è in corso, accoda (max 5)."""
    return _JOBS.start(fn, kind, source)


def cancel_queue():
    return _JOBS.cancel_queue()


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
        "faster_whisper": have("faster_whisper"),
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


def _lesson_details(name):
    try:
        from tools.lesson_admin import lesson_info
        return lesson_info(BASE, name)
    except Exception:
        l = BASE / name
        return {"name": name, "title": name.replace("_lesson", ""),
                "size": 0, "duration": 0, "modified": 0}


def _state():
    lessons = [_lesson_details(l.name) for l in list_lessons()]
    try:
        from tools.lesson_admin import list_archived
        archived = list_archived(BASE)
    except Exception:
        archived = []
    return {
        "materials": _materials(), "lessons": lessons, "archived": archived,
        "singles": _single_files(), "deps": _deps(),
        "lan_ip": lan_ip(), "port": RUNTIME_PORT,
        "max_upload_mb": MAX_UPLOAD_MB,
        "pipeline_stantia": _pipeline_mtime() != _PIPELINE_MTIME_START,
        "config": {k: CONFIG.get(k) for k in
                   ("theme", "voice", "edge_voice", "edge_rate",
                    "llm_model", "num_moduli_min", "num_moduli_max",
                    "profilo_durata", "profilo_livello", "profilo_obiettivo",
                    "whisper_model")},
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
        if path == "/api/progress":
            return self._progress()
        if path == "/api/history":
            return self._history()
        if path == "/api/lan":
            return self._json({"lan_ip": lan_ip(), "port": RUNTIME_PORT,
                               "url": f"http://{lan_ip()}:{RUNTIME_PORT}/" if lan_ip() else None})
        if path == "/api/qr":
            return self._qr(query)
        if path == "/api/diagnostica":
            from tools.netdiag import diagnose
            return self._json(diagnose(RUNTIME_PORT))
        if path == "/api/logs_download":
            return self._logs_download()
        if path == "/api/voices":
            return self._json({"voices": _edge_voices_live(),
                               "current": CONFIG.get("edge_voice")})
        if path == "/api/lesson_data":
            return self._lesson_data(query)
        if path == "/api/settings":
            from tools.panel_settings import public_config
            return self._json({"settings": public_config(),
                               "runtime_port": RUNTIME_PORT})
        self.send_error(404, "API sconosciuta")

    def _teacher_allowed(self):
        return _class_repo_authorized(
            CONFIG.get("pin_docente"), self.headers.get("X-Teacher-Pin"))

    def _require_teacher(self):
        if self._teacher_allowed():
            return True
        self._json({"ok": False, "error": "PIN docente non valido."}, 403)
        return False

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

    def _qr(self, query):
        """QR locale: funziona anche senza internet, a differenza dei servizi esterni."""
        text = (query.get("url", [""])[0] or "").strip()
        if not re.fullmatch(r"https?://[^\s]{1,500}", text):
            self._json({"error": "Indirizzo URL non valido"}, 400)
            return
        try:
            from tools.qr import qr_png_bytes
            body = qr_png_bytes(text, scale=6, border=4)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"QR non creato: {e}"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _logs_download(self):
        """Un unico ZIP con i log utili per assistenza, senza dati degli alunni."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("log-pannello.txt", "\n".join(list(LOG)[-500:]).encode("utf-8"))
            for name in ("generazione.log", "panel_errors.log"):
                p = BASE / name
                if p.is_file():
                    z.write(p, arcname=name)
            z.writestr("diagnostica.json", json.dumps(
                _state(), ensure_ascii=False, indent=2).encode("utf-8"))
        body = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="log-generatore.zip"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _lesson_action(self, action, data):
        name = str(data.get("lesson") or "")
        from tools import lesson_admin
        if action == "rename":
            result = lesson_admin.rename_lesson(BASE, name, data.get("title"))
        elif action == "duplicate":
            result = lesson_admin.duplicate_lesson(BASE, name)
        elif action == "archive":
            result = lesson_admin.archive_lesson(BASE, name)
        elif action == "restore":
            result = lesson_admin.restore_lesson(BASE, name)
        elif action == "delete":
            if data.get("confirm") != name:
                raise ValueError("Conferma richiesta: nome della lezione non corrisponde.")
            result = lesson_admin.delete_lesson(BASE, name)
        else:
            raise ValueError("Operazione lezione sconosciuta.")
        _invalidate_lessons_cache()
        self._json({"ok": True, "name": result})

    def _resolve_source(self, src):
        """Solo file caricati via upload in questa sessione (no scansione
        cartella) oppure URL. Niente path traversal."""
        if is_url(src):
            return src
        name = Path(urllib.parse.unquote(src)).name
        p = (BASE / name).resolve()
        if p.parent != BASE or not p.is_file() or p.suffix.lower() not in SUPPORTED_EXT:
            raise ValueError(f"Materiale non valido: {src}")
        UPLOADED.setdefault(name, {"t": time.time()})
        return str(p)

    def _parse_build(self):
        from common import normalize_profilo
        data = self._read_json_body()
        src = self._resolve_source(str(data.get("source") or ""))
        force = bool(data.get("force"))
        bozza = bool(data.get("bozza"))
        single = bool(data.get("single"))
        whisper = bool(data.get("whisper"))
        if Path(src).suffix.lower() in (".mp3", ".m4a", ".wav") and not whisper:
            raise ValueError("Per generare da audio devi confermare la trascrizione Whisper.")
        profilo = normalize_profilo(data.get("profilo") or {
            "durata": data.get("durata"), "livello": data.get("livello"),
            "obiettivo": data.get("obiettivo")})
        return src, force, bozza, single, profilo

    def _start(self, parsed):
        import new_lesson
        src, force, bozza, single, profilo = parsed
        source_name = Path(src).name if not is_url(src) else src
        if _material_job_pending(source_name):
            state = "già in lavorazione" if JOB["running"] else "già in coda"
            self._json({"started": False,
                        "reason": f"Attenzione: questo materiale è {state}."}, 409)
            return
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
            if len(buf) > MAX_UPLOAD_BYTES * 10 + 2 * 1024 * 1024:
                raise _UploadTooBig(
                    f"Caricamento troppo grande: massimo {MAX_UPLOAD_MB} MB per file e 10 file per richiesta.")
        return bytes(buf)

    def _parse_upload_body(self):
        """Estrae i file dal multipart con controlli centralizzati nel modulo."""
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("Richiesta senza corpo da caricare.")
        if length > MAX_UPLOAD_BYTES * 10 + 2 * 1024 * 1024:
            raise _UploadTooBig(
                f"Caricamento troppo grande: massimo {MAX_UPLOAD_MB} MB per file e 10 file per richiesta.")
        from tools.multipart import parse_multipart
        return parse_multipart(self._read_limited(length), ctype)

    def _upload(self):
        files = self._parse_upload_body()
        if len(files) > 10:
            raise ValueError("Carica al massimo 10 file per volta.")
        saved = []
        for raw_name, data in files:
            saved.append(self._save_uploaded_file(raw_name, data))
        total = sum((BASE / name).stat().st_size for name in saved)
        self._json({"ok": True, "names": saved, "name": saved[0],
                    "size": total, "replaced": len(saved) == 1,
                    "copy": len(saved) != 1})

    def _save_uploaded_file(self, raw_name, data):
        from tools.uploads import UploadTooBig, save_upload
        try:
            final_name, replaced, copied = save_upload(
                BASE, raw_name, data, SUPPORTED_EXT, MAX_UPLOAD_BYTES)
        except UploadTooBig as exc:
            raise _UploadTooBig(str(exc)) from exc
        _register_upload(final_name)
        _invalidate_lessons_cache()
        _log(f"✔ materiale caricato dal pannello: {final_name}"
             + (" (sostituito)" if replaced else "")
             + (" (salvato come copia)" if copied else ""))
        return final_name

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
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
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
        elif parsed.path == "/api/lesson_action":
            try:
                data = self._read_json_body()
                self._lesson_action(str(data.get("action") or ""), data)
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 400)
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
        elif parsed.path == "/api/cancel_job":
            if not JOB["running"]:
                self._json({"ok": False, "error": "Nessun lavoro in corso."}, 409)
                return
            from sources import cancel_transcription
            cancel_transcription()
            _log("⏹ annullamento trascrizione richiesto")
            self._json({"ok": True})
        elif parsed.path == "/api/settings":
            if not self._require_teacher():
                return
            try:
                from tools.panel_settings import update_public
                global CONFIG, MAX_UPLOAD_MB, MAX_UPLOAD_BYTES
                values = update_public(self._read_json_body())
                old_port = int(CONFIG.get("porta", 8341))
                old_cache = int(CONFIG.get("cache_max_mb", 300))
                CONFIG = load_config()
                MAX_UPLOAD_MB = int(CONFIG.get("max_upload_mb", 100))
                MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
                restart = (int(CONFIG.get("porta", 8341)) != old_port or
                           int(CONFIG.get("cache_max_mb", 300)) != old_cache)
                self._json({"ok": True, "settings": values,
                            "restart_required": restart})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 400)
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
        elif parsed.path == "/api/clear_history":
            _history_clear()
            self._json({"ok": True})
        elif parsed.path == "/api/reset_classifica":
            if not _class_repo_authorized(CONFIG.get("pin_docente"),
                                          self.headers.get("X-Teacher-Pin")):
                self._json({"ok": False, "error": "PIN docente non valido."}, 403)
                return
            _classifica_reset()
            self._json({"ok": True})
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
try:
    from panel_ui import PANEL_HTML
except ImportError:  # esecuzione dalla cartella tools
    from tools.panel_ui import PANEL_HTML




# ------------------------------------------------------------------ main
def main():
    global RUNTIME_PORT
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

    RUNTIME_PORT = port
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
