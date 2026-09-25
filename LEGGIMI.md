# Simulatore Mindsmith — genera lezioni interattive da Word

## Avvio (un solo file)

Doppio clic su **`AVVIA.bat`**: crea/usa l'ambiente virtuale `.venv`,
installa le dipendenze, parte il server locale e si apre nel browser
il **pannello di controllo** (pagina grafica), con cui puoi:
1. caricare il **materiale**: trascinalo nella zona tratteggiata del pannello
   o usa "Sfoglia" (documenti, `.pptx`, `.epub`, `.mp3`, `.m4a`, `.wav`) e premi
   **Carica e genera**; i materiali già presenti sono elencati anche nella
   sezione **Materiali e spazio**;
2. generare da **link** (sito web o video YouTube);
3. impostare il **profilo lezione**: durata (breve/standard/approfondita),
   livello (base/intermedio/avanzato), obiettivo Bloom
   (conoscenza/comprensione/applicazione/analisi);
4. impostare le **opzioni**: rigenera anche le lezioni esistenti (`--force`),
   bozza senza LLM (`--bozza`), rigenera solo l'audio di una lezione,
   generare come **file HTML unico** (senza cartella) e attivare la
   **trascrizione audio locale con Whisper**;
5. usare le **impostazioni** del pannello per porta, limite upload, cache,
   modello Whisper, backup, IP e PIN docente;
6. gestire **materiali**, spazio occupato e **backup** dalle sezioni dedicate;
7. **modificare** le slide dopo la generazione (titolo, narrazione, quiz)
   con rigenerazione audio della singola slide;
