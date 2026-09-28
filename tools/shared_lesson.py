# -*- coding: utf-8 -*-
"""Lezione mostrata in classe: quale lezione vedono gli alunni via LAN.

Il docente sceglie UNA lezione dal pannello; il QR/pagina LAN rimanda
diretto a quella (completa, tutti i moduli). File atomico + robusto:
se manca o non è valido -> nessuna lezione condivisa (hub normale).
"""
import json
from pathlib import Path


def _path(base: Path) -> Path:
    return Path(base) / "lezione_in_classe.json"


def get_shared(base: Path):
    """Nome della lezione condivisa, o '' se nessuna / non valida."""
    try:
        raw = _path(base).read_text(encoding="utf-8")
        name = (json.loads(raw) or {}).get("lesson") or ""
        name = str(name).strip()
    except Exception:
        return ""
    if not name or "/" in name or "\\" in name or ".." in name:
        return ""
    lesson = Path(base) / name
    try:
        if lesson.is_dir() and (lesson / "index.html").is_file():
            return name
    except Exception:
        pass
    return ""


def set_shared(base: Path, name: str):
    """Imposta (o azzera con '') la lezione condivisa. Ritorna il nome attivo."""
    name = (name or "").strip()
    if name:
        if "/" in name or "\\" in name or ".." in name:
            raise ValueError("Nome lezione non valido.")
        lesson = Path(base) / name
        if not (lesson.is_dir() and (lesson / "index.html").is_file()):
            raise ValueError("Lezione non trovata.")
    tmp = _path(base).with_suffix(".tmp")
    tmp.write_text(json.dumps({"lesson": name}, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(_path(base))
    return name
