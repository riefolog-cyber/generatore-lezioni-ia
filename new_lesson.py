# -*- coding: utf-8 -*-
"""Pipeline automatica: metti un materiale (docx/pdf/txt/md/html o URL di sito/YouTube)
nella cartella -> lezione interattiva completa.

Uso:
  python new_lesson.py watch                    # osserva la cartella e genera al volo
  python new_lesson.py build <file.docx>        # genera da un singolo file
  python new_lesson.py build <file.pdf|.txt|.md|.html>
  python new_lesson.py build https://esempio.it/pagina
  python new_lesson.py build https://www.youtube.com/watch?v=...
  python new_lesson.py build <file.docx> --bozza  # senza LLM (struttura dal docx)
  python new_lesson.py build <file.docx> --no-cache  # salta la cache LLM (nuovi contenuti)
  python new_lesson.py build <file.docx> --single  # SOLO file HTML unico (senza cartella)
  python new_lesson.py build <file.docx> --single --keep-folder  # file unico + cartella
  python new_lesson.py preview <file|URL>       # anteprima struttura (nessun audio)
  python new_lesson.py reaudio <cartella_lesson>  # rigenera audio/VTT/player (senza LLM)

Flusso di build:
  1. estrae il contenuto dalla fonte (docx/pdf/txt/md/html/URL/YouTube)
  2. 9router (LLM) struttura il materiale in moduli, quiz e abbinamenti (JSON)
     con retry su più modelli; fallback strutturale senza LLM
  3. costruisce le SLIDE della lezione: ogni slide ha DENTRO la sua narrazione,
     così l'audio è sempre sincronizzato con il passaggio mostrato
  4. genera audio neurale edge-tts (cache, fallback Piper, loudness
     normalizzata via ffmpeg) + sottotitoli VTT sincronizzati
     con word boundary reali e durate misurate (ffprobe)
  5. scrive il player autogenerato (player_template.py: tema e palette si
     adattano a config/theme e al titolo — nessuna cartella template_player)
  6. valida il pacchetto e scrive report.html

Configurazione (config.json): llm_url, llm_model, voice, theme, porta.
"""
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "tools"))
from common import (load_config, resolve_voice, setup_logging, validate_lesson, write_report)
from player_template import write_player, bust_cache
from sources import extract_source, is_url, SUPPORTED_EXT

CONFIG = load_config()
LLM_URL = CONFIG.get("llm_url", "http://localhost:20128/v1").rstrip("/")
LLM_MODEL = CONFIG.get("llm_model")
VOICE, CACHE = resolve_voice()
STATE = BASE / "_generated.json"
LOCK = BASE / ".generazione.lock"      # blocco anti-concorrenza tra build
FLATTEN_LIMIT = int(CONFIG.get("llm_contesto_caratteri", 18000))
CACHE_MAX_MB = int(CONFIG.get("cache_max_mb", 300))
TTS_WORKERS = int(CONFIG.get("tts_workers", 4))
TTS_RETRIES = int(CONFIG.get("tts_retries", 2))
LLM_API_KEY = CONFIG.get("llm_api_key") or ""

MIN_MODULI = int(CONFIG.get("num_moduli_min", 4))
MAX_MODULI = int(CONFIG.get("num_moduli_max", 7))
CACHE_VERSION = "v3"  # bump: invalida cache del bug 1-modulo (groq/gpt-oss-120b ha risposto con 1 modulo)
_FFMPEG_OK = None
AUDIO_DUAL_PASS = bool(CONFIG.get("audio_loudnorm_dual", False))


def _ffmpeg_ok():
    """Cache del check ffmpeg: evita decine di spawn falliti quando manca."""
    global _FFMPEG_OK
    if _FFMPEG_OK is None:
        _FFMPEG_OK = bool(shutil.which("ffmpeg"))
    return _FFMPEG_OK


def _jitter_sleep(base):
    time.sleep(base + random.uniform(0, 1.0))


def _is_retryable(msg):
    """Solo errori transienti meritano retry: 429/5xx/timeout/connessione.
    401/403/400/404 (voce/modello errati) falliscono subito."""
    m = (msg or "").lower()
    if any(k in m for k in ("401", "403", "unauthorized", "forbidden",
                            "400", "bad request", "404", "not found",
                            "invalid voice", "invalid model")):
        return False
    return True


# ================================================================== 1. estrazione fonte
# L'estrazione dei contenuti (docx/pdf/txt/md/html/URL/YouTube) vive in
# tools/sources.py: extract_source() è il punto d'ingresso unico.


# ================================================================== 2. LLM (9router)
def llm_reachable():
    try:
        with urllib.request.urlopen(f"{LLM_URL}/models", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def pick_model():
    if LLM_MODEL:
        return LLM_MODEL
    try:
        with urllib.request.urlopen(f"{LLM_URL}/models", timeout=8) as r:
            data = json.loads(r.read().decode())
        ids = [m.get("id", "") for m in data.get("data", [])]
        for pref in ("gpt-4o-mini", "gpt-4o", "claude", "gemini/gemini-3", "comboact"):
            for i in ids:
                if pref in i:
                    return i
        if ids:
            return ids[0]
    except Exception:
        pass
    return "comboact"


SCHEMA = """{
  "titolo": "Titolo della lezione (dal documento)",
  "sottotitolo": "Una frase che introduce il percorso",
  "intro": "Narrazione di apertura, 2 frasi, tono naturale e parlato",
  "outro": "Narrazione di conclusione, 2 frasi",
  "citazione": "Una frase memorabile dal documento, breve",
  "moduli": [
    {
      "titolo": "Titolo del modulo (max 60 caratteri)",
      "testo": "2-3 frasi che spiegano il concetto chiave, fedeli al documento",
      "punti": ["elenco puntato 1", "elenco puntato 2", "elenco puntato 3"],
      "keywords": ["parola1", "parola2", "parola3"],
      "narrazione": "1-2 frasi parlate per la slide del modulo",
      "quiz_narrazione": "1 frase parlata che introduce il quiz",
      "quiz": {
        "domanda": "domanda a scelta multipla sul modulo",
        "opzioni": [
          {"testo": "risposta corretta", "corretta": true, "feedback": "perché è giusta"},
          {"testo": "distrattore", "corretta": false, "feedback": "perché è sbagliata"},
          {"testo": "distrattore", "corretta": false, "feedback": "perché è sbagliata"},
          {"testo": "distrattore", "corretta": false, "feedback": "perché è sbagliata"}
        ],
        "ok": "feedback globale quando la risposta è corretta",
        "ko": "feedback globale quando la risposta è errata"
      },
      "abbinamenti": [
        {"termine": "concetto 1", "definizione": "definizione corretta"},
        {"termine": "concetto 2", "definizione": "definizione corretta"},
        {"termine": "conetto 3", "definizione": "definizione corretta"}
      ],
      "vero_falso": [
        {"affermazione": "frase da giudicare Vero o Falso, fedele o contraria al documento",
         "vero": true, "spiegazione": "perché è vero/falso, 1 frase"},
        {"affermazione": "seconda affermazione", "vero": false, "spiegazione": "perché"},
        {"affermazione": "terza affermazione", "vero": true, "spiegazione": "perché"}
      ],
      "sequenza": {
        "istruzione": "Metti in ordine i passaggi di…",
        "passi": ["passo 1 del processo", "passo 2", "passo 3", "passo 4"]
      },
      "compila": [
        {"frase": "frase con una ___ al posto della parola chiave",
         "risposta": "parola corretta",
         "aiuto": ["parola plausibile ma sbagliata", "altra parola sbagliata"]},
        {"frase": "seconda frase con ___", "risposta": "parola corretta",
         "aiuto": ["distrattore"]}
      ],
      "scenari": [
        {"situazione": "caso concreto breve da risolvere",
         "opzioni": [
           {"testo": "azione più corretta", "corretta": true, "conseguenza": "cosa succede e perché"},
           {"testo": "azione sbagliata", "corretta": false, "conseguenza": "rischio o problema"},
           {"testo": "azione sbagliata", "corretta": false, "conseguenza": "rischio o problema"}
         ],
         "conclusione": "morale del caso, 1 frase"}
      ],
      "errori": [
        {"brano": "frase di 15-25 parole che contiene UN errore concettuale",
         "errore": "esatta parte sbagliata del brano",
         "correzione": "versione corretta di quella parte",
         "spiegazione": "perché è un errore, 1 frase"}
      ],
      "flashcards": [
        {"termine": "concetto chiave", "definizione": "spiegazione chiara in max 15 parole"},
        {"termine": "secondo concetto", "definizione": "spiegazione"},
        {"termine": "terzo concetto", "definizione": "spiegazione"}
      ]
    }
  ]
}"""

PROMPT_TMPL = """Sei un esperto di instructional design. Trasforma il materiale didattico qui sotto
in una lezione interattiva strutturata. Regole:
- Dividi il materiale in {nmin}-{nmax} moduli logici e coesi (mai meno di {nmin}).
- RISPOSTA COMPATTA: "testo" max 2 frasi, "punti" max 3, spiegazioni e feedback
  max 12 parole ciascuno. Nessun testo fuori dal JSON.
{regole}
{profilo_istruzioni}
- Se il materiale lo consente aggiungi anche "sequenza" (3-5 passi); altrimenti null/vuoto.
- Sii fedele al documento: nessuna invenzione.
- Le narrazioni devono suonare naturali e parlate, in italiano.
- Rispondi SOLO con un JSON valido, senza testo fuori dal JSON, con questa struttura esatta:

{schema}

MATERIALE DIDATTICO:
{testo}"""


def profilo_istruzioni(profilo):
    """Istruzioni LLM dal profilo (durata/livello/obiettivo Bloom)."""
    if not isinstance(profilo, dict):
        return ""
    durata = profilo.get("durata", "standard")
    livello = profilo.get("livello", "intermedio")
    obiettivo = profilo.get("obiettivo", "comprensione")
    liv = {"base": "linguaggio semplice, definisci ogni termine tecnico, esempi concreti",
           "intermedio": "linguaggio chiaro ma preciso, collegamenti tra concetti",
           "avanzato": "dettagli, casi limite, distinzioni sottili, niente banalizzazioni"}.get(livello, "")
    ob = {"conoscenza": "verifica il ricordo: definizioni, fatti, termini chiave",
          "comprensione": "verifica la comprensione: spiega con parole tue, esempi, confronti",
          "applicazione": "verifica l'uso: casi concreti, cosa faresti, errori tipici",
          "analisi": "verifica l'analisi: confronta, scomponi, trova relazioni ed errori"}.get(obiettivo, "")
    return (f"- PROFILO LEZIONE: durata {durata}, livello {livello} ({liv}). "
            f"Obiettivo Bloom: {obiettivo} ({ob}).")


def profilo_moduli(profilo):
    """Range moduli dal profilo durata (None = usa config)."""
    d = (profilo or {}).get("durata", "standard") if isinstance(profilo, dict) else "standard"
    if d == "breve":
        return (3, 4)
    if d == "approfondita":
        return (6, 8)
    return (MIN_MODULI, MAX_MODULI)


def _regole_adattive(nchars, profilo=None):
    """Regole per l'LLM proporzionate alla quantità di materiale: con poco
    testo chiediamo meno attività (e MAI inventare), con molto tutto il
    pacchetto. Ritorna (regole, nmin, nmax). Il profilo durata può restringere
    o estendere il range moduli."""
    if nchars < 3500:
        return ("""- Ogni quiz ha ESATTAMENTE 4 opzioni, una sola corretta (se il materiale
  non basta a 4 opzioni serie, usa 3 opzioni: mai distrattori inventati).
- Ogni modulo ha 2 abbinamenti termine/definizione (se il materiale lo consente).
- Ogni modulo ha ESATTAMENTE 2 affermazioni vero/falso (mischiare vere e false)
  con una breve spiegazione.
- Ogni modulo ha ESATTAMENTE 1 frase "compila" (___ = parola chiave mancante;
  "aiuto" = 2 parole plausibili ma SBAGLIATE, mai la risposta).
- Ogni modulo ha ESATTAMENTE 1 scenario decisionale (3 opzioni, una sola corretta).
- Ogni modulo ha ESATTAMENTE 1 "errore" da trovare (brano 15-25 parole;
  "errore" riporta ESATTAMENTE le parole sbagliate del brano).
- Ogni modulo ha ESATTAMENTE 3 "flashcards" termine/definizione sui concetti
  chiave (definizioni brevi, definizioni diverse tra loro).
- MATERIALE SCARSO: se un'attività richiede contenuti che nel documento non
  esistono, omettila o rendila più semplice (3 opzioni, 1 abbinamento, 2
  flashcards) piuttosto che inventare.""", 3, 4)
    return ("""- Ogni quiz ha ESATTAMENTE 4 opzioni, una sola corretta.
- Ogni modulo ha 2-3 abbinamenti termine/definizione.
- Ogni modulo ha ESATTAMENTE 3 affermazioni vero/falso (mischiare vere e false)
  con una breve spiegazione.
- Ogni modulo ha ESATTAMENTE 2 frasi "compila" (___ = parola chiave mancante;
  "aiuto" = 2 parole plausibili ma SBAGLIATE, mai la risposta).
- Ogni modulo ha ESATTAMENTE 1 scenario decisionale (3 opzioni, una sola corretta).
- Ogni modulo ha ESATTAMENTE 1 "errore" da trovare (brano 15-25 parole;
  "errore" riporta ESATTAMENTE le parole sbagliate del brano).
- Ogni modulo ha ESATTAMENTE 3 "flashcards" termine/definizione sui concetti
  chiave (definizioni brevi, definizioni diverse tra loro).""",
            *profilo_moduli(profilo))


def parse_json(content):
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.S)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON non trovato nella risposta LLM")
    return json.loads(content[start:end + 1])