8. provare le **voci** neurali (anteprima audio) prima di generare;
9. seguire il **log** in tempo reale (errori anche in `panel_errors.log`);
10. **aprire** le lezioni generate (anche da tablet/telefono sulla stessa rete
   Wi-Fi: l'indirizzo LAN è mostrato nel pannello).

`AVVIA.bat gui` → vecchia GUI desktop (tkinter: anteprima, genera, esporta ZIP).
`python avvia.py` → flusso automatico da terminale (senza pannello).

In alternativa al pannello, da terminale: `python new_lesson.py build <file|URL>`.

## Fonti di partenza

La pipeline accetta qualsiasi di queste fonti (stessa struttura interna):

- **File**: `.docx`, `.pdf`, `.txt`, `.md`, `.html`, `.pptx`, `.epub`;
  inoltre `.mp3`, `.m4a`, `.wav` vengono trascritti localmente con Whisper.
  Dal pannello si caricano con drag & drop o pulsante
  "Carica e genera": il file atterra nella cartella del progetto.
  Da terminale restano validi file locali e `watch`:
  `python new_lesson.py build <file|URL>` (con `--durata= --livello=
  --obiettivo=` per il profilo).
- **Sito web**: `python new_lesson.py build https://esempio.it/pagina`
- **Video YouTube**: `python new_lesson.py build https://www.youtube.com/watch?v=...`
  (usa la trascrizione automatica via `youtube-transcript-api`; se manca o non
  è disponibile, ripiega su titolo + descrizione).
- **Anteprima** senza audio: `python new_lesson.py preview <file|URL>`.

Dipendenze opzionali (PDF, YouTube e Whisper): `pip install -r requirements-extra.txt`.

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
  salvata — ma solo della STESSA versione della lezione: se la lezione viene
  rigenerata (slide diverse) si riparte sempre dalla prima pagina, e se si
  riprende a metà percorso compare un avviso con "⏮ Ricomincia dall'inizio".
  **Segnalibri "da rivedere"** (tasto S): il dot della slide riceve
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
- **File unico "HTML singolo"**: con `python new_lesson.py build <file> --single`
  (o la casella **"Genera come file HTML unico"** nel pannello) la build produce
  un solo `Nome_singola.html` auto-contenuto — CSS, script, dati e TUTTO l'audio
  incorporato — e rimuove la cartella: basta un doppio clic per aprirlo su
  qualsiasi computer, anche da pendrive o via email, senza server né internet.
  `--keep-folder` conserva anche la cartella. I file unici generati compaiono
  nel pannello sotto "File unici" con il link per aprirli/salvarli.
- Nessun video: niente clip da generare (build molto più rapida).
- `report.html` in ogni lezione con l'esito della validazione.

## Struttura

```
AVVIA.bat            avvio unico (tutto automatico, o interfaccia con "gui")
avvia.py             flusso automatico: dipendenze -> build mancanti -> serve
panel.py             pannello di controllo web: upload materiale (solo upload,
                      niente scansione cartella), profilo lezione, voci,
                      editor slide, genera da file/link, coda job, log live,
                      apre le lezioni
app.py               GUI: verifica, anteprima, genera, apri, esporta
new_lesson.py        pipeline: watch | build | preview | reaudio
start_lesson.py      server locale con porta libera (8341-8350);
                     senza argomenti serve la prima lezione, con più
                     lezioni apre l'indice per scegliere
check_env.py         controllo ambiente
config.json          llm_url, llm_model, llm_api_key (opzionale), voice,
                      edge_voice, edge_voice_domande (opzionale: voci alternate),
                      edge_rate, audio_bitrate, theme,
                      num_moduli_min/max, porta, cache_max_mb,
                      tts_workers, tts_retries, profilo_durata/livello/obiettivo,
                      llm_modo (due_fasi|unica), llm_modelli_fallback,
                      llm_max_tokens, llm_timeout, llm_deadline, llm_parallel
classifica.json      risultati degli studenti per la classifica di classe
                     (creato dal pannello, ignorato da git)
classifica.sqlite3   archivio SQLite locale della classifica (rigenerabile)
generatore-lezioni-mappa.html   mappa interattiva del sistema (Archify):
                     apri nel browser per esplorare componenti e percorsi
generatore-lezioni-mappa.json   sorgente dell'IR per rigenerare la mappa
requirements.txt     python-docx, edge-tts (ffmpeg serve per durata/fallback)
requirements.lock    versioni esatte testate (pip install -r requirements.lock)
requirements-extra.txt  pypdf, YouTube e faster-whisper (audio locale)
requirements-dev.txt    pytest (test unitari)
tools/               common, player_template (player autogenerato),
                     sources (fonti e Whisper), export_zip, export_single,
                     class_report, selftest (QA), netdiag, qr, jobs,
                     multipart, uploads, panel_ui, panel_settings, materials,
                     backups, class_repository, lesson_admin
assets/voice/        modello Piper + cache audio
```

## Note

- **9router** viene avviato da solo se non è attivo; senza di lui la lezione
  esce in modalità ridotta (banner BOZZA, senza quiz).
- **LLM in due fasi** (default `llm_modo: "due_fasi"`): prima la struttura
  (titolo, testi e scaletta dei moduli), poi le attività **modulo per modulo in
  parallelo** (`llm_parallel`, default 4). Chiedere tutto in una richiesta sola
  supera il tetto di token delle rotte gratuite: il modello risponde troncato (o
  con un solo modulo) e la risposta va buttata — è il caso che faceva finire la
  fase LLM in timeout con una lezione degradata. Con `"unica"` si torna alla
  richiesta singola (schema completo), utile solo con modelli che completano
  output lunghi.
- **Client LLM tollerante**: legge anche le risposte in streaming (chunk
  `data:`), usa il campo `reasoning` quando `content` è vuoto (modelli
  reasoning come gpt-oss), ripara un JSON tagliato a metà conservando i moduli
  completi. Le rotte in cooldown/quota (503/404/429) vengono marcate e saltate
  per il resto della build, senza aspettare `llm_deadline` (default 240 s) a vuoto.
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
- Log in `generazione.log`, errori API del pannello in `panel_errors.log`.
  Rigenera con `python new_lesson.py build file.docx --force`.
- **Codice modulare**: `panel.py` contiene logica HTTP e compatibilità API;
  interfaccia, coda/job, upload, impostazioni, materiali, backup, rete e
  classifica vivono in moduli `tools/` separati e testati.
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
- File HTML unico senza cartella: `python new_lesson.py build file.docx --single`.
- **Trascrizione audio con Whisper**: carica un `.mp3`, `.m4a` o `.wav`,
  spunta l'opzione nel pannello e premi «Carica e genera». La trascrizione avviene
  localmente; il testo ottenuto crea la lezione con il flusso normale.
  Usa il modello Whisper `base` con accelerazione multi-core: la qualità
  resta invariata, ma su computer con 8 core la trascrizione è circa 7 volte
  più rapida. Il modello si cambia in **Impostazioni** (`tiny`, `base`, `small`).
  La trascrizione viene salvata in `.whisper_cache/`: rigenerare lo stesso
  file con lo stesso modello la riutilizza immediatamente. Durante il lavoro
  il pannello mostra percentuale e tempo residuo stimato e permette di
  annullare la trascrizione.
- **Backup e ripristino**: la sezione Backup elenca le copie automatiche di
  classifica e cronologia. Il ripristino crea prima un backup di sicurezza;
  le impostazioni permettono di scegliere quante copie conservare e ogni
  quanti minuti crearne una.
- **Materiali e spazio**: la sezione omonima mostra i file di partenza, la
  lezione corrispondente e lo spazio per materiali, lezioni, cache e backup.
  Un materiale può essere eliminato solo digitando nuovamente il suo nome e
  solo se la lezione corrispondente esiste già.
- **Trascina nella categoria**: nuova attività interattiva (l'LLM la crea solo
  quando il materiale offre categorie nette): elementi da smistare su 2-3
  colonne con drag & drop o tap; punteggio al primo collocamento. Valida dal
  validatore e visualizzata nel player.
- **Voci alternate**: imposta `edge_voice_domande` in config.json (es.
  `it-IT-ElsaNeural`) e le domande/verifiche saranno lette da una seconda voce:
  la lezione suona come un dialogo. Cache audio distinta per voce.
- **Mappa del percorso**: sotto la barra di avanzamento le stazioni dei moduli
  (verde = raggiunta, lampeggiante = dove sei): tap per saltare al modulo.
- **Badge collezionabili**: 10 badge sbloccati in base a come giochi (serie,
  precisione, velocità, esplorazione) con toast di sblocco e armadietto nel
  menu ☰; persistiti per lezione nel browser.
- **Classifica di classe**: dal pannello finale lo studente invia il risultato
  con un tap («🏆 Invia alla classifica»); il docente vede la classifica per
  lezione nella sezione 🏆 del pannello (anche da client LAN), con export CSV
  e inserimento manuale. Nessun servizio esterno: tutto in `classifica.json`.
