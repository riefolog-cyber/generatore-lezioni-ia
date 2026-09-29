# -*- coding: utf-8 -*-
"""Test della GUI tkinter (app.py), che non aveva alcuna copertura.

`AVVIA.bat gui` avvia proprio questa applicazione: se `app.py` si rompe, il
dottore lo scopre solo cliccando e non c'e' nessun test che lo dica prima.

Le funzioni pure e la costruzione dei comandi si testano SENZA aprire una
finestra (tkinter non serve: si sostituisce con un doppione finto). I test che
richiedono un Tk() vero saltano se non c'e' un display, cosi' la suite resta
utilizzabile su CI Linux headless.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))


def _tk_disp():
    """Importa app.py; salta se tkinter non e' disponibile."""
    pytest.importorskip("tkinter")
    import app
    return app


# ------------------------------------------------------------- funzioni pure
def test_docx_files_ordinati_e_solo_docx(tmp_path, monkeypatch):
    app = _tk_disp()
    for nome in ("b.docx", "a.docx", "nota.txt", "lezione_lesson"):
        (tmp_path / nome).write_text("x", encoding="utf-8")
    monkeypatch.setattr(app, "BASE", tmp_path)
    assert app.docx_files() == ["a.docx", "b.docx"]


def test_lesson_dirs_solo_cartelle_con_index(tmp_path, monkeypatch):
    """Una cartella *_lesson senza index.html non e' una lezione pronta."""
    app = _tk_disp()
    buona = tmp_path / "Prova_lesson"
    buona.mkdir()
    (buona / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "Rotta_lesson").mkdir()              # senza index.html
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(app, "BASE", tmp_path)
    assert app.lesson_dirs() == ["Prova_lesson"]


# ------------------------------------------------------------ costruzione comandi
class _Finto:
    """Doppione di Tk: registra gli `after` invece di eseguirli."""

    def __init__(self):
        self.pendenti = []

    def after(self, _ms, fn):
        self.pendenti.append(fn)
        return "after#1"

    def esegui(self):
        for fn in self.pendenti:
            fn()
        self.pendenti.clear()


def _app_finta(monkeypatch, msg=None):
    """Costruisce App.root senza creare finestre reali."""
    app = _tk_disp()
    root = _Finto()
    istanza = app.App.__new__(app.App)      # niente __init__: niente widget
    istanza.root = root
    istanza.running = False
    istanza.actions = {}
    istanza.out = type("Out", (), {"insert": lambda *a: None, "see": lambda *a: None})()
    if msg is not None:
        monkeypatch.setattr(app.messagebox, msg, lambda *a, **k: False)
    return app, istanza


def _lb(valore):
    """Listbox finta che restituisce sempre lo stesso elemento selezionato."""
    return type("LB", (), {"curselection": lambda s: (0,) if valore else (),
                           "get": lambda s, i: valore})()


def test_do_check_costruisce_il_comando_check_env(monkeypatch):
    """Il bottone 'Verifica ambiente' deve lanciare check_env.py."""
    app, istanza = _app_finta(monkeypatch)
    lanciati = []
    monkeypatch.setattr(istanza, "run", lambda cmd: lanciati.append(cmd))
    istanza.do_check()
    assert lanciati[0][1:] == ["check_env.py"]


def test_do_preview_senza_file_non_lancia_nulla(monkeypatch):
    """Nessuna selezione: avviso all'utente e nessun comando."""
    app, istanza = _app_finta(monkeypatch, "showinfo")
    lanciati = []
    monkeypatch.setattr(istanza, "run", lambda cmd: lanciati.append(cmd))
    istanza.lb_docx = _lb(None)
    istanza.do_preview()
    assert lanciati == []


def test_do_build_con_force_aggiunge_flag(monkeypatch):
    """Con --force selezionato il comando deve portare il flag."""
    app, istanza = _app_finta(monkeypatch)
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: True)
    lanciati = []
    monkeypatch.setattr(istanza, "run", lambda cmd: lanciati.append(cmd))
    istanza.lb_docx = _lb("materiale.docx")
    istanza.force = type("V", (), {"get": lambda s: True})()
    istanza.do_build()
    assert lanciati[0][1:3] == ["new_lesson.py", "build"]
    assert "--force" in lanciati[0]


