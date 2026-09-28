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

// ---------------------------------------------------------------- PIN docente
// Il PIN protegge TUTTE le operazioni che modificano qualcosa (non solo due
// endpoint): viene mandato su ogni richiesta al pannello e, se il server lo
// rifiuta, chiesto una volta sola e ricordato per la sessione.
let TEACHER_PIN = '';
try { TEACHER_PIN = sessionStorage.getItem('teacher-pin') || ''; } catch (e) {}

function withPin(opts) {
  opts = opts || {};
  if (!TEACHER_PIN) return opts;
  const h = Object.assign({}, opts.headers || {}, {'X-Teacher-Pin': TEACHER_PIN});
  return Object.assign({}, opts, {headers: h});
}

async function askPin() {
  const pin = prompt('Inserisci il PIN docente:');
  if (pin === null) throw new Error('Operazione annullata.');
  TEACHER_PIN = pin;
  try { sessionStorage.setItem('teacher-pin', pin); } catch (e) {}
  return pin;
}

async function pfetch(path, opts) {
  // fetch con PIN + retry una volta se il server chiede il PIN.
  opts = withPin(opts);
  let r = await fetch(path, opts);
  if (r.status !== 403) return r;
  let msg = '';
  try { msg = (await r.clone().json()).error || ''; } catch (e) {}
  if (!/PIN docente/.test(msg)) return r;
  await askPin();
  opts = withPin(opts);
  return fetch(path, opts);
}

async function api(path, opts) {
  let r;
  try {
    r = await pfetch('/api/' + path, opts);
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
  return api(path, { method: 'POST', headers: {'Content-Type': 'application/json'},
                     body: JSON.stringify(body || {}) }); }

async function loadSettings() {
  try {
    const j = await api('settings'), s = j.settings || {};
    $('#setPorta').value = s.porta || 8341;
    $('#setUpload').value = s.max_upload_mb || 100;
    $('#setCache').value = s.cache_max_mb || 300;
    $('#setWhisper').value = s.whisper_model || 'base';
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
      lan_ip_fisso:$('#setLanIp').value.trim() };
    const pin = $('#setPin').value.trim();
    if ($('#clearPin').checked) body.pin_docente = '';
    else if (pin && pin !== '••••') body.pin_docente = pin;
    const j = await teacherPost('settings', body);
    if (body.pin_docente !== undefined) TEACHER_PIN = body.pin_docente || '';
    else if (pin && pin !== '••••') TEACHER_PIN = pin;
    try { sessionStorage.setItem('teacher-pin', TEACHER_PIN); } catch (e) {}
    msg.textContent = j.restart_required ? 'Salvate: riavvia per applicare la porta.' : '✓ Impostazioni salvate';
    msg.className = 'upmsg ok';
    refresh();
  } catch (e) { msg.textContent = '✗ ' + e.message; msg.className = 'upmsg err'; }
};

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
  const r = await pfetch('/api/reset_classifica', { method: 'POST' });
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
    window._stateLessons = s.lessons || [];
    serverRetryAt = 0;
    serverRetryStep = 0;
    if (s.pipeline_stantia) $('#stale').style.display = 'block';
    $('#statusline').innerHTML =
      `Porta ${esc(s.port)} · server locale` + (s.lan_ip ? ` · da tablet/telefono: <span id="lan">http://${esc(s.lan_ip)}:${esc(s.port)}/</span>` : '');
    $('#deps').innerHTML = depChips(s.deps);
    $('#uptxt').textContent = 'Più file insieme · .docx, .pdf, .txt, .md, .html · massimo ' +
      (s.max_upload_mb || 100) + ' MB per file';

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
  // cursore MONOTONO assoluto fornito dal server: prima si indicizzava
  // s.lines (finestra scorrevole di 200 righe dentro un deque di 500), quindi
  // in ogni build lunga le righe saltavano o comparivano due volte.
  let cursor = -1;
  for (;;) {
    const s = await api('log?from=' + cursor);
    if (s.lines && s.lines.length) addLog(s.lines);
    if (s.cursor != null) cursor = s.cursor;
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
    // pollLog gira comunque, anche in coda: senza questo `busy` restava true
    // per sempre e i pulsanti "Svuota coda" / "Annulla" morivano.
    await pollLog();
  } catch (e) { addLog(['✗ ' + e.message]); busy = false; await refresh(); }
}


$('#btnCancelQ').onclick = async () => {
  await pfetch('/api/cancel_queue', { method: 'POST' });
};
$('#btnCancelJob').onclick = async () => {
  if (!confirm('Annullare la trascrizione audio attuale?')) return;
  try { await api('cancel_job', {method:'POST'}); addLog(['— annullamento trascrizione richiesto']); }
  catch (e) { addLog(['✗ ' + e.message]); }
};

