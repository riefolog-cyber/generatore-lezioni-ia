# -*- coding: utf-8 -*-
"""Test delle migliorie del pannello: lezioni, impostazioni, QR e rete."""
import json
import re
import sys
import time
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tools"))

from tools import lesson_admin
from tools.qr import qr_png_bytes, qr_svg


def _make_lesson(root, name="Prova_lesson", title="Lezione prova"):
    lesson = root / name
    (lesson / "assets").mkdir(parents=True)
    (lesson / "index.html").write_text(
        f'<script>window.LESSON_DIR = "{name}";</script>', encoding="utf-8")
    (lesson / "lesson-data.js").write_text(
        'window.LESSON_DATA = {"titolo":"' + title +
        '","slides":[{"duration":60},{"duration":30}]};', encoding="utf-8")
    (lesson / "assets" / "audio.mp3").write_bytes(b"x" * 100)
    return lesson


def test_lesson_rename_duplicate_archive_restore(tmp_path):
    old = _make_lesson(tmp_path)
    renamed = lesson_admin.rename_lesson(tmp_path, old.name, "Nuovo nome")
    assert renamed == "Nuovo_nome_lesson"
    index = (tmp_path / renamed / "index.html").read_text(encoding="utf-8")
    assert 'window.LESSON_DIR = "Nuovo_nome_lesson"' in index
    # la rinomina cambia anche il titolo mostrato dal pannello: altrimenti
    # cartella e pannello mostravano due nomi diversi
    assert lesson_admin.lesson_info(tmp_path, renamed)["title"] == "Nuovo nome"

    copy = lesson_admin.duplicate_lesson(tmp_path, renamed)
    assert copy != renamed
    assert (tmp_path / copy / "assets" / "audio.mp3").exists()

    archived = lesson_admin.archive_lesson(tmp_path, copy)
    assert archived in lesson_admin.list_archived(tmp_path)
    assert not (tmp_path / copy).exists()
    restored = lesson_admin.restore_lesson(tmp_path, archived)
    assert (tmp_path / restored / "index.html").exists()


def test_rename_updates_title_everywhere(tmp_path):
    lesson = _make_lesson(tmp_path)
    (lesson / "index.html").write_text(
        '<html><head><title>Vecchio</title></head><body>'
        '<h1 id="ttl">Vecchio</h1>'
        f'<script>window.LESSON_DIR = "{lesson.name}";</script></body></html>',
        encoding="utf-8")
    (lesson / "manifest.json").write_text('{"name": "Vecchio", "short_name": "Vecchio"}',
                                         encoding="utf-8")
    nuovo = "Educare ai sentimenti: Gisèle Pelicot"
    lesson_admin.rename_lesson(tmp_path, lesson.name, nuovo)
    cartella = [p for p in tmp_path.glob("*_lesson")][0]
    # accenti conservati nel nome della cartella
    assert "Gisèle" in cartella.name
    data = (cartella / "lesson-data.js").read_text(encoding="utf-8")
    assert f'"titolo":"{nuovo}"' in data or f'"titolo": "{nuovo}"' in data
    index = (cartella / "index.html").read_text(encoding="utf-8")
    assert f"<title>{nuovo}</title>" in index
    assert f'<h1 id="ttl">{nuovo}</h1>' in index
    mf = json.loads((cartella / "manifest.json").read_text(encoding="utf-8"))
    assert mf["name"] == nuovo


def test_lesson_names_with_accents_are_managable(tmp_path):
    """Una cartella con "à" o "è" deve restare rinominabile/archiviabile."""
    nome = "Saman_Abbas_e_il_prezzo_della_libertà_lesson"
    assert lesson_admin._valid_dir_name(nome)
    _make_lesson(tmp_path, name=nome)
    rinominata = lesson_admin.rename_lesson(tmp_path, nome, "Altra lezione")
    assert rinominata.endswith("_lesson")
    lesson_admin.archive_lesson(tmp_path, rinominata)
    archiviati = [p.name for p in (tmp_path / "archivio_lezioni").glob("*_lesson")]
    assert len(archiviati) == 1