def test_do_build_url_rifiuta_indirizzo_non_http(monkeypatch):
    """Senza http(s):// il comando non parte: niente link arbitrari."""
    app, istanza = _app_finta(monkeypatch, "showinfo")
    lanciati = []
    monkeypatch.setattr(istanza, "run", lambda cmd: lanciati.append(cmd))
    istanza.url_var = type("V", (), {"get": lambda s: "ftp://esempio.it"})()
    istanza.do_build_url()
    assert lanciati == []


def test_do_open_e_do_export_usa_la_lezione_selezionata(monkeypatch):
    """I due bottoni passano il nome della lezione selezionata."""
    app, istanza = _app_finta(monkeypatch, "showinfo")
    lanciati = []
    monkeypatch.setattr(istanza, "run", lambda cmd: lanciati.append(cmd))
    istanza.lb_lessons = _lb("Prova_lesson")
    istanza.do_open()
    istanza.do_export()
    assert lanciati[0][1:3] == ["start_lesson.py", "Prova_lesson"]
    assert lanciati[1][1:3] == ["tools/export_zip.py", "Prova_lesson"]


def test_run_ignora_un_secondo_comando_mentre_e_busy(monkeypatch):
    """tkinter non e' thread-safe: due job contemporanei si corromperebbero."""
    app, istanza = _app_finta(monkeypatch)
    istanza.running = True
    thread_avviati = []

    def _fake_thread(**kw):
        thread_avviati.append(kw)
        return type("T", (), {"start": lambda s: None})()

    monkeypatch.setattr(app.threading, "Thread", _fake_thread)
    istanza.run([sys.executable, "-c", "print(1)"])
    assert thread_avviati == [], "non deve partire un thread se gia' busy"


def test_set_busy_disabilita_e_riabilita_i_pulsanti(monkeypatch):
    """Mentre gira un comando i pulsanti devono essere disabilitati."""
    app, istanza = _app_finta(monkeypatch)
    stati = []
    istanza.actions = {"go": type("B", (), {"config": lambda s, state: stati.append(state)})()}
    istanza.set_busy(True)
    istanza.root.esegui()
    assert stati == ["disabled"]
    istanza.set_busy(False)
    istanza.root.esegui()
    assert stati[-1] == "normal"



# ----------------------------------------------------- test che richiedono Tk
@pytest.fixture
def root():
    """Tk() vero; salta se non c'e' un display (CI Linux headless)."""
    tk = pytest.importorskip("tkinter")
    try:
        r = tk.Tk()
    except Exception as e:  # nessun display
        pytest.skip(f"Tk non disponibile: {e}")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:
        pass


def test_gui_si_costruisce_senza_errori(root, monkeypatch):
    """L'intera interfaccia deve costruirsi: e' il test che copre AVVIA.bat gui."""
    app = _tk_disp()
    monkeypatch.setattr(app, "docx_files", list)
    monkeypatch.setattr(app, "lesson_dirs", list)
    istanza = app.App(root)
    root.update()                 # esegue gli after(0) pendenti
    testo = istanza.out.get("1.0", "end")
    assert "Pronto" in testo
    assert set(istanza.actions) == {"check", "preview", "build", "open",
                                    "export", "browse", "refresh"}
    root.update()
    assert not istanza.running, "la GUI non deve risultare occupata all'avvio"


def test_gui_lista_i_file_alla_costruzione(root, monkeypatch, tmp_path):
    """Le liste si popolano con i .docx e le lezioni presenti."""
    app = _tk_disp()
    (tmp_path / "materiale.docx").write_text("x", encoding="utf-8")
    lez = tmp_path / "Prova_lesson"
    lez.mkdir()
    (lez / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(app, "BASE", tmp_path)
    istanza = app.App(root)
    root.update()
    assert istanza.lb_docx.get(0) == "materiale.docx"
    assert istanza.lb_lessons.get(0) == "Prova_lesson"
