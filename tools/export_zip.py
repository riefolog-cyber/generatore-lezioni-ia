# -*- coding: utf-8 -*-
"""Esporta la lezione come ZIP pronta da condividere:
cartella lezione + avvia_qui.py + start_lesson.bat + LEGGIMI.txt.

Il destinatario NON deve installare nulla:
  - doppio clic su start_lesson.bat  (apre un server locale + la lezione nel browser)
  - oppure apre direttamente index.html (funziona anche offline)

Uso: python tools/export_zip.py [nome_lezione]
"""
import sys, zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

AVVIA_PY = '''# -*- coding: utf-8 -*-
"""Avvia la lezione esportata: server locale su una porta libera + browser.
Nessuna dipendenza esterna: solo standard library."""
import http.server
import os
import socket
import sys
import webbrowser

os.chdir(os.path.dirname(os.path.abspath(__file__)))


def _porta_libera():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


class _Silenzioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def log_error(self, *args):
        pass


def main():
    porta = _porta_libera()
    url = f"http://localhost:{porta}/index.html"
    print("=" * 56)
    print("  Lezione Mindsmith")
    print(f"  URL: {url}")
    print("  Chiudi questa finestra per fermare il server.")
    print("=" * 56)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", porta), _Silenzioso)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\\nServer fermato.")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
'''

BAT = """@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
title Lezione Mindsmith
where python >nul 2>nul
if errorlevel 1 (
  echo Python non trovato.
  echo Apri direttamente index.html nel browser: funziona lo stesso.
  pause
  exit /b 1
)
python avvia_qui.py
pause
"""

LEGGIMI = """LEZIONE: {name}

COME AVVIARLA (2 modi)
  1) Doppio clic su start_lesson.bat
     -> apre un piccolo server locale e la lezione nel browser
        (serve Python installato)
  2) Apri direttamente index.html con un doppio clic
     -> funziona anche senza Python e offline

CONTENUTO DELLA LEZIONE
  - Player interattivo: slide narrate con audio sincronizzato (ogni passaggio
    ha la propria voce, con sottotitoli che seguono la lettura)
  - Attività: quiz, Vero/Falso, Compila il vuoto, Ordina la sequenza,
    Cosa faresti?, Trova l'errore, abbinamenti, sfida finale lampo
  - Punteggio a stelle e medaglia finale, tema chiaro/scuro
  - Non serve internet e non serve installare niente per vederla

CONSIGLI PER IL DOCENTE
  - In classe: proiettare e rispondere insieme, oppure far avanzare gli
    studenti in autonomia (la voce guida ogni passaggio).
  - I tasti freccia cambiano slide, Spazio mette in pausa l'audio,
    R riascolta la narrazione.
"""


def list_lessons():
    return sorted(p.name for p in BASE.glob('*_lesson') if p.is_dir() and (p / 'index.html').exists())


def export(lesson_name=None):
    if not lesson_name:
        lessons = list_lessons()
        if not lessons:
            print('ERRORE: nessuna cartella *_lesson trovata')
            return None
        lesson_name = lessons[0]
        if len(lessons) > 1:
            print('Lezioni disponibili: ' + ', '.join(lessons))
            print(f"Uso la prima: {lesson_name} (passa il nome per sceglierne un'altra)")
    src = BASE / lesson_name
    if not src.exists():
        print(f'ERRORE: cartella {lesson_name} non trovata')
        return None
    out = BASE / f'{lesson_name}_export.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        top = lesson_name
        for f in sorted(src.rglob('*')):
            if f.is_file():
                z.write(f, f'{top}/{f.relative_to(src)}')
        # launcher autocontenuti dentro la stessa cartella lezione
        z.writestr(f'{top}/avvia_qui.py', AVVIA_PY)
        z.writestr(f'{top}/start_lesson.bat', BAT)
        z.writestr(f'{top}/LEGGIMI.txt', LEGGIMI.format(name=lesson_name))
    mb = out.stat().st_size / 1024 / 1024
    print(f'Export OK: {out.name} ({mb:.1f} MB)')
    print(f'  → apri {lesson_name}/start_lesson.bat oppure {lesson_name}/index.html')
    return out


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] in ('--list', '-l'):
        for n in list_lessons():
            print(n)
    else:
        name = sys.argv[1] if len(sys.argv) > 1 else None
        export(name)