def _extract_api_body(body):
    """Estrae l'oggetto JSON da una risposta API che può avere coda SSE."""
    start = body.find("{")
    idx = body.find("data:")
    end = body.rfind("}")
    if idx > start and idx < end:
        end = idx
    if start < 0 or end < start:
        raise ValueError("JSON non trovato nel body della risposta")
    return json.loads(body[start:end + 1])


LLM_REQUEST_TIMEOUT = 150   # secondi per singola richiesta
LLM_TOTAL_DEADLINE = 420    # secondi totali per tutta la fase LLM (poi fallback)


def llm_structure(ext, use_cache=True, profilo=None):
    """Chiama 9router e restituisce (struttura, via).
    via è "cache" se la struttura viene riusata da una chiamata precedente
    sulla stessa fonte (stesso hash testo+modello+parametri+profilo), altrimenti "llm".
    Con use_cache (default), rigenerare lo stesso materiale non rifà la chiamata
    LLM (che può durare minuti): si usa --no-cache per forzare contenuti nuovi."""
    key = _llm_cache_key(ext, profilo) if use_cache else None
    if key:
        hit = _llm_cache_get(key)
        if hit is not None:
            print("  cache LLM: struttura riusata (fonte, modello e profilo invariati; "
                  "--no-cache per rigenerarla)", flush=True)
            return hit, "cache"
    testo = _flatten(ext)
    regole, nmin, nmax = _regole_adattive(len(testo), profilo)
    if (nmin, nmax) != profilo_moduli(profilo):
        print(f"  materiale corto ({len(testo)} caratteri): moduli {nmin}-{nmax} "
              "e attività ridotte per non inventare contenuti", flush=True)
    print(f"  profilo: {((profilo or {}).get('durata', '?'))}/"
          f"{((profilo or {}).get('livello', '?'))}/"
          f"{((profilo or {}).get('obiettivo', '?'))} -> moduli {nmin}-{nmax}", flush=True)
    prompt = PROMPT_TMPL.format(nmin=nmin, nmax=nmax, regole=regole,
                                profilo_istruzioni=profilo_istruzioni(profilo),
                                schema=SCHEMA, testo=testo)
    models = []
    for m in (LLM_MODEL or pick_model(), "comboact", "openrouter/openrouter/free"):
        if m and m not in models:
            models.append(m)
    last = None
    t0 = time.time()
    attempt = 0
    while time.time() - t0 < LLM_TOTAL_DEADLINE:
        attempt += 1
        for model in models:
            if time.time() - t0 > LLM_TOTAL_DEADLINE:
                break
            print(f"  LLM tentativo {attempt}: modello {model}…", flush=True)
            payload = {
                "model": model, "temperature": 0.3, "max_tokens": 8000,
                "messages": [
                    {"role": "system", "content": "Rispondi SOLO con JSON valido."},
                    {"role": "user", "content": prompt},
                ],
            }
            # primo giro con response_format json_object, poi senza (compatibilità)
            for use_rf in ((True, False) if attempt == 1 else (False,)):
                p = dict(payload)
                if use_rf:
                    p["response_format"] = {"type": "json_object"}
                try:
                    headers = {"Content-Type": "application/json"}
                    if LLM_API_KEY:
                        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
                    req = urllib.request.Request(
                        f"{LLM_URL}/chat/completions",
                        data=json.dumps(p).encode("utf-8"),
                        headers=headers)
                    with urllib.request.urlopen(req, timeout=LLM_REQUEST_TIMEOUT) as r:
                        out = _extract_api_body(r.read().decode("utf-8"))
                    content = out["choices"][0]["message"]["content"]
                    struct = parse_json(content)
                    struct.setdefault("moduli", [])
                    if not struct.get("titolo"):
                        struct["titolo"] = ext["title"]
                    _check_struct(struct)
                    nm = len(struct.get("moduli", []))
                    if nm < nmin and nm > 0:
                        try:
                            struct = _complete_modules(testo, struct, nmin, profilo)
                            nm = len(struct.get("moduli", []))
                        except Exception:
                            pass
                    if nm < nmin or nm > nmax + 1:
                        raise ValueError(f"moduli insufficienti: {nm} < {nmin} richiesti (o > {nmax}) — risposta scartata")
                    if nm < MIN_MODULI and len(testo) >= 3500:
                        raise ValueError(f"moduli {nm} < MIN_MODULI {MIN_MODULI} su materiale lungo — scarto")
                    if key:
                        _llm_cache_put(key, struct)
                    return struct, "llm"
                except Exception as e:  # noqa: BLE001
                    last = e
                    msg = str(e)[:100]
                    print(f"  scarto risposta {model}: {msg}", flush=True)
                    if not _is_retryable(str(e)):
                        print(f"  errore non retryable su {model}, passo oltre", flush=True)
                        continue
            # backoff con jitter tra round completi (evita hammering su 429/5xx)
            if time.time() - t0 < LLM_TOTAL_DEADLINE:
                _jitter_sleep(min(2 * attempt, 10))
    raise RuntimeError(f"LLM non disponibile entro {LLM_TOTAL_DEADLINE}s: {last}")


def _norm_opts(raw, maxn=4):
    """Opzioni LLM pulite: distinte (case-insensitive), max `maxn`,
    con esattamente una corretta (la prima) e almeno una. Conserva le
    chiavi extra (feedback/conseguenza) presenti nell'input."""
    seen = set()
    out = []
    for o in raw:
        if not isinstance(o, dict) or not str(o.get("testo") or "").strip():
            continue
        t = str(o["testo"]).strip()
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        item = dict(o)
        item["testo"] = t
        item["corretta"] = bool(o.get("corretta"))
        out.append(item)
    if not out:
        return []
    first_correct = next((o for o in out if o["corretta"]), None)
    if first_correct is None:
        out[0]["corretta"] = True
        first_correct = out[0]
    for o in out:
        if o is not first_correct:
            o["corretta"] = False
    return out[:maxn]


def _check_struct(struct):
    """Valida e normalizza la struttura LLM (quiz, abbinamenti, vf, sequenze)."""
    mods = struct.get("moduli")
    if not isinstance(mods, list) or not mods:
        raise ValueError("risposta senza moduli")
    for m in mods:
        if not isinstance(m, dict) or not m.get("titolo"):
            raise ValueError("modulo malformato (non è un oggetto con titolo)")
    for m in mods:
        q = m.get("quiz")
        if isinstance(q, dict) and q.get("domanda"):
            opts = _norm_opts(q.get("opzioni") if isinstance(q.get("opzioni"), list) else [])
            if len(opts) >= 2:
                m["quiz"] = {"domanda": str(q["domanda"]), "opzioni": opts,
                             "ok": str(q.get("ok") or ""), "ko": str(q.get("ko") or "")}
            else:
                m["quiz"] = None
        else:
            m["quiz"] = None
        ab = m.get("abbinamenti")
        if ab is not None and not isinstance(ab, list):
            m["abbinamenti"] = []
        if isinstance(m.get("abbinamenti"), list):
            m["abbinamenti"] = [a for a in m["abbinamenti"] if isinstance(a, dict)]
        # vero/falso: max 3 affermazioni ben formate
        vf = m.get("vero_falso")
        if not isinstance(vf, list):
            vf = []
        good = []
        for v in vf:
            if (isinstance(v, dict) and v.get("affermazione")
                    and isinstance(v.get("vero"), bool)):
                good.append({"affermazione": str(v["affermazione"]),
                             "vero": v["vero"],
                             "spiegazione": str(v.get("spiegazione") or "")})
        m["vero_falso"] = good[:3]
        # sequenza: almeno 3 passi
        seq = m.get("sequenza")
        if isinstance(seq, dict) and isinstance(seq.get("passi"), list):
            passi = [str(p).strip() for p in seq["passi"] if str(p).strip()]
            if len(passi) >= 3:
                m["sequenza"] = {"istruzione": str(seq.get("istruzione") or ""),
                                 "passi": passi[:6]}
            else:
                m["sequenza"] = None
        else:
            m["sequenza"] = None
        # compila: frasi con ___ + risposta + distrattori (mai la risposta negli aiuti)
        cp = m.get("compila")
        good_cp = []
        if isinstance(cp, list):
            for c in cp:
                if (isinstance(c, dict) and c.get("frase") and "___" in str(c["frase"])
                        and c.get("risposta")):
                    risp = str(c["risposta"]).strip()
                    aiuti = [str(a).strip() for a in (c.get("aiuto") or [])
                             if str(a).strip() and str(a).strip() != risp]
                    good_cp.append({"frase": str(c["frase"]), "risposta": risp,
                                    "aiuto": aiuti[:3]})
        m["compila"] = good_cp[:3]
        # scenari: 1 scenario, 2-3 opzioni, almeno una corretta
        sc = m.get("scenari")
        m["scenari"] = None
        if isinstance(sc, list) and sc and isinstance(sc[0], dict):
            s0 = sc[0]
            raw_opts = s0.get("opzioni") if isinstance(s0.get("opzioni"), list) else []
            opts = _norm_opts(raw_opts)
            for o in opts:
                o.setdefault("conseguenza", "")
            if s0.get("situazione") and len(opts) >= 2:
                m["scenari"] = {"situazione": str(s0["situazione"]), "opzioni": opts[:3],
                                "conclusione": str(s0.get("conclusione") or "")}
        # flashcards: termini chiave con definizioni distinte (studio + verifica)
        fc = m.get("flashcards")
        good_fc = []
        if isinstance(fc, list):
            for f0 in fc:
                if isinstance(f0, dict) and f0.get("termine") and f0.get("definizione"):
                    good_fc.append({"termine": str(f0["termine"]),
                                    "definizione": str(f0["definizione"])})
        m["flashcards"] = good_fc[:3]
        # errori: brano + correzione; "errore" (parte esatta sbagliata) viene
        # propagato come "sbagliato" per la verifica del punto esatto nel player
        er = m.get("errori")
        good_er = []
        if isinstance(er, list):
            for e0 in er:
                if isinstance(e0, dict) and e0.get("brano") and e0.get("correzione"):
                    wrong = str(e0.get("errore") or "").strip()
                    if wrong:
                        bw = set(w.lower().strip(".,;:!?»«()")
                                 for w in wrong.split() if len(w) > 2)
                        brw = set(w.lower().strip(".,;:!?»«()")
                                  for w in str(e0["brano"]).split())
                        if not bw or not bw.issubset(brw):
                            wrong = ""   # "errore" non coerente col brano: ignoralo
                    good_er.append({"brano": str(e0["brano"]),
                                    "sbagliato": wrong,
                                    "correzione": str(e0["correzione"]),
                                    "spiegazione": str(e0.get("spiegazione") or "")})
        m["errori"] = good_er[:2]


