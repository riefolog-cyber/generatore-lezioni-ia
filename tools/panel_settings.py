# -*- coding: utf-8 -*-
"""Configurazione editabile dal pannello.

Scrive solo chiavi note e valide, in modo atomico. Non espone mai
``llm_api_key`` o altri segreti verso l'interfaccia.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE / "config.json"

PUBLIC_FIELDS = {
    "porta": ("number", 1024, 65535, 8341),
    "max_upload_mb": ("number", 1, 1000, 100),
    "cache_max_mb": ("number", 50, 2000, 300),
    "whisper_model": ("choice", ("tiny", "base", "small"), None, "base"),
    "pin_docente": ("text", 0, 12, ""),
    "lan_ip_fisso": ("text", 0, 15, ""),
    "theme": ("choice", ("dark", "light"), None, "dark"),
}


def _validate(name, value):
    kind, lo, hi, default = PUBLIC_FIELDS[name]
    if kind == "number":
        try:
            clean = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"Valore non valido per {name}.")
        if not lo <= clean <= hi:
            raise ValueError(f"Valore fuori intervallo per {name}: {lo}-{hi}.")
        return clean
    if kind == "choice":
        clean = str(value or default)
        if clean not in lo:
            raise ValueError(f"Valore non valido per {name}.")
        return clean
    clean = str(value or "")[:hi]
    if name == "lan_ip_fisso" and clean and not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", clean):
        raise ValueError("Indirizzo IP non valido.")
    if name == "pin_docente" and clean and not clean.isdigit():
        raise ValueError("Il PIN deve contenere solo numeri.")
    return clean


def public_config(path=None):
    """Valori non sensibili pronti per la UI."""
    path = Path(path or CONFIG_FILE)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
    out = {}
    for name, (_, _, _, default) in PUBLIC_FIELDS.items():
        value = data.get(name, default)
        try:
            out[name] = _validate(name, value)
        except ValueError:
            out[name] = default
    # Non restituisce mai il PIN in chiaro; basta sapere se è impostato.
    out["pin_docente"] = ""
    out["pin_configured"] = bool(data.get("pin_docente"))
    return out


def update_public(values, path=None):
    """Valida e salva atomicamente; restituisce la configurazione pubblica."""
    if not isinstance(values, dict):
        raise ValueError("Configurazione non valida.")
    clean = {k: _validate(k, v) for k, v in values.items() if k in PUBLIC_FIELDS}
    path = Path(path or CONFIG_FILE)
    current = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except (OSError, ValueError, TypeError):
            current = {}
    current.update(clean)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
    return public_config(path)
