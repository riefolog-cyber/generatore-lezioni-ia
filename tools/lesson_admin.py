# -*- coding: utf-8 -*-
"""Operazioni sicure sulle cartelle lezione (rinomina, copia, archivio).

Tutte le funzioni accettano solo directory reali poste direttamente nella
cartella dell'app (o nel suo archivio). Questo evita cancellazioni o spostamenti
verso percorsi esterni tramite nomi inseriti dall'utente.
"""
import json
import os
import re
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _valid_dir_name(name):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_ -]{0,99}_lesson", name or ""))


def _safe_lesson(base, name, parent):
    if not _valid_dir_name(name):
        raise ValueError("Nome lezione non valido: usa lettere, numeri, spazi o _.")
    lesson = (base / name).resolve()
    root = parent.resolve()
    if lesson.parent != root or not lesson.is_dir():
        raise ValueError("Lezione non trovata.")
    return lesson


def safe_lesson(base, name):
    """Restituisce una lezione attiva dopo tutti i controlli di sicurezza."""
    return _safe_lesson(Path(base), name, Path(base))


def safe_archived(base, name):
    archive = Path(base) / "archivio_lezioni"
    return _safe_lesson(archive, name, archive)


def unique_destination(parent, name):
    """Nome libero mantenendo _lesson; es. Prova_lesson -> Prova (2)_lesson."""
    parent = Path(parent)
    if not (parent / name).exists():
        return parent / name
    stem, suffix = name[:-7], "_lesson"
    for n in range(2, 1000):
        candidate = f"{stem} ({n}){suffix}"
        if not (parent / candidate).exists():
            return parent / candidate
    raise ValueError("Esistono già troppe copie con questo nome.")


def lesson_info(base, name):
    """Metadati economici per la lista, senza importare la pipeline LLM."""
    lesson = safe_lesson(base, name)
    size = 0
    for p in lesson.rglob("*"):
        try:
            if p.is_file():
                size += p.stat().st_size
        except OSError:
            pass
    title = lesson.name[:-7]
    duration = 0.0
    try:
        raw = (lesson / "lesson-data.js").read_text(encoding="utf-8")
        match = re.search(r"window\.LESSON_DATA\s*=\s*(\{.*\})\s*;?\s*$", raw, re.S)
        if match:
            payload = json.loads(match.group(1))
            title = str(payload.get("titolo") or title)[:120]
            duration = sum(float(s.get("duration") or 0) for s in payload.get("slides", []))
    except (OSError, ValueError, TypeError):
        pass
    return {
        "name": lesson.name,
        "title": title,
        "size": size,
        "duration": round(duration, 1),
        "modified": lesson.stat().st_mtime,
    }


def _update_lesson_dir(lesson, old_name, new_name):
    """Aggiorna il nome usato dal player per inviare la classifica."""
    index = lesson / "index.html"
    if not index.exists():
        return
    text = index.read_text(encoding="utf-8")
    old = f'window.LESSON_DIR = "{old_name}";'
    new = f'window.LESSON_DIR = "{new_name}";'
    if old not in text:
        return
    tmp = index.with_suffix(".html.tmp")
    tmp.write_text(text.replace(old, new, 1), encoding="utf-8")
    os.replace(tmp, index)


def rename_lesson(base, name, new_title):
    base = Path(base)
    lesson = safe_lesson(base, name)
    clean = re.sub(r"[^A-Za-z0-9]+", "_", str(new_title or "").strip()).strip("_")
    if not clean:
        raise ValueError("Scrivi un nome per la lezione.")
    new_name = f"{clean[:100]}_lesson"
    old_name = lesson.name
    dest = unique_destination(base, new_name)
    if dest.name == old_name:
        return dest.name
    lesson.rename(dest)
    _update_lesson_dir(dest, old_name, dest.name)
    return dest.name


def duplicate_lesson(base, name):
    base = Path(base)
    lesson = safe_lesson(base, name)
    stem = lesson.name[:-7]
    dest = unique_destination(base, f"{stem} copia_lesson")
    shutil.copytree(lesson, dest)
    _update_lesson_dir(dest, name, dest.name)
    return dest.name


def archive_lesson(base, name):
    base = Path(base)
    lesson = safe_lesson(base, name)
    archive = base / "archivio_lezioni"
    archive.mkdir(exist_ok=True)
    dest = unique_destination(archive, lesson.name)
    shutil.move(str(lesson), str(dest))
    return dest.name


def list_archived(base):
    archive = Path(base) / "archivio_lezioni"
    if not archive.is_dir():
        return []
    return sorted(p.name for p in archive.glob("*_lesson")
                  if p.is_dir() and (p / "index.html").exists())


def restore_lesson(base, name):
    base = Path(base)
    lesson = safe_archived(base, name)
    dest = unique_destination(base, lesson.name)
    shutil.move(str(lesson), str(dest))
    return dest.name


def delete_lesson(base, name):
    lesson = safe_lesson(Path(base), name)
    shutil.rmtree(lesson)
    return name
