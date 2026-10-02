# -*- coding: utf-8 -*-
"""Test delle nuove funzionalità (nessuna rete, nessun LLM)."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

import new_lesson
from new_lesson import (build_final_exam, build_glossary, build_slides,
                        bloom_rubric_text, glossary_term_count)
from sources import extract_text_raw


def _mod(titolo="M1"):
    tag = "".join(ch for ch in titolo if ch.isalnum())
    return {"titolo": titolo, "testo": "Testo.", "punti": ["p1"],
            "keywords": [f"chiave1{tag}", f"chiave2{tag}"], "narrazione": "Narr.",
            "quiz": {"domanda": "D?", "opzioni": [
                {"testo": "giusta", "corretta": True, "feedback": "ok"},
                {"testo": "sbagliata", "corretta": False, "feedback": "ko"}],
                "ok": "ok", "ko": "ko"},
            "abbinamenti": [{"termine": f"T1{tag}", "definizione": "D1"}],
            "flashcards": [{"termine": f"F1{tag}", "definizione": "def uno due tre"}],
            "vero_falso": [], "sequenza": None, "compila": [],
            "scenari": None, "errori": []}


def test_glossary_dedup():
    groups = build_glossary([_mod("A"), _mod("B")])
    assert glossary_term_count(groups) >= 3
    names = [t["t"].lower() for g in groups for t in g["terms"]]
    assert len(names) == len(set(names))


def test_glossary_grouped_sorted():
    groups = build_glossary([_mod("B"), _mod("A")])
    assert len(groups) == 2
    assert groups[0]["modulo"] == "B" and groups[1]["modulo"] == "A"
    for g in groups:
        titles = [t["t"].lower() for t in g["terms"]]
        assert titles == sorted(titles)


def test_glossary_real_definitions_only():
    m = _mod("A")
    m["testo"] = ("La fotosintesi è il processo con cui le piante producono "
                  "nutrimento dalla luce del sole e dalla clorofilla.")
    m["keywords"] = ["fotosintesi", "parolainventataxyz"]
    m["flashcards"] = []
    m["abbinamenti"] = []
    groups = build_glossary([m])
    assert glossary_term_count(groups) == 1
    t = groups[0]["terms"][0]
    assert t["t"] == "fotosintesi"
    assert "piante" in t["d"]            # frase reale del modulo
    assert "Concetto chiave" not in t["d"]  # mai definizioni inventate


def test_il_glossario_non_e_piu_una_slide():
    """La lezione non genera piu' la slide del glossario.

    Era una slide intera di sola consultazione piu' un bottone nel menu che
    portava li': toglieva una slide dal percorso senza aggiungere esercizio.
    """
    struct = {"titolo": "T", "sottotitolo": "s", "intro": "i",
              "outro": "o", "citazione": "c",
              "moduli": [_mod(f"M{i}") for i in range(4)]}
    slides = build_slides(struct)
    titles = [s["title"] for s in slides]
    assert not any(t.startswith("Glossario") for t in titles), (
        "la slide del glossario e' stata riaggiunta")
    for s in slides:
        for b in s.get("blocks", []):
            assert "glossario" not in b, "resta un blocco glossario"


def test_nel_player_non_c_e_piu_il_glossario():
    """Niente glossario nel player: nemmeno il bottone nel menu.

    Tolto il glossario, il bottone sarebbe un salto a una slide inesistente
    (il suo handler finiva sull'ultima slide quando non trovava quella giusta).
    """
    import player_template as pt
    from player_assets import base_css, main_js

    assert "btnGloss" not in str(pt.THEMES), "il bottone resta nelle stringhe"
    assert "btnGloss" not in main_js(), "il bottone del glossario e' ancora cablato"
    assert "blockGlossario" not in main_js(), "il blocco glossario e' ancora nel player"
    assert "glosearch" not in base_css(), "il CSS del glossario e' rimasto"


def test_flashcards_la_carta_torna_sulla_faccia_originale():
    """La definizione si vede e POI la carta torna sul termine.

    Prima la carta si girava una volta sola e RESTAVA girata: per rivedere la
    definizione bisognava uscire e rientrare nella slide, e con piu' carte
    girate il mazzo sembrava bloccato. Ora ogni click mostra la definizione e
    un timer la riporta sulla faccia del termine; il click successivo la
    rivede. La verifica sotto resta in pagina, quindi l'attivita' non viene
    saltata.
    """
    from player_assets import main_js
    js = main_js()
    assert "FLASH_VISIBLE_MS" in js, "manca la durata di lettura della definizione"
    assert "card.classList.add('flip');" in js
    assert "card.classList.remove('flip');" in js, (
        "la carta deve tornare sul termine, non restare girata")
    assert "if (card.flipT) clearTimeout(card.flipT);" in js, (
        "clicchi di fila: senza annullare il timer precedente la carta torna su "
        "al primo scadere, non dopo l'ultimo click")
    assert "if (!girata) {" not in js, "il blocco 'gira una volta sola' e' ancora"
    assert "!c2.classList.contains('flip')" not in js, (
        "il bottone Gira deve poter rivedere la carta mostrata")


def test_trova_l_errore_mostra_presto_il_suggerimento():
    """Il suggerimento compare al primo errore, col numero di tentativo.

    Il brano e' una frase di 15-25 parole cliccabili: senza un appiglio
    presto lo studente clicca a caso e resta fermo sull'esercizio.
    """
    from player_assets import main_js
    js = main_js()
    assert "wrongPicks >= 1" in js, (
        "il suggerimento arriva troppo tardi: dopo 2 errori si arriva al terzo")
    assert "tentativo" in js, "manca il resoconto dei tentativi"


def test_il_pulsante_classifica_e_in_evidenza():
    """L'invio alla classifica non puo' passare inosservato.

    Era un bottone grigio identico a 'CSV' e 'Stampa', quindi lo studente
    chiudeva la lezione senza sapere che il risultato andava inviato.
    """
    from player_assets import base_css, main_js
    js, css = main_js(), base_css()
    assert "sendbtn" in js, "il bottone deve avere una classe dedicata"
    assert "sendrow" in js
    assert ".sendrow .sendbtn" in css
    # a piena larghezza e colorato, non grigio come gli export
    blocco = css.split(".sendrow .sendbtn {")[1].split("}")[0]
    assert "width: 100%" in blocco
    assert "linear-gradient(135deg, var(--accent)" in blocco


def test_il_pulsante_classifica_compare_senza_attivita_svolte():
    """Il bottone che conclude il percorso non puo' sparire.

    Era dentro il ramo `if (done > 0)` degli export: con zero attivita'
    svolte non compariva, e lo studente non aveva piu' modo di inviare il
    risultato al docente.
    """
    from player_assets import main_js
    js = main_js()
    ramo = js.split("if (done > 0) {")[1]
    # primo blocco chiuso del ramo: deve contenere SOLO l'export
    primo = ramo.split("\n  }")[0]
    assert "sendrow" not in primo, (
        "il bottone classifica e' ancora dentro il ramo done > 0: sparisce "
        "quando lo studente non ha svolto attivita'")
    assert "sendrow" in js, "il bottone classifica deve esistere comunque"



def test_final_exam_limit():
    mods = [_mod(f"M{i}") for i in range(7)]
    exam = build_final_exam(mods)
    assert 1 <= len(exam) <= 5


def test_build_slides_ha_esame_e_conclusione():
    struct = {"titolo": "T", "sottotitolo": "s", "intro": "i",
              "outro": "o", "citazione": "c",
              "moduli": [_mod(f"M{i}") for i in range(4)]}
    slides = build_slides(struct)
    titles = [s["title"] for s in slides]
    assert any(t.startswith("Esame finale") for t in titles)
    assert titles[-1] == "Conclusione"


def test_rubric_text():
    r = bloom_rubric_text({"durata": "standard", "livello": "base",
                           "obiettivo": "comprensione"}, {"quiz": 4})
    assert "comprensione" in r and "quiz 4" in r


def test_extract_text_raw():
    ext = extract_text_raw("Riga uno.\nRiga due, un po' più lunga per il test. " * 5,
                           title="Mio titolo")
    assert ext["title"] == "Mio titolo" and ext["sections"]


def test_extract_text_raw_too_short():
    try:
        extract_text_raw("ciao")
        assert False, "doveva fallire"
    except ValueError:
        pass


def test_move_delete_add_slide(tmp_path):
    payload = {"titolo": "T", "slides": [
        {"title": f"S{i}", "blocks": [], "narration": "n"} for i in range(5)]}
    (tmp_path / "lesson-data.js").write_text(
        "window.LESSON_DATA = " + __import__("json").dumps(payload) + ";",
        encoding="utf-8")
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    assert new_lesson.move_slide(str(tmp_path), 0, 2) == 5
    _, p2 = new_lesson.load_lesson(str(tmp_path))
    assert p2["slides"][2]["title"] == "S0"
    pos = new_lesson.add_slide(str(tmp_path), "Nuova", "narr")
    assert isinstance(pos, int)
    n = new_lesson.delete_slide(str(tmp_path), 0)
    assert n == 5


# ==================================================================
# Nuove funzionalità: classifica (drag & drop), voci alternate, badge,
# mappa percorso, classifica di classe
# ==================================================================

def _mod_cl():
    m = _mod("C1")
    m["classificazione"] = {
        "istruzione": "Assegna ogni elemento.",
        "categorie": ["Animali", "Piante"],
        "elementi": [{"testo": "Gatto", "categoria": "Animali"},
                     {"testo": "Rosa", "categoria": "Piante"},
                     {"testo": "Delfino", "categoria": "Animali"},
                     {"testo": "Quercia", "categoria": "Piante"}],
    }
    return m


def test_classificazione_check_and_slides():
    from new_lesson import _check_struct, build_slides
    struct = {"titolo": "T", "sottotitolo": "s", "intro": "i",
              "outro": "o", "citazione": "c", "moduli": [_mod_cl(), _mod("B"), _mod("D")]}
    _check_struct(struct)
    cl = struct["moduli"][0]["classificazione"]
    assert cl and len(cl["categorie"]) == 2 and len(cl["elementi"]) == 4
    # categoria inesistente scartata
    struct["moduli"][0]["classificazione"]["elementi"][0]["categoria"] = "Minerali"
    _check_struct(struct)
    assert struct["moduli"][0]["classificazione"] is None
    # slide di classifica generata quando la rotazione la assegna (modulo 5, i=4):
    # servono almeno 5 moduli
    struct2 = {"titolo": "T", "sottotitolo": "s", "intro": "i",
               "outro": "o", "citazione": "c",
               "moduli": [_mod("A"), _mod("B"), _mod("C"), _mod("D"), _mod_cl(), _mod("E")]}
    _check_struct(struct2)
    slides = build_slides(struct2)
    cl_slides = [s for s in slides if any("classifica" in b for b in s["blocks"])]
    assert len(cl_slides) == 1 and "Trascina" in cl_slides[0]["title"]
    items = next(b for b in cl_slides[0]["blocks"] if "classifica" in b)["classifica"]["items"]
    assert all(0 <= it["cat"] < 2 for it in items)


def test_classifica_validation_and_handout(tmp_path):
    slides = [{"title": "Classifica", "audio": "./assets/audio/narration-01.mp3",
               "duration": 3.0, "caption": "./assets/captions/narration-01.vtt",
               "words": [[0, 1, "x"]],
               "blocks": [{"classifica": {"cats": ["A", "B"],
                                          "items": [{"t": "x1", "cat": 0},
                                                    {"t": "x2", "cat": 1},
                                                    {"t": "x3", "cat": 0},
                                                    {"t": "x4", "cat": 1}]}}]}]
    out = tmp_path / "L2_lesson"
    (out / "assets" / "audio").mkdir(parents=True)
    (out / "assets" / "captions").mkdir(parents=True)
    (out / "index.html").write_text("<html></html>", encoding="utf-8")
    (out / "lesson-data.js").write_text("window.LESSON_DATA = {};", encoding="utf-8")
    (out / "assets" / "audio" / "narration-01.mp3").write_bytes(b"\x00" * 2048)
    (out / "assets" / "captions" / "narration-01.vtt").write_text(
        "WEBVTT\n\n1\n00:00:00,000 --> 00:00:01,000\nx", encoding="utf-8")
    from common import validate_lesson
    ok, errs, stats = validate_lesson(out, slides)
    assert ok, errs
    assert stats["classifica"] == 1


def test_voci_alternate_config_and_choice():
    import new_lesson
    assert hasattr(new_lesson, "voice_for_text")
    v = new_lesson.voice_for_text("Qual è la capitale? Verifica le tue conoscenze.")
    assert v in (new_lesson.EDGE_VOICE, new_lesson.EDGE_VOICE_Q or new_lesson.EDGE_VOICE)
    # con voce domande configurata, il testo con "?" la usa
    old, oldq = new_lesson.EDGE_VOICE, new_lesson.EDGE_VOICE_Q
    try:
        new_lesson.EDGE_VOICE, new_lesson.EDGE_VOICE_Q = "V-Narr", "V-Dom"
        assert new_lesson.voice_for_text("Che cosa significa?") == "V-Dom"
        assert new_lesson.voice_for_text("Ora un gioco: giudica se è vero.") == "V-Dom"
        assert new_lesson.voice_for_text("Ripassiamo insieme il concetto.") == "V-Narr"
    finally:
        new_lesson.EDGE_VOICE, new_lesson.EDGE_VOICE_Q = old, oldq


def test_player_contains_new_features(tmp_path):
    from player_template import write_player
    out = tmp_path / "P_lesson"
    out.mkdir()
    write_player(out, "Test Nuove Funzioni")
    js = (out / "main.js").read_text(encoding="utf-8")
    html = (out / "index.html").read_text(encoding="utf-8")
    css = (out / "main.css").read_text(encoding="utf-8")
    # F1: invio classifica
    assert "LESSON_DIR" in html and "/api/classifica" in js
    # F3: attività classifica
    assert "blockClassify" in js and "clzone" in css
    # F4: mappa del percorso
    assert 'id="map"' in html and "paintMap" in js
    # F5: badge
    assert "BADGES" in js and "btnBadges" in html and "badge-toast" in css
    # hook di avanzamento disponibile per adattatori esterni
    assert "reportProgress" in js




def test_player_classroom_modes_and_service_worker(tmp_path):
    from player_template import write_player
    out = tmp_path / "Classroom_lesson"
    out.mkdir()
    write_player(out, "Modalità classe")
    html = (out / "index.html").read_text(encoding="utf-8")
    js = (out / "main.js").read_text(encoding="utf-8")
    css = (out / "main.css").read_text(encoding="utf-8")
    sw = (out / "sw.js").read_text(encoding="utf-8")
    assert 'id="btnModeTeacher"' not in html and 'id="btnModeExam"' in html
    assert 'id="examTimer"' in html and 'id="examBanner"' in html
    assert "btnModeTeacher" not in js and "data-mode=\"teacher\"" not in css
    # L'ordine delle opzioni resta stabile fra Indietro e Avanti, così lo
    # studente risponde al contenuto e non alla posizione. La chiave di
    # memorizzazione porta un suffisso quando il set mostrato cambia (BES/DSA
    # ne mostra meno): senza, l'ordine salvato per 4 opzioni sarebbe
    # riutilizzato su 3 e l'opzione mostrata non corrisponderebbe a quella
    # salvata.
    assert "stableOrder(idx, bidx, 'quiz' + (BES ? 'B' : ''), shown.length," in js
    assert "function shuffleOpts(opts)" in js
    assert "function stableOrder(idx, bidx, kind, n, factory)" in js
    assert "String.fromCharCode(65 + pos)" in js
    assert "setLessonMode" in js and "startExam" in js and "goExam" in js
    assert "navigator.serviceWorker.register('./sw.js')" in js
    # Il nome della cache NON può essere una costante fissa: altrimenti il
    # service worker continua a servire agli alunni una lezione vecchia e
    # l'activate non cancella mai nulla. Deve portare l'impronta del player.
    assert "const C=" in sw and "caches.open" in sw
    assert "lesson-v4" not in sw
    assert "ignoreSearch:true" in sw          # i ?v=<hash> matchano la precache
    assert "ks.filter(k=>k!==C)" in sw        # activate elimina le cache vecchie
    assert "fetch(req).then" in sw            # strategia network-first
    assert "self.skipWaiting()" in sw

def test_player_lesson_dir_is_valid_js(tmp_path):
    """Regressione: lo script inline window.LESSON_DIR non deve contenere
    entità HTML (&quot;/&amp;): dentro <script> le entità NON vengono
    decodificate e il browser solleva `Uncaught SyntaxError:
    Unexpected token '&'` (visto come about:srcdoc)."""
    from player_template import write_player
    out = tmp_path / "La_Repubblica_14_Settembre_2026_lesson"
    out.mkdir()
    write_player(out, "Prova & Titolo con 'apostrofo'")
    html = (out / "index.html").read_text(encoding="utf-8")
    line = next(l for l in html.splitlines() if "LESSON_DIR" in l)
    assert "&quot;" not in line and "&amp;" not in line and "&#x27;" not in line
    assert line.strip() == \
        '<script>window.LESSON_DIR = "La_Repubblica_14_Settembre_2026_lesson";</script>'


def test_classifica_add_and_view(tmp_path, monkeypatch):
    import panel
    monkeypatch.setattr(panel, "CLASSIFICA_FILE", tmp_path / "classifica.json")
    # La classifica e' la MEDIA di errori e tempo: Alice è più lenta ma sbaglia
    # meno di Bob, e la media la mette davanti (indice più alto).
    panel._classifica_add("X_lesson", "Alice", 8, 10, True, 6, 1)
    panel._classifica_add("X_lesson", "Bob", 9, 10, True, 3, 5)
    panel._classifica_add("X_lesson", "Alice", 9, 10, True, 5, 1)   # migliora il suo best
    panel._classifica_add("Y_lesson", "Carl", 2, 10, False, 9, 7)
    view = panel._classifica_view()
    by = {c["lesson"]: c["rows"] for c in view["classifiche"]}
    assert [r["studente"] for r in by["X_lesson"]] == ["Alice", "Bob"]
    assert by["X_lesson"][0]["punti"] == 9
    # l'indice tiene conto di entrambi i dati: 0 errori e 2 minuti -> 100
    assert by["X_lesson"][0]["indice"] > by["X_lesson"][1]["indice"]
    assert by["X_lesson"][0]["errori"] == 1
    assert len(by["Y_lesson"]) == 1


def test_classifica_ordina_per_media_errori_e_tempo(tmp_path, monkeypatch):
    """La regola: indice = 100 - media(penalità errori, penalità tempo)."""
    import panel
    from tools import classifica_score
    monkeypatch.setattr(panel, "CLASSIFICA_FILE", tmp_path / "classifica.json")
    # Carl: 0 errori in 10 minuti -> penalita' 0 e 100, indice 50.
    # Alice: 4 errori in 2 minuti  -> penalita' 100 e 20, indice 40.
    # Vince Carl: l'assenza di errori non compensa da sola la lentezza, perche'
    # i due dati pesano uguale nella media.
    panel._classifica_add("Z_lesson", "Carl", 10, 10, True, 10, 0)
    panel._classifica_add("Z_lesson", "Alice", 6, 10, True, 2, 4)
    rows = {c["lesson"]: c["rows"] for c in panel._classifica_view()["classifiche"]}["Z_lesson"]
    assert [(r["studente"], r["indice"]) for r in rows] == [("Carl", 50.0), ("Alice", 40.0)]
    # chi non ha finito il percorso resta in coda, e non fa da termometro
    panel._classifica_add("Z_lesson", "Dan", 1, 10, False, 1, 0)
    rows = {c["lesson"]: c["rows"] for c in panel._classifica_view()["classifiche"]}["Z_lesson"]
    assert rows[-1]["studente"] == "Dan"
    assert classifica_score.indice(0, 0, 0, 0) == 100.0
    # la classe vuota non deve dividere per zero
    assert classifica_score.punta([]) == []


def test_classifica_reset_and_history_clear(tmp_path, monkeypatch):
    import panel
    monkeypatch.setattr(panel, "CLASSIFICA_FILE", tmp_path / "classifica.json")
    monkeypatch.setattr(panel, "HISTORY_FILE", tmp_path / "job_history.json")
    panel._classifica_add("X_lesson", "Alice", 8, 10, True, 4)
    panel._history_append("generazione", "prova", True, 1.5)
    assert panel._classifica_view()["classifiche"]
    panel._classifica_reset()
    panel._history_clear()
    assert panel._classifica_view() == {"classifiche": []}
    import json as _j
    assert _j.loads((tmp_path / "job_history.json").read_text(encoding="utf-8")) == []
