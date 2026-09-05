# -*- coding: utf-8 -*-
"""Self-test della lezione: un solo comando per verificare tutto quello che
può rompersi prima di portare la lezione in classe.

Uso:
  python tools/selftest.py                  # testa tutte le lezioni *_lesson
  python tools/selftest.py Nome_lesson      # testa una lezione specifica

Controlli eseguiti:
  1. file essenziali presenti (index.html, main.css, main.js, lesson-data.js)
  2. lesson-data.js valido: slide, attività, durate, timing parole
  3. file audio e sottotitoli referenziati esistono davvero su disco
  4. server effimero: ogni asset risponde HTTP 200
  5. rendering in Chrome headless: slide popolata, dots completi, zero errori JS
"""
import functools
import http.server
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"


def find_lessons():
    return sorted(p for p in BASE.glob("*_lesson")
                  if p.is_dir() and (p / "index.html").exists())


def check_files(L, errs):
    for name in ("index.html", "main.css", "main.js", "lesson-data.js"):
        if not (L / name).exists():
            errs.append(f"{L.name}: manca {name}")


def check_data(L, errs, stats):
    raw = (L / "lesson-data.js").read_text(encoding="utf-8")
    m = re.search(r"window\.LESSON_DATA\s*=\s*(\{.*\});?\s*$", raw, re.S)
    if not m:
        errs.append(f"{L.name}: lesson-data.js non contiene LESSON_DATA valido")
        return None
    try:
        data = json.loads(m.group(1))
    except Exception as e:
        errs.append(f"{L.name}: lesson-data.js non è JSON valido ({e})")
        return None
    slides = data.get("slides") or []
    if not slides:
        errs.append(f"{L.name}: nessuna slide nei dati")
        return None
    stats["slide"] = len(slides)
    acts = 0
    for i, s in enumerate(slides):
        for b in s.get("blocks", []):
            if any(k in b for k in ("quiz", "match", "vf", "seq", "compila",
                                    "scenario", "errore", "flashcards")):
                acts += 1
        if s.get("audio"):
            if not (L / s["audio"].lstrip("./")).exists():
                errs.append(f"{L.name}: slide {i + 1}: audio mancante {s['audio']}")
            if s.get("duration", 0) <= 0:
                errs.append(f"{L.name}: slide {i + 1}: durata audio non valida")
            if not s.get("words"):
                errs.append(f"{L.name}: slide {i + 1}: mancano i timing parole")
        if s.get("caption"):
            if not (L / s["caption"].lstrip("./")).exists():
                errs.append(f"{L.name}: slide {i + 1}: VTT mancante {s['caption']}")
    stats["attivita"] = acts
    stats["audio"] = sum(1 for s in slides if s.get("audio"))
    return data


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(L):
    """Server effimero sulla cartella della lezione. Ritorna (httpd, port)."""
    port = _free_port()
    base_handler = http.server.SimpleHTTPRequestHandler

    class Quiet(base_handler):
        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), functools.partial(Quiet, directory=str(L)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def check_http(L, data, errs):
    httpd, port = _serve(L)
    try:
        import urllib.request
        urls = ["/index.html", "/main.css", "/main.js", "/lesson-data.js"]
        for s in (data or {}).get("slides", []):
            if s.get("audio"):
                urls.append("/" + s["audio"].lstrip("./"))
            if s.get("caption"):
                urls.append("/" + s["caption"].lstrip("./"))
        bad = 0
        for u in urls:
            try:
                r = urllib.request.urlopen(f"http://127.0.0.1:{port}{u}", timeout=5)
                if r.status != 200:
                    bad += 1
                    errs.append(f"{L.name}: HTTP {r.status} su {u}")
            except Exception as e:
                bad += 1
                errs.append(f"{L.name}: HTTP KO su {u} ({e})")
        return bad
    finally:
        httpd.shutdown()
        httpd.server_close()


def check_render(L, n_slides, errs):
    if not Path(CHROME).exists():
        errs.append("(avviso) Chrome non trovato: salto il test di rendering")
        return
    prof = tempfile.mkdtemp(prefix="selftest_chr_")
    httpd, port = _serve(L)
    out_f = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    err_f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    out_f.close(); err_f.close()
    try:
        # su Windows Chrome tiene aperte le pipe dei figli: meglio i file
        with open(out_f.name, "w", encoding="utf-8") as fo, \
                open(err_f.name, "w", encoding="utf-8") as fe:
            proc = subprocess.Popen(
                [CHROME, "--headless", "--disable-gpu", "--virtual-time-budget=5000",
                 f"--user-data-dir={prof}", "--enable-logging=stderr", "--v=0",
                 "--dump-dom", f"http://127.0.0.1:{port}/index.html"],
                stdout=fo, stderr=fe)
            try:
                proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                proc.kill()
        dom = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
        # sintassi del JS generato (node --check, se node è disponibile)
        import shutil as _sh
        node = _sh.which("node")
        if node:
            r = subprocess.run([node, "--check", str(L / "main.js")],
                               capture_output=True, text=True)
            if r.returncode != 0:
                errs.append(f"{L.name}: main.js ha errori di sintassi: "
                            f"{r.stderr.strip()[:160]}")
        if "Pagina non aggiornata" in dom:
            errs.append(f"{L.name}: il player segnala index.html obsoleto")
            return
        if 'id="slide"' not in dom:
            errs.append(f"{L.name}: il contenitore slide non è nel DOM")
            return
        slide = re.search(r'<div id="slide"[^>]*>(.*?)</main>', dom, re.S)
        if not slide or len(slide.group(1)) < 200:
            errs.append(f"{L.name}: la slide risulta vuota (possibile errore JS)")
        dots = len(re.findall(r'<span title=', dom))
        if dots and dots != n_slides:
            errs.append(f"{L.name}: i dots sono {dots}, attese {n_slides} slide")
        console = Path(err_f.name).read_text(encoding="utf-8", errors="replace")
        uncaught = [l for l in console.splitlines() if "Uncaught" in l]
        for l in uncaught[:3]:
            errs.append(f"{L.name}: errore JS: {l.strip()[:160]}")
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(prof, ignore_errors=True)
        for f in (out_f.name, err_f.name):
            try:
                Path(f).unlink()
            except OSError:
                pass


def run(lesson_name=None):
    lessons = [BASE / lesson_name] if lesson_name else find_lessons()
    if not lessons:
        print("Nessuna lezione da testare.")
        return 1
    total_errs = 0
    for L in lessons:
        print(f"\n=== {L.name} ===")
        errs, stats = [], {}
        check_files(L, errs)
        data = None
        if not errs:
            data = check_data(L, errs, stats)
        if data is not None and not [e for e in errs if "manca" in e]:
            check_http(L, data, errs)
            check_render(L, stats.get("slide", 0), errs)
        if stats:
            print(f"  slide: {stats.get('slide', '?')} — attività: {stats.get('attivita', '?')}"
                  f" — audio: {stats.get('audio', '?')}")
        if errs:
            total_errs += len(errs)
            for e in errs:
                print(f"  ✗ {e}")
            print("  ESITO: PROBLEMI")
        else:
            print("  ESITO: OK ✓")
    print(f"\n{'=' * 40}")
    print(f"Errori totali: {total_errs}")
    return 0 if total_errs == 0 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))
