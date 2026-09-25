# -*- coding: utf-8 -*-
"""Inventario e pulizia sicura dei materiali di partenza."""
from __future__ import annotations

import re
from pathlib import Path

MATERIAL_EXT = {".docx", ".pdf", ".txt", ".md", ".html", ".htm",
                 ".pptx", ".epub", ".mp3", ".m4a", ".wav"}


def _lesson_name(path):
    """Stessa normalizzazione di new_lesson.sanitize_stem, così l'audio
    trascritto e la lezione risultante hanno lo stesso nome."""
    stem = re.sub(r"^[\d\s_\-.]+", "", Path(path).stem)
    stem = re.sub(r"[^\w\s\-]", "", stem, flags=re.UNICODE)
    stem = re.sub(r"\s+", "_", stem.strip())[:40].strip(" .") or "Lezione"
    return f"{stem}_lesson"


def _lesson_names(path):
    """Nomi lezione plausibili: stem del file o titolo della trascrizione."""
    names = {_lesson_name(path)}
    if Path(path).suffix.lower() in (".mp3", ".m4a", ".wav"):
        try:
            import hashlib
            import json
            from tools import sources
            for model in ("base", "tiny", "small"):
                h = hashlib.sha256(model.encode("utf-8"))
                with Path(path).open("rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                data = json.loads((sources._WHISPER_CACHE_DIR / f"{h.hexdigest()}.json")
                                  .read_text(encoding="utf-8"))
                title = str(data.get("title") or "").strip()
                if title:
                    names.add(_lesson_name(title))
        except Exception:
            pass
    return names


def list_materials(base):
    base = Path(base)
    out = []
    for p in sorted(base.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file() or p.suffix.lower() not in MATERIAL_EXT:
            continue
        try:
            st = p.stat()
            lesson = _lesson_name(p)
            out.append({"name": p.name, "size": st.st_size, "modified": st.st_mtime,
                        "lesson": lesson,
                        "generated": any((base / n / "index.html").is_file()
                                         for n in _lesson_names(p))})
        except OSError:
            continue
    return out


def delete_material(base, name, confirm=None):
    base = Path(base).resolve()
    if confirm != name:
        raise ValueError("Conferma non valida: digita di nuovo il nome del file.")
    target = (base / Path(str(name)).name).resolve()
    if target.parent != base or not target.is_file() or target.suffix.lower() not in MATERIAL_EXT:
        raise ValueError("Materiale non valido.")
    if not any((base / n / "index.html").is_file() for n in _lesson_names(target)):
        raise ValueError("Non puoi eliminare un materiale senza la lezione generata.")
    size = target.stat().st_size
    target.unlink()
    return {"deleted": target.name, "size": size}


def storage_report(base):
    """Dimensioni per categoria, senza seguire collegamenti esterni."""
    base = Path(base)
    groups = {"materiali": 0, "lezioni": 0, "cache_audio": 0,
              "cache_llm": 0, "backup": 0, "altro": 0}
    for p in base.iterdir():
        try:
            if p.is_dir():
                if p.name.endswith("_lesson") or p.name == "archivio_lezioni":
                    groups["lezioni"] += sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
                elif p.name == "assets":
                    groups["cache_audio"] += sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
                elif p.name in (".llm_cache", ".whisper_cache"):
                    groups["cache_llm"] += sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
                elif p.name == "archivio_backup":
                    groups["backup"] += sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
                else:
                    groups["altro"] += sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
            elif p.is_file():
                key = "materiali" if p.suffix.lower() in MATERIAL_EXT else "altro"
                groups[key] += p.stat().st_size
        except OSError:
            continue
    return groups
