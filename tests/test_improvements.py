# -*- coding: utf-8 -*-
"""Test delle migliorie del pannello: lezioni, backup, QR e rete."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tools"))

from tools import backups, lesson_admin
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


def test_backup_copies_and_keeps(tmp_path):
    (tmp_path / "job_history.json").write_text("[]", encoding="utf-8")
    (tmp_path / "classifica.json").write_text("[]", encoding="utf-8")
    first = backups.create_backup(tmp_path, keep=1)
    assert first and (first / "classifica.json").exists()
    second = backups.create_backup(tmp_path, keep=1)
    assert second and second != first
    folders = [p for p in (tmp_path / "archivio_backup").iterdir() if p.is_dir()]
    assert len(folders) == 1 and folders[0] == second


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
                "e concetti importanti per comprendere meglio il periodo."))]
            return segments, object()

    fake = SimpleNamespace(WhisperModel=FakeModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(sources.importlib.util, "find_spec",
                        lambda name: object() if name == "faster_whisper" else None)
    monkeypatch.setattr(sources, "_WHISPER_MODEL", None)
    monkeypatch.setattr(sources, "_WHISPER_BACKEND", None)

    for name in ("uno.wav", "due.wav"):
        path = tmp_path / name
        path.write_bytes(b"RIFF----WAVE")
        result = sources.extract_audio(path)
        assert "storia" in " ".join(result["sections"][0]["paras"])

    assert calls.count("init") == 1
    assert len(calls) == 3
