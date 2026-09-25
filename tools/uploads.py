# -*- coding: utf-8 -*-
"""Salvataggio atomico e sicuro dei materiali caricati dal pannello."""
from __future__ import annotations

import os
import stat
import tempfile
import time
from pathlib import Path


class UploadTooBig(ValueError):
    """File oltre il limite configurato."""


def save_upload(base, raw_name, data, supported_ext, max_bytes):
    """Salva un file e ritorna (nome Finale, sostituito,copiato).

    Scrittura atomica, retry su Windows e fallback `nome (2).ext` se il file
    è bloccato da un altro programma.
    """
    base = Path(base)
    safe = Path(raw_name or "").name.strip()
    if not safe:
        raise ValueError("Nome file non valido.")
    if Path(safe).suffix.lower() not in supported_ext:
        raise ValueError(
            f"Formato non supportato ({Path(safe).suffix or 'nessuna estensione'}): "
            "usa .docx, .pdf, .pptx, .epub, .txt, .md, .html, .mp3, .m4a o .wav.")
    if not data:
        raise ValueError("Il file ricevuto è vuoto.")
    if len(data) > max_bytes:
        raise UploadTooBig(f"«{safe}» supera il limite di {max_bytes / 1048576:.0f} MB.")
    dest = base / safe
    replaced = dest.exists()
    fd, tmp_path = tempfile.mkstemp(dir=str(base), prefix=".upload-", suffix=".tmp")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    tmp = Path(tmp_path)
    saved = False
    try:
        try:
            os.chmod(dest, stat.S_IWRITE)
        except OSError:
            pass
        for attempt in range(6):
            try:
                os.replace(tmp, dest)
                saved = True
                break
            except PermissionError:
                if attempt < 5:
                    time.sleep(0.5)
        if not saved:
            stem, ext = dest.stem, dest.suffix
            for number in range(2, 12):
                candidate = base / f"{stem} ({number}){ext}"
                if candidate.exists():
                    continue
                try:
                    os.replace(tmp, candidate)
                except PermissionError:
                    continue
                dest = candidate
                replaced = False
                saved = True
                break
        if not saved:
            raise ValueError(
                f"«{safe}» è bloccato da un altro programma (PDF/Word aperto?) "
                "e non riesco a salvarlo nemmeno come copia: chiudilo e riprova.")
        return dest.name, replaced, dest.name != safe
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
