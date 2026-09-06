# -*- coding: utf-8 -*-
"""Controllo ambiente prima di generare/avviare lezioni.
Uso: python check_env.py  (exit 0 = pronto, 1 = manca qualcosa di grave)

Bloccanti (senza questi non si parte):  Python >= 3.10, python-docx.
Avvisi (il flusso continua, con qualità ridotta): edge-tts, voce Piper, ffmpeg.
"""
import importlib.metadata
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "tools"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from common import load_config, resolve_voice  # noqa: E402

ok = True
avvisi = 0


def bloccante(label, cond, hint=""):
    """✗ = manca qualcosa senza cui non si può generare (exit 1)."""
    global ok
    print(("✓ " if cond else "✗ ") + label + ("" if cond else f"  → {hint}"))
    if not cond:
        ok = False


def avviso(label, cond, hint=""):
    """○ = qualità/extra ridotti, ma il flusso continua."""
    global avvisi
    print(("✓ " if cond else "○ ") + label + ("" if cond else f"  → {hint}"))
    if not cond:
        avvisi += 1


print("=== Controllo ambiente ===")
print(f"Python: {sys.version.split()[0]}")
bloccante("Python >= 3.10", sys.version_info >= (3, 10))


def _ver(modulo, distribuzione):
    try:
        return importlib.metadata.version(distribuzione)
    except Exception:
        try:
            m = __import__(modulo)
            return getattr(m, "__version__", "?")
        except Exception:
            return None


def _check_modulo(modulo, distribuzione, label, bloccante_=False, hint=""):
    ver = _ver(modulo, distribuzione)
    fn = bloccante if bloccante_ else avviso
    fn(f"{label} ({ver})" if ver else label, bool(ver), hint)
    return ver

_check_modulo("docx", "python-docx", "python-docx installato (lettura .docx)",
              bloccante_=True, hint="pip install -r requirements.txt")
_check_modulo("edge_tts", "edge-tts", "edge-tts: voce neurale primaria",
              hint="pip install edge-tts — senza, audio solo da Piper locale o silenzio")
_check_modulo("pypdf", "pypdf", "pypdf (materiale PDF)",
              hint="pip install -r requirements-extra.txt — senza, i .pdf non sono "
              "leggibili come materiale di partenza")
_check_modulo("youtube_transcript_api", "youtube-transcript-api",
              "youtube-transcript-api (trascrizioni YouTube)",
              hint="pip install -r requirements-extra.txt — senza, i video YouTube "
              "usano solo titolo e descrizione")

voice, _ = resolve_voice()
avviso(f"voce Piper di riserva ({voice.name})", voice.exists(),
       f"file mancante: {voice} — senza, in assenza di rete le slide avranno "
       "audio sostituito (durata stimata)")

avviso("ffmpeg/ffprobe (durata audio, fallback)", bool(shutil.which("ffmpeg")),
       "serve per misurare la durata e per il fallback Piper")
avviso("ffprobe (durata audio)", bool(shutil.which("ffprobe")),
       "installalo con ffmpeg: senza, durate stimate invece che misurate")

cfg = load_config()
print(f"Config: llm={cfg.get('llm_url')} modello={cfg.get('llm_model')} | "
      f"voce edge={cfg.get('edge_voice')} @ {cfg.get('edge_rate')} | "
      f"piper={cfg.get('voice')} | tema={cfg.get('theme')} | porta={cfg.get('porta')} | "
      f"tts workers={cfg.get('tts_workers', 4)} retries={cfg.get('tts_retries', 2)}")

import urllib.request  # noqa: E402
try:
    with urllib.request.urlopen(f"{cfg['llm_url'].rstrip('/')}/models", timeout=4) as r:
        reachable = r.status == 200
except Exception:
    reachable = False
print(("✓ 9router raggiungibile" if reachable else
       "○ 9router non raggiungibile (lezioni in modalità ridotta, senza attività)"))

print("=== " + ("PRONTO" if ok else "MANCA QUALCOSA DI ESSENZIALE — vedi sopra") + " ===")
if avvisi:
    print(f"({avvisi} avviso/i non bloccanti: la qualità può essere ridotta, ma si può procedere)")
sys.exit(0 if ok else 1)
