# -*- coding: utf-8 -*-
"""Lezione mostrata in classe: quale lezione vedono gli alunni via LAN.

Il docente sceglie UNA lezione dal pannello; il QR/pagina LAN rimanda
diretto a quella (completa, tutti i moduli). File atomico + robusto:
se manca o non è valido -> nessuna lezione condivisa (hub normale).

Lo stesso file tiene anche il **timer di classe**: il conto alla rovescia che
compare in alto sulla pagina degli studenti. La scadenza è un istante
assoluto (epoch ms) e non una durata: tutti gli alunni devono vedere la
stessa fine, altrimenti chi apre la lezione un minuto dopo avrebbe tempo
in più e la classe non sarebbe più sincronizzata.
"""
import json
import time
from pathlib import Path

MINUTI_MAX = 180          # 3 ore: oltre non è un'attività, è una lezione intera


def _path(base: Path) -> Path:
    return Path(base) / "lezione_in_classe.json"


def _leggi(base: Path) -> dict:
    """Stato grezzo dal file; `{}` se manca, illeggibile o non è un oggetto."""
    try:
        raw = json.loads(_path(base).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _scrivi(base: Path, stato: dict) -> None:
    tmp = _path(base).with_suffix(".tmp")
    tmp.write_text(json.dumps(stato, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_path(base))


def _nome_valido(base: Path, name: str) -> bool:
    name = str(name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return False
    lesson = Path(base) / name
    try:
        return lesson.is_dir() and (lesson / "index.html").is_file()
    except Exception:
        return False


def get_shared(base: Path):
    """Nome della lezione condivisa, o '' se nessuna / non valida."""
    name = str(_leggi(base).get("lesson") or "").strip()
    return name if _nome_valido(base, name) else ""


def set_shared(base: Path, name: str):
    """Imposta (o azzera con '') la lezione condivisa. Ritorna il nome attivo.

    Il timer NON viene toccato: cambiare la lezione mostrata non deve far
    sparire il conto alla rovescia già avviato (e viceversa: avviare il timer
    non deve scegliere una lezione).
    """
    name = (name or "").strip()
    if name and not _nome_valido(base, name):
        raise ValueError("Lezione non trovata.")
    stato = _leggi(base)
    stato["lesson"] = name
    _scrivi(base, stato)
    return name


def _timer_grezzo(base: Path) -> dict:
    t = _leggi(base).get("timer")
    return t if isinstance(t, dict) else {}


def get_timer(base: Path) -> dict:
    """Timer di classe: `{minuti, scadenza, attivo, residuo}`.

    `scadenza` è epoch in millisecondi (0 = nessun timer). `residuo` sono i
    millisecondi che mancano, 0 se è scaduto. I valori assurdi vengono
    normalizzati invece di propagarsi: il file può essere stato scritto a mano
    o da una versione precedente.
    """
    t = _timer_grezzo(base)
    try:
        minuti = int(t.get("minuti") or 0)
    except (TypeError, ValueError):
        minuti = 0
    try:
        scadenza = int(t.get("scadenza") or 0)
    except (TypeError, ValueError):
        scadenza = 0
    minuti = max(0, min(MINUTI_MAX, minuti))
    residuo = max(0, scadenza - int(time.time() * 1000)) if scadenza else 0
    return {"minuti": minuti, "scadenza": scadenza,
            "attivo": bool(scadenza and residuo > 0), "residuo": residuo}


def set_timer(base: Path, minuti) -> dict:
    """Avvia (ri)imposta il timer da adesso; `minuti` = 0 lo disattiva.

    Ritorna lo stato risultante, così il pannello non deve rileggere il file.
    Il conteggio riparte da capo a ogni avvio: è quello che il docente si
    aspetta premendo «Avvia» (prolungare, non riavviare).
    """
    try:
        minuti = int(minuti or 0)
    except (TypeError, ValueError):
        raise ValueError("Minuti non validi.")
    if minuti < 0 or minuti > MINUTI_MAX:
        raise ValueError(f"Timer fuori range (1-{MINUTI_MAX} minuti).")
    stato = _leggi(base)
    stato["timer"] = {
        "minuti": minuti,
        "scadenza": int(time.time() * 1000) + minuti * 60_000 if minuti else 0,
    }
    _scrivi(base, stato)
    return get_timer(base)
