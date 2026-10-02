# -*- coding: utf-8 -*-
"""Server locale per le lezioni: prova le porte 8341-8350, apre il browser.
Uso:
  python start_lesson.py [cartella_lezione]
  python start_lesson.py --list   (elenca le lezioni *_lesson)
"""
import functools
import gzip
import html
import http.server
import io
import os
import re
import socket
import sys
import urllib.parse
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent

try:
    sys.path.insert(0, str(BASE / "tools"))
    from common import load_config
    from http_safety import safe_request_path
    DEFAULT_PORT = int(load_config().get("porta", 8341))
except Exception:
    DEFAULT_PORT = 8341
    safe_request_path = None  # guardia anti-traversal non disponibile


def list_lesson_dirs():
    """Cartelle *_lesson con l'index del player, anche se i dati non ci sono."""
    return sorted(p for p in BASE.glob("*_lesson") if p.is_dir() and (p / "index.html").exists())


def list_lessons():
    """Lezioni COMPLETE, cioè apribili.

    Serve anche `lesson-data.js`: il player lo carica per primo e, se manca,
    lo studente cade su un 404 e su una pagina bianca. Una build interrotta
    (Ctrl+C, rete caduta, errore in sintesi) scrive il player prima dell'audio
    e i dati solo alla fine: la cartella a meta' finiva cosi' nell'indice e
    nel pannello come se fosse pronta. Il pannello la mostra lo stesso, ma
    etichettata "incompleta" e senza link (vedi tools/lesson_admin.lesson_info).
    """
    return [p for p in list_lesson_dirs() if (p / "lesson-data.js").is_file()]


