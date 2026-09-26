# -*- coding: utf-8 -*-
"""Estrazione del materiale di partenza da fonti diverse.

Supporta:
- file .docx (python-docx)
- file .pdf (pypdf, opzionale)
- file di testo .txt / .md / .html locale
- URL di siti web (fetch + parsing HTML)
- video YouTube (trascrizione via youtube-transcript-api, opzionale;
  fallback: titolo + descrizione)
- audio MP3/M4A/WAV (trascrizione locale con Whisper, opzionale)

Ogni estrattore ritorna la stessa struttura di extract_docx:
{"title": str, "sections": [{"heading": str|None, "paras": [str, ...]}]}
così il resto della pipeline non cambia.
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

# Whisper viene caricato solo al primo audio trascritto e riusato per gli altri.
_WHISPER_MODEL = None
_WHISPER_BACKEND = None
_WHISPER_MODELS = {}
_WHISPER_CACHE_DIR = Path(__file__).resolve().parent.parent / ".whisper_cache"
_TRANSCRIBE_CANCEL = threading.Event()
_TRANSCRIBE_PROGRESS = None

# Messaggio unico quando nessun backend di trascrizione e disponibile: su x64 si
# risolve con faster-whisper, su ARM serve whisper.cpp (vedi tools/whisper_cpp.py).
_NESSUNA_TRASCRIZIONE = (
    "Trascrizione audio non disponibile.\n"
    "  x64:  pip install faster-whisper\n"
    "  ARM:  scarica whisper-bin-win-cpu-arm64.zip ed estraila in "
    ".whisper_cache\\whisper-cpp\\ (copia TUTTA la cartella Release: "
    "l'eseguibile va tenuto insieme alle sue DLL)"
)


def begin_transcription():
    """Prepara un nuovo job e restituisce l'evento di cancellazione."""
    _TRANSCRIBE_CANCEL.clear()
    return _TRANSCRIBE_CANCEL


def set_transcription_progress(callback):
    global _TRANSCRIBE_PROGRESS
    _TRANSCRIBE_PROGRESS = callback


def cancel_transcription():
    _TRANSCRIBE_CANCEL.set()
    return _TRANSCRIBE_CANCEL.is_set()


def is_transcription_cancelled():
    return _TRANSCRIBE_CANCEL.is_set()
SUPPORTED_EXT = {".docx", ".pdf", ".txt", ".md", ".html", ".htm", ".pptx", ".epub", ".mp3", ".m4a", ".wav"}

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
_FETCH_TIMEOUT = 25
_FETCH_RETRIES = 3


def _http_get(url, timeout=_FETCH_TIMEOUT, max_bytes=5 * 1024 * 1024):
    """GET con retry + backoff (3 tentativi): le pagine YT/web flakano spesso.
    Limite 5 MB per evitare zip-bomb / pagine giganti."""
    last = None
    # SSRF: blocca localhost/privato se richiesto da panel (chiamante può validare)
    for attempt in range(_FETCH_RETRIES):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"Risposta troppo grande (> {max_bytes} byte)")
                return data, r.headers.get("Content-Type", "")
        except Exception as e:  # noqa: BLE001
            last = e
            if "troppo grande" in str(e):
                raise
            if attempt < _FETCH_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    raise ValueError(f"Download fallito dopo {_FETCH_RETRIES} tentativi ({url}): {last}")


def is_url(value):
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def is_youtube(url):
    host = re.sub(r"^https?://", "", url.lower()).split("/", 1)[0]
    return host in ("youtube.com", "www.youtube.com", "m.youtube.com",
                    "youtu.be", "www.youtu.be", "youtube-nocookie.com",
                    "www.youtube-nocookie.com")


