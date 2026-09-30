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
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classifica_score import ordina, punta  # noqa: E402


def _errori(rep):
    """Numero di risposte sbagliate.

    Il player lo scrive gia' nel report; per i report generati da una versione
    precedente il campo non c'e', quindi si conta da `risposte` (la stessa
    fonte da cui nasce `errori_per_tipo`).
    """
    n = rep.get("errori")
    if isinstance(n, (int, float)):
        return int(n)
    return sum(1 for r in rep.get("risposte", []) if not r.get("esito"))


def _punti(r):
    """(punti, totale, pct) da un report: i punti sono una stringa "8/10"."""
    pt, tot = 0, 0
    grezzo = str(r.get("punti") or "")
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)\s*$", grezzo)
    if m:
        pt, tot = int(m.group(1)), int(m.group(2))
    pct = _numero(r.get("precisione"), 0.0)
    return pt, tot, int(pct if pct else (round(pt / tot * 100) if tot else 0))


def _riga(rep):
    """Una riga di classifica nella stessa forma del pannello."""
    pt, tot, pct = _punti(rep)
    return {"studente": rep.get("studente") or "Anonimo",
            "lezione": rep.get("lezione") or "",
            "punti": pt, "totale": tot, "pct": pct,
            "errori": _errori(rep),
            "tempo_min": _numero(rep.get("tempo_min"), 0.0),
            "completata": True,
            "t": rep.get("data") or "",
            # il report di origine: serve per il dettaglio per tipo di attivita'
            "rep": rep}


def classifica(reports):
    """Stessa regola del pannello: media degli errori e del tempo.

    Un solo file per studente (i report sono uno per invio), quindi non serve
    la scelta del "tentativo migliore": l'indice basta a ordinarli.
    """
    return ordina(punta([_riga(r) for r in reports]), per_studente=False)


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
        w.writerow(["posizione", "studente", "indice", "media_errori_tempo",
                    "errori", "tempo_min", "lezione", "data", "punti",
                    "precisione_%", "dettaglio_per_tipo"])
        for pos, r in enumerate(classifica(reports), start=1):
            tipo_txt = "; ".join(f"{k}: {c}/{t}" for k, (c, t)
                                 in sorted(_per_tipo(r["rep"]).items()))
            w.writerow([pos, r.get("studente"), r.get("indice"), r.get("media"),
                        r.get("errori"), r.get("tempo_min"), r.get("lezione"),
                        r.get("t"), f'{r.get("punti", 0)}/{r.get("totale", 0)}',
                        r.get("pct", 0), tipo_txt])
        precs, tempi = _aggregate(reports)
        w.writerow([])
        w.writerow(["STATISTICHE DI CLASSE"])
        if precs:
            w.writerow(["precisione media %", f"{statistics.mean(precs):.0f}"])
            w.writerow(["precisione mediana %", f"{statistics.median(precs):.0f}"])
            w.writerow(["precisione min %", f"{min(precs)}"])
            w.writerow(["precisione max %", f"{max(precs)}"])
        if tempi:
            w.writerow(["tempo medio min", f"{statistics.mean(tempi):.1f}"])
            w.writerow(["tempo mediano min", f"{statistics.median(tempi):.1f}"])
        indici = [r.get("indice", 0) for r in classifica(reports)]
        if indici:
            w.writerow(["indice medio (errori+tempo)",
                        f"{statistics.mean(indici):.0f}"])


def _aggregate(reports):
    """Liste di (precisione %, tempo min) valide dai report."""
    precs, tempi = [], []
    for r in reports:
        p = str(r.get("precisione") or "").replace("%", "").strip()
        if p.replace(",", "").replace(".", "").isdigit():
            precs.append(float(p.replace(",", ".")))
        t = str(r.get("tempo_min") or "").replace(",", ".").strip()
        if t.replace(".", "").isdigit():
            tempi.append(float(t))
    return precs, tempi


