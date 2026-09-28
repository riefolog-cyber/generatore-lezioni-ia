# -*- coding: utf-8 -*-
"""Rigenera il PLAYER (index.html, main.css, main.js, manifest, sw.js) delle
lezioni esistenti, senza toccare audio, sottotitoli o contenuti.

Perche' serve: il player viene scritto come file statici dentro ogni cartella
`*_lesson`, quindi NON si aggiorna da solo quando si modifica
`tools/player_template.py`. Senza questo passaggio le lezioni continuano a
servire il vecchio player (e' gia' successo: dopo un aggiornamento del player
gli studenti vedevano la grafica di una versione precedente, o la pagina
 bianca se il vecchio service worker serviva file non piu' compatibili).

Usato dal pannello ("Aggiorna il player di tutte le lezioni") e dalla linea di
comando:  python rigenera_player.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

from player_template import bust_cache, player_version, write_player  # noqa: E402


def lezioni(base=None):
    """Elenco delle lezioni generate (cartelle *_lesson con i dati)."""
    base = Path(base) if base else BASE
    out = []
    for d in sorted(base.glob("*_lesson")):
        if d.is_dir() and (d / "lesson-data.js").is_file() and (d / "index.html").is_file():
            out.append(d)
    return out


def _titolo(lesson_dir):
    try:
        raw = (lesson_dir / "lesson-data.js").read_text(encoding="utf-8")
        payload = json.loads(raw.split("=", 1)[1].strip().rstrip(";"))
        return payload.get("titolo") or lesson_dir.name.replace("_lesson", "")
    except Exception:
        return lesson_dir.name.replace("_lesson", "")


def rigenera(base=None, log=print):
    """Rigenera il player di tutte le lezioni. Ritorna (ok, messaggio)."""
    base = Path(base) if base else BASE
    trovate = lezioni(base)
    if not trovate:
        return True, "Nessuna lezione da aggiornare."
    log(f"Impronta del player: {player_version()}")
    fatte = saltate = 0
    for d in trovate:
        sw = d / "sw.js"
        prima = sw.read_text(encoding="utf-8").splitlines()[0] if sw.exists() else "(assente)"
        try:
            write_player(d, _titolo(d), tema="dark")
            bust_cache(d)
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ {d.name}: {e}")
            saltate += 1
            continue
        dopo = sw.read_text(encoding="utf-8").splitlines()[0] if sw.exists() else "(assente)"
        if prima == dopo:
            log(f"  · {d.name}: gia' aggiornata")
        else:
            log(f"  + {d.name}: {dopo}")
        fatte += 1
    msg = f"Player aggiornato in {fatte} lezioni."
    if saltate:
        msg += f" {saltate} fallite."
    return saltate == 0, msg


def main():
    ok, msg = rigenera()
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