# Un PDF con encoding a due byte può arrivare con un byte di riempimento fra i
# caratteri ("\x00l\x00a" invece di "la"), e in generale il testo estratto può
# contenere caratteri di controllo: in LLM e nella voce si sentirebbero come
# pause e parole spezzate, e il titolo diventerebbe un residuo ("\x00È").
_CONTROLLI = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _resa_testo(s):
    """Quanto un testo "sembra" prosa: lettere/segni in proporzione, NUL esclusi."""
    if not s:
        return -1.0
    buoni = sum(1 for c in s if c.isalnum() or c in " \n\t.,;:!?'\"()-")
    return buoni / len(s)


def _senza_nul(text):
    """Toglie i NUL; se il risultato resta illeggibile (due byte per carattere,
    es. UTF-16 decodificato byte per byte) prova a ricomporre il testo."""
    base = text.replace("\x00", "")
    if _resa_testo(base) >= 0.6:
        return base
    try:
        grezzo = text.encode("latin-1", errors="ignore")
    except UnicodeEncodeError:
        return base
    candidati = [base]
    for codec in ("utf-16-le", "utf-16-be"):
        try:
            candidati.append(grezzo.decode(codec, errors="ignore"))
        except Exception:  # noqa: BLE001
            pass
    return max(candidati, key=_resa_testo)


def _ripara_testo(text):
    """Ripulisce il testo di una fonte: via i byte di riempimento e i caratteri
    di controllo, spazi e righe ripetute normalizzati."""
    if not text:
        return text
    if "\x00" in text:
        text = _senza_nul(text)
    text = _CONTROLLI.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def _pulisci_estratto(ext):
    """Applica la pulizia a titolo, intestazioni e paragrafi di una fonte."""
    if not isinstance(ext, dict):
        return ext
    titolo = _ripara_testo(ext.get("title") or "")
    ext["title"] = titolo or ext.get("title")
    sezioni = []
    for s in ext.get("sections") or []:
        if not isinstance(s, dict):
            continue
        if s.get("heading"):
            s["heading"] = _ripara_testo(str(s["heading"])) or None
        s["paras"] = [p for p in (_ripara_testo(str(p)) for p in (s.get("paras") or [])) if p]
        if s["paras"] or s.get("heading"):
            sezioni.append(s)
    ext["sections"] = sezioni
    return ext


def _whisper_model_name():
    try:
        from common import load_config
        name = str(load_config().get("whisper_model") or "base").lower()
        return name if name in ("tiny", "base", "small") else "base"
    except Exception:
        return "base"


def _load_whisper_model(name, duration=0.0):
    """Carica una sola istanza per modello; riusata nei job successivi.

    Ordine di preferenza: faster-whisper, whisper, whisper.cpp. Il primo e il
    piu' rapido su x64; il terzo e l'unico disponibile su Windows ARM, dove
    faster-whisper non e installabile (CTranslate2 non ha wheel win_arm64).
    """
    global _WHISPER_MODEL, _WHISPER_BACKEND
    if name in _WHISPER_MODELS:
        _WHISPER_MODEL, _WHISPER_BACKEND = _WHISPER_MODELS[name]
        return _WHISPER_MODEL
    if importlib.util.find_spec("faster_whisper") is not None:
        from faster_whisper import WhisperModel
        model = WhisperModel(
            name, device="cpu", compute_type="int8",
            cpu_threads=max(1, min(8, os.cpu_count() or 1)))
        _WHISPER_MODELS[name] = (model, "faster-whisper")
        _WHISPER_MODEL, _WHISPER_BACKEND = model, "faster-whisper"
        return model
    if importlib.util.find_spec("whisper") is not None:
        import whisper
        model = whisper.load_model(name)
        _WHISPER_MODELS[name] = (model, "whisper")
        _WHISPER_MODEL, _WHISPER_BACKEND = model, "whisper"
        return model
    # Windows ARM: whisper.cpp, binario nativo (NEON, build anche per Adreno).
    try:
        from whisper_cpp import WhisperCppModel, find_binary
    except ImportError as exc:
        raise ValueError(_NESSUNA_TRASCRIZIONE) from exc
    if find_binary() is None:
        raise ValueError(_NESSUNA_TRASCRIZIONE)
    model = WhisperCppModel(
        name, progress=_TRANSCRIBE_PROGRESS,
        cancel=_TRANSCRIBE_CANCEL, duration=duration)
    _WHISPER_MODELS[name] = (model, "whisper-cpp")
    _WHISPER_MODEL, _WHISPER_BACKEND = model, "whisper-cpp"
    return model


