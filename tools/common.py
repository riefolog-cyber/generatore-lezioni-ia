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
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG = {
    "llm_url": "http://localhost:20128/v1",
    "llm_model": "comboact",
    "voice": "it_IT-serena-high",
    "edge_voice": "it-IT-GiuseppeMultilingualNeural",
    "edge_rate": "-4%",
    "theme": "dark",
    "num_moduli_min": 4,
    "num_moduli_max": 7,
    "porta": 8341,
    "llm_contesto_caratteri": 18000,
    "cache_max_mb": 300,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    p = BASE / "config.json"
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if v is not None})
        except Exception:
            pass
    return cfg


def resolve_voice(voice_name=None):
    """Ritorna (onnx_path, cache_dir). Cerca in assets/voice/ e .tools/piper/."""
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
    return candidates[0], BASE / "assets" / "voice" / "cache"


def setup_logging(name="lezioni"):
    logfile = BASE / "generazione.log"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(logfile, encoding="utf-8")
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
    quiz = matching = vf = seq = compila = scenario = errore = flashcards = 0
    seen_audio = set()
    for i, s in enumerate(slides):
        has_activity = False
        for b in s.get("blocks", []):
            if "quiz" in b:
                quiz += 1
                has_activity = True
                if len(b["quiz"].get("opts", [])) < 2:
                    errs.append(f"slide {i + 1}: quiz con meno di 2 opzioni")
                if not any(o.get("ok") for o in b["quiz"].get("opts", [])):
                    errs.append(f"slide {i + 1}: quiz senza risposta corretta")
            if "match" in b:
                matching += 1
                has_activity = True
                if len(b["match"].get("pairs", [])) < 2:
                    errs.append(f"slide {i + 1}: abbinamento con meno di 2 coppie")
            if "vf" in b:
                vf += 1
                has_activity = True
                if len(b["vf"]) < 2:
                    errs.append(f"slide {i + 1}: vero/falso con meno di 2 affermazioni")
                for v in b["vf"]:
                    if not isinstance(v, dict) or not v.get("t") or not isinstance(v.get("ok"), bool):
                        errs.append(f"slide {i + 1}: affermazione vero/falso malformata")
                        break
            if "seq" in b:
                seq += 1
                has_activity = True
                if len(b["seq"].get("passi", [])) < 3:
                    errs.append(f"slide {i + 1}: sequenza con meno di 3 passi")
            if "compila" in b:
                compila += 1
                has_activity = True
                for c in b["compila"]:
                    if (not isinstance(c, dict) or "___" not in str(c.get("frase", ""))
                            or not c.get("risposta")):
                        errs.append(f"slide {i + 1}: frase compila malformata")
                        break
            if "scenario" in b:
                scenario += 1
                has_activity = True
                sc = b["scenario"]
                if not sc.get("situazione") or len(sc.get("opts", [])) < 2:
                    errs.append(f"slide {i + 1}: scenario incompleto")
                elif not any(o.get("ok") for o in sc["opts"]):
                    errs.append(f"slide {i + 1}: scenario senza opzione corretta")
            if "errore" in b:
                errore += 1
                has_activity = True
                e0 = b["errore"]
                if not e0.get("brano") or not e0.get("correzione"):
                    errs.append(f"slide {i + 1}: esercizio errore incompleto")
            if "flashcards" in b:
                flashcards += 1
                has_activity = True
                fc = b["flashcards"]
                if len(fc.get("cards", [])) < 2:
                    errs.append(f"slide {i + 1}: flashcards con meno di 2 carte")
                for c in fc.get("cards", []):
                    if not isinstance(c, dict) or not c.get("t") or not c.get("d"):
                        errs.append(f"slide {i + 1}: carta flashcards malformata")
                        break
        if s.get("audio") is None:
            errs.append(f"slide {i + 1}: audio mancante")
        else:
            ap = out_dir / s["audio"][2:]
            if not ap.exists():
                errs.append(f"slide {i + 1}: file audio mancante {s['audio']}")
            elif s.get("duration", 0) <= 0:
                errs.append(f"slide {i + 1}: durata non misurata")
            if s["audio"] in seen_audio:
                errs.append(f"audio duplicato: {s['audio']}")
            seen_audio.add(s["audio"])
            cp = out_dir / s["caption"][2:]
            if not cp.exists():
                errs.append(f"slide {i + 1}: sottotitoli mancanti {s['caption']}")
    stats = {
        "slide": len(slides),
        "quiz": quiz,
        "vf": vf,
        "seq": seq,
        "compila": compila,
        "scenario": scenario,
        "errore": errore,
        "flashcards": flashcards,
        "matching": matching,
        "audio": len(seen_audio),
    }
    return (len(errs) == 0, errs, stats)


def write_report(out_dir, errs, stats, extra=None):
    """Scrive report.html leggibile nella cartella lezione."""
    out_dir = Path(out_dir)
    rows = "".join(f"<tr><td>{k}</td><td><b>{v}</b></td></tr>" for k, v in stats.items())
    if errs:
        err_html = "<ul>" + "".join(f"<li>{e}</li>" for e in errs) + "</ul>"
        stato = "⚠ Generata con avvisi"
    else:
        err_html = "<p>VALIDAZIONE OK — nessun errore.</p>"
        stato = "✓ Lezione pronta"
    extra_html = f"<p>{extra}</p>" if extra else ""
    html = f"""<!DOCTYPE html><html lang="it"><meta charset="utf-8">
<title>Report — {out_dir.name}</title>
<body style="font-family:sans-serif;max-width:720px;margin:2rem auto;background:#0d1420;color:#eaf1ff">
<h1>{stato}</h1><h2>{out_dir.name}</h2>
<table border="1" cellpadding="6">{rows}</table>
<h3>Dettagli</h3>{err_html}{extra_html}
<p>Apri <a href="index.html" style="color:#4f8cff">index.html</a> per vedere la lezione.</p>
</body></html>"""
    (out_dir / "report.html").write_text(html, encoding="utf-8")
