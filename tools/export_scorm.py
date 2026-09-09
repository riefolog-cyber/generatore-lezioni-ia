# -*- coding: utf-8 -*-
"""Esporta una lezione in pacchetto SCORM 1.2 (Moodle/Classroom).

Riusa la cartella *_lesson esistente e aggiunge imsmanifest.xml.
Uso: python tools/export_scorm.py <cartella_lesson> [--out file.zip]
"""
import sys
import zipfile
from pathlib import Path
import xml.sax.saxutils as _sx

BASE = Path(__file__).resolve().parent.parent


def _lesson_dir(name):
    out = Path(name)
    if not out.is_absolute():
        out = BASE / name
    out = out.resolve()
    if not out.is_dir() or not (out / "index.html").exists():
        raise ValueError(f"Lezione non trovata: {name} (manca index.html)")
    return out


def export_scorm(lesson_dir, out_path=None):
    """Crea uno ZIP SCORM 1.2. Ritorna il Path creato."""
    src = _lesson_dir(lesson_dir)
    title = _sx.escape(src.name.replace("_lesson", "").replace("_", " "))
    manifest = f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="MAN-{src.name}" version="1.2"
  xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
  xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2">
  <metadata><schema>ADL SCORM</schema><schemaversion>1.2</schemaversion></metadata>
  <organizations default="ORG-1">
    <organization identifier="ORG-1"><title>{title}</title>
      <item identifier="ITEM-1" identifierref="RES-1"><title>{title}</title>
      </item>
    </organization>
  </organizations>
  <resources><resource identifier="RES-1" type="webcontent"
    adlcp:scormtype="sco" href="index.html">
    <file href="index.html"/>
  </resource></resources>
</manifest>
"""
    if out_path is None:
        out_path = BASE / f"{src.name}_scorm.zip"
    out_path = Path(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("imsmanifest.xml", manifest)
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src).as_posix())
    return out_path


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = None
    for i, a in enumerate(sys.argv[1:]):
        if a == "--out" and i + 1 < len(sys.argv[1:]):
            out = sys.argv[1:][i + 1]
    if not args:
        print(__doc__)
        sys.exit(1)
    try:
        p = export_scorm(args[0], out)
        print(f"→ SCORM pronto: {p} ({p.stat().st_size / 1048576:.1f} MB)")
    except Exception as e:  # noqa: BLE001
        print(f"✗ Export SCORM fallito: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
