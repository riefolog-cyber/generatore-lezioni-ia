# -*- coding: utf-8 -*-
"""Fixture condivise da tutta la suite.

Il profilo di Chrome headless e' creato una volta sola e riusato da tutte le
invocazioni (test_grading, test_player_css): misurati ~70 ms risparmiati per
avvio, perche' Chrome non deve ricostruire cache e preferenze da zero. Alla
fine della sessione viene rimosso, cosi' non restano cartelle nel TEMP.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))


@pytest.fixture(scope="session", autouse=True)
def _pulisci_profilo_chrome():
    """Alla fine della sessione rimuove il profilo Chrome condiviso."""
    from tools.selftest import cleanup_chrome_profile

    yield
    cleanup_chrome_profile()
