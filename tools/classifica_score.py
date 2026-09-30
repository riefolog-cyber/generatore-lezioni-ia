# -*- coding: utf-8 -*-
"""Indice di classifica: media degli errori e del tempo impiegato.

La classifica non metteva in fila gli studenti per percentuale di risposte
corrette: due alunni con lo stesso 80% erano indistinguibili, e uno studente
lento che aveva capito finiva davanti a uno svelto che aveva indovinato a caso.
La richiesta del docente e' una sola: la classifica deve essere la MEDIA degli
errori commessi e del tempo impiegato per concludere le attivita'.

Qui vive la formula, in un posto solo e senza dipendenze, perche' sia provabile
da test e riusabile dal pannello, dall'export CSV e dal report di classe.

Formula
-------
I due dati hanno unita' diversa (errori = numero, tempo = minuti) e nessuno dei
due e' confrontabile con l'altro: 3 errori non sono "pegio" di 3 minuti. Ogni
componente viene quindi riportato a una penalita' 0-100 rispetto al peggiore
della classe, e l'indice e' la loro media:

    penalita_errori = errori   / max_errori * 100
    penalita_tempo  = tempo   / max_tempo  * 100
    indice          = 100 - (penalita_errori + penalita_tempo) / 2

L'indice va da 0 (peggior combinazione della classe) a 100 (nessun errore e il tempo
piu' breve): si sommano due vantaggi obviousi invece di moltiplicarli, quindi
una classe con un solo studente non collassa a zero.

I massimi sono calcolati solo sui risultati COMPLETI: chi ha abbandonato a
meta' perche' si e' perso non deve fare da termometro della classe.
"""
from __future__ import annotations

MINUTI_MAX = 600.0     # 10 h: oltre non e' piu' "tempo per fare un'attivita'"


def indice(errori, tempo_min, max_errori, max_tempo):
    """Indice 0-100 (100 = nessun errore e il tempo piu' rapido).

    `max_errori` / `max_tempo` sono i peggiori valori della lezione: entrambi a
    0 (classe vuota, o un solo studente senza errori in 0 minuti) danno
    penalita' 0, cioe' indice 100 senza divisioni per zero.
    """
    pen_errori = (max(0.0, float(errori)) / max(1.0, float(max_errori))) * 100.0
    tempo = max(0.0, min(MINUTI_MAX, float(tempo_min or 0)))
    pen_tempo = (tempo / max(0.0001, float(max_tempo))) * 100.0
    pen_errori = min(100.0, pen_errori)
    pen_tempo = min(100.0, pen_tempo)
    return round(100.0 - (pen_errori + pen_tempo) / 2.0, 1)


def estremi(rows):
    """(max_errori, max_tempo) sui soli risultati completati."""
    finiti = [r for r in rows if r.get("completata")]
    if not finiti:
        finiti = list(rows)
    if not finiti:
        return 0.0, 0.0
    max_errori = max((max(0.0, float(r.get("errori") or 0)) for r in finiti), default=0.0)
    max_tempo = max((max(0.0, min(MINUTI_MAX, float(r.get("tempo_min") or 0)))
                     for r in finiti), default=0.0)
    return max_errori, max_tempo


def ordina(rows, per_studente=True):
    """Classifica gia' calcolata: se un solo studente ha piu' tentativi ne
    resta il MIGLIORE (indice alto, a parita' il piu' rapido), e chi non ha
    finito il percorso resta in coda invece di pesare sul termometro.

    L'indice e' gia' presente su ogni riga (calcolato da `punta`): qui si
    ordina e basta, cosi' chi lo calcola decide anche come trattare chi non
    ha finito.
    """
    if not rows:
        return []

    def chiave(r):
        # 0 per chi ha finito: con `sorted` crescente la chiave piu' piccola
        # apre la classifica, quindi chi non ha concluso il percorso va in coda
        finito = 0 if r.get("completata") else 1
        return (finito, -(r.get("indice") or 0.0),
                -(r.get("pct") or 0), float(r.get("tempo_min") or 0),
                str(r.get("studente") or ""))

    if not per_studente:
        return sorted(rows, key=chiave)

    migliore = {}
    for r in rows:
        k = r.get("studente") or "Anonimo"
        # `sorted` mette il migliore per primo, quindi il "best" e' la riga con
        # la chiave PIU' PICCOLA: confrontare con ">" avrebbe tenuto la peggiore.
        if k not in migliore or chiave(r) < chiave(migliore[k]):
            migliore[k] = r
    return sorted(migliore.values(), key=chiave)


def punta(rows, punteggio=None):
    """Restituisce le righe con `indice` e `media` calcolati e pronte da
    ordinare. `punteggio` (0-100) e' l'indice già presente nelle righe: se manca
    o non e' numerico si ricalcola dal numero di errori e dai minuti.
    """
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        r = dict(r)
        punti, totale = int(r.get("punti") or 0), int(r.get("totale") or 0)
        r["punti"], r["totale"] = punti, totale
        r["pct"] = int(r.get("pct") or (round(punti / totale * 100) if totale else 0))
        r["errori"] = max(0, int(r.get("errori") or 0))
        r["tempo_min"] = round(max(0.0, min(MINUTI_MAX, float(r.get("tempo_min") or 0))), 1)
        r["completata"] = bool(r.get("completata"))
        out.append(r)
    max_errori, max_tempo = estremi(out)
    for r in out:
        if punteggio is not None:
            try:
                r["indice"] = round(max(0.0, min(100.0, float(punteggio))), 1)
            except (TypeError, ValueError):
                r["indice"] = indice(r["errori"], r["tempo_min"], max_errori, max_tempo)
        else:
            r["indice"] = indice(r["errori"], r["tempo_min"], max_errori, max_tempo)
        # media leggibile: penalita' 0-100, dove 0 e' il massimo della classe
        r["media"] = round(100.0 - r["indice"], 1)
    return out