def _flatten(ext, limit=None):
    limit = FLATTEN_LIMIT if limit is None else limit
    out = [f'TITOLO: {ext["title"]}']
    for s in ext["sections"]:
        if s["heading"]:
            out.append(f'\n## {s["heading"]}')
        out.extend(s["paras"])
    txt = "\n".join(out)
    if len(txt) > limit:
        # Testa + coda (non solo testa): intro e conclusione restano visibili
        head = int(limit * 0.67)
        tail = limit - head
        txt = (txt[:head] + "\n\n[...omissis: parte centrale tagliata per limite contesto...]\n\n"
               + txt[len(txt) - tail:])
        print(f"  [!] materiale lungo: l'LLM vede testa+coda ({limit} caratteri). "
              "Valuta di spezzare il documento in piu' lezioni.", flush=True)
        return txt
    return txt


# ---------------------------------------------------------------- cache LLM
# La strutturazione dei contenuti dipende dal testo di partenza e dal modello:
# se la fonte non è cambiata (stesso hash), riusare l'ultima struttura buona
# salva una chiamata LLM di minuti. Disattivabile con --no-cache.
LLM_CACHE_DIR = BASE / ".llm_cache"
LLM_CACHE_MAX = 200        # oltre questo numero di voci si eliminano le più vecchie


def _llm_cache_key(ext, profilo=None):
    """Chiave: hash del testo appiattito + parametri che influenzano il prompt
    (modello, URL del router, numero moduli, limite contesto, profilo)."""
    txt = _flatten(ext)
    p = profilo if isinstance(profilo, dict) else {}
    payload = "\x1f".join([
        txt, LLM_URL, str(LLM_MODEL or ""),
        str(MIN_MODULI), str(MAX_MODULI), str(FLATTEN_LIMIT),
        str(p.get("durata", "")), str(p.get("livello", "")), str(p.get("obiettivo", "")),
        CACHE_VERSION,
    ])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _llm_cache_get(key):
    try:
        data = json.loads((LLM_CACHE_DIR / f"{key}.json").read_text(encoding="utf-8"))
        return data.get("struct") if isinstance(data, dict) else None
    except Exception:
        return None


def _llm_cache_put(key, struct):
    try:
        LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (LLM_CACHE_DIR / f"{key}.json").write_text(
            json.dumps({"struct": struct}, ensure_ascii=False), encoding="utf-8")
        _llm_cache_prune()
        return True
    except Exception:
        return False


