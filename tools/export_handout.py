# -*- coding: utf-8 -*-
"""Dispensa stampabile di una lezione: HTML print-friendly con testi,
quiz (con soluzioni) e glossario. Da browser: Stampa → salva PDF.

Uso: python tools/export_handout.py <cartella_lesson> [--out file.html]
"""
import html as _html
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
esc = lambda s: _html.escape(str(s or ""), quote=True)


def _load(lesson_dir):
    src = Path(lesson_dir)
    if not src.is_absolute():
        src = BASE / lesson_dir
    js = src / "lesson-data.js"
    if not js.exists():
        raise ValueError(f"Lezione non trovata: {lesson_dir}")
    payload = json.loads(re.sub(r"^window\.LESSON_DATA\s*=\s*", "",
                                js.read_text(encoding="utf-8")).rstrip().rstrip(";"))
    return src.resolve(), payload


def export_handout(lesson_dir, out_path=None):
    """Genera Nome_dispensa.html dentro BASE. Ritorna il Path."""
    src, payload = _load(lesson_dir)
    titolo = payload.get("titolo", src.name)
    parts = [f"<h1>{esc(titolo)}</h1>"]
    prof = payload.get("profilo")
    if isinstance(prof, dict):
        parts.append(f"<p><i>Profilo: {esc(prof.get('durata',''))} / "
                     f"{esc(prof.get('livello',''))} / {esc(prof.get('obiettivo',''))}</i></p>")
    for i, s in enumerate(payload.get("slides", []), 1):
        parts.append(f"<h2>{i}. {esc(s.get('title', ''))}</h2>")
        if s.get("narration"):
            parts.append(f"<p>{esc(s['narration'])}</p>")
        for b in s.get("blocks", []):
            if "list" in b:
                parts.append("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in b["list"]) + "</ul>")
            if "p" in b:
                parts.append(f"<p>{esc(b['p'])}</p>")
            if "quiz" in b:
                q = b["quiz"]
                parts.append(f"<p><b>Quiz:</b> {esc(q.get('q',''))}</p><ul>")
                for o in q.get("opts", []):
                    mark = " ✓" if o.get("ok") else ""
                    parts.append(f"<li>{esc(o.get('t',''))}{mark}</li>")
                parts.append("</ul>")
            if "vf" in b:
                for v in b["vf"]:
                    parts.append(f"<p>V/F: {esc(v.get('t',''))} — "
                                 f"<b>{'Vero' if v.get('ok') else 'Falso'}</b></p>")
            if "flashcards" in b:
                for c in b["flashcards"].get("cards", []):
                    parts.append(f"<p><b>{esc(c.get('t',''))}:</b> {esc(c.get('d',''))}</p>")
            if "classifica" in b:
                cl = b["classifica"] or {}
                cats = cl.get("cats", [])
                parts.append(f"<h3>{esc(cl.get('instr') or 'Trascina nella categoria')}</h3>")
                for ci, cat in enumerate(cats):
                    inside = [esc(str(i.get('t', ''))) for i in cl.get("items", [])
                              if i.get("cat") == ci]
                    parts.append(f"<p><b>{esc(str(cat))}:</b> "
                                 + (", ".join(inside) or "—") + "</p>")
            if "glossario" in b:
                for gr in (b["glossario"] or {}).get("groups", []):
                    parts.append(f"<h3>Glossario — {esc(gr.get('modulo','Modulo'))}</h3><ul>")
                    for t in gr.get("terms", []):
                        parts.append(f"<li><b>{esc(t.get('t',''))}:</b> {esc(t.get('d',''))}</li>")
                    parts.append("</ul>")
    body = "\n".join(parts)
    doc = (f"<!DOCTYPE html><html lang='it'><meta charset='utf-8'>"
           f"<title>Dispensa — {esc(titolo)}</title>"
           f"<body style='font-family:sans-serif;max-width:760px;margin:2rem auto'>"
           f"{body}<hr><p><i>Generata dal Simulatore Mindsmith — "
           f"Stampa → Salva come PDF.</i></p></body></html>")
    if out_path is None:
        out_path = BASE / f"{src.name.replace('_lesson','')}_dispensa.html"
    out_path = Path(out_path)
    out_path.write_text(doc, encoding="utf-8")
    return out_path


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    try:
        p = export_handout(args[0])
        print(f"→ Dispensa pronta: {p}")
    except Exception as e:  # noqa: BLE001
        print(f"✗ Dispensa fallita: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
