# -*- coding: utf-8 -*-
"""Test dell'avvio automatico di 9router e del modello configurato.

Esegui:  python -m pytest tests/ -q

Nessun avvio reale: `_find_9router_cli`, `start_9router` e `llm_reachable`
sono sostituiti, quindi il test non richiede 9router installato e non apre
nessuna connessione.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import export_zip

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

import new_lesson  # noqa: E402
from common import DEFAULT_CONFIG, load_config  # noqa: E402


# ------------------------------------------------------------------ encoding
def test_import_new_lesson_non_sfonda_su_console_cp1252():
    """Regressione: `new_lesson` deve essere importabile da una console cp1252.

    Prima il reconfigure di UTF-8 stava solo in `main()`, quindi importare il
    modulo (come fa panel.py e come fanno i test) lasciava lo stdout su cp1252:
    il primo print con "✓" dentro ensure_llm() moriva con UnicodeEncodeError e
    l'avvio automatico di 9router partiva senza più confermare nulla.
    """
    import importlib

    try:
        sys.stdout.reconfigure(encoding="cp1252", errors="strict")
    except Exception:
        return  # niente reconfigure (es. stdout non e' un TextIOWrapper): non testabile

    try:
        mod = importlib.reload(new_lesson)
        assert mod.llm_reachable is not None
        # il punto esatto del crash: un print con caratteri non-ASCII
        print("✓ 9router avviato e raggiungibile.")
    finally:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ------------------------------------------------------- avvio automatico
def test_ensure_llm_non_avvia_il_router_se_è_gia_raggiungibile(monkeypatch):
    """Se il router risponde, ensure_llm() deve restituire True e non avviarlo."""
    partiti = []
    monkeypatch.setattr(new_lesson, "llm_reachable", lambda: True)
    monkeypatch.setattr(new_lesson, "start_9router",
                        lambda: partiti.append(1) or True)
    assert new_lesson.ensure_llm() is True
    assert partiti == [], "non si deve riavviare un router già attivo"


def test_ensure_llm_avvia_il_router_e_attende_che_sia_pronto(monkeypatch):
    """Router assente: deve lanciarlo e aspettare che diventi raggiungibile."""
    stato = {"n": 0}

    def _raggiungibile():
        stato["n"] += 1
        return stato["n"] >= 3      # pronto al terzo sondaggio

    monkeypatch.setattr(new_lesson, "llm_reachable", _raggiungibile)
    monkeypatch.setattr(new_lesson, "start_9router", lambda: True)
    monkeypatch.setattr(new_lesson.time, "sleep", lambda s: None)
    assert new_lesson.ensure_llm(timeout=30) is True


def test_ensure_llm_va_in_grande_senza_infinite_attese(monkeypatch):
    """Router che non parte: timeout rispettato e valore di ritorno False."""
    monkeypatch.setattr(new_lesson, "llm_reachable", lambda: False)
    monkeypatch.setattr(new_lesson, "start_9router", lambda: False)
    monkeypatch.setattr(new_lesson.time, "sleep", lambda s: None)
    assert new_lesson.ensure_llm(timeout=6) is False


def test_start_9router_usa_la_cli_trovata_senza_shell(monkeypatch):
    """La CLI va lanciata con shell=False.

    Su Windows con shell=True la lista finisce in `cmd.exe /c` e il percorso
    deriva da %APPDATA%: un valore con `&` o `^` diventerebbe esecuzione di
    comandi arbitrari. Il percorso passato come lista è la difesa.
    """
    catturati = {}

    class _Proc:
        pass

    def _popen(cmd, **kw):
        catturati["cmd"] = cmd
        catturati["kw"] = kw
        return _Proc()

    monkeypatch.setattr(new_lesson, "_find_9router_cli", lambda: ["node", "cli.js"])
    monkeypatch.setattr(new_lesson.subprocess, "Popen", _popen)
    assert new_lesson.start_9router() is True
    assert catturati["cmd"] == ["node", "cli.js", "-n", "--skip-update", "-t"]
    assert catturati["kw"].get("shell") is False


def test_start_9router_segnala_se_non_trova_la_cli(monkeypatch, capsys):
    """CLI assente: avviso all'utente, non un'eccezione."""
    monkeypatch.setattr(new_lesson, "_find_9router_cli", lambda: None)
    assert new_lesson.start_9router() is False
    assert "9router" in capsys.readouterr().out


# ------------------------------------------------------------------ cache LLM
def test_cache_prune_tiene_il_limite_e_cancella_le_piu_vecchie(monkeypatch, tmp_path):
    """Il prune deve (a) non cancellare nulla sotto il limite, (b) ridurre
    la cache al limite scegliendo le voci piu' vecchie per mtime."""
    monkeypatch.setattr(new_lesson, "LLM_CACHE_DIR", tmp_path)
    monkeypatch.setattr(new_lesson, "LLM_CACHE_MAX", 5)
    for i in range(5):
        f = tmp_path / f"{i}.json"
        f.write_text("{}", encoding="utf-8")
        # mtime crescente deciso, senza dormire sul clock del filesystem
        os.utime(f, (1_600_000_000 + i * 10, 1_600_000_000 + i * 10))
    new_lesson._llm_cache_prune()
    assert len(list(tmp_path.glob("*.json"))) == 5      # sotto il limite: intatto

    for i in range(5, 8):                                 # 3 file nuovi -> 8 totali
        f = tmp_path / f"{i}.json"
        f.write_text("{}", encoding="utf-8")
        os.utime(f, (1_600_000_000 + i * 10, 1_600_000_000 + i * 10))
    new_lesson._llm_cache_prune()
    rimasti = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(rimasti) == 5
    # devono restare i CINQUE piu' recenti, non tre arbitrari
    assert rimasti == ["3.json", "4.json", "5.json", "6.json", "7.json"]