def test_lesson_admin_rejects_traversal(tmp_path):
    _make_lesson(tmp_path)
    for bad in ("../Prova_lesson", "altro\\Prova_lesson", "plain"):
        try:
            lesson_admin.safe_lesson(tmp_path, bad)
        except ValueError:
            continue
        raise AssertionError("nome non sicuro accettato: " + bad)


def test_lesson_info_size_and_duration(tmp_path):
    _make_lesson(tmp_path)
    info = lesson_admin.lesson_info(tmp_path, "Prova_lesson")
    assert info["duration"] == 90
    assert info["size"] > 100
    assert info["title"] == "Lezione prova"


def test_qr_assets_are_well_formed():
    png = qr_png_bytes("http://192.168.0.2:8341/", scale=4, border=4)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in png
    svg = qr_svg("http://192.168.0.2:8341/")
    assert svg.startswith("<svg") and "</svg>" in svg


def test_qr_generator_polynomial_matches_spec():
    """Il polinomio generatore RS deve essere in ordine decrescente di grado.

    Se torna al contrario l'ECC e' sbagliato e i telefoni non leggono il QR:
    vettori ufficiali (thonky) per 7 e 10 codewords di correzione.
    """
    from tools import qr
    assert qr._rs_gen(7) == [1, 127, 122, 154, 164, 11, 68, 117]
    assert qr._rs_gen(10) == [1, 216, 194, 159, 111, 199, 94, 95, 113, 157, 193]


def _qr_syndromes(cws, version):
    """Sintiomi RS di ogni blocco: devono essere tutti zero."""
    from tools import qr
    groups, ec_len = qr._EC[version]
    sizes = [dc for n, dc in groups for _ in range(n)]
    blocks = [[] for _ in sizes]
    idx = 0
    for i in range(max(sizes)):
        for b in range(len(sizes)):
            if i < sizes[b]:
                blocks[b].append(cws[idx])
                idx += 1
    for i in range(ec_len):
        for b in range(len(blocks)):
            blocks[b].append(cws[idx])
            idx += 1
    bad = 0
    for full in blocks:
        for i in range(ec_len):
            s = 0
            for c in full:
                s = qr._gf_mul(s, qr._EXP[i]) ^ c
            bad += 1 if s else 0
    return bad


def test_qr_error_correction_is_valid_for_every_version():
    """Ogni versione usata dal pannello deve produrre un QR decodificabile."""
    from tools import qr
    urls = [
        "http://192.168.0.2:8341/",
        "http://192.168.0.2:8341/La_Repubblica_14_Settembre_2026_lesson/index.html?attivita=2",
        "A", "x" * 60, "y" * 120, "z" * 200,
    ]
    for text in urls:
        v = qr._pick_version(text)
        cws = qr._encode_data(text, v)
        assert _qr_syndromes(cws, v) == 0, f"ECC non valida per v{v}"


def test_panel_qr_is_clickable_and_fullscreen():
    import panel
    html = panel.PANEL_HTML
    assert 'id="qrOv"' in html and 'id="qrOvImg"' in html
    assert "$('#lanQr').onclick = openQrFullscreen;" in html
    assert "e.key === 'Escape'" in html
    assert 'width:min(88vmin,760px)' in html
    # QR grande + cache-buster (niente QR vecchi se cambia l'IP)
    assert 'scale=8&url=' in html
    # avviso rete: spiega quale IP usare quando ce ne sono due
    assert 'id="lanWarn"' in html
    # NB: la rotta /api/lesson_activities e' stata rimossa: il pannello sceglie una lezione intera
    assert 'id="focusQr"' in html and 'id="focusUrl"' in html
    # tasto dedicato per scegliere la lezione da mostrare + finestra unica
    assert 'id="btnFocusPick"' in html and 'id="focusOv"' in html
    assert "$('#btnFocusPick').onclick = openFocusPicker;" in html
    assert 'id="focusNow"' in html
    # una sola finestra: niente menu affiancati, niente scelta per singola attività
    assert 'id="focusList"' in html and 'id="focusSearch"' in html
    assert 'id="focusLesson"' not in html and 'id="focusAct"' not in html
    assert 'all_activities' not in html
    # la scelta chiude la finestra, imposta la lezione condivisa e c'è il tasto QR
    assert "closeFocusPicker();   // scelta fatta: la finestra si chiude da sola" in html
    assert "action: 'share'" in html
    assert 'id="btnFocusQr"' in html


