# -*- coding: utf-8 -*-
"""Trascrizione audio locale con whisper.cpp (binario nativo, senza pip).

Perche' esiste: `faster-whisper` dipende da CTranslate2, che non pubblica wheel
Windows ARM64, quindi su Snapdragon non e installabile. whisper.cpp e C++ puro,
ha build ARM64 ufficiali e si usa da riga di comando: non serve alcun pacchetto
pip.

Build consigliata su Windows ARM64: `whisper-bin-win-cpu-arm64.zip`. Esiste anche
`whisper-bin-win-opencl-adreno-arm64.zip` (accelerazione GPU sull'Adreno), ma
richiede i driver OpenCL del produttore installati: senza la cartella
System32/OpenCL/vendors il binario non parte (errore 0xC0000135, DLL mancante).
Per questo di default si usa la build CPU, che e gia' molto veloce
(NEON + ARM FMA + INT8 attivi).

Non sostituisce faster-whisper: e un backend AGGIUNTIVO, scelto solo se
faster-whisper non e installato. Su x64 resta preferito faster-whisper.

Dipendenze: solo stdlib. Serve `ffmpeg` per convertire l'audio in WAV 16 kHz
monofono, che e il formato atteso da whisper.cpp.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
CACHE_DIR = _ROOT / ".whisper_cache"
BIN_DIR = CACHE_DIR / "whisper-cpp"
MODEL_DIR = CACHE_DIR / "ggml"

# Nomi con cui il binario puo' chiamarsi nelle build ufficiali.
_EXE_NAMES = ("whisper-cli.exe", "whisper.exe", "main.exe")

# Modelli GGML (stessi pesi di Whisper, formato whisper.cpp).
_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"
_MODEL_ALIASES = {"tiny": "tiny", "base": "base", "small": "small"}

# timestamp che whisper.cpp stampa su stderr durante la decodifica
_TS_RE = re.compile(r"\[(\d+):(\d\d):(\d\d)\.(\d+)\s*-->")


def _log(msg):
    print(f"  whisper.cpp: {msg}", file=sys.stderr, flush=True)


def find_binary():
    """Percorso del binario whisper.cpp, o None se non e disponibile.

    Ordine: variabile d'ambiente WHISPER_CPP_BIN, cache del progetto, PATH.
    Solo nomi propri di whisper.cpp: "main" da solo e troppo ambiguo (su
    Windows `shutil.which("main")` restituisce main.CPL, l'applet "Mouse e
    Tastiera" del Pannello di controllo: accettarlo farebbe fallire la
    trascrizione solo dopo aver scaricato il modello).
    """
    env = os.environ.get("WHISPER_CPP_BIN", "").strip()
    if env and Path(env).is_file():
        return Path(env)
    for name in _EXE_NAMES:
        for base in (BIN_DIR, _HERE, _ROOT):
            cand = base / name
            if cand.is_file():
                return cand
    for name in ("whisper-cli", "whisper"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def model_path(name: str) -> Path:
    """Percorso del modello GGML, scaricandolo al primo uso se manca."""
    key = _MODEL_ALIASES.get(str(name).lower())
    if not key:
        raise ValueError(f"modello Whisper non valido: {name} (attesi: tiny, base, small)")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    target = MODEL_DIR / f"ggml-{key}.bin"
    if target.is_file() and target.stat().st_size > 0:
        return target
    url = _MODEL_URL.format(name=key)
    _log(f"scarico il modello ggml-{key}.bin (solo al primo uso)...")
    tmp = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=180) as resp, tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except Exception as exc:  # rete assente: si riprovera' alla prossima run
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"impossibile scaricare il modello Whisper da {url}: {exc}") from exc
    if tmp.stat().st_size < 1_000_000:  # una pagina di errore non e un modello
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"download del modello incompleto ({url})")
    tmp.replace(target)
    _log(f"modello salvato in {target.name}")
    return target


def _to_wav16k(src: Path, workdir: Path) -> Path:
    """Converte in WAV 16 kHz mono PCM: formato richiesto da whisper.cpp."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg non trovato: serve per convertire l'audio per whisper.cpp. "
            "Installa ffmpeg e rilancia."
        )
    dst = workdir / "audio16k.wav"
    res = subprocess.run(
        [ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or not dst.is_file() or dst.stat().st_size <= 44:
        raise RuntimeError(f"conversione audio fallita: {(res.stderr or '').strip()[:300]}")
    return dst


class WhisperCppModel:
    """Adattatore con la stessa interfaccia minima usata dagli altri backend.

    Espone ``transcribe(path) -> {"text": str}``: e la firma che `sources.py`
    usa nel ramo "backend generico", quindi non richiede modifiche al chiamante.
    """

    def __init__(self, name="base", language="it", threads=None, progress=None,
                 cancel=None, duration=0.0):
        self.name = str(name).lower()
        self.language = language
        self.threads = threads or max(1, min(8, os.cpu_count() or 1))
        self._progress = progress
        self._cancel = cancel
        self._duration = float(duration or 0.0)
        self._exe = find_binary()
        if not self._exe:
            raise RuntimeError(
                "whisper.cpp non trovato. Scarica la build per questa CPU e "
                "estraila in .whisper_cache\\whisper-cpp\\ tenendo l'eseguibile "
                "INSIEME alle sue DLL (whisper.dll, ggml*.dll, libomp140.*.dll): "
                "Windows ARM64 -> whisper-bin-win-cpu-arm64.zip "
                "(l'estratto contiene una cartella Release: copia tutto il "
                "contenuto, non solo l'eseguibile). Oppure imposta WHISPER_CPP_BIN."
            )
        self._model = model_path(self.name)

    # -- API usata da sources.py ------------------------------------------
    def transcribe(self, path, language=None):
        src = Path(path)
        if not src.is_file():
            raise ValueError(f"file audio non trovato: {src}")
        with tempfile.TemporaryDirectory(prefix="whispercpp_") as tmp:
            work = Path(tmp)
            wav = _to_wav16k(src, work)
            out_base = work / "out"
            cmd = [
                str(self._exe),
                "-m", str(self._model),
                "-f", str(wav),
                "-l", str(language or self.language),
                "-t", str(self.threads),
                "-of", str(out_base),
                "-oj",          # scrive out.json con i segmenti
            ]
            _log(f"{self._exe.name}, modello ggml-{self.name}, {self.threads} thread")
            text = self._run(cmd, out_base)
        return {"text": text}

    # -- interno -----------------------------------------------------------
    def _cancelled(self) -> bool:
        try:
            return bool(self._cancel and self._cancel.is_set())
        except AttributeError:
            return bool(self._cancel and self._cancel())

    def _run(self, cmd, out_base: Path) -> str:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        last_pct = -1
        for line in (proc.stderr or ()):
            m = _TS_RE.search(line)
            if not m:
                continue
            pos = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if self._progress and self._duration > 0:
                pct = min(99, int(pos / self._duration * 100))
                if pct > last_pct + 2:
                    last_pct = pct
                    try:
                        self._progress(pct, 0, self._duration, False, self.name)
                    except Exception:
                        pass
        proc.wait()
        if self._cancelled():
            raise ValueError("Trascrizione annullata dall'utente.")
        if proc.returncode != 0:
            raise RuntimeError(f"whisper.cpp e uscito con codice {proc.returncode}")
        jf = out_base.with_suffix(".json")
        if not jf.is_file():
            raise RuntimeError(f"whisper.cpp non ha prodotto {jf.name}")
        data = json.loads(jf.read_text(encoding="utf-8"))
        parti = [str(s.get("text", "")).strip() for s in data.get("transcription", [])]
        return " ".join(p for p in parti if p).strip()
