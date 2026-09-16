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


def test_glossary_slide_uses_glossario_block():
    struct = {"titolo": "T", "sottotitolo": "s", "intro": "i",
              "outro": "o", "citazione": "c",
              "moduli": [_mod(f"M{i}") for i in range(4)]}
    slides = build_slides(struct)
    glo = next(s for s in slides if s["title"].startswith("Glossario"))
    blk = next(b for b in glo["blocks"] if "glossario" in b)
    groups = blk["glossario"]["groups"]
    assert glossary_term_count(groups) >= 3
    # link alle slide dei moduli assegnati
    assert any(g.get("slide") is not None for g in groups)


def test_glossario_validation_and_handout(tmp_path):
    import json as _j
    from common import validate_lesson
    slides = [{"title": "Glossario", "audio": "./assets/audio/narration-01.mp3",
               "duration": 3.0, "caption": "./assets/captions/narration-01.vtt",
               "words": [[0, 1, "x"]],
               "blocks": [{"glossario": {"groups": [
                   {"modulo": "M1", "slide": 2,
                    "terms": [{"t": "Alpha", "d": "prima"},
                              {"t": "Beta", "d": "seconda"},
                              {"t": "Gamma", "d": "terza"}]}]}}]}]
    out = tmp_path / "L_lesson"
    (out / "assets" / "audio").mkdir(parents=True)
    (out / "assets" / "captions").mkdir(parents=True)
    (out / "index.html").write_text("<html></html>", encoding="utf-8")
    (out / "lesson-data.js").write_text("window.LESSON_DATA = {};", encoding="utf-8")
    (out / "assets" / "audio" / "narration-01.mp3").write_bytes(b"\x00" * 2048)
    (out / "assets" / "captions" / "narration-01.vtt").write_text(
        "WEBVTT\n\n1\n00:00:00,000 --> 00:00:01,000\nx", encoding="utf-8")
    ok, errs, stats = validate_lesson(out, slides)
    assert ok, errs
    assert stats["glossario"] == 3
    from export_handout import export_handout
    (out / "lesson-data.js").write_text(
        "window.LESSON_DATA = " + _j.dumps(
            {"titolo": "L", "slides": [
                {"title": "Glossario", "narration": "g",
                 "blocks": [{"glossario": {"groups": [
                     {"modulo": "M1",
                      "terms": [{"t": "Alpha", "d": "prima"}]}]}}]}]}),
        encoding="utf-8")
    h = export_handout(out, tmp_path / "disp.html")
    txt = h.read_text(encoding="utf-8")
    assert "Alpha" in txt and "Glossario" in txt


def test_final_exam_limit():
    mods = [_mod(f"M{i}") for i in range(7)]
    exam = build_final_exam(mods)
    assert 1 <= len(exam) <= 5


def test_build_slides_has_glossario_esame():
    struct = {"titolo": "T", "sottotitolo": "s", "intro": "i",
              "outro": "o", "citazione": "c",
              "moduli": [_mod(f"M{i}") for i in range(4)]}
    slides = build_slides(struct)
    titles = [s["title"] for s in slides]
    assert any(t.startswith("Glossario") for t in titles)
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


def test_scorm_handout(tmp_path):
    import json as _j
    lsn = tmp_path / "Prova_lesson"
    lsn.mkdir()
    (lsn / "index.html").write_text("<html><head><title>t</title></head><body>lezione</body></html>", encoding="utf-8")
    (lsn / "lesson-data.js").write_text(
        "window.LESSON_DATA = " + _j.dumps(
            {"titolo": "Prova", "profilo": {"durata": "standard"},
             "slides": [{"title": "A", "narration": "ciao",
                         "blocks": [{"quiz": {"q": "D?",
                                              "opts": [{"t": "x", "ok": True}]}}]}]}),
        encoding="utf-8")
    from export_scorm import export_scorm
    from export_handout import export_handout
    z = export_scorm(lsn, tmp_path / "p_scorm.zip")
    assert z.exists()
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        assert "imsmanifest.xml" in names
        assert "scorm-adapter.js" in names
        # manifest XML ben formato, con ogni file del pacchetto elencato
        root = ET.fromstring(zf.read("imsmanifest.xml").decode("utf-8"))
        assert root.tag.endswith("manifest")
        declared = {el.get("href") for el in root.iter() if el.tag.endswith("file")}
        assert "index.html" in declared and "scorm-adapter.js" in declared
        assert {n for n in names if n != "imsmanifest.xml"} <= declared
        # adapter iniettato in index.html DENTRO lo zip, non come doppio tag
        idx = zf.read("index.html").decode("utf-8")
        assert idx.count("scorm-adapter.js") == 1
        assert "LMSInitialize" in zf.read("scorm-adapter.js").decode("utf-8")
    # l'export è idempotente e la cartella originale NON viene toccata
    assert "scorm-adapter" not in (lsn / "index.html").read_text(encoding="utf-8")
    assert export_scorm(lsn, tmp_path / "p_scorm2.zip").exists()
    assert "scorm-adapter" not in (lsn / "index.html").read_text(encoding="utf-8")
    h = export_handout(lsn, tmp_path / "disp.html")
    assert h.exists() and "Dispensa" in h.read_text(encoding="utf-8")


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
    import json as _j
    (out / "lesson-data.js").write_text(
        "window.LESSON_DATA = " + _j.dumps(
            {"titolo": "L2", "slides": [{"title": "Classifica", "narration": "n",
              "blocks": [{"classifica": {"cats": ["A", "B"],
                                        "items": [{"t": "x", "cat": 1}]}}]}]}),
        encoding="utf-8")
    from export_handout import export_handout
    txt = export_handout(out, tmp_path / "disp2.html").read_text(encoding="utf-8")
    assert "A:" in txt and "B:" in txt


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
    # hook SCORM ancora presente
    assert "reportProgress" in js


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
    panel._classifica_add("X_lesson", "Alice", 8, 10, True, 4)
    panel._classifica_add("X_lesson", "Bob", 6, 10, True, 3)
    panel._classifica_add("X_lesson", "Alice", 9, 10, True, 5)   # migliora il suo best
    panel._classifica_add("Y_lesson", "Carl", 2, 10, False, 9)
    view = panel._classifica_view()
    by = {c["lesson"]: c["rows"] for c in view["classifiche"]}
    assert [r["studente"] for r in by["X_lesson"]] == ["Alice", "Bob"]
    assert by["X_lesson"][0]["punti"] == 9
    assert len(by["Y_lesson"]) == 1


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
