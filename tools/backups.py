# -*- coding: utf-8 -*-
"""Backup automatici di cronologia e classifica (solo librerie standard)."""
import shutil
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BACKUP_NAMES = ("job_history.json", "classifica.json")
DEFAULT_KEEP = 14


def _keep_count(base=None, keep=None):
    if keep is not None:
        return max(1, min(100, int(keep)))
    if base is not None:
        try:
            import json
            cfg = json.loads((Path(base) / "config.json").read_text(encoding="utf-8"))
            return max(1, min(100, int(cfg.get("backup_keep", DEFAULT_KEEP))))
        except Exception:
            pass
    return DEFAULT_KEEP


def _backup_root(base, backup_dir=None):
    return Path(backup_dir) if backup_dir else Path(base) / "archivio_backup"


def create_backup(base=None, keep=None, backup_dir=None):
    """Copia i dati presenti in una cartella datata e rimuove i salvataggi vecchi."""
    base = Path(base or BASE)
    keep = _keep_count(base, keep)
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
    for old in backups[max(1, keep):]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def backup_loop(base=None, interval=None):
    """Esegui in background: backup subito e poi a intervallo configurabile."""
    while True:
        try:
            create_backup(base)
        except Exception:
            pass
        wait = max(60, int(interval or 0))
        if base is not None:
            try:
                import json
                cfg = json.loads((Path(base) / "config.json").read_text(encoding="utf-8"))
                wait = max(300, int(cfg.get("backup_interval_min", 60)) * 60)
            except Exception:
                pass
        time.sleep(wait)


def list_backups(base=None, backup_dir=None):
    """Backup disponibili, dal più recente al più vecchio."""
    root = _backup_root(base or BASE, backup_dir)
    if not root.is_dir():
        return []
    out = []
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        files = [p.name for p in folder.iterdir() if p.is_file()]
        size = sum((folder / name).stat().st_size for name in files)
        out.append({"name": folder.name, "files": files, "size": size})
    return out


def restore_backup(base=None, name=None, backup_dir=None):
    """Ripristina JSON da un backup validato; crea prima una copia di sicurezza."""
    base = Path(base or BASE)
    root = _backup_root(base, backup_dir).resolve()
    if not name or not root.is_dir():
        raise ValueError("Backup non trovato.")
    folder = (root / str(name)).resolve()
    if folder.parent != root or not folder.is_dir():
        raise ValueError("Nome backup non valido.")
    create_backup(base, keep=_keep_count(base) + 1, backup_dir=backup_dir)
    restored = []
    for filename in BACKUP_NAMES:
        src = folder / filename
        if not src.is_file():
            continue
        dest = base / filename
        try:
            shutil.copy2(src, dest)
            restored.append(filename)
        except OSError as exc:
            raise ValueError(f"Ripristino fallito: {exc}") from exc
    if not restored:
        raise ValueError("Il backup non contiene dati ripristinabili.")
    # Il JSON è la fonte compatibile: elimina lo SQLite precedente così
    # class_repository lo ricostruisce senza mixare dati vecchi e nuovi.
    try:
        (base / "classifica.sqlite3").unlink(missing_ok=True)
    except OSError:
        pass
    return restored


def last_backup(base=None, backup_dir=None):
    root = _backup_root(base or BASE, backup_dir)
    if not root.is_dir():
        return None
    folders = [p for p in root.iterdir() if p.is_dir()]
    return max(folders, key=lambda p: p.name).name if folders else None




