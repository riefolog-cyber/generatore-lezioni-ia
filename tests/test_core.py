# -*- coding: utf-8 -*-
"""Test unitari per le funzioni pure della pipeline (nessuna rete, nessun LLM).

Esegui:  python -m pytest tests/ -q
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

import new_lesson  # noqa: E402
from new_lesson import (_accetta_moduli, _check_struct, _norm_opts,  # noqa: E402
                        _regole_adattive, _tts_text, _weighted_words,
                        build_slides, fallback_structure, parse_json,
                        profilo_moduli, sanitize_stem)
from sources import (SUPPORTED_EXT, is_url, is_youtube,  # noqa: E402
                     extract_source, parse_html)


# ---------------------------------------------------------------- _tts_text
def test_tts_abbreviazioni():
    t = _tts_text("Vedi es. l'art. 5, pag. 3.")
    assert "esempio" in t and "articolo" in t and "pagina" in t


# ------------------------------------------------------- _accetta_moduli (profilo)
def test_accetta_moduli_profilo_breve():
    """Regressione: con profilo "breve" (3-4 moduli) una risposta da 3 moduli
    è CORRETTA e non deve essere scartata dal minimo globale (num_moduli_min).
    Prima del fix la build finiva nel fallback degradata (3 slide, 0 quiz)."""
    nmin, nmax = profilo_moduli({"durata": "breve"})
    assert (nmin, nmax) == (3, 4)
    assert _accetta_moduli(3, nmin, nmax) is True      # il caso delle 17:28
    assert _accetta_moduli(4, nmin, nmax) is True
    assert _accetta_moduli(5, nmin, nmax) is True      # tolleranza +1
    assert _accetta_moduli(2, nmin, nmax) is False     # troppo pochi
    assert _accetta_moduli(6, nmin, nmax) is False     # troppi
    # profilo standard: il range resta quello di config
    nmin_s, nmax_s = profilo_moduli(None)
    assert _accetta_moduli(nmin_s, nmin_s, nmax_s) is True
    assert _accetta_moduli(nmin_s - 1, nmin_s, nmax_s) is False


def test_accetta_moduli_valori_anomali():
    assert _accetta_moduli(None, 3, 4) is False
    assert _accetta_moduli(3, None, 4) is False


# ---------------------------------------------------------------- player ripresa
def test_player_chiave_ripresa_con_impronta():
    """Il main.js del player deve calcolare DATA_KEY con l'impronta del
    contenuto (n° slide + narrazioni): una lezione rigenerata non deve
    riapplicare la posizione salvata della versione precedente."""
    src = (BASE / "tools" / "player_template.py").read_text(encoding="utf-8")
    assert "_fingerprint" in src
    assert "slides.length + 's-'" in src



def test_tts_simboli():
    t = _tts_text("Sconto del 20% e prezzo €5 → ok")
    assert "percento" in t and "euro" in t and "porta a" in t


def test_tts_niente_spazi_doppi():
    t = _tts_text("Ciao  mondo  ,   fine")
    assert "  " not in t


# ---------------------------------------------------------------- parse_json
def test_parse_json_con_fence():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_con_testo_attorno():
    assert parse_json('Ecco: {"a": [1, 2]} fine') == {"a": [1, 2]}


# ------------------------------------------- LLM: streaming, reasoning, troncamento
def test_extract_api_body_accumula_i_delta_streaming():
    body = ('data: {"choices":[{"delta":{"content":"{\\"a\\": 1"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":", \\"b\\": 2}"}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"completion_tokens":7}}\n\n'
            'data: [DONE]\n')
    out = new_lesson._extract_api_body(body)
    assert new_lesson._risposta_testo(out) == '{"a": 1, "b": 2}'
    assert new_lesson._dettagli_risposta(out) == ("stop", 7)


def test_extract_api_body_json_con_coda_streaming():
    body = ('{"choices":[{"message":{"content":"{\\"ok\\":true}"},'
            '"finish_reason":"stop"}]}\n'
            'data: {"choices":[{"delta":{"content":"ignorami"}}]}\n')
    out = new_lesson._extract_api_body(body)
    assert new_lesson._risposta_testo(out) == '{"ok":true}'


def test_extract_api_body_vuoto_solleva():
    for body in ("", "   ", "data: [DONE]\n"):
        try:
            new_lesson._extract_api_body(body)
        except ValueError:
            continue
        raise AssertionError(f"atteso ValueError per body={body!r}")


def test_risposta_testo_usa_reasoning_se_content_e_vuoto():
    out = {"choices": [{"message": {"content": "", "reasoning": '{"moduli":[{"titolo":"A"}]}'}}]}
    assert new_lesson._risposta_testo(out).startswith('{"moduli"')


def test_ripara_json_troncato_tiene_i_moduli_completi():
    troncato = '{"titolo":"T","moduli":[{"titolo":"A","quiz":null},{"titolo":"B","te'
    obj = new_lesson._ripara_json(troncato)
    assert obj["titolo"] == "T"
    assert [m["titolo"] for m in obj["moduli"]] == ["A"]


def test_ripara_json_troncato_dentro_una_stringa():
    troncato = '{"moduli":[{"titolo":"A"},{"titolo":"B","testo":"frase interro'
    obj = new_lesson._ripara_json(troncato)
    assert [m["titolo"] for m in obj["moduli"]] == ["A"]


def test_ripara_json_lascia_intatto_un_json_valido():
    valido = '{"titolo":"T","moduli":[{"titolo":"A"}]}'
    assert new_lesson._ripara_json(valido)["moduli"] == [{"titolo": "A"}]


def test_rotta_morta_e_errore_trasporto_classificano_i_guasti():
    # rotta in cooldown/quota: inutile ritentarla
    assert new_lesson._rotta_morta("HTTP Error 503: Service Unavailable")
    assert new_lesson._rotta_morta("HTTP Error 404: The model `x` does not exist")
    assert new_lesson._rotta_morta("Rate limit exceeded")
    # errore di rete: ritentare senza response_format non cambia nulla
    assert new_lesson._errore_trasporto("timed out")
    assert new_lesson._errore_trasporto("<urlopen error [WinError 10061]>")
    # risposta scartata per contenuto: non è un problema di rotta
    assert not new_lesson._rotta_morta("moduli insufficienti: 1 < 6 richiesti")


# ---------------------------------------------------------------- sanitize_stem
def test_sanitize_stem():
    assert sanitize_stem("  02_Regolamento IA  ") == "Regolamento_IA"
    assert sanitize_stem("!!!") == "Lezione"


# ---------------------------------------------------------------- _norm_opts
def test_norm_opts_deduplica_case_insensitive():
    raw = [
        {"testo": "Roma", "corretta": True},
        {"testo": "roma", "corretta": False},       # duplicato -> scartato
        {"testo": "Milano", "corretta": False},
        {"testo": "Torino", "corretta": False},
    ]
    out = _norm_opts(raw)
    assert len(out) == 3
    assert sum(1 for o in out if o["corretta"]) == 1
    assert out[0]["testo"] == "Roma"


def test_norm_opts_nessuna_corretta_la_prima_diventa_giusta():
    out = _norm_opts([{"testo": "A"}, {"testo": "B"}, {"testo": "C"}])
    assert out[0]["corretta"] is True
    assert sum(1 for o in out if o["corretta"]) == 1


def test_norm_opts_conserva_chiavi_extra():
    out = _norm_opts([{"testo": "A", "conseguenza": "rischio"}])
    assert out[0]["conseguenza"] == "rischio"


# ---------------------------------------------------------------- _check_struct
def test_check_struct_quiz_con_duplicati():
    struct = {"titolo": "T", "moduli": [{
        "titolo": "M",
        "quiz": {"domanda": "Q?", "opzioni": [
            {"testo": "X", "corretta": True},
            {"testo": "x", "corretta": False},
            {"testo": "Y", "corretta": False},
            {"testo": "Z", "corretta": False},
        ]},
    }]}
    _check_struct(struct)
    q = struct["moduli"][0]["quiz"]
    assert q and len(q["opzioni"]) == 3


def test_check_struct_scenario_opzioni_ripulite():
    struct = {"titolo": "T", "moduli": [{
        "titolo": "M",
        "scenari": [{"situazione": "Caso", "opzioni": [
            {"testo": "A", "corretta": True, "conseguenza": "ok"},
            {"testo": "B", "corretta": False},
            {"testo": "a", "corretta": False},
        ]}],
    }]}
    _check_struct(struct)
    sc = struct["moduli"][0]["scenari"]
    assert sc and len(sc["opzioni"]) == 2


# ---------------------------------------------------------------- _regole_adattive
def test_regole_adattive_materiale_corto():
    regole, nmin, nmax = _regole_adattive(500)
    assert "MATERIALE SCARSO" in regole
    assert nmin <= 3 and nmax <= 4


def test_regole_adattive_materiale_lungo():
    regole, nmin, nmax = _regole_adattive(20000)
    assert nmin >= 4 and nmax >= 5


# ---------------------------------------------------------------- _weighted_words
def test_weighted_words_coprono_la_durata():
    words = _weighted_words("una frase di prova", 4.0)
    assert words and len(words) == 4
    assert abs(words[-1][1] - 4.0) < 0.01
    assert all(a <= b for a, b, _ in words)


# ---------------------------------------------------------------- fallback_structure
def test_fallback_structure_moduli_da_sezioni():
    ext = {"title": "Titolo", "sections": [
        {"heading": "Sezione 1", "paras": ["Testo uno.", "Testo due."]},
        {"heading": "Sezione 2", "paras": ["Testo tre."]},
    ]}
    struct = fallback_structure(ext)
    assert struct["titolo"] == "Titolo"
    assert len(struct["moduli"]) == 2
    assert struct["moduli"][0]["quiz"] is None


# ---------------------------------------------------------------- build_slides
def test_build_slides_struttura_minima():
    struct = {"titolo": "T", "sottotitolo": "S", "intro": "I", "outro": "O",
              "citazione": "C", "moduli": [{
                  "titolo": "M1", "testo": "Testo", "punti": ["p"],
                  "keywords": ["k"], "narrazione": "N",
                  "quiz_narrazione": "QN", "quiz": None,
                  "abbinamenti": [], "vero_falso": [], "sequenza": None,
                  "compila": [], "scenari": None, "errori": [], "flashcards": [],
              }]}
    slides = build_slides(struct)
    assert slides[0]["title"] == "Apertura"
    assert slides[-1]["title"] == "Conclusione"
    assert any("Modulo" in s["title"] for s in slides)
    assert all(s.get("narration") for s in slides)


# ---------------------------------------------------------------- cache LLM
def test_llm_cache_key_deterministic():
    ext = {"title": "Titolo", "sections": [{"heading": "H", "paras": ["Testo."]}]}
    assert new_lesson._llm_cache_key(ext) == new_lesson._llm_cache_key(ext)


def test_llm_cache_key_cambia_con_testo():
    a = new_lesson._llm_cache_key(
        {"title": "T", "sections": [{"heading": "H", "paras": ["Uno."]}]})
    b = new_lesson._llm_cache_key(
        {"title": "T", "sections": [{"heading": "H", "paras": ["Due."]}]})
    assert a != b


def test_llm_cache_put_get_roundtrip():
    import tempfile
    old = new_lesson.LLM_CACHE_DIR
    try:
        with tempfile.TemporaryDirectory() as td:
            new_lesson.LLM_CACHE_DIR = Path(td)
            ext = {"title": "T", "sections": [{"heading": "H", "paras": ["Testo."]}]}
            struct = {"titolo": "T", "moduli": []}
            key = new_lesson._llm_cache_key(ext)
            assert new_lesson._llm_cache_put(key, struct) is True
            assert new_lesson._llm_cache_get(key) == struct
            assert new_lesson._llm_cache_get("inesistente") is None
    finally:
        new_lesson.LLM_CACHE_DIR = old


# ---------------------------------------------------------------- export_single
def test_export_single_incorpora_css_js_audio():
    import tempfile
    from export_single import export_single
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "Lezione_Prova_lesson_test"
        (d / "assets" / "audio").mkdir(parents=True)
        (d / "assets" / "captions").mkdir(parents=True)
        (d / "main.css").write_text("body{}", encoding="utf-8")
        (d / "main.js").write_text("// js", encoding="utf-8")
        (d / "index.html").write_text(
            '<link rel="stylesheet" href="main.css?v=123">'
            '<script src="lesson-data.js?v=123"></script>'
            '<script src="main.js?v=123"></script>', encoding="utf-8")
        (d / "assets" / "audio" / "narration-01.mp3").write_bytes(b"\x00\x01\x02")
        (d / "lesson-data.js").write_text(
            'window.LESSON_DATA={"titolo":"T","slides":'
            '[{"audio":"./assets/audio/narration-01.mp3"}]};',
            encoding="utf-8")
        out = export_single(d, out_path=root / "Lezione_Prova_singola.html")
        html = out.read_text(encoding="utf-8")
        assert out.exists()
        # audio incorporato come data URI, niente riferimenti esterni rimasti
        assert "data:audio/mpeg;base64" in html
        assert 'href="main.css' not in html
        assert 'src="main.js' not in html
        assert 'src="lesson-data.js' not in html
        assert 'assets/audio/narration-01.mp3' not in html


# ---------------------------------------------------------------- sources
def test_supported_ext():
    assert ".docx" in SUPPORTED_EXT and ".pdf" in SUPPORTED_EXT
    assert ".txt" in SUPPORTED_EXT and ".md" in SUPPORTED_EXT


def test_is_url():
    assert is_url("https://example.com/")
    assert is_url("http://a.b/c")
    assert not is_url("file.docx")


def test_is_youtube():
    assert is_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube("https://youtu.be/dQw4w9WgXcQ")
    assert not is_youtube("https://example.com/video")


def test_parse_html_titolo_e_sezioni():
    html = ("<html><head><title>La mia pagina</title></head><body>"
            "<h1>Introduzione</h1><p>Primo paragrafo.</p>"
            "<p>Secondo paragrafo.</p></body></html>")
    out = parse_html(html)
    assert out["title"] == "La mia pagina"
    assert any("Introduzione" in s["heading"] for s in out["sections"])
    assert any("Primo paragrafo." in p for s in out["sections"] for p in s["paras"])


def test_extract_source_file_inesistente():
    try:
        extract_source("file_che_non_esiste_xyz.txt")
    except ValueError:
        return
    raise AssertionError("doveva sollevare ValueError per file inesistente")


if __name__ == "__main__":
    # esecuzione senza pytest: python tests/test_core.py
    import traceback
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except Exception:
                failed += 1
                print(f"  ✗ {name}")
                traceback.print_exc()
    print(f"\n{('TUTTI OK' if not failed else f'{failed} FALLITI')}")
    sys.exit(1 if failed else 0)