def _llm_cache_prune():
    """Autolimite: oltre LLM_CACHE_MAX voci, elimina le più vecchie per mtime."""
    try:
        files = sorted(LLM_CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for p in files[:max(0, len(files) - LLM_CACHE_MAX)]:
            p.unlink()
    except Exception:
        pass


def fallback_structure(ext):
    """Senza LLM: moduli dai titoli delle sezioni, senza quiz (lezione ridotta)."""
    moduli = []
    for i, s in enumerate(ext["sections"]):
        tit = s["heading"] or f"Sezione {i + 1}"
        paras = s["paras"] or ["Contenuto della sezione."]
        # flashcards di riserva: dal titolo e dai primi punti della sezione
        fc = []
        for j, p in enumerate(paras[:2]):
            pw = p.strip().split()
            if len(pw) >= 3:
                fc.append({"termine": (tit if j == 0 else " ".join(pw[:4])).strip()[:40],
                           "definizione": p.strip()[:110]})
        moduli.append({
            "titolo": tit[:60], "testo": " ".join(paras)[:900],
            "punti": [p[:140] for p in paras[:5]],
            "keywords": [], "frase_chiave": tit[:55],
            "narrazione": " ".join(paras)[:300] or f"Parliamo di {tit}.",
            "quiz_narrazione": f"Ora verifica le tue conoscenze sulla sezione {i + 1}.",
            "quiz": None, "abbinamenti": [], "flashcards": fc,
        })
    return {"titolo": ext["title"], "sottotitolo": "Percorso interattivo basato sul documento.",
            "intro": f"Benvenuti. In questo percorso esploreremo {ext['title']}.",
            "outro": "Hai completato il percorso: ricorda i concetti principali.",
            "citazione": "La conoscenza cresce quando la condividi e la verifichi.",
            "moduli": [{**m, "vero_falso": [], "sequenza": None, "compila": [],
                        "scenari": None, "errori": []} for m in moduli]}


# ================================================================== 3. slide
ICONS = {
    "open": "🚀",
    "mod": ["📘", "🧭", "🔬", "💡", "🗺️", "⚙️", "🎯"],
    "quiz": "❓",
    "vf": "⚖️",
    "seq": "🔢",
    "match": "🧩",
    "compila": "✏️",
    "scenario": "🎬",
    "flash": "🃏",
    "errore": "🔍",
    "gloss": "📖",
    "exam": "🎓",
    "end": "🏁",
}


def _define_keyword(kw, m):
    """Definizione REALE di una keyword: la prima frase di testo/punti/narrazione
    del modulo che la contiene (niente definizioni inventate). Ritorna None se
    il modulo non la definisce davvero."""
    import re as _re
    kwl = kw.lower()
    if len(kw) < 3:
        return None
    corpus = [m.get("testo") or "", m.get("narrazione") or ""] + list(m.get("punti") or [])
    for para in corpus:
        for sent in _re.split(r"(?<=[.!?])\s+", str(para)):
            s = sent.strip()
            if len(s) >= 40 and kwl in s.lower():
                return s[:180]
    return None


def build_glossary(moduli):
    """Glossario strutturato per modulo: [{"modulo": titolo, "slide": None,
    "terms": [{"t": termine, "d": definizione}, ...]}, ...].

    Priorità definizioni: flashcards/abbinamenti dell'LLM, poi frase reale del
    modulo che contiene la keyword. Le keyword senza definizione vera vengono
    SCARTATE (meglio un glossario corto e affidabile che voci inventate).
    Termini ordinati alfabeticamente dentro ogni gruppo."""
    groups = []
    seen = set()
    for i, m in enumerate(moduli):
        title = (m.get("titolo") or f"Modulo {i + 1}")[:80]
        terms, local = [], set()
        for fc in (m.get("flashcards") or [])[:3]:
            if isinstance(fc, dict) and fc.get("termine") and fc.get("definizione"):
                k = str(fc["termine"]).strip()
                if k and k.lower() not in seen and k.lower() not in local:
                    seen.add(k.lower()); local.add(k.lower())
                    terms.append({"t": k, "d": str(fc["definizione"])[:180]})
        for ab in (m.get("abbinamenti") or [])[:2]:
            if isinstance(ab, dict) and ab.get("termine") and ab.get("definizione"):
                k = str(ab["termine"]).strip()
                if k and k.lower() not in seen and k.lower() not in local:
                    seen.add(k.lower()); local.add(k.lower())
                    terms.append({"t": k, "d": str(ab["definizione"])[:180]})
        for kw in (m.get("keywords") or [])[:4]:
            k = str(kw).strip().strip(",;.")
            if not k or k.lower() in seen or k.lower() in local:
                continue
            d = _define_keyword(k, m)
            if d:
                seen.add(k.lower()); local.add(k.lower())
                terms.append({"t": k, "d": d})
        if terms:
            terms.sort(key=lambda t: t["t"].lower())
            groups.append({"modulo": title, "slide": None, "terms": terms[:6]})
    return groups[:8]


def glossary_term_count(groups):
    return sum(len(g.get("terms", [])) for g in groups)


def build_final_exam(moduli, max_q=5):
    """Esame finale: un quiz per modulo (round-robin), max `max_q` domande."""
    out = []
    for i, m in enumerate(moduli):
        q = m.get("quiz")
        if isinstance(q, dict) and q.get("domanda") and isinstance(q.get("opzioni"), list):
            opts = [o for o in q["opzioni"] if isinstance(o, dict) and o.get("testo")]
            if len(opts) >= 2:
                out.append({"modulo": m.get("titolo") or f"Modulo {i + 1}",
                            "domanda": str(q["domanda"]),
                            "opzioni": [{"t": str(o["testo"])[:300],
                                         "ok": bool(o.get("corretta")),
                                         "fb": str(o.get("feedback") or "")} for o in opts[:4]],
                            "ok": str(q.get("ok") or "Esatto!"),
                            "ko": str(q.get("ko") or "Rileggi il modulo e riprova.")})
        if len(out) >= max_q:
            break
    return out


def bloom_rubric_text(profilo, stats):
    """Riga di rubrica Bloom per il report docente: obiettivo vs attività prodotte."""
    if not isinstance(profilo, dict):
        return ""
    ob = profilo.get("obiettivo", "?")
    return (f"Rubrica Bloom — obiettivo «{ob}»: quiz {stats.get('quiz', 0)}, "
            f"V/F {stats.get('vf', 0)}, sequenze {stats.get('seq', 0)}, "
            f"compila {stats.get('compila', 0)}, scenari {stats.get('scenario', 0)}, "
            f"errori {stats.get('errore', 0)}, flashcards {stats.get('flashcards', 0)}, "
            f"glossario {stats.get('glossario', 0)} termini, "
            f"abbinamenti {stats.get('matching', 0)}. "
            f"Profilo: {profilo.get('durata', '?')}/{profilo.get('livello', '?')}/{ob}.")

# Rotazione: ogni modulo ha SEMPRE il quiz, più 2 attività prese a giro
# da queste, così i tipi si alternano da un modulo all'altro.
EXTRA_ROTATION = ["vf", "compila", "seq", "scenario", "errore", "flashcards"]


def _extra_keys(i):
    """Le 2 attività extra per il modulo i (rotazione circolare)."""
    return [EXTRA_ROTATION[(i * 2) % len(EXTRA_ROTATION)],
            EXTRA_ROTATION[(i * 2 + 1) % len(EXTRA_ROTATION)]]


def build_slides(struct, draft=False):
    """Costruisce le slide CON la narrazione dentro: audio sincronizzato per costruzione.
    Ogni slide = un passaggio del percorso; la voce legge esattamente ciò che si vede."""
    slides = []
    moduli = struct.get("moduli", [])

    def add(title, blocks, narration, icon=None):
        s = {"title": title, "blocks": blocks, "narration": narration}
        if icon:
            s["icon"] = icon
        slides.append(s)

    # apertura
    blocks = [{"h1": struct["titolo"]}]
    if struct.get("sottotitolo"):
        blocks.append({"p": struct["sottotitolo"]})
    if draft:
        blocks.insert(0, {"banner": "BOZZA generata senza 9router: quiz e abbinamenti da completare"})
    if moduli:
        blocks.append({"list": [f'Modulo {i + 1}: {m["titolo"]}' for i, m in enumerate(moduli)]})
    add("Apertura", blocks,
        struct.get("intro") or f"Benvenuti. In questo percorso esploreremo {struct['titolo']}.",
        ICONS["open"])

    # moduli: contenuto + attività come passaggi separati e narrati
    for i, m in enumerate(moduli):
        tit = (m.get("titolo") or f"Modulo {i + 1}")[:120]
        narr = (m.get("narrazione") or "").strip()
        if not narr and m.get("testo"):
            narr = m["testo"][:300]
        blocks = [{"callout": f"Modulo {i + 1} di {len(moduli)}"},
                  {"h1": tit},
                  {"p": m.get("testo") or ""}]
        if m.get("punti"):
            blocks.append({"list": [p for p in m["punti"] if p]})
        if m.get("keywords"):
            kws = [str(k).strip().strip(",;.") for k in m["keywords"][:4] if str(k).strip()]
            if kws:
                blocks.append({"callout": "🔑 Parole chiave: " + " • ".join(kws)})
        add(f"Modulo {i + 1} – {tit}", blocks,
            narr or f"Parliamo di {tit}.", ICONS["mod"][i % len(ICONS["mod"])])

        # ---- quiz: sempre presente (una per modulo)
        q = m.get("quiz")
        if q and q.get("domanda") and q.get("opzioni"):
            opts = []
            for o in q["opzioni"]:
                if not isinstance(o, dict) or not o.get("testo"):
                    continue
                opts.append({"t": o["testo"], "ok": bool(o.get("corretta")),
                             "fb": o.get("feedback") or ""})
            if len(opts) >= 2:
                qn = (m.get("quiz_narrazione")
                      or f"Ora verifica le tue conoscenze sul modulo {i + 1}.")
                add(f"Quiz modulo {i + 1}",
                    [{"callout": f"Quiz {i + 1}"},
                     {"quiz": {"q": q["domanda"], "opts": opts,
                               "ok": q.get("ok") or "Esatto!",
                               "ko": q.get("ko") or "Rileggi il modulo e riprova."}}],
                    qn, ICONS["quiz"])

        # ---- attività extra: 2 per modulo, a rotazione per variare i tipi
        extras = _extra_keys(i)

        if "vf" in extras:
            vf = m.get("vero_falso") or []
            if len(vf) >= 2:
                items = [{"t": v["affermazione"], "ok": v["vero"],
                          "fb": v.get("spiegazione") or ""} for v in vf[:3]]
                add(f"Vero o falso — modulo {i + 1}",
                    [{"callout": "Vero o falso"},
                     {"vf": items}],
                    "Ora un gioco rapido: giudica se queste affermazioni sono vere o false.",
                    ICONS["vf"])

        if "compila" in extras:
            cp = m.get("compila") or []
            if len(cp) >= 2:
                items = [{"frase": c["frase"], "risposta": c["risposta"],
                          "aiuto": c.get("aiuto") or [""]} for c in cp[:3]]
                add(f"Compila il vuoto — modulo {i + 1}",
                    [{"callout": "Compila il vuoto"},
                     {"compila": items}],
                    "Completa le frasi: clicca il vuoto e scegli la parola giusta tra le opzioni.",
                    ICONS["compila"])

        if "seq" in extras:
            seq = m.get("sequenza")
            if isinstance(seq, dict) and len(seq.get("passi", [])) >= 3:
                add(f"Metti in ordine — modulo {i + 1}",
                    [{"callout": "Sequenza"},
                     {"seq": {"instr": seq.get("istruzione")
                              or "Clicca i passaggi nell'ordine corretto.",
                              "passi": seq["passi"]}}],
                    "Rimetti in ordine i passaggi: cliccali nella sequenza corretta.",
                    ICONS["seq"])

        if "scenario" in extras:
            sc = m.get("scenari")
            if isinstance(sc, dict) and sc.get("situazione"):
                opts = [{"t": o["testo"], "ok": o["corretta"],
                         "fb": o.get("conseguenza") or ""} for o in sc["opzioni"]]
                add(f"Cosa faresti? — modulo {i + 1}",
                    [{"callout": "Scenario"},
                     {"scenario": {"situazione": sc["situazione"], "opts": opts,
                                   "conclusione": sc.get("conclusione") or ""}}],
                    "Mettiti alla prova con un caso concreto: scegli l'azione più corretta.",
                    ICONS["scenario"])

        if "flashcards" in extras:
            fc = m.get("flashcards") or []
            if len(fc) >= 2:
                cards = [{"t": c["termine"], "d": c["definizione"]} for c in fc[:4]]
                add(f"Flashcards — modulo {i + 1}",
                    [{"callout": "Flashcards"},
                     {"flashcards": {"instr": "Studia le carte: clicca per girarle, poi mettiti alla prova.",
                                     "cards": cards}}],
                    "Ecco le carte di studio: girale per memorizzare i concetti chiave, poi verifica te stesso.",
                    ICONS["flash"])

        if "errore" in extras:
            er = m.get("errori") or []
            if er:
                e0 = er[0]
                add(f"Trova l'errore — modulo {i + 1}",
                    [{"callout": "Trova l'errore"},
                     {"errore": {"brano": e0["brano"], "sbagliato": e0.get("sbagliato") or "",
                                 "correzione": e0["correzione"],
                                 "spiegazione": e0.get("spiegazione") or ""}}],
                    "Caccia all'errore: leggi il brano, individua la parte sbagliata e correggila.",
                    ICONS["errore"])

    # abbinamento riepilogo (una sola attività, presa da tutti i moduli)
    pairs = []
    for m in moduli:
        for ab in m.get("abbinamenti", []):
            if ab.get("termine") and ab.get("definizione"):
                pairs.append({"term": ab["termine"], "def": ab["definizione"]})
                break
        if len(pairs) >= 6:
            break
    if len(pairs) >= 2:
        add("Mettiti alla prova: abbina",
            [{"callout": "Riepilogo"},
             {"match": {"instr": "Abbina ogni concetto alla definizione corretta.",
                        "pairs": pairs}}],
            "Mettiti alla prova: abbina ogni concetto alla definizione corretta.",
            ICONS["match"])

    # glossario riepilogativo: gruppi per modulo con link alla slide del modulo
    gloss = build_glossary(moduli)
    if glossary_term_count(gloss) >= 3:
        mod_slide_idx = {}
        for si, s in enumerate(slides):
            for i in range(len(moduli)):
                if s["title"].startswith(f"Modulo {i + 1} –"):
                    mod_slide_idx[i] = si
        for i, g in enumerate(gloss):
            if i < len(moduli) and i in mod_slide_idx:
                g["slide"] = mod_slide_idx[i]
        add("Glossario — parole chiave",
            [{"callout": f"Glossario ({glossary_term_count(gloss)} termini)"},
             {"glossario": {"instr": "Cerca un termine o sfoglia per modulo: "
                                     "clicca per vedere la definizione.",
                            "groups": gloss}}],
            "Prima di concludere, ripassiamo le parole chiave della lezione, "
            "raggruppate per modulo.",
            ICONS["gloss"])

    # esame finale riepilogativo (un quiz per modulo, soglia 70% nel report)
    exam = build_final_exam(moduli)
    for j, eq in enumerate(exam):
        add(f"Esame finale {j + 1}/{len(exam)}",
            [{"callout": f"Esame finale — domanda {j + 1} di {len(exam)} (soglia 70%)"},
             {"quiz": {"q": f"[{eq['modulo'][:50]}] {eq['domanda']}",
                       "opts": eq["opzioni"], "ok": eq["ok"], "ko": eq["ko"],
                       "exam": True}}],
            f"Domanda {j + 1} dell'esame finale, dal modulo {eq['modulo'][:60]}.",
            ICONS["exam"])

    # conclusione
    add("Conclusione",
        [{"h1": "Complimenti!"},
         {"p": struct.get("outro") or "Hai completato il percorso."},
         {"quote": struct.get("citazione") or "La conoscenza cresce quando la verifichi.",
          "attr": struct["titolo"]}],
        struct.get("outro") or "Hai completato il percorso.",
        ICONS["end"])
    return slides


# ================================================================== 4. audio + VTT

# ---------------------------------------------------------------- motore TTS
# Motore primario: edge-tts (voci neurali Microsoft, alta qualità, word boundary
# reali per sottotitoli sincronizzati). Serve la connessione; offline si ripiega
# su Piper locale, infine sul silenzio (l'audio non è mai assente).
EDGE_VOICE = CONFIG.get("edge_voice", "it-IT-GiuseppeMultilingualNeural")
EDGE_RATE = CONFIG.get("edge_rate", "-4%")
EDGE_BITRATE = f"{int(CONFIG.get('audio_bitrate', 96))}k"  # post-produzione a 44.1 kHz


def _ffmpeg_probe(path):
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=15)
        return float(probe.stdout.strip())
    except Exception:
        return None


def _atomic_write_bytes(path, data):
    import tempfile as _tf
    fd, tmp = _tf.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _polish_mp3(src, bitrate="96k"):
    """Post-produzione ffmpeg: loudness uniforme tra le slide (niente salti di
    volume), micro-fade d'ingresso anti-pop e rimux a 44.1 kHz. Se ffmpeg manca
    il file resta com'è: nessun fallimento."""
    if not _ffmpeg_ok():
        return False
    try:
        import tempfile as _tf2
        fd2, tmp = _tf2.mkstemp(dir=str(src.parent), prefix="." + src.name + ".pol.", suffix=".mp3")
        os.close(fd2)
        tmp = Path(tmp)
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
        filt = "loudnorm=I=-17:TP=-1.5:LRA=9,afade=t=in:st=0:d=0.04"
        if AUDIO_DUAL_PASS:
            # Dual-pass: misura poi applica (qualità superiore, ~2x tempo)
            meas = subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), "-af",
                 "loudnorm=I=-17:TP=-1.5:LRA=9:print_format=json",
                 "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, timeout=120, check=False)
            import json as _j
            m = re.search(r"\{[^}]*measured_[^}]+\}", meas.stderr or "", re.S)
            if m:
                try:
                    vals = _j.loads(m.group(0))
                    filt = ("loudnorm=I=-17:TP=-1.5:LRA=9:"
                            f"measured_I={vals['measured_I']}:"
                            f"measured_TP={vals['measured_TP']}:"
                            f"measured_LRA={vals['measured_LRA']}:"
                            f"measured_thresh={vals['measured_thresh']}:"
                            f"offset={vals.get('target_offset', 0)}:"
                            "linear=true,afade=t=in:st=0:d=0.04")
                except Exception:
                    pass
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-af", filt,
             "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", bitrate,
             str(tmp)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=120, check=False)
        if tmp.exists() and tmp.stat().st_size > 1000:
            os.replace(tmp, src)
            return True
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception:
        pass
    return False


