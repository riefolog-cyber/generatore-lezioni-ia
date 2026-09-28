# -*- coding: utf-8 -*-
"""Rigenera index/main delle lezioni esistenti dopo fix player (modalità focus)."""
from pathlib import Path
import sys
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))
from player_template import write_player, bust_cache  # noqa: E402
from common import load_config  # noqa: E402

tema = load_config().get("theme", "dark")
for out in sorted(BASE.glob("*_lesson")):
    if not (out / "lesson-data.js").exists():
        continue
    titolo = out.name.replace("_lesson", "").replace("_", " ")
    try:
        import re, json
        t = (out / "lesson-data.js").read_text(encoding="utf-8")
        payload = json.loads(re.sub(r"^window\.LESSON_DATA\s*=\s*", "", t).rstrip().rstrip(";"))
        titolo = payload.get("titolo") or titolo
    except Exception:
        pass
    write_player(out, titolo, tema=tema)
    bust_cache(out)
    main = (out / "main.js").read_text(encoding="utf-8")
    print(out.name, "focus:", ("focusAct" in main and "attivita" in main))
print("FINE")

