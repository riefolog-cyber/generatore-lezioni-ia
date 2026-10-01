# -*- coding: utf-8 -*-
"""Pannello di controllo web (locale).

Sostituisce l'avvio da terminale con una pagina grafica nel browser:
  - caricamento materiale via upload (drag & drop o Sfoglia: documenti,
    PPTX, EPUB, testo e audio MP3/M4A/WAV) con pulsante "Carica e genera";
  - trascrizione locale dell'audio con Whisper, se installato;
  - generazione da URL (sito web o video YouTube);
  - opzioni: rigenera anche se esiste (--force), bozza senza LLM (--bozza),
    rigenera solo l'audio di una lezione già generata (--reaudio);
  - console di log in tempo reale durante la generazione;
  - elenco delle lezioni generate con pulsante "Apri".

Sicurezza (vedi tools/http_safety.py):
  - le API /api/* rispondono SOLO alle richieste da localhost; dalla LAN si
    raggiungono solo le lezioni, l'indice e l'invio della classifica;
  - l'header Host deve essere un indirizzo di QUESTA macchina: senza questo
    controllo, un sito visitato dal docente potrebbe puntare il proprio
    dominio a 127.0.0.1 (DNS-rebinding) e comandare il pannello;
  - le POST sono rifiutate se Origin/Sec-Fetch-Site indicano un'altra origine
    (CSRF sulle richieste "semplici" come l'upload multipart);
  - se in Impostazioni è impostato un PIN docente, è richiesto per TUTTE le
    operazioni che modificano qualcosa (con PIN vuoto l'unica barriera resta
    il controllo di loopback);
  - la classifica (nomi, puni) e il suo export CSV sono protetti dal PIN quando
    la richiesta arriva dalla LAN, perché sono dati di minori;
  - i file del progetto (config.json, sorgenti, .git…) non vengono mai serviti.

Uso:  python panel.py        (avvia il pannello e apre il browser)
      python panel.py --port 9000
"""
import functools
import http.server
import importlib.util
import io
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

from common import load_config, write_text_atomic  # noqa: E402
from class_repository import add_result as _class_repo_add, authorized as _class_repo_authorized  # noqa: E402
from class_repository import list_results as _class_repo_list, reset as _class_repo_reset  # noqa: E402
from http_safety import (  # noqa: E402
    LOOPBACK_HOSTS, host_allowed, same_origin_post, safe_request_path)
from sources import SUPPORTED_EXT, is_url  # noqa: E402
from start_lesson import _RangeHandler, _hub_page, find_port, list_lessons  # noqa: E402

CONFIG = load_config()
DEFAULT_PORT = int(CONFIG.get("porta", 8341))
MAX_UPLOAD_MB = int(CONFIG.get("max_upload_mb", 100))  # configurabile
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
# soffitto assoluto sul corpo di una richiesta: `max_upload_mb` arriva a 1000
# dalla UI, e MAX_UPLOAD_BYTES*10 diventava ~10 GB per richiesta (tutto in RAM)
MAX_BODY_BYTES = 512 * 1024 * 1024
UPLOAD_FILES_PER_REQUEST = 10
RUNTIME_PORT = DEFAULT_PORT  # valore reale, utile se find_port sceglie 8342 ecc.

# Il pannello importa new_lesson UNA volta: i moduli restano in memoria anche
# se i file cambiano su disco (es. aggiornamenti del codice). Se uno dei file
# della pipeline cambia dopo l'avvio, il codice caricato è "stantio": /api/state
# lo segnala e l'interfaccia mostra l'avviso di riavvio, così una generazione
# non parte mai silenziosamente con il codice vecchio.
_PIPELINE_FILES = ("new_lesson.py", Path("tools") / "sources.py",
                   Path("tools") / "common.py", Path("tools") / "player_template.py")


def _pipeline_mtime():
    tot = 0.0
    for rel in _PIPELINE_FILES:
        try:
            tot += (BASE / rel).stat().st_mtime
        except OSError:
            pass
    return tot


_PIPELINE_MTIME_START = _pipeline_mtime()

# ------------------------------------------------------------------ job runner
from tools.jobs import JobManager  # noqa: E402

def _lan_selftest(ip, port):
    """Verifica che l'URL LAN risponda davvero (visto dal PC stesso).

    Non garantisce che il telefono lo raggiunga (quello dipende dal Wi-Fi
    e dal firewall), ma becca subito: server non in ascolto su 0.0.0.0,
    porta sbagliata, IP non locale. Ritorna dict JSON con ok+detail.
    """
    if not ip:
        return {"ok": False, "detail": "nessun IP LAN rilevato"}
    url = f"http://{ip}:{port}/"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "selftest"})
        with urllib.request.urlopen(req, timeout=4) as r:
            body = r.read(2000).decode("utf-8", "replace")
            if r.status == 200 and ("lezioni" in body.lower() or "lesson" in body.lower()
                                    or "<html" in body.lower()):
                return {"ok": True, "detail": f"{url} risponde ✔",
                        "url": url}
            return {"ok": False, "detail": f"{url} risponde ma pagina strana (stato {r.status})",
                    "url": url}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "10013" in msg or "denied" in msg.lower() or "permesso" in msg.lower():
            hint = " — quasi sicuramente il Firewall di Windows blocca Python: consenti Python (reti private) e riprova."
        elif "10049" in msg or "assign" in msg.lower():
            hint = " — questo IP non appartiene più al PC (rete cambiata?): controlla il Wi-Fi."
        elif "refused" in msg.lower() or "10061" in msg:
            hint = " — server non in ascolto su questa interfaccia: riavvia con AVVIA.bat."
        elif "timed out" in msg.lower() or "timeout" in msg.lower():
            hint = " — nessuna risposta: PC e telefono devono stare sulla STESSA Wi-Fi."
        else:
            hint = ""
        return {"ok": False, "detail": f"{url} non risponde ({msg}){hint}", "url": url}


HISTORY_FILE = BASE / "job_history.json"

# rate-limit build: max 60 build/ora per IP (rete scolastica / click multipli)
# 20/ora era SOTTO l'uso naturale ("Carica e genera tutto" con 10 file ne fa
# 10) e il dict cresceva senza eviction per ogni IP visto.
_RATE = {}
_RATE_LOCK = threading.Lock()
RATE_MAX = 60
RATE_WINDOW = 3600


def _rate_ok(ip):
    now = time.time()
    with _RATE_LOCK:
        lst = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
        if len(lst) >= RATE_MAX:
            _RATE[ip] = lst
            return False
        lst.append(now)
        _RATE[ip] = lst
        # eviction: dimentica gli IP la cui finestra è scaduta
        if len(_RATE) > 256:
            for k in [k for k, v in _RATE.items()
                      if not any(now - t < RATE_WINDOW for t in v)]:
                _RATE.pop(k, None)
        return True


def _history_append(kind, source, ok, secs):
    """Read-modify-write sotto lock + scrittura atomica.

    Senza lock due job che finiscono insieme perdevano righe; senza
    os.replace un crash a metà lasciava un file troncato.
    """
    try:
        with _HISTORY_LOCK:
            hist = []
            if HISTORY_FILE.exists():
                hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8") or "[]")
            if not isinstance(hist, list):
                hist = []
            hist.append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
                         "source": str(source)[:160], "ok": bool(ok),
                         "secs": round(secs or 0, 1)})
            write_text_atomic(HISTORY_FILE, json.dumps(hist[-100:], ensure_ascii=False))
    except Exception:
        pass


_HISTORY_LOCK = threading.Lock()


def _history_clear():
    """Svuota la cronologia generazioni (pulsante nel pannello)."""
    try:
        write_text_atomic(HISTORY_FILE, "[]")
    except OSError:
        pass


def _classifica_reset():
    """Azzera la classifica di classe (pulsante nel pannello)."""
    try:
        _class_repo_reset(CLASSIFICA_FILE)
    except OSError:
        pass