def test_panel_lan_prefers_configured_ip_and_warns_on_dual_net(monkeypatch):
    import panel

    def _no_cache():
        # l'IP LAN è in cache 30 s (prima ogni refresh del pannello faceva
        # 2 getaddrinfo + 2 socket): il test cambia rete, quindi la svuota
        panel._LAN_CACHE.update(at=0.0, ip=None, warn=None)

    _no_cache()
    monkeypatch.setattr(panel, "_local_ips", lambda: {"192.168.0.2", "10.1.1.103"})
    monkeypatch.setattr(panel, "_default_route_ip", lambda: "10.1.1.103")
    assert panel.lan_ip() == "192.168.0.2"
    assert "10.1.1.103" in (panel.lan_warning() or "")
    # entro i 30 s il risultato è riusato dalla cache
    assert panel.lan_ip() == "192.168.0.2"
    _no_cache()
    monkeypatch.setattr(panel, "_local_ips", lambda: {"10.1.1.103"})
    assert panel.lan_ip() == "10.1.1.103"
    assert "192.168.0.2" in (panel.lan_warning() or "")


def test_player_focus_mode_single_activity():
    from tools import player_template
    src = player_template._js()
    assert "attivita=" in src and "focusAct" in src
    # il percorso parte dall'attività e si prosegue fino in fondo
    assert "FOCUS_STOP" in src
    assert "Math.max(FOCUS_STOP, Math.min(LAST, i))" in src
    # niente errori a runtime: il parametro va letto prima di restorePos
    # (prima era usato FOCUS li dentro, ma FOCUS nasce dopo -> ReferenceError)
    assert src.index("let FOCUS_SLIDE") < src.index("(function restorePos()")
    assert "if (FOCUS_SLIDE >= 0) return;" in src
    # il wrapper aggancia render (che esiste), non una paint() inesistente
    assert "const _origRender = render;" in src
    assert "_origPaint" not in src


def test_lesson_action_share_sets_shared_lesson(tmp_path):
    """La lezione scelta nella finestra diventa la lezione "in classe"."""
    import panel
    from tools import shared_lesson
    lesson = tmp_path / "Prova_lesson"
    (lesson / "assets").mkdir(parents=True)
    (lesson / "index.html").write_text("x", encoding="utf-8")
    captured = {}

    class FakeHandler(panel.PanelHandler):
        def __init__(self):
            pass

        def _json(self, obj, code=200):
            captured.update(obj)

    orig_base = panel.BASE
    panel.BASE = tmp_path
    try:
        FakeHandler()._lesson_action("share", {"lesson": "Prova_lesson"})
    finally:
        panel.BASE = orig_base
    assert captured.get("ok") is True
    assert captured.get("name") == "Prova_lesson"
    assert shared_lesson.get_shared(tmp_path) == "Prova_lesson"
    # la lezione condivisa è quella che gli alunni aprono via LAN
    assert shared_lesson.get_shared(tmp_path) is not None


def test_netdiag_returns_panel_fields():
    from tools.netdiag import diagnose
    d = diagnose(8341)
    for key in ("url_locale", "url_lan", "port", "disk_free_gb", "deps", "ok"):
        assert key in d


def test_class_repository_migrates_json_and_keeps_compatibility(tmp_path):
    import json
    from tools.class_repository import add_result, db_path_for, list_results

    json_path = tmp_path / "classifica.json"
    json_path.write_text(json.dumps([
        {"t": "2026-01-01 10:00", "lesson": "Prova_lesson",
         "studente": "Alice", "punti": 7, "totale": 10, "pct": 70,
         "completata": True, "tempo_min": 4}
    ], ensure_ascii=False), encoding="utf-8")

    rows = list_results(json_path)
    assert rows[0]["studente"] == "Alice"
    assert db_path_for(json_path).exists()

    add_result(json_path, "Prova_lesson", "Bob", 9, 10, True, 3)
    rows = list_results(json_path)
    assert {r["studente"] for r in rows} == {"Alice", "Bob"}
    assert json.loads(json_path.read_text(encoding="utf-8"))


