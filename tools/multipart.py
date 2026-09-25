# -*- coding: utf-8 -*-
"""Parser delle richieste multipart usate dal pannello."""
from __future__ import annotations

import re
import urllib.parse


def parse_multipart(body: bytes, content_type: str):
    """Ritorna [(nome_file, dati)] validando boundary e file vuoti."""
    match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type or "")
    if not match:
        raise ValueError("Richiesta non multipart (manca il boundary).")
    separator = b"--" + (match.group(1) or match.group(2)).encode("utf-8")
    files = []
    for part in body.split(separator):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head_end = part.find(b"\r\n\r\n")
        if head_end < 0:
            continue
        headers = part[:head_end].decode("utf-8", "replace")
        if 'name="file"' not in headers:
            continue
        plain = re.search(r'filename="([^"]*)"', headers)
        name = plain.group(1) if plain else None
        if name is None:
            encoded = re.search(r"filename\*\s*=\s*[^']*''([^;\s]+)", headers)
            if encoded:
                try:
                    name = urllib.parse.unquote(encoded.group(1))
                except Exception:
                    name = None
        if name:
            files.append((name, part[head_end + 4:]))
    if not files:
        raise ValueError("Nessun file ricevuto nella richiesta.")
    if any(not data for _, data in files):
        raise ValueError("Uno dei file ricevuti è vuoto (0 byte).")
    return files