def _audio_fingerprint(path, model_name):
    h = hashlib.sha256()
    h.update(model_name.encode("utf-8"))
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _transcript_cache_path(path, model_name):
    return _WHISPER_CACHE_DIR / f"{_audio_fingerprint(path, model_name)}.json"


def _load_transcript_cache(path, model_name):
    try:
        data = json.loads(_transcript_cache_path(path, model_name).read_text(encoding="utf-8"))
        if data.get("model") == model_name and int(data.get("chars", 0)) >= 50:
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None


def _save_transcript_cache(path, model_name, title, text, duration):
    try:
        _WHISPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        target = _transcript_cache_path(path, model_name)
        target.write_text(json.dumps({
            "model": model_name, "title": title, "text": text,
            "duration": duration, "chars": len(text), "t": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _split_into_sections(text, title=None):
    """Trasforma testo piatto in sezioni: intestazioni brevi (non frasi)
    diventano heading, il resto paragrafi."""
    text = _ripara_testo(text)
    if not title:
        righe = [r.strip() for r in (text or "").splitlines() if r.strip()]
        # capita che la prima riga sia un residuo di estrazione (es. "È"): il
        # titolo si prende dalla prima riga sensata
        title = next((r for r in righe if len(r) >= 12), righe[0] if righe else "Materiale")[:80]
    sections = []
    cur = {"heading": None, "paras": []}
    head_re = re.compile(r"^\d+[\.\)]\s*|^(?:capitolo|sezione|modulo|lezione|parte)\b",
                         re.IGNORECASE)
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        is_head = (len(ln) <= 70 and not ln.endswith((".", "!", "?"))
                   and head_re.match(ln))
        if is_head:
            if cur["paras"]:
                sections.append(cur)
            cur = {"heading": ln, "paras": []}
        else:
            cur["paras"].append(ln)
    if cur["paras"]:
        sections.append(cur)
    return {"title": title, "sections": sections}




# ------------------------------------------------------------------ PPTX / EPUB
def _clean_xml_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def extract_pptx(path):
    """Estrae il testo delle slide usando solo zipfile/XML standard."""
    import xml.etree.ElementTree as ET
    import zipfile
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    sections, title = [], None
    with zipfile.ZipFile(str(path)) as zf:
        names = sorted((n for n in zf.namelist()
                        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                       key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for n, name in enumerate(names, 1):
            root = ET.fromstring(zf.read(name))
            text = _clean_xml_text(" ".join(
                node.text or "" for node in root.iter()
                if str(node.tag).rsplit("}", 1)[-1] == "t"))
            if text:
                if title is None:
                    title = text[:120]
                sections.append({"heading": f"Slide {n}", "paras": [text]})
    if not sections:
        raise ValueError("Il PowerPoint non contiene testo estraibile.")
    return {"title": title or Path(path).stem, "sections": sections}


def extract_epub(path):
    """Estrae un EPUB basilare leggendo i documenti XHTML nello ZIP."""
    import xml.etree.ElementTree as ET
    import zipfile
    sections, title = [], None
    with zipfile.ZipFile(str(path)) as zf:
        names = [n for n in zf.namelist()
                 if n.lower().endswith((".xhtml", ".html", ".htm"))]
        for name in names:
            try:
                root = ET.fromstring(zf.read(name))
            except ET.ParseError:
                continue
            text = _clean_xml_text(" ".join(x.strip() for x in root.itertext() if x.strip()))
            if text:
                if title is None:
                    title = Path(name).stem.replace("_", " ").strip()
                sections.append({"heading": Path(name).stem, "paras": [text]})
    if not sections:
        raise ValueError("L'EPUB non contiene testo estraibile.")
    return {"title": title or Path(path).stem, "sections": sections}


def _audio_duration(path):
    """Durata in secondi via ffprobe; None se non disponibile."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15, check=True).stdout.strip()
        return float(out)
    except Exception:
        return None


def _format_duration(seconds):
    """Durata leggibile mm:ss (o h:mm:ss)."""
    total = max(0, int(round(float(seconds or 0))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m} min {s:02d} s"


def extract_audio(path):
    """Trascrizione locale con cache hash, avanzamento e cancellazione."""
    p = Path(path)
    if p.suffix.lower() not in (".mp3", ".m4a", ".wav"):
        raise ValueError("Formato audio non supportato: usa MP3, M4A o WAV.")
    model_name = _whisper_model_name()
    duration = _audio_duration(p)
    cached = _load_transcript_cache(p, model_name)
    if cached:
        print(f"  Whisper: trascrizione in cache ({cached['chars']} caratteri).", flush=True)
        if _TRANSCRIBE_PROGRESS:
            _TRANSCRIBE_PROGRESS(100, 0, duration, True, model_name)
        return _split_into_sections(cached["text"], title=cached.get("title") or p.stem)
    dur_txt = f", durata {_format_duration(duration)}" if duration else ""
    print(f"  Whisper modello {model_name} ({min(8, os.cpu_count() or 1)} core): "
          f"trascrizione di {p.name}{dur_txt}…", flush=True)
    model = _load_whisper_model(model_name, duration or 0.0)
    backend = _WHISPER_BACKEND
    started = time.time()
    parts = []
    if backend == "faster-whisper":
        segments, _ = model.transcribe(str(p), language="it")
        for segment in segments:
            if _TRANSCRIBE_CANCEL.is_set():
                raise ValueError("Trascrizione annullata dall'utente.")
            if str(getattr(segment, "text", "")).strip():
                parts.append(segment.text.strip())
            position = float(getattr(segment, "end", 0) or 0)
            pct = min(99, int(position / duration * 100)) if duration else 0
            left = max(0, int((time.time() - started) * (100 - pct) / max(1, pct))) if pct else 0
            if _TRANSCRIBE_PROGRESS:
                _TRANSCRIBE_PROGRESS(pct, left, duration, False, model_name)
    else:
        if _TRANSCRIBE_CANCEL.is_set():
            raise ValueError("Trascrizione annullata dall'utente.")
        text = str(model.transcribe(str(p)).get("text", "")).strip()
        parts = [text]
        if _TRANSCRIBE_PROGRESS:
            _TRANSCRIBE_PROGRESS(100, 0, duration, True, model_name)
    text = " ".join(parts).strip()
    if len(text) < 50:
        raise ValueError("Trascrizione audio troppo breve o non riconosciuta.")
    _save_transcript_cache(p, model_name, p.stem, text, duration)
    return _split_into_sections(text, title=p.stem)

# ------------------------------------------------------------------ DOCX
def extract_docx(path):
    """Estrae titolo + sezioni (heading -> paragrafi) da un .docx (python-docx)."""
    from docx import Document
    d = Document(str(path))
    title, sections, cur = None, [], None

    def flush():
        nonlocal cur
        if cur and cur["paras"]:
            sections.append(cur)
        cur = None

    for p in d.paragraphs:
        txt = p.text.strip()
        if not txt:
            continue
        name = (p.style.name or "").lower()
        is_head = ("heading" in name or "title" in name or name.startswith("titolo")
                   or name.startswith("intestazione"))
        if is_head:
            if title is None:
                title = txt
                continue
            flush()
            cur = {"heading": txt, "paras": []}
        else:
            if cur is None:
                cur = {"heading": None, "paras": []}
            cur["paras"].append(txt)
    flush()

    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                if cur is None:
                    cur = {"heading": None, "paras": []}
                cur["paras"].append(" • ".join(cells))
    flush()

    if title is None and sections and sections[0]["heading"]:
        title = sections[0]["heading"]
    if title is None:
        title = Path(path).stem
    return {"title": title, "sections": sections}


# ------------------------------------------------------------------ PDF
def extract_pdf(path):
    """Testo da PDF via pypdf (opzionale)."""
    try:
        from pypdf import PdfReader
    except Exception:
        raise ValueError("Leggere i PDF richiede pypdf: "
                         "pip install -r requirements-extra.txt")
    reader = PdfReader(str(path))
    pages = []
    for p in reader.pages:
        t = (p.extract_text() or "").strip()
        if t:
            pages.append(t)
    if not pages:
        raise ValueError("Il PDF non contiene testo estraibile (forse è un'immagine).")
    return _split_into_sections("\n\n".join(pages))


# ------------------------------------------------------------------ testo
def extract_text(path):
    """File di testo semplice (.txt/.md) o HTML locale."""
    p = Path(path)
    try:
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
    except OSError as e:
        raise ValueError(f"File non leggibile: {e}")
    if p.suffix.lower() in (".html", ".htm"):
        return parse_html(text)
    return _split_into_sections(text)


# ------------------------------------------------------------------ web
class _HtmlExtractor(HTMLParser):
    """Estrae titolo + sezioni (heading -> paragrafi) da HTML."""

    _BLOCK = {"p", "li", "div", "blockquote", "figcaption", "tr"}
    _HEAD = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.cur_head = None
        self.paras = []
        self.sections = []
        self._buf = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "title":
            self.cur_head = "__TITLE__"
        elif tag in self._HEAD:
            self._flush()
            self.cur_head = tag
        elif tag in self._BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self.cur_head = None
        elif tag in self._HEAD:
            self._flush()
            self.cur_head = None
        elif tag in self._BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        if self.cur_head == "__TITLE__":
            self.title = (self.title or "") + data
        elif self.cur_head:
            self._head_buf = getattr(self, "_head_buf", "")
            self._head_buf += data
        elif data.strip():
            self._buf.append(data)

    def _flush(self):
        txt = re.sub(r"\s+", " ", " ".join(self._buf)).strip()
        self._buf = []
        if self.cur_head and self.cur_head != "__TITLE__":
            head = re.sub(r"\s+", " ", getattr(self, "_head_buf", "")).strip()
            self._head_buf = ""
            if head:
                self.paras.append({"heading": head, "paras": []})
        if txt:
            if self.paras:
                self.paras[-1]["paras"].append(txt)
            else:
                self.paras.append({"heading": None, "paras": [txt]})


def parse_html(html):
    h = _HtmlExtractor()
    try:
        h.feed(html)
    except Exception:
        pass
    title = (h.title or "").strip() or None
    sections = []
    for s in h.paras:
        if s["paras"]:
            sections.append({"heading": s["heading"], "paras": s["paras"]})
    if title is None and sections and sections[0]["heading"]:
        title = sections[0]["heading"]
    if title is None:
        title = "Materiale dal sito web"
    return {"title": title, "sections": sections}


def fetch_url(url):
    """Scarica una pagina web e ne estrae titolo + sezioni."""
    try:
        raw, ctype = _http_get(url)
    except ValueError as e:
        raise ValueError(f"Pagina non scaricabile ({url}): {e}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
    if "html" in ctype.lower():
        return parse_html(text)
    return _split_into_sections(re.sub(r"<[^>]+>", " ", text))


# ------------------------------------------------------------------ YouTube
def _youtube_video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([\w-]{11})", url)
    return m.group(1) if m else None


def _youtube_meta(url):
    """Titolo + descrizione dalla pagina (fallback senza trascrizione)."""
    try:
        raw, _ = _http_get(url, timeout=_FETCH_TIMEOUT)
        html = raw.decode("utf-8", errors="replace")
        title = re.search(r"<title>([^<]+)</title>", html)
        desc = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html)
        return {"title": (title.group(1).replace(" - YouTube", "").strip()
                          if title else None),
                "description": desc.group(1) if desc else ""}
    except Exception:
        return {"title": None, "description": ""}


def _fetch_transcript_lines(vid):
    """Testo della trascrizione del video, compatibile con youtube-transcript-api
    v1 (get_transcript, librerie vecchie) e v2 (fetch, librerie nuove; il primo
    metodo presente decide quale usare)."""
    from youtube_transcript_api import YouTubeTranscriptApi
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        # v1: lista di dict {text, start, duration}
        data = YouTubeTranscriptApi.get_transcript(vid, languages=["it", "en"])
        return [d["text"] for d in data if d.get("text")]
    # v2: FetchedTranscript iterabile di snippet con .text
    fetched = YouTubeTranscriptApi().fetch(vid, languages=["it", "en"])
    return [s.text for s in fetched]


def _youtube_title(url):
    """Titolo del video SENZA scaricare di nuovo la pagina intera: l'endpoint
    oEmbed risponde con un JSON minuscolo. Fallback: _youtube_meta (pagina)."""
    vid = _youtube_video_id(url)
    if not vid:
        return None
    try:
        embed = ("https://www.youtube.com/oembed?url="
                 f"https://www.youtube.com/watch?v={vid}&format=json")
        raw, _ = _http_get(embed, timeout=8)
        data = json.loads(raw.decode("utf-8", "replace"))
        t = (data or {}).get("title")
        if t:
            return t
    except Exception:
        pass
    try:
        return _youtube_meta(url).get("title")
    except Exception:
        return None


def extract_youtube(url):
    """Trascrizione del video (youtube-transcript-api v1/v2) con fallback meta.
    Titolo via oEmbed (leggero) invece di un secondo download della pagina."""
    vid = _youtube_video_id(url)
    if not vid:
        raise ValueError("URL YouTube non riconosciuto: impossibile trovare l'id del video.")
    try:
        lines = _fetch_transcript_lines(vid)
        if not lines:
            raise ValueError("Trascrizione vuota")
        # raggruppa la trascrizione in sezioni ~30 frasi per un contesto utile
        chunk = 30
        sections = []
        for i in range(0, len(lines), chunk):
            blocco = lines[i:i + chunk]
            sections.append({
                "heading": f"Parte {i // chunk + 1} del video",
                "paras": [" ".join(blocco)],
            })
        return {"title": _youtube_title(url) or "Video YouTube", "sections": sections}
    except Exception as e:
        meta = _youtube_meta(url)
        desc = (meta.get("description") or "").strip()
        if desc:
            return _split_into_sections(desc, title=meta.get("title") or "Video YouTube")
        raise ValueError(f"Trascrizione non disponibile e descrizione assente: {e}")


# ------------------------------------------------------------------ testo incollato
def extract_text_raw(text, title=None):
    """Fonte 'incolla-testo' dal pannello: niente file, solo stringa."""
    text = (text or "").strip()
    if len(text) < 50:
        raise ValueError("Testo troppo corto (min 50 caratteri).")
    if len(text) > 200000:
        raise ValueError("Testo troppo lungo (max 200.000 caratteri).")
    return _split_into_sections(text, title=title or "Materiale incollato")


# ------------------------------------------------------------------ dispatch
def extract_source(source):
    """Punto d'ingresso unico: accetta un percorso file oppure un URL."""
    if is_url(source):
        if is_youtube(source):
            return extract_youtube(source)
        return fetch_url(source)
    p = Path(source)
    if not p.exists():
        raise ValueError(f"File non trovato: {source}")
    ext = p.suffix.lower()
    if ext == ".docx":
        return extract_docx(p)
    if ext == ".pptx":
        return extract_pptx(p)
    if ext == ".epub":
        return extract_epub(p)
    if ext in (".mp3", ".m4a", ".wav"):
        return extract_audio(p)
    if ext == ".pdf":
        return extract_pdf(p)
    if ext in SUPPORTED_EXT:
        return extract_text(p)
    raise ValueError(f"Formato non supportato ({ext or 'nessuna estensione'}): "
                     "usa .docx, .pdf, .pptx, .epub, .txt, .md, .html, .mp3, .m4a, .wav "
                     "oppure un URL di sito/YouTube.")