function profilo() {
  return { durata: $('#profDurata').value, livello: $('#profLivello').value,
           obiettivo: $('#profObiettivo').value,
           accessibilita: $('#profAccess').value };
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

// ------------------------------------------------- lezione da mostrare agli alunni
// Un'unica finestra con tutte le lezioni: il docente sceglie QUALE lezione far
// vedere e gli alunni la ricevono intera (dall'inizio alla fine), non a pezzi.
let _focusSel = null;

function focusLabel(l) { return l.title || lessonTitle(l.name); }
function lessonTitle(name) {
  return (name || '').replace(/_lesson$/, '').replace(/_/g, ' ');
}
function renderFocusList() {
  const box = $('#focusList');
  if (!box) return;
  const q = ($('#focusSearch').value || '').trim().toLowerCase();
  const all = (window._stateLessons || []);
  const hits = all.filter(l => !q
    || ((l.title || '') + ' ' + l.name).toLowerCase().includes(q));
  if (!hits.length) {
    box.innerHTML = '<div class="fempty">' +
      (all.length ? 'Nessuna lezione trovata.' : 'Nessuna lezione generata ancora.') + '</div>';
    return;
  }
  box.innerHTML = hits.map(l => {
    const mins = l.duration ? Math.max(1, Math.round(l.duration / 60)) : null;
    const sel = _focusSel && _focusSel.name === l.name;
    return '<button type="button" data-lesson="' + escAttr(l.name) + '" class="' + (sel ? 'sel' : '') + '">'
      + '<span class="fkind">📚</span>'
      + '<span class="ftitle">' + esc(l.title || lessonTitle(l.name))
      + (mins ? ' · ' + mins + ' min' : '') + '</span></button>';
  }).join('');
  box.querySelectorAll('button[data-lesson]').forEach(b => {
    b.onclick = () => selectFocus(b.dataset.lesson);
  });
}
async function selectFocus(name) {
  const l = (window._stateLessons || []).find(x => x.name === name);
  if (!l) return;
  // la lezione scelta diventa anche quella "in classe": il QR principale
  // della card Condividi punterà a lei, così alunni e docente sono allineati
  try {
    const r = await pfetch('/api/lesson_action', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lesson: name, action: 'share' })
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) { alert('Condivisione fallita: ' + (j.error || r.status)); return; }
  } catch (e) { alert('Condivisione fallita: ' + e.message); return; }
  _focusSel = l;
  renderFocusList();
  closeFocusPicker();   // scelta fatta: la finestra si chiude da sola
  loadLan();            // ricarica link/QR principali (ora puntano alla lezione)
  updateFocusQr();
}
function updateFocusQr() {
  const url = $('#focusUrl'), qr = $('#focusQr');
  if (!url || !qr) return;
  const lan = $('#lanUrl').value || '';
  const m = /^https?:\/\/[^\/]+:\d+/.exec(lan);
  const link = (_focusSel && m) ? (m[0] + '/' + _focusSel.name + '/index.html') : '';
  url.value = link || (_focusSel ? 'LAN non disponibile (stessa Wi-Fi del PC?)' : '');
  if (link) {
    qr.src = '/api/qr?scale=10&url=' + encodeURIComponent(link) + '&t=' + Date.now();
    qr.hidden = false;
    qr.onerror = () => {
      qr.hidden = true;
      url.value = link + '  (QR non caricato: copia il link con 📋)';
    };
  } else qr.hidden = true;
  const now = $('#focusNow');
  if (now) {
    now.textContent = link
      ? 'Agli alunni viene mostrata la lezione completa: ' + focusLabel(_focusSel) + ' — ' + link
      : (_focusSel
        ? 'Lezione scelta: ' + focusLabel(_focusSel) + ' (collega il PC alla stessa Wi-Fi per ottenere link e QR)'
        : 'Nessuna lezione scelta: premi il tasto per sceglierne una.');
    now.hidden = false;
  }
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
  await pfetch('/api/clear_history', { method: 'POST' });
  loadLan();
}
async function refreshPlayer() {
  if (!confirm('Riscrivere il player (grafica, script) di tutte le lezioni?\n\n'
             + 'Audio, sottotitoli e contenuti non vengono toccati.')) return;
  const m = $('#maintMsg');
  m.textContent = 'Aggiorno il player…';
  try {
    const j = await teacherPost('refresh_player', {});
    m.textContent = j.messaggio || 'Player aggiornato.';
    refresh();
  } catch (e) { m.textContent = '✗ ' + e.message; }
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
      // cache-buster + QR grande: se il server cambia IP il QR non resta vecchio
      qr.src = '/api/qr?scale=8&url=' + encodeURIComponent(url) + '&t=' + Date.now();
      qr.hidden = false;
      // se il QR non si carica, NON nasconderlo in silenzio: spiega il perché
      qr.onerror = () => {
        qr.hidden = true;
        $('#lanUrl').value = url + '  (QR non caricato: copia il link con 📋)';
      };
    } else { qr.hidden = true; }
    const w = $('#lanWarn');
    if (w) {
      if (j.warning) { w.textContent = '⚠ ' + j.warning; w.hidden = false; }
      else w.hidden = true;
    }
  } catch (e) { $('#lanUrl').value = 'LAN non disponibile'; }
  updateFocusQr();
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
  const fq = $('#focusQr');
  // Se il QR dell'attività singola è visibile, ingrandisce quello
  const src = (fq && !fq.hidden && fq.src) ? fq.src : (qr && !qr.hidden && qr.src ? qr.src : '');
  const label = (fq && !fq.hidden && fq.src) ? ($('#focusUrl').value || '') : ($('#lanUrl').value || '');
  if (!src) return;
  $('#qrOvImg').src = src;
  $('#qrOvUrl').textContent = label;
  $('#qrOv').hidden = false;
  $('#qrOvClose').focus();
}
$('#lanQr').onclick = openQrFullscreen;
$('#lanQr').onkeydown = e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openQrFullscreen(); }
};
$('#focusQr').onclick = openQrFullscreen;
$('#focusQr').onkeydown = e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openQrFullscreen(); }
};
$('#qrOvClose').onclick = closeQrFullscreen;
$('#qrOv').onclick = e => { if (e.target === e.currentTarget) closeQrFullscreen(); };
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (!$('#qrOv').hidden) { closeQrFullscreen(); return; }
    if ($('#focusOv') && !$('#focusOv').hidden) closeFocusPicker();
  }
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
function copyText(v, btn) {
  if (!v || v.startsWith('LAN')) return;
  const done = () => {
    if (!btn) return;
    const old = btn.textContent;
    btn.textContent = '✓ Copiato';
    setTimeout(() => { btn.textContent = old; }, 1600);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(v).then(done).catch(() => {
      const inp = (btn && btn.id === 'btnFocusCopy') ? $('#focusUrl') : $('#lanUrl');
      if (inp) { inp.select(); document.execCommand('copy'); }
      done();
    });
  } else {
    const inp = (btn && btn.id === 'btnFocusCopy') ? $('#focusUrl') : $('#lanUrl');
    if (inp) { inp.select(); document.execCommand('copy'); }
    done();
  }
}
$('#btnLanCopy').onclick = () => copyText($('#lanUrl').value, $('#btnLanCopy'));
$('#btnFocusCopy').onclick = () => copyText($('#focusUrl').value, $('#btnFocusCopy'));
$('#btnFocusOpen').onclick = () => {
  const u = $('#focusUrl').value || '';
  if (u.startsWith('http')) window.open(u, '_blank');
};
if ($('#focusSearch')) $('#focusSearch').oninput = renderFocusList;

