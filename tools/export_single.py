# -*- coding: utf-8 -*-
"""Esporta una lezione già generata in UN SOLO file HTML autonomo.

Il file risultante contiene TUTTO: CSS, JavaScript, dati della lezione e
le tracce audio incorporate (in base64). Si apre con un doppio clic in
qualsiasi browser, funziona anche da pendrive o via email, senza bisogno
del server locale né di Internet.

Uso:
  python tools/export_single.py <cartella_lesson> [--out file.html]
  python new_lesson.py single <cartella_lesson>

Nota: il file può pesare qualche MB (le tracce audio occupano ~4/3 della
loro dimensione in base64); le lezioni molto lunghe danno un file più grande.
"""
import base64
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _data_uri(path):
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:audio/mpeg;base64,{b64}"


def export_single(lesson_dir, out_path=None):
    """Crea <nome>_singola.html dentro la cartella della lezione (o in `out_path`).
    Ritorna il Path del file creato."""
    out = Path(lesson_dir)
    if not (out / "index.html").exists():
        raise ValueError(f"Lezione non trovata: {lesson_dir} (manca index.html)")
    html = (out / "index.html").read_text(encoding="utf-8")
    css = (out / "main.css").read_text(encoding="utf-8")
    data_js = (out / "lesson-data.js").read_text(encoding="utf-8")
    main_js = (out / "main.js").read_text(encoding="utf-8")

    # 1. CSS inline
    html = re.sub(r'<link rel="stylesheet" href="[^"]+">',
                  lambda m: "<style>\n" + css + "\n</style>", html, count=1)

    # 2. audio -> data URI dentro lesson-data.js
    def _audio_repl(m):
        rel = m.group(1)
        p = out / rel
        if p.exists():
            return '"' + _data_uri(p) + '"'
        return m.group(0)

    data_inline = re.sub(r'"\.?/?(assets/audio/narration-\d+\.mp3)"',
                         _audio_repl, data_js)

    # 3. script inline (lesson-data.js poi main.js)
    def _script_repl(m):
        src = m.group(1)
        if src.startswith("lesson-data"):
            return "<script>\n" + data_inline + "\n</script>"
        return "<script>\n" + main_js + "\n</script>"

    html = re.sub(r'<script src="([^"]+)"></script>', _script_repl, html)

    if out_path is None:
        stem = out.name.replace("_lesson", "")
        out_path = out / f"{stem}_singola.html"
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    lesson = None
    out_path = None
    i = 0
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_path = args[i + 1]
            i += 2
        elif not args[i].startswith("--"):
            lesson = args[i]
            i += 1
        else:
            i += 1
    if not lesson:
        print(__doc__)
        sys.exit(1)
    try:
        p = export_single(lesson, out_path)
        mb = p.stat().st_size / 1024 / 1024
        print(f"→ HTML singolo pronto: {p}")
        print(f"  dimensione: {mb:.1f} MB — apri con doppio clic nel browser")
    except Exception as e:  # noqa: BLE001
        print(f"✗ Esportazione fallita: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