class _UploadTooBig(Exception):
    """File caricato dal pannello oltre il limite (risponde HTTP 413)."""


# ------------------------------------------------------------------ classifica di classe
# Gli studenti (anche da tablet sulla LAN) inviano il risultato del percorso;
# il pannello lo accumula in classifica.json e mostra la classifica per lezione.
CLASSIFICA_FILE = BASE / "classifica.json"
# il limite delle righe su disco vive in class_repository.MAX_ROWS: qui era
# duplicato (_CLASSIFICA_MAX = 500) e mai usato, quindi due numeri da tenere
# allineati senza che nessuno dei due venisse letto
_CLASSIFICA_PANEL = 50         # righe mostrate in classifica per lezione


def _int_clamp(value, lo, hi, default=0):
    """Intero tra `lo` e `hi`, ripiegando su `default` su qualsiasi input.

    `int("abc")` sollevava ValueError e la risposta allo studente conteneva il
    messaggio interno di Python ("invalid literal for int() with base 10"):
    un dettaglio della macchina del docente, consegnato dalla rete di classe.
    Un campo malformato vale 0, e i limiti tengono fuori i valori assurdi.
    """
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _classifica_add(lesson, studente, punti, totale, completata, tempo_min,
                    errori=0):
    """Aggiunge un risultato (chiamato anche da client LAN: nessun segreto)."""
    _class_repo_add(CLASSIFICA_FILE, lesson, studente, punti, totale,
                    completata, tempo_min, errori)


def _classifica_view():
    """Classifica per lezione ordinata dall'indice: la MEDIA degli errori e
    del tempo impiegato per concludere le attivita'.

    La formula e' in tools/classifica_score.py: qui si raggruppa per lezione e
    si restituisce gia' ordinata, con `indice` (0-100, 100 = nessun errore e il
    tempo piu' rapido) e `media` (la penalita' 0-100) accanto ai dati grezzi.
    """
    from tools import classifica_score
    try:
        rows = _class_repo_list(CLASSIFICA_FILE)
    except Exception:
        rows = []
    by_lesson = {}
    for r in rows:
        if isinstance(r, dict) and r.get("lesson"):
            by_lesson.setdefault(str(r["lesson"]), []).append(r)
    out = []
    for lesson in sorted(by_lesson):
        ranked = classifica_score.ordina(classifica_score.punta(by_lesson[lesson]))
        out.append({"lesson": lesson, "rows": ranked[:_CLASSIFICA_PANEL]})
    return {"classifiche": out}


def _csv_cell(value):
    """Cella CSV sicura: neutralizza la CSV/formula injection.

    Un valore che inizia con = + - @ (o che contiene ; " o a capo) viene
    quotato: senza questo, il nome di uno studente come
    `=cmd|'/C calc'!A0` verrebbe eseguito da Excel all'apertura del file
    che il docente scarica.
    """
    s = str(value)
    if s and s[0] in "=+-@\t\r":
        s = "'" + s
    if ";" in s or '"' in s or "\n" in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


# ------------------------------------------------------------------ cache whitelist lezioni
# La whitelist delle lezioni viene valutata a ogni richiesta (anche per gli mp3
# durante la riproduzione). Un piccolo TTL evita glob su disco a ogni asset;
# l'invalidazione esplicita (dopo upload/generazione/ri-audio) rende le novità
# subito disponibili senza aspettare la scadenza.
LESSONS_CACHE_TTL = 2.0
_lessons_cache = {"at": 0.0, "names": None, "hub": None, "singles": None}
_LESSONS_CACHE_LOCK = threading.RLock()   # le richieste sono in thread paralleli


def _bump_lessons_cache():
    with _LESSONS_CACHE_LOCK:
        now = time.time()
        if _lessons_cache["names"] is None or now - _lessons_cache["at"] > LESSONS_CACHE_TTL:
            _lessons_cache.update(at=now,
                                  names={l.name for l in list_lessons()},
                                  singles={p.name for p in BASE.glob("*_singola.html")
                                           if p.is_file()},
                                  hub=None)


def _allowed_lesson_names():
    _bump_lessons_cache()
    return _lessons_cache["names"] or set()


def _allowed_single_names():
    _bump_lessons_cache()
    return _lessons_cache["singles"] or set()


def _hub_page_cached():
    _bump_lessons_cache()
    if _lessons_cache["hub"] is None:
        _lessons_cache["hub"] = _hub_page()
    return _lessons_cache["hub"]


def _invalidate_lessons_cache():
    with _LESSONS_CACHE_LOCK:
        _lessons_cache["names"] = None
        _lessons_cache["singles"] = None
        _lessons_cache["hub"] = None
        _lessons_cache["at"] = 0.0
    # lo snapshot di /api/state e le info delle lezioni vanno ricalcolati:
    # dopo una generazione cambia il contenuto delle cartelle
    _STATE_CACHE.update(at=0.0, data=None)
    _LESSON_INFO_CACHE.clear()


