# -*- coding: utf-8 -*-
"""La generazione deve produrre attività USABILI e variare.

Copre le funzioni che decidono cosa vede lo studente, senza le quali la lezione
esce formalmente valida ma practically rotta:
  - `_taglia_opzioni`: troncatura che non deve mai perdere la risposta giusta
    (prima: `out[:maxn` produceva quiz con SOLO distrattori, irrisolvibili);
  - `_modulo_ha` / `_extra_keys`: la rotazione deve usare le attività che
    l'LLM ha davvero prodotto, e variare fra moduli;
  - `build_final_exam`: l'esame non deve ripetere le domande già poste;
  - `vtt_ts`: timestamp WebVTT arrotondati che producevano `00:00:60,000`;
  - `dumps_js`: U+2028/U+2029 rendevano lesson-data.js non parsabile.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

import new_lesson as nl  # noqa: E402


# --------------------------------------------------------------- _taglia_opzioni
def _opts(n, giusta_a=0):
    return [{"t": f"opzione {i}", "ok": i == giusta_a} for i in range(n)]


@pytest.mark.parametrize("giusta_a", range(6))
def test_taglia_opzioni_conserva_la_risposta_corretta(giusta_a):
    """Il bug: si tagliava a maxn e la corretta poteva sparire."""
    out = nl._taglia_opzioni(_opts(6, giusta_a), 3)
    assert len(out) == 3
    assert sum(1 for o in out if o["ok"]) == 1, "esattamente una opzione deve essere corretta"
    assert any(o["ok"] for o in out), "la risposta corretta e' stata persa nella troncatura"
    giusta = next(o for o in out if o["ok"])
    assert giusta["t"] == f"opzione {giusta_a}"


def test_taglia_opzioni_oltre_il_limite():
    out = nl._taglia_opzioni(_opts(4), 3)
    assert len(out) == 3, "la lista era gia' sotto il limite: deve restare intatta"


def test_la_garanzia_che_una_opzione_e_corretta_viene_da_norm_opts():
    """`_taglia_opzioni` non crea risposte corrette: le garantisce `_norm_opts`,
    che gira in `_check_struct` prima della costruzione delle slide. Il
    contratto da presidiare e' la catena, non il singolo anello.

    Attenzione alle chiavi: `_norm_opts` lavora sull'output GREZZO dell'LLM
    (`testo`/`corretta`), `_taglia_opzioni` su quello normalizzato del player
    (`t`/`ok`).
    """
    grezze = [{"testo": "a", "corretta": False}, {"testo": "b", "corretta": False},
              {"testo": "c", "corretta": False}]
    normate = nl._norm_opts(grezze)
    assert len(normate) == 3
    assert sum(1 for o in normate if o["corretta"]) == 1, (
        "_norm_opts deve garantire esattamente una risposta corretta")
    # la pipeline converte nel formato del player, poi tronca
    per_player = [{"t": o["testo"], "ok": bool(o["corretta"]), "fb": ""}
                  for o in normate]
    tagliate = nl._taglia_opzioni(per_player, 2)
    assert sum(1 for o in tagliate if o["ok"]) == 1
    assert any(o["ok"] for o in tagliate)


def test_norm_opts_conserva_la_corretta_oltre_la_quarta_posizione():
    """Il bug originale: `out[:maxn` tagliava e la corretta spariva."""
    grezze = [{"testo": f"o{i}", "corretta": i == 5} for i in range(7)]
    out = nl._norm_opts(grezze, maxn=4)
    assert len(out) == 4
    assert sum(1 for o in out if o["corretta"]) == 1
    assert any(o["testo"] == "o5" for o in out), "la risposta corretta e' stata persa"


def test_norm_opts_una_sola_opzione_rende_il_quiz_irrisolvibile():
    """Con una sola opzione non c'e' scelta: viene aggiunto un riempimento."""
    out = nl._norm_opts([{"testo": "unica", "corretta": True}])
    assert len(out) >= 2, "un quiz con una sola opzione non e' utilizzabile"
    assert sum(1 for o in out if o["corretta"]) == 1


# ------------------------------------------------------------------ _modulo_ha
def _modulo_con(**campi):
    m = {"titolo": "M", "testo": "t", "punti": [], "keywords": [], "narration": "n"}
    m.update(campi)
    return m


def test_modulo_ha_rispetta_le_soglie_di_validazione():
    """Le soglie devono coincidere con quelle di `_check_struct` e
    `build_slides`: se sono piu' permissive la rotazione assegna un turno a
    un'attivita' che il costruttore scarterebbe."""
    # sequenza: _check_struct richiede >= 3 passi
    assert nl._modulo_ha({"sequenza": {"passi": ["a", "b"]}}, "seq") is False
    assert nl._modulo_ha({"sequenza": {"passi": ["a", "b", "c"]}}, "seq") is True
    # vero/falso: build_slides richiede >= 2
    assert nl._modulo_ha({"vero_falso": [{"t": "a", "ok": True}]}, "vf") is False
    assert nl._modulo_ha({"vero_falso": [{"t": "a", "ok": True}, {"t": "b", "ok": False}]},
                         "vf") is True
    # compila: >= 2 vuoti
    assert nl._modulo_ha({"compila": [{"frase": "a ___", "risposta": "x"}]}, "compila") is False
    # classificazione: la chiave normalizzata e' `elementi`, non `item`,
    # e servono >= 4 elementi
    assert nl._modulo_ha({"classificazione": {"item": [1, 2, 3, 4]}}, "classifica") is False
    assert nl._modulo_ha({"classificazione": {"elementi": [1, 2, 3]}}, "classifica") is False
    assert nl._modulo_ha({"classificazione": {"elementi": [1, 2, 3, 4]}}, "classifica") is True


def test_modulo_ha_traduce_i_nomi_dei_tipi():
    """La rotazione ragiona in tipi ('vf', 'errore', 'match', 'scenario') ma i
    campi del modulo hanno nomi diversi. Senza la mappa, `_modulo_ha(m,'vf')`
    guardava `m['vf']` — che non esiste — e scartava TUTTO."""
    m = _modulo_con(vero_falso=[{"t": "a", "ok": 1}, {"t": "b", "ok": 0}],
                    errori=[{"brano": "b", "errore": "e"}],
                    abbinamenti=[{"termine": "t", "definizione": "d"}] * 3,
                    scenari=[{"situazione": "s", "opzioni": []}])
    assert nl._modulo_ha(m, "vf") is True
    assert nl._modulo_ha(m, "errore") is True
    assert nl._modulo_ha(m, "match") is True
    assert nl._modulo_ha(m, "scenario") is True


# ----------------------------------------------------------------- _extra_keys
def _modulo_ricco():
    return _modulo_con(
        vero_falso=[{"t": f"a{i}", "ok": True} for i in range(3)],
        sequenza={"passi": ["a", "b", "c", "d"]},
        compila=[{"frase": "a ___", "risposta": "x"} for _ in range(2)],
        errori=[{"brano": "b", "errore": "e"}],
        scenari=[{"situazione": "s", "opzioni": []}],
        abbinamenti=[{"termine": "t", "definizione": "d"}] * 3,
        flashcards=[{"termine": "t", "definizione": "d"}])


def test_extra_keys_usa_solo_attivita_disponibili():
    """Il modulo ha solo vero/falso e un quiz: la rotazione non deve assegnare
    `seq` o `classifica`, che il costruttore scarterebbe."""
    povero = _modulo_con(vero_falso=[{"t": "a", "ok": 1}, {"t": "b", "ok": 0}])
    for i in range(8):
        for k in nl._extra_keys(i, modulo=povero, profilo={"obiettivo": "auto"}):
            assert nl._modulo_ha(povero, k), f"assegnata '{k}', non disponibile"


def test_extra_keys_varia_fra_moduli():
    """Il difetto reale: con la profondita' al primo posto, 'errore' vinceva
    sempre e finiva in TUTTI i moduli (verificato su una build: 4 moduli su 4
    con la stessa attivita')."""
    m = _modulo_ricco()
    profilo = {"obiettivo": "auto"}
    scelte = []
    usati = set()
    for i in range(6):
        k = nl._extra_keys(i, modulo=m, profilo=profilo, usati=usati)
        assert k, "il modulo ricco non ha prodotto nessuna attivita'"
        usati.update(k)
        scelte.extend(k)
    assert len(set(scelte)) >= 4, (
        f"solo {len(set(scelte))} tipi diversi su {len(scelte)} assegnazioni: "
        f"{scelte}")


def test_extra_keys_bes_riduce_le_attivita():
    m = _modulo_ricco()
    standard = nl._extra_keys(0, modulo=m, profilo={"obiettivo": "auto"})
    bes = nl._extra_keys(0, modulo=m,
                         profilo={"obiettivo": "auto", "accessibilita": "bes"})
    assert len(standard) == 2
    assert len(bes) == 1, "il profilo BES deve dimezzare il carico cognitivo"


def test_extra_keys_segue_l_obiettivo_bloom():
    m = _modulo_ricco()
    analisi = nl._extra_keys(0, modulo=m, profilo={"obiettivo": "analisi"})
    conoscenza = nl._extra_keys(0, modulo=m, profilo={"obiettivo": "conoscenza"})
    assert analisi != conoscenza, (
        "l'obiettivo Bloom deve cambiare il tipo di attivita', non solo il testo")


# ------------------------------------------------------------ build_final_exam
def _modulo_esame(chiave, testo="d"):
    q = {"domanda": f"domanda {testo}",
         "opzioni": [{"testo": "giusta", "corretta": True, "feedback": "f"},
                     {"testo": "sbagliata", "corretta": False, "feedback": "f"}],
         "ok": "ok", "ko": "ko"}
    return _modulo_con(titolo="Mod", **{chiave: q})


def test_esame_preferisce_le_domande_dedicate():
    moduli = [_modulo_esame("quiz_esame", "sintesi"),
              _modulo_esame("quiz_esame", "sintesi2")]
    esame = nl.build_final_exam(moduli)
    assert len(esame) == 2
    assert not any(e.get("ripresa") for e in esame), (
        "l'esame sta riusando le domande gia' poste nei moduli")
    assert "sintesi" in esame[0]["domanda"]


def test_esame_ripresa_segnalata_ma_mai_silenziosamente():
    """Senza quiz_esame si puo' solo riusare il quiz del modulo: va dichiarato,
    altrimenti l'esame sembra una verifica nuova e non lo e'."""
    esame = nl.build_final_exam([_modulo_esame("quiz", "delmodulo")])
    assert esame and esame[0].get("ripresa") is True
    assert "delmodulo" in esame[0]["domanda"]


def test_esame_rispetta_il_tetto():
    moduli = [_modulo_esame("quiz_esame", f"d{i}") for i in range(9)]
    assert len(nl.build_final_exam(moduli, max_q=5)) == 5


def test_esame_opzioni_brevi_e_corretta_presente():
    moduli = [_modulo_con(titolo="M", quiz={
        "domanda": "d",
        "opzioni": [{"testo": f"opzione lunghissima {i}", "corretta": i == 3}
                   for i in range(6)],
        "ok": "", "ko": ""})]
    esame = nl.build_final_exam(moduli)
    assert esame, "l'esame non e' stato prodotto"
    opts = esame[0]["opzioni"]
    assert sum(1 for o in opts if o["ok"]) == 1, "esattamente una corretta"
    assert len(opts) <= 4


# --------------------------------------------------------------------- vtt_ts
@pytest.mark.parametrize("sec,atteso", [
    (0, "00:00:00,000"),
    (1.5, "00:00:01,500"),
    (59.9995, "00:01:00,000"),      # il caso che produceva 00:00:60,000
    (59.99949, "00:00:59,999"),
    (3599.9999, "01:00:00,000"),
    (3600, "01:00:00,000"),
    (86399.999, "23:59:59,999"),
])
def test_vtt_ts_always_valid(sec, atteso):
    """`f"{ss:06.3f}"` arrotondava e poteva produrre 00:00:60,000, che non
    e' un timestamp WebVTT valido: il cue veniva scartato dal browser."""
    ts = nl.vtt_ts(sec)
    assert ts == atteso
    assert int(ts[6:8]) < 60, f"secondi fuori range: {ts}"
    assert int(ts[3:5]) < 60, f"minuti fuori range: {ts}"


def test_vtt_ts_gestisce_negativi_e_grandi():
    assert nl.vtt_ts(-5) == "00:00:00,000"
    assert nl.vtt_ts(36000) == "10:00:00,000"


# ------------------------------------------------------------------ dumps_js
def test_dumps_js_escapa_i_separatori_di_riga_javascript():
    """U+2028/U+2029 sono LineTerminator in JavaScript: dentro una stringa
    rendono lesson-data.js non parsabile, e il player mostrava
    'Dati della lezione non trovati'. Escono da un .docx o da un PDF copiato."""
    out = nl.dumps_js({"t": "riga\u2028qui\u2029fine"})
    assert "\u2028" not in out and "\u2029" not in out
    assert "\\u2028" in out and "\\u2029" in out
    import json
    # il risultato deve restare JSON valido
    assert json.loads(out)["t"] == "riga\u2028qui\u2029fine"


def test_dumps_js_preserva_gli_accenti():
    out = nl.dumps_js({"t": "perchè così"})
    assert "perchè così" in out, "la serializzazione deve mantenere i caratteri UTF-8"


# --------------------------------------------------------- contrasto dell'accent
def test_accent_sempre_legibile_su_ogni_titolo():
    """L'accent dipende dal titolo della lezione, quindi il difetto compariva
    solo su alcune lezioni: testo bianco su un verde acceso dava 1,53:1, cioe'
    pulsanti illeggibili. `--accent-ink` garantisce almeno 4,5:1 ovunque."""

    import player_template as pt

    peggiori = []
    for i in range(300):
        a, _a2, ink = pt._accent_from_title(f"Lezione di prova numero {i}")
        peggiori.append((pt.contrast_ratio(ink, a), a, ink))
    peggiori.sort()
    assert peggiori[0][0] >= 4.5, (
        f"accent {peggiori[0][1]} con inchiostro {peggiori[0][2]}: "
        f"contrasto {peggiori[0][0]:.2f}:1 sotto la soglia")
    assert all(c >= 4.5 for c, _, _ in peggiori)


def test_accent_vivace_il_contrasto_non_lo_spenga():
    """Il contrasto si ottiene regolando la LUMINOSITA', non la saturazione.

    La versione precedente fissava anche la saturazione a 0,5 durante
    l'aggiustamento: il verde acceso della lezione originale diventava
    verdolino spento, e il docente segnalava la grafica "persa".
    """
    import colorsys

    import player_template as pt

    sats = []
    for i in range(120):
        a, _a2, _ink = pt._accent_from_title(f"Titolo con hash {i}")
        r, g, b = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
        sats.append(colorsys.rgb_to_hls(r / 255, g / 255, b / 255)[2])
    media = sum(sats) / len(sats)
    assert media >= 0.65, (
        f"saturazione media {media:.2f}: il colore e' diventato spento "
        "in nome del contrasto")
