# -*- coding: utf-8 -*-
"""Test delle migliorie del pannello: lezioni, impostazioni, QR e rete."""
import json
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

    copy = lesson_admin.duplicate_lesson(tmp_path, renamed)
    assert copy != renamed
    assert (tmp_path / copy / "assets" / "audio.mp3").exists()

    archived = lesson_admin.archive_lesson(tmp_path, copy)
    assert archived in lesson_admin.list_archived(tmp_path)
    assert not (tmp_path / copy).exists()
    restored = lesson_admin.restore_lesson(tmp_path, archived)
    assert (tmp_path / restored / "index.html").exists()


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


def test_panel_qr_is_clickable_and_fullscreen():
    import panel
    html = panel.PANEL_HTML
    assert 'id="qrOv"' in html and 'id="qrOvImg"' in html
    assert "$('#lanQr').onclick = openQrFullscreen;" in html
    assert "e.key === 'Escape'" in html
    assert 'width:min(88vmin,760px)' in html


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