def find_port(start=DEFAULT_PORT, tries=10):
    """Prima porta libera tra `start` e `start + tries`, provando a legarla
    davvero (bind): un semplice test di connessione può dare falsi positivi.
    Ritorna None se non c'è nessuna porta disponibile."""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Come SimpleHTTPRequestHandler ma senza log per ogni richiesta, con
    compressione gzip e intestazioni di cache coerenti col versioning.

    Tre livelli, per non confondere "cambia spesso" con "cambia sempre":

    - `index.html` → `no-store`: non può mai arrivare stantio, perché è il
      documento che decide quali versioni di css/js caricare;
    - file con `?v=<sha1>` → `immutable` per un anno: l'hash cambia a ogni
      rigenerazione, quindi l'URL cambia e la cache può essere tenuta per
      sempre (seconda apertura della stessa lezione: 0 byte, prima li rileggeva
      tutti con un 304 ciascuno);
    - tutto il resto → `no-cache`: si rivalida, ma senza scaricare di nuovo
      se il file non è cambiato.
    """

    _GZIP_TYPES = {".html", ".css", ".js", ".json", ".svg", ".webmanifest", ".vtt"}
    _IMMUTABLE = (".css", ".js", ".webmanifest")

    def log_message(self, *args):
        pass

    def log_error(self, *args):
        pass

    def _cache_headers(self):
        path = (getattr(self, "path", "") or "").split("?", 1)[0].lower()
        if path.endswith(".mp3"):
            self.send_header("Cache-Control", "private, no-cache")
            return
        ext = os.path.splitext(path)[1]
        if ext in self._IMMUTABLE and "?v=" in (getattr(self, "path", "") or ""):
            # URL content-hashed: la cache può essere tenuta, cambia l'URL al cambio
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            return
        if path.endswith((".html", "/")) or not ext:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            return
        self.send_header("Cache-Control", "private, no-cache")

    def _vuole_gzip(self, ext):
        try:
            enc = (self.headers.get("Accept-Encoding") or "").lower()
            return "gzip" in enc and ext in self._GZIP_TYPES
        except Exception:
            return False

    def send_header(self, keyword, value):
        # Con il gzip attivo Content-Length deve valere la lunghezza del corpo
        # COMPRESSO, non quella del file. Si corregge qui, sul percorso
        # standard di SimpleHTTPRequestHandler: costruire le intestazioni a
        # mano produceva una risposta che Chrome rifiutava per i fogli di stile
        # (caricati ma con ZERO regole → pagina bianca e disordinata).
        if keyword == 'Content-Length' and getattr(self, "_gz", None) is not None:
            value = str(len(self._gz))
        super().send_header(keyword, value)

    def end_headers(self):
        self._cache_headers()
        if getattr(self, "_gz", None) is None:
            # Accept-Ranges ha senso solo su risposte non compresse: i byte
            # citati dai Range sarebbero quelli codificati, non quelli del file
            self.send_header("Accept-Ranges", "bytes")
        else:
            self.send_header("Accept-Ranges", "none")
        if getattr(self, "_gz", None) is not None:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        super().end_headers()

    def send_head(self):
        """Comprime i tipi testuali se il client accetta gzip.

        main.css (57 KB) + main.js (107 KB) + lesson-data.js (22 KB) +
        index.html sono ~192 KB di testo quasi incomprimibile: gzip li porta a
        ~47 KB. Su una classe da 30 tablet è la differenza fra una lezione che
        si apre subito e una che arranca sul Wi-Fi.

        Il corpo viene compresso PRIMA di `super().send_head()`, così
        Content-Length riflette la lunghezza reale.
        """
        self._gz = None
        path = (getattr(self, "path", "") or "").split("?", 1)[0].lower()
        ext = os.path.splitext(path)[1]
        if self._vuole_gzip(ext):
            try:
                body = self._read_file_for_gzip()
                if body is not None:
                    buf = io.BytesIO()
                    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
                        gz.write(body)
                    payload = buf.getvalue()
                    # su file piccoli il gzip può essere più grande: non serve
                    if len(payload) < len(body):
                        self._gz = payload
            except Exception:
                self._gz = None
        return super().send_head()

    def copyfile(self, source, outputfile):
        if getattr(self, "_gz", None) is not None:
            outputfile.write(self._gz)
            return
        super().copyfile(source, outputfile)

    def _read_file_for_gzip(self):
        """Legge il file richiesto come fa SimpleHTTPRequestHandler, ma in
        memoria. Nessun path traversal: translate_path è già vincolata."""
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
            if not os.path.isfile(path):
                return None
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()


class _RangeHandler(_QuietHandler):
    """Serve i file MP3 con supporto Range (richieste parziali): senza, alcuni
    browser non permettono di spostare il cursore della traccia né di riprendere
    il download. Solo per l'audio, il resto va bene così com'è."""

    def do_GET(self):
        # anti-traversal prima di qualsiasi translate_path (guardia condivisa
        # con panel.py: la conclusiva del pannello non deve vivere in questo file)
        if safe_request_path is None:
            self.send_error(500, "Guardia anti-traversal non disponibile")
            return
        try:
            parsed = urllib.parse.urlparse(self.path)
            if safe_request_path(parsed.path, BASE) is None:
                self.send_error(404, "Non disponibile")
                return
        except Exception:
            self.send_error(404, "Non disponibile")
            return
        if not self.translate_path(self.path).lower().endswith(".mp3"):
            return super().do_GET()
        rng = self.headers.get("Range")
        if not rng:
            return super().do_GET()
        try:
            f = open(self.translate_path(self.path), "rb")
        except OSError:
            return super().do_GET()
        with f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                # file vuoto: qualunque 206 prometterebbe piu' byte di quanti
                # esistano e il browser (che si aspetta l'audio) si bloccherebbe
                # aspettando dati che non arriveranno
                self.send_error(416, "File vuoto")
                return
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if not m:
                return super().do_GET()
            primo, secondo = m.group(1), m.group(2)
            if primo == "" and secondo == "":
                return super().do_GET()
            if primo == "":
                # suffisso: "bytes=-500" = ULTIMI 500 byte. Prima partiva da 0
                # e serviva 0-500, cioe' l'inizio del file invece della coda.
                n = int(secondo)
                if n <= 0:
                    self.send_error(416, "Range non soddisfacibile")
                    return
                start = max(0, size - n)
                end = size - 1
            else:
                start = int(primo)
                end = int(secondo) if secondo else size - 1
                if start >= size:
                    self.send_error(416, "Range non soddisfacibile")
                    return
                end = min(end, size - 1)
            if end < start:
                self.send_error(416, "Range non soddisfacibile")
                return
            f.seek(start)
            self.send_response(206)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if self.command != "HEAD":
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)


def _is_safe_request_path(path, allowed_names):
    """Anti-traversal: decodifica %2e, blocca .. e verifica che il path risolto resti dentro allowed.

    Usa la guardia condivisa `http_safety.safe_request_path`: la stessa
    logica era prima implementata qui E in panel.py, con criteri leggermente
    diversi (drift garantito). In più la conclusiva del pannello viveva in
    questo file, che ha una responsibility completamente diversa.
    """
    try:
        dec = urllib.parse.unquote(path)
    except Exception:
        return False
    parts = [p for p in dec.strip("/").split("/") if p]
    if not parts:
        return True  # "/" o "/index.html" gestiti prima
    if safe_request_path is None:
        return False
    first = parts[0]
    if first not in allowed_names:
        return False
    target = safe_request_path(path, BASE)
    if target is None:
        return False
    try:
        # deve restare dentro BASE/first
        target.relative_to((BASE / first).resolve())
        (BASE / first).resolve().relative_to(BASE.resolve())
    except Exception:
        return False
    return True


_HUB_CSS_FILE = Path(__file__).resolve().parent / "tools" / "hub.css"


def _hub_css():
    """Stili della pagina indice.

    Erano ~15 regole CSS dentro una stringa Python: non formattate, non
    ispezionabili, e un errore di sintassi non poteva essere rilevato da
    nessuno. Ora stanno in `tools/hub.css`.
    """
    try:
        return _HUB_CSS_FILE.read_text(encoding="utf-8")
    except OSError:
        # senza il file la pagina resta usabile, solo senza stile
        return "body{font-family:system-ui,sans-serif}"


def _hub_page():
    """Pagina indice che elenca tutte le lezioni disponibili (solo quelle,
    nient'altro del progetto è esposto)."""
    lessons = list_lessons()
    items = "".join(
        f'<a class="card" href="/{html.escape(l.name)}/index.html">'
        f'<span class="ic">🎓</span><span class="nm">{html.escape(l.name.replace("_lesson", ""))}</span>'
        f'<span class="op">Apri →</span></a>'
        for l in lessons)
    if not items:
        items = '<p class="empty">Nessuna lezione generata: lancia AVVIA.bat per crearne una.</p>'
    return ("<!DOCTYPE html><html lang=\"it\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Lezioni disponibili</title><style>"
            + _hub_css()
            + "</style></head><body>"
            '<h1>🎓 Lezioni disponibili</h1><div class="sub">Scegli la lezione da aprire</div>'
            f'<div class="grid">{items}</div></body></html>')


def serve(lesson_dir=None, port=None):
    if lesson_dir is None:
        lessons = list_lessons()
        if not lessons:
            print("Nessuna lezione trovata. Genera prima una lezione.")
            sys.exit(1)
        lesson_dir = lessons[0].name
        if len(lessons) > 1:
            print(f"  ({len(lessons)} lezioni trovate: la pagina iniziale è l'indice)")
            lesson_dir = None
    if lesson_dir is not None:
        lesson = BASE / lesson_dir
        if not lesson.exists():
            print(f"ERRORE: cartella {lesson_dir} non trovata.")
            lessons = list_lessons()
            if lessons:
                print("Lezioni disponibili:")
                for l in lessons:
                    print(f"  - {l.name}")
            sys.exit(1)
    port = port or find_port()
    if port is None:
        print(f"ERRORE: nessuna porta libera tra {DEFAULT_PORT} e "
              f"{DEFAULT_PORT + 9} (tutte occupate). Chiudi gli altri server "
              "e riprova, oppure imposta un'altra porta in config.json.")
        sys.exit(1)
    if lesson_dir is None:
        # hub: indice delle lezioni, i link puntano alle singole cartelle.
        # Sicurezza: vengono esposte SOLO le cartelle *_lesson; qualsiasi altro
        # file del progetto (config.json, sorgenti, log, .git…) risponde 404.
        _allowed = {l.name for l in list_lessons()}
        url = f"http://localhost:{port}/"
        print("=" * 60)
        print("  Indice lezioni (hub)")
        print(f"  URL: {url}")
        print("  Premi CTRL+C per fermare il server.")
        print("=" * 60)
        hub = _hub_page()

        class _HubHandler(_QuietHandler):
            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    body = hub.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if not _is_safe_request_path(self.path, _allowed):
                    self.send_error(404, "Non disponibile")
                    return
                super().do_GET()


        handler_cls = _HubHandler
        directory = str(BASE)
    else:
        url = f"http://localhost:{port}/index.html"
        print("=" * 60)
        print(f"  Lezione: {lesson_dir}")
        print(f"  URL: {url}")
        print("  Premi CTRL+C per fermare il server.")
        print("=" * 60)
        handler_cls = _RangeHandler
        directory = str(BASE / lesson_dir)
    handler = functools.partial(handler_cls, directory=directory)
    # prima si lega la porta, poi si apre il browser: nessuna corsa al primo
    # caricamento (il server è già in ascolto quando la pagina viene aperta)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    # indirizzo nella rete locale: da tablet/telefono sulla stessa Wi-Fi basta
    # quel numero per aprire la lezione (utile in classe)
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        _ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        _ip = None
    if _ip and not _ip.startswith("127."):
        print(f"  Da tablet/telefono sulla stessa rete Wi-Fi: http://{_ip}:{port}/")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer fermato.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    if args and args[0] == "--list":
        for l in list_lessons():
            print(l.name)
    else:
        name = args[0] if args else None
        # senza argomento: se c'è una sola lezione la serve diretta,
        # con più lezioni mostra l'indice
        serve(name)
