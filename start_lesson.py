# -*- coding: utf-8 -*-
"""Server locale per le lezioni: prova le porte 8341-8350, apre il browser.
Uso:
  python start_lesson.py [cartella_lezione]
  python start_lesson.py --list   (elenca le lezioni *_lesson)
"""
import functools
import http.server
import re
import socket
import sys
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent

try:
    sys.path.insert(0, str(BASE / "tools"))
    from common import load_config
    DEFAULT_PORT = int(load_config().get("porta", 8341))
except Exception:
    DEFAULT_PORT = 8341


def list_lessons():
    return sorted(p for p in BASE.glob("*_lesson") if p.is_dir() and (p / "index.html").exists())


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
    """Come SimpleHTTPRequestHandler ma senza log per ogni richiesta e con
    intestazioni no-cache: il browser deve sempre rileggere i file della
    lezione (evita mix di index.html vecchio + main.js nuovo = pagina bianca)."""

    def log_message(self, *args):
        pass

    def log_error(self, *args):
        pass

    def end_headers(self):
        # Gli MP3 sono i file "pesanti" e il player li pre-carica (traccia
        # successiva). Con `no-cache` (ri-validazione, NON no-store) il browser
        # li riusa dopo un semplice 304 e il passaggio tra slide è immediato;
        # se la lezione viene rigenerata, il nuovo mtime del file forza un 200
        # con contenuto fresco. Hanno già Last-Modified/If-Modified-Since via
        # SimpleHTTPRequestHandler.send_head(). Il resto (html/js/css/dati)
        # resta no-store: la pagina non può mai arrivare "stantia".
        if self.path.lower().endswith(".mp3"):
            self.send_header("Cache-Control", "private, no-cache")
        else:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.send_header("Accept-Ranges", "bytes")   # seek fluido nella barra audio
        super().end_headers()


class _RangeHandler(_QuietHandler):
    """Serve i file MP3 con supporto Range (richieste parziali): senza, alcuni
    browser non permettono di spostare il cursore della traccia né di riprendere
    il download. Solo per l'audio, il resto va bene così com'è."""

    def do_GET(self):
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
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            start = int(m.group(1) or 0) if m else 0
            end = int(m.group(2)) if m and m.group(2) else size - 1
            start = max(0, min(start, size - 1))
            end = max(start, min(end, size - 1))
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


def _hub_page():
    """Pagina indice che elenca tutte le lezioni disponibili (solo quelle,
    nient'altro del progetto è esposto)."""
    lessons = list_lessons()
    items = "".join(
        f'<a class="card" href="/{l.name}/index.html">'
        f'<span class="ic">🎓</span><span class="nm">{l.name.replace("_lesson", "")}</span>'
        f'<span class="op">Apri →</span></a>'
        for l in lessons)
    if not items:
        items = '<p class="empty">Nessuna lezione generata: lancia AVVIA.bat per crearne una.</p>'
    return ("<!DOCTYPE html><html lang=\"it\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Lezioni disponibili</title><style>"
            "body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1220;color:#eef2ff;"
            "margin:0;padding:40px 20px}"
            "h1{text-align:center;font-size:26px;margin-bottom:6px}"
            ".sub{text-align:center;color:#93a0c4;margin-bottom:30px;font-size:14px}"
            ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));"
            "gap:14px;max-width:900px;margin:0 auto}"
            ".card{display:flex;align-items:center;gap:12px;padding:16px 18px;border-radius:14px;"
            "background:#161d33;border:1px solid #2a3554;color:#eef2ff;text-decoration:none;"
            "transition:transform .15s,border-color .2s,box-shadow .2s}"
            ".card:hover{transform:translateY(-2px);border-color:#5b7bd5;"
            "box-shadow:0 10px 26px rgba(0,0,0,.35)}"
            ".ic{font-size:24px}.nm{font-weight:700;flex:1}.op{color:#8ecaff;font-size:13px;font-weight:700}"
            ".empty{grid-column:1/-1;text-align:center;color:#93a0c4;padding:30px;border:1px dashed #2a3554;"
            "border-radius:14px}</style></head><body>"
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
                first = self.path.lstrip("/").split("/", 1)[0]
                if first not in _allowed:
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