def _numero(v, default=0.0):
    """Numero da una stringa che può avere la virgola decimale.

    Il report dello studente può arrivare con "85,5" (separatore italiano) o
    con "85.5". `int("85,5".replace("%",""))` sollevava ValueError: il CSV
    era gia' stato scritto, ma l'HTML moriva e il docente restava con un file
    a meta' senza saperlo.
    """
    s = str(v or "").replace("%", "").replace(",", ".").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def build_html(reports, out_path):
    esc = lambda s: __import__("html").escape(str(s), quote=True)
    classifica_rows = classifica(reports)
    rows = []
    for pos, r in enumerate(classifica_rows, start=1):
        rows.append(f"<tr><td>{pos}</td><td>{esc(r.get('studente', '?'))}</td>"
                    f"<td><b>{esc(r.get('indice', '-'))}</b></td>"
                    f"<td>{esc(r.get('errori', '-'))}</td>"
                    f"<td>{esc(r.get('tempo_min', '?'))} min</td>"
                    f"<td>{esc(r.get('punti', 0))}/{esc(r.get('totale', 0))}</td>"
                    f"<td>{esc(r.get('pct', 0))}%</td></tr>")
    # punti deboli della classe: tipo di attività con precisione media più bassa
    agg = {}
    for r in reports:
        for tipo, (c, t) in _per_tipo(r).items():
            cc, tt = agg.get(tipo, (0, 0))
            agg[tipo] = (cc + c, tt + t)
    weak = sorted(((tipo, c, t) for tipo, (c, t) in agg.items() if t > 0),
                  key=lambda x: x[1] / x[2])[:3]
    weak_html = ("<ul>" + "".join(
        f"<li><b>{esc(tipo)}</b>: {c}/{t} corrette ({_pct(c, t)}%) — da ripassare</li>"
        for tipo, c, t in weak) + "</ul>") if weak else "<p>Nessun dato.</p>"
    precs, tempi = _aggregate(reports)
    if precs:
        stats_html = (f"<p>Precisione: media <b>{statistics.mean(precs):.0f}%</b> · "
                      f"mediana <b>{statistics.median(precs):.0f}%</b> · "
                      f"min {min(precs):.0f}% · max {max(precs):.0f}%</p>")
    else:
        stats_html = "<p>Nessuna precisione valida.</p>"
    if tempi:
        stats_html += (f"<p>Tempo: media <b>{statistics.mean(tempi):.1f} min</b> · "
                       f"mediana <b>{statistics.median(tempi):.1f} min</b></p>")
    if classifica_rows:
        indici = [r.get("indice", 0) for r in classifica_rows]
        errori_tot = sum(r.get("errori", 0) for r in classifica_rows)
        stats_html += (f"<p>Indice (errori + tempo): media <b>{statistics.mean(indici):.0f}</b> · "
                       f"errori totali <b>{errori_tot}</b></p>")
    esc_lezione = __import__("html").escape(str(reports[0].get('lezione', '?') if reports else '?'), quote=True)
    html = f"""<!DOCTYPE html><html lang="it"><meta charset="utf-8">
<title>Report di classe</title>
<body style="font-family:'Segoe UI',sans-serif;max-width:760px;margin:2rem auto;background:#0d1420;color:#eaf1ff">
<h1>📊 Report di classe — {esc_lezione}</h1>
<p>{len(reports)} studenti · generato il {__import__('datetime').date.today().isoformat()}</p>
<h2>Statistiche di classe</h2>
{stats_html}
<h2>Classifica — media degli errori e del tempo</h2>
<p style="font-size:13px;color:#9fb0c8">Ordinata per <b>indice</b>: metà errori e metà tempo
impiegato a concludere le attività, riportati al peggiore della classe
(100 = nessun errore e il tempo più rapido).</p>
<table border="1" cellpadding="8" style="border-collapse:collapse">
<tr style="background:#1a2740"><th>#</th><th>Studente</th><th>Indice</th><th>Errori</th><th>Tempo</th><th>Punti</th><th>Precisione</th></tr>
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
