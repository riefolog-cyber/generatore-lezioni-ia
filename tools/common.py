# -*- coding: utf-8 -*-
"""Utilità condivise per la pipeline lezioni (senza video, player autogenerato).

- load_config(): config.json con default estesi
- resolve_voice(): cerca il modello Piper in più percorsi
- setup_logging(): log su console + generazione.log
- validate_lesson(): validazione slide+audio (nessun video)
- write_report(): report.html leggibile dopo la build
"""
import json
import logging
import logging.handlers
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def write_text_atomic(path, text):
    """Scrive un file di testo senza mai lasciarlo a metà.

    Scrive in un temporaneo nella stessa cartella e poi `os.replace`, che su
    Windows e su POSIX è atomico: se il processo muore (o il PC si spegne)
    durante la scrittura, il file di destinazione resta quello vecchio e
    integro invece di essere troncato. Costo ~1 ms su NTFS.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".w-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def safe_join(base, rel):
    """join di un percorso relativo VINCOLATO dentro `base`.

    Ritorna None se il risultato uscirebbe dalla cartella base (path traversal
    nei dati della lezione: `audio`, `caption`, ecc.).
    """
    if not rel:
        return None
    try:
        p = (Path(base) / str(rel).lstrip("/\\")).resolve()
        p.relative_to(Path(base).resolve())
    except (ValueError, OSError):
        return None
    return p

DEFAULT_CONFIG = {
    "llm_url": "http://localhost:20128/v1",
    "llm_model": "comboact",
    "llm_api_key": "",
    # catena di riserva se il modello principale è in cooldown/quota esaurita
    "llm_modelli_fallback": ["groq/openai/gpt-oss-120b", "comboact"],
    "llm_max_tokens": 8000,
    "llm_timeout": 120,
    "llm_deadline": 240,
    "llm_modo": "due_fasi",
    "llm_parallel": 4,
    "voice": "it_IT-serena-high",
    "edge_voice": "it-IT-GiuseppeMultilingualNeural",
    "edge_voice_domande": "",  # voci alternate: es. it-IT-ElsaNeural per le domande
    "edge_rate": "-4%",
    "audio_bitrate": 96,
    "theme": "dark",
    "num_moduli_min": 4,
    "num_moduli_max": 7,
    "porta": 8341,
    "max_upload_mb": 100,
    # normalizzazione audio: prima erano numeri magici in new_lesson.py
    # (2.8 parole/sec compariva 4 volte, i filtri ffmpeg erano scritti 3 volte)
    "audio_lufs": -17,
    "audio_true_peak": -1.5,
    "audio_lra": 9,
    "audio_fade_in_s": 0.04,
    "audio_durata_minima_s": 2.0,
    "parole_per_secondo": 2.8,
    "pin_docente": "",
    "llm_contesto_caratteri": 18000,
    "cache_max_mb": 300,
    "tts_workers": 4,
    "tts_retries": 2,
    "audio_loudnorm_dual": False,
    "whisper_model": "base",
    "profilo_durata": "standard",
    "profilo_livello": "intermedio",
    "profilo_obiettivo": "auto",
    "profilo_accessibilita": "standard",
    # IP fisso mostrato come indirizzo LAN in classe (es. "192.168.0.2"):
    # utile con router senza internet, dove l'auto-rilevamento fallisce.
    # Vuoto = rilevamento automatico.
    "lan_ip_fisso": "",
}

PROFILO_DURATE = ("breve", "standard", "approfondita")
PROFILO_LIVELLI = ("base", "intermedio", "avanzato")
PROFILO_OBIETTIVI = ("conoscenza", "comprensione", "applicazione", "analisi", "auto")
PROFILO_ACCESSIBILITA = ("standard", "bes")


def normalize_profilo(data):
    """Valida il profilo lezione (durata/livello/obiettivo Bloom/accessibilità)."""
    d = data if isinstance(data, dict) else {}
    durata = str(d.get("durata") or d.get("profilo_durata") or "standard").lower()
    livello = str(d.get("livello") or d.get("profilo_livello") or "intermedio").lower()
    obiettivo = str(d.get("obiettivo") or d.get("profilo_obiettivo") or "auto").lower()
    access = str(d.get("accessibilita") or d.get("profilo_accessibilita")
                 or "standard").lower()
    if durata not in PROFILO_DURATE:
        durata = "standard"
    if livello not in PROFILO_LIVELLI:
        livello = "intermedio"
    if obiettivo not in PROFILO_OBIETTIVI:
        obiettivo = "comprensione"
    if access not in PROFILO_ACCESSIBILITA:
        access = "standard"
    return {"durata": durata, "livello": livello, "obiettivo": obiettivo,
            "accessibilita": access}


def _clamp_int(value, default, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


_CFG_CACHE = None  # (cfg, mtime, path) — ricalcolata solo se il file cambia


def load_config():
    """Legge config.json. Il risultato è messo in cache per mtime del file:
    è chiamata 13+ volte per build e ogni chiamata rileggeva e riparsava il
    JSON. La semantica "leggi a caldo" resta: se il file cambia, la cache
    viene invalidata al chiamante successivo."""
    global _CFG_CACHE
    p = BASE / "config.json"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _CFG_CACHE is not None and _CFG_CACHE[1] == mtime:
        return _CFG_CACHE[0]

    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if v is not None})
        except Exception as exc:  # config corrotto: si prosegue coi default, ma si dice
            print(f"⚠ config.json non leggibile ({exc}): uso i valori predefiniti")
    # Override da variabili d'ambiente (Docker/CI senza toccare config.json)
    for env_key, cfg_key in (
        ("LLM_URL", "llm_url"), ("LLM_MODEL", "llm_model"),
        ("LLM_API_KEY", "llm_api_key"), ("EDGE_VOICE", "edge_voice"),
        ("EDGE_VOICE_DOMANDE", "edge_voice_domande"),
        ("EDGE_RATE", "edge_rate"), ("THEME", "theme"), ("PORTA", "porta"),
    ):
        if os.environ.get(env_key):
            cfg[cfg_key] = os.environ[env_key]
    # Validazione + clamp: mai crash per un valore errato in config.json
    cfg["tts_workers"] = _clamp_int(cfg.get("tts_workers", 4), 4, 1, 8)
    cfg["tts_retries"] = _clamp_int(cfg.get("tts_retries", 2), 2, 0, 5)
    cfg["audio_bitrate"] = _clamp_int(cfg.get("audio_bitrate", 96), 96, 32, 320)
    cfg["porta"] = _clamp_int(cfg.get("porta", 8341), 8341, 1024, 65535)
    cfg["max_upload_mb"] = _clamp_int(
        cfg.get("max_upload_mb", 100), 100, 1, 200)
    cfg["cache_max_mb"] = _clamp_int(cfg.get("cache_max_mb", 300), 300, 50, 2000)
    cfg["llm_contesto_caratteri"] = _clamp_int(
        cfg.get("llm_contesto_caratteri", 18000), 18000, 2000, 60000)
    nmin = _clamp_int(cfg.get("num_moduli_min", 4), 4, 1, 12)
    nmax = _clamp_int(cfg.get("num_moduli_max", 7), 7, 1, 12)
    if nmin > nmax:
        nmin, nmax = nmax, nmin
    cfg["num_moduli_min"], cfg["num_moduli_max"] = nmin, nmax
    if cfg.get("theme") not in ("dark", "light"):
        cfg["theme"] = "dark"
    if cfg.get("whisper_model") not in ("tiny", "base", "small"):
        cfg["whisper_model"] = "base"
    if not re.fullmatch(r"-?\d+%", str(cfg.get("edge_rate", "-4%"))):
        cfg["edge_rate"] = "-4%"
    prof = normalize_profilo(cfg)
    cfg["profilo_durata"], cfg["profilo_livello"], cfg["profilo_obiettivo"] = \
        prof["durata"], prof["livello"], prof["obiettivo"]
    _CFG_CACHE = (cfg, mtime, p)
    return cfg


def _ensure_piper_voice(dest, name):
    """Scarica il modello Piper se manca (onnx + json). Ritorna True se ora esiste."""
    try:
        parts = name.split("-")  # es. it_IT-serena-high -> short=serena, quality=high
        short = parts[1] if len(parts) > 1 else "serena"
        quality = parts[2] if len(parts) > 2 else "medium"
        dest.parent.mkdir(parents=True, exist_ok=True)
        for suffix in (".onnx", ".onnx.json"):
            target = dest.with_name(dest.name + suffix) if suffix != ".onnx" else dest
            if target.exists() and target.stat().st_size > 1000:
                continue
            url = (f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
                   f"it/it_IT/{short}/{quality}/{name}{suffix}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(target, "wb") as f:
                f.write(r.read())
        return dest.exists()
    except Exception:
        return False


def resolve_voice(voice_name=None, auto_download=True):
    """Ritorna (onnx_path, cache_dir). Cerca in assets/voice/ e .tools/piper/.
    Se manca e auto_download=True, prova a scaricarla da HuggingFace."""
    cfg = load_config()
    name = voice_name or cfg.get("voice", "it_IT-serena-high")
    candidates = [
        BASE / "assets" / "voice" / f"{name}.onnx",
        BASE / ".tools" / "piper" / f"{name}.onnx",
    ]
    for c in candidates:
        if c.exists():
            cache = BASE / "assets" / "voice" / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            return c, cache
    if auto_download and _ensure_piper_voice(candidates[0], name):
        cache = BASE / "assets" / "voice" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        return candidates[0], cache
    return candidates[0], BASE / "assets" / "voice" / "cache"


def setup_logging(name="lezioni", max_bytes=2 * 1024 * 1024, backup=3):
    logfile = BASE / "generazione.log"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False  # niente record duplicati sul root logger
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(
        logfile, maxBytes=max_bytes, backupCount=backup, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def validate_lesson(out_dir, slides):
    """Validazione del pacchetto lezione (player autogenerato, nessun video).
    `slides` = lista di dict {title, audio, duration, blocks}. Ritorna (ok, errori, stats)."""
    out_dir = Path(out_dir)
    errs = []
    if not (out_dir / "index.html").exists():
        errs.append("index.html mancante (player non generato)")
    if not (out_dir / "lesson-data.js").exists():
        errs.append("lesson-data.js mancante")
    quiz = matching = vf = seq = compila = scenario = errore = flashcards = glossario = 0
    classifica = 0
    workbook = 0
    seen_audio = set()
    for i, s in enumerate(slides):
        for b in s.get("blocks", []):
            # Il blocco arriva dall'LLM: puo' essere una lista o una stringa
            # invece di un dizionario. Senza guardia, `b["quiz"].get(...)` fa
            # AttributeError e l'eccezione ABORTA TUTTA la validazione,
            # perdendo anche gli errori raccolti fino a quel punto. Per questo
            # ogni tipo viene controllato con isinstance prima di usarlo.
            if not isinstance(b, dict):
                errs.append(f"slide {i + 1}: blocco non valido ({type(b).__name__})")
                continue
            if "quiz" in b:
                quiz += 1
                if not isinstance(b["quiz"], dict):
                    errs.append(f"slide {i + 1}: quiz malformato")
                else:
                    opts = b["quiz"].get("opts") or []
                    if len(opts) < 2:
                        errs.append(f"slide {i + 1}: quiz con meno di 2 opzioni")
                    if not any(o.get("ok") for o in opts if isinstance(o, dict)):
                        errs.append(f"slide {i + 1}: quiz senza risposta corretta")
            if "match" in b:
                matching += 1
                if not isinstance(b["match"], dict) or len(b["match"].get("pairs") or []) < 2:
                    errs.append(f"slide {i + 1}: abbinamento con meno di 2 coppie")
            if "vf" in b:
                vf += 1
                if not isinstance(b["vf"], list) or len(b["vf"]) < 2:
                    errs.append(f"slide {i + 1}: vero/falso con meno di 2 affermazioni")
                for v in (b["vf"] if isinstance(b["vf"], list) else []):
                    if not isinstance(v, dict) or not v.get("t") or not isinstance(v.get("ok"), bool):
                        errs.append(f"slide {i + 1}: affermazione vero/falso malformata")
                        break
            if "seq" in b:
                seq += 1
                if not isinstance(b["seq"], dict) or len(b["seq"].get("passi") or []) < 3:
                    errs.append(f"slide {i + 1}: sequenza con meno di 3 passi")
            if "compila" in b:
                compila += 1
                for c in (b["compila"] if isinstance(b["compila"], list) else []):
                    if (not isinstance(c, dict) or "___" not in str(c.get("frase", ""))
                            or not c.get("risposta")):
                        errs.append(f"slide {i + 1}: frase compila malformata")
                        break
            if "scenario" in b:
                scenario += 1
                sc = b["scenario"]
                if not isinstance(sc, dict) or not sc.get("situazione") \
                        or len(sc.get("opts") or []) < 2:
                    errs.append(f"slide {i + 1}: scenario incompleto")
                elif not any(o.get("ok") for o in (sc.get("opts") or [])
                             if isinstance(o, dict)):
                    errs.append(f"slide {i + 1}: scenario senza opzione corretta")
            if "errore" in b:
                errore += 1
                e0 = b["errore"]
                if not isinstance(e0, dict) or not e0.get("brano") or not e0.get("correzione"):
                    errs.append(f"slide {i + 1}: esercizio errore incompleto")
            if "flashcards" in b:
                flashcards += 1
                fc = b["flashcards"]
                if not isinstance(fc, dict) or len(fc.get("cards") or []) < 2:
                    errs.append(f"slide {i + 1}: flashcards con meno di 2 carte")
                for c in fc.get("cards", []):
                    if not isinstance(c, dict) or not c.get("t") or not c.get("d"):
                        errs.append(f"slide {i + 1}: carta flashcards malformata")
                        break
            if "classifica" in b:
                classifica += 1
                cl = b["classifica"]
                cats = cl.get("cats", []) if isinstance(cl, dict) else []
                items = cl.get("items", []) if isinstance(cl, dict) else []
                if len(cats) < 2:
                    errs.append(f"slide {i + 1}: classifica con meno di 2 categorie")
                if len(items) < 4:
                    errs.append(f"slide {i + 1}: classifica con meno di 4 elementi")
                for it in items:
                    if (not isinstance(it, dict) or not it.get("t")
                            or not isinstance(it.get("cat"), int)
                            or not (0 <= it.get("cat", -1) < len(cats))):
                        errs.append(f"slide {i + 1}: elemento classifica malformato")
                        break
            if "workbook" in b:
                # il quaderno (risposta libera) esiste nel player e viene
                # valutato, ma non era controllato: una domanda senza chiavi
                # non può mai essere data per corretta dallo studente
                workbook += 1
                for w in (b["workbook"] if isinstance(b["workbook"], list) else []):
                    if not isinstance(w, dict) or not w.get("q") or not w.get("k"):
                        errs.append(f"slide {i + 1}: domanda di quaderno malformata "
                                    "(manca la domanda o le parole chiavi)")
                        break
            if "glossario" in b:
                gl = b["glossario"]
                groups = gl.get("groups", []) if isinstance(gl, dict) else []
                nterms = sum(len(gr.get("terms", [])) for gr in groups
                             if isinstance(gr, dict))
                glossario += nterms
                if nterms < 3:
                    errs.append(f"slide {i + 1}: glossario con meno di 3 termini")
                for gr in groups:
                    if not isinstance(gr, dict) or not gr.get("terms"):
                        errs.append(f"slide {i + 1}: gruppo glossario vuoto o malformato")
                        break
                    for t in gr["terms"]:
                        if not isinstance(t, dict) or not t.get("t") or not t.get("d"):
                            errs.append(f"slide {i + 1}: voce di glossario malformata")
                            break
        if s.get("audio") is None:
            errs.append(f"slide {i + 1}: audio mancante")
        else:
            ap = safe_join(out_dir, s["audio"])
            if ap is None or not ap.exists():
                errs.append(f"slide {i + 1}: file audio mancante {s['audio']}")
            elif float(s.get("duration") or 0) <= 0:
                errs.append(f"slide {i + 1}: durata non misurata")
            if s["audio"] in seen_audio:
                errs.append(f"audio duplicato: {s['audio']}")
            seen_audio.add(s["audio"])
            cap_rel = s.get("caption") or ""
            cp = safe_join(out_dir, cap_rel) if cap_rel else None
            # `safe_join` restituisce None se il percorso esce dalla cartella:
            # chiamare .exists() su None faceva crashare l'intera validazione
            # (la riga dell'audio, tre righe sopra, controlla gia' `ap is None`)
            if not cap_rel or cp is None or not cp.exists():
                errs.append(f"slide {i + 1}: sottotitoli mancanti {s.get('caption')}")
            elif cp.stat().st_size < 20:
                errs.append(f"slide {i + 1}: sottotitoli vuoti")
            # coerenza words vs durata: l'ultimo timing non deve sforare
            try:
                words = s.get("words") or []
                if words and s.get("duration"):
                    last_end = max(float(w[1]) for w in words)
                    if last_end > s["duration"] + 1.0:
                        errs.append(f"slide {i + 1}: timing parole oltre la durata "
                                    f"({last_end:.1f}s > {s['duration']:.1f}s)")
            except Exception:
                pass
    stats = {
        "slide": len(slides),
        "quiz": quiz,
        "vf": vf,
        "seq": seq,
        "compila": compila,
        "scenario": scenario,
        "errore": errore,
        "flashcards": flashcards,
        "classifica": classifica,
        "workbook": workbook,
        "glossario": glossario,
        "matching": matching,
        "audio": len(seen_audio),
    }
    return (len(errs) == 0, errs, stats)


def write_report(out_dir, errs, stats, extra=None):
    """Scrive report.html leggibile nella cartella lezione."""
    out_dir = Path(out_dir)
    esc = lambda s: __import__("html").escape(str(s), quote=True)
    rows = "".join(f"<tr><td>{esc(k)}</td><td><b>{esc(v)}</b></td></tr>" for k, v in stats.items())
    if errs:
        err_html = "<ul>" + "".join(f"<li>{esc(e)}</li>" for e in errs) + "</ul>"
        stato = "⚠ Generata con avvisi"
    else:
        err_html = "<p>VALIDAZIONE OK — nessun errore.</p>"
        stato = "✓ Lezione pronta"
    extra_html = f"<p>{esc(extra)}</p>" if extra else ""
    html = f"""<!DOCTYPE html><html lang="it"><meta charset="utf-8">
<title>Report — {esc(out_dir.name)}</title>
<body style="font-family:sans-serif;max-width:720px;margin:2rem auto;background:#0d1420;color:#eaf1ff">
<h1>{stato}</h1><h2>{esc(out_dir.name)}</h2>
<table border="1" cellpadding="6">{rows}</table>
<h3>Dettagli</h3>{err_html}{extra_html}
<p>Apri <a href="index.html" style="color:#4f8cff">index.html</a> per vedere la lezione.</p>
</body></html>"""
    write_text_atomic(out_dir / "report.html", html)