def test_panel_blocks_duplicate_generation_jobs():
    import panel
    panel.JOB.update(running=True, kind="generazione", source="Prova.m4a")
    panel.QUEUE.clear()
    try:
        assert panel._material_job_pending("Prova.m4a")
        assert not panel._material_job_pending("Altro.m4a")
        panel.JOB["running"] = False
        panel.QUEUE.append((lambda: None, "generazione", "Coda.m4a"))
        assert panel._material_job_pending("Coda.m4a")
    finally:
        panel.JOB["running"] = False
        panel.JOB["source"] = None
        panel.QUEUE.clear()


def test_audio_transcription_log_shows_duration(tmp_path):
    from sources import _audio_duration, _format_duration
    assert _format_duration(543) == "9 min 03 s"
    assert _format_duration(3723) == "1:02:03"
    assert _audio_duration(tmp_path / "inesistente.mp3") is None


def test_panel_settings_validates_and_hides_pin(tmp_path):
    from tools.panel_settings import public_config, update_public
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_api_key": "segreto", "porta": 9000,
                                "pin_docente": "1234", "whisper_model": "small"}),
                    encoding="utf-8")
    public = public_config(path)
    assert public["porta"] == 9000 and public["whisper_model"] == "small"
    assert public["pin_configured"] is True and public["pin_docente"] == ""
    assert "llm_api_key" not in public
    updated = update_public({"max_upload_mb": 55, "whisper_model": "tiny"}, path)
    assert updated["max_upload_mb"] == 55 and updated["whisper_model"] == "tiny"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["llm_api_key"] == "segreto" and saved["pin_docente"] == "1234"
    with pytest.raises(ValueError):
        update_public({"porta": 10}, path)
    with pytest.raises(ValueError):
        update_public({"pin_docente": "abc"}, path)


def test_uploads_save_atomically_and_validate(tmp_path):
    from tools.uploads import UploadTooBig, save_upload
    ext = {".txt", ".m4a"}
    name, replaced, copied = save_upload(tmp_path, "../materiale.txt", b"uno",
                                        ext, 100)
    assert name == "materiale.txt" and not replaced and not copied
    assert (tmp_path / "materiale.txt").read_bytes() == b"uno"
    name, replaced, copied = save_upload(tmp_path, "materiale.txt", b"due",
                                        ext, 100)
    assert name == "materiale.txt" and replaced and not copied
    assert (tmp_path / "materiale.txt").read_bytes() == b"due"
    with pytest.raises(ValueError):
        save_upload(tmp_path, "vuoto.txt", b"", ext, 100)
    with pytest.raises(ValueError):
        save_upload(tmp_path, "malizioso.exe", b"x", ext, 100)
    with pytest.raises(UploadTooBig):
        save_upload(tmp_path, "grande.txt", b"x" * 20, ext, 10)
    assert not list(tmp_path.glob(".upload-*.tmp"))


def test_multipart_parser_and_validation():
    from tools.multipart import parse_multipart
    boundary = "ABC"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            "filename=\"documento.txt\"\r\n\r\ncontenuto\r\n"
            f"--{boundary}--\r\n").encode("utf-8")
    assert parse_multipart(body, f"multipart/form-data; boundary={boundary}") == [
        ("documento.txt", b"contenuto")]
    with pytest.raises(ValueError):
        parse_multipart(b"", "multipart/form-data")
    empty = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             "filename=\"vuoto.txt\"\r\n\r\n\r\n"
             f"--{boundary}--\r\n").encode()
    with pytest.raises(ValueError):
        parse_multipart(empty, f"multipart/form-data; boundary={boundary}")


def test_job_manager_runs_and_invalidates():
    from tools.jobs import JobManager
    events = []
    manager = JobManager(lambda *args: events.append("history"),
                         lambda: events.append("invalidate"),
                         lambda text: events.append("error"))
    assert manager.start(lambda: None, "generazione", "uno") == (True, False)
    for _ in range(100):
        if not manager.state["running"]:
            break
        time.sleep(0.01)
    assert manager.state["ok"] is True
    assert "invalidate" in events and manager.pending_source("uno") is False


