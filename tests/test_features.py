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
    (lsn / "index.html").write_text("<html>lezione</html>", encoding="utf-8")
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
    assert "imsmanifest.xml" in zipfile.ZipFile(z).namelist()
    h = export_handout(lsn, tmp_path / "disp.html")
    assert h.exists() and "Dispensa" in h.read_text(encoding="utf-8")
