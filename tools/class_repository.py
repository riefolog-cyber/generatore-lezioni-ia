# -*- coding: utf-8 -*-
"""Archivio SQLite della classifica di classe.

Mantiene la compatibilità con il vecchio ``classifica.json``:
- importa automaticamente i dati JSON al primo avvio;
- usa SQLite per letture e scritture successive;
- conserva il JSON come copia di compatibilità per backup e installazioni vecchie.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

MAX_ROWS = 500
_WRITE_LOCK = threading.RLock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS risultati (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t TEXT NOT NULL,
    lesson TEXT NOT NULL,
    studente TEXT NOT NULL,
    punti INTEGER NOT NULL,
    totale INTEGER NOT NULL,
    pct INTEGER NOT NULL,
    completata INTEGER NOT NULL,
    tempo_min REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risultati_lesson ON risultati(lesson);
CREATE INDEX IF NOT EXISTS idx_risultati_studente ON risultati(lesson, studente);
"""


def db_path_for(json_path: Path) -> Path:
    """Database associato al JSON; utile per test e directory temporanee."""
    return Path(json_path).with_suffix(".sqlite3")


def _connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _json_rows(json_path: Path):
    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8") or "[]")
    except (OSError, ValueError, TypeError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _trim(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM risultati WHERE id NOT IN "
        "(SELECT id FROM risultati ORDER BY id DESC LIMIT ?)", (MAX_ROWS,))


def _migrate_if_needed(json_path: Path, conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM risultati").fetchone()[0]
    if count or not Path(json_path).is_file():
        return
    rows = _json_rows(json_path)
    if not rows:
        return
    with conn:
        for r in rows:
            try:
                totale = int(r.get("totale", 0) or 0)
                punti = int(r.get("punti", 0) or 0)
                pct = int(r.get("pct", round(punti / totale * 100) if totale else 0))
                conn.execute(
                    "INSERT INTO risultati(t, lesson, studente, punti, totale, pct, completata, tempo_min) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(r.get("t") or time.strftime("%Y-%m-%d %H:%M"))[:40],
                     str(r.get("lesson") or "")[:80], str(r.get("studente") or "Anonimo")[:40],
                     punti, totale, pct, int(bool(r.get("completata"))),
                     float(r.get("tempo_min", 0) or 0)))
            except (TypeError, ValueError):
                continue
    _trim(conn)


def _write_json_compat(json_path: Path, rows: list[dict]) -> None:
    try:
        tmp = Path(json_path).with_suffix(Path(json_path).suffix + ".tmp")
        tmp.write_text(json.dumps(rows[-MAX_ROWS:], ensure_ascii=False), encoding="utf-8")
        tmp.replace(json_path)
    except OSError:
        pass


def add_result(json_path: Path, lesson: str, studente: str, punti: int, totale: int,
               completata: bool, tempo_min: float) -> None:
    json_path = Path(json_path)
    with _WRITE_LOCK:
        conn = _connect(db_path_for(json_path))
        try:
            _migrate_if_needed(json_path, conn)
            pct = round(punti / totale * 100) if totale > 0 else 0
            with conn:
                conn.execute(
                    "INSERT INTO risultati(t, lesson, studente, punti, totale, pct, completata, tempo_min) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (time.strftime("%Y-%m-%d %H:%M"), str(lesson)[:80], str(studente)[:40],
                     int(punti), int(totale), pct, int(bool(completata)), float(tempo_min)))
                _trim(conn)
            _write_json_compat(json_path, list_results(json_path))
        finally:
            conn.close()


def list_results(json_path: Path) -> list[dict]:
    json_path = Path(json_path)
    conn = _connect(db_path_for(json_path))
    try:
        _migrate_if_needed(json_path, conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM risultati ORDER BY id")]
        return [{**r, "completata": bool(r["completata"])} for r in rows]
    finally:
        conn.close()


def reset(json_path: Path) -> None:
    json_path = Path(json_path)
    conn = _connect(db_path_for(json_path))
    try:
        with conn:
            conn.execute("DELETE FROM risultati")
        _write_json_compat(json_path, [])
    finally:
        conn.close()


def authorized(pin: str | None, supplied: str | None) -> bool:
    expected = str(pin or "").strip()
    return not expected or expected == str(supplied or "")
