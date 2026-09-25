# -*- coding: utf-8 -*-
"""Diagnostica rete di classe + salute sistema (solo stdlib, offline).

Usato dal pannello (/api/diagnostica) e da riga di comando:
    python tools/netdiag.py
Ritorna un dict JSON-serializzabile: IP, porta, disco, dipendenze.
"""
import shutil
import socket
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))


def lan_ips():
    """Tutti gli IPv4 locali non-loopback (una per interfaccia)."""
    ips = set()
    # 1) hostname -> IPs
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    # 2) IP di uscita verso internet (fallisce senza internet: ok, si ignora)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def configured_ip():
    """IP configurato (lan_ip_fisso) + rilevati."""
    try:
        from common import load_config
        fisso = str(load_config().get("lan_ip_fisso") or "").strip()
    except Exception:
        fisso = ""
    import re
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", fisso or ""):
        fisso = ""
    return fisso, lan_ips()


def port_free(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", int(port)))
        return True
    except Exception:
        return False


def disk_free_mb(path=None):
    try:
        total, used, free = shutil.disk_usage(str(path or BASE))
        return {"totale_mb": round(total / 1048576, 1),
                "liberi_mb": round(free / 1048576, 1)}
    except Exception:
        return {"totale_mb": 0, "liberi_mb": 0}


def diagnose(port=None):
    """Report completo per il pannello."""
    try:
        from common import load_config
        cfg = load_config()
    except Exception:
        cfg = {}
    port = int(port or cfg.get("porta", 8341))
    fisso, ips = configured_ip()
    primario = fisso or (ips[0] if ips else "")
    out = {
        "port": port, "porta": port,
        "port_free": port_free(port), "porta_libera": port_free(port),
        "lan_ip_fisso": fisso, "lan_ips": ips, "lan_ip": primario,
        "ip": primario, "url_locale": f"http://localhost:{port}/",
        "url_lan": f"http://{primario}:{port}/" if primario else None,
        "same_configured_ip": bool(fisso and fisso in ips),
        "stesso_ip_configurato": bool(fisso and fisso in ips),
        "wifi_ok": bool(primario), "firewall_ok": None,
        "disk_free_gb": round(disk_free_mb()["liberi_mb"] / 1024, 1),
        "disco": disk_free_mb(),
        "warnings": ([] if primario else ["Nessun indirizzo LAN rilevato: controlla Wi-Fi o configura lan_ip_fisso in config.json."]),
        "lezioni": len([p for p in BASE.glob("*_lesson")
                        if p.is_dir() and (p / "index.html").exists()]),
        "deps": {},
        "ok": bool(primario),
    }
    for mod in ("docx", "edge_tts", "pypdf", "youtube_transcript_api"):
        try:
            __import__(mod)
            out["deps"][mod] = True
        except Exception:
            out["deps"][mod] = False
    out["deps"]["ffmpeg"] = bool(shutil.which("ffmpeg"))
    return out


if __name__ == "__main__":
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(diagnose(), indent=2, ensure_ascii=False))