def _try_edge_tts(text, mp3_path):
    """Sintesi con edge-tts. Ritorna (ok, words): words = timing reali [(a,b,txt)]."""
    try:
        import asyncio
        import edge_tts

        async def _run():
            comm = edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE,
                                        boundary="WordBoundary")
            mp3 = bytearray()
            words = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    mp3.extend(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append((chunk["offset"] / 1e7,
                                  (chunk["offset"] + chunk["duration"]) / 1e7,
                                  chunk["text"]))
            return bytes(mp3), words

        mp3, words = asyncio.run(_run())
        if len(mp3) > 1000:
            _atomic_write_bytes(mp3_path, mp3)
            return True, words
        return False, []
    except Exception as e:  # noqa: BLE001
        print(f"  edge-tts non disponibile ({str(e)[:80]})", flush=True)
        # Marca l'errore per il chiamante: se non retryable, niente attesa
        _try_edge_tts.last_retryable = _is_retryable(str(e))
        return False, []


def _try_piper(text, mp3_path):
    """Fallback locale: Piper + ffmpeg in mp3. Ritorna (ok, words=[])."""
    try:
        sys.path.insert(0, str(BASE / ".tools" / "piper"))
        from piper.voice import PiperVoice
        voice = PiperVoice.load(str(VOICE))
        wav = mp3_path.with_suffix(".tmp.wav")
        ok = False
        for _ in (1, 2):
            try:
                with wave.open(str(wav), "wb") as f:
                    voice.synthesize_wav(text, f)
                if wav.exists() and wav.stat().st_size > 1000:
                    ok = True
                    break
            except Exception:
                try:
                    wav.unlink()
                except OSError:
                    pass
        if ok:
            if not _ffmpeg_ok():
                return False, []
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav), "-codec:a", "libmp3lame",
                 "-b:a", EDGE_BITRATE, "-ar", "44100", "-ac", "1", str(mp3_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=120, check=False)
            try:
                wav.unlink()
            except OSError:
                pass
            return mp3_path.exists() and mp3_path.stat().st_size > 1000, []
    except Exception as e:  # noqa: BLE001
        print(f"  piper non disponibile ({str(e)[:80]})", flush=True)
    return False, []


# ----------------------------------------------------------------- testo parlato
# Sigle e abbreviazioni che il TTS leggerebbe male ("es." -> "e s"...) e
# ritocchi di prosodia: il testo pulito rende la voce molto più naturale.
_TTS_ABBR = {
    "es.": "esempio", "etc.": "eccetera", "ecc.": "eccetera",
    "art.": "articolo", "artt.": "articoli", "par.": "paragrafo",
    "parr.": "paragrafi", "cap.": "capitolo", "nr.": "numero",
    "n.": "numero", "nn.": "numeri", "pag.": "pagina", "pagg.": "pagine",
    "cfr.": "confronta", "vs.": "contro", "e.g.": "ad esempio",
    "i.e.": "cioè", "prof.": "professore", "dott.": "dottore",
    "dott.ssa": "dottoressa", "d.ssa": "dottoressa",
    "avv.": "avvocato", "ing.": "ingegnere",
}


def _tts_text(raw):
    """Testo ripulito per la sintesi: niente abbreviazioni/sigle leggibili male,
    punteggiatura di pausa normalizzata, niente spazi doppi. È questo testo
    (più pulito) che viene letto e i cui timing sono sincronizzati."""
    t = (raw or "").strip()
    t = (t.replace("\u2019", "'").replace("\u2018", "'")
         .replace("\u201c", '"').replace("\u201d", '"'))
    for abbr, full in _TTS_ABBR.items():
        pat = r"(?<![A-Za-zÀ-ÿ])" + re.escape(abbr) + r"(?![A-Za-zÀ-ÿ])"
        t = re.sub(pat, full, t, flags=re.IGNORECASE)
    t = t.replace("…", ".").replace("...", ".").replace("—", ", ")
    # simboli: il TTS li salta o li legge male
    t = (t.replace("%", " percento").replace("€", " euro")
          .replace("&", " e ").replace("→", " porta a")
          .replace("≥", " uguale o maggiore di ")
          .replace("≤", " uguale o minore di "))
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)   # niente spazio prima della punteggiatura
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def _weighted_words(text, dur):
    """Timing parole stimati quando l'engine non fornisce i word boundary:
    ogni parola pesata sulla lunghezza, distribuite sulla durata reale."""
    ws = (text or "").split()
    if not ws:
        return []
    weights = [max(len(w) + 2, 3) for w in ws]
    wsum = sum(weights) or 1
    cur = 0.0
    words = []
    for wi, w in enumerate(ws):
        d = max(dur * weights[wi] / wsum, 0.12)
        end = min(cur + d, dur) if wi < len(ws) - 1 else dur
        words.append((cur, end, w))
        cur = end
    return words


def _silent_mp3(path, seconds):
    """Ultimo recurso: mp3 silenzioso (l'audio non è mai assente)."""
    if not _ffmpeg_ok():
        # Senza ffmpeg: placeholder minimo (verrà stimata la durata)
        path.write_bytes(b"\x00" * 2048)
        return
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
             "-t", str(max(1.0, seconds)), "-codec:a", "libmp3lame", "-b:a", "32k",
             str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=60, check=False)
    except Exception:
        pass


