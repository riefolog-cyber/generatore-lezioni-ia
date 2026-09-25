# -*- coding: utf-8 -*-
"""HTML/CSS/JavaScript del pannello, separato dalla logica HTTP."""

PANEL_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pannello di controllo — Generatore lezioni</title>
<style>
:root{--bg:#0d1220;--card:#161d33;--line:#2a3554;--txt:#eef2ff;--mut:#93a0c4;
--acc:#5b7bd5;--ok:#3ecf8e;--err:#ff6b6b;--warn:#ffd166}
html[data-theme="light"]{--bg:#eef2fb;--card:#ffffff;--line:#d5dff0;--txt:#17233b;
--mut:#5a6a8a;--acc:#3f63c8;--ok:#128a4a;--err:#d64545;--warn:#9a6b00}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--txt);
margin:0;padding:26px 18px 60px;transition:background .3s ease,color .3s ease}
.wrap{max-width:960px;margin:0 auto}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:12px}
h1{font-size:22px;margin:0 0 4px}
#btnTheme{font-size:18px;line-height:1;padding:8px 12px;border-radius:10px;flex:0 0 auto}
html[data-theme="light"] pre{background:#f1f5fd}
html[data-theme="light"] .urlrow input,html[data-theme="light"] .urlrow textarea,
html[data-theme="light"] .urlrow select,html[data-theme="light"] select{background:#f1f5fd!important;color:var(--txt)!important}
html[data-theme="light"] .chip{background:#f1f5fd}
html[data-theme="light"] .row{border-bottom-color:var(--line)}
html[data-theme="light"] .upzone:hover,html[data-theme="light"] .upzone.drag{background:#e3eaf7}
html[data-theme="light"] .card h2{color:#2c3d62}
html[data-theme="light"] .badge{background:#e3eaf7;color:#2c3d62}
html[data-theme="light"] a.apri{color:#2b5fc7}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:16px 18px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 12px;color:#c8d4f5;display:flex;align-items:center;gap:10px}
.step{flex:0 0 auto;width:26px;height:26px;border-radius:50%;font-size:13px;font-weight:800;
display:inline-flex;align-items:center;justify-content:center;color:#fff;
background:linear-gradient(135deg,var(--acc),#8a6ff0);box-shadow:0 2px 10px rgba(91,123,213,.5)}
button.primary{background:linear-gradient(135deg,var(--acc),#8a6ff0);font-size:14px;padding:10px 22px;
box-shadow:0 4px 16px rgba(91,123,213,.45)}
button.primary:hover{filter:brightness(1.12);transform:translateY(-1px)}
details.adv{margin-top:12px;font-size:13px;color:var(--mut)}
details.adv summary{cursor:pointer;padding:6px 0;user-select:none}
details.adv summary:hover{color:var(--txt)}
details.adv .opts{margin-top:6px}
.urlrow{flex-wrap:wrap}
#lanQr{width:140px;height:140px;border-radius:12px;border:1px solid var(--line);background:#fff;padding:6px;cursor:zoom-in;box-sizing:border-box}
#lanQr:hover,#lanQr:focus{outline:3px solid var(--acc);outline-offset:2px}
.qrOv{position:fixed;inset:0;z-index:9999;background:rgba(4,8,18,.94);display:flex;align-items:center;justify-content:center;padding:22px;flex-direction:column;gap:14px}
.qrOv[hidden]{display:none}
.qrOv img{width:min(88vmin,760px);height:min(88vmin,760px);background:#fff;padding:18px;border-radius:18px;box-shadow:0 24px 80px rgba(0,0,0,.65)}
.qrOv .qru{color:#fff;font-size:17px;font-weight:700;word-break:break-all;text-align:center}
.qrOv button{background:#fff;color:#111827;border:none;border-radius:10px;padding:10px 18px;font-weight:800;cursor:pointer;font-size:15px}
.lanrow{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.lanrow .grow{flex:1;min-width:200px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{padding:5px 11px;border-radius:20px;font-size:12px;border:1px solid var(--line);
background:#10172a;color:var(--mut)}
.chip.ok{color:var(--ok);border-color:#1e4b3a}
.chip.no{color:var(--err);border-color:#5b2b2b}
.row{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid #1d2742;
flex-wrap:wrap}
.row:last-child{border-bottom:none}
.name{font-weight:700;flex:1;min-width:160px;word-break:break-all}
.meta{color:var(--mut);font-size:12px;min-width:110px}
.badge{font-size:11px;padding:3px 9px;border-radius:12px;background:#233054;color:#b7c6ee}
.badge.exists{background:#1e3b2e;color:var(--ok)}
.badge.link{background:#3b2f1e;color:var(--warn)}
button{background:var(--acc);color:#fff;border:none;border-radius:9px;padding:8px 15px;
font-weight:700;cursor:pointer;font-size:13px;transition:filter .15s}
button:hover{filter:brightness(1.12)}
button:disabled{opacity:.45;cursor:not-allowed}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--mut)}
button.ghost:hover{color:var(--txt);border-color:var(--acc)}
button.mini{padding:5px 11px;font-size:12px}
.urlrow{display:flex;gap:8px}
.urlrow input{flex:1;background:#0d1220;border:1px solid var(--line);color:var(--txt);
border-radius:9px;padding:9px 12px;font-size:13px}
.opts{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px;font-size:13px;color:var(--mut)}
.opts label{display:flex;gap:6px;align-items:center;cursor:pointer}
pre{background:#0a0f1c;border:1px solid var(--line);border-radius:10px;padding:12px;
height:160px;overflow:auto;font:12px/1.55 Consolas,'Cascadia Mono',monospace;margin:0;
white-space:pre-wrap;word-break:break-word}
#lan{color:var(--warn)}
a.apri{text-decoration:none;color:#8ecaff;font-weight:700}
.empty{color:var(--mut);font-size:13px;padding:8px 0}
.upzone{border:2px dashed var(--line);border-radius:14px;padding:26px 22px;
margin:0 0 14px;color:var(--mut);font-size:14px;cursor:pointer;user-select:none;text-align:center;
transition:border-color .2s,background .2s}
.upzone:hover,.upzone.drag{border-color:var(--acc);background:#18233f;color:#c8d8ff}
.upzone .big{font-size:15px;font-weight:700;color:var(--txt);display:block;margin-bottom:4px}
.upzone .sub2{font-size:12.5px}
#uprow{justify-content:center;margin-top:12px}
.uprow{display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap}
.upmsg{font-size:12px;margin:6px 0 0}
.upmsg.ok{color:var(--ok)}.upmsg.err{color:var(--err)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.grid2 label{display:flex;flex-direction:column;gap:5px;color:var(--mut);font-size:13px}
.grid2 input,.grid2 select{background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px 12px;font-size:13px}
.settingsgrid{padding:9px 0;border-bottom:1px solid var(--line)}
.helpbox{background:#0d1220;border:1px solid var(--line);border-radius:9px;padding:10px}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <h1>🎛 Pannello di controllo — Generatore lezioni</h1>
    <button id="btnTheme" class="ghost" type="button" title="Tema chiaro / scuro">☀️</button>
  </div>
  <div class="sub" id="statusline">…</div>
  <div id="stale" style="display:none;margin:10px 0;padding:10px 14px;border:1px solid #ffb020;
       background:#2a2205;color:#ffd27a;border-radius:8px;font-size:14px">
    ⚠ <b>Il codice di generazione è cambiato</b> da quando il pannello è stato avviato:
    le generazioni userebbero il codice vecchio. Chiudi il pannello e riavvialo
    (<b>AVVIA.bat</b>) per attivare gli aggiornamenti.
  </div>

  <div class="card">
    <h2>Ambiente</h2>
    <div class="chips" id="deps"></div>
  </div>

  <div class="card">
    <h2><span class="step">1</span> Carica il materiale e genera</h2>
    <div class="upzone" id="upzone" role="button" tabindex="0"
         title="Carica un file (trascinalo qui sopra o clicca per sceglierlo)">
      <input type="file" id="upfile" accept=".docx,.pdf,.txt,.md,.html,.htm,.pptx,.epub,.mp3,.m4a,.wav" multiple hidden>
      <span class="big">📄 Trascina qui uno o più materiali</span>
      <span class="sub2" id="uptxt">Documenti, siti salvati, PPTX, EPUB o audio MP3/M4A/WAV</span>
      <div class="uprow" id="uprow" hidden>
        <span class="name" id="upname"></span>
        <span class="meta" id="upsize"></span>
        <button class="primary" id="btnUpGen" type="button">Carica e genera tutto</button>
        <button class="mini ghost" id="btnUp" type="button">Solo carica</button>
        <button class="mini ghost" id="btnUpX" type="button">✕</button>
      </div>
      <div class="upmsg" id="upmsg" hidden></div>
    </div>
    <div id="materials"></div>
    <div class="upmsg" id="backupInfo" style="text-align:center;margin-top:10px"></div>
    <div class="opts">
      <label>Durata <select id="profDurata">
        <option value="breve">Breve (3-4 moduli)</option>
        <option value="standard" selected>Standard (4-7)</option>
        <option value="approfondita">Approfondita (6-8)</option>
      </select></label>
      <label>Livello <select id="profLivello">
        <option value="base">Base</option>
        <option value="intermedio" selected>Intermedio</option>
        <option value="avanzato">Avanzato</option>
      </select></label>
      <label>Obiettivo <select id="profObiettivo">
        <option value="auto" selected>Auto (misto per modulo)</option>
      </select></label>
    </div>
    <details class="adv">
      <summary>⚙ Opzioni avanzate (rigenera, bozza, file unico)</summary>
      <div class="opts">
        <label><input type="checkbox" id="forceAll"> Rigenera anche le lezioni già esistenti</label>
        <label><input type="checkbox" id="bozzaAll"> Bozza veloce senza IA (solo struttura dal testo)</label>
        <label><input type="checkbox" id="singleAll"> Genera come file HTML unico, senza cartella</label>
        <label><input type="checkbox" id="whisperAudio"> 🎙️ Trascrizione audio con Whisper (MP3, M4A, WAV)</label>
      </div>
    </details>
  </div>

  <div class="card">
    <h2>Voce — anteprima</h2>
    <div class="urlrow">
      <select id="voiceSel" style="max-width:320px;background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px"></select>
      <select id="rateSel" style="background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px">
        <option value="-10%">Lenta -10%</option>
        <option value="-4%" selected>Normale -4%</option>
        <option value="+0%">+0%</option>
        <option value="+10%">Veloce +10%</option>
      </select>
      <button id="btnVoice" type="button">Prova voce</button>
    </div>
    <div class="urlrow" style="margin-top:8px">
      <input id="voiceTxt" value="Ciao! Questa è un'anteprima della voce per le lezioni." maxlength="500">
    </div>
    <audio id="voiceAudio" controls style="width:100%;margin-top:8px" hidden></audio>
    <div class="upmsg" id="voiceMsg" hidden></div>
  </div>

  <div class="card">
    <h2><span class="step">2</span> Oppure genera da un link</h2>
    <div class="urlrow">
      <input id="url" placeholder="https://it.wikipedia.org/wiki/…  oppure  https://www.youtube.com/watch?v=…">
      <button id="btnUrl" class="primary">Genera da link</button>
    </div>
  </div>

  <div class="card">
    <h2><span class="step">2</span> Oppure incolla il testo <span style="color:var(--mut);font-weight:400;font-size:12px">(appunti, dispense)</span></h2>
    <div class="urlrow"><input id="txtTitle" placeholder="Titolo lezione (es. Il sistema solare)" maxlength="80"></div>
    <div class="urlrow" style="margin-top:8px">
      <textarea id="txtBody" rows="4" style="flex:1;background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px"
        placeholder="Incolla qui il testo (min 50 caratteri)…"></textarea>
    </div>
    <div class="uprow"><button class="primary" id="btnText" type="button">Genera da testo</button></div>
  </div>

  <div class="card">
    <h2><span class="step">3</span> Lezioni generate</h2>
    <div class="urlrow" style="margin-bottom:8px">
      <input id="lessonSearch" placeholder="Cerca una lezione…" oninput="filterLessons()">
    </div>
    <div id="lessons"></div>
    <details style="margin-top:12px">
      <summary>Archivio lezioni (<span id="archiveCount">0</span>)</summary>
      <div id="archived" style="margin-top:8px"></div>
    </details>
    <h2 style="margin-top:16px">File unici (HTML singolo)</h2>
    <div id="singles"></div>
  </div>

  <div class="card">
    <h2><span class="step">🏆</span> Classifica di classe</h2>
    <div class="urlrow">
      <select id="claSel" onchange="loadClassifica()" style="flex:1;background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px"></select>
      <button class="mini ghost" onclick="loadClassifica()" type="button">🔄 Aggiorna</button>
      <button class="mini ghost" onclick="exportClassifica()" type="button">⬇ CSV</button>
      <button class="mini ghost" onclick="resetClassifica()" type="button" title="Azzera tutta la classifica di classe">🗑 Svuota</button>
    </div>
    <div class="urlrow" style="margin-top:8px">
      <input id="claAddName" placeholder="Nome studente (per aggiungere a mano un risultato)">
      <input id="claAddPts" placeholder="Punti (es. 7/10)" style="max-width:140px">
      <button class="mini" onclick="addManuale()" type="button">Aggiungi</button>
    </div>
    <div id="classifica" style="margin-top:10px"><span class="empty">Nessun risultato ancora: gli studenti lo inviano dal pulsante nel pannello finale della lezione.</span></div>
  </div>

  <div class="card" id="editor" hidden>
    <h2>Modifica slide <span id="edMeta" style="color:var(--mut);font-weight:400;font-size:12px"></span></h2>
    <div class="urlrow">
      <select id="edSel" onchange="ED.idx=+this.value;renderEditor()" style="flex:1;background:#0d1220;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:9px"></select>
      <button class="mini ghost" onclick="document.querySelector('#editor').hidden=true" type="button">Chiudi</button>
    </div>
    <div class="urlrow" style="margin-top:8px"><input id="edTitle" placeholder="Titolo slide" maxlength="200"></div>
    <div class="urlrow" style="margin-top:8px"><input id="edNarr" placeholder="Narrazione (voce legge questo testo)" maxlength="3000"></div>
    <div class="urlrow" style="margin-top:8px"><input id="edQuizQ" placeholder="Domanda quiz (vuoto = nessun quiz)"></div>
    <div class="urlrow" style="margin-top:8px"><input id="edQuizOpts" placeholder="Opzioni, una per riga — * davanti = corretta (es. * Roma)"></div>
    <div class="uprow">
      <button class="mini" onclick="saveEditor(false)" type="button">Salva testo/quiz</button>
      <button class="mini" onclick="saveEditor(true)" type="button" title="Salva e rigenera l'audio di questa slide">Salva + rigenera audio</button>
      <button class="mini ghost" onclick="moveEditor(-1)" type="button" title="Sposta slide indietro">← Sposta</button>
      <button class="mini ghost" onclick="moveEditor(1)" type="button" title="Sposta slide avanti">Sposta →</button>
      <button class="mini ghost" onclick="addEditor()" type="button" title="Aggiungi slide contenuto">+ Aggiungi</button>
      <button class="mini ghost" onclick="delEditor()" type="button" title="Elimina questa slide">✕ Elimina</button>
    </div>
    <div class="upmsg" id="edMsg" hidden></div>
  </div>

  <div class="card">
    <h2>Log di generazione <span id="jobinfo" style="color:var(--mut);font-weight:400;font-size:12px"></span>
      <button class="mini ghost" id="btnCancelQ" type="button" hidden>Svuota coda</button>
      <button class="mini ghost" id="btnCancelJob" type="button" hidden>⏹ Annulla trascrizione</button></h2>
    <div class="urlrow" style="margin-bottom:8px"><span id="progTxt" style="color:var(--warn);font-size:12px"></span></div>
    <pre id="log">Il log apparirà qui durante la generazione…</pre>
  </div>

  <div class="card">
    <h2><span class="step">4</span> Condividi in classe <span style="color:var(--mut);font-weight:400;font-size:12px">(stessa Wi-Fi)</span></h2>
    <div class="lanrow">
      <img id="lanQr" hidden tabindex="0" role="button"
           title="Clicca per vedere il QR a schermo intero"
           alt="QR per aprire la lezione dal telefono">
      <div class="grow">
        <div class="urlrow"><input id="lanUrl" readonly placeholder="Caricamento indirizzo…">
          <button class="mini" id="btnLanCopy" type="button">📋 Copia link</button>
          <button class="mini ghost" id="btnLan" type="button" title="Ricarica indirizzo e cronologia">↻</button></div>
        <div class="upmsg" style="font-size:12px">Gli studenti vedono solo l'indice delle lezioni, non questo pannello.</div>
      </div>
    </div>
    <h2 style="margin-top:14px">Cronologia generazioni
      <button class="mini ghost" onclick="clearHistory()" type="button" title="Cancella la cronologia generazioni">🗑 Svuota</button></h2>
    <div id="hist" style="margin-top:8px;font-size:12px;color:var(--mut)"><div>Nessun job ancora.</div></div>
  </div>


  <div id="qrOv" class="qrOv" hidden role="dialog" aria-modal="true" aria-label="QR a schermo intero">
    <img id="qrOvImg" alt="QR a schermo intero">
    <div class="qru" id="qrOvUrl"></div>
    <button type="button" id="qrOvClose">Chiudi (Esc)</button>
  </div>

  <div class="card">
    <h2>📚 Istruzioni</h2>
    <div class="grid2">
      <div><b>1. Carica</b><br><span class="empty">Trascina qui un documento. Per un audio MP3/M4A/WAV spunta «Trascrizione audio con Whisper».</span></div>
      <div><b>2. Genera</b><br><span class="empty">Scegli durata e livello, poi premi «Carica e genera». Puoi usare anche un link o incollare testo.</span></div>
      <div><b>3. Condividi</b><br><span class="empty">Apri il QR a schermo intero e fai scansione dal telefono. Computer e dispositivi devono essere sulla stessa rete.</span></div>
      <div><b>4. Assistenza</b><br><span class="empty">Se un dispositivo non si collega, premi «Controlla computer e rete». I backup si ripristinano dalla sezione Backup.</span></div>
    </div>
  </div>

  <div class="card">
    <h2>⚙ Impostazioni</h2>
    <div class="grid2">
      <label>Porta server <input id="setPorta" type="number" min="1024" max="65535"></label>
      <label>Upload massimo (MB) <input id="setUpload" type="number" min="1" max="1000"></label>
      <label>Cache audio (MB) <input id="setCache" type="number" min="50" max="2000"></label>
      <label>Modello Whisper <select id="setWhisper">
        <option value="tiny">Veloce (tiny)</option><option value="base">Bilanciato (base)</option>
        <option value="small">Preciso (small)</option></select></label>
      <label>Backup da conservare <input id="setBackupKeep" type="number" min="1" max="100"></label>
      <label>Backup ogni minuti <input id="setBackupInterval" type="number" min="15" max="1440"></label>
      <label>IP fisso (facoltativo) <input id="setLanIp" maxlength="15"></label>
      <label>PIN docente (facoltativo) <input id="setPin" type="password" maxlength="12" inputmode="numeric"></label>
    </div>
    <label class="mini" style="display:flex;gap:6px;align-items:center;margin-top:10px">
      <input type="checkbox" id="clearPin"> Rimuovi il PIN docente</label>
    <div class="uprow"><button class="primary" id="btnSaveSettings" type="button">Salva impostazioni</button>
      <span class="upmsg" id="settingsMsg"></span></div>
  </div>

  <div class="card">
    <h2>🗃 Materiali e spazio</h2>
    <div id="storageInfo" class="opts"></div>
    <div id="allMaterials" style="margin-top:10px"></div>
  </div>

  <div class="card">
    <h2>💾 Backup</h2>
    <div class="uprow"><button class="mini" id="btnBackupNow" type="button">Crea backup ora</button>
      <span class="upmsg" id="backupMsg"></span></div>
    <div id="backupList" style="margin-top:10px"></div>
  </div>

  <div class="card">
    <h2>🛟 Assistenza rapida</h2>
    <div class="uprow">
      <button class="mini" id="btnDiagnostica" type="button"
              onclick="event.preventDefault(); if (typeof runDiagnostica === 'function') runDiagnostica(); else window.open('/api/diagnostica','_blank','noopener');">🔍 Controlla computer e rete</button>
      <button class="mini ghost" onclick="location.href='/api/logs_download'">⬇ Scarica log per assistenza</button>
    </div>
    <pre id="diagnostica" style="display:none"></pre>
  </div>
</div>

<script>
// Tema chiaro/scuro pannello (icona in alto, persistente)
function applyPanelTheme(tt){
  document.documentElement.dataset.theme = tt;
  try{localStorage.setItem('panel-theme', tt);}catch(e){}
  const b = document.querySelector('#btnTheme');
  if(b) b.textContent = tt === 'dark' ? '☀️' : '🌙';
}
let _startTheme = 'dark';
try{_startTheme = localStorage.getItem('panel-theme') || 'dark';}catch(e){}
applyPanelTheme(_startTheme);
const $ = s => document.querySelector(s);
let busy = false;
let serverRetryAt = 0;
let serverRetryStep = 0;
const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const escAttr = s => esc(s).replace(/`/g,'&#96;');

async function api(path, opts) {
  let r;
  try {
    r = await fetch('/api/' + path, opts);
  } catch (e) {
    const err = new Error('Server non raggiungibile. Riavvia AVVIA.bat e attendi alcuni secondi.');
    err.offline = true;
    throw err;
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.reason || j.error || ('Errore ' + r.status));
  if (j.ok === false) throw new Error(j.error || 'Operazione non riuscita.');
  return j;
}

function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

function depChips(d) {
  const map = [
    ['python_docx', 'python-docx (.docx)'],
    ['edge_tts', 'edge-tts (voce)'],
    ['pypdf', 'pypdf (.pdf)'],
    ['youtube_transcript_api', 'youtube-transcript-api (YouTube)'],
    ['faster_whisper', 'Whisper (trascrizione audio locale)'],
    ['ffmpeg', 'ffmpeg (audio)'],
  ];
  return map.map(([k, label]) =>
    `<span class="chip ${d[k] ? 'ok' : 'no'}">${d[k] ? '✓' : '✗'} ${label}</span>`).join('');
}

async function teacherPost(path, body) {
  const send = pin => api(path, { method:'POST', headers:Object.assign(
    {'Content-Type':'application/json'}, pin ? {'X-Teacher-Pin':pin} : {}),
    body:JSON.stringify(body || {}) });
  try { return await send(''); }
  catch (e) {
    if (!/PIN docente non valido/.test(e.message)) throw e;
    const pin = prompt('Inserisci il PIN docente:');
    if (pin === null) throw new Error('Operazione annullata.');
    return send(pin);
  }
}

async function loadSettings() {
  try {
    const j = await api('settings'), s = j.settings || {};
    $('#setPorta').value = s.porta || 8341;
    $('#setUpload').value = s.max_upload_mb || 100;
    $('#setCache').value = s.cache_max_mb || 300;
    $('#setWhisper').value = s.whisper_model || 'base';
    $('#setBackupKeep').value = s.backup_keep || 14;
    $('#setBackupInterval').value = s.backup_interval_min || 60;
    $('#setLanIp').value = s.lan_ip_fisso || '';
    $('#setPin').value = s.pin_configured ? '••••' : '';
    $('#setPin').placeholder = s.pin_configured ? 'Lascia invariato per non rimuoverlo' : 'Nessun PIN';
    $('#clearPin').checked = false;
  } catch (e) { /* conserva i valori già inseriti */ }
}
$('#btnSaveSettings').onclick = async () => {
  const msg = $('#settingsMsg');
  msg.textContent = 'Salvataggio…'; msg.className = 'upmsg';
  try {
    const body = { porta:+$('#setPorta').value, max_upload_mb:+$('#setUpload').value,
      cache_max_mb:+$('#setCache').value, whisper_model:$('#setWhisper').value,
      backup_keep:+$('#setBackupKeep').value,
      backup_interval_min:+$('#setBackupInterval').value,
      lan_ip_fisso:$('#setLanIp').value.trim() };
    const pin = $('#setPin').value.trim();
    if ($('#clearPin').checked) body.pin_docente = '';
    else if (pin && pin !== '••••') body.pin_docente = pin;
    const j = await teacherPost('settings', body);
    msg.textContent = j.restart_required ? 'Salvate: riavvia per applicare la porta.' : '✓ Impostazioni salvate';
    msg.className = 'upmsg ok';
    refresh();
  } catch (e) { msg.textContent = '✗ ' + e.message; msg.className = 'upmsg err'; }
};

async function loadManagement() {
  try {
    const [st, ma] = await Promise.all([api('storage'), api('materials_all')]);
    const s = st.storage || {};
    $('#storageInfo').innerHTML = Object.entries(s).map(([k,v]) =>
      `<span class="chip">${esc(k.replace('_',' '))}: ${fmtSize(v)}</span>`).join('');
    $('#allMaterials').innerHTML = (ma.materials || []).length ? ma.materials.map(m =>
      `<div class="row"><span class="name">${esc(m.name)}</span>
        <span class="meta">${fmtSize(m.size)}</span>
        <span class="badge ${m.generated ? 'exists' : ''}">${m.generated ? 'lezione presente' : 'non generato'}</span>
        ${m.generated ? `<button class="mini ghost" onclick="deleteMaterial('${escAttr(m.name)}')">Elimina materiale</button>` : ''}
      </div>`).join('') : '<div class="empty">Nessun materiale nella cartella.</div>';
  } catch (e) { /* la card resta vuota */ }
}
async function deleteMaterial(name) {
  const confirmName = prompt('Per sicurezza scrivi di nuovo il nome del file da eliminare:\n' + name);
  if (confirmName === null) return;
  if (!confirm('La lezione generata verrà conservata. Eliminare il materiale?')) return;
  try { await teacherPost('material_delete', {name, confirm:confirmName}); await loadManagement(); await refresh(); }
  catch (e) { alert(e.message); }
}

async function loadBackups() {
  const msg = $('#backupMsg');
  try {
    const j = await api('backups'), list = j.backups || [];
    $('#backupList').innerHTML = list.length ? list.map(b =>
      `<div class="row"><span class="name">${esc(b.name.replace('_',' '))}</span>
        <span class="meta">${fmtSize(b.size)}</span><span class="badge">${esc((b.files||[]).join(', '))}</span>
        <button class="mini ghost" onclick="restoreBackup('${escAttr(b.name)}')">↩ Ripristina</button></div>`).join('')
      : '<div class="empty">Nessun backup disponibile.</div>';
  } catch (e) { msg.textContent = e.message; msg.className = 'upmsg err'; }
}
$('#btnBackupNow').onclick = async () => {
  const msg = $('#backupMsg');
  try { const j = await api('backup_create', {method:'POST'}); msg.textContent = '✓ Backup creato'; msg.className='upmsg ok'; await loadBackups(); }
  catch (e) { msg.textContent = '✗ ' + e.message; msg.className='upmsg err'; }
};
async function restoreBackup(name) {
  if (!confirm('Ripristinare classifica e cronologia dal backup ' + name + '?\nVerrà creato prima un backup di sicurezza.')) return;
  try { const j = await teacherPost('backup_restore', {name}); alert('Ripristinati: ' + j.restored.join(', ')); await loadBackups(); }
  catch (e) { alert(e.message); }
}

// ---------------------------------------------------------------- classifica di classe
async function loadClassifica() {
  const box = $('#classifica');
  if (!box) return;
  try {
    const r = await fetch('/api/classifica');
    const j = await r.json();
    const lesson = $('#claSel').value;
    const entry = (j.classifiche || []).find(c => c.lesson === lesson);
    if (!lesson || !entry || !entry.rows.length) {
      box.innerHTML = '<span class="empty">Nessun risultato per questa lezione (o nessuna lezione scelta).</span>';
      return;
    }
    const medal = i => i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : (i + 1);
    box.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:13.5px">'
      + '<tr style="color:var(--mut);text-align:left"><th style="padding:6px 8px">#</th><th>Studente</th><th>Punti</th><th>%</th><th>Minuti</th><th>Quando</th></tr>'
      + entry.rows.map((r2, i) => `<tr style="border-top:1px solid var(--line)">
        <td style="padding:6px 8px">${medal(i)}</td>
        <td style="font-weight:700">${esc(r2.studente)}${r2.completata ? ' <span style="color:var(--ok)">✓</span>' : ''}</td>
        <td>${r2.punti}/${r2.totale}</td><td>${r2.pct}%</td><td>${r2.tempo_min}</td><td style="color:var(--mut)">${esc(r2.t)}</td>
      </tr>`).join('') + '</table>';
  } catch (e) {
    box.innerHTML = '<span class="empty">Errore: ' + esc(e.message) + '</span>';
  }
}
function exportClassifica() {
  const lesson = $('#claSel').value;
  if (!lesson) { alert('Scegli prima una lezione.'); return; }
  const a = document.createElement('a');
  a.href = '/api/classifica_export?lesson=' + encodeURIComponent(lesson);
  a.download = 'classifica-' + lesson.replace(/_lesson$/, '') + '.csv';
  document.body.appendChild(a); a.click(); a.remove();
}
async function resetClassifica() {
  if (!confirm('Azzerare TUTTA la classifica di classe? I risultati degli studenti andranno persi.')) return;
  let r = await fetch('/api/reset_classifica', { method: 'POST' });
  if (r.status === 403) {
    const pin = prompt('Inserisci il PIN docente per azzerare la classifica:');
    if (pin === null) return;
    r = await fetch('/api/reset_classifica', { method: 'POST',
      headers: {'X-Teacher-Pin': pin} });
  }
  if (!r.ok) { alert('Svuotamento fallito: PIN non valido.'); return; }
  loadClassifica();
}
async function addManuale() {
  const lesson = $('#claSel').value;
  const nome = $('#claAddName').value.trim();
  const pm = ($('#claAddPts').value || '').trim().match(/^\s*(\d+)\s*\/\s*(\d+)\s*$/);
  if (!lesson || !nome || !pm) {
    alert('Scegli la lezione, scrivi il nome e i punti nel formato 7/10.');
    return;
  }
  const r = await api('classifica', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson, studente: nome, punti: +pm[1], totale: +pm[2], completata: true, tempo_min: 0 }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) { alert('Errore: ' + (j.error || r.status)); return; }
  $('#claAddName').value = ''; $('#claAddPts').value = '';
  loadClassifica();
}

function lessonRow(l) {
  const mins = Math.max(1, Math.round((l.duration || 0) / 60));
  return `<div class="row" data-lesson-search="${escAttr(((l.title || '') + ' ' + l.name).toLowerCase())}">
    <span class="name">${esc(l.title)}<div class="meta">${fmtSize(l.size)} · ${mins} min</div></span>
    <a class="apri" href="/${escAttr(l.name)}/index.html" target="_blank">Apri →</a>
    <button class="mini ghost" onclick="openEditor('${escAttr(l.name)}')">Modifica</button>
    <button class="mini ghost" onclick="single('${escAttr(l.name)}')">HTML singolo</button>
    <button class="mini ghost" onclick="reaudio('${escAttr(l.name)}')">Rigenera audio</button>
    <button class="mini ghost" onclick="lessonAction('${escAttr(l.name)}','duplicate')" title="Duplica">⧉</button>
    <button class="mini ghost" onclick="lessonAction('${escAttr(l.name)}','rename')" title="Rinomina">✎</button>
    <button class="mini ghost" onclick="lessonAction('${escAttr(l.name)}','archive')" title="Archivia">⌸</button>
    <button class="mini ghost" onclick="lessonAction('${escAttr(l.name)}','delete')" title="Elimina">🗑</button>
  </div>`;
}
function filterLessons() {
  const q = ($('#lessonSearch')?.value || '').trim().toLowerCase();
  document.querySelectorAll('#lessons [data-lesson-search]').forEach(row => {
    row.hidden = q && !row.dataset.lessonSearch.includes(q);
  });
}

async function refresh() {
  if (Date.now() < serverRetryAt) return false;
  try {
    const s = await api('state');
    serverRetryAt = 0;
    serverRetryStep = 0;
    if (s.pipeline_stantia) $('#stale').style.display = 'block';
    $('#statusline').innerHTML =
      `Porta ${esc(s.port)} · server locale` + (s.lan_ip ? ` · da tablet/telefono: <span id="lan">http://${esc(s.lan_ip)}:${esc(s.port)}/</span>` : '');
    $('#deps').innerHTML = depChips(s.deps);
    $('#uptxt').textContent = 'Più file insieme · .docx, .pdf, .txt, .md, .html · massimo ' +
      (s.max_upload_mb || 100) + ' MB per file';
    $('#backupInfo').textContent = s.last_backup ?
      '💾 Ultimo backup automatico: ' + s.last_backup.replace('_', ' ') : '💾 I backup automatici partiranno dopo il primo lavoro.';

    const mats = s.materials;
    $('#materials').innerHTML = mats.length ? mats.map(m => {
      const exists = s.lessons.some(l => l.name === m.lesson);
      return `<div class="row">
        <span class="name">${esc(m.name)}</span>
        <span class="meta">${fmtSize(m.size)}</span>
        <span class="badge ${exists ? 'exists' : ''}">${exists ? 'lezione esistente' : 'caricato'}</span>
        <button class="mini" onclick="gen('${escAttr(m.name)}')">Genera</button>
      </div>`;
    }).join('') : '<div class="empty">Nessun file caricato: trascina qui sopra o usa Sfoglia, poi premi «Carica e genera».</div>';

    $('#lessons').innerHTML = s.lessons.length ? s.lessons.map(lessonRow).join('')
      : '<div class="empty">Nessuna lezione generata ancora.</div>';
    filterLessons();
    $('#archiveCount').textContent = (s.archived || []).length;
    $('#archived').innerHTML = (s.archived || []).length ? s.archived.map(n =>
      `<div class="row"><span class="name">${esc(n.replace(/_lesson$/, ''))}</span>
       <button class="mini" onclick="lessonAction('${escAttr(n)}','restore')">↩ Ripristina</button></div>`).join('')
      : '<div class="empty">Archivio vuoto.</div>';

    // classifica: opzioni lezione (mantieni la selezione corrente se c'è ancora)
    const selC = $('#claSel');
    const prevC = selC.value;
    selC.innerHTML = '<option value="">— scegli lezione —</option>' +
      s.lessons.map(l => `<option value="${escAttr(l.name)}" ${l.name === prevC ? 'selected' : ''}>${esc(l.title)}</option>`).join('');
    if (prevC && s.lessons.some(l => l.name === prevC)) loadClassifica();

    const singles = s.singles || [];
    $('#singles').innerHTML = singles.length ? singles.map(f =>
      `<div class="row">
        <span class="name">${esc(f.stem)}</span>
        <span class="meta">${fmtSize(f.size)}</span>
        <a class="apri" href="/${escAttr(f.name)}" target="_blank" download="${escAttr(f.name)}">Apri / salva →</a>
      </div>`).join('')
      : '<div class="empty">Nessun file unico: spunta "Genera come file HTML unico" qui sopra la prossima volta.</div>';
    return true;
  } catch (e) {
    $('#statusline').textContent = e.message;
    if (e.offline) {
      const delays = [5000, 10000, 20000, 30000];
      const wait = delays[Math.min(serverRetryStep, delays.length - 1)];
      serverRetryStep++;
      serverRetryAt = Date.now() + wait;
    }
    return false;
  }
}

function addLog(lines) {
  const el = $('#log');
  el.textContent = lines.join('\n') + (lines.length ? '\n' : '');
  el.scrollTop = el.scrollHeight;
}

async function pollLog() {
  let last = 0;
  for (;;) {
    const s = await api('log');
    const lines = s.lines.slice(last);
    last = s.lines.length;
    if (lines.length) addLog(lines);
    const info = [];
    if (s.running && s.elapsed != null) info.push(s.elapsed + 's');
    if (s.queued) info.push('coda: ' + s.queued + (s.queue && s.queue[0] ? ' (' + s.queue[0] + ')' : ''));
    $('#jobinfo').textContent = info.length ? '· ' + info.join(' · ') : '';
    $('#btnCancelQ').hidden = !s.queued;
    $('#btnCancelJob').hidden = !s.running;
    if (!s.running) {
      busy = false;
      document.querySelectorAll('button').forEach(b => b.disabled = false);
      if (s.error) addLog(['✗ ' + s.error]);
      refresh();
      return;
    }
    await new Promise(r => setTimeout(r, 1200));
  }
}

async function startJob(path, payload) {
  busy = true;
  addLog(['— nuova richiesta: ' + path]);
  try {
    const j = await api(path, { method: 'POST', headers: {'Content-Type': 'application/json'},
                     body: JSON.stringify(payload) });
    if (j.queued) addLog(['⏳ accodato: partirà dopo quello in corso']);
    else await pollLog();
  } catch (e) { addLog(['✗ ' + e.message]); await refresh(); return; }
}

async function startJobs(items, payloadFn) {
  for (const item of items) {
    await startJob('build', payloadFn(item));
    if (busy) break;
  }
}

$('#btnCancelQ').onclick = async () => {
  await fetch('/api/cancel_queue', { method: 'POST' });
};
$('#btnCancelJob').onclick = async () => {
  if (!confirm('Annullare la trascrizione audio attuale?')) return;
  try { await api('cancel_job', {method:'POST'}); addLog(['— annullamento trascrizione richiesto']); }
  catch (e) { addLog(['✗ ' + e.message]); }
};

function profilo() {
  return { durata: $('#profDurata').value, livello: $('#profLivello').value,
           obiettivo: $('#profObiettivo').value };
}

async function gen(name) {
  if (/\.(mp3|m4a|wav)$/i.test(name) && !$('#whisperAudio').checked) {
    alert('Per generare da audio devi spuntare «Trascrizione audio con Whisper».');
    return;
  }
  startJob('build', { source: name, force: $('#forceAll').checked,
                      bozza: $('#bozzaAll').checked, single: $('#singleAll').checked,
                      whisper: $('#whisperAudio').checked, profilo: profilo() });
}

async function reaudio(lesson) {
  startJob('reaudio?lesson=' + encodeURIComponent(lesson), {});
}

async function single(lesson) {
  const r = await fetch('/api/export_single?lesson=' + encodeURIComponent(lesson));
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { alert('Esportazione fallita: ' + (j.error || r.status)); return; }
  const mb = (j.size / 1048576).toFixed(1);
  alert('HTML singolo pronto (' + mb + ' MB): si apre in una nuova scheda. ' +
        'Puoi salvarlo e usarlo anche senza server (pendrive/email).');
  window.open(j.url, '_blank');
}

async function lessonAction(lesson, action) {
  if (action === 'delete' && !confirm('Eliminare definitivamente «' + lesson + '»? È consigliato archiviarla.')) return;
  let extra = {};
  if (action === 'rename') {
    const title = prompt('Nuovo nome della lezione:', lesson.replace(/_lesson$/, ''));
    if (!title) return;
    extra = {title: title};
  }
  const data = Object.assign({lesson: lesson, action: action}, extra);
  if (action === 'delete') data.confirm = lesson;
  try {
    await api('lesson_action', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    await refresh();
    loadClassifica();
  } catch (e) { alert(e.message); }
}

async function refreshProg() {
  try {
    const p = await api('progress');
    if (p) {
      $('#progTxt').textContent = 'Fase: ' + (p.fase || '') + ' — ' + (p.pct || 0) + '% ' + (p.extra || '');
      $('#btnCancelJob').hidden = !(p.fase || '').toLowerCase().includes('whisper');
    }
  } catch (e) { /* ignora */ }
}
async function clearHistory() {
  if (!confirm('Cancellare la cronologia generazioni?')) return;
  await fetch('/api/clear_history', { method: 'POST' });
  loadLan();
}
setInterval(() => { if (busy) refreshProg(); }, 2000);

$('#btnText').onclick = () => {
  const t = $('#txtBody').value.trim();
  if (t.length < 50) { alert('Incolla almeno 50 caratteri di testo.'); return; }
  startJob('build_text', { text: t, title: $('#txtTitle').value.trim() || 'Materiale incollato',
    force: $('#forceAll').checked, bozza: $('#bozzaAll').checked,
    single: $('#singleAll').checked, profilo: profilo() });
};

async function loadLan() {
  try {
    const j = await api('lan');
    const url = j.url || '';
    $('#lanUrl').value = url || 'LAN non disponibile (stessa Wi-Fi del PC?)';
    const qr = $('#lanQr');
    if (url) {
      qr.src = '/api/qr?url=' + encodeURIComponent(url);  // QR locale: funziona offline
      qr.hidden = false;
      qr.onerror = () => { qr.hidden = true; };
    } else { qr.hidden = true; }
  } catch (e) { $('#lanUrl').value = 'LAN non disponibile'; }
  try {
    const h = await api('history');
    $('#hist').innerHTML = (h.history || []).slice(-8).reverse().map(x =>
      `<div>${esc(x.t)} · ${esc(x.kind)} · ${esc(x.source)} — ${x.ok ? '✓' : '✗'} (${x.secs}s)</div>`).join('')
      || '<div>Nessun job ancora.</div>';
  } catch (e) { /* resta il placeholder */ }
}
function closeQrFullscreen() {
  const ov = $('#qrOv');
  if (ov) ov.hidden = true;
}
function openQrFullscreen() {
  const qr = $('#lanQr');
  if (!qr || qr.hidden || !qr.src) return;
  $('#qrOvImg').src = qr.src;
  $('#qrOvUrl').textContent = $('#lanUrl').value || '';
  $('#qrOv').hidden = false;
  $('#qrOvClose').focus();
}
$('#lanQr').onclick = openQrFullscreen;
$('#lanQr').onkeydown = e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openQrFullscreen(); }
};
$('#qrOvClose').onclick = closeQrFullscreen;
$('#qrOv').onclick = e => { if (e.target === e.currentTarget) closeQrFullscreen(); };
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('#qrOv').hidden) closeQrFullscreen();
});
$('#btnLan').onclick = loadLan;
async function runDiagnostica() {
  const box = $('#diagnostica');
  box.hidden = false;
  box.textContent = 'Controllo in corso…';
  try {
    const d = await api('diagnostica');
    const lines = [];
    lines.push(d.ok ? '✓ Computer pronto per la classe' : '⚠ Controlla i punti seguenti');
    if (d.url_lan) lines.push('• Indirizzo rete: ' + d.url_lan);
    else lines.push('• Indirizzo rete: non rilevato');
    lines.push('• Porta del server: ' + d.port);
    lines.push('• Wi-Fi: ' + (d.wifi_ok ? 'collegata' : 'non disponibile o da controllare'));
    if (d.same_configured_ip === false && d.lan_ip_fisso) {
      lines.push('• Attenzione: l\'IP configurato non coincide con quelli rilevati.');
    }
    if (d.disk_free_gb != null) lines.push('• Spazio disco: ' + d.disk_free_gb + ' GB liberi');
    lines.push('• Firewall: Windows/ antivirus possono chiedere conferma alla prima apertura.');
    if (d.warnings && d.warnings.length) lines.push(...d.warnings.map(x => '• ' + x));
    box.textContent = lines.join('\n');
  } catch (e) { box.textContent = 'Diagnostica non disponibile: ' + e.message; }
}
$('#btnDiagnostica').onclick = runDiagnostica;
$('#btnLanCopy').onclick = async () => {
  const v = $('#lanUrl').value;
  if (!v || v.startsWith('LAN')) return;
  try { await navigator.clipboard.writeText(v); $('#btnLanCopy').textContent = '✓ Copiato'; }
  catch (e) { $('#lanUrl').select(); document.execCommand('copy'); }
  setTimeout(() => { $('#btnLanCopy').textContent = '📋 Copia link'; }, 1600);
};

// ---------------------------------------------------- upload materiale
const upZone = $('#upzone'), upFile = $('#upfile');
let pendingFiles = [];

function upMsg(text, ok) {
  const el = $('#upmsg');
  el.textContent = text;
  el.hidden = !text;
  el.className = ok === null ? 'upmsg' : ok ? 'upmsg ok' : 'upmsg err';
}

function pickUpload(files) {
  files = Array.from(files || []);
  if (!files.length) return;
  const bad = files.filter(f => !/\.(docx|pdf|txt|md|html?|pptx|epub|mp3|m4a|wav)$/i.test(f.name));
  if (bad.length) { upMsg('Formato non supportato: ' + bad.map(f => f.name).join(', '), false); return; }
  if (files.length > 10) { upMsg('Carica al massimo 10 file per volta.', false); return; }
  pendingFiles = files;
  const total = files.reduce((n, f) => n + f.size, 0);
  $('#upname').textContent = files.length === 1 ? files[0].name : files.length + ' materiali';
  $('#upsize').textContent = fmtSize(total);
  $('#uprow').hidden = false;
  upMsg('', null);
}

function clearUpload() {
  pendingFiles = [];
  upFile.value = '';
  $('#uprow').hidden = true;
  upMsg('', null);
}

async function doUpload(andGenerate) {
  if (!pendingFiles.length) return [];
  const audioFiles = pendingFiles.filter(f => /\.(mp3|m4a|wav)$/i.test(f.name));
  if (andGenerate && audioFiles.length && !$('#whisperAudio').checked) {
    upMsg('Per gli audio spunta «Trascrizione audio con Whisper» prima di generare.', false);
    return [];
  }
  upMsg('Caricamento di ' + pendingFiles.length + ' file…', null);
  const fd = new FormData();
  pendingFiles.forEach(f => fd.append('file', f));
  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('Errore ' + r.status));
    const names = j.names || [j.name];
    upMsg('✔ Caricati: ' + names.join(', '), true);
    $('#uprow').hidden = true;
    pendingFiles = [];
    upFile.value = '';
    await refresh();
    if (andGenerate) {
      // Le richieste restano in coda sul server; non si attende la fine di ogni
      // generazione, così più materiali vengono preparati con una sola azione.
      busy = true;
      for (const name of names) {
        const res = await fetch('/api/build', { method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ source: name, force: $('#forceAll').checked,
            bozza: $('#bozzaAll').checked, single: $('#singleAll').checked,
            whisper: $('#whisperAudio').checked, profilo: profilo() }) });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.reason || err.error || ('Avvio generazione fallito: ' + res.status));
        }
      }
      await pollLog();
    }
    return names;
  } catch (e) {
    upMsg('✗ ' + e.message, false);
    return [];
  }
}

upZone.addEventListener('click', e => {
  if (e.target.closest && e.target.closest('button')) return;   // niente dialogo sui bottoni
  upFile.click();
});
upZone.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); upFile.click(); }
});
upZone.addEventListener('dragover', e => { e.preventDefault(); upZone.classList.add('drag'); });
upZone.addEventListener('dragleave', () => upZone.classList.remove('drag'));
upZone.addEventListener('drop', e => {
  e.preventDefault();
  upZone.classList.remove('drag');
  pickUpload(e.dataTransfer.files);
});
upFile.onchange = () => pickUpload(upFile.files);
$('#btnUp').onclick = () => doUpload(false);
$('#btnUpGen').onclick = () => doUpload(true);
$('#btnUpX').onclick = clearUpload;

$('#btnUrl').onclick = () => {
  const u = $('#url').value.trim();
  if (!/^https?:\/\//i.test(u)) { alert('Incolla un indirizzo completo (https://…).'); return; }
  startJob('build', { source: u, force: $('#forceAll').checked,
                      bozza: $('#bozzaAll').checked, single: $('#singleAll').checked,
                      profilo: profilo() });
};

// ---------------------------------------------------- voce anteprima + editor
async function loadVoices() {
  try {
    const r = await fetch('/api/voices');
    const j = await r.json();
    $('#voiceSel').innerHTML = (j.voices || []).map(v =>
      `<option value="${v}"${v === j.current ? ' selected' : ''}>${v.replace('it-IT-', '').replace('MultilingualNeural', '')}</option>`).join('');
  } catch (e) { /* resta vuoto */ }
}

$('#btnVoice').onclick = async () => {
  const msg = $('#voiceMsg'), au = $('#voiceAudio');
  msg.hidden = true; au.hidden = true;
  msg.textContent = 'Sintesi…'; msg.hidden = false; msg.className = 'upmsg';
  try {
    const r = await fetch('/api/tts_preview', { method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: $('#voiceTxt').value, voice: $('#voiceSel').value, rate: $('#rateSel').value }) });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || ('Errore ' + r.status)); }
    const blob = await r.blob();
    au.src = URL.createObjectURL(blob);
    au.hidden = false;
    msg.hidden = true;
    au.play().catch(() => {});
  } catch (e) { msg.textContent = '✗ ' + e.message; msg.className = 'upmsg err'; }
};

let ED = { lesson: null, slides: [], idx: 0 };
async function openEditor(lesson) {
  const r = await fetch('/api/lesson_data?lesson=' + encodeURIComponent(lesson));
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) { alert('Editor fallito: ' + (j.error || r.status)); return; }
  ED = { lesson, slides: j.slides || [], idx: 0 };
  if (!ED.slides.length) { alert('Lezione senza slide.'); return; }
  renderEditor();
  $('#editor').hidden = false;
  $('#editor').scrollIntoView({ behavior: 'smooth' });
}
function renderEditor() {
  const s = ED.slides[ED.idx];
  $('#edSel').innerHTML = ED.slides.map((x, i) =>
    `<option value="${i}"${i === ED.idx ? ' selected' : ''}>${i + 1}. ${(x.title || '').slice(0, 50)}</option>`).join('');
  $('#edTitle').value = s.title || '';
  $('#edNarr').value = s.narration || '';
  const q = s.quiz || {};
  $('#edQuizQ').value = q.q || q.domanda || '';
  const opts = q.opts || q.opzioni || [];
  $('#edQuizOpts').value = opts.map(o => ((o.ok || o.corretta) ? '* ' : '') + (o.t || o.testo || '')).join('\n');
  $('#edMeta').textContent = `Slide ${ED.idx + 1}/${ED.slides.length} · durata ${s.duration || '?'}s` +
    (s.quiz ? '' : ' · (nessun quiz: compila domanda+opzioni per aggiungerlo)');
  $('#edMsg').hidden = true;
}
async function saveEditor(reaudio) {
  const msg = $('#edMsg');
  const lines = $('#edQuizOpts').value.split('\n').map(x => x.trim()).filter(Boolean);
  let quiz = null;
  if ($('#edQuizQ').value.trim() || lines.length) {
    quiz = { domanda: $('#edQuizQ').value.trim(),
             opzioni: lines.map(l => l.startsWith('* ')
               ? { testo: l.slice(2).trim(), corretta: true }
               : { testo: l, corretta: false }) };
  }
  const body = { lesson: ED.lesson, index: ED.idx,
    patch: { title: $('#edTitle').value, narration: $('#edNarr').value,
             ...(quiz ? { quiz } : {}) } };
  msg.textContent = 'Salvataggio…'; msg.hidden = false; msg.className = 'upmsg';
  try {
    const r = await fetch('/api/save_slide', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || ('Errore ' + r.status));
    msg.textContent = '✔ Salvato. ' + (j.warning || '');
    msg.className = 'upmsg ok';
    if (reaudio) {
      msg.textContent = '✔ Salvato. Rigenero audio slide…';
      const r2 = await fetch('/api/reaudio_slide?lesson=' + encodeURIComponent(ED.lesson) + '&index=' + ED.idx, { method: 'POST' });
      const j2 = await r2.json().catch(() => ({}));
      if (!r2.ok) throw new Error(j2.reason || j2.error || ('Errore ' + r2.status));
      busy = true; pollLog();
    } else {
      openEditor(ED.lesson);
    }
  } catch (e) { msg.textContent = '✗ ' + e.message; msg.className = 'upmsg err'; }
}
async function moveEditor(d) {
  const to = ED.idx + d;
  if (to < 0 || to >= ED.slides.length) return;
  const r = await fetch('/api/move_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson: ED.lesson, from: ED.idx, to }) });
  if (!r.ok) { alert('Spostamento fallito'); return; }
  ED.idx = to; openEditor(ED.lesson);
}
async function addEditor() {
  const t = prompt('Titolo nuova slide:'); if (t === null) return;
  const n = prompt('Narrazione (voce legge questo testo):') || '';
  const r = await fetch('/api/add_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson: ED.lesson, title: t, narration: n }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) { alert('Aggiunta fallita: ' + (j.error || r.status)); return; }
  ED.idx = j.pos || 0; openEditor(ED.lesson);
}
async function delEditor() {
  if (!confirm('Eliminare la slide ' + (ED.idx + 1) + '?')) return;
  const r = await fetch('/api/delete_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson: ED.lesson, index: ED.idx }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) { alert('Eliminazione fallita: ' + (j.error || r.status)); return; }
  ED.idx = 0; openEditor(ED.lesson);
}
document.querySelector('#btnTheme').onclick = () =>
  applyPanelTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
loadVoices();

refresh();
loadLan();
loadSettings();
loadManagement();
loadBackups();
setInterval(() => { if (!busy) refresh(); }, 4000);
</script>
</body>
</html>
"""
