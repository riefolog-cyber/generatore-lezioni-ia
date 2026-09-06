# -*- coding: utf-8 -*-
"""Avvio unico automatico: controlla ambiente -> installa dipendenze ->
genera le lezioni mancanti dai .docx -> apre la lezione nel browser.

Uso:  python avvia.py          (tutto in automatico)
      python avvia.py gui      (apre la GUI invece del flusso automatico)
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))


def have(module):
    try:
        __import__(module)
        return True
    except Exception:
        return False


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if len(sys.argv) > 1 and sys.argv[1].lower() == "gui":
        import app
        app.main()
        return

    print("=" * 62)
    print("  SIMULATORE MINDSMITH — avvio automatico")
    print("=" * 62)

    # 1. dipendenze Python (auto-install)
    if not have("docx"):
        print("[1/5] Installo dipendenze mancanti (python-docx, edge-tts)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                        str(BASE / "requirements.txt")], check=False)
        if not have("docx"):
            print("✗ python-docx ancora mancante. Esegui a mano: pip install -r requirements.txt")
            sys.exit(1)
    print("[1/5] python-docx OK")

    # 2. ambiente audio: edge-tts è primario, Piper solo di riserva (nessuno è bloccante)
    edge = have("edge_tts")
    print("[2/5] " + ("edge-tts OK (voce neurale primaria)" if edge
                      else "○ edge-tts non installato: audio solo Piper/silenzio"))
    from common import resolve_voice
    voice, _ = resolve_voice()
    print("[2/5] " + (f"voce Piper di riserva OK ({voice.name})" if voice.exists()
                      else f"○ voce Piper di riserva mancante ({voice.name}): "
                           "in assenza di rete l'audio sarà sostituito"))

    # 3. 9router: se attivo la lezione è completa, altrimenti esce in bozza
    from new_lesson import llm_reachable
    print("[3/5] " + ("9router raggiungibile (lezione completa)" if llm_reachable()
                      else "○ 9router non raggiungibile: lezione in modalità ridotta (lo avvio se serve)"))

    # 4. genera le lezioni mancanti (una sola alla volta: blocco anti-concorrenza)
    from new_lesson import sanitize_stem, build_from_docx
    from sources import SUPPORTED_EXT
    from common import setup_logging
    log = setup_logging()
    docs = sorted(p for p in BASE.iterdir()
                  if p.suffix.lower() in SUPPORTED_EXT and p.is_file())
    if not docs:
        print("[4/5] Nessun file di materiale (.docx, .pdf, .txt, .md, .html): "
              "mettine uno in questa cartella e rilancia AVVIA. "
              "Per un link (sito/YouTube) usa: python new_lesson.py build <URL>")
    else:
        for d in docs:
            out = BASE / f"{sanitize_stem(d.stem)}_lesson"
            if out.exists() and (out / "index.html").exists():
                print(f"[4/5] {out.name} già pronta, salto.")
                continue
            print(f"[4/5] Genero {out.name} da {d.name}…")
            try:
                build_from_docx(d, force=True)
            except Exception as e:  # noqa: BLE001
                print(f"✗ Errore su {d.name}: {e}")
                log.error(f"avvia: {d.name}: {e}")

    # 5. apri la prima lezione disponibile
    from start_lesson import list_lessons, serve
    lessons = list_lessons()
    if not lessons:
        print("[5/5] Nessuna lezione generata. Controlla gli errori sopra.")
        sys.exit(1)
    print(f"[5/5] Apro {lessons[0].name}…")
    serve(lessons[0].name)


if __name__ == "__main__":
    main()