def test_cache_prune_ignora_i_file_non_json(monkeypatch, tmp_path):
    """Un file di testo nella cartella cache non deve contare né morire."""
    monkeypatch.setattr(new_lesson, "LLM_CACHE_DIR", tmp_path)
    monkeypatch.setattr(new_lesson, "LLM_CACHE_MAX", 2)
    (tmp_path / "nota.txt").write_text("tengo", encoding="utf-8")
    for i in range(4):
        (tmp_path / f"{i}.json").write_text("{}", encoding="utf-8")
    new_lesson._llm_cache_prune()
    assert (tmp_path / "nota.txt").exists()
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_cache_prune_su_cartella_inesistente_non_raises(monkeypatch, tmp_path):
    """Prima del fix scandir su una dir assente sollevava: qui deve uscire pulito."""
    monkeypatch.setattr(new_lesson, "LLM_CACHE_DIR", tmp_path / "non_esiste")
    new_lesson._llm_cache_prune()      # non deve sollevare


# ------------------------------------------------------------------ anteprima
def test_anteprima_non_salta_su_normalize_profilo(monkeypatch, tmp_path):
    """Regressione: `python new_lesson.py preview` era rotto.

    preview_from_source() usava `normalize_profilo` col nome semplice, ma quella
    funzione vive in tools/common.py e NON e' fra gli import in testa al modulo:
    l'anteprima moriva subito con "name 'normalize_profilo' is not defined".
    Non lo prendeva nessun test perche' la funzione viene esercitata solo a
    runtime. Qui si chiama davvero, con l'LLM sostituito.
    """
    src = tmp_path / "materiale.txt"
    src.write_text("Il cervello governa il corpo. La memoria conserva. "
                   "Le sinapsi trasmettono. I neuroni comunicano.", encoding="utf-8")
    monkeypatch.setattr(new_lesson, "ensure_llm", lambda timeout=0: False)
    out = new_lesson.preview_from_source(str(src), out_name=str(tmp_path / "ant.json"))
    assert out and out.exists(), "l'anteprima non ha prodotto nessun file"
    struct = json.loads(out.read_text(encoding="utf-8"))
    assert struct.get("titolo")
    assert struct.get("moduli"), "la struttura deve avere almeno un modulo"


# ------------------------------------------------------------------ modello
def test_modello_configurato_e_comboact():
    """Il modello richiesto è comboact, in config.json e nel default."""
    assert DEFAULT_CONFIG["llm_model"] == "comboact"
    assert load_config().get("llm_model") == "comboact"


# ------------------------------------------------------- export e encoding
def test_export_zip_su_console_cp1252():
    """Regressione: `export_zip` moriva con UnicodeEncodeError su cp1252.

    Il reconfigure di UTF-8 stava solo in `if __name__ == '__main__'`, ma il
    pannello chiama `export_zip.export()` come libreria: senza passare da
    main() lo stdout restava su cp1252 e la freccia "→" del messaggio finale
    faceva esplodere la funzione DOPO aver gia' creato lo ZIP. Il docente
    vedeva un traceback invece di "Export OK".

    Il test rilancia l'interprete in una subprocess con PYTHONIOENCODING=cp1252,
    che e' il vero scenario (la console di PowerShell). Sostituire sys.stdout
    dentro il processo non proverebbe nulla: il reconfigure all'import agisce
    sullo stream gia' presente, quindi un sostituto successivo lo annullerebbe.
    """
    lezioni = [n for n in export_zip.list_lessons() if n.endswith("_lesson")]
    if not lezioni:
        pytest.skip("nessuna lezione da esportare")

    codice = (
        "import sys; sys.path.insert(0, 'tools')\n"
        "from pathlib import Path\n"
        "from export_zip import export\n"
        f"out = export({lezioni[0]!r})\n"
        "assert out and Path(out).exists()\n"
        "Path(out).unlink()\n"
        "print('ESPORTAZIONE OK')\n"
    )
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, "-c", codice], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env,
                       cwd=str(BASE))
    assert "ESPORTAZIONE OK" in r.stdout, (
        "export() deve funzionare anche su console cp1252:\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr[-500:]!r}")


@pytest.mark.parametrize("modulo", ["new_lesson", "export_zip", "export_single"])
def test_il_reconfigure_e_a_livello_di_modulo(modulo):
    """I moduli stampano caratteri non-ASCII: il reconfigure deve stare in testa.

    Se torna dentro `if __name__ == '__main__'`, importarli (come fa il
    pannello) li lascia con stdout cp1252 e il primo print con "✓" muore.
    """
    import importlib

    try:
        sys.stdout.reconfigure(encoding="cp1252", errors="strict")
    except Exception:
        pytest.skip("stdout non reconfigurabile")
    try:
        src = Path(importlib.import_module(modulo).__file__).read_text(encoding="utf-8")
    finally:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    primo_def = src.find("def ")
    pos = src.find("sys.stdout.reconfigure")
    assert pos != -1, f"{modulo} non reconfigura lo stdout"
    assert pos < primo_def, (
        f"{modulo}: il reconfigure deve stare a livello di modulo, non dentro "
        "main(): i chiamanti che importano la libreria altrimenti restano su cp1252")

def test_config_json_e_json_valido():
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    assert cfg["llm_url"].rstrip("/").endswith("/v1")
    assert cfg["llm_model"] == "comboact"
    # comboact in catena non deve generare duplicati: llm_structure() deduplica,
    # ma la config resta leggibile
    assert isinstance(cfg["llm_modelli_fallback"], list)