// ---------------------------------------------------- tasto "lezione da mostrare"
function openFocusPicker() {
  const ov = $('#focusOv');
  if (!ov) return;
  ov.hidden = false;
  renderFocusList();
  const s = $('#focusSearch');
  if (s) setTimeout(() => s.focus(), 30);
}
function closeFocusPicker() {
  const ov = $('#focusOv');
  if (ov) ov.hidden = true;
}
$('#btnFocusPick').onclick = openFocusPicker;
$('#focusOvClose').onclick = closeFocusPicker;
$('#focusOv').onclick = e => { if (e.target === e.currentTarget) closeFocusPicker(); };
$('#btnFocusQr').onclick = () => {
  if ($('#focusQr') && !$('#focusQr').hidden) openQrFullscreen();
  else openFocusPicker();
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
    const r = await pfetch('/api/upload', { method: 'POST', body: fd });
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
        const res = await pfetch('/api/build', { method: 'POST', headers: {'Content-Type': 'application/json'},
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
    const r = await pfetch('/api/tts_preview', { method: 'POST',
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
    const r = await pfetch('/api/save_slide', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || ('Errore ' + r.status));
    msg.textContent = '✔ Salvato. ' + (j.warning || '');
    msg.className = 'upmsg ok';
    if (reaudio) {
      msg.textContent = '✔ Salvato. Rigenero audio slide…';
      const r2 = await pfetch('/api/reaudio_slide?lesson=' + encodeURIComponent(ED.lesson) + '&index=' + ED.idx, { method: 'POST' });
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
  const r = await pfetch('/api/move_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson: ED.lesson, from: ED.idx, to }) });
  if (!r.ok) { alert('Spostamento fallito'); return; }
  ED.idx = to; openEditor(ED.lesson);
}
async function addEditor() {
  const t = prompt('Titolo nuova slide:'); if (t === null) return;
  const n = prompt('Narrazione (voce legge questo testo):') || '';
  const r = await pfetch('/api/add_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ lesson: ED.lesson, title: t, narration: n }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) { alert('Aggiunta fallita: ' + (j.error || r.status)); return; }
  ED.idx = j.pos || 0; openEditor(ED.lesson);
}
async function delEditor() {
  if (!confirm('Eliminare la slide ' + (ED.idx + 1) + '?')) return;
  const r = await pfetch('/api/delete_slide', { method: 'POST', headers: {'Content-Type': 'application/json'},
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
setInterval(() => { if (!busy) refresh(); }, 4000);