def _flog(txt):
    """Log persistente su file (diagnosi errori API anche dopo il riavvio)."""
    try:
        with open(BASE / "panel_errors.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " | " + str(txt).rstrip() + "\n")
    except OSError as e:
        # se disco pieno, almeno log su stderr
        try:
            print(f"_flog failed: {e}", file=sys.stderr)
        except Exception:
            pass


_JOBS = JobManager(_history_append, _invalidate_lessons_cache, _flog)
LOG = _JOBS.log
JOB = _JOBS.state
QUEUE = _JOBS.queue
LOG_LOCK = LOG.lock


def _log(txt):
    with LOG_LOCK:
        LOG.append(str(txt).rstrip())


def _material_job_pending(source_name):
    """True se lo stesso materiale è già in lavorazione o accodato."""
    return _JOBS.pending_source(source_name)


def start_job(fn, kind, source):
    """Lancia una generazione in background. Se una è in corso, accoda (max 5)."""
    return _JOBS.start(fn, kind, source)


def cancel_queue():
    return _JOBS.cancel_queue()


# ------------------------------------------------------------------ helpers
def _local_ips():
    """Tutti gli IPv4 locali (per verificare che l'IP fisso esista davvero)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    for dest in (("8.8.8.8", 80), ("192.168.0.1", 80)):
        # `with`: prima close() stava DENTRO il try, dopo getsockname(): se
        # connect() o getsockname() sollevava il socket non veniva mai
        # chiuso. Queste funzioni girano ogni 30 s (TTL della cache degli
        # host ammessi), quindi il leak era cumulativo.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1.5)
                s.connect(dest)
                ip = s.getsockname()[0]
            if not ip.startswith("127."):
                ips.add(ip)
        except Exception:
            pass
    return ips


def _default_route_ip():
    """IP dell'interfaccia con la route predefinita (= rete che va a internet)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return None


def lan_ip():
    # Rete di classe SENZA internet: l'auto-rilevamento via 8.8.8.8 fallisce,
    # per questo esiste lan_ip_fisso in config (192.168.0.2). A casa però il
    # .0.2 non esiste e il QR punterebbe al vuoto: il fisso vale SOLO se è
    # davvero assegnato a questa macchina.
    # Se il fisso non è attivo, meglio l'IP della route predefinita (quello
    # che il telefono può davvero raggiungere da questa rete).
    # L'IP non cambia più volte al secondo: risultato in cache 30 s, così il
    # refresh del pannello non fa 2 getaddrinfo + 2 socket per ogni poll.
    now = time.time()
    if _LAN_CACHE["ip"] is not None and now - _LAN_CACHE["at"] < 30.0:
        return _LAN_CACHE["ip"]
    ip = _lan_ip_uncached()
    _LAN_CACHE.update(at=now, ip=ip, warn=_lan_warning_uncached())
    return ip


_LAN_CACHE = {"at": 0.0, "ip": None, "warn": None}


def _lan_ip_uncached():
    fisso = ""
    try:
        fisso = str(CONFIG.get("lan_ip_fisso") or "").strip()
    except Exception:
        fisso = ""
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", fisso or ""):
        try:
            if fisso in _local_ips():
                return fisso
        except Exception:
            pass
        # fisso non attivo su questa macchina: fallback qui sotto
    auto = _default_route_ip()
    if auto:
        return auto
    try:
        for ip in sorted(_local_ips()):
            return ip
    except Exception:
        pass
    return None


def lan_warning():
    """Messaggio se l'IP fisso da config non è attivo (QR a casa)."""
    lan_ip()   # popola la cache
    return _LAN_CACHE["warn"]


def _lan_warning_uncached():
    try:
        fisso = str(CONFIG.get("lan_ip_fisso") or "").strip()
    except Exception:
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", fisso or ""):
        try:
            if fisso not in _local_ips():
                auto = _default_route_ip()
                if auto:
                    return (f"IP fisso {fisso} non attivo su questo PC "
                            f"(sei su un'altra rete: uso {auto}). "
                            f"In classe torna tutto da sé.")
                return (f"IP fisso {fisso} non attivo su questo PC: "
                        f"il QR potrebbe non aprirsi da qui.")
            # fisso attivo MA non è la route predefinita (doppia rete:
            # cavo scuola + Wi-Fi classe): avvisa comunque, perché il
            # telefono sulla rete di tutti i giorni non lo raggiunge.
            auto = _default_route_ip()
            if auto and auto != fisso:
                return (f"Stai usando anche un'altra rete ({auto}): "
                        f"per la classe usa il QR {fisso}, "
                        f"per questa rete usa {auto}.")
        except Exception:
            pass
    return None


def _lesson_for(filename):
    stem = Path(filename).stem
    return f"{re.sub(r'[^A-Za-z0-9_]+', '_', stem).strip('_') or 'Lezione'}_lesson"


# Solo upload: niente più scansione della cartella progetto.
# I materiali generabili sono SOLO quelli caricati dal pannello in questa
# sessione (drag&drop / Sfoglia). I file già presenti su disco non compaiono.
UPLOADED = {}


def _materials():
    out = []
    # snapshot della lista: `_register_upload` e `_resolve_source` inseriscono
    # in UPLOADED da altri thread mentre iteriamo (prima: RuntimeError
    # "dictionary changed size during iteration")
    for name in sorted(list(UPLOADED.keys())):
        p = BASE / name
        try:
            st = p.stat()          # una sola stat(), non due
        except OSError:
            continue
        out.append({
            "name": name,
            "size": st.st_size,
            "modified": st.st_mtime,
            "lesson": _lesson_for(name),
        })
    return out


def _register_upload(name):
    UPLOADED[Path(name).name] = {"t": time.time()}


def _deps():
    def have(m):
        # find_spec e NON __import__: chiedere "e' installata?" non deve
        # eseguire la libreria. Con __import__ questo helper costava ~1,5 s
        # (edge_tts da sola ~1,1 s), e _deps sta dentro _state(), quindi il
        # costo ricadeva su ogni snapshot freddo del pannello: all'avvio e a
        # ogni invalidazione della cache (upload, generazione), cioe' proprio
        # quando il docente sta aspettando. find_spec risponde in microsecondi
        # e senza effetti collaterali.
        try:
            return importlib.util.find_spec(m) is not None
        except (ImportError, ValueError):
            # ValueError: il modulo esiste in sys.modules ma ha __spec__ = None
            return False
    def whisper_disponibile():
        """Whisper è disponibile se c'è faster-whisper (pip) OPPURE whisper.cpp.

        Prima si guardava SOLO il modulo `faster_whisper`, quindi su Windows ARM
        il pannello diceva sempre "Whisper ✗": lì faster-whisper non è
        installabile (CTranslate2 non ha wheel win_arm64) e il progetto ripiega
        su whisper.cpp, un binario che pip non vede. Risultato: la trascrizione
        funzionava e il pannello la dava per assente — un falso negativo che
        aveva fatto perdere tempo anche per capire che il materiale audio era
        già supportato. Ora la stessa logica di `check_env.py` e di
        `sources._load_whisper_model`: un backend basta.
        """
        if have("faster_whisper") or have("whisper"):
            return True
        try:
            sys.path.insert(0, str(BASE / "tools"))
            from whisper_cpp import find_binary
            return find_binary() is not None
        except Exception:
            return False
    return {
        "python_docx": have("docx"),
        "edge_tts": have("edge_tts"),
        "pypdf": have("pypdf"),
        "youtube_transcript_api": have("youtube_transcript_api"),
        # chiave generica: il pannello non deve sapere quale backend sia in uso,
        # solo se la trascrizione è possibile
        "whisper": whisper_disponibile(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def _single_files():
    """File HTML unici generati (Nome_singola.html) con dimensione."""
    out = []
    for p in sorted(BASE.glob("*_singola.html")):
        if p.is_file():
            try:
                out.append({"name": p.name,
                            "stem": p.name.replace("_singola.html", ""),
                            "size": p.stat().st_size})
            except OSError:
                continue
    return out


_LESSON_INFO_CACHE = {}   # name -> (mtime_max, info)


def _lesson_details(name):
    """Info di una lezione, memoizzate per mtime del pacchetto.

    `lesson_info` fa rglob + stat su ogni file e rilegge e riparsa
    lesson-data.js: con 20 lezioni da ~60 file erano ~1200 stat() e 15
    riparse al minuto, solo perché il pannello fa refresh ogni 4 s.
    """
    d = BASE / name
    try:
        stamp = max((p.stat().st_mtime for p in d.rglob("*")), default=0.0)
    except OSError:
        stamp = 0.0
    hit = _LESSON_INFO_CACHE.get(name)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        from tools.lesson_admin import lesson_info
        info = lesson_info(BASE, name)
    except Exception:
        info = {"name": name, "title": name.replace("_lesson", ""),
                "size": 0, "duration": 0, "modified": 0}
    _LESSON_INFO_CACHE[name] = (stamp, info)
    return info


_STATE_CACHE = {"at": 0.0, "data": None}
_STATE_TTL = 15.0


def _state():
    """Snapshot completo del pannello, con cache breve.

    L'interfaccia fa refresh ogni 4 s: senza cache, ogni poll ripagava un
    rglob per lezione, due getaddrinfo per l'IP LAN e le dipendenze. La
    cache viene invalidata esplicitamente a ogni upload/generazione.
    """
    now = time.time()
    if _STATE_CACHE["data"] is not None and now - _STATE_CACHE["at"] < _STATE_TTL:
        return _STATE_CACHE["data"]
    lessons = [_lesson_details(l.name) for l in list_lessons()]
    try:
        from tools.lesson_admin import list_archived
        archived = list_archived(BASE)
    except Exception:
        archived = []
    data = {
        "materials": _materials(), "lessons": lessons, "archived": archived,
        "singles": _single_files(), "deps": _deps(),
        "lan_ip": lan_ip(), "port": RUNTIME_PORT,
        "lan_warning": lan_warning(),
        "max_upload_mb": MAX_UPLOAD_MB,
        "pipeline_stantia": _pipeline_mtime() != _PIPELINE_MTIME_START,
        "config": {k: CONFIG.get(k) for k in
                   ("theme", "voice", "edge_voice", "edge_rate",
                    "llm_model", "num_moduli_min", "num_moduli_max",
                    "profilo_durata", "profilo_livello", "profilo_obiettivo",
                    "whisper_model")},
        "voices": EDGE_VOICES,
    }
    _STATE_CACHE.update(at=now, data=data)
    return data


EDGE_VOICES = [
    "it-IT-GiuseppeMultilingualNeural",
    "it-IT-IsabellaNeural",
    "it-IT-DiegoNeural",
    "it-IT-ElsaNeural",
]
_VOICES_CACHE = {"at": 0.0, "names": None}


def _edge_voices_live():
    """Lista voci it-IT dal servizio (cache 1h), fallback alla lista statica."""
    if _VOICES_CACHE["names"] and time.time() - _VOICES_CACHE["at"] < 3600:
        return _VOICES_CACHE["names"]
    try:
        import asyncio
        import edge_tts

        async def _list():
            return await edge_tts.list_voices()

        vs = asyncio.run(_list())
        names = sorted(v["ShortName"] for v in vs
                       if str(v.get("Locale", "")).startswith("it"))
        if names:
            _VOICES_CACHE.update(at=time.time(), names=names)
            return names
    except Exception:
        pass
    return list(EDGE_VOICES)


def _loopback(handler):
    return handler.client_address[0] in LOOPBACK_HOSTS or \
        handler.client_address[0] == "::ffff:127.0.0.1"


# ------------------------------------------------- anti DNS-rebinding / CSRF
# Gli host ammessi sono gli indirizzi di QUESTA macchina: il pannello è
# raggiungibile in locale e in LAN, mai via un nome di dominio arbitrario.
# Senza questo controllo un sito visitato dal docente può puntare il proprio
# dominio a 127.0.0.1 (DNS-rebinding) e parlare con il pannello come se fosse
# same-origin: il check su client_address passerebbe, perché la connessione
# arriva davvero dal loopback.
_HOSTS_CACHE = {"at": 0.0, "hosts": None}
_HOSTS_TTL = 30.0


def _allowed_hosts():
    """Insieme (lowercase, senza porta) degli host che possono servire il
    pannello: loopback + IP locali + IP fisso configurato."""
    now = time.time()
    cached = _HOSTS_CACHE["hosts"]
    if cached is not None and now - _HOSTS_CACHE["at"] < _HOSTS_TTL:
        return cached
    hosts = {"localhost", "127.0.0.1", "::1", "[::1]"}
    try:
        fisso = str(CONFIG.get("lan_ip_fisso") or "").strip()
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", fisso):
            hosts.add(fisso)
    except Exception:
        pass
    for ip in _local_ips():
        hosts.add(ip)
    # snapshot iniziale sempre incluso: se la rete salta, gli host già noti
    # restano ammessi e il docente non rimane fuori dal pannello
    hosts |= (cached or set())
    _HOSTS_CACHE.update(at=now, hosts=hosts)
    return hosts


def _host_ok(handler):
    return host_allowed(handler.headers.get("Host"), _allowed_hosts(), RUNTIME_PORT)


# ------------------------------------------------------------------ handler
class PanelHandler(_RangeHandler):
    """Serve: pannello + API (solo localhost) + lezioni (tutti, whitelist)."""

    # -- API ------------------------------------------------------------------
    def _api(self, path, query):
        """Instrada le GET usando il registro in tools/panel_routes.py.

        Prima era una catena di 16 `if path == "/api/..."`: ogni rotta era
        definita in un punto solo ma la lista viveva incastrata nel codice, e il
        path `/api/reaudio` compariva sia qui sia in do_POST. Il registro è ora
        l'unica fonte di verità e le guardie (localhost / PIN) sono dichiarate
        per rotta invece che dedotte dalla posizione nell'if.
        """
        from tools import panel_routes

        rotta = panel_routes.lookup("GET", path)
        if rotta is None:
            self.send_error(404, "API sconosciuta")
            return
        if rotta.guardia == panel_routes.PIN_CLASSE:
            # dalla rete di classe i dati degli studenti (nome, punti) sono
            # visibili solo con il PIN docente
            if not _loopback(self) and not self._teacher_allowed():
                self._reject(403, "PIN docente richiesto per questa API.")
                return
        elif rotta.guardia == panel_routes.PUBBLICA:
            # raggiungibile anche dalla rete: al momento solo il timer di classe,
            # che e' un orario di fine e non un dato del docente. Il controllo
            # Host (anti DNS-rebinding) e' gia' passato in do_GET.
            pass
        elif not _loopback(self):
            self._reject(403, "API riservate a localhost")
            return
        getattr(self, "_api_" + rotta.method)(query)

    # -- implementazioni delle rotte GET (una per rotta del registro) ----------
    def _api_stato(self, query):
        return self._json(_state())

    def _api_classifica(self, query):
        return self._json(_classifica_view())

    def _api_classifica_export(self, query):
        return self._classifica_export(query)

    def _api_build(self, query):
        return self._start(self._parse_build())

    def _api_export_single(self, query):
        return self._export_single(query)

    def _api_progresso(self, query):
        return self._progress()

    def _api_history(self, query):
        return self._history()

    def _api_qr(self, query):
        return self._qr(query)

    def _api_diagnostica(self, query):
        from tools.netdiag import diagnose
        return self._json(diagnose(RUNTIME_PORT))

    def _api_logs_download(self, query):
        return self._logs_download()

    def _api_voices(self, query):
        return self._json({"voices": _edge_voices_live(),
                           "current": CONFIG.get("edge_voice")})

    def _api_lesson_data(self, query):
        return self._lesson_data(query)

    def _api_settings(self, query):
        from tools.panel_settings import public_config
        return self._json({"settings": public_config(),
                           "runtime_port": RUNTIME_PORT})

    def _api_class_timer(self, query):
        """Timer di classe: l'unica GET aperta alla rete di classe.

        Espone solo `{minuti, scadenza, attivo, residuo}`: l'orario di fine
        dell'attivita'. Nessun dato del docente, nessun risultato degli studenti
        (quelli restano dietro PIN_classe), quindi il player puo' leggerlo per
        mostrare il conto alla rovescia. Se il file non esiste, semplicemente
        non c'e' un timer.
        """
        from tools.shared_lesson import get_timer
        try:
            return self._json(get_timer(BASE))
        except Exception as e:  # noqa: BLE001
            _flog(f"class_timer GET: {e}")
            return self._json({"minuti": 0, "scadenza": 0, "attivo": False,
                               "residuo": 0})

    def _api_reaudio(self, query):
        return self._start_reaudio(query)

    def _api_log(self, query):
        s = _JOBS.snapshot()          # copia coerente sotto lock
        started = s.get("started_at")
        elapsed = (time.time() - started) if started and s.get("running") else None
        try:
            cur = int((query.get("from", ["-1"])[0] or "-1"))
        except (TypeError, ValueError):
            cur = -1
        lines, total, truncated = LOG.since(cur)
        return self._json({"running": s["running"], "kind": s["kind"],
                           "source": s["source"], "error": s["error"],
                           "done_at": s["done_at"], "ok": s["ok"],
                           "elapsed": round(elapsed, 1) if elapsed else None,
                           "queued": len(s["queue"]),
                           "queue": s["queue"],
                           "cursor": total, "truncated": truncated,
                           "lines": lines})

    def _api_lan(self, query):
        ip = lan_ip()
        try:
            from tools.shared_lesson import get_shared
            shared = get_shared(BASE)
        except Exception:
            shared = ""
        base = f"http://{ip}:{RUNTIME_PORT}" if ip else ""
        return self._json({
            "lan_ip": ip, "port": RUNTIME_PORT,
            "url": f"{base}/{shared}/index.html" if (base and shared) else (f"{base}/" if base else None),
            "hub_url": f"{base}/" if base else None,
            "lesson_url": f"{base}/{shared}/index.html" if (base and shared) else None,
            "shared": shared,
            "warning": lan_warning(),
            "selftest": _lan_selftest(ip, RUNTIME_PORT) if ip else None,
        })

    def _teacher_allowed(self):
        return _class_repo_authorized(
            CONFIG.get("pin_docente"), self.headers.get("X-Teacher-Pin"))

    def _genera_e_verifica(self, src, force, bozza, single, profilo,
                           text=None, title=None):
        """Genera e SOLO allora riesce.

        `build_from_source` restituisce `(None, False)` quando il lock
        anti-concorrenza e' occupato: prima la lambda del pannello scartava quel
        risultato, il runner metteva `ok=True` e il pannello annunciava
        "JOB COMPLETATO" scrivendo in cronologia un successo per una lezione
        che non era stata generata. Il runner deve vedere l'eccezione.
        """
        import new_lesson
        if text is not None:
            out, ok = new_lesson.build_from_text(text, title=title, force=force,
                                                  bozza=bozza, single=single,
                                                  profilo=profilo)
        else:
            out, ok = new_lesson.build_from_source(src, force=force, bozza=bozza,
                                                   single=single, profilo=profilo)
        if out is None:
            raise RuntimeError(
                "Generazione saltata: un'altra generazione e' gia' in corso. "
                "Riprova fra poco.")
        return out, ok

    def _require_teacher(self):
        if self._teacher_allowed():
            return True
        self._json({"ok": False, "error": "PIN docente non valido."}, 403)
        return False

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reject(self, status, reason):
        """Rifiuta la richiesta svuotando prima il body: se il corpo resta
        nel buffer della connessione keep-alive, la richiesta successiva su
        quella socket legge spazzatura e la sessione HTTP si corrompe."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if 0 < length <= 8 * 1024 * 1024:
                self.rfile.read(length)
        except Exception:
            self.close_connection = True
        self.close_connection = True
        self._json({"ok": False, "error": reason}, status)

    def _origin_ok(self):
        return same_origin_post(self.headers, _allowed_hosts())

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2 * 1024 * 1024:
            raise ValueError("Corpo troppo grande")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _qr(self, query):
        """QR locale: funziona anche senza internet, a differenza dei servizi esterni."""
        text = (query.get("url", [""])[0] or "").strip()
        if not re.fullmatch(r"https?://[^\s]{1,500}", text):
            self._json({"error": "Indirizzo URL non valido"}, 400)
            return
        try:
            scale = int((query.get("scale", ["6"])[0] or "6"))
            scale = max(4, min(12, scale))
        except Exception:
            scale = 6
        try:
            from tools.qr import qr_png_bytes
            body = qr_png_bytes(text, scale=scale, border=4)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"QR non creato: {e}"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _logs_download(self):
        """Un unico ZIP con i log utili per assistenza, senza dati degli alunni."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("log-pannello.txt", "\n".join(list(LOG)[-500:]).encode("utf-8"))
            for name in ("generazione.log", "panel_errors.log"):
                p = BASE / name
                if p.is_file():
                    z.write(p, arcname=name)
            z.writestr("diagnostica.json", json.dumps(
                _state(), ensure_ascii=False, indent=2).encode("utf-8"))
        body = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="log-generatore.zip"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _lesson_action(self, action, data):
        name = str(data.get("lesson") or "")
        from tools import lesson_admin
        if action == "share":
            # lezione mostrata agli alunni: la imposta come "condivisa in classe"
            from tools.shared_lesson import set_shared
            result = set_shared(BASE, name)
        elif action == "rename":
            result = lesson_admin.rename_lesson(BASE, name, data.get("title"))
        elif action == "duplicate":
            result = lesson_admin.duplicate_lesson(BASE, name)
        elif action == "archive":
            result = lesson_admin.archive_lesson(BASE, name)
        elif action == "restore":
            result = lesson_admin.restore_lesson(BASE, name)
        elif action == "delete":
            if data.get("confirm") != name:
                raise ValueError("Conferma richiesta: nome della lezione non corrisponde.")
            result = lesson_admin.delete_lesson(BASE, name)
        else:
            raise ValueError("Operazione lezione sconosciuta.")
        _invalidate_lessons_cache()
        self._json({"ok": True, "name": result})

    def _resolve_source(self, src):
        """Solo file caricati via upload in questa sessione (no scansione
        cartella) oppure URL. Niente path traversal."""
        if is_url(src):
            return src
        name = Path(urllib.parse.unquote(src)).name
        p = (BASE / name).resolve()
        if p.parent != BASE or not p.is_file() or p.suffix.lower() not in SUPPORTED_EXT:
            raise ValueError(f"Materiale non valido: {src}")
        UPLOADED.setdefault(name, {"t": time.time()})
        return str(p)

    def _parse_build(self):
        from common import normalize_profilo
        data = self._read_json_body()
        src = self._resolve_source(str(data.get("source") or ""))
        force = bool(data.get("force"))
        bozza = bool(data.get("bozza"))
        single = bool(data.get("single"))
        whisper = bool(data.get("whisper"))
        if Path(src).suffix.lower() in (".mp3", ".m4a", ".wav") and not whisper:
            raise ValueError("Per generare da audio devi confermare la trascrizione Whisper.")
        profilo = normalize_profilo(data.get("profilo") or {
            "durata": data.get("durata"), "livello": data.get("livello"),
            "obiettivo": data.get("obiettivo")})
        return src, force, bozza, single, profilo

    def _start(self, parsed):
        src, force, bozza, single, profilo = parsed
        source_name = Path(src).name if not is_url(src) else src
        if _material_job_pending(source_name):
            state = "già in lavorazione" if JOB["running"] else "già in coda"
            self._json({"started": False,
                        "reason": f"Attenzione: questo materiale è {state}."}, 409)
            return
        started, queued = start_job(
            lambda: self._genera_e_verifica(src, force, bozza, single, profilo),
            "generazione", Path(src).name if not is_url(src) else src)
        if not started:
            self._json({"started": False, "reason": "Coda piena (5 job): attendi la fine."}, 409)
            return
        self._json({"started": True, "queued": queued})

    def _start_text(self):
        from common import normalize_profilo
        data = self._read_json_body()
        text = str(data.get("text") or "")
        title = str(data.get("title") or "Materiale incollato")[:80]
        if len(text.strip()) < 50:
            raise ValueError("Testo troppo corto (min 50 caratteri).")
        force = bool(data.get("force"))
        bozza = bool(data.get("bozza"))
        single = bool(data.get("single"))
        profilo = normalize_profilo(data.get("profilo") or {})
        started, queued = start_job(
            lambda: self._genera_e_verifica(None, force, bozza, single, profilo,
                                            text=text, title=title),
            "testo incollato", title)
        if not started:
            self._json({"started": False, "reason": "Coda piena (5 job): attendi la fine."}, 409)
            return
        self._json({"started": True, "queued": queued})

    def _move_slide(self):
        data = self._read_json_body()
        import new_lesson
        lesson = self._check_lesson(str(data.get("lesson") or ""))
        n = new_lesson.move_slide(str(lesson), int(data.get("from", -1)), int(data.get("to", -1)))
        _invalidate_lessons_cache()
        self._json({"ok": True, "slides": n})

    def _add_slide(self):
        data = self._read_json_body()
        import new_lesson
        lesson = self._check_lesson(str(data.get("lesson") or ""))
        pos = new_lesson.add_slide(str(lesson), data.get("title"), data.get("narration"))
        _invalidate_lessons_cache()
        self._json({"ok": True, "pos": pos})

    def _delete_slide(self):
        data = self._read_json_body()
        import new_lesson
        lesson = self._check_lesson(str(data.get("lesson") or ""))
        n = new_lesson.delete_slide(str(lesson), int(data.get("index", -1)))
        _invalidate_lessons_cache()
        self._json({"ok": True, "slides": n})

    def _start_reaudio(self, query):
        name = query.get("lesson", [None])[0] or ""
        try:
            lesson = self._check_lesson(name)
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        import new_lesson
        started, queued = start_job(lambda: new_lesson.regen_audio_lesson(str(lesson)),
                       "rigenerazione audio", name)
        if not started:
            self._json({"started": False, "reason": "Coda piena (5 job): attendi la fine."}, 409)
            return
        self._json({"started": True, "queued": queued})

    def _export_single(self, query):
        name = query.get("lesson", [None])[0] or ""
        try:
            lesson = self._check_lesson(name)
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        try:
            from export_single import export_single
            p = export_single(lesson)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)
            return
        self._json({"url": f"/{lesson.name}/{p.name}",
                    "size": p.stat().st_size})

    def _progress(self):
        try:
            data = json.loads((BASE / ".progress.json").read_text(encoding="utf-8"))
        except Exception:
            data = {"fase": "inattiva", "pct": 0}
        data["job_running"] = JOB["running"]
        return self._json(data)

    def _history(self):
        try:
            hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8") or "[]")
        except Exception:
            hist = []
        return self._json({"history": hist[-20:]})

    def _check_lesson(self, name):
        # anti-traversal: rigetta .., /, \, null byte e nomi non whitelistati
        if not name or "\x00" in name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("Lezione non valida")
        if name not in _allowed_lesson_names() and name not in _allowed_single_names():
            # fallback strict: verifica comunque il path risolto
            lesson = (BASE / name).resolve()
            try:
                lesson.relative_to(BASE.resolve())
            except Exception:
                raise ValueError("Lezione non valida")
            if not lesson.is_dir() or not (lesson / "index.html").exists():
                raise ValueError("Lezione non trovata")
            return lesson
        lesson = BASE / name
        if not lesson.is_dir() or not (lesson / "index.html").exists():
            raise ValueError("Lezione non trovata")
        try:
            # double-check che non sia symlink fuori da BASE
            lesson.resolve().relative_to(BASE.resolve())
            if lesson.resolve().parent != BASE.resolve():
                raise ValueError("Lezione non valida")
        except ValueError:
            raise
        except Exception:
            raise ValueError("Lezione non valida")
        return lesson

    def _lesson_data(self, query):
        name = query.get("lesson", [None])[0] or ""
        try:
            lesson = self._check_lesson(name)
            import new_lesson
            _, payload = new_lesson.load_lesson(str(lesson))
            slides = []
            for i, s in enumerate(payload.get("slides", [])):
                q = None
                for b in s.get("blocks", []):
                    if "quiz" in b:
                        q = b["quiz"]
                        break
                slides.append({"index": i, "title": s.get("title", ""),
                               "narration": s.get("narration", ""),
                               "duration": s.get("duration", 0),
                               "quiz": q})
            self._json({"lesson": lesson.name, "titolo": payload.get("titolo"),
                        "profilo": payload.get("profilo"), "slides": slides})
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 400)

    def _save_slide(self):
        data = self._read_json_body()
        lesson = self._check_lesson(str(data.get("lesson") or ""))
        index = int(data.get("index", -1))
        patch = data.get("patch") or {}
        import new_lesson
        with new_lesson._lesson_lock():
            out, payload = new_lesson.load_lesson(str(lesson))
            slides = payload["slides"]
            if not (0 <= index < len(slides)):
                raise ValueError("Indice slide fuori range")
            clean = new_lesson.validate_slide_edit(index, patch)
            s = slides[index]
            narration_changed = False
            if "title" in clean:
                s["title"] = clean["title"]
                # aggiorna anche l'h1 del blocco se presente
                for b in s.get("blocks", []):
                    if "h1" in b:
                        b["h1"] = clean["title"]
                        break
            if "narration" in clean and clean["narration"] != s.get("narration"):
                s["narration"] = clean["narration"]
                narration_changed = True
            if "quiz" in clean:
                for b in s.get("blocks", []):
                    if "quiz" in b:
                        if clean["quiz"] is None:
                            s["blocks"].remove(b)
                        else:
                            q = clean["quiz"]
                            b["quiz"] = {"q": q["domanda"],
                                         "opts": [{"t": o["testo"], "ok": o["corretta"], "fb": ""}
                                                  for o in q["opzioni"]],
                                         "ok": "Esatto!", "ko": "Rileggi e riprova."}
                        break
                else:
                    if clean["quiz"] is not None:
                        q = clean["quiz"]
                        s.setdefault("blocks", []).append(
                            {"quiz": {"q": q["domanda"],
                                      "opts": [{"t": o["testo"], "ok": o["corretta"], "fb": ""}
                                               for o in q["opzioni"]],
                                      "ok": "Esatto!", "ko": "Rileggi e riprova."}})
            new_lesson.save_lesson(str(out), payload)
        _invalidate_lessons_cache()
        self._json({"ok": True, "narration_changed": narration_changed,
                    "warning": ("Testo narrazione modificato: l'audio è invariato. "
                                "Usa «Rigenera audio slide» per riallinearlo."
                                if narration_changed else "")})

    def _reaudio_slide(self, query):
        name = query.get("lesson", [None])[0] or ""
        try:
            index = int(query.get("index", [-1])[0])
        except Exception:
            index = -1
        lesson = self._check_lesson(name)
        ok = start_job(lambda: self._do_reaudio_slide(str(lesson), index),
                       "audio slide", f"{lesson.name}#{index}")
        if not ok[0]:
            self._json({"started": False, "reason": "Coda piena."}, 409)
            return
        self._json({"started": True, "queued": ok[1]})

    @staticmethod
    def _do_reaudio_slide(lesson, index):
        import new_lesson
        dur, engine = new_lesson.regen_slide_audio(lesson, index)
        _log(f"✔ slide {index + 1} audio rigenerato [{engine}] {dur:.1f}s")
        _invalidate_lessons_cache()

    def _tts_preview(self):
        data = self._read_json_body()
        text = str(data.get("text") or "").strip()[:500]
        if not text:
            raise ValueError("Testo vuoto (max 500 caratteri)")
        voice = str(data.get("voice") or CONFIG.get("edge_voice"))
        if voice not in _edge_voices_live() and voice not in EDGE_VOICES:
            raise ValueError(f"Voce non supportata ({voice or 'vuota'}): "
                             "selezionane una dal menu e ricarica la pagina se è vuoto")
        rate = str(data.get("rate") or CONFIG.get("edge_rate", "-4%"))
        if not re.fullmatch(r"-?\d+%", rate):
            raise ValueError("Rate non valido (es. -4%)")
        try:
            import asyncio
            import edge_tts
            mp3 = bytearray()

            async def _run():
                comm = edge_tts.Communicate(text, voice, rate=rate)
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        mp3.extend(chunk["data"])
                    if len(mp3) > 2 * 1024 * 1024:
                        break

            asyncio.run(_run())
        except Exception as e:  # noqa: BLE001
            cause = str(e)[:150] or type(e).__name__
            _log(f"✗ anteprima voce fallita [{voice}]: {cause}")
            _flog(f"tts_preview 502 | [{voice}] {rate} | {cause}")
            body = json.dumps({"ok": False,
                               "error": f"Sintesi vocale fallita: {cause}. "
                                        "Controlla la connessione verso Microsoft."},
                              ensure_ascii=False).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if len(mp3) < 1000:
            _log("✗ anteprima voce: audio vuoto restituito dal servizio")
            self._json({"ok": False, "error": "Sintesi fallita: audio vuoto, riprova."}, 502)
            return
        body = bytes(mp3)
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- upload materiale -------------------------------------------------------
    def _max_body_bytes(self):
        """Tetto assoluto sul corpo della richiesta, indipendente da
        `max_upload_mb`.

        Prima era `MAX_UPLOAD_BYTES * 10 + 2 MB` e `max_upload_mb` era
        ammesso fino a 1000 dalla UI: una singola richiesta poteva chiedere
        ~10 GB, tutto bufferizzato in un bytearray (con il `body.split()` che
        lo duplicava). ThreadingHTTPServer non ha pool: bastava una manciata di
        richieste per esaurire la RAM. Qui il limite per-corpo resta legato
        alla config, ma con un soffitto assoluto.
        """
        return min(MAX_UPLOAD_BYTES * 10 + 2 * 1024 * 1024, MAX_BODY_BYTES)

    def _read_limited(self, length):
        """Lettura a chunk 64KB con limite: evita un singolo read() enorme."""
        cap = self._max_body_bytes()
        buf = bytearray()
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            buf.extend(chunk)
            remaining -= len(chunk)
            if len(buf) > cap:
                raise _UploadTooBig(
                    f"Caricamento troppo grande: massimo {MAX_UPLOAD_MB} MB per file "
                    f"e {UPLOAD_FILES_PER_REQUEST} file per richiesta.")
        return bytes(buf)

    def _parse_upload_body(self):
        """Estrae i file dal multipart con controlli centralizzati nel modulo."""
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("Richiesta senza corpo da caricare.")
        if length > self._max_body_bytes():
            raise _UploadTooBig(
                f"Caricamento troppo grande: massimo {MAX_UPLOAD_MB} MB per file "
                f"e {UPLOAD_FILES_PER_REQUEST} file per richiesta.")
        from tools.multipart import parse_multipart
        return parse_multipart(self._read_limited(length), ctype)

    def _upload(self):
        files = self._parse_upload_body()
        if len(files) > UPLOAD_FILES_PER_REQUEST:
            raise ValueError(f"Carica al massimo {UPLOAD_FILES_PER_REQUEST} file per volta.")
        saved = []
        for raw_name, data in files:
            saved.append(self._save_uploaded_file(raw_name, data))
        total = sum((BASE / name).stat().st_size for name in saved)
        self._json({"ok": True, "names": saved, "name": saved[0],
                    "size": total, "replaced": len(saved) == 1,
                    "copy": len(saved) != 1})

    def _save_uploaded_file(self, raw_name, data):
        from tools.uploads import UploadTooBig, save_upload
        try:
            final_name, replaced, copied = save_upload(
                BASE, raw_name, data, SUPPORTED_EXT, MAX_UPLOAD_BYTES)
        except UploadTooBig as exc:
            raise _UploadTooBig(str(exc)) from exc
        _register_upload(final_name)
        _invalidate_lessons_cache()
        _log(f"✔ materiale caricato dal pannello: {final_name}"
             + (" (sostituito)" if replaced else "")
             + (" (salvato come copia)" if copied else ""))
        return final_name

    # -- GET ------------------------------------------------------------------
    def do_GET(self):
        if not _host_ok(self):
            self._reject(403, "Host non consentito.")
            return
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if path.startswith("/api/"):
            return self._api(path, query)
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path in ("/", "/index.html"):
            body = PANEL_HTML.encode("utf-8") if _loopback(self) else _hub_page_cached().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # anti-traversal: guardia CONDIVISA con start_lesson (era duplicata
        # qui con criteri leggermente diversi: due implementazioni da tenere
        # allineate, con drift garantito)
        target = safe_request_path(path, BASE)
        if target is None:
            self.send_error(404, "Non disponibile")
            return
        try:
            parts = [p for p in urllib.parse.unquote(path).strip("/").split("/") if p]
        except Exception:
            self.send_error(404, "Non disponibile")
            return
        first = parts[0] if parts else ""
        if first:
            # valutati UNA volta sola: la whitelist cambia a ogni rigenerazione
            names = _allowed_lesson_names()
            if first not in names and first not in _allowed_single_names():
                self.send_error(404, "Non disponibile")
                return
            try:
                # se first è una lezione, deve restare dentro BASE/first
                if first in names:
                    target.relative_to((BASE / first).resolve())
            except Exception:
                self.send_error(404, "Non disponibile")
                return
        super().do_GET()

    def do_POST(self):
        if not _host_ok(self):
            self._reject(403, "Host non consentito.")
            return
        parsed = urllib.parse.urlparse(self.path)
        # UNA SOLA API aperta alla LAN: gli studenti inviano il risultato del
        # percorso (nessun dato sensibile, valori sanitized e limitati). Resta
        # comunque soggetta al controllo origine: altrimenti un qualsiasi sito
        # potrebbe far inviare righe arbitrarie. Le rotte sono nel registro
        # (tools/panel_routes.py, guardia PUBBLICA), ma questa va gestita PRIMA
        # del blocco sotto perché salta i controlli di loopback e PIN docente.
        if parsed.path == "/api/classifica":
            if not self._origin_ok():
                self._reject(403, "Origine non consentita.")
                return
            return self._classifica_post()
        if not _loopback(self):
            self._reject(403, "API riservate a localhost")
            return
        if not self._origin_ok():
            self._reject(403, "Origine non consentita.")
            return
        # Ogni POST reached è una mutazione: se il docente ha impostato un PIN
        # (impostazioni -> "PIN docente"), viene richiesto per TUTTE, non solo
        # per due endpoint. Con PIN vuoto l'unica protezione resta il loopback,
        # quindi l'installazione predefinita non cambia comportamento.
        if not self._teacher_allowed():
            self._reject(403, "PIN docente non valido.")
            return
        self._post_api(parsed)

    # -- POST: dispatch dal registro + un metodo per rotta ---------------------
    def _post_api(self, parsed):
        """Instrada le POST usando il registro in tools/panel_routes.py.

        Prima era una catena di 17 `elif parsed.path == "/api/..."` lunga ~120
        righe, con dentro la gestione errori di ciascuna rotta: aggiungere un
        endpoint richiedeva di ricordarsi in che punto della catena infilarlo e
        la forma dell'errore ({"started":...} o {"ok":...}) era implicita nella
        posizione. Le rotte sono ora dichiarate una volta sola in
        tools/panel_routes.py, con guardia, rate-limit e forma dell'errore
        espliciti: `tests/test_panel_routes.py` verifica che ogni rotta
        dichiarata abbia davvero il metodo corrispondente.
        """
        from tools import panel_routes

        rotta = panel_routes.lookup("POST", parsed.path)
        if rotta is None:
            self.send_error(404, "API sconosciuta")
            return
        if rotta.guardia == panel_routes.PUBBLICA:
            # unica rotta aperta alla rete: gestita già in do_POST, dove il
            # controllo origine viene prima del blocco loopback/PIN
            return self._classifica_post()
        if rotta.rate_limit and not _rate_ok(self.client_address[0]):
            self._json({"started": False,
                        "reason": "Troppe richieste: riprova tra un po'."}, 429)
            return
        if rotta.err is None:
            getattr(self, "_post_" + rotta.method)(parsed)   # contratto proprio
            return
        try:
            metodo = getattr(self, "_post_" + rotta.method)
            if rotta.query:
                metodo(urllib.parse.parse_qs(parsed.query))
            else:
                metodo()
        except Exception as e:  # noqa: BLE001
            if rotta.err == "started":
                self._json({"started": False, "reason": str(e)}, 400)
            else:
                self._json({"ok": False, "error": str(e)}, 400)

    def _post_build(self):
        return self._start(self._parse_build())

    def _post_build_text(self):
        return self._start_text()

    def _post_move_slide(self):
        return self._move_slide()

    def _post_add_slide(self):
        return self._add_slide()

    def _post_delete_slide(self):
        return self._delete_slide()

    def _post_reaudio(self, query):
        return self._start_reaudio(query)

    def _post_lesson_action(self):
        data = self._read_json_body()
        return self._lesson_action(str(data.get("action") or ""), data)

    def _post_save_slide(self):
        # il log tiene traccia del body non valido: senza, una slide che non si
        # salva dal pannello non lascia nessuna traccia
        try:
            return self._save_slide()
        except Exception as e:  # noqa: BLE001
            _flog(f"save_slide 400 | {e}")
            raise

    def _post_reaudio_slide(self, query):
        return self._reaudio_slide(query)

    def _post_tts_preview(self):
        return self._tts_preview()

    def _post_upload(self, parsed):
        """L'upload ha un contratto proprio: 413 se il file supera il limite."""
        try:
            self._upload()
        except _UploadTooBig as e:
            _log(f"✗ upload rifiutato (troppo grande): {e}")
            _flog(f"upload 413 | {e} | len={self.headers.get('Content-Length')} "
                  f"| ctype={self.headers.get('Content-Type', '')[:100]}")
            self._json({"ok": False, "error": str(e)}, 413)
        except Exception as e:  # noqa: BLE001
            _log(f"✗ upload fallito: {e}")
            _flog(f"upload 400 | {e} | len={self.headers.get('Content-Length')} "
                  f"| ctype={self.headers.get('Content-Type', '')[:100]}")
            self._json({"ok": False, "error": str(e)}, 400)

    def _post_cancel_queue(self, parsed):
        n = cancel_queue()
        self._json({"ok": True, "removed": n})

    def _post_cancel_job(self, parsed):
        if not JOB["running"]:
            self._json({"ok": False, "error": "Nessun lavoro in corso."}, 409)
            return
        from sources import cancel_transcription
        cancel_transcription()
        _log("⏹ annullamento trascrizione richiesto")
        self._json({"ok": True})

    def _post_clear_history(self, parsed):
        _history_clear()
        self._json({"ok": True})

    def _post_reset_classifica(self, parsed):
        # PIN già verificato sopra per tutte le POST
        _classifica_reset()
        self._json({"ok": True})

    # -- metodi delle rotte POST con contratto proprio -------------------------
    def _post_settings(self, parsed):
        """Salva le impostazioni e ricalcola i limiti (doppio check del PIN)."""
        if not self._require_teacher():
            return
        try:
            from tools.panel_settings import update_public
            global CONFIG, MAX_UPLOAD_MB, MAX_UPLOAD_BYTES
            values = update_public(self._read_json_body())
            old_port = int(CONFIG.get("porta", 8341))
            old_cache = int(CONFIG.get("cache_max_mb", 300))
            CONFIG = load_config()
            MAX_UPLOAD_MB = int(CONFIG.get("max_upload_mb", 100))
            MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
            restart = (int(CONFIG.get("porta", 8341)) != old_port or
                       int(CONFIG.get("cache_max_mb", 300)) != old_cache)
            self._json({"ok": True, "settings": values,
                        "restart_required": restart})
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": str(e)}, 400)

    def _post_refresh_player(self, parsed):
        """Riscrive il player di tutte le lezioni.

        Il player (CSS/JS/SW) è scritto come file statici dentro ogni cartella
        lezione e non si aggiorna da solo quando si modifica
        tools/player_template.py: senza questo pulsante gli studenti
        continuano a vedere una versione vecchia. Non tocca audio, sottotitoli
        o contenuti.
        """
        import rigenera_player

        rigenera_player.rigenera(BASE, log=lambda m: _log(m))
        _invalidate_lessons_cache()
        self._json({"ok": True,
                    "messaggio": "Player aggiornato in tutte le lezioni."})

    def _post_class_timer(self, parsed):
        """Avvia o ferma il timer di classe (solo dal PC del docente).

        `minuti` = 0 ferma il timer. Il merito e' la risposta: il pannello mostra
        subito l'orario di fine e non deve rileggere il file. Firma con `parsed`
        perché la rotta è dichiarata `err=None` (gestisce da se ogni errore, con
        un 500 sulle colpe del server invece di un 400 che farebbe sembrare la
        colpa dello studente).
        """
        from tools.shared_lesson import set_timer
        try:
            d = self._read_json_body()
            stato = set_timer(BASE, d.get("minuti"))
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        except Exception as e:  # noqa: BLE001
            _flog(f"class_timer POST: {e}")
            self._json({"ok": False, "error": "Salvataggio non riuscito."}, 500)
            return
        self._json({"ok": True, **stato})

    def _classifica_post(self):
        """Invio del risultato da parte dello studente.

        E' l'UNICO endpoint raggiungibile dalla rete di classe. Restituire
        `str(e)` significava consegnare allo studente path locali e dettagli
        interni (SQLite, filesystem) di una macchina che non e' la sua. Il
        dettaglio va nel log del pannello, non in risposta.
        """
        try:
            d = self._read_json_body()
            lesson = self._check_lesson(str(d.get("lesson") or ""))
            studente = re.sub(r"\s+", " ", str(d.get("studente") or "Anonimo")).strip()[:40] \
                or "Anonimo"
            punti = _int_clamp(d.get("punti"), 0, 999)
            totale = _int_clamp(d.get("totale"), 0, 999)
            tempo_min = _int_clamp(d.get("tempo_min"), 0, 600)
            # errori: senza questo numero la classifica non puo' essere la media
            # di errori e tempo, ma solo un ordine sui minuti
            errori = _int_clamp(d.get("errori"), 0, 999)
            if totale <= 0:
                raise ValueError("Nessuna attivita' registrata")
            _classifica_add(lesson.name, studente, punti, totale,
                            bool(d.get("completata")), tempo_min, errori)
            self._json({"ok": True})
        except ValueError as e:
            # errori di input: il messaggio e' utile e non rivela nulla
            self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            _flog(f"classifica POST: {e}")
            self._json({"ok": False, "error": "Salvataggio non riuscito."}, 500)

    def _classifica_export(self, query):
        """CSV della classifica di una lezione (stesso ordine della vista)."""
        name = query.get("lesson", [None])[0] or ""
        try:
            lesson = self._check_lesson(name)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 400)
        view = _classifica_view()
        entry = next((c for c in view["classifiche"] if c["lesson"] == lesson.name), None)
        rows = entry["rows"] if entry else []
        lines = ["posizione;studente;indice;media_errori_tempo;errori;minuti;"
                 "punti;percentuale;completata;quando"]
        for i, r in enumerate(rows):
            lines.append(";".join(_csv_cell(x) for x in (
                i + 1, r.get("studente", ""), r.get("indice", 0),
                r.get("media", 0), r.get("errori", 0), r.get("tempo_min", 0),
                f'{r.get("punti", 0)}/{r.get("totale", 0)}',
                f'{r.get("pct", 0)}%', "si" if r.get("completata") else "no",
                r.get("t", ""))))
        body = ("\ufeff" + "\n".join(lines)).encode("utf-8")
        fname = f'classifica-{lesson.name.replace("_lesson", "")}.csv'
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ------------------------------------------------------------------ UI
try:
    from panel_ui import PANEL_HTML
