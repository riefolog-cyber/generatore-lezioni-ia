# -*- coding: utf-8 -*-
"""Backup automatici di cronologia e classifica (solo librerie standard)."""
import shutil
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BACKUP_NAMES = ("job_history.json", "classifica.json")
KEEP = 14


def _backup_root(base, backup_dir=None):
    return Path(backup_dir) if backup_dir else Path(base) / "archivio_backup"


def create_backup(base=None, keep=KEEP, backup_dir=None):
    """Copia i dati presenti in una cartella datata e rimuove i salvataggi vecchi."""
    base = Path(base or BASE)
    root = _backup_root(base, backup_dir)
    sources = [base / name for name in BACKUP_NAMES if (base / name).is_file()]
    if not sources:
        return None
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    folder = root / stamp
    suffix = 2
    while folder.exists():
        folder = root / f"{stamp}_{suffix}"
        suffix += 1
    folder.mkdir()
    for source in sources:
        try:
            shutil.copy2(source, folder / source.name)
        except OSError:
            pass
    backups = sorted((p for p in root.iterdir() if p.is_dir()),
                     key=lambda p: p.name, reverse=True)
    for old in backups[max(1, int(keep)):]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def backup_loop(base=None, interval=3600):
    """Esegui in background: backup subito e poi ogni ora."""
    while True:
        try:
            create_backup(base)
        except Exception:
            pass
        time.sleep(max(60, int(interval)))


def last_backup(base=None, backup_dir=None):
    root = _backup_root(base or BASE, backup_dir)
    if not root.is_dir():
        return None
    folders = [p for p in root.iterdir() if p.is_dir()]
    return max(folders, key=lambda p: p.name).name if folders else None




