# -*- coding: utf-8 -*-
"""Esporta una lezione in pacchetto SCORM 1.2 (Moodle/Classroom).

Riusa la cartella *_lesson esistente e aggiunge:
- imsmanifest.xml (elencando TUTTI i file del pacchetto);
- un adapter SCORM (scorm-adapter.js) iniettato in index.html: manda all'LMS
  (Moodle e amici) punteggio (cmi.core.score.raw), stato di
  completamento (cmi.core.lesson_status), posizione (cmi.core.lesson_location)
  e tempo di sessione (cmi.core.session_time).
Fuori dall'LMS l'adapter resta silenzioso (nessuna API = nessun effetto).

Uso: python tools/export_scorm.py <cartella_lesson> [--out file.zip]
"""
import sys
import zipfile
from pathlib import Path
import xml.sax.saxutils as _sx

BASE = Path(__file__).resolve().parent.parent

SCORM_ADAPTER = """/* Adapter SCORM 1.2 — iniettato da tools/export_scorm.py.
   Al caricamento della pagina cerca l'API dell'LMS (standard SCORM 1.2), la
   inizializza e sostituisce l'hook reportProgress del player: ogni aggiornamento
   punteggio manda all'LMS score.raw, lesson_status, lesson_location e
   session_time. Fuori dall'LMS (apertura diretta del file) non fa nulla. */
(function () {
  'use strict';
  function findAPI(win) {
    var tries = 0;
    while (win && !win.API && win.parent && win.parent !== win && tries < 20) {
      win = win.parent; tries++;
    }
    return win && win.API ? win.API : null;
  }
  var api = findAPI(window);
  if (!api) return;                       // nessun LMS: il player resta standalone

  function init() {
    try { api.LMSInitialize(''); } catch (e) { return; }
    try { api.LMSSetValue('cmi.core.student_name', api.LMSGetValue('cmi.core.student_name') || ''); } catch (e) {}
    window.reportProgress = function (p) {
      if (!p) return;
      try {
        if (p.totale > 0) api.LMSSetValue('cmi.core.score.raw', String(Math.round(p.pct)));
        api.LMSSetValue('cmi.core.score.min', '0');
        api.LMSSetValue('cmi.core.score.max', '100');
        api.LMSSetValue('cmi.core.lesson_location', 'slide-' + p.slide);
        api.LMSSetValue('cmi.core.session_time',
          String(Math.floor(p.tempo_s / 3600)).padStart(2, '0') + ':' +
          String(Math.floor(p.tempo_s / 60) % 60).padStart(2, '0') + ':' +
          String(p.tempo_s % 60).padStart(2, '0'));
        api.LMSSetValue('cmi.core.lesson_status',
          p.completata ? (p.totale > 0 && p.pct >= 60 ? 'passed' : 'completed') : 'incomplete');
        api.LMSCommit('');
      } catch (e) { /* mai bloccare il player per un errore LMS */ }
    };
    try { api.LMSSetValue('cmi.core.lesson_status', 'incomplete'); api.LMSCommit(''); } catch (e) {}
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  window.addEventListener('beforeunload', function () {
    try { api.LMSFinish(''); } catch (e) {}
  });
})();
"""


def _lesson_dir(name):
    out = Path(name)
    if not out.is_absolute():
        out = BASE / name
    out = out.resolve()
    if not out.is_dir() or not (out / "index.html").exists():
        raise ValueError(f"Lezione non trovata: {name} (manca index.html)")
    return out


def build_manifest(src, files):
    """Manifest SCORM 1.2 che elenca tutti i file del pacchetto.
    `files` = percorsi relativi (stringhe posix) dei file nello zip."""
    title = _sx.escape(src.name.replace("_lesson", "").replace("_", " "))
    file_tags = "\n".join(f"    <file href=\"{_sx.escape(str(f))}\"/>"
                          for f in files)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="MAN-{_sx.escape(src.name)}" version="1.2"
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
{file_tags}
  </resource></resources>
</manifest>
"""


def _inject_index(index_html):
    """Inietta l'adapter SCORM nel player (idempotente)."""
    if "scorm-adapter" in index_html:
        return index_html, False
    tag = "<script src=\"scorm-adapter.js\"></script>"
    if "</head>" in index_html:
        return index_html.replace("</head>", f"{tag}</head>", 1), True
    # index.html senza </head>: inietta all'inizio del primo <script>
    if "<script" in index_html:
        i = index_html.index("<script")
        return index_html[:i] + tag + index_html[i:], True
    return index_html, False


def export_scorm(lesson_dir, out_path=None):
    """Crea lo ZIP SCORM 1.2 con adapter di tracciamento. Ritorna il Path creato."""
    src = _lesson_dir(lesson_dir)
    index_html = (src / "index.html").read_text(encoding="utf-8")
    index_html, injected = _inject_index(index_html)

    if out_path is None:
        out_path = BASE / f"{src.name}_scorm.zip"
    out_path = Path(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        files = sorted(f.relative_to(src).as_posix() for f in src.rglob("*")
                       if f.is_file() and f.name != "report.html")
        if injected:
            files.append("scorm-adapter.js")
        z.writestr("imsmanifest.xml", build_manifest(src, files))
        z.writestr("scorm-adapter.js", SCORM_ADAPTER)
        z.writestr("index.html", index_html)
        for rel in files:
            if rel in ("index.html", "scorm-adapter.js"):
                continue  # già scritti sopra (versione con adapter)
            z.write(src / rel, rel)
    return out_path


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = None
    argv1 = sys.argv[1:]
    for i, a in enumerate(argv1):
        if a == "--out" and i + 1 < len(argv1):
            out = argv1[i + 1]
    if not args:
        print(__doc__)
        sys.exit(1)
    try:
        p = export_scorm(args[0], out)
        print(f"→ SCORM pronto: {p} ({p.stat().st_size / 1048576:.1f} MB)")
        print("  Caricalo su Moodle come \"Pacchetto SCORM\": punteggio e "
              "completamento arrivano automaticamente al registro voti.")
    except Exception as e:  # noqa: BLE001
        print(f"✗ Export SCORM fallito: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