def test_whisper_cache_progress_and_cancel(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sources
    audio = tmp_path / "memo.m4a"
    audio.write_bytes(b"audio-distinto")
    progress = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            return [SimpleNamespace(text="parola " * 30, end=15.0)], object()

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(sources.importlib.util, "find_spec",
                        lambda name: object() if name == "faster_whisper" else None)
    monkeypatch.setattr(sources, "_WHISPER_MODELS", {})
    monkeypatch.setattr(sources, "_WHISPER_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(sources, "_audio_duration", lambda path: 30.0)
    monkeypatch.setattr(sources, "_whisper_model_name", lambda: "base")
    sources.begin_transcription()
    sources.set_transcription_progress(lambda *args: progress.append(args))
    first = sources.extract_audio(audio)
    assert progress and progress[-1][3] is False
    progress.clear()
    second = sources.extract_audio(audio)
    assert progress[-1][3] is True
    assert first["sections"][0]["paras"] == second["sections"][0]["paras"]
    sources.cancel_transcription()
    assert sources.is_transcription_cancelled()
    sources.set_transcription_progress(None)


def test_class_repository_pin_is_optional_but_checked():
    from tools.class_repository import authorized
    assert authorized("", None)
    assert authorized(None, None)
    assert not authorized("1234", None)
    assert not authorized("1234", "wrong")
    assert authorized("1234", "1234")


def test_sources_support_pptx_epub_and_audio_dispatch(tmp_path):
    import zipfile
    from sources import extract_epub, extract_pptx, extract_source

    pptx = tmp_path / "Lezione.pptx"
    with zipfile.ZipFile(pptx, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", """<p:sld xmlns:p="p" xmlns:a="a">
          <a:t>Il sistema solare</a:t><a:t> contiene pianeti e stelle.</a:t></p:sld>""")
    assert "sistema solare" in extract_pptx(pptx)["sections"][0]["paras"][0].lower()
    assert extract_source(str(pptx))["title"]

    epub = tmp_path / "Libro.epub"
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("OEBPS/capitolo1.xhtml", "<html><body><h1>Storia</h1><p>Il primo capitolo.</p></body></html>")
    assert "capitolo" in extract_epub(epub)["sections"][0]["paras"][0].lower()
    assert extract_source(str(epub))["sections"]

    audio = tmp_path / "voce.wav"
    audio.write_bytes(b"RIFF----WAVE")
    import importlib.util
    if not any(importlib.util.find_spec(x) for x in ("faster_whisper", "whisper")):
        try:
            extract_source(str(audio))
        except ValueError as e:
            assert "Whisper" in str(e)
        else:
            raise AssertionError("audio senza libreria non deve fallire silenziosamente")


def test_whisper_audio_transcription_reuses_model(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    import sources

    calls = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            calls.append("init")

        def transcribe(self, path, **kwargs):
            calls.append(path)
            segments = [SimpleNamespace(text=(
                "Questa lezione parla di storia e introduce eventi, personaggi "
                "e concetti importanti per comprendere meglio il periodo."), end=30.0)]
            return segments, object()

    fake = SimpleNamespace(WhisperModel=FakeModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(sources.importlib.util, "find_spec",
                        lambda name: object() if name == "faster_whisper" else None)
    monkeypatch.setattr(sources, "_WHISPER_MODEL", None)
    monkeypatch.setattr(sources, "_WHISPER_BACKEND", None)
    monkeypatch.setattr(sources, "_WHISPER_MODELS", {})
    monkeypatch.setattr(sources, "_WHISPER_CACHE_DIR", tmp_path / "cache")
    sources.begin_transcription()

    for name, content in (("uno.wav", b"RIFF----WAVE-UNO"), ("due.wav", b"RIFF----WAVE-DUE")):
        path = tmp_path / name
        path.write_bytes(content)
        result = sources.extract_audio(path)
        assert "storia" in " ".join(result["sections"][0]["paras"])

    assert calls.count("init") == 1
    assert len(calls) == 3
    assert sources._WHISPER_MODEL is not None
    cached = sources.extract_audio(tmp_path / "uno.wav")
    assert "storia" in " ".join(cached["sections"][0]["paras"])
    assert len(calls) == 3  # seconda chiamata: cache, nessuna nuova trascrizione
    sources.set_transcription_progress(None)
