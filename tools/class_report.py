# -*- coding: utf-8 -*-
"""Report di classe: aggrega i file JSON esportati dal player della lezione.

Il docente fa scaricare a ogni studente il report JSON (pannello finale ->
⬇ JSON), raccoglie i file in una cartella e lancia:

  python tools/class_report.py cartella_con_i_report
  python tools/class_report.py report1.json report2.json ...

Produce in output:
  - report_classe.csv   (una riga per studente, apribile in Excel)
  - report_classe.html  (riepilogo con classifica e punti deboli per attività)
"""
import csv
import json
import sys
from pathlib import Path


def _load(paths):
    """Carica i report JSON validi (uno o più file, oppure una cartella)."""
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.json")))
        elif p.is_file():
            files.append(p)
    reports = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "risposte" in d and "lezione" in d:
                d["_file"] = f.name
                reports.append(d)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ {f.name}: ignorato ({str(e)[:60]})")
    return reports


def _pct(e, t):
    return round(e / t * 100) if t else 0


def _per_tipo(rep):
    """Per ogni tipo di attività: (corrette, totali) dal registro risposte."""
    stats = {}
    for r in rep.get("risposte", []):
        tipo = r.get("tipo") or "?"
        c, t = stats.get(tipo, (0, 0))
        stats[tipo] = (c + (1 if r.get("esito") else 0), t + 1)
    return stats


def build_csv(reports, out_path):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["studente", "lezione", "data", "tempo_min", "punti",
                    "precisione_%", "dettaglio_per_tipo"])
        for r in reports:
            pt = r.get("punti") or ""
            prec = (r.get("precisione") or "").replace("%", "")
            tipo_txt = "; ".join(f"{k}: {c}/{t}" for k, (c, t)
                                 in sorted(_per_tipo(r).items()))
            w.writerow([r.get("studente"), r.get("lezione"), r.get("data"),
                        r.get("tempo_min"), pt, prec, tipo_txt])


def build_html(reports, out_path):
    rows = []
    for r in sorted(reports, key=lambda x: -(int(str(x.get("precisione") or "0")
                                                  .replace("%", "") or 0))):
        rows.append(f"<tr><td>{r.get('studente', '?')}</td>"
                    f"<td>{r.get('punti', '-')}</td>"
                    f"<td>{r.get('precisione', '-')}</td>"
                    f"<td>{r.get('tempo_min', '?')} min</td></tr>")
    # punti deboli della classe: tipo di attività con precisione media più bassa
    agg = {}
    for r in reports:
        for tipo, (c, t) in _per_tipo(r).items():
            cc, tt = agg.get(tipo, (0, 0))
            agg[tipo] = (cc + c, tt + t)
    weak = sorted(((tipo, c, t) for tipo, (c, t) in agg.items() if t > 0),
                  key=lambda x: x[1] / x[2])[:3]
    weak_html = ("<ul>" + "".join(
        f"<li><b>{tipo}</b>: {c}/{t} corrette ({_pct(c, t)}%) — da ripassare</li>"
        for tipo, c, t in weak) + "</ul>") if weak else "<p>Nessun dato.</p>"
    html = f"""<!DOCTYPE html><html lang="it"><meta charset="utf-8">
<title>Report di classe</title>
<body style="font-family:'Segoe UI',sans-serif;max-width:760px;margin:2rem auto;background:#0d1420;color:#eaf1ff">
<h1>📊 Report di classe — {reports[0].get('lezione', '?') if reports else '?'}</h1>
<p>{len(reports)} studenti · generato il {__import__('datetime').date.today().isoformat()}</p>
<h2>Classifica (per precisione)</h2>
<table border="1" cellpadding="8" style="border-collapse:collapse">
<tr style="background:#1a2740"><th>Studente</th><th>Punti</th><th>Precisione</th><th>Tempo</th></tr>
{''.join(rows)}
</table>
<h2>Punti deboli della classe</h2>
{weak_html}
</body></html>"""
    out_path.write_text(html, encoding="utf-8")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    reports = _load(args)
    if not reports:
        print("Nessun report JSON valido trovato.")
        return 1
    out_csv = Path("report_classe.csv")
    out_html = Path("report_classe.html")
    build_csv(reports, out_csv)
    build_html(reports, out_html)
    print(f"✓ {len(reports)} studenti aggregati")
    print(f"  → {out_csv}  (per Excel / registro)")
    print(f"  → {out_html} (riepilogo leggibile)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
