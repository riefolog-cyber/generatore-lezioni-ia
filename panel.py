# -*- coding: utf-8 -*-
"""Pannello di controllo web (locale).

Sostituisce l'avvio da terminale con una pagina grafica nel browser:
  - elenco dei materiali nella cartella (.docx/.pdf/.txt/.md/.html) con
    pulsante "Genera" per ciascuno;
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
import http.server
import io
import json
import os
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
from start_lesson import _RangeHandler, _hub_page, list_lessons  # noqa: E402

CONFIG = load_config()
DEFAULT_PORT = int(CONFIG.get("porta", 8341))
MAX_UPLOAD_MB = 100        # limite per i file caricati dal pannello
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# ------------------------------------------------------------------ job runner
LOG = collections.deque(maxlen=500)
JOB = {"running": False, "kind": None, "source": None, "error": None,
       "done_at": None, "ok": None}


class _UploadTooBig(Exception):
    """File caricato dal pannello oltre il limite (risponde HTTP 413)."""


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
    _lessons_cache["hub"] = None


class _LogWriter:
    def write(self, s):
        s = s.rstrip()
        if s:
            LOG.append(s)
        return len(s) + 1

    def flush(self):
        pass


def _log(txt):
    LOG.append(str(txt).rstrip())


def start_job(fn, kind, source):
    """Lancia una generazione in background (una alla volta: blocco del progetto
    incluso). Ritorna True se il job è partito, False se uno è già in corso."""
    if JOB["running"]:
        return False
    JOB.update(running=True, kind=kind, source=source, error=None,
               done_at=None, ok=None)
    _log(f"▶ [{kind}] {source}")

    def work():
        try:
            with contextlib.redirect_stdout(_LogWriter()), \
                    contextlib.redirect_stderr(_LogWriter()):
                fn()
            JOB["ok"] = True
            _log("✔ JOB COMPLETATO")
        except Exception as e:  # noqa: BLE001
            JOB["error"] = str(e)
            JOB["ok"] = False
            _log(f"✗ ERRORE: {e}")
        finally:
            JOB["running"] = False
            JOB["done_at"] = time.time()
            _invalidate_lessons_cache()   # una nuova lezione è (forse) pronta

    threading.Thread(target=work, daemon=True).start()
    return True


# ------------------------------------------------------------------ helpers
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if not ip.startswith("127.") else None
    except Exception:
        return None


def _materials():
    out = []
    project = re.compile(r"^(LEGGIMI|README|requirements|CHANGELOG|setup|pyproject)"
                         r"(?:[._\-].*)?$|^_", re.IGNORECASE)
    for p in sorted(BASE.iterdir()):
        if not (p.is_file() and p.suffix.lower() in SUPPORTED_EXT and not p.name.startswith(".")):
            continue
        if project.match(p.stem) or p.suffix.lower() == ".bat":
            continue
        out.append({
            "name": p.name,
            "size": p.stat().st_size,
            "modified": p.stat().st_mtime,
            "lesson": f"{re.sub(r'[^A-Za-z0-9_]+', '_', p.stem).strip('_') or 'Lezione'}_lesson",
        })
    return out


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
        "config": {k: CONFIG.get(k) for k in
                   ("theme", "voice", "edge_voice", "edge_rate",
                    "llm_model", "num_moduli_min", "num_moduli_max")},
    }


def _loopback(handler):
    return handler.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


# ------------------------------------------------------------------ handler
class PanelHandler(_RangeHandler):
    """Serve: pannello + API (solo localhost) + lezioni (tutti, whitelist)."""

    # -- API ------------------------------------------------------------------
    def _api(self, path, query):
        if not _loopback(self):
            self.send_error(403, "API riservate a localhost")
            return
        if path == "/api/state":
            return self._json(_state())
        if path == "/api/log":
            return self._json({"running": JOB["running"], "kind": JOB["kind"],
                               "source": JOB["source"], "error": JOB["error"],
                               "done_at": JOB["done_at"], "ok": JOB["ok"],
                               "lines": list(LOG)[-200:]})
        if path == "/api/build":
            return self._start(self._parse_build())
        if path == "/api/reaudio":
            return self._start_reaudio(query)
        if path == "/api/export_single":
            return self._export_single(query)
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
        """File nella cartella del progetto (no path traversal) oppure URL."""
        if is_url(src):
            return src
        name = urllib.parse.unquote(src)
        p = (BASE / name).resolve()
        if p.parent != BASE or not p.is_file() or p.suffix.lower() not in SUPPORTED_EXT:
            raise ValueError(f"Materiale non valido: {src}")
        return str(p)

    def _parse_build(self):
        data = self._read_json_body()
        src = self._resolve_source(str(data.get("source") or ""))
        force = bool(data.get("force"))
        bozza = bool(data.get("bozza"))
        single = bool(data.get("single"))
        return src, force, bozza, single

    def _start(self, parsed):
        import new_lesson
        src, force, bozza, single = parsed
        ok = start_job(
            lambda: new_lesson.build_from_docx(src, force=force, bozza=bozza,
                                               single=single),
            "generazione", Path(src).name if not is_url(src) else src)
        if not ok:
            self._json({"started": False, "reason": "Un'altra generazione è già in corso."}, 409)
            return
        self._json({"started": True})

    def _start_reaudio(self, query):
        name = query.get("lesson", [None])[0] or ""
        lesson = BASE / name
        if not name or not lesson.is_dir() or not (lesson / "index.html").exists():
            self.send_error(400, "Lezione non trovata")
            return
        import new_lesson
        ok = start_job(lambda: new_lesson.regen_audio_lesson(str(lesson)),
                       "rigenerazione audio", name)
        if not ok:
            self._json({"started": False, "reason": "Un'altra generazione è già in corso."}, 409)
            return
        self._json({"started": True})

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

    # -- upload materiale -------------------------------------------------------
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
        body = self.rfile.read(length)
        sep = b"--" + boundary
        for part in body.split(sep):
            part = part.strip(b"\r\n")
            if not part or part == b"--":
                continue
            head_end = part.find(b"\r\n\r\n")
            if head_end < 0:
                continue
            headers = part[:head_end].decode("utf-8", "replace")
            mf = re.search(r'filename="([^"]*)"', headers)
            if mf is None or 'name="file"' not in headers:
                continue
            return mf.group(1), part[head_end + 4:]
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
        dest = BASE / safe
        replaced = dest.exists()
        dest.write_bytes(data)
        _invalidate_lessons_cache()   # il nuovo materiale può generare una lezione
        _log(f"✔ materiale caricato dal pannello: {safe}"
             + (" (sostituito)" if replaced else ""))
        self._json({"ok": True, "name": safe, "replaced": replaced,
                    "size": len(data)})

    # -- GET ------------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if path.startswith("/api/"):
            return self._api(path, query)
        if path in ("/", "/index.html"):
            body = PANEL_HTML.encode("utf-8") if _loopback(self) else _hub_page_cached().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        first = path.lstrip("/").split("/", 1)[0]
        # whitelist con TTL breve (2s) + invalidazione su upload/generazione:
        # le lezioni appena create (o i file unici *_singola.html) diventano
        # subito disponibili, ma senza fare glob su disco a ogni asset
        if first not in _allowed_lesson_names() and first not in _allowed_single_names():
            self.send_error(404, "Non disponibile")
            return
        super().do_GET()

    def do_POST(self):
        if not _loopback(self):
            self.send_error(403, "API riservate a localhost")
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/build":
            try:
                self._start(self._parse_build())
            except Exception as e:  # noqa: BLE001
                self._json({"started": False, "reason": str(e)}, 400)
        elif parsed.path == "/api/reaudio":
            try:
                self._start_reaudio(urllib.parse.parse_qs(parsed.query))
            except Exception as e:  # noqa: BLE001
                self._json({"started": False, "reason": str(e)}, 400)
        elif parsed.path == "/api/upload":
            try:
                self._upload()
            except _UploadTooBig as e:
                self._json({"ok": False, "error": str(e)}, 413)
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 400)
        else:
            self.send_error(404, "API sconosciuta")


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
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--txt);
margin:0;padding:26px 18px 60px}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:16px 18px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 12px;color:#c8d4f5}
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
height:230px;overflow:auto;font:12px/1.55 Consolas,'Cascadia Mono',monospace;margin:0;
white-space:pre-wrap;word-break:break-word}
#lan{color:var(--warn)}
a.apri{text-decoration:none;color:#8ecaff;font-weight:700}
.empty{color:var(--mut);font-size:13px;padding:8px 0}
.upzone{border:1px dashed var(--line);border-radius:12px;padding:15px 18px;
margin:0 0 14px;color:var(--mut);font-size:13px;cursor:pointer;user-select:none}
.upzone:hover,.upzone.drag{border-color:var(--acc);background:#18233f;color:#c8d8ff}
.uprow{display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap}
.upmsg{font-size:12px;margin:6px 0 0}
.upmsg.ok{color:var(--ok)}.upmsg.err{color:var(--err)}
</style>
</head>
<body>
<div class="wrap">
  <h1>🎛 Pannello di controllo — Generatore lezioni</h1>
  <div class="sub" id="statusline">…</div>

  <div class="card">
    <h2>Ambiente</h2>
    <div class="chips" id="deps"></div>
  </div>

  <div class="card">
    <h2>1. Materiale per la lezione</h2>
    <div class="upzone" id="upzone" role="button" tabindex="0"
         title="Carica un file dal pannello (trascinalo qui sopra o clicca per sceglierlo)">
      <input type="file" id="upfile" accept=".docx,.pdf,.txt,.md,.html,.htm" hidden>
      <span id="uptxt">📄 Trascina qui il materiale (.docx, .pdf, .txt, .md, .html) — oppure clicca per sceglierlo (max 100 MB)</span>
      <div class="uprow" id="uprow" hidden>
        <span class="name" id="upname"></span>
        <span class="meta" id="upsize"></span>
        <button class="mini" id="btnUp" type="button">Carica</button>
        <button class="mini ghost" id="btnUpX" type="button" title="Annulla">✕</button>
      </div>
      <div class="upmsg" id="upmsg" hidden></div>
    </div>
    <div id="materials"></div>
    <div class="opts">
      <label><input type="checkbox" id="forceAll"> Rigenera anche le lezioni già esistenti (--force)</label>
      <label><input type="checkbox" id="bozzaAll"> Bozza senza LLM (struttura dal testo, --bozza)</label>
      <label><input type="checkbox" id="singleAll"> Genera come file HTML unico, senza cartella</label>
    </div>
  </div>

  <div class="card">
    <h2>2. Oppure genera da un link</h2>
    <div class="urlrow">
      <input id="url" placeholder="https://it.wikipedia.org/wiki/…  oppure  https://www.youtube.com/watch?v=…">
      <button id="btnUrl">Genera da link</button>
    </div>
  </div>

  <div class="card">
    <h2>3. Lezioni generate</h2>
    <div id="lessons"></div>
    <h2 style="margin-top:16px">File unici (HTML singolo)</h2>
    <div id="singles"></div>
  </div>

  <div class="card">
    <h2>Log di generazione</h2>
    <pre id="log"></pre>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
let busy = false;

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

async function refresh() {
  try {
    const s = await api('state');
    $('#statusline').textContent =
      `Porta ${s.port} · server locale` + (s.lan_ip ? ` · da tablet/telefono: <span id="lan">http://${s.lan_ip}:${s.port}/</span>` : '');
    $('#deps').innerHTML = depChips(s.deps);

    const mats = s.materials;
    $('#materials').innerHTML = mats.length ? mats.map(m => {
      const exists = s.lessons.some(l => l.name === m.lesson);
      return `<div class="row">
        <span class="name">${m.name}</span>
        <span class="meta">${fmtSize(m.size)}</span>
        <span class="badge ${exists ? 'exists' : ''}">${exists ? 'lezione esistente' : 'da generare'}</span>
        <button class="mini" onclick="gen('${m.name.replace(/'/g, "\\'")}')">Genera</button>
      </div>`;
    }).join('') : '<div class="empty">Nessun materiale: caricalo qui sopra oppure mettilo nella cartella del progetto.</div>';

    $('#lessons').innerHTML = s.lessons.length ? s.lessons.map(l =>
      `<div class="row">
        <span class="name">${l.title}</span>
        <a class="apri" href="/${l.name}/index.html" target="_blank">Apri →</a>
        <button class="mini ghost" onclick="single('${l.name}')">HTML singolo</button>
        <button class="mini ghost" onclick="reaudio('${l.name}')">Rigenera audio</button>
      </div>`).join('')
      : '<div class="empty">Nessuna lezione generata ancora.</div>';

    const singles = s.singles || [];
    $('#singles').innerHTML = singles.length ? singles.map(f =>
      `<div class="row">
        <span class="name">${f.stem}</span>
        <span class="meta">${fmtSize(f.size)}</span>
        <a class="apri" href="/${f.name}" target="_blank" download="${f.name}">Apri / salva →</a>
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
  if (busy) { alert('Generazione già in corso: attendi la fine.'); return; }
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  addLog(['— nuova richiesta: ' + path]);
  try {
    await api(path, { method: 'POST', headers: {'Content-Type': 'application/json'},
                     body: JSON.stringify(payload) });
  } catch (e) { addLog(['✗ ' + e.message]); busy = false;
    document.querySelectorAll('button').forEach(b => b.disabled = false); return; }
  pollLog();
}

async function gen(name) {
  startJob('build', { source: name, force: $('#forceAll').checked,
                      bozza: $('#bozzaAll').checked, single: $('#singleAll').checked });
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

async function doUpload() {
  if (!pendingFile) return;
  upMsg('Caricamento…', null);
  const fd = new FormData();
  fd.append('file', pendingFile);
  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('Errore ' + r.status));
    upMsg('✔ Caricato: ' + j.name + (j.replaced ? ' (sostituito il file esistente)' : ''), true);
    $('#uprow').hidden = true;
    pendingFile = null;
    upFile.value = '';
    refresh();
  } catch (e) {
    upMsg('✗ ' + e.message, false);
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
$('#btnUp').onclick = doUpload;
$('#btnUpX').onclick = clearUpload;

$('#btnUrl').onclick = () => {
  const u = $('#url').value.trim();
  if (!/^https?:\/\//i.test(u)) { alert('Incolla un indirizzo completo (https://…).'); return; }
  startJob('build', { source: u, force: $('#forceAll').checked,
                      bozza: $('#bozzaAll').checked, single: $('#singleAll').checked });
};

refresh();
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


def find_port(start=DEFAULT_PORT, tries=10):
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return None


if __name__ == "__main__":
    main()
