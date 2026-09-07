# -*- coding: utf-8 -*-
"""Estrazione del materiale di partenza da fonti diverse.

Supporta:
- file .docx (python-docx)
- file .pdf (pypdf, opzionale)
- file di testo .txt / .md / .html locale
- URL di siti web (fetch + parsing HTML)
- video YouTube (trascrizione via youtube-transcript-api, opzionale;
  fallback: titolo + descrizione)

Ogni estrattore ritorna la stessa struttura di extract_docx:
{"title": str, "sections": [{"heading": str|None, "paras": [str, ...]}]}
così il resto della pipeline non cambia.
"""
import json
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED_EXT = {".docx", ".pdf", ".txt", ".md", ".html", ".htm"}

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


def _split_into_sections(text, title=None):
    """Trasforma testo piatto in sezioni: intestazioni brevi (non frasi)
    diventano heading, il resto paragrafi."""
    title = title or (text.strip().splitlines()[0].strip()[:80] if text.strip() else "Materiale")
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
    if ext == ".pdf":
        return extract_pdf(p)
    if ext in SUPPORTED_EXT:
        return extract_text(p)
    raise ValueError(f"Formato non supportato ({ext or 'nessuna estensione'}): "
                     "usa .docx, .pdf, .txt, .md, .html oppure un URL di sito/YouTube.")