except ImportError:  # esecuzione dalla cartella tools
    from tools.panel_ui import PANEL_HTML




# ------------------------------------------------------------------ main
def main():
    global RUNTIME_PORT
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # dipendenze base: auto-install se mancanti (come AVVIA.bat)
    def have(m):
        try:
            __import__(m)
            return True
        except Exception:
            return False

    if not have("docx"):
        print("Installo le dipendenze mancanti (python-docx, edge-tts)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                        str(BASE / "requirements.txt")], check=False)

    port = DEFAULT_PORT
    argv = sys.argv[1:]
    try:
        i = argv.index("--port")
        port = int(argv[i + 1])
    except (ValueError, IndexError):
        pass
    free = find_port(port, 10)
    if free is None:
        print(f"ERRORE: nessuna porta libera tra {port} e {port + 9} "
              "(tutte occupate). Chiudi gli altri server e riprova.")
        sys.exit(1)
    port = free

    RUNTIME_PORT = port
    url = f"http://localhost:{port}/"
    print("=" * 60)
    print("  PANNELLO DI CONTROLLO — generatore lezioni")
    print(f"  URL: {url}")
    print("  Premi CTRL+C per fermare il server.")
    print("=" * 60)
    ip = lan_ip()
    if ip:
        print(f"  Lezioni per tablet/telefono sulla stessa rete: http://{ip}:{port}/")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    handler = functools.partial(PanelHandler, directory=str(BASE))
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer fermato.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