def generate_audio(out_dir, slides):
    """Audio per OGNI slide, motore a cascata edge-tts -> Piper -> silenzio.
    Durata misurata con ffprobe; word boundary reali dall'engine quando dati,
    altrimenti pesati sulla durata reale. Cache per testo+voce.
    Ritorna (n_cached, durate): le durate misurate vengono riusate dalla
    validazione per evitare una seconda chiamata ffprobe per ogni traccia."""
    AUDIO = out_dir / "assets" / "audio"
    AUDIO.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    cached = 0
    durations = {}
    todo = []   # (i, text, mp3, cached_mp3, cached_meta) da sintetizzare
    engines = {}
    for i, s in enumerate(slides):
        text = _tts_text(s.get("narration")) or "Fine di questa parte."
        h = hashlib.sha1(
            f"{CACHE_VERSION}|edge|{EDGE_VOICE}|{EDGE_RATE}|{EDGE_BITRATE}|{text}".encode("utf-8")
        ).hexdigest()
        mp3 = AUDIO / f"narration-{i + 1:02d}.mp3"
        cached_mp3 = CACHE / f"{h}.mp3"
        cached_meta = CACHE / f"{h}.json"
        engines[i] = "cache"

        if cached_mp3.exists():
            shutil.copyfile(cached_mp3, mp3)
            if cached_meta.exists():
                try:
                    s["_words"] = [tuple(w) for w in json.loads(cached_meta.read_text(encoding="utf-8"))]
                except Exception:
                    s["_words"] = []
            else:
                s["_words"] = []
            cached += 1
        else:
            todo.append((i, text, mp3, cached_mp3, cached_meta))

    if todo:
        # sintesi IN PARALLELO: edge-tts è un servizio di rete, poche richieste
        # insieme accorciano molto il tempo totale; la cascata di fallback
        # (edge -> Piper -> silenzio) resta identica per ogni traccia.
        from concurrent.futures import ThreadPoolExecutor

        def _track(item):
            i, text, mp3, cached_mp3, cached_meta = item
            ok, words = False, []
            # retry con backoff + jitter: solo errori transienti (timeout/429/5xx)
            _try_edge_tts.last_retryable = True
            for attempt in range(TTS_RETRIES + 1):
                ok, words = _try_edge_tts(text, mp3)
                if ok:
                    break
                if not getattr(_try_edge_tts, "last_retryable", True):
                    break  # 401/403/voce errata: inutile riprovare
                if attempt < TTS_RETRIES:
                    _jitter_sleep(2 * (attempt + 1))
            engine = "edge"
            if not ok:
                engine = "piper"
                ok, words = _try_piper(text, mp3)
            if not ok:
                engine = "silenzio"
                est = max(2.0, len(text.split()) / 2.8)
                _silent_mp3(mp3, est)
            else:
                # post-produzione: loudness uniforme tra le slide + fade + 44.1 kHz
                _polish_mp3(mp3, EDGE_BITRATE)
                try:
                    # Scrittura cache atomica con nome unico (evita race tra worker)
                    import tempfile as _tf3
                    fd_c, p_c = _tf3.mkstemp(dir=str(CACHE), prefix=".cache-", suffix=".mp3")
                    os.close(fd_c)
                    Path(p_c).unlink(missing_ok=True)
                    shutil.copyfile(mp3, p_c)
                    os.replace(p_c, cached_mp3)
                    if words:
                        fd_j, p_j = _tf3.mkstemp(dir=str(CACHE), prefix=".cache-", suffix=".json")
                        os.close(fd_j)
                        Path(p_j).write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
                        os.replace(p_j, cached_meta)
                except OSError:
                    pass
            return item, engine, words

        workers = min(TTS_WORKERS, len(todo))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for (i, text, mp3, _cm, _cj), engine, words in pool.map(_track, todo):
                engines[i] = engine
                slides[i]["_words"] = words
                if engine == "silenzio":
                    print(f"  ⚠ slide {i + 1}: audio sostituito da silenzio", flush=True)
                else:
                    print(f"  sintesi slide {i + 1} [{engine}] ok", flush=True)

    for i, s in enumerate(slides):
        text = _tts_text(s.get("narration")) or "Fine di questa parte."
        mp3 = AUDIO / f"narration-{i + 1:02d}.mp3"
        words = s.pop("_words", [])
        engine = engines.get(i, "?")

        dur = _ffmpeg_probe(mp3)
        if not dur or dur <= 0:
            dur = max(2.0, len(text.split()) / 2.8)
        durations[i] = round(dur, 2)
        # timing parole: reali se l'engine li ha forniti, altrimenti pesati
        if not words:
            words = _weighted_words(text, dur)
        s["audio"] = f"./assets/audio/narration-{i + 1:02d}.mp3"
        s["duration"] = round(dur, 2)
        s["caption"] = f"./assets/captions/narration-{i + 1:02d}.vtt"
        s["words"] = [[round(a, 2), round(b, 2), t] for a, b, t in words]
        print(f"  audio {i + 1}/{len(slides)} [{engine}]: {dur:.1f}s — {text[:44]}…", flush=True)
    # cache: se supera il limite, elimina le voci più vecchie (mp3 + json)
    try:
        files = sorted(CACHE.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        total = sum(f.stat().st_size for f in files) / 1048576
        if total > CACHE_MAX_MB:
            for f in files:
                if total <= CACHE_MAX_MB * 0.7:
                    break
                sz = f.stat().st_size / 1048576
                try:
                    f.unlink()
                    (f.with_suffix(".json")).unlink(missing_ok=True)
                except OSError:
                    pass
                total -= sz
            print(f"  cache audio ridotta: ora ~{total:.0f} MB (limite {CACHE_MAX_MB} MB)")
    except Exception:
        pass
    return cached, durations


def write_vtt(out_dir, slides):
    """Sottotitoli parola-per-parola: usa i timing reali dell'engine
    (slide['words']) se presenti, altrimenti pesa le parole sulla durata."""
    CAP = out_dir / "assets" / "captions"
    CAP.mkdir(parents=True, exist_ok=True)

    def fmt(ts):
        hh = int(ts // 3600)
        mm = int(ts % 3600 // 60)
        ss = ts % 60
        return f"{hh:02d}:{mm:02d}:{ss:06.3f}".replace(".", ",")

    for i, s in enumerate(slides):
        total = s.get("duration") or 1.0
        wdata = s.get("words") or []
        if wdata:
            lines = [(float(a), float(b), t) for a, b, t in wdata]
        else:
            lines = _weighted_words(s.get("narration") or " ", total)
        out = "WEBVTT\n\n" + "\n\n".join(
            f"{n}\n{fmt(a)} --> {fmt(b)}\n{t}" for n, (a, b, t) in enumerate(lines, 1))
        (CAP / f"narration-{i + 1:02d}.vtt").write_text(out, encoding="utf-8")


# ================================================================== 5. build
def _validate_audio(out_dir, slides, durations=None):
    """Controllo finale: ogni traccia deve esistere, essere leggibile e durare
    quanto dichiarato (tolleranza 0.6s). Le durate già misurate durante la
    sintesi (`durations`) evitano una seconda chiamata ffprobe per ogni file."""
    durations = durations or {}
    problems = []
    AUDIO = out_dir / "assets" / "audio"
    for i, s in enumerate(slides):
        mp3 = AUDIO / f"narration-{i + 1:02d}.mp3"
        if not mp3.exists() or mp3.stat().st_size < 1000:
            problems.append(f"slide {i + 1}: traccia audio assente o vuota")
            continue
        real = durations.get(i)
        if real is None:
            real = _ffmpeg_probe(mp3)
        if not real or real <= 0:
            problems.append(f"slide {i + 1}: traccia illeggibile (ffprobe)")
            continue
        declared = s.get("duration") or 0
        if declared and abs(real - declared) > 0.6:
            # la durata dichiarata è quella misurata: uno scarto grande indica
            # un file riscritto a metà generazione
            problems.append(f"slide {i + 1}: durata {real:.1f}s != dichiarata {declared:.1f}s")
    return problems


def sanitize_stem(stem):
    # rimuove caratteri non validi su Windows (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    s = re.sub(r"^[\d\s_\-\.]+", "", stem)
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s.strip())[:40].strip(" .")
    s = s or "Lezione"
    reserved = {"CON","PRN","AUX","NUL"} | {f"COM{i}" for i in range(1,10)} | {f"LPT{i}" for i in range(1,10)}
    if s.upper() in reserved:
        s = f"_{s}"
    return s


def _pid_alive(pid):
    try:
        pid = int(pid)
    except Exception:
        return False
    try:
        if os.name == "nt":
            import subprocess as _sp
            out = _sp.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                          capture_output=True, text=True, timeout=5)
            # cerca riga con PID esatto, non substring
            for line in (out.stdout or "").splitlines():
                parts = line.split()
                if parts and parts[0].isdigit() or (len(parts) > 1 and parts[1] == str(pid)):
                    # tasklist NH: "Image Name PID ..." -> PID è seconda colonna
                    if str(pid) in line.split():
                        # verifica che la colonna PID sia esattamente pid
                        import re as _re
                        if _re.search(rf"\b{pid}\b", line):
                            # ulteriore check: assicura che sia PID column
                            tokens = line.split()
                            if str(pid) in tokens:
                                # trova esatta posizione PID
                                if tokens[1] == str(pid) if len(tokens) > 1 and tokens[1].isdigit() else str(pid) in tokens:
                                    return True
                            return True
                    return False
            # fallback: usa psutil-like via wmic se disponibile
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _lock_build():
    """Acquisisce il blocco atomico con O_EXCL. Ritorna True se acquisito."""
    # pulizia stale prima del tentativo atomico
    if LOCK.exists():
        try:
            raw = LOCK.read_text(encoding="utf-8").strip()
            try:
                info = json.loads(raw)
                pid = info.get("pid")
                mtime = LOCK.stat().st_mtime
            except Exception:
                try:
                    pid = int(raw)
                except Exception:
                    pid = None
                mtime = LOCK.stat().st_mtime
            stale = (time.time() - mtime > 30 * 60) or (pid is not None and not _pid_alive(pid))
            if stale:
                try:
                    LOCK.unlink()
                except OSError:
                    pass
        except OSError:
            pass
        except Exception:
            pass
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps({"pid": os.getpid(), "t": time.time()}).encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        # fallback: se O_EXCL non disponibile, prova write con check
        try:
            if not LOCK.exists():
                LOCK.write_text(json.dumps({"pid": os.getpid(), "t": time.time()}), encoding="utf-8")
                return True
        except OSError:
            pass
        return False


def _unlock_build():
    try:
        # cancella solo se siamo proprietari
        raw = LOCK.read_text(encoding="utf-8")
        info = json.loads(raw)
        if int(info.get("pid", -1)) == os.getpid():
            LOCK.unlink()
        else:
            # non siamo proprietari, non cancellare
            pass
    except Exception:
        try:
            LOCK.unlink()
        except OSError:
            pass


def build_from_docx(path, force=False, bozza=False, no_cache=False,
                    single=False, keep_folder=False, profilo=None):
    """Avvia la generazione (con blocco anti-concorrenza: una alla volta).
    `path` può essere un file (.docx/.pdf/.txt/.md/.html) oppure un URL
    (sito web o video YouTube). no_cache=True salta la cache LLM.
    single=True: resta UN SOLO file HTML completo (audio incorporato) e la
    cartella temporanea viene rimossa (keep_folder=True la conserva).
    profilo: dict {durata, livello, obiettivo} (validato, default da config)."""
    if not _lock_build():
        print(f"  ⚠ Un'altra generazione è già in corso ({LOCK.name} presente): salto {path}.")
        return None, False
    try:
        return _build_impl(path, force=force, bozza=bozza, no_cache=no_cache,
                           single=single, keep_folder=keep_folder, profilo=profilo)
    finally:
        _unlock_build()


PROGRESS_FILE = BASE / ".progress.json"


def _set_progress(fase, pct, extra=""):
    """Progress % reale della build (letto dal pannello via /api/log)."""
    try:
        PROGRESS_FILE.write_text(json.dumps(
            {"fase": fase, "pct": pct, "extra": extra, "t": time.time()},
            ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _complete_modules(testo, struct, nmin, profilo):
    """Retry parziale LLM: se mancano moduli, ne chiede solo la differenza
    invece di scartare tutta la risposta (risparmia minuti)."""
    have = len(struct.get("moduli", []))
    need = max(0, nmin - have)
    if need <= 0:
        return struct
    print(f"  retry parziale: ho {have} moduli, ne chiedo altri {need}…", flush=True)
    mini_prompt = (
        "Aggiungi ESATTAMENTE {need} moduli didattici sullo stesso materiale, "
        "stesso schema JSON dei moduli "
        "(titolo/testo/punti/keywords/narrazione/quiz_narrazione/quiz/abbinamenti/"
        "vero_falso/sequenza/compila/scenari/errori/flashcards). "
        "Rispondi SOLO con un JSON {\"moduli\": [...]}.\n\nMATERIALE:\n{testo}"
    ).format(need=need, testo=testo[:FLATTEN_LIMIT])
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    for model in ([LLM_MODEL or pick_model(), "comboact"]):
        if not model:
            continue
        try:
            req = urllib.request.Request(
                f"{LLM_URL}/chat/completions",
                data=json.dumps({"model": model, "temperature": 0.3,
                                 "max_tokens": 4000,
                                 "messages": [{"role": "user", "content": mini_prompt}]}).encode(),
                headers=headers)
            with urllib.request.urlopen(req, timeout=LLM_REQUEST_TIMEOUT) as r:
                out = _extract_api_body(r.read().decode("utf-8"))
            add = parse_json(out["choices"][0]["message"]["content"])
            for m in add.get("moduli", [])[:need]:
                if isinstance(m, dict) and m.get("titolo"):
                    struct.setdefault("moduli", []).append(m)
            _check_struct(struct)
            print(f"  retry parziale ok: ora {len(struct['moduli'])} moduli", flush=True)
            return struct
        except Exception as e:  # noqa: BLE001
            print(f"  retry parziale fallito su {model}: {str(e)[:80]}", flush=True)
    return struct


def _build_impl(source, force=False, bozza=False, no_cache=False,
                single=False, keep_folder=False, profilo=None):
    log = setup_logging()
    src = str(source)
    display = src if is_url(src) else Path(src).name

    t0 = time.time()
    _times = {}
    msg = f"=== GENERO LA LEZIONE DA: {display} ==="
    print(f"\n{msg}")
    log.info(msg)
    _set_progress("avvio", 2, display)
    try:
        from common import normalize_profilo as _np
        _cfg_prof = {"durata": CONFIG.get("profilo_durata"),
                     "livello": CONFIG.get("profilo_livello"),
                     "obiettivo": CONFIG.get("profilo_obiettivo")}
        profilo = _np({**_cfg_prof, **(profilo or {})})
    except Exception:
        profilo = {"durata": "standard", "livello": "intermedio", "obiettivo": "comprensione"}
    _set_progress("lettura materiale", 5, display)
    t_read = time.time()
    ext = extract_source(src)
    _times["lettura"] = round(time.time() - t_read, 1)
    print(f'[1/6] Materiale letto ({len(ext["sections"])} sezioni), '
          f'titolo: {ext["title"][:60]}')

    stem = sanitize_stem(ext["title"] if is_url(src) else Path(src).stem)
    out_dir = BASE / f"{stem}_lesson"
    if out_dir.exists() and not force:
        print(f"  → {out_dir.name} esiste già, salto (usa --force per rigenerare)")
        return out_dir, False

    _set_progress("strutturazione LLM", 15)
    t_llm = time.time()
    if bozza:
        print("[2/6] Modalità BOZZA: salto LLM, struttura dal docx (senza quiz).")
        struct, via_llm = fallback_structure(ext), False
    elif ensure_llm():
        print("[2/6] 9router raggiungibile: strutturazione contenuti (LLM)…")
        try:
            struct, _via = llm_structure(ext, use_cache=not no_cache, profilo=profilo)
            via_llm = True
            if _via == "cache":
                print("  (struttura dalla cache LLM: stessa fonte e modello)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ LLM fallito ({str(e)[:120]}): uso struttura ridotta dal docx")
            struct, via_llm = fallback_structure(ext), False
    else:
        print(f"[2/6] ⚠ 9router non raggiungibile ({LLM_URL}). Uso struttura ridotta (senza quiz).")
        struct, via_llm = fallback_structure(ext), False
    _times["llm"] = round(time.time() - t_llm, 1)

    moduli = struct.get("moduli", [])
    n_quiz = sum(1 for m in moduli if m.get("quiz"))
    n_ab = sum(1 for m in moduli if m.get("abbinamenti"))
    n_vf = sum(1 for m in moduli if m.get("vero_falso"))
    n_seq = sum(1 for m in moduli if m.get("sequenza"))
    n_cp = sum(1 for m in moduli if m.get("compila"))
    n_sc = sum(1 for m in moduli if m.get("scenari"))
    n_er = sum(1 for m in moduli if m.get("errori"))
    n_fc = sum(1 for m in moduli if m.get("flashcards"))
    print(f"  Moduli: {len(moduli)} | Quiz: {n_quiz} | V/F: {n_vf} | Sequenze: {n_seq} | "
          f"Compila: {n_cp} | Scenari: {n_sc} | Errori: {n_er} | Abbinamenti: {n_ab} | "
          f"Flashcards: {n_fc}")

    _set_progress("costruzione slide", 55)
    print("[3/6] Costruisco le slide (narrazione dentro ogni passaggio)…")
    t_sl = time.time()
    slides = build_slides(struct, draft=not via_llm)
    _times["slide"] = round(time.time() - t_sl, 1)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    tema = CONFIG.get("theme", "dark")
    print(f"[4/6] Player autogenerato (tema {tema}, si adatta al titolo)…")
    write_player(out_dir, struct["titolo"], tema=tema)

    _set_progress("audio neurale", 70, f"{len(slides)} slide")
    print("[5/6] Audio neurale edge-tts per ogni slide (cache) + sottotitoli sincronizzati…")
    t_au = time.time()
    cached, measured = generate_audio(out_dir, slides)
    write_vtt(out_dir, slides)
    _times["audio"] = round(time.time() - t_au, 1)
    audio_probs = _validate_audio(out_dir, slides, measured)
    print(f"  cache audio: {cached}/{len(slides)} tracce riusate")

    data = {"titolo": struct["titolo"], "slides": slides, "profilo": profilo}
    (out_dir / "lesson-data.js").write_text(
        "window.LESSON_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n",
        encoding="utf-8")
    bust_cache(out_dir)   # versiona i riferimenti con l'hash dei file

    print("[6/6] Validazione…")
    ok, errs, stats = validate_lesson(out_dir, slides)
    errs.extend(audio_probs)   # problemi audio emersi dopo la sintesi
    print(f'Slide: {stats["slide"]} | Quiz: {stats["quiz"]} | V/F: {stats.get("vf", 0)} | '
          f'Sequenze: {stats.get("seq", 0)} | Compila: {stats.get("compila", 0)} | '
          f'Scenari: {stats.get("scenario", 0)} | Errori: {stats.get("errore", 0)} | '
          f'Matching: {stats["matching"]} | Audio: {stats["audio"]}')
    if errs:
        print("ERRORI:")
        for e in errs:
            print(" -", e)
    else:
        print("VALIDAZIONE OK")
    _set_progress("validazione", 95)
    try:
        extra = None
        if not via_llm:
            extra = "BOZZA senza quiz: avvia 9router e rigenera per la versione completa."
        rub = bloom_rubric_text(profilo, stats)
        tempi = "Tempi fasi (s): " + ", ".join(f"{k}={v}" for k, v in _times.items())
        extra = ((extra + " ") if extra else "") + rub + " " + tempi
        write_report(out_dir, errs, stats, extra)
        print(f"  report scritto in {out_dir.name}/report.html")
        print(f"  {tempi}")
    except Exception:
        pass
    print(f"→ LEZIONE PRONTA: {out_dir}  ({time.time() - t0:.0f}s)\n")
    _set_progress("completata", 100, out_dir.name)
    if not ok:
        print("⚠ La lezione è stata generata ma la validazione ha segnalato problemi.\n")

    # modalità "file unico": esporta tutto (audio compreso) in un solo HTML
    # auto-contenuto e, se richiesto, rimuove la cartella temporanea
    if single:
        try:
            from export_single import export_single
            out_single = BASE / f"{out_dir.name.replace('_lesson', '')}_singola.html"
            export_single(out_dir, out_path=out_single)
            mb = out_single.stat().st_size / 1024 / 1024
            print(f"→ FILE UNICO PRONTO: {out_single} ({mb:.1f} MB — "
                  "ha tutto dentro: audio, testi, attività)", flush=True)
            if not keep_folder:
                shutil.rmtree(out_dir, ignore_errors=True)
                print(f"  cartella {out_dir.name} rimossa: basta il file unico.\n",
                      flush=True)
            return out_single, ok
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ File unico non creato ({e}): resta la cartella {out_dir.name}\n",
                  flush=True)
            return out_dir, ok
    return out_dir, ok


def build_from_text(text, title=None, force=False, bozza=False, no_cache=False,
                    single=False, keep_folder=False, profilo=None):
    """Genera da testo incollato (pannello): scrive un .txt temporaneo e riusa la build."""
    from sources import extract_text_raw
    ext = extract_text_raw(text, title=title)
    tmp = BASE / f"_incollato_{sanitize_stem(ext['title'])}.txt"
    try:
        tmp.write_text(text, encoding="utf-8")
        return build_from_docx(str(tmp), force=force, bozza=bozza, no_cache=no_cache,
                               single=single, keep_folder=keep_folder, profilo=profilo)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def preview_from_docx(path, out_name=None, no_cache=False):
    """Anteprima veloce: solo struttura moduli/quiz, senza audio.
    Accetta un file oppure un URL (sito web / YouTube)."""
    ext = extract_source(str(path))
    if ensure_llm(timeout=15):
        try:
            struct, via = llm_structure(ext, use_cache=not no_cache)
            via = "cache LLM" if via == "cache" else "LLM 9router"
        except Exception as e:  # noqa: BLE001
            struct, via = fallback_structure(ext), f"fallback (LLM errore: {e})"
    else:
        struct, via = fallback_structure(ext), "fallback (9router non raggiungibile)"
    slides = build_slides(struct)
    stem = sanitize_stem(ext["title"])
    out = BASE / (out_name or f"{stem}_anteprima.json")
    out.write_text(json.dumps(struct, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Anteprima ({via}): {len(slides)} slide → {out.name}")
    for i, s in enumerate(slides, 1):
        if any("quiz" in b for b in s["blocks"]):
            att = "quiz ✓"
        elif any("match" in b for b in s["blocks"]):
            att = "abbinamenti ✓"
        elif any("vf" in b for b in s["blocks"]):
            att = "vero/falso ✓"
        elif any("seq" in b for b in s["blocks"]):
            att = "sequenza ✓"
        else:
            att = "—"
        print(f'  {i}. {s["title"][:60]} [{att}]')
    return out


# ================================================================== ensure LLM + watch
NINE_ROUTER_START_TIMEOUT = 90


def _find_9router_cli():
    import os
    appdata = os.environ.get("APPDATA", "")
    js = Path(appdata) / "npm" / "node_modules" / "9router" / "cli.js"
    if js.exists():
        return ["node", str(js)]
    exe = shutil.which("9router")
    if exe:
        return [exe]
    return None


def start_9router():
    cmd = _find_9router_cli()
    if not cmd:
        print("  ⚠ Comando 9router non trovato: avvialo manualmente (comando: 9router)")
        return False
    flags = ["-n", "--skip-update", "-t"]
    for shell in (False, True):
        try:
            subprocess.Popen(cmd + flags, cwd=str(BASE),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=shell)
            return True
        except Exception:
            continue
    print("  ⚠ Impossibile avviare 9router.")
    return False


def ensure_llm(timeout=NINE_ROUTER_START_TIMEOUT):
    if llm_reachable():
        return True
    print("9router non è in esecuzione: provo ad avviarlo automaticamente…")
    started = start_9router()
    waited = 0
    while waited < timeout:
        time.sleep(3)
        waited += 3
        if llm_reachable():
            print("✓ 9router avviato e raggiungibile.")
            return True
    if started:
        print(f"⚠ 9router avviato ma non ancora raggiungibile dopo {timeout}s.")
    else:
        print("⚠ 9router non disponibile. Avvialo manualmente (comando: 9router).")
    return False


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def watch():
    log = setup_logging()
    state = load_state()
    print("=" * 62)
    print("  OSSERVO LA CARTELLA: appena metti un file .docx qui")
    print("  parte automaticamente la generazione della lezione.")
    print("  (Ctrl+C per fermarmi — log in generazione.log)")
    print("=" * 62)
    log.info("watch avviato")
    while not ensure_llm():
        print("⏸ 9router non pronto: riprovo tra 10s… (Ctrl+C per uscire)")
        time.sleep(10)
    while True:
        docs = sorted(p for p in BASE.iterdir()
                      if p.suffix.lower() in SUPPORTED_EXT and p.is_file())
        for f in docs:
            st = state.get(f.name, {})
            if st.get("status") == "ok":
                continue
            size = f.stat().st_size
            time.sleep(2)
            if f.stat().st_size != size:
                continue
            try:
                out, ok = build_from_docx(f, force=True)
                state[f.name] = {"status": "ok" if ok else "warning",
                                 "out": out.name, "time": time.strftime("%Y-%m-%d %H:%M")}
                save_state(state)
            except Exception as e:  # noqa: BLE001
                print(f"✗ ERRORE su {f.name}: {str(e)[:200]}")
                log.error(f"{f.name}: {e}")
                state[f.name] = {"status": "error", "error": str(e)[:200]}
                save_state(state)
        time.sleep(4)


def regen_audio_lesson(lesson_dir):
    """Rigenera audio (voce/config correnti), sottotitoli e player di una lezione
    già generata, senza toccare la struttura dei contenuti (niente LLM).
    Protetto dal lock anti-concorrenza: niente collisioni con una build in corso."""
    if not _lock_build():
        print(f"  ⚠ Un'altra generazione è già in corso ({LOCK.name} presente): salto {lesson_dir}.")
        return
    try:
        _regen_audio_impl(lesson_dir)
    finally:
        _unlock_build()


def _regen_audio_impl(lesson_dir):
    out = Path(lesson_dir)
    js = out / "lesson-data.js"
    if not (out.exists() and js.exists()):
        print(f"  ⚠ {lesson_dir!r} non contiene una lezione generata (manca lesson-data.js).")
        return
    payload = json.loads(re.sub(r"^window\.LESSON_DATA\s*=\s*", "",
                                js.read_text(encoding="utf-8")).rstrip().rstrip(";"))
    slides = payload.get("slides") or []
    if not slides:
        print("  ⚠ lesson-data.js senza slide.")
        return
    t0 = time.time()
    print(f"\n=== RIGENERO AUDIO PER: {out.name} ({len(slides)} slide) ===")
    print(f"  voce: {EDGE_VOICE} @ {EDGE_RATE}")
    write_player(out, payload.get("titolo") or out.name, tema=CONFIG.get("theme", "dark"))
    cached, measured = generate_audio(out, slides)
    write_vtt(out, slides)
    audio_probs = _validate_audio(out, slides, measured)
    js.write_text("window.LESSON_DATA = "
                  + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")
    bust_cache(out)   # versiona i riferimenti con l'hash dei file
    print(f"  cache: {cached}/{len(slides)} tracce riusate")
    ok, errs, stats = validate_lesson(out, slides)
    errs.extend(audio_probs)
    print(f"  VALIDAZIONE: {'OK' if ok and not audio_probs else 'PROBLEMI'}")
    if errs:
        for e in errs:
            print("  -", e)
    print(f"→ AUDIO + PLAYER AGGIORNATI ({time.time() - t0:.0f}s)\n")


def load_lesson(lesson_dir):
    """Carica lesson-data.js di una lezione. Ritorna (out_dir, payload)."""
    out = Path(lesson_dir)
    js = out / "lesson-data.js"
    if not (out.is_dir() and js.is_file() and (out / "index.html").is_file()):
        raise ValueError(f"Lezione non trovata: {lesson_dir}")
    payload = json.loads(re.sub(r"^window\.LESSON_DATA\s*=\s*", "",
                                js.read_text(encoding="utf-8")).rstrip().rstrip(";"))
    if not isinstance(payload.get("slides"), list):
        raise ValueError("lesson-data.js senza slide")
    return out, payload


def save_lesson(out_dir, payload):
    """Scrive lesson-data.js + bust cache (nessun TTS/LLM)."""
    out = Path(out_dir)
    (out / "lesson-data.js").write_text(
        "window.LESSON_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8")
    bust_cache(out)


def validate_slide_edit(index, patch):
    """Valida una patch {title?, narration?, quiz?} per la slide index.
    Ritorna dict pulito {title?, narration?, quiz?} o solleva ValueError."""
    clean = {}
    if "title" in patch:
        t = str(patch["title"] or "").strip()[:200]
        if not t:
            raise ValueError("Titolo vuoto")
        clean["title"] = t
    if "narration" in patch:
        n = str(patch["narration"] or "").strip()
        if not n:
            raise ValueError("Narrazione vuota")
        if len(n) > 3000:
            raise ValueError("Narrazione troppo lunga (max 3000 caratteri)")
        clean["narration"] = n
    if "quiz" in patch:
        q = patch["quiz"]
        if q is None:
            clean["quiz"] = None
        elif isinstance(q, dict):
            domanda = str(q.get("domanda") or q.get("q") or "").strip()
            raw_opts = q.get("opzioni") or q.get("opts") or []
            opts = []
            for o in raw_opts:
                if isinstance(o, dict):
                    t = str(o.get("testo") or o.get("t") or "").strip()
                    if t:
                        opts.append({"testo": t[:300], "corretta": bool(o.get("corretta") or o.get("ok"))})
            if not domanda:
                raise ValueError("Domanda quiz vuota")
            if len(opts) < 2:
                raise ValueError("Il quiz deve avere almeno 2 opzioni")
            if len(opts) > 6:
                opts = opts[:6]
            if not any(o["corretta"] for o in opts):
                opts[0]["corretta"] = True
            # una sola corretta
            first = next(o for o in opts if o["corretta"])
            for o in opts:
                if o is not first:
                    o["corretta"] = False
            clean["quiz"] = {"domanda": domanda[:500], "opzioni": opts}
        else:
            raise ValueError("Quiz malformato")
    if not clean:
        raise ValueError("Nessuna modifica valida")
    return clean


def move_slide(lesson_dir, frm, to):
    """Sposta la slide `frm` in posizione `to` (riordino docente)."""
    out, payload = load_lesson(lesson_dir)
    slides = payload["slides"]
    if not (0 <= frm < len(slides)) or not (0 <= to < len(slides)):
        raise ValueError("Indice slide fuori range")
    s = slides.pop(frm)
    slides.insert(to, s)
    save_lesson(str(out), payload)
    return len(slides)


def delete_slide(lesson_dir, index):
    """Elimina una slide (min 3 slide restanti)."""
    out, payload = load_lesson(lesson_dir)
    slides = payload["slides"]
    if len(slides) <= 3:
        raise ValueError("Minimo 3 slide: impossibile eliminare")
    if not (0 <= index < len(slides)):
        raise ValueError("Indice slide fuori range")
    slides.pop(index)
    save_lesson(str(out), payload)
    return len(slides)


def add_slide(lesson_dir, title, narration):
    """Aggiunge una slide contenuto in fondo (prima di glossario/esame/conclusione)."""
    out, payload = load_lesson(lesson_dir)
    slides = payload["slides"]
    t = str(title or "").strip()[:200] or "Nuova slide"
    n = str(narration or "").strip()[:3000] or "Nuovo contenuto della lezione."
    # inserisci prima delle slide speciali finali (glossario/esame/conclusione)
    pos = len(slides) - 1
    for i, s in enumerate(slides):
        if str(s.get("title", "")).startswith(("Glossario", "Esame finale", "Conclusione")):
            pos = i
            break
    slides.insert(pos, {"title": t, "blocks": [{"h1": t}, {"p": n}], "narration": n,
                        "audio": None, "duration": 0})
    save_lesson(str(out), payload)
    return pos


def regen_slide_audio(lesson_dir, index):
    """Rigenera audio+VTT della SOLA slide `index` (voce/config correnti).
    Aggiorna duration/words in lesson-data.js. Ritorna (durata, engine)."""
    if not _lock_build():
        raise RuntimeError("Un'altra generazione è in corso: riprova dopo.")
    try:
        out, payload = load_lesson(lesson_dir)
        slides = payload["slides"]
        if not (0 <= index < len(slides)):
            raise ValueError(f"Slide {index} fuori range (0-{len(slides) - 1})")
        s = slides[index]
        text = _tts_text(s.get("narration")) or "Fine di questa parte."
        mp3 = out / "assets" / "audio" / f"narration-{index + 1:02d}.mp3"
        mp3.parent.mkdir(parents=True, exist_ok=True)
        ok, words = _try_edge_tts(text, mp3)
        engine = "edge"
        if not ok:
            engine = "piper"
            ok, words = _try_piper(text, mp3)
        if not ok:
            engine = "silenzio"
            _silent_mp3(mp3, max(2.0, len(text.split()) / 2.8))
        else:
            _polish_mp3(mp3, EDGE_BITRATE)
            try:  # aggiorna cache globale
                h = hashlib.sha1(
                    f"{CACHE_VERSION}|edge|{EDGE_VOICE}|{EDGE_RATE}|{EDGE_BITRATE}|{text}".encode("utf-8")
                ).hexdigest()
                tmp_c = CACHE / f"{h}.mp3.tmp"
                shutil.copyfile(mp3, tmp_c)
                os.replace(tmp_c, CACHE / f"{h}.mp3")
                if words:
                    tmp_j = CACHE / f"{h}.json.tmp"
                    tmp_j.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
                    os.replace(tmp_j, CACHE / f"{h}.json")
            except OSError:
                pass
        dur = _ffmpeg_probe(mp3) or max(2.0, len(text.split()) / 2.8)
        if not words:
            words = _weighted_words(text, dur)
        s["duration"] = round(dur, 2)
        s["words"] = [[round(a, 2), round(b, 2), t] for a, b, t in words]
        # VTT della sola slide (stesso formato di write_vtt)
        cap_dir = out / "assets" / "captions"
        cap_dir.mkdir(parents=True, exist_ok=True)

        def _fmt(ts):
            hh = int(ts // 3600)
            mm = int(ts % 3600 // 60)
            ss = ts % 60
            return f"{hh:02d}:{mm:02d}:{ss:06.3f}".replace(".", ",")

        lines = [(float(a), float(b), t) for a, b, t in s["words"]]
        vtt = "WEBVTT\n\n" + "\n\n".join(
            f"{n}\n{_fmt(a)} --> {_fmt(b)}\n{t}" for n, (a, b, t) in enumerate(lines, 1))
        (cap_dir / f"narration-{index + 1:02d}.vtt").write_text(vtt, encoding="utf-8")
        save_lesson(out, payload)
        return round(dur, 2), engine
    finally:
        _unlock_build()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    log = setup_logging()

    def need_file(p, label):
        f = Path(p)
        if not f.exists():
            print(f"✗ {label}: file non trovato: {p}")
            sys.exit(1)
        return f

    if len(args) >= 1 and args[0] == "watch":
        watch()
    elif len(args) >= 2 and args[0] == "build":
        src = args[1]
        if is_url(src):
            f = src
        else:
            f = need_file(args[1], "Genera lezione")
        try:
            def _opt(name):
                for a in sys.argv:
                    if a.startswith(name + "="):
                        return a.split("=", 1)[1]
                return None
            from common import normalize_profilo as _npc
            _cli_prof = _npc({"durata": _opt("--durata"), "livello": _opt("--livello"),
                              "obiettivo": _opt("--obiettivo")})
            build_from_docx(f, force="--force" in sys.argv, bozza="--bozza" in sys.argv,
                            no_cache="--no-cache" in sys.argv,
                            single="--single" in sys.argv,
                            keep_folder="--keep-folder" in sys.argv,
                            profilo=_cli_prof)
        except Exception as e:  # noqa: BLE001
            print(f"✗ Generazione fallita: {e}")
            log.error(f"build {f if isinstance(f, str) else f.name}: {e}")
            sys.exit(1)
    elif len(args) >= 2 and args[0] == "reaudio":
        d = need_file(args[1], "Riaudio/riplayer")
        try:
            regen_audio_lesson(d)
        except Exception as e:  # noqa: BLE001
            print(f"✗ Riaudio fallito: {e}")
            log.error(f"reaudio {d.name}: {e}")
            sys.exit(1)
    elif len(args) >= 2 and args[0] == "preview":
        src = args[1]
        if is_url(src):
            f = src
        else:
            f = need_file(args[1], "Anteprima")
        try:
            preview_from_docx(f, no_cache="--no-cache" in sys.argv)
        except Exception as e:  # noqa: BLE001
            print(f"✗ Anteprima fallita: {e}")
            sys.exit(1)
    elif len(args) >= 2 and args[0] in ("single", "export"):
        d = need_file(args[1], "HTML singolo")
        from export_single import export_single
        try:
            p = export_single(d)
            mb = p.stat().st_size / 1024 / 1024
            print(f"→ HTML singolo pronto: {p}")
            print(f"  dimensione: {mb:.1f} MB — apri con doppio clic nel browser")
        except Exception as e:  # noqa: BLE001
            print(f"✗ Esportazione fallita: {e}")
            sys.exit(1)
    elif len(args) >= 1 and args[0] == "check":
        import runpy
        runpy.run_path(str(BASE / "check_env.py"), run_name="__main__")
    else:
        print(__doc__)
        print("Esempi:")
        print("  python new_lesson.py watch")
        print("  python new_lesson.py build 02_Regolamento_IA_Revisionato.docx")
        print("  python new_lesson.py build https://it.wikipedia.org/wiki/Energia")
        print("  python new_lesson.py build https://www.youtube.com/watch?v=VIDEO_ID")
        print("  python new_lesson.py preview 02_Regolamento_IA_Revisionato.docx")
        print("  python new_lesson.py single <cartella_lesson>   # HTML unico, tutto incluso")
        sys.exit(1)


if __name__ == "__main__":
    main()
