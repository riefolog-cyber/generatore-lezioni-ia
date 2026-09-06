# Simulatore Mindsmith — genera lezioni interattive da Word

## Avvio (un solo file)

Doppio clic su **`AVVIA.bat`**: parte il server locale e si apre nel browser
il **pannello di controllo** (pagina grafica), con cui puoi:
1. scegliere il **materiale**: trascinalo nella zona tratteggiata del pannello
   o usa "Sfoglia" per caricarlo dal browser (`.docx`, `.pdf`, `.txt`, `.md`,
   `.html`), oppure usa i file già presenti nella cartella, e premere **Genera**;
2. generare da **link** (sito web o video YouTube);
3. impostare le **opzioni**: rigenera anche le lezioni esistenti (`--force`),
   bozza senza LLM (`--bozza`), rigenera solo l'audio di una lezione;
4. seguire il **log** in tempo reale;
5. **aprire** le lezioni generate (anche da tablet/telefono sulla stessa rete
   Wi-Fi: l'indirizzo LAN è mostrato nel pannello).

`AVVIA.bat gui` → vecchia GUI desktop (tkinter: anteprima, genera, esporta ZIP).
`python avvia.py` → flusso automatico da terminale (senza pannello).

In alternativa al pannello, da terminale: `python new_lesson.py build <file|URL>`.

## Fonti di partenza

La pipeline accetta qualsiasi di queste fonti (stessa struttura interna):

- **File**: `.docx` (python-docx), `.pdf` (serve `pypdf`), `.txt`, `.md`,
  `.html` — si caricano dal pannello (drag & drop o pulsante: atterrano
  nella cartella del progetto) oppure si mettono direttamente in cartella:
  vengono presi in carico automaticamente da `AVVIA.bat` / `watch`.
- **Sito web**: `python new_lesson.py build https://esempio.it/pagina`
- **Video YouTube**: `python new_lesson.py build https://www.youtube.com/watch?v=...`
  (usa la trascrizione automatica via `youtube-transcript-api`; se manca o non
  è disponibile, ripiega su titolo + descrizione).
- **Anteprima** senza audio: `python new_lesson.py preview <file|URL>`.

Dipendenze opzionali per PDF e YouTube: `pip install -r requirements-extra.txt`.

## Cosa genera

- **Player autogenerato**: niente cartelle template da mantenere — a ogni
  richiesta il codice scrive il player (HTML/CSS/JS) adattando **grafica e
  numero di attività** al tema (`theme` in config.json) e al contenuto del
  documento. La tinta d'accento deriva dal titolo della lezione.
- Slide: apertura → moduli (contenuto narrato) → attività interattive a
  rotazione (quiz, Vero/Falso, Compila il vuoto, Ordina la sequenza, Cosa
  faresti?, Trova l'errore — con passaggi guidati Leggi→Individua→Correggi,
  opzioni come pulsanti e suggerimento dopo due errori, abbinamenti) → conclusione
- **Audio neurale sempre presente e sincronizzato**: ogni slide contiene la
  propria narrazione, quindi la voce legge esattamente il passaggio mostrato.
  Motore primario edge-tts (voci neurali Microsoft, `edge_voice` in
  config.json) con word boundary reali al millisecondo per i sottotitoli;
  fallback Piper locale e infine silenzio della durata stimata — l'audio non
  manca mai. **Post-produzione ffmpeg** su ogni traccia: loudness uniformata
  tra le slide (`loudnorm`), micro-fade anti-pop e ricodifica a 44.1 kHz /
  96 kbps (`audio_bitrate` in config.json). Il player mostra la didascalia a
  frasi naturali durante la lettura e offre controlli: ▶/pausa (Spazio),
  riascolta ⟲ (R), velocità 0.8×/1×/1.25×, riproduzione continua ⏭
  (auto-avanti a fine audio). Il testo letto viene ripulito prima della
  sintesi (abbreviazioni espanse, simboli %/€/→ detti a parole, punteggiatura
  normalizzata) per una voce più naturale.
- **Gamification nel player**: ogni attività assegna punti (chip ⭐ in alto),
  le slide dei moduli hanno una barra di navigazione rapida, la conclusione
  mostra medaglia/stelle/confetti con riepilogo del percorso, una **Sfida
  lampo** (5 domande miste pescate dalla lezione) e il pulsante "Rigioca".
  **Riprendi da dove eri**: riaprendo la lezione si riparte dalla slide
  salvata. **Segnalibri "da rivedere"** (tasto S): il dot della slide riceve
  un segnalino 🔖 persistente. **Ricerca nella lezione** (tasto F o 🔍):
  salta subito alla slide che contiene il testo cercato. Guida completa
  delle scorciatoie con il tasto **?**. Transizioni animate avanti/indietro.
- **Report per il docente**: nel pannello finale lo studente inserisce il
  proprio nome (pannello inline, mai finestre di sistema) e può scaricare il
  report in **CSV**, **JSON** o **stamparlo**: attività svolte, punti,
  precisione, tempo totale e per slide, registro delle risposte. Tutto
  locale nel browser, nessun dato inviato altrove.
- **Modalità ripasso**: dal pannello finale il pulsante "🎯 Ripassa le slide
  segnalate" ripercorre solo i segnalibri; a fine percorso suggerisce di
  riprovarci tra 3 giorni (ripetizione dilazionata).
- **Accessibilità**: pulsante ♿ con ingrandimento testo (4 livelli) e tema
  ad alto contrasto (Ctrl+Alt+T), feedback announced via aria-live,
  navigazione da tastiera che ignora i campi di testo.
- **Cache-busting automatico**: i riferimenti a css/js/dati portano l'hash
  del file (es. `main.js?v=f43f460fef`): il browser ricarica sempre la
  versione giusta, niente più pagine bianche da cache.
- **Hub multi-lezione**: con più lezioni generate, il server apre una pagina
  indice da cui scegliere quale lezione avviare.
- **Self-test**: `python tools/selftest.py` verifica in un colpo solo file,
  dati, audio, sottotitoli, server e rendering headless (zero errori JS).
- Nessun video: niente clip da generare (build molto più rapida).
- `report.html` in ogni lezione con l'esito della validazione.

## Struttura

```
AVVIA.bat            avvio unico (tutto automatico, o interfaccia con "gui")
avvia.py             flusso automatico: dipendenze -> build mancanti -> serve
panel.py             pannello di controllo web: carica materiale (upload),
                     genera da file/link, log in tempo reale, apre le lezioni
app.py               GUI: verifica, anteprima, genera, apri, esporta
new_lesson.py        pipeline: watch | build | preview | reaudio
start_lesson.py      server locale con porta libera (8341-8350);
                     senza argomenti serve la prima lezione, con più
                     lezioni apre l'indice per scegliere
check_env.py         controllo ambiente
config.json          llm_url, llm_model, llm_api_key (opzionale), voice,
                     edge_voice, edge_rate, audio_bitrate, theme,
                     num_moduli_min/max, porta, cache_max_mb,
                     tts_workers, tts_retries
requirements.txt     python-docx, edge-tts (ffmpeg serve per durata/fallback)
requirements-extra.txt  pypdf (PDF), youtube-transcript-api (YouTube)
requirements-dev.txt    pytest (test unitari)
tools/               common, player_template (player autogenerato),
                     sources (fonti di partenza), export_zip,
                     selftest (QA automatico)
assets/voice/        modello Piper + cache audio
```

## Note

- **9router** viene avviato da solo se non è attivo; senza di lui la lezione
  esce in modalità ridotta (banner BOZZA, senza quiz).
- **Anti-concorrenza**: un blocco (`.generazione.lock`) evita che watch, GUI e
  riga di comando generino due lezioni insieme; un blocco "vecchio" (oltre 30
  minuti) viene considerato abbandonato e sostituito.
- **Cache audio** in `assets/voice/cache`: riusa le tracce per lo stesso
  testo+voce e si autolimita a `cache_max_mb` (default 300 MB) eliminando le
  voci più vecchie.
- **Cache LLM** (`.llm_cache/`): rigenerando con `--force` la stessa fonte e lo
  stesso modello configurato, la struttura dei contenuti si riusa al posto di
  rifare la chiamata LLM (risparmio di minuti). Con `--no-cache` si forza una
  nuova strutturazione. Autolimitata a 200 voci (le più vecchie vengono rimosse).
- Log in `generazione.log`. Rigenera con `python new_lesson.py build file.docx --force`.
- **Niente pagina bianca da cache**: il server della lezione invia intestazioni
  no-cache e i file sono caricati con versione (`main.js?v=4`); se il browser
  usa comunque un `index.html` vecchio, il player mostra un messaggio con il
  pulsante "Ricarica la pagina" invece di restare bianco.
- `check_env.py` distingue i blocchi veri (Python/python-docx) dagli avvisi:
  edge-tts, voce Piper e ffmpeg mancanti NON impediscono di generare (l'audio
  degrada a Piper/silenzio), avvia.py prosegue e segnala.
- L'export ZIP (`tools/export_zip.py`) ora include `start_lesson.bat` +
  `avvia_qui.py` funzionanti: il destinatario estrae, fa doppio clic sul .bat
  (o apre `index.html`) e la lezione parte senza installare nulla.
- Anteprima veloce senza audio: `python new_lesson.py preview file.docx`.
- Riaudio/riplayer senza rifare l'LLM (dopo aver cambiato voce o tema in
  config.json): `python new_lesson.py reaudio Nome_Lezione_lesson`.
