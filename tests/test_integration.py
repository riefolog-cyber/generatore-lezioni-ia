# -*- coding: utf-8 -*-
"""Test integrazione senza rete/LLM: fallback -> slide -> validazione + VTT.

Esegui:  python -m pytest tests/ -q
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

import new_lesson
from new_lesson import _flatten, build_slides, fallback_structure, write_vtt
from common import validate_lesson


def _ext_fake():
    return {"title": "Lezione prova", "sections": [
        {"heading": "Intro", "paras": ["Prima frase. Seconda frase."]},
        {"heading": "Sviluppo", "paras": ["Terza frase con contenuto."]},
    ]}


def test_pipeline_fallback_slides(tmp_path):
    ext = _ext_fake()
    struct = fallback_structure(ext)
    assert len(struct["moduli"]) == 2
    slides = build_slides(struct, draft=True)
    assert slides[0]["title"] == "Apertura"
    assert slides[-1]["title"] == "Conclusione"
    # Simula audio/VTT senza TTS reale
    out = tmp_path / "Lezione_lesson"
    (out / "assets" / "audio").mkdir(parents=True)
    (out / "assets" / "captions").mkdir(parents=True)
    (out / "index.html").write_text("x", encoding="utf-8")
    (out / "lesson-data.js").write_text("x", encoding="utf-8")
    for i, s in enumerate(slides):
        mp3 = out / "assets" / "audio" / f"narration-{i + 1:02d}.mp3"
        mp3.write_bytes(b"\x00" * 2048)
        s["audio"] = f"./assets/audio/narration-{i + 1:02d}.mp3"
        s["duration"] = 3.0
        s["caption"] = f"./assets/captions/narration-{i + 1:02d}.vtt"
        s["words"] = [[0.0, 1.5, "ciao"], [1.5, 3.0, "mondo"]]
    write_vtt(out, slides)
    ok, errs, stats = validate_lesson(out, slides)
    assert ok, errs
    assert stats["slide"] == len(slides)


def test_flatten_testa_coda():
    ext = {"title": "T", "sections": [{"heading": "H", "paras": ["x" * 20000]}]}
    t = _flatten(ext, limit=1000)
    assert "omissis" in t
    assert len(t) <= 1200


def test_validate_catch_timing_sforato(tmp_path):
    out = tmp_path / "L_lesson"
    (out / "assets" / "audio").mkdir(parents=True)
    (out / "assets" / "captions").mkdir(parents=True)
    (out / "index.html").write_text("x", encoding="utf-8")
    (out / "lesson-data.js").write_text("x", encoding="utf-8")
    (out / "assets" / "audio" / "narration-01.mp3").write_bytes(b"\x00" * 2048)
    (out / "assets" / "captions" / "narration-01.vtt").write_text("WEBVTT", encoding="utf-8")
    slides = [{"title": "S", "blocks": [], "audio": "./assets/audio/narration-01.mp3",
               "duration": 2.0, "caption": "./assets/captions/narration-01.vtt",
               "words": [[0.0, 99.0, "troppo-lungo"]]}]
    ok, errs, _ = validate_lesson(out, slides)
    assert not ok
    assert any("timing" in e for e in errs)


def test_profilo_moduli_e_istruzioni():
    assert new_lesson.profilo_moduli({"durata": "breve"}) == (3, 4)
    assert new_lesson.profilo_moduli({"durata": "approfondita"}) == (6, 8)
    std = new_lesson.profilo_moduli({"durata": "standard"})
    assert std[0] <= std[1]
    txt = new_lesson.profilo_istruzioni(
        {"durata": "breve", "livello": "avanzato", "obiettivo": "analisi"})
    assert "avanzato" in txt and "analisi" in txt


def test_profilo_cache_key_distinta():
    ext = {"title": "T", "sections": [{"heading": "H", "paras": ["x"]}]}
    assert new_lesson._llm_cache_key(ext) != new_lesson._llm_cache_key(
        ext, {"durata": "breve", "livello": "base", "obiettivo": "conoscenza"})


def test_normalize_profilo_fallback():
    from common import normalize_profilo
    assert normalize_profilo({}) == {"durata": "standard", "livello": "intermedio",
                                     "obiettivo": "comprensione"}
    assert normalize_profilo({"durata": "xxx"})["durata"] == "standard"


def test_validate_slide_edit():
    clean = new_lesson.validate_slide_edit(0, {
        "title": "Nuovo titolo",
        "narration": "Nuova narrazione.",
        "quiz": {"domanda": "Quanto fa?", "opzioni": [
            {"testo": "A", "corretta": True}, {"testo": "B"}]}})
    assert clean["title"] == "Nuovo titolo"
    assert len(clean["quiz"]["opzioni"]) == 2
    try:
        new_lesson.validate_slide_edit(0, {"quiz": {"domanda": "Q", "opzioni": [
            {"testo": "solo-una"}]}})
    except ValueError:
        return
    raise AssertionError("doveva fallire con 1 sola opzione")
