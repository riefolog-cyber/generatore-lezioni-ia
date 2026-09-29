
const $ = s => document.querySelector(s);
// ------------------------------------------------------------ guardie di compatibilità
// Se index.html è vecchio (cache del browser) e main.js è nuovo, gli elementi
// mancano: senza guardie il player resta una pagina bianca con pochi tasti.
const _btn = id => document.getElementById(id);
if (!_btn('slide') || !_btn('dots')) {
  document.body.innerHTML = '<div style="padding:40px;font-family:sans-serif">'
    + '<h2>⚠ Pagina non aggiornata</h2><p>Il browser ha caricato una vecchia '
    + 'versione dalla cache.</p>'
    + '<button onclick="location.reload(true)" style="padding:12px 22px;font-size:16px;cursor:pointer">'
    + '🔄 Ricarica la pagina</button></div>';
  throw new Error('index.html obsoleto: ricaricare la pagina');
}
function _safe(id) {
  const n = _btn(id);
  if (!n) console.warn('elemento mancante (cache?):', id);
  return n;
}
const data = window.LESSON_DATA || { titolo: 'Lezione', slides: [] };
const slides = data.slides;
if (!slides.length) {
  document.body.innerHTML = '<div style="padding:40px;font-family:sans-serif">'
    + '<h2>⚠ Dati della lezione non trovati</h2><p>Il file lesson-data.js è '
    + 'assente o non valido: rigenera la lezione.</p>'
    + '<button onclick="location.reload(true)" style="padding:12px 22px;font-size:16px;cursor:pointer">'
    + '🔄 Ricarica la pagina</button></div>';
  throw new Error('lesson-data.js vuoto o non valido');
}
// Impronta del contenuto DENTRO la chiave di ripresa: se la lezione viene
// rigenerata (stesso titolo, slide diverse) la posizione salvata della
// versione precedente non deve essere riapplicata — altrimenti il player
// riparte da metà percorso e la prima pagina "non si vede".
function _fingerprint(s2) {
  let hh = 5381;
  for (let j = 0; j < s2.length; j++) hh = ((hh << 5) + hh + s2.charCodeAt(j)) | 0;
  return (hh >>> 0).toString(36);
}
const DATA_KEY = 'lesson-' + (data.titolo || 'lezione') + '-'
  + slides.length + 's-'
  + _fingerprint(slides.map(s2 => s2.narration || '').join('|'));
const IS_LOCAL_FILE = location.protocol === 'file:';
// nome dello studente per il report del docente (una sola volta, poi salvato)
let studentName = '';
try { studentName = localStorage.getItem(DATA_KEY + '-nome') || ''; } catch (e) {}
let cur = 0, results = {};
// streak di risposte corrette consecutive: contatore della serie in corso e
// record personale persistito (per la lezione) in localStorage
var STREAK = { cur: 0, best: 0 };   // var: accessibile da console/test
try {
  const bs = JSON.parse(localStorage.getItem(DATA_KEY + '-streak') || 'null');
  if (bs && Number.isInteger(bs.best) && bs.best >= 0) STREAK.best = bs.best;
} catch (e) {}
function saveStreak() {
  try { localStorage.setItem(DATA_KEY + '-streak', JSON.stringify({ best: STREAK.best })); } catch (e) {}
}
// tempo di lezione + registro risposte (per l'export del docente)
let sessStart = Date.now(), sessMs = 0;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) sessMs += Date.now() - sessStart; else sessStart = Date.now();
  // scheda in secondo piano: aurora ferma, niente consumo a video acceso
  document.body.classList.toggle('idle', document.hidden || isIdle());
});
// --- gating dell'aurora: si riattiva a ogni interazione, si ferma dopo 2,5 s
let _idleTimer = null, _lastAct = Date.now();
function isIdle() { return Date.now() - _lastAct > 2500; }
function wake() {
  _lastAct = Date.now();
  document.body.classList.remove('idle');
  clearTimeout(_idleTimer);
  _idleTimer = setTimeout(() => document.body.classList.add('idle'), 2500);
}
['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(ev =>
  document.addEventListener(ev, wake, { passive: true }));
wake();
let slideEnteredAt = Date.now(), lastAnswerAt = 0;
var LOG = [];   // var: accessibile anche da console/test (window.LOG)
function logAnswer(slideIdx, tipo, esito) {
  askName();   // alla prima risposta, non all'apertura della pagina
  const now = Date.now();
  LOG.push({ studente: studentName, slide: slideIdx + 1, tipo,
             esito: !!esito,
             tempo: Math.round((now - (lastAnswerAt || slideEnteredAt)) / 1000) });
  lastAnswerAt = now;
  // Segnalibro automatico su errore. Prima la slide finiva nel percorso di
  // ripasso solo se lo studente premeva a mano 🔖: in una lezione da 40
  // attività, le slide in cui aveva sbagliato restavano fuori dal ripasso,
  // cioè il ripasso non guardava dove l'errore era avvenuto.
  if (!esito && tipiAttivita(slideIdx)) markReview(slideIdx, true);
}
function tipiAttivita(i) {
  return (slides[i] && slides[i].blocks || []).some(b =>
    ACT_TYPES.some(t => b[t]) || (b.quiz && b.quiz.q));
}
// Qualcosa da fare su questa slide? Comprende anche le flashcard, che non
// sono conteggiate nel punteggio (sono esercizio facoltativo) ma sono comunque
// cliccabili. Non governa piu' l'avanzamento automatico (non esiste piu'):
// resta un informatore sul tipo di slide, usato dai test e a disposizione del
// docente.
function slideHaAttivita(i) {
  return (slides[i] && slides[i].blocks || []).some(b =>
    ACT_TYPES.some(t => b[t]) || b.flashcards || (b.quiz && b.quiz.q));
}
let animDir = 'init', celebrated = false;
// Velocità di riproduzione: LEGGIMI.md prometteva 0,8×/1×/1,25× ma il codice
// aveva `const rate = 1` fisso e il selettore rimosso: la promessa non era
// stata mantenuta. Serve anche come supporto BES/DSA (e in aula, per rivedere
// un passaggio velocemente).
// NOTA: le funzioni che toccano `audio` vengono richiamate piu' in basso,
// DOPO `const audio = new Audio()`: dichiarate qui, il `audio.addEventListener`
// would eseguirebbe prima dell'inizializzazione ("Cannot access 'audio' before
// initialization") e l'intero player si fermava.
const RATE_VALORI = [0.8, 1, 1.25, 1.5];
let rateIdx = 1;
try {
  const salvata = +(localStorage.getItem(DATA_KEY + '-rate') || 1);
  const k = RATE_VALORI.indexOf(salvata);
  if (k >= 0) rateIdx = k;
} catch (e) {}
function applyRate() {
  const r = RATE_VALORI[rateIdx];
  const b = _safe('btnSpeed');
  if (b) {
    b.textContent = (r === 1 ? '1' : String(r)) + '×';
    b.classList.toggle('on', r !== 1);
    b.title = 'Velocita audio: ' + r + '× (clic per cambiare)';
  }
  try { localStorage.setItem(DATA_KEY + '-rate', String(r)); } catch (e) {}
  if (typeof audio !== 'undefined' && audio) audio.playbackRate = r;
}

// Riproduzione continua: DISATTIVATA di proposito. Prima l'audio finito portava
// da solo alla slide successiva, anche su quelle di solo contenuto: lo
// studente non decideva quando ripartire e non poteva fermarsi a rileggere
// quello che aveva appena ascoltato senza toccare lo schermo. Ora la scelta
// e' sempre sua: si prosegue con il tasto "Avanti" o con la freccia.
var RIPASSO = [], RIPASSO_POS = 0;   // percorso di ripasso sulle slide segnalate 🔖
let lastSlideTime = null;            // per il tempo per slide del report docente
const slideTimes = {};               // slide (0-based) -> ms trascorsi
// riprendi da dove eri + segnalibri "da rivedere" (persistiti in localStorage)
var review = new Set();   // var: accessibile da console/test
try { review = new Set(JSON.parse(localStorage.getItem(DATA_KEY + '-review') || '[]')); } catch (e) {}
function saveReview() {
  try { localStorage.setItem(DATA_KEY + '-review', JSON.stringify([...review])); } catch (e) {}
}
// Parametro ?attivita=N: da quale slide parte il percorso. Va letto PRIMA di
// restorePos (che altrimenti riporterebbe l'alunno sulla slide memorizzata).
let FOCUS_SLIDE = -1;
try {
  const m = /[?&]attivita=(\d+)/.exec(location.search || '');
  if (m) {
    const n = parseInt(m[1], 10);
    if (Number.isInteger(n) && n >= 0) FOCUS_SLIDE = n;
  }
} catch (e) {}
(function restorePos() {
  if (FOCUS_SLIDE >= 0) return;   // focus: si parte dall'attività, non dalla cronologia
  try {
    const saved = JSON.parse(localStorage.getItem(DATA_KEY) || 'null');
    if (saved && Number.isInteger(saved.slide) && saved.slide >= 0 && saved.slide < slides.length) {
      cur = saved.slide;
    }
  } catch (e) {}
})();
function savePos() {
  try { localStorage.setItem(DATA_KEY, JSON.stringify({ slide: cur })); } catch (e) {}
}
const ACT_TYPES = ['quiz', 'match', 'vf', 'seq', 'compila', 'scenario', 'errore', 'classifica'];
// Modalità "una sola attività": il docente condivide ?attivita=N e gli alunni
// vedono SOLO il percorso che parte da quella slide (niente mappa, ricerca,
// esame né salti a slide esterne): cliccando Avanti si prosegue il modulo.
let focusAct = (FOCUS_SLIDE >= 0 && FOCUS_SLIDE < slides.length) ? FOCUS_SLIDE : -1;
const FOCUS = focusAct >= 0;
// Percorso dell'alunno in focus: dalla slide dell'attività fino alla fine.
const FOCUS_STOP = FOCUS ? focusAct : -1;
const activeIdx = slides.map((s, i) =>
  (s.blocks || []).some(b => ACT_TYPES.some(k => b[k])) ? i : -1).filter(i => i >= 0);
// slide di apertura dei moduli: alimentano la barra di navigazione dei moduli
const MODNAV = slides.map((s, i) => {
  const cb = (s.blocks || []).find(x => x.callout && /^Modulo\s*\d+\s*di/i.test(x.callout));
  if (!cb) return null;
  const n = (cb.callout.match(/^Modulo\s*(\d+)\s*di/i) || [0, '1'])[1];
  const label = (s.title || '').replace(/^Modulo\s*\d+\s*[-–—]\s*/i, '').trim() || ('Modulo ' + n);
  return { i, n: +n, label };
}).filter(Boolean);
const LAST = slides.length - 1;

// Modalità operative: studente (predefinita) ed esame.
let lessonMode = 'student';
let examOrder = [], examPos = 0, examTimer = null, examEndsAt = 0, examDone = false;
function setLessonMode(next) {
  lessonMode = next === 'exam' ? 'exam' : 'student';
  document.documentElement.dataset.mode = lessonMode;
  try { localStorage.setItem(DATA_KEY + '-mode', lessonMode); } catch (e) {}
  const eb = _safe('btnModeExam');
  if (eb) eb.setAttribute('aria-pressed', lessonMode === 'exam' ? 'true' : 'false');
  const banner = _safe('examBanner'), timer = _safe('examTimer');
  if (lessonMode !== 'exam') {
    if (timer) timer.hidden = true;
    if (banner) banner.hidden = true;
    if (examTimer) { clearInterval(examTimer); examTimer = null; }
  }
}
function fmtExam(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}
function startExam() {
  if (!activeIdx.length) { alert('Questa lezione non contiene attività interattive.'); return; }
  if (!confirm('Avviare la modalità esame? Le attività verranno mescolate e il tempo sarà di 15 minuti.')) return;
  setLessonMode('exam');
  results = {}; celebrated = false; examDone = false; examOrder = shuffle(activeIdx.slice());
  examPos = 0; examEndsAt = Date.now() + 15 * 60 * 1000;
  const timer = _safe('examTimer'), banner = _safe('examBanner');
  if (timer) { timer.hidden = false; timer.textContent = '⏱ 15:00'; }
  if (banner) { banner.hidden = false; banner.textContent = 'Esame: un tentativo, ordine casuale e feedback nascosto fino alla fine.'; }
  go(examOrder[0]);
  if (examTimer) clearInterval(examTimer);
  examTimer = setInterval(() => {
    const left = examEndsAt - Date.now();
    if (timer) timer.textContent = '⏱ ' + fmtExam(left);
    if (left <= 0 && !examDone) finishExam();
  }, 1000);
}
function finishExam() {
  if (examDone) return;
  examDone = true;
  if (examTimer) { clearInterval(examTimer); examTimer = null; }
  const timer = _safe('examTimer'), banner = _safe('examBanner');
  if (timer) timer.textContent = '⏱ 00:00';
  if (banner) { banner.hidden = false; banner.textContent = 'Esame terminato. Il punteggio è disponibile nel riepilogo.'; }
  go(LAST);
}
function goExam(delta) {
  if (lessonMode !== 'exam' || !examOrder.length) return go(cur + delta);
  const next = examPos + delta;
  if (delta > 0 && next >= examOrder.length) { finishExam(); return; }
  examPos = (next + examOrder.length) % examOrder.length;
  go(examOrder[examPos]);
}

// ------------------------------------------------------------ tema
const btnTheme = _safe('btnTheme') || el('div');
function applyTheme(tt) {
  document.documentElement.dataset.theme = tt;
  try { localStorage.setItem('lesson-theme', tt); } catch (e) {}
  if (btnTheme) { const _ic = btnTheme.querySelector('.ic'); if (_ic) _ic.textContent = tt === 'dark' ? '☀️' : '🌙'; }
}
let startTheme = 'dark';
try { startTheme = localStorage.getItem('lesson-theme') || 'dark'; } catch (e) {}
applyTheme(startTheme);
if (btnTheme) btnTheme.onclick = () =>
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

// ------------------------------------------------------------ helper DOM
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
// Escape HTML per l'UNICO punto in cui il player scrive markup costruito
// (la stampa del report). Tutto il resto passa da el() -> textContent, quindi
// non è iniettabile: qui invece si concatena dentro document.write, e i dati
// (titolo della slide generato dall'LLM, nome dello studente da localStorage)
// arrivano da sorgenti esterne.
function escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
// Cella CSV: neutralizza la formula injection (= + - @ iniziali).
function csvCell(v) {
  let s = String(v == null ? '' : v);
  if (s && '=+-@\t\r'.indexOf(s[0]) >= 0) s = "'" + s;
  return '"' + s.replace(/"/g, '""') + '"';
}
function shuffle(a) {
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}
// Ordine delle opzioni STABILE fra le visite alla stessa slide.
// Prima shuffle() veniva rieseguito a ogni render(): lo studente rispondeva,
// premeva Indietro e poi Avanti, e le opzioni erano in ordine diverso —
// rispondeva alla posizione, non al contenuto. Il guard "la risposta giusta
// non è sempre la prima" di shuffleOpts era vanificato proprio dal re-shuffle.
// La cache è per (slide, blocco, tipo) e si invalida solo se i dati cambiano.
const ORDER_CACHE = new Map();
const ORDER_CACHE_MAX = 400;   // tetto di memoria: oltre, si scarta la voce piu' vecchia
function stableOrder(idx, bidx, kind, n, factory) {
  const key = idx + ':' + bidx + ':' + kind + ':' + n;
  let v = ORDER_CACHE.get(key);
  if (v === undefined) {
    v = factory();
    ORDER_CACHE.set(key, v);
    // tetto di memoria: 400 voci sono ~400 array di 4-8 interi, qualche KB.
    // NON si svuota a ogni cambio slide: farlo rimescolava le opzioni a ogni
    // visita (il test `test_ordine_opzioni_stabile` lo intercetta).
    if (ORDER_CACHE.size > ORDER_CACHE_MAX) {
      ORDER_CACHE.delete(ORDER_CACHE.keys().next().value);
    }
  }
  return v;
}
function shuffleOpts(opts) {
  const order = shuffle(opts.map((o, k) => k));
  // La risposta corretta non viene mai mostrata per prima: evita il pattern
  // "la risposta giusta è sempre A", senza cambiare il testo delle opzioni.
  const good = opts.findIndex(o => !!o.ok);
  if (order.length > 1 && order[0] === good) {
    const alt = order.findIndex(k => k !== good);
    [order[0], order[alt]] = [order[alt], order[0]];
  }
  return order;
}

// ------------------------------------------------------------ dots + stat
const dots = $('#dots');
// I pallini di navigazione erano <span> con onclick: irraggiungibili con la
// tastiera, quindi la lezione non si poteva percorrere senza mouse. Ora sono
// <button> con "roving tabindex": Tab entra nel gruppo una volta sola e le
// frecce muovono la selezione, come in uno slider.
slides.forEach((s, i) => {
  const d = document.createElement('button');
  d.type = 'button';
  d.title = (s.icon || '') + ' ' + s.title;
  d.setAttribute('aria-label', 'Vai a: ' + (s.title || ('slide ' + (i + 1))));
  d.tabIndex = i === cur ? 0 : -1;
  d.onclick = () => go(i);
  dots.appendChild(d);
});
dots.addEventListener('keydown', e => {
  const k = e.key;
  if (k !== 'ArrowRight' && k !== 'ArrowLeft' && k !== 'Home' && k !== 'End') return;
  e.preventDefault();
  let n = cur;
  if (k === 'ArrowRight') n = Math.min(slides.length - 1, cur + 1);
  else if (k === 'ArrowLeft') n = Math.max(0, cur - 1);
  else if (k === 'Home') n = 0;
  else n = slides.length - 1;
  go(n);
  const d = dots.children[cur];
  if (d) { d.tabIndex = 0; d.focus(); }
});
function doneCount() {
  let n = 0;
  for (const i of activeIdx) if (results[i]) n++;
  return n;
}
function paintDots() {
  [...dots.children].forEach((d, i) => {
    d.className = i === cur ? 'on' : (results[i] ? 'done' : '');
    d.tabIndex = i === cur ? 0 : -1;
    if (review.has(i)) d.classList.add('rv');
  });
  const n = doneCount();
  const st = _safe('stat');
  if (st) {
    if (n > 0 && activeIdx.length) {
      st.hidden = false;
      st.textContent = '✓ ' + n + '/' + activeIdx.length;
    } else st.hidden = true;
  }
  paintScore();
}

// ------------------------------------------------------------ punteggio
function blankCount(it) {
  return Math.max(1, String(it.frase).split('___').length - 1);
}
function slideTotal(i) {
  let t = 0;
  for (const b of slides[i].blocks || []) {
    if (b.quiz || b.scenario || b.seq || b.errore || b.match || b.flashcards) t += 1;
    else if (b.classifica) t += (b.classifica.items || []).length;
    else if (b.vf) t += b.vf.length;
    else if (b.compila) t += b.compila.reduce((n, it) => n + blankCount(it), 0);
  }
  return t;
}
function attempted(i) {
  return !!results[i] && Object.keys(results[i]).length > 0;
}
function grade(i) {
  const r = results[i] || {};
  let e = 0;
  const log = (tipo, esito) => {
    if (!r._logged) r._logged = {};
    if (r._logged[tipo]) return;
    r._logged[tipo] = true;
    logAnswer(i, tipo, esito);
  };
  for (const b of slides[i].blocks || []) {
    if (b.quiz && r.quiz !== undefined) { e += r.quiz ? 1 : 0; log('quiz', r.quiz); }
    else if (b.scenario && r.scenario !== undefined) { e += r.scenario ? 1 : 0; log('scenario', r.scenario); }
    else if (b.seq && r.seq !== undefined) { e += r.seq === true ? 1 : 0; log('sequenza', r.seq === true); }
    else if (b.errore && r.errore === true) { e += 1; log('errore', true); }
    else if (b.match && r.match !== undefined) { e += r.match === true ? 1 : 0; log('abbinamenti', r.match === true); }
    else if (b.flashcards && r.flash !== undefined) { e += r.flash === true ? 1 : 0; log('flashcards', r.flash === true); }
    else if (b.classifica && r.classifica !== undefined) {
      e += r.classifica;
      log('classifica', r.classifica === (b.classifica.items || []).length);
    }
    else if (b.vf && Array.isArray(r.vf)) {
      const got = r.vf.slice(0, b.vf.length).filter(Boolean).length;
      e += got;
      log('verofalso', b.vf.length === 0 || got === b.vf.length);
    } else if (b.compila && Array.isArray(r.compila)) {
      const tot = b.compila.reduce((n, it) => n + blankCount(it), 0);
      const got = r.compila.slice(0, tot).filter(Boolean).length;
      e += got;
      log('compila', tot === 0 || got === tot);
    }
  }
  return { e, t: slideTotal(i) };
}
// ------------------------------------------------------------ progress hook
// Punto d'aggancio per integrazioni esterne: non invia nulla di per sé.
// Un adattatore può sostituire questo hook quando richiesto.
function reportProgress(_p) { /* nessun invio esterno predefinito */ }
// Punteggio HONESTO: il denominatore è SEMPRE il totale delle attività.
//
// Prima le attività saltate venivano escluse dal denominatore
// (`if (!attempted(i)) continue`): rispondere correttamente a 3 attività su 10
// e ignorare le altre 7 dava 3/3 = 100% di precisione, medaglia d'oro e 5
// stelle. Il numero che il docente leggeva come voto premiava la selezione,
// non l'apprendimento. Ora:
//   punti     = e / t   (t fisso: saltare costa 0 punti)
//   precisione = e / tSvolte  (come va letta: "di quelle che hai provato")
//   copertura  = tSvolte / t  (quanto hai percorso)
function scoreMetrics() {
  let e = 0, t = 0, tSvolte = 0, svolte = 0;
  for (const i of activeIdx) {
    const g = grade(i);
    t += g.t;
    if (attempted(i)) { e += g.e; tSvolte += g.t; svolte++; }
  }
  return {
    e: e, t: t, tSvolte: tSvolte, svolte: svolte, totaleAtt: activeIdx.length,
    pct: t ? Math.round(e / t * 100) : 0,
    pctSvolte: tSvolte ? Math.round(e / tSvolte * 100) : 0,
    copertura: t ? Math.round(tSvolte / t * 100) : 0,
    completo: activeIdx.length > 0 && svolte >= activeIdx.length
  };
}
// esito dell'esame finale, calcolato davvero (prima era solo una stringa
// "soglia 70%" scritta nella slide, mai confrontata con nulla)
function examMetrics() {
  const idx = examIdx();
  if (!idx.length) return null;
  let e = 0, t = 0;
  idx.forEach(i => { const g = grade(i); e += g.e; t += g.t; });
  const svolte = idx.filter(i => attempted(i)).length;
  const soglia = Math.ceil(t * 0.7);
  return { e: e, t: t, svolte: svolte, totale: idx.length, soglia: soglia,
           pct: t ? Math.round(e / t * 100) : 0, superato: e >= soglia };
}
function examIdx() {
  const out = [];
  for (let i = 0; i < slides.length; i++) {
    if ((slides[i].blocks || []).some(b => b.quiz && b.quiz.exam)) out.push(i);
  }
  return out;
}
function paintScore() {
  SCORE_REV++;
  const M = scoreMetrics();
  const st = $('#score');
  if (st && M.t > 0) { st.hidden = false; st.textContent = '⭐ ' + M.e + '/' + M.t; }
  else if (st) st.hidden = true;
  reportProgress(buildProgress());
  // streak: si aggiorna SOLO quando la slide completa è stata corretta (esito
  // affidabile); una slide errata o incompleta azzera la serie
  const sc = _safe('streak');
  if (sc) {
    if (M.tSvolte > 0) {
      STREAK.cur = M.e === M.tSvolte ? STREAK.cur + 1 : 0;
      if (STREAK.cur > STREAK.best) { STREAK.best = STREAK.cur; saveStreak(); }
    }
    if (STREAK.cur >= 2) { sc.hidden = false; sc.textContent = '🔥 ' + STREAK.cur + ' di fila'; }
    else sc.hidden = true;
  }
  checkBadges();
}

// ------------------------------------------------------------ barra moduli
function modnavBar(curI) {
  const bar = el('div', 'modnav');
  bar.appendChild(el('span', 'mnlabel', 'Moduli'));
  MODNAV.forEach(m => {
    const b = el('button', m.i === curI ? 'on' : '', m.n + ' · ' + m.label);
    b.onclick = () => go(m.i);
    bar.appendChild(b);
  });
  return bar;
}

// ------------------------------------------------------------ ripasso segnalibri
function goRipasso() {
  RIPASSO = [...review].sort((a, b) => a - b);
  RIPASSO_POS = 0;
  go(RIPASSO[0]);
  const chip = _btn('rvw');
  if (chip) {
    chip.hidden = false;
    chip.textContent = '🎯 ripasso: ' + RIPASSO.length + ' slide';
    clearTimeout(chip._t);
    chip._t = setTimeout(() => { chip.hidden = true; }, 2600);
  }
}

// L'utente ha chiesto meno movimento? Allora niente animazioni in JS, niente
// confetti, niente re-layout per misurare. Prima questa preferenza era rispettata
// solo dal CSS: i confetti (JavaScript) partivano comunque, e il fallback
// dell'animazione di ingresso forzava un reflow di tutti i blocchi a ogni slide.
const REDUCED_MOTION = !!(window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

// ------------------------------------------------------------ confetti
function confetti() {
  // 90 coriandoli a schermo intero, lanciati a ogni ritorno al pannello
  // finale (il pannello si ri-costruiva a ogni visita).
  if (REDUCED_MOTION || confetti._t) return;
  confetti._t = true;
  const colors = ['#ffd166', '#ff6b6b', '#3ddc84', '#4d9fff', '#b983ff', '#ff9f43'];
  for (let i = 0; i < 90; i++) {
    const c = el('div', 'confetti');
    c.style.left = (Math.random() * 100) + 'vw';
    c.style.background = colors[i % colors.length];
    c.style.animationDuration = (2.2 + Math.random() * 2.4) + 's';
    c.style.animationDelay = (Math.random() * 0.7) + 's';
    c.style.transform = 'rotate(' + Math.floor(Math.random() * 360) + 'deg)';
    document.body.appendChild(c);
    setTimeout(() => c.remove(), 6500);
  }
}

// ------------------------------------------------------------ mappa del percorso
// Le "stazioni" sono le aperture dei moduli (stesse di modnavBar): verde =
// raggiunta, lampeggiante = dove sei ora. Un tacco su una stazione porta lì.
// Le stazioni sono costruite UNA volta sola: la struttura non cambia mai,
// cambiano solo le classi .reached/.now. Prima venivano ricreate a ogni
// navigazione (~21 nodi + 8 closure per cambio slide).
let _mapBuilt = false, _mapStations = null;
function paintMap() {
  const map = _safe('map');
  if (!map) return;
  if (!_mapBuilt) {
    _mapBuilt = true;
    _mapStations = [];
    if (MODNAV.length && slides.length >= 6) {
      MODNAV.forEach(m => {
        const st = el('div', 'st');
        st.appendChild(el('div', 'stico', (slides[m.i] && slides[m.i].icon) || '📘'));
        st.appendChild(el('div', 'stlab', 'M' + m.n + ' · ' + m.label.slice(0, 22)));
        st.onclick = () => go(m.i);
        st.title = m.label;
        map.appendChild(st);
        _mapStations.push({ el: st, i: m.i });
      });
      const stEnd = el('div', 'st');
      stEnd.appendChild(el('div', 'stico', '🏁'));
      stEnd.appendChild(el('div', 'stlab', 'Fine'));
      stEnd.onclick = () => go(LAST);
      map.appendChild(stEnd);
      _mapStations.push({ el: stEnd, i: LAST });
    }
    return;
  }
  if (!_mapStations || !_mapStations.length) return;
  const finito = activeIdx.length > 0 && doneCount() === activeIdx.length;
  for (let k = 0; k < _mapStations.length; k++) {
    const s = _mapStations[k];
    s.el.classList.toggle('now', cur === s.i);
    s.el.classList.toggle('reached', s.i === LAST ? (cur === LAST || finito) : (cur >= s.i));
  }
}

// ------------------------------------------------------------ badge collezionabili
// Sbloccati in base a come giochi, persistiti per lezione in localStorage.
const BADGES = [
  { id: 'primo',   ico: '🌟', nome: 'Prima risposta',  desc: 'Hai risposto alla prima attività' },
  { id: 'serie3',  ico: '🔥', nome: 'In serie',        desc: '3 risposte esatte di fila' },
  { id: 'serie5',  ico: '⚡', nome: 'Fuoco',           desc: '5 risposte esatte di fila' },
  { id: 'metà',    ico: '🌗', nome: 'A metà',          desc: 'Metà delle attività completate' },
  { id: 'tutte',   ico: '🎯', nome: 'Tutto svolto',    desc: 'Tutte le attività completate' },
  { id: 'perfetto',ico: '💎', nome: 'Perfetto',        desc: 'Tutte le attività con punteggio pieno' },
  { id: 'veloce',  ico: '⏱️', nome: 'Fulmine',         desc: 'Lezione completata in meno di 10 minuti' },
  { id: 'esploratore', ico: '🧭', nome: 'Esploratore', desc: 'Hai usato la ricerca (F)' },
  { id: 'ripasso', ico: '🔖', nome: 'Ripassatore',     desc: 'Hai segnato qualcosa da rivedere' },
  { id: 'sfida',   ico: '⚔️', nome: 'Sfida accettata', desc: 'Hai finito la Sfida lampo' }
];
const BD_KEY = DATA_KEY + '-badges';
let badges = {};
try { badges = JSON.parse(localStorage.getItem(BD_KEY) || '{}') || {}; } catch (e) {}
let badgeQueue = [];
function saveBadges() {
  try { localStorage.setItem(BD_KEY, JSON.stringify(badges)); } catch (e) {}
}
function unlockBadge(id) {
  if (badges[id] || !BADGES.some(b => b.id === id)) return;
  badges[id] = true;
  saveBadges();
  badgeQueue.push(id);
  if (badgeQueue.length === 1) showBadgeToast();
}
function showBadgeToast() {
  const b = BADGES.find(x => x.id === badgeQueue[0]);
  if (!b) { badgeQueue.shift(); if (badgeQueue.length) showBadgeToast(); return; }
  const t = el('div', 'badge-toast');
  t.appendChild(el('span', 'bicon', b.ico));
  const tx = el('div');
  tx.appendChild(el('div', 'btit', '🏅 Badge sbloccato: ' + b.nome));
  tx.appendChild(el('div', 'bdesc', b.desc));
  t.appendChild(tx);
  document.body.appendChild(t);
  setTimeout(() => { t.remove(); badgeQueue.shift(); if (badgeQueue.length) showBadgeToast(); }, 3200);
}
function badgesGrid() {
  const g = el('div', 'bdgrid');
  BADGES.forEach(b => {
    const c = el('div', 'bdcard' + (badges[b.id] ? ' got' : ''));
    c.appendChild(el('div', 'bdico', b.ico));
    c.appendChild(el('div', 'bdnome', b.nome));
    c.appendChild(el('div', 'bddesc', b.desc));
    g.appendChild(c);
  });
  return g;
}
function checkBadges() {
  const done = activeIdx.filter(i => attempted(i));
  // I badge che misuravano solo NAVIGAZIONE premiavano il click, non
  // l'apprendimento: "prima risposta" si sblocava anche sbagliando, "a metà
  // percorso" bastava aprire 5 attività, "fulmine" ricompensava la velocità
  // (cioè l'andare a caso). Ora richiedono anche CORRETTEZZA.
  if (done.length >= 1 && done.some(i => { const g = grade(i); return g.t > 0 && g.e > 0; }))
    unlockBadge('primo');
  if (STREAK.cur >= 3) unlockBadge('serie3');
  if (STREAK.cur >= 5) unlockBadge('serie5');
  if (activeIdx.length && done.length >= Math.ceil(activeIdx.length / 2)) {
    // metà percorso: almeno metà delle attività svolte, e almeno la metà giuste
    let e = 0, t = 0;
    done.forEach(i => { const g = grade(i); e += g.e; t += g.t; });
    if (t > 0 && e >= t * 0.5) unlockBadge('metà');
  }
  if (activeIdx.length && done.length === activeIdx.length) {
    unlockBadge('tutte');
    const allOk = done.every(i => { const g = grade(i); return g.t > 0 && g.e === g.t; });
    if (allOk) unlockBadge('perfetto');
    // "veloce" solo se il percorso è stato anche capito: 10 minuti con tutte
    // le risposte corrette è padronanza, 10 minuti sbagliando è fortuna
    const totS = sessMs + (document.hidden ? 0 : Date.now() - sessStart);
    if (totS < 10 * 60 * 1000 && allOk) unlockBadge('veloce');
  }
}
function badgesSummary() {
  const got = BADGES.filter(b => badges[b.id]).length;
  if (!got) return null;
  const row = el('div', 'badgerow');
  row.appendChild(el('span', 'badgelab', '🏅 Badge:'));
  BADGES.filter(b => badges[b.id]).slice(-4).forEach(b => row.appendChild(el('span', 'badge', b.ico + ' ' + b.nome)));
  const more = el('button', 'abar', 'Tutti (' + got + '/' + BADGES.length + ')');
  more.onclick = () => openBadges();
  row.appendChild(more);
  return row;
}
function openBadges() {
  const ov = el('div', 'bdov');
  const box = el('div', 'bdbox');
  const got = BADGES.filter(b => badges[b.id]).length;
  box.appendChild(el('h2', null, '🏅 I tuoi badge — ' + got + '/' + BADGES.length));
  box.appendChild(badgesGrid());
  const cl = el('button', 'primary', 'Chiudi');
  cl.onclick = () => ov.remove();
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  box.appendChild(cl);
  ov.appendChild(box);
  document.body.appendChild(ov);
}

// ------------------------------------------------------------ sfida lampo
function challengeWidget() {
  const pool = [];
  slides.forEach(s => (s.blocks || []).forEach(b => {
    if (b.quiz) pool.push({ q: b.quiz.q, opts: b.quiz.opts.map(o => ({ t: o.t, ok: !!o.ok })) });
    else if (b.vf) b.vf.forEach(v => pool.push({
      q: v.t, opts: [{ t: 'Vero', ok: !!v.ok }, { t: 'Falso', ok: !v.ok }]
    }));
  }));
  if (pool.length < 3) return null;
  const items = shuffle(pool).slice(0, 5);
  const w = el('div', 'chal');
  w.appendChild(el('div', 'callout', '🎯 Sfida lampo'));
  w.appendChild(el('p', null, 'Cinque domande pescate da tutto il percorso: quante ne azzecchi al primo colpo?'));
  const body = el('div');
  w.appendChild(body);
  const state = { k: 0, score: 0, started: false, lock: false };
  function stars5(n) {
    const s = el('div', 'stars');
    for (let i = 0; i < 5; i++) s.appendChild(el('span', i < n ? '' : 'off', '⭐'));
    return s;
  }
  function draw() {
    body.innerHTML = '';
    if (!state.started) {
      const b = el('button', 'start', '▶ Inizia la sfida');
      b.onclick = () => { state.started = true; draw(); };
      body.appendChild(b);
      return;
    }
    if (state.k >= items.length) {
      const pct = state.score / items.length;
      const fin = el('div', 'finish');
      fin.appendChild(stars5(pct >= 0.9 ? 5 : pct >= 0.6 ? 4 : pct >= 0.4 ? 3 : 2));
      const res = pct >= 0.9 ? 'Strepitoso! 🎉' : pct >= 0.6 ? 'Ottimo!' : pct >= 0.4 ? 'Buono, puoi fare meglio!' : 'Riprova per migliorare! 💪';
      fin.appendChild(el('div', 'res', state.score + ' / ' + items.length + ' — ' + res));
      unlockBadge('sfida');
      const again = el('button', 'start', '🔁 Rigioca la sfida');
      again.onclick = () => { state.k = 0; state.score = 0; state.started = false; state.lock = false; draw(); };
      fin.appendChild(again);
      body.appendChild(fin);
      return;
    }
    const it = items[state.k];
    const top = el('div', 'qtop');
    top.appendChild(el('span', 'sc', '⭐ ' + state.score));
    top.appendChild(el('span', 'qnum', 'Domanda ' + (state.k + 1) + ' / ' + items.length));
    body.appendChild(top);
    body.appendChild(el('q', null, it.q));
    const btns = [];
    const order = shuffleOpts(it.opts);
    order.forEach((k, pos) => {
      const o = it.opts[k];
      const btn = el('button', 'opt');
      btn.appendChild(el('span', 'letter', String.fromCharCode(65 + pos)));
      btn.appendChild(el('span', null, o.t));
      btns[k] = btn;
      btn.onclick = () => {
        if (state.lock) return;
        state.lock = true;
        const good = !!o.ok;
        btns.forEach((b2, k2) => {
          const ok2 = it.opts[k2].ok;
          b2.classList.add(ok2 ? 'correct' : 'wrong');
          if (ok2) b2.querySelector('.letter').textContent = '✓';
        });
        btn.classList.add(good ? 'correct' : 'wrong');
        btn.querySelector('.letter').textContent = good ? '✓' : '✗';
        if (good) state.score++;
        const fb = el('div', 'fb ' + (good ? 'ok' : 'ko'));
        fb.appendChild(el('div', 'verdict', good ? '✓ Esatto!' : '✗ Era “' + it.opts.find(o2 => o2.ok).t + '”.'));
        body.appendChild(fb);
        setTimeout(() => { if (document.body.contains(body)) { state.lock = false; state.k++; draw(); } }, 1150);
      };
      body.appendChild(btn);
    });
  }
  draw();
  return w;
}

// ------------------------------------------------------------ export docente
function buildExport() {
  const doneS = activeIdx.filter(i => attempted(i));
  const M = scoreMetrics();
  const EX = examMetrics();
  const esameIdx = examIdx();
  let e = 0, t = 0;
  const perSlide = [];
  doneS.forEach(i => {
    const g = grade(i);
    e += g.e; t += g.t;
    perSlide.push({ slide: i + 1, titolo: slides[i].title || '', punti: g.e, totale: g.t });
  });
  const totMs = sessMs + (document.hidden ? 0 : Date.now() - sessStart);
  // errori più frequenti per tipo di attività: il dato didatticamente più utile
  // per il docente (su cosa sbaglia di più), prima assente dall'export
  const perTipo = {};
  LOG.forEach(r2 => {
    const k = r2.tipo || '?';
    perTipo[k] = perTipo[k] || { tipo: k, giuste: 0, sbagliate: 0 };
    if (r2.esito) perTipo[k].giuste++; else perTipo[k].sbagliate++;
  });
  const errori = Object.values(perTipo).sort((a, b) => b.sbagliate - a.sbagliate);
  return { lezione: data.titolo, studente: studentName || 'Studente',
           data: new Date().toISOString().slice(0, 10),
           attivita_svolte: M.svolte + '/' + M.totaleAtt,
           punti: M.e + '/' + M.t,
           precisione: (M.t ? Math.round(M.e / M.t * 100) : 0) + '%',
           precisione_sulle_svolte: (M.tSvolte ? Math.round(M.e / M.tSvolte * 100) : 0) + '%',
           copertura: M.copertura + '%',
           esame: EX ? { esito: EX.e + '/' + EX.t, pct: EX.pct + '%',
                         soglia: EX.soglia + '/' + EX.t,
                         superato: EX.superato, domande: EX.svolte + '/' + EX.totale } : null,
           esame_ripreso: esameIdx.some(i => { const b2 = (slides[i].blocks || [])
             .find(b3 => b3.quiz && b3.quiz.exam); return b2 && b2.quiz.ripresa; }),
           // segnalibri "da rivedere": non saputi dal docente, che è il vuoto
           // più grande dell'export
           segnalibri: [...review].map(i => ({ slide: i + 1,
             titolo: (slides[i] && slides[i].title) || '' })),
           errori_per_tipo: errori,
           streak_record: STREAK.best,
           // tempo reale: il minimo a 1 minuto gonfiava la classifica
           // (3 secondi e 2 minuti risultavano identici)
           tempo_min: Math.round(totMs / 60000 * 10) / 10,
           slide_corrente: cur + 1,
           per_slide: perSlide.map(p2 => {
             const ms = slideTimes[p2.slide - 1];
             return Object.assign(p2, { tempo_s: ms ? Math.round(ms / 1000) : 0 });
           }),
           risposte: LOG };
}
// Riepilogo compatto dei progressi, disponibile per adattatori esterni.
function buildProgress() {
  const M = scoreMetrics();
  const totMs = sessMs + (document.hidden ? 0 : Date.now() - sessStart);
  return { done: M.svolte, total: M.totaleAtt, punti: M.e, totale: M.t,
            pct: M.pct, pct_svolte: M.pctSvolte, copertura: M.copertura,
            esame: examMetrics(),
            completata: M.completo,
            slide: cur + 1, di: slides.length,
            studente: studentName || 'Studente',
            tempo_s: Math.round(totMs / 1000) };
}
function downloadFile(name, content, mime) {
  const b = new Blob([content], { type: mime });
  const u = URL.createObjectURL(b);
  const a = document.createElement('a');
  a.href = u; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(u), 800);
}
function perSlideCsv(R) {
  return R.per_slide.map(p2 => [R.studente, p2.slide + ' (' + p2.titolo + ')', 'punti',
    p2.totale ? Math.round(p2.punti / p2.totale * 100) : 0, p2.tempo_s || 0].join(';'));
}
function exportReport(kind) {
  const R = buildExport();
  if (kind === 'csv') {
    const csv = ['"studente";"slide";"tipo";"esito";"tempo_s"'].concat(
      R.risposte.map(r2 => [csvCell(r2.studente), csvCell(r2.slide), csvCell(r2.tipo),
                           r2.esito ? '1' : '0', csvCell(r2.tempo)].join(';')))
      .concat(perSlideCsv(R)).join('\n');
    downloadFile('report-' + R.studente.replace(/[^\w]+/g, '_') + '.csv', '﻿' + csv, 'text/csv;charset=utf-8');
  } else if (kind === 'json') {
    downloadFile('report-' + R.studente.replace(/[^\w]+/g, '_') + '.json',
      JSON.stringify(R, null, 2), 'application/json');
  } else if (kind === 'stampa') {
    const rows = R.per_slide.map(p2 => '<tr><td>' + escHtml(p2.slide) + '</td><td>'
      + escHtml(p2.titolo) + '</td><td>' + escHtml(p2.punti) + '/' + escHtml(p2.totale)
      + '</td></tr>').join('');
    const w = window.open('', '_blank');
    w.document.write('<html><head><title>Report - ' + escHtml(R.studente) + '</title><style>'
      + 'body{font-family:Segoe UI,sans-serif;padding:28px;color:#111}'
      + 'h1{font-size:20px}table{border-collapse:collapse;width:100%;margin-top:12px}'
      + 'td,th{border:1px solid #ccc;padding:7px 10px;font-size:13px;text-align:left}'
      + 'th{background:#f3f3f3}</style></head><body>'
      + '<h1>Report lezione - ' + escHtml(R.lezione) + '</h1>'
      + '<p><b>Studente:</b> ' + escHtml(R.studente) + ' - <b>Data:</b> ' + escHtml(R.data)
      + ' - <b>Tempo:</b> ' + escHtml(R.tempo_min) + ' min</p>'
      + '<p><b>Attività svolte:</b> ' + escHtml(R.attivita_svolte) + ' - <b>Punti:</b> ' + escHtml(R.punti)
      + ' - <b>Precisione:</b> ' + escHtml(R.precisione) + '</p>'
      + '<table><tr><th>Slide</th><th>Titolo</th><th>Punti</th></tr>' + rows + '</table>'
      + '<script>setTimeout(() => window.print(), 350)<\/script></body></html>');
    w.document.close();
  }
}

// ------------------------------------------------------------ pannello finale
// Il pannello veniva ricostruito da zero a ogni visita all'ultima slide
// (~100 nodi, ricomputazione di tutti i punteggi). Viene riusato finche' i
// dati non cambiano: si invalida a ogni variazione di risultato.
let _finishCache = null, _finishKey = '';
// SCORE_REV aumenta a ogni ricalcolo del punteggio: la sola lunghezza di
// `results` non basta, perché un risultato può cambiare da errato a giusto
// senza che se ne aggiunga uno nuovo.
let SCORE_REV = 0;
function finishPanel() {
  const key = SCORE_REV + '|' + slides.length;
  if (_finishCache && _finishKey === key) return _finishCache;
  _finishKey = key;
  _finishCache = buildFinishPanel();
  return _finishCache;
}
function buildFinishPanel() {
  const wrap = el('div', 'fin');
  const chal = challengeWidget();
  if (chal) wrap.appendChild(chal);
  // metriche oneste: il denominatore è sempre il totale delle attività, e la
  // copertura è mostrata SEPARATAMENTE. Prima, saltare un'attività la escludeva
  // dal denominatore: 3 risposte giuste su 3 svolte (e 7 ignorate) davano 100%
  // di precisione, medaglia d'oro e 5 stelle.
  const M = scoreMetrics();
  const EX = examMetrics();
  const done = M.svolte, e = M.e, t = M.t, pct = M.pct;
  wrap.appendChild(el('div', 'callout', '🏁 Risultati del percorso'));
  if (activeIdx.length === 0) {
    wrap.appendChild(el('p', null, 'Questa lezione non ha attività interattive: navigala pure con Avanti.'));
    return wrap;
  }
  const row = el('div', 'sumrow');
  const c1 = el('div', 'sumcard');
  c1.appendChild(el('div', 'big', e + ' / ' + t));
  c1.appendChild(el('div', 'lab', 'punti conquistati'));
  const c2 = el('div', 'sumcard');
  c2.appendChild(el('div', 'big', pct + '%'));
  c2.appendChild(el('div', 'lab', 'precisione sul totale'));
  const c3 = el('div', 'sumcard');
  c3.appendChild(el('div', 'big', done + ' / ' + M.totaleAtt));
  c3.appendChild(el('div', 'lab', 'attività svolte'));
  row.appendChild(c1); row.appendChild(c2); row.appendChild(c3);
  // esame finale: esito e soglia calcolati davvero
  if (EX) {
    const cx = el('div', 'sumcard ' + (EX.superato ? 'ok' : 'ko'));
    cx.appendChild(el('div', 'big', EX.e + ' / ' + EX.t));
    cx.appendChild(el('div', 'lab', 'esame (soglia ' + EX.soglia + ')'));
    row.appendChild(cx);
  }
  // serie record personale (streak) accanto alle altre statistiche
  if (STREAK.best >= 2) {
    const c4 = el('div', 'sumcard');
    c4.appendChild(el('div', 'big', '🔥 ' + STREAK.best));
    c4.appendChild(el('div', 'lab', 'record di fila'));
    row.appendChild(c4);
  }
  wrap.appendChild(row);
  // medaglia e stelle solo se il percorso è stato davvero svolto tutto:
  // altrimenti premiano chi ha risposto a meno domande
  const completo = M.completo;
  const medal = el('div', 'medal',
    !completo ? '📖' : pct >= 90 ? '🥇' : pct >= 70 ? '🥈' : pct >= 50 ? '🥉' : '💪');
  wrap.appendChild(medal);
  const s = el('div', 'stars');
  const filled = !completo ? 0 : pct >= 90 ? 5 : pct >= 70 ? 4 : pct >= 50 ? 3 : 2;
  for (let i = 0; i < 5; i++) s.appendChild(el('span', i < filled ? '' : 'off', '⭐'));
  wrap.appendChild(s);
  const bsum = badgesSummary();
  if (bsum) wrap.appendChild(bsum);
  wrap.appendChild(el('p', null,
    !completo
      ? 'Hai svolto ' + done + ' attività su ' + M.totaleAtt + ' (' + M.copertura + '% del percorso). '
        + 'Le stelle e la medaglia si sbloccano svolgendole tutte: tornare indietro e '
        + 'completare le mancanti aumenta il punteggio.'
      : (pct >= 90 ? 'Percorso completo e ottimo: padroni del tema! 🎉'
                   : 'Percorso completo: hai svolto tutte le attività. '
                     + 'Riascolta i moduli e riprova per un punteggio migliore.')));
  const acts = el('div', 'actrow');
  const again = el('button', 'primary', '🔄 Rigioca il percorso');
  if (again) again.onclick = () => { if (lessonMode === 'exam') return; results = {}; celebrated = false; const sc = _btn('score'); if (sc) sc.hidden = true; go(0); };
  const back = el('button', null, '⬅ Torna all\'inizio');
  back.onclick = () => go(0);
  acts.appendChild(again); acts.appendChild(back);
  // export risultati per il docente + ripasso dei segnalibri
  if (done > 0) {
    const exp = el('div', 'exprow');
    exp.appendChild(el('span', 'explab', '📄 Report per il docente (' + (studentName || 'nome non impostato — tasto U') + '):'));
    const bCsv = el('button', null, '⬇ CSV'); bCsv.onclick = () => exportReport('csv');
    const bJs = el('button', null, '⬇ JSON'); bJs.onclick = () => exportReport('json');
    const bPr = el('button', null, '🖨 Stampa'); bPr.onclick = () => exportReport('stampa');
    exp.appendChild(bCsv); exp.appendChild(bJs); exp.appendChild(bPr);
    wrap.appendChild(exp);
    // classifica di classe: manda il risultato al pannello del docente
    if (window.LESSON_DIR) {
      const crow = el('div', 'exprow');
      const cbtn = el('button', null, '🏆 Invia alla classifica di classe');
      cbtn.onclick = async () => {
        const p = buildProgress();
        if (!p.done) { alert('Prima svolgi almeno un\'attività.'); return; }
        cbtn.disabled = true; cbtn.textContent = '⏳ Invio…';
        try {
          const r = await fetch('/api/classifica', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lesson: window.LESSON_DIR, studente: p.studente,
                                   punti: p.punti, totale: p.totale,
                                   completata: p.completata,
                                   copertura: p.copertura,
                                   tempo_min: Math.round(p.tempo_s / 6) / 10 }) });
          const j = await r.json().catch(() => ({}));
          cbtn.textContent = (r.ok && j.ok) ? '✓ Inviato in classifica!' : '✗ Invio fallito (aperto da file? usa il server)';
        } catch (e) {
          cbtn.textContent = '✗ Invio fallito (nessun server)';
        }
        cbtn.disabled = false;
      };
      crow.appendChild(cbtn);
      wrap.appendChild(crow);
    }
  }
  if (review.size > 0) {
    const rip = el('button', null, '🎯 Ripassa le slide segnalate (' + review.size + ')');
    rip.onclick = () => goRipasso();
    acts.appendChild(rip);
  }
  // nome studente sempre visibile/modificabile nel pannello finale
  const nmrow = el('div', 'namerow');
  nmrow.appendChild(el('span', null, 'Studente:'));
  const nmIn = el('input');
  nmIn.type = 'text'; nmIn.value = studentName; nmIn.placeholder = 'il tuo nome';
  nmIn.maxLength = 40;
  nmIn.oninput = () => {
    studentName = nmIn.value.trim();
    try { localStorage.setItem(DATA_KEY + '-nome', studentName); } catch (e) {}
  };
  nmrow.appendChild(nmIn);
  wrap.appendChild(nmrow);
  wrap.appendChild(acts);
  // i coriandoli si scattano a percorso COMPLETO e con un punteggio
  // decente: prima bastava aver aperto tutte le slide, quindi uno studente
  // con 2 risposte giuste su 11 riceveva lo stesso spettacolo di chi ha
  // capito tutto
  if (done >= activeIdx.length && pct >= 50 && !celebrated) {
    celebrated = true;
    setTimeout(confetti, 350);
  }
  return wrap;
}
$('#prog').textContent = '1 / ' + slides.length;
const ttlEl = _safe('ttl');
if (ttlEl) ttlEl.textContent = data.titolo;
// nome studente per il report: piccolo pannello INLINE (mai prompt():
// un dialogo modale blocca il rendering in headless e disturba lo studente)
function askName() {
  const box = _btn('nmbox');
  if (!box || studentName) return;
  try {
    if (localStorage.getItem(DATA_KEY + '-nome-chiesto')) return;
    localStorage.setItem(DATA_KEY + '-nome-chiesto', '1');
  } catch (e) {}
  box.hidden = false;
  const inp = _btn('nminput');
  if (inp) inp.focus();
}
function saveName() {
  const inp = _btn('nminput');
  const box = _btn('nmbox');
  if (inp) {
    studentName = (inp.value || '').trim().slice(0, 40);
    try { localStorage.setItem(DATA_KEY + '-nome', studentName); } catch (e) {}
  }
  if (box) box.hidden = true;
}
const _nmOk = _safe('nmok');
if (_nmOk) _nmOk.onclick = saveName;
const _nmInput = _safe('nminput');
if (_nmInput) _nmInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); saveName(); }
  e.stopPropagation();
});

// ------------------------------------------------------------ menu compila (unica istanza)
const cmpMenu = el('div', 'cmpmenu');
document.body.appendChild(cmpMenu);
let openBlank = null;
function closeCmp() { cmpMenu.classList.remove('open'); openBlank = null; }
document.addEventListener('click', e => {
  if (cmpMenu.classList.contains('open') && !cmpMenu.contains(e.target) && e.target !== openBlank) closeCmp();
});

// ------------------------------------------------------------ render slide
function render(i) {
  const s = slides[i];
  const box = $('#slide');
  box.innerHTML = '';
  closeCmp();
  try { window.__attivo = null; } catch (e) {}
  // riavvia l'animazione d'ingresso nella direzione di navigazione
  box.style.animation = 'none';
  void box.offsetWidth;
  box.style.animation = '';
  box.style.animationName = animDir === 'fwd' ? 'slideInF' : animDir === 'bwd' ? 'slideInB' : 'slideIn';
  box.style.animationDuration = '.45s';
  box.style.animationTimingFunction = 'cubic-bezier(.2, .7, .3, 1)';
  if (s.icon) box.appendChild(el('div', 'icon', s.icon));
  if (s.banner) box.appendChild(el('div', 'banner', s.banner));
  // bidx = indice del blocco nella slide: serve a dare a ogni attività un
  // proprio ordine stabile fra le visite (vedi stableOrder).
  // I blocchi senza tipo riconosciuto restituiscono null: vanno scartati qui,
  // altrimenti `b.style` su un nodo non-Elemento fa eccezione.
  const blocks = (s.blocks || [])
    .map((b, bi) => renderBlock(b, i, bi))
    .filter(Boolean);
  blocks.forEach((b, k) => {
    // l'animazione di ingresso si applica solo se l'utente non ha chiesto
    // riduzione del movimento: sotto prefers-reduced-motion ogni blocco leggeva
    // offsetHeight (nel CSS `animation: none` faceva fallire l'animazione e il
    // fallback forzava un reflow di tutti i blocchi a ogni cambio slide)
    if (!REDUCED_MOTION) b.style.animationDelay = (k * 75) + 'ms';
    box.appendChild(b);
  });
  if (MODNAV.some(m => m.i === i)) {
    const nav = modnavBar(i);
    if (!REDUCED_MOTION) nav.style.animation = 'rise .4s both';
    box.insertBefore(nav, box.firstChild);
  }
  if (i === LAST) box.appendChild(finishPanel());
  // annuncia SOLO il titolo della slide al lettore di schermo
  const ann = _safe('slideAnnounce');
  if (ann) ann.textContent = (s.title || ('Slide ' + (i + 1))) + ' — '
    + (i + 1) + ' di ' + slides.length;
  // quando la slide completa un'attività interattiva, riparte la lettura:
  // la voce accompagna anche il feedback (non solo la teoria)
  if (window.__attivo && !RIPASSO.length) {
    try { audio.currentTime = 0; tryPlay(); } catch (e) {}
  }
  const bPrev = _btn('btnPrev'), bNext = _btn('btnNext');
  if (bPrev) bPrev.disabled = i === 0;
  if (bNext) {
    bNext.textContent = i === LAST ? '🏁 Fine' : 'Avanti →';
    bNext.disabled = false;
  }
  if (lessonMode === 'exam' && examOrder.length && i !== LAST) {
    const ep = examOrder.indexOf(i);
    if (ep >= 0) examPos = ep;
  }
  const prog = _btn('prog');
  if (prog) prog.textContent = (i + 1) + ' / ' + slides.length;
  const pf = _btn('pfill');
  if (pf) pf.style.transform = 'scaleX(' + ((i + 1) / slides.length) + ')';
  paintDots();
  loadAudio(i, true);
  savePos();
}

function renderBlock(b, idx, bidx) {
  if (b.callout) return el('div', 'callout', b.callout);
  if (b.h1) return el('h1', null, b.h1);
  if (b.h2) return el('h2', null, b.h2);
  if (b.p) return el('p', null, b.p);
  if (b.quote) {
    const w = document.createElement('div');
    w.appendChild(el('blockquote', null, b.quote));
    if (b.attr) w.appendChild(el('div', 'attr', '— ' + b.attr));
    return w;
  }
  if (b.list) {
    const ul = el('ul');
    b.list.forEach(it => ul.appendChild(el('li', null, it)));
    return ul;
  }
  if (b.quiz) return blockQuiz(b.quiz, idx, bidx);
  if (b.scenario) return blockScenario(b.scenario, idx, bidx);
  if (b.vf) return blockVf(b.vf, idx);
  if (b.seq) return blockSeq(b.seq, idx, bidx);
  if (b.compila) return blockCompila(b.compila, idx);
  if (b.errore) return blockErrore(b.errore, idx, bidx);
  if (b.match) return blockMatch(b.match, idx, bidx);
  if (b.flashcards) return blockFlashcards(b.flashcards, idx);
  if (b.classifica) return blockClassify(b.classifica, idx);
  if (b.glossario) return blockGlossario(b.glossario, idx);
  // tipo di blocco sconosciuto (o vuoto): niente da mostrare.
  // Restituiva un TextNode vuoto, che in render() finiva in
  // `b.style.animationDelay` -> TypeError e slide BLANKCA.
  return null;
}

// Scaffold progressivo: l'aiuto cresce con gli errori invece di comparire
// tutto subito (o mai, com'era: esisteva solo in 1 attività su 11).
function scaffolding(nErrori) {
  const w = el('div', 'adapt');
  const msg = nErrori <= 1
    ? '💡 Quale parola della domanda ti ha tratto in inganno? Rileggila e prova di nuovo.'
    : '💡 Quale concetto chiede esattamente la domanda? Torna al modulo e trova quel passaggio.';
  w.appendChild(el('span', null, msg));
  return w;
}

function answerFeedback(good, okTxt, koTxt, fbTxt) {
  const f = el('div', 'fb ' + (good ? 'ok' : 'ko'));
  f.appendChild(el('div', 'verdict', good ? ('✓ ' + (okTxt || 'Esatto!')) : ('✗ ' + (koTxt || 'Non è corretto.'))));
  if (fbTxt) f.appendChild(el('div', 'item', fbTxt));
  return f;
}

function blockQuiz(q, idx, bidx) {
  const w = el('div', 'quiz');
  w.appendChild(el('q', null, q.q));
  // Ordine casuale coerente: la lettera segue la posizione mostrata e il
  // dataset conserva l'indice reale per valutare correttamente la risposta.
  // L'ordine è memorizzato per (slide, blocco): resta identico fra Indietro e
  // Avanti, così lo studente risponde al contenuto e non alla posizione.
  const order = stableOrder(idx, bidx, 'quiz', q.opts.length, () => shuffleOpts(q.opts));
  order.forEach((k, pos) => {
    const o = q.opts[k];
    const btn = el('button', 'opt');
    btn.appendChild(el('span', 'letter', String.fromCharCode(65 + pos)));
    btn.appendChild(el('span', null, o.t));
    btn.dataset.k = k;
    w.appendChild(btn);
  });
  const fb = el('div', 'fb');
  w.appendChild(fb);
  w.querySelectorAll('.opt').forEach(btn => {
    btn.onclick = () => {
      if (w.classList.contains('revealed')) return;
      const k = +btn.dataset.k;
      const opt = q.opts[k];
      const good = !!opt.ok;
      w.classList.add('revealed');
      w.querySelectorAll('.opt').forEach(b2 => b2.classList.remove('correct', 'wrong'));
      btn.classList.add(good ? 'correct' : 'wrong');
      btn.querySelector('.letter').textContent = good ? '✓' : '✗';
      if (!good) {
        const gi = q.opts.findIndex(o => o.ok);
        const g = [...w.querySelectorAll('.opt')].find(x => +x.dataset.k === gi);
        if (g) { g.classList.add('correct'); g.querySelector('.letter').textContent = '✓'; }
      }
      fb.innerHTML = '';
      fb.appendChild(answerFeedback(good, q.ok, q.ko, opt.fb || ''));
      if (!good) {
        // LA RISPOSTA GIUSTA CON LA SUA MOTIVAZIONE. Prima si mostrava solo
        // il feedback dell'opzione sbagliata scelta: lo studente sapeva perché
        // la sua era errata, ma non perché quella giusta è giusta — che è
        // l'informazione che trasforma una risposta corretta in apprendimento.
        const gi = q.opts.findIndex(o => o.ok);
        const fbOk = el('div', 'fb ok giusto');
        fbOk.appendChild(el('div', 'verdict', '✓ La risposta giusta: ' + q.opts[gi].t));
        if (q.opts[gi].fb) fbOk.appendChild(el('div', 'item', q.opts[gi].fb));
        fb.appendChild(fbOk);
        // scaffolding progressivo: l'aiuto cresce con gli errori
        fb.appendChild(scaffolding(1));
        // il pulsante torna al MODULO di questo quiz, non alla slide
        // precedente (dall'esame finale portava al glossario)
        const ad = el('div', 'adapt', '💡 Vuoi rivedere il passaggio? Torna al modulo, rileggi e poi riprova.');
        const back = el('button', 'abar', '← Rileggi il modulo');
        const dest = (typeof q.modulo_slide === 'number' && q.modulo_slide >= 0)
          ? q.modulo_slide : Math.max(0, idx - 1);
        back.onclick = (e) => { e.stopPropagation(); go(dest); };
        ad.appendChild(back);
        w.appendChild(ad);
      }
      results[idx] = results[idx] || {};
      results[idx].quiz = good;
      paintDots();
    };
  });
  return w;
}

function blockScenario(s, idx, bidx) {
  const m = el('div', 'scn');
  m.appendChild(el('div', 'situ', s.situazione));
  stableOrder(idx, bidx, 'scn', s.opts.length, () => shuffleOpts(s.opts)).forEach((k, pos) => {
    const o = s.opts[k];
    const btn = el('button', 'opt');
    btn.appendChild(el('span', 'letter', String.fromCharCode(65 + pos)));
    btn.appendChild(el('span', null, o.t));
    btn.dataset.ok = o.ok ? '1' : '';
    btn.dataset.fb = o.fb || '';
    m.appendChild(btn);
  });
  const fb = el('div', 'fb');
  m.appendChild(fb);
  m.querySelectorAll('.opt').forEach(btn => {
    btn.onclick = () => {
      if (m.classList.contains('revealed')) return;
      const good = btn.dataset.ok === '1';
      m.classList.add('revealed');
      m.querySelectorAll('.opt').forEach(b2 => b2.classList.remove('correct', 'wrong'));
      btn.classList.add(good ? 'correct' : 'wrong');
      btn.querySelector('.letter').textContent = good ? '✓' : '✗';
      if (!good) {
        const g = [...m.querySelectorAll('.opt')].find(x => x.dataset.ok === '1');
        if (g) { g.classList.add('correct'); g.querySelector('.letter').textContent = '✓'; }
      }
      fb.innerHTML = '';
      fb.appendChild(answerFeedback(good, 'Ottima scelta.', 'Non la migliore.', btn.dataset.fb || ''));
      if (!good) {
        const ad = el('div', 'adapt', '💡 Suggerimento: rileggi la situazione e la conclusione qui sotto prima di continuare.');
        m.appendChild(ad);
      }
      if (s.conclusione) m.appendChild(el('div', 'expl', '💡 ' + s.conclusione));
      results[idx] = results[idx] || {};
      results[idx].scenario = good;
      paintDots();
    };
  });
  return m;
}

function blockVf(items, idx) {
  const w = el('div', 'vf');
  items.forEach((item, n) => {
    const card = el('div', 'vfcard');
    card.appendChild(el('div', 'vfq', (n + 1) + '. ' + item.t));
    const row = el('div', 'vfrow');
    ['✓ Vero', '✗ Falso'].forEach(lbl => {
      const btn = el('button', 'vfbtn', lbl);
      btn.onclick = () => {
        if (card.classList.contains('answered')) return;
        card.classList.add('answered');
        const correct = (lbl.indexOf('Vero') >= 0) === !!item.ok;
        const other = [...row.querySelectorAll('.vfbtn')].find(x => x !== btn);
        btn.classList.add(correct ? 'ok' : 'ko');
        // data-ok: il simbolo ✓/✗ oltre al colore, cosic' l'esito si legge
        // anche per chi non distingue il rosso dal verde
        btn.dataset.ok = correct ? '1' : '0';
        if (!correct && other) { other.classList.add('ok'); other.dataset.ok = '1'; }
        results[idx] = results[idx] || {};
        results[idx].vf = results[idx].vf || [];
        results[idx].vf.push(correct);
        paintDots();
        const f = el('div', 'vffb ' + (correct ? 'ok' : 'ko'),
          (correct ? '✓ Esatto. ' : '✗ Non proprio. ') + (item.fb || ''));
        card.appendChild(f);
      };
      row.appendChild(btn);
    });
    card.appendChild(row);
    w.appendChild(card);
  });
  return w;
}

function blockSeq(s, idx, bidx) {
  const m = el('div', 'seq');
  m.appendChild(el('p', null, s.instr || "Clicca i passaggi nell'ordine corretto."));
  const pool = el('div', 'seqpool');
  const line = el('div', 'seqline');
  line.appendChild(el('div', 'seqlabel', 'Il tuo ordine'));
  const order = stableOrder(idx, bidx, 'seq', s.passi.length,
                            () => shuffle(s.passi.map((p, k) => k)));
  let placed = 0, wrongTries = 0;
  order.forEach(k => {
    const c = el('button', 'seqchip', s.passi[k]);
    c.dataset.k = k;
    c.onclick = () => {
      if (c.classList.contains('ok')) return;
      if (+c.dataset.k === placed) {
        c.classList.add('ok');
        c.textContent = (placed + 1) + '. ' + c.textContent;
        line.appendChild(c);
        placed++;
        if (placed === s.passi.length) {
          results[idx] = results[idx] || {};
          results[idx].seq = wrongTries === 0;
          paintDots();
          m.appendChild(el('div', 'seqmsg', wrongTries === 0 ? 'Sequenza perfetta! ✓' : 'Sequenza completata! ✓ (riprova per farla senza errori)'));
        }
      } else {
        wrongTries++;
        results[idx] = results[idx] || {};
        results[idx].seq = false;
        c.classList.add('err');
        setTimeout(() => c.classList.remove('err'), 450);
      }
    };
    pool.appendChild(c);
  });
  m.appendChild(pool);
  m.appendChild(line);
  return m;
}

function idxOfBlank(w, blank) {
  return [...w.children].findIndex(n => n.contains && n.contains(blank));
}

function blockCompila(items, idx) {
  const w = el('div', 'cmp');
  items.forEach(item => {
    const fr = el('div', 'cmpfrase');
    const parts = String(item.frase).split('___');
    fr.appendChild(document.createTextNode(parts[0] || ''));
    const blank = el('button', 'cmpblank', '____');
    blank.dataset.risposta = item.risposta;
    blank.dataset.aiuto = (item.aiuto || []).join('\u0001');
    blank.onclick = ev => {
      ev.stopPropagation();
      if (blank.classList.contains('done')) return;
      openBlank = blank;
      const opts = shuffle([blank.dataset.risposta,
        ...blank.dataset.aiuto.split('\u0001').filter(Boolean)]);
      cmpMenu.innerHTML = '';
      opts.forEach(tt => {
        const b = el('button', null, tt);
        b.onclick = e2 => {
          e2.stopPropagation();
          blank.textContent = tt;
          const good = tt === blank.dataset.risposta;
          blank.classList.add('done', good ? 'ok' : 'ko');
          closeCmp();
          paintCompila(w, idx);
          // FEEDBACK DIDATTICO: quando sbaglia, il vuoto si riempie da solo con
          // la risposta corretta e spiega PERCHÉ. Prima restava la parola
          // sbagliata in rosso, senza sapere quale fosse giusta né perché:
          // lo studente registrava il proprio errore e chiudeva il menu.
          if (!good) {
            blank.textContent = blank.dataset.risposta;
            blank.classList.remove('ko');
            const fb = el('div', 'cmpfb',
              'La parola giusta è “' + blank.dataset.risposta + '”. '
              + 'Rileggi la frase con questa parola: che cosa cambia nel significato?');
            w.insertBefore(fb, w.children[idxOfBlank(w, blank) + 1] || null);
          }
        };
        cmpMenu.appendChild(b);
      });
      const r = blank.getBoundingClientRect();
      cmpMenu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 250)) + 'px';
      cmpMenu.style.top = Math.min(r.bottom + 6, window.innerHeight - 220) + 'px';
      cmpMenu.classList.add('open');
    };
    fr.appendChild(blank);
    if (parts[1]) fr.appendChild(document.createTextNode(parts.slice(1).join('___')));
    w.appendChild(fr);
  });
  const msg = el('div', 'cmpmsg', 'Clicca ogni vuoto e scegli la parola giusta.');
  w.appendChild(msg);
  return w;
}

function paintCompila(w, idx) {
  const all = [...w.querySelectorAll('.cmpblank')];
  const answered = all.filter(b2 => b2.classList.contains('done'));
  const msg = w.querySelector('.cmpmsg');
  const okList = answered.map(b2 => b2.classList.contains('ok'));
  results[idx] = results[idx] || {};
  results[idx].compila = okList;
  if (answered.length === all.length) {
    const allOk = okList.every(x => x);
    msg.textContent = allOk ? 'Frasi tutte complete! 🎉' : 'Hai completato: ripensa quelle in rosso.';
    msg.className = 'cmpmsg ' + (allOk ? 'ok' : 'ko');
  } else {
    msg.textContent = (answered.length + ' / ' + all.length + ' vuoti compilati');
    msg.className = 'cmpmsg';
  }
  paintDots();
}

function blockMatch(s, idx, bidx) {
  const m = el('div', 'match');
  m.appendChild(el('p', null, s.instr || 'Abbina ogni concetto alla definizione corretta.'));
  const wrap = el('div', 'pairs');
  const left = el('div', 'col L'), right = el('div', 'col R');
  left.appendChild(el('div', 'collabel', 'Concetti'));
  right.appendChild(el('div', 'collabel', 'Definizioni'));
  const terms = stableOrder(idx, bidx, 'matchL', s.pairs.length,
                            () => shuffle(s.pairs.map((p, k) => ({ t: p.term, k }))));
  const defs = stableOrder(idx, bidx, 'matchR', s.pairs.length,
                           () => shuffle(s.pairs.map((p, k) => ({ t: p.def, k }))));
  terms.forEach(o => { const c = el('button', 'chip', o.t); c.dataset.k = o.k; c.dataset.side = 'L'; left.appendChild(c); });
  defs.forEach(o => { const c = el('button', 'chip', o.t); c.dataset.k = o.k; c.dataset.side = 'R'; right.appendChild(c); });
  wrap.appendChild(left); wrap.appendChild(right);
  m.appendChild(wrap);
  m.appendChild(el('div', 'msg', ''));
  const chips = [...m.querySelectorAll('.chip')];
  const msg = m.querySelector('.msg');
  let sel = null, solved = 0;
  chips.forEach(c => {
    c.onclick = () => {
      if (c.classList.contains('done')) return;
      if (!sel) { sel = c; c.classList.add('sel'); return; }
      if (sel === c) { sel.classList.remove('sel'); sel = null; return; }
      if (sel.dataset.side === c.dataset.side) {
        sel.classList.remove('sel'); sel = c; c.classList.add('sel'); return;
      }
      const a = +sel.dataset.k, b = +c.dataset.k;
      if (a === b) {
        sel.classList.remove('sel'); sel.classList.add('done'); c.classList.add('done');
        sel.disabled = c.disabled = true; solved++;
        msg.textContent = solved === s.pairs.length ? 'Tutti gli abbinamenti sono corretti! 🎉' : 'Corretto ✓';
        msg.style.color = 'var(--ok)';
        results[idx] = results[idx] || {};
        results[idx].match = solved === s.pairs.length;
        paintDots();
      } else {
        c.classList.add('err');
        setTimeout(() => c.classList.remove('err'), 500);
        msg.textContent = 'Non corrispondono: riprova.';
        msg.style.color = 'var(--ko)';
        results[idx] = results[idx] || {};
        results[idx].match = false;
      }
      sel.classList.remove('sel'); sel = null;
    };
  });
  return m;
}

function blockFlashcards(f, idx) {
  const m = el('div', 'flash');
  m.appendChild(el('p', null, f.instr || 'Studia le carte, poi mettiti alla prova.'));
  const deck = el('div', 'fcdeck');
  const cards = [];
  let curC = -1;
  const dotsW = el('div', 'fcdots');
  f.cards.forEach((c, k) => {
    const card = el('div', 'fcard');
    const inner = el('div', 'fcinner');
    const front = el('div', 'fcface fcfront');
    front.appendChild(el('span', 'fclabel', 'Termine ' + (k + 1) + '/' + f.cards.length));
    front.appendChild(el('div', 'fcterm', c.t));
    front.appendChild(el('div', 'fchint', 'clicca per girare'));
    const back = el('div', 'fcface fcback');
    back.appendChild(el('span', 'fclabel', 'Definizione'));
    back.appendChild(el('div', 'fcdef', c.d));
    inner.appendChild(front); inner.appendChild(back);
    card.appendChild(inner);
    card.onclick = () => {
      card.classList.toggle('flip');
      if (curC === k) return;
      curC = k;
      [...dotsW.children].forEach((d2, j) => d2.classList.toggle('on', j === k));
      dotsW.dataset.viste = String(new Set([...dotsW.children].filter(x => x.classList.contains('on')).map(x => +x.dataset.k).concat(k)).size);
    };
    cards.push(card);
    deck.appendChild(card);
    const d = el('span'); d.dataset.k = k; dotsW.appendChild(d);
  });
  if (dotsW.children[0]) dotsW.children[0].classList.add('on');
  m.appendChild(deck);
  m.appendChild(dotsW);
  const ctrl = el('div', 'fcctrl');
  const bFlip = el('button', null, '🔄 Gira');
  bFlip.onclick = () => { if (cards[curC] || cards[0]) (cards[curC] || cards[0]).classList.toggle('flip'); };
  const bPrev = el('button', null, '←');
  bPrev.onclick = () => navC(-1);
  const bNext = el('button', null, '→');
  bNext.onclick = () => navC(1);
  function navC(d) {
    curC = Math.max(0, Math.min(cards.length - 1, (curC < 0 ? 0 : curC) + d));
    cards.forEach((c2, j) => c2.classList.toggle('fcon', j === curC));
    [...dotsW.children].forEach((d2, j) => d2.classList.toggle('on', j === curC));
  }
  ctrl.appendChild(bPrev); ctrl.appendChild(bFlip); ctrl.appendChild(bNext);
  m.appendChild(ctrl);
  // verifica: per ogni termine, scegli la definizione giusta tra TUTTE quelle del mazzo
  const v = el('div', 'fcverify');
  v.appendChild(el('p', null, 'Ora verifica: abbina ogni termine alla definizione giusta.'));
  let hits = 0;
  f.cards.forEach((c, k) => {
    const row = el('div', 'fcrow');
    row.appendChild(el('span', 'fcterm', c.t));
    const opts = f.cards.map((x, j) => ({ t: x.d, j }));
    for (let n = opts.length - 1; n > 0; n--) {
      const r = Math.floor(Math.random() * (n + 1));
      const tmp = opts[n]; opts[n] = opts[r]; opts[r] = tmp;
    }
    const ob = el('div', 'fcrowopts');
    opts.forEach(o => {
      const btn = el('button', 'opt small');
      btn.textContent = o.t;
      btn.onclick = () => {
        if (row.classList.contains('done')) return;
        const good = o.j === k;
        row.classList.add('done');
        btn.classList.add(good ? 'correct' : 'wrong');
        // evidenzia sempre la giusta (in verde)
        [...ob.children].forEach((x2, j2) => { if (opts[j2].j === k) x2.classList.add('correct'); });
        if (good) hits++;
        results[idx] = results[idx] || {};
        results[idx].flash = hits === f.cards.length;
        paintDots();
      };
      ob.appendChild(btn);
    });
    row.appendChild(ob);
    v.appendChild(row);
  });
  m.appendChild(v);
  return m;
}

// ------------------------------------------------ attività: trascina nella categoria
// Le "classifica" assegnano ogni elemento (pill) alla categoria giusta: click
// sulla pillola + click sulla zona (o drag & drop su desktop). Punteggio =
// numero di elementi classificati correttamente al primo collocamento.
function blockClassify(c, idx) {
  const items = (c.items || []).slice(0, 10);
  const cats = (c.cats || []).slice(0, 4);
  const m = el('div', 'classify');
  if (!items.length || cats.length < 2) return m;
  const catNames = cats.map(x => typeof x === 'string' ? x : (x.n || 'Categoria'));
  m.appendChild(el('p', null, c.instr || 'Trascina (o clicca) ogni elemento nella categoria giusta.'));
  const pool = el('div', 'clpool');
  const zones = el('div', 'clzones');
  const placed = {};        // itemIdx -> catIdx (primo collocamento conta per il punteggio)
  let solved = 0, sel = -1;
  const total = items.length;
  const doneMsg = () => {
    results[idx] = results[idx] || {};
    results[idx].classifica = solved;
    if (Object.keys(placed).length >= total) {
      m.appendChild(el('div', 'clmsg', solved === total ? 'Classificazione perfetta! ✓' : 'Completata: ' + solved + ' su ' + total + ' al primo collocamento. ✓'));
      zones.querySelectorAll('.clzone').forEach(z => z.classList.add('done'));
    }
    paintDots();
  };
  const zoneEls = catNames.map((nome, ci) => {
    const z = el('div', 'clzone');
    z.appendChild(el('div', 'clzname', nome));
    const list = el('div', 'clzlist');
    z.appendChild(list);
    const drop = (ii) => {
      const p = document.querySelector('.clpill[data-i="' + ii + '"]');
      if (!p || placed[ii] !== undefined) return;
      placed[ii] = ci;
      if (+p.dataset.ok === ci) solved++;
      p.classList.remove('sel');
      p.classList.add('in');
      p.draggable = false;
      list.appendChild(p);
      doneMsg();
    };
    z.onclick = () => { if (sel >= 0) { const p2 = document.querySelector('.clpill[data-i="' + sel + '"]'); sel = -1; if (p2) drop(+p2.dataset.i); } };
    z.addEventListener('dragover', e => { e.preventDefault(); z.classList.add('over'); });
    z.addEventListener('dragleave', () => z.classList.remove('over'));
    z.addEventListener('drop', e => {
      e.preventDefault(); z.classList.remove('over');
      try {
        const ii = parseInt(String(e.dataTransfer.getData('text/plain') || '').replace(/[^0-9]/g, ''), 10);
        if (!Number.isNaN(ii) && ii >= 0) drop(ii);
      } catch (err) { /* alcuni browser limitano getData: il click resta disponibile */ }
    });
    zones.appendChild(z);
    return z;
  });
  const order = shuffle(items.map((x, k) => k));
  order.forEach(ii => {
    const it = items[ii];
    const p = el('button', 'clpill', String(it.t || ''));
    p.dataset.i = ii;
    p.dataset.ok = it.cat;
    p.onclick = () => {
      if (placed[ii] !== undefined) return;
      if (sel === ii) { sel = -1; p.classList.remove('sel'); return; }
      pool.querySelectorAll('.clpill').forEach(x2 => x2.classList.remove('sel'));
      sel = ii;
      p.classList.add('sel');
    };
    p.draggable = true;
    p.addEventListener('dragstart', e => {
      if (placed[ii] !== undefined) { e.preventDefault(); return; }
      e.dataTransfer.setData('text/plain', String(ii));
    });
    pool.appendChild(p);
  });
  m.appendChild(el('div', 'clhint', '💡 Clicca la pillola e poi la categoria (o trascinala sopra).'));
  m.appendChild(pool);
  m.appendChild(zones);
  return m;
}

function blockGlossario(g, idx) {
  const m = el('div', 'glos');
  m.appendChild(el('p', null, g.instr || 'Cerca un termine o sfoglia per modulo.'));
  const groups = (g.groups || []).filter(gr => gr && (gr.terms || []).length);
  const total = groups.reduce((n, gr) => n + gr.terms.length, 0);
  const search = el('input', 'glosearch');
  search.type = 'search';
  search.placeholder = '🔍 Cerca tra ' + total + ' termini…';
  search.setAttribute('aria-label', 'Cerca nel glossario');
  const count = el('div', 'glocount', total + ' termini · ' + groups.length + ' moduli');
  const list = el('div', 'glolist');
  m.appendChild(search); m.appendChild(count); m.appendChild(list);
  function paint(filter) {
    list.innerHTML = '';
    const f = (filter || '').trim().toLowerCase();
    let shown = 0, shownGroups = 0;
    groups.forEach(gr => {
      const terms = gr.terms.filter(t =>
        !f || (t.t || '').toLowerCase().includes(f) || (t.d || '').toLowerCase().includes(f));
      if (!terms.length) return;
      shownGroups++;
      list.appendChild(el('div', 'glogroup', '📖 ' + (gr.modulo || 'Modulo')));
      terms.forEach(t => {
        shown++;
        const row = el('div', 'gloterm');
        const btn = el('button');
        btn.appendChild(el('span', null, t.t));
        btn.appendChild(el('span', 'arrow', '▶'));
        btn.onclick = () => row.classList.toggle('open');
        row.appendChild(btn);
        const def = el('div', 'glodef', t.d);
        if (gr.slide !== null && gr.slide !== undefined && gr.modulo) {
          const link = el('button', 'glomod', '→ Vedi: ' + gr.modulo);
          link.onclick = (e) => { e.stopPropagation(); try { go(gr.slide); } catch (_) {} };
          def.appendChild(el('br'));
          def.appendChild(link);
        }
        row.appendChild(def);
        list.appendChild(row);
      });
    });
    if (!shown) list.appendChild(el('div', 'gloempty', 'Nessun termine trovato.'));
    count.textContent = f
      ? shown + ' risultati · ' + shownGroups + ' moduli'
      : total + ' termini · ' + groups.length + ' moduli';
  }
  let deb = null;
  search.addEventListener('input', () => {
    clearTimeout(deb);
    deb = setTimeout(() => paint(search.value), 140);
  });
  paint('');
  return m;
}

function blockErrore(s, idx, bidx) {
  const m = el('div', 'erra');
  const badPhrase = (s.sbagliato || '').trim();
  const norm = wd => wd.replace(/[.,;:!?»«()]/g, '').toLowerCase();
  const words = s.brano.split(/\s+/);
  // parole che compongono la frase sbagliata: cliccarne una qualsiasi vale
  // come individuare la parte esatta (le frasi sono multi-parola)
  const badWords = new Set(badPhrase.toLowerCase().replace(/[.,;:!?»«()]/g, '')
    .split(/\s+/).filter(wd => wd.length > 2));
  const isBadWord = wd => badWords.has(norm(wd));

  // barra dei passaggi 1-2-3: Leggi -> Individua -> Correggi
  const steps = el('div', 'steps');
  const nums = [1, 2, 3].map(n => {
    const sp = el('span', 'snum', String(n));
    sp.title = n === 1 ? 'Leggi il brano' : n === 2 ? 'Individua l\u2019errore' : 'Scegli la correzione';
    steps.appendChild(sp);
    return sp;
  });
  steps.appendChild(el('span', 'stxt', 'Leggi \u2192 Individua \u2192 Correggi'));

  // 1) il brano cliccabile
  const br = el('div', 'brano');
  let wrongPicks = 0;
  words.forEach(word => {
    const sp = el('span', 'w', word);
    sp.onclick = () => {
      if (br.classList.contains('lock')) return;
      br.querySelectorAll('.w').forEach(x => x.classList.remove('pick', 'miss'));
      sp.classList.add('pick');
      nums[0].classList.remove('cur'); nums[0].classList.add('done');
      nums[1].classList.add('cur');
      hint.textContent = 'Hai scelto: \u201c' + word + '\u201d. Ora scegli la correzione giusta (passaggio 3).';
      hint.style.color = '';
      if (!fix.classList.contains('show')) fix.classList.add('show');
    };
    br.appendChild(sp);
    br.appendChild(document.createTextNode(' '));
  });

  // messaggio-guida sotto il brano
  const hint = el('div', 'hint', badPhrase
    ? '\ud83d\udd0d Leggi il brano e clicca una parola della parte SBAGLIATA (passaggio 2).'
    : '\ud83d\udd0d Clicca la parte sbagliata del brano, poi scegli la correzione.');

  // 2) opzioni di correzione come pulsanti chiari (niente menu a tendina)
  const fix = el('div', 'fixbox');
  fix.appendChild(el('span', 'flab', '3. Correzione giusta:'));
  const _wordsAll = words.filter(wd => !isBadWord(wd) && wd.length > 3);
  const _nDistract = badPhrase ? 1 : 3;
  const distract = stableOrder(idx, bidx, 'errDist', _nDistract,
                               () => shuffle(_wordsAll).slice(0, _nDistract));
  const opts = stableOrder(idx, bidx, 'errOpts', _nDistract + 1,
                           () => shuffle([s.correzione, ...distract]));
  const expl = el('div', 'expl');
  const hintBox = el('div', 'why');
  hintBox.hidden = true;
  opts.forEach((tt, k) => {
    const b = el('button', 'flopt');
    b.appendChild(el('span', 'letter', String.fromCharCode(65 + k)));
    b.appendChild(el('span', null, tt));
    b.onclick = () => {
      if (br.classList.contains('lock')) return;
      const picked = br.querySelector('.pick');
      if (!picked) {
        hint.textContent = '\u26a0 Prima clicca la parte sbagliata nel brano (passaggio 2).';
        hint.style.color = 'var(--ko)';
        setTimeout(() => { hint.style.color = ''; }, 1400);
        return;
      }
      if (tt === s.correzione) {
        if (badPhrase && !isBadWord(picked.textContent)) {
          hint.textContent = '\u274c Quasi! La correzione \u00e8 giusta, ma va applicata a un altro punto: clicca la parte esatta nel brano.';
          hint.style.color = 'var(--ko)';
          picked.classList.remove('pick');
          setTimeout(() => { hint.style.color = ''; }, 1800);
          return;
        }
        br.classList.add('lock');
        nums.forEach(n2 => { n2.classList.remove('cur'); n2.classList.add('done'); });
        br.querySelectorAll('.w').forEach(x => x.classList.remove('pick', 'miss'));
        // evidenzia TUTTA la parte sbagliata, non solo la parola cliccata
        br.querySelectorAll('.w').forEach(x => { if (isBadWord(x.textContent)) x.classList.add('hit'); });
        picked.classList.add('hit');
        fix.classList.remove('show');
        hint.textContent = '\u2705 Errore trovato e corretto!';
        hint.style.color = 'var(--ok)';
        expl.textContent = '\u270f\ufe0f ' + (s.spiegazione || '');
        results[idx] = results[idx] || {};
        results[idx].errore = true;
        paintDots();
      } else {
        wrongPicks++;
        picked.classList.remove('pick');
        picked.classList.add('miss');
        b.classList.add('wrong');
        hint.textContent = '\u274c Non \u00e8 la combinazione giusta: riprova.';
        hint.style.color = 'var(--ko)';
        setTimeout(() => {
          picked.classList.remove('miss');
          b.classList.remove('wrong');
          hint.style.color = '';
        }, 900);
        if (badPhrase && wrongPicks >= 2) {
          hintBox.textContent = '\ud83d\udca1 Suggerimento: focalizzati su \u201c' + badPhrase + '\u201d.';
          hintBox.hidden = false;
        }
      }
    };
    fix.appendChild(b);
  });
  m.appendChild(steps);
  m.appendChild(br);
  m.appendChild(hint);
  m.appendChild(fix);
  m.appendChild(hintBox);
  m.appendChild(expl);
  return m;
}

// ---------------------------------------------------------------- audio
const audio = new Audio();
audio.preload = 'auto';
// Alla fine dell'audio NON si passa da soli alla slide successiva: l'avanti e'
// sempre una scelta dello studente (pulsante "Avanti" o freccia destra), anche
// sulle slide di solo contenuto. Nessun ascolto su 'ended': senza questo
// aggancio la traccia si ferma dove finisce e ▶ la riavvia.
// velocità: applicata qui, ora che `audio` esiste
applyRate();
if (_safe('btnSpeed')) _safe('btnSpeed').onclick = () => {
  rateIdx = (rateIdx + 1) % RATE_VALORI.length;
  applyRate();
};
if (_safe('btnGloss')) {
  // il glossario stava solo come slide: da un telefono, per usarlo si
  // attraversava l'intera lezione. Ora è sempre a portata di un tocco.
  _safe('btnGloss').onclick = () => {
    const i = slides.findIndex(s => (s.title || '').startsWith('Glossario'));
    go(i >= 0 ? i : LAST);
  };
}
const preAudio = new Audio();   // usato SOLO per scaldare la cache del browser
preAudio.preload = 'auto';
let preloadedUrl = null;
let dur = 0, wordTimings = [], chunks = [], raf = null;
let audioUnlocked = false;
let pendingAutoplay = false;
function showAudioUnlock() { const u = _btn('audioUnlock'); if (u) u.hidden = false; }
function hideAudioUnlock() { const u = _btn('audioUnlock'); if (u) u.hidden = true; }
if (IS_LOCAL_FILE) showAudioUnlock();
function tryPlay() {
  if (!audio.src || audioMuted) return Promise.resolve();
  return audio.play().then(() => { audioUnlocked = true; hideAudioUnlock(); pendingAutoplay = false; }).catch(err => {
    const blocked = err && (err.name === 'NotAllowedError' || /not allowed|gesture|interaction/i.test(err.message || ''));
    if (blocked) { pendingAutoplay = true; showAudioUnlock(); }
    else { const aerr = _btn('audioErr'); if (aerr && audio.src) aerr.hidden = false; }
  });
}
document.addEventListener('click', () => { if (pendingAutoplay && !audioUnlocked) tryPlay(); }, { capture: true });
try { const _au = _btn('audioUnlockBtn'); if (_au) _au.onclick = () => tryPlay(); } catch (e) {}

function loadAudio(i, autoplay) {
  const s = slides[i];
  // preload della traccia successiva: il server glielo permette (ri-validazione
  // 304), così quando si clicca Avanti il browser riusa il file scaricato e il
  // passaggio tra slide è senza attese.
  const nxt = slides[i + 1];
  if (nxt && nxt.audio && nxt.audio !== preloadedUrl) {
    try { preAudio.src = nxt.audio; } catch (e) {}
    preloadedUrl = nxt.audio;
  }
  audio.pause();
  audio.src = s.audio || '';
  // la velocità scelta va riapplicata a ogni slide: impostarla solo all'avvio
  // non bastava, `loadAudio` risetta l'elemento audio a ogni cambio slide
  audio.playbackRate = RATE_VALORI[rateIdx];
  audio.muted = audioMuted;
  dur = s.duration || 0;
  wordTimings = s.words || [];
  chunks = buildChunks(wordTimings);
  const fill = _btn('fill'); if (fill) fill.style.width = '0%';
  const tt = _btn('tt'); if (tt) tt.textContent = fmt(0) + ' / ' + fmt(dur);
  const cap = _btn('cap'); if (cap) cap.textContent = '';
  const bp = _btn('btnPlay'); if (bp) bp.textContent = '▶';
  const aerr = _btn('audioErr'); if (aerr) aerr.hidden = true;
  if (s.audio) {
    audio.load();
    if (autoplay) {
      // autoplay può essere bloccato dal browser fino alla prima interazione:
      // mostriamo il banner "Attiva audio" invece di fallire in silenzio
      if (audioUnlocked) { tryPlay(); }
      else { tryPlay(); }
    }
  } else {
    const aerr = _btn('audioErr');
    if (aerr) aerr.hidden = false;    // l'audio non è mai assente, ma non è mai detto
  }
}

// raggruppa le parole in frasi naturali (pausa lunga tra parole = confine)
function buildChunks(wt) {
  const out = [];
  let curChunk = null;
  wt.forEach((w2, i) => {
    if (!curChunk) {
      curChunk = { a: w2[0], b: w2[1], t: [w2[2]], wc: 1 };
    } else if (w2[0] - curChunk.b > 0.45 || curChunk.wc >= 6) {
      out.push(curChunk);
      curChunk = { a: w2[0], b: w2[1], t: [w2[2]], wc: 1 };
    } else {
      curChunk.b = w2[1];
      curChunk.t.push(w2[2]);
      curChunk.wc++;
    }
  });
  if (curChunk) out.push(curChunk);
  return out;
}

function fmt(x) {
  x = Math.max(0, x | 0);
  return Math.floor(x / 60) + ':' + String(x % 60).padStart(2, '0');
}

function setPlayIcon() {
  const p = !!audio.src && !audio.paused && !audio.ended && audio.readyState > 0;
  const bp = _btn('btnPlay');
  if (bp) bp.textContent = p ? '⏸' : '▶';
}
const _btnPlay = _safe('btnPlay');
if (_btnPlay) _btnPlay.onclick = () => {
  if (!audio.src) return;
  if (audioMuted) { applyMute(false); return; }
  if (audio.paused) { tryPlay(); } else { audio.pause(); }
};
const _btnRestart = _safe('btnRestart');
if (_btnRestart) _btnRestart.onclick = () => {
  if (!audio.src) return;
  if (audioMuted) applyMute(false);
  audio.currentTime = 0;
  tryPlay();
};
// ------------------------------------------------------------ audio globale ON/OFF
// Un solo interruttore nell'header: vale per tutta la lezione (persistito).
let audioMuted = false;
try { audioMuted = localStorage.getItem(DATA_KEY + '-muted') === '1'; } catch (e) {}
function applyMute(m) {
  audioMuted = !!m;
  try { localStorage.setItem(DATA_KEY + '-muted', audioMuted ? '1' : '0'); } catch (e) {}
  const b = _btn('btnMute');
  if (b) {
    b.setAttribute('aria-pressed', audioMuted ? 'true' : 'false');
    b.title = audioMuted ? 'Audio disattivato: riattiva la voce' : 'Disattiva la voce per tutta la lezione';
    const ic = b.querySelector('.hbtnico');
    if (ic) ic.textContent = audioMuted ? '🔇' : '🔊';
  }
  if (audioMuted) { try { audio.pause(); } catch (e) {} hideAudioUnlock(); }
  else if (audio.src && audio.paused) { tryPlay(); }
}
const _btnMute = _safe('btnMute');
if (_btnMute) _btnMute.onclick = () => applyMute(!audioMuted);
applyMute(audioMuted);
const _btnModeExam = _safe('btnModeExam');
if (_btnModeExam) _btnModeExam.onclick = () => {
  if (lessonMode === 'exam') setLessonMode('student');
  else startExam();
};
try {
  const savedMode = localStorage.getItem(DATA_KEY + '-mode');
  if (savedMode === 'exam') setLessonMode('student');
  else setLessonMode('student');
  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    navigator.serviceWorker.register('./sw.js').catch(() => {});
  }
} catch (e) { setLessonMode('student'); }

// Ultimo valore scritto: evita 3 scritture DOM a ogni frame (180/s). La
// didascalia sta dentro #audioBar, che ha un backdrop-filter: scriverla a
// 60 Hz forzava il re-blur del fondo anche a slide ferma.
// I riferimenti DOM sono messi in cache e i sottotitoli si cercano con un
// indice che avanza: `chunks.find` era O(n) a 60 Hz su una lezione lunga.
let _lastPct = -1, _lastSec = -1, _lastCap = null;
let _elFill = null, _elTT = null, _elCap = null, _capIdx = 0;
function cacheAudioEls() {
  _elFill = _btn('fill'); _elTT = _btn('tt'); _elCap = _btn('cap');
  _capIdx = 0;
}
function tick() {
  const t = audio.currentTime || 0;
  const p = dur ? Math.min(100, t / dur * 100) : 0;
  const sec = Math.floor(t);
  if (p !== _lastPct) {
    if (_elFill) _elFill.style.transform = 'scaleX(' + (p / 100) + ')';
    _lastPct = p;
  }
  if (sec !== _lastSec) {
    if (_elTT) _elTT.textContent = fmt(t) + ' / ' + fmt(dur);
    _lastSec = sec;
  }
  // indice monotono sui sottotitoli: si sposta in avanti e riparte dal basso
  if (_capIdx >= chunks.length || t < chunks[_capIdx].a) _capIdx = 0;
  while (_capIdx < chunks.length - 1 && t >= chunks[_capIdx].b) _capIdx++;
  const c = chunks[_capIdx];
  const capTxt = (c && t >= c.a && t < c.b) ? c.t.join(' ') : '';
  if (capTxt !== _lastCap) {
    if (_elCap) _elCap.textContent = capTxt;
    _lastCap = capTxt;
  }
  raf = requestAnimationFrame(tick);
}
audio.onplay = () => {
  setPlayIcon();
  _lastPct = -1; _lastSec = -1; _lastCap = null;
  cacheAudioEls();
  raf = requestAnimationFrame(tick);
};
audio.onpause = () => { setPlayIcon(); cancelAnimationFrame(raf); };
audio.onerror = () => {
  const aerr = _btn('audioErr');
  if (aerr && audio.src) aerr.hidden = false;
  setPlayIcon();
};
function markReview(i, silenzioso) {
  if (review.has(i)) return;
  review.add(i); saveReview();
  const d = _btn('dots') && _btn('dots').children[i];
  if (d) d.classList.add('rv');
  if (!silenzioso) {
    const chip = _btn('rvw');
    if (chip) {
      chip.hidden = false;
      chip.textContent = '🔖 segnalato: da rivedere';
      clearTimeout(chip._t);
      chip._t = setTimeout(() => { chip.hidden = true; }, 2600);
    }
  }
}
function unmarkReview(i) {
  review.delete(i); saveReview();
  const d = _btn('dots') && _btn('dots').children[i];
  if (d) d.classList.remove('rv');
}
audio.onended = () => {
  setPlayIcon();
};
const _seek = _safe('seek');
if (_seek) _seek.onclick = e => {
  if (!dur) return;
  const r = e.currentTarget.getBoundingClientRect();
  audio.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur;
};

// ------------------------------------------------ accessibility
const accMenu = _safe('accMenu');
const btnAcc = _safe('btnAcc');
const btnZoom = _safe('btnZoom');
let fsLvl = 0;
try { fsLvl = +(localStorage.getItem('lesson-fs') || 0); } catch (e) {}
function applyFs(n) {
  fsLvl = n;
  document.documentElement.dataset.fs = n ? String(n) : '';
  try { localStorage.setItem('lesson-fs', String(n)); } catch (e) {}
  if (btnZoom) btnZoom.textContent = ['A', 'A+', 'A++', 'A+++'][n] || 'A';
}
let zoomLock = false;
if (btnZoom) btnZoom.onclick = () => {
  if (zoomLock) return;
  zoomLock = true;
  applyFs((fsLvl + 1) % 4);
  setTimeout(() => { zoomLock = false; }, 250);
};
applyFs(fsLvl);
function applyContrast(on) {
  if (on) document.documentElement.dataset.contrast = '1';
  else delete document.documentElement.dataset.contrast;
  try { localStorage.setItem('lesson-contrast', on ? '1' : '0'); } catch (e) {}
}
function toggleContrast() { applyContrast(document.documentElement.dataset.contrast !== '1'); }
(function restoreContrast() {
  let c = '0';
  try { c = localStorage.getItem('lesson-contrast') || '0'; } catch (e) {}
  if (c === '1') document.documentElement.dataset.contrast = '1';
})();
if (btnAcc && accMenu) {
  btnAcc.onclick = e => { e.stopPropagation(); accMenu.hidden = !accMenu.hidden; };
  document.addEventListener('click', e => {
    if (!accMenu.hidden && !accMenu.contains(e.target) && e.target !== btnAcc) accMenu.hidden = true;
  });
}
// L'alto contrasto era raggiungibile SOLO con Ctrl+Alt+T (una scorciatoia
// che né studenti né docenti conoscono). Ora c'è un bottone che dice cosa fa.
const btnContrast = _safe('btnContrast');
if (btnContrast) {
  btnContrast.onclick = () => {
    applyContrast(document.documentElement.dataset.contrast !== '1');
  };
  const paintC = () => {
    const on = document.documentElement.dataset.contrast === '1';
    btnContrast.textContent = on ? '◑' : '◐';
    btnContrast.classList.toggle('on', on);
    btnContrast.title = on ? 'Alto contrasto ATTIVO (clic per disattivare)' : 'Alto contrasto (Ctrl+Alt+T)';
  };
  paintC();
  if (btnContrast._apply) btnContrast._apply(paintC);
}
// se il sistema chiede più contrasto, applicalo senza chiedere
try {
  if (window.matchMedia && window.matchMedia('(prefers-contrast: more)').matches
      && !localStorage.getItem('lesson-contrast')) {
    applyContrast(true);
  }
} catch (e) {}

// velocità audio, play continuo e glossario vengono applicati piu' in basso,
// subito dopo la creazione di `audio`: vedi la sezione "audio".
// menu header (Stampa / Tema / Accessibilità con etichette)
(function headerMenu() {
  const btn = _btn('btnMenu'), drop = _btn('hdrop');
  if (!btn || !drop) return;
  btn.onclick = e => { e.stopPropagation(); drop.hidden = !drop.hidden; };
  document.addEventListener('click', e => {
    if (!drop.hidden && !drop.contains(e.target) && e.target !== btn) drop.hidden = true;
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !drop.hidden) drop.hidden = true;
  });
  drop.querySelectorAll('button').forEach(b => {
    if (b.id !== 'btnAcc') b.addEventListener('click', () => { drop.hidden = true; });
  });
})();

// ---------------------------------------------------------------- nav
function go(i) {
  // `prev` serve per la direzione dell'animazione e per il tempo per slide
  const prev = cur;
  cur = Math.max(0, Math.min(LAST, i));
  paintMap();
  // tempo passato sulla slide appena lasciata (per il report docente)
  if (lastSlideTime !== null) {
    slideTimes[prev] = (slideTimes[prev] || 0) + Date.now() - lastSlideTime;
  }
  lastSlideTime = Date.now();
  // nel percorso di ripasso la direzione segue l'ordine dei segnalibri
  if (RIPASSO.length) {
    const k = RIPASSO.indexOf(cur);
    if (k >= 0) {
      animDir = k > RIPASSO_POS ? 'fwd' : k < RIPASSO_POS ? 'bwd' : 'init';
      RIPASSO_POS = k;
    } else animDir = cur > prev ? 'fwd' : cur < prev ? 'bwd' : 'init';
  } else {
    animDir = cur > prev ? 'fwd' : cur < prev ? 'bwd' : 'init';
  }
  render(cur);
  savePos();
}

// ---------------------------------------------------------------- ricerca
const sbox = _safe('sbox');
const _btnSearch = _safe('btnSearch');
if (sbox && _btnSearch) _btnSearch.onclick = () => {
  sbox.classList.toggle('open');
  if (sbox.classList.contains('open')) $('#sinput').focus();
  else $('#sres').innerHTML = '';
};
document.addEventListener('click', e => {
  if (sbox && sbox.classList.contains('open') && !sbox.contains(e.target) && e.target !== _btn('btnSearch')) {
    sbox.classList.remove('open');
    $('#sres').innerHTML = '';
  }
});
// Indice di ricerca costruito UNA volta: prima, a ogni keystroke si
// ricostruiva l'elenco dei testi di tutte le slide e si riapplicava
// toLowerCase() a tutto (30-80 ms di blocco main thread su lezioni lunghe).
const SEARCH_IDX = slides.map((s, i) => {
  const texts = [];
  (s.blocks || []).forEach(b => {
    ['p', 'h1', 'h2', 'quote', 'callout'].forEach(k => { if (b[k]) texts.push(b[k]); });
    if (b.list) texts.push(b.list.join(' '));
    if (b.quiz) texts.push(b.quiz.q + ' ' + b.quiz.opts.map(o => o.t).join(' '));
    if (b.classifica) (b.classifica.items || []).forEach(i2 => texts.push(i2.t));
    if (b.glossario) (b.glossario.groups || []).forEach(gr =>
      (gr.terms || []).forEach(t => texts.push(t.t + ' ' + t.d)));
  });
  return { i: i, icon: s.icon || '📄', title: s.title || ('Slide ' + (i + 1)),
           hay: ((s.title || '') + ' ' + texts.join(' ')).toLowerCase() };
});
let _searchDeb = null;
$('#sinput').addEventListener('input', e => {
  // stesso debounce (140 ms) già usato dal glossario: coerenza interna
  clearTimeout(_searchDeb);
  const raw = e.target.value;
  _searchDeb = setTimeout(() => runSearch(raw.trim().toLowerCase()), 140);
});
function runSearch(q) {
  const res = $('#sres') || sbox;   // se manca #sres (cache), degrada su sbox
  res.innerHTML = '';
  if (q.length < 2) return;
  let hits = 0;
  for (let k = 0; k < SEARCH_IDX.length && hits < 8; k++) {
    const e = SEARCH_IDX[k];
    if (e.hay.includes(q)) {
      hits++;
      const btn = el('button', null, e.icon + ' ' + e.title);
      btn.onclick = () => { go(e.i); sbox.classList.remove('open'); res.innerHTML = ''; };
      res.appendChild(btn);
    }
  }
  if (!hits) res.appendChild(el('div', 'nores', 'Nessun risultato'));
  else unlockBadge('esploratore');
}
// ------------------------------------------------------------ stampa / PDF
function printLesson() {
  const esc = escHtml;   // unica implementazione (vedi helper DOM)
  const printHead = (cls, txt) => '<' + cls + '>' + esc(txt) + '</' + cls + '>';
  let body = '';
  slides.forEach((s, i) => {
    body += '<div class="sl">';
    body += printHead('h2', (i + 1) + '. ' + (s.icon ? s.icon + ' ' : '') + (s.title || ''));
    (s.blocks || []).forEach(b => {
      if (b.callout) body += '<div class="call">' + esc(b.callout) + '</div>';
      else if (b.h1) body += printHead('h3', b.h1);
      else if (b.h2) body += printHead('h4', b.h2);
      else if (b.p) body += '<p>' + esc(b.p) + '</p>';
      else if (b.quote) body += '<blockquote>' + esc(b.quote) + (b.attr ? ' <i>— ' + esc(b.attr) + '</i>' : '') + '</blockquote>';
      else if (b.list) body += '<ul>' + b.list.map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>';
      else if (b.quiz) body += '<p><b>Quiz:</b> ' + esc(b.quiz.q) + '<br><i>Risposta: '
        + esc(((b.quiz.opts || []).filter(o => o.ok).map(o => o.t).join(' | ') || '-')) + '</i></p>';
      else if (b.vf) body += '<p><b>Vero o falso:</b></p><ul>' + b.vf.map(v2 => '<li>' + esc(v2.t)
        + ' — <i>' + (v2.ok ? 'Vero' : 'Falso') + '</i></li>').join('') + '</ul>';
      else if (b.compila) body += '<p><b>Compila:</b> ' + b.compila.map(c2 => esc(c2.frase).replace(/___/g, '<u>' + esc(c2.risposta || '…') + '</u>')).join(' · ') + '</p>';
      else if (b.classifica) body += (b.classifica.cats || []).map((cat, ci) =>
        '<p><b>' + esc(cat) + ':</b> '
        + (((b.classifica.items || []).filter(i2 => i2.cat === ci).map(i2 => esc(i2.t)).join(', ')) || '-')
        + '</p>').join('');
      else if (b.seq) body += '<p><b>Sequenza:</b> ' + esc((b.seq.passi || []).join(' → ')) + '</p>';
      else if (b.flashcards) body += '<p><b>Flashcards:</b></p><ul>'
        + (b.flashcards.cards || []).map(c2 => '<li><b>' + esc(c2.t) + '</b>: ' + esc(c2.d) + '</li>').join('') + '</ul>';
      else if (b.glossario) body += '<p><b>Glossario:</b></p>' + (b.glossario.groups || []).map(gr =>
        '<p><i>' + esc(gr.modulo || 'Modulo') + '</i></p><ul>'
        + ((gr.terms || []).map(t2 => '<li><b>' + esc(t2.t) + '</b>: ' + esc(t2.d) + '</li>').join('')) + '</ul>').join('');
    });
    body += '</div>';
  });
  const w = window.open('', '_blank');
  const escTitle = escHtml(document.title);
  w.document.write('<html><head><title>' + escTitle + ' — versione stampabile</title><style>'
    + 'body{font-family:Segoe UI,sans-serif;padding:30px;color:#111;max-width:820px;margin:auto}'
    + 'h1{font-size:22px}h2{font-size:17px;margin:18px 0 6px;border-bottom:2px solid #3a55a0;padding-bottom:4px}'
    + 'h3{font-size:15px}h4{font-size:14px}.sl{page-break-inside:avoid;margin-bottom:10px}'
    + '.call{font-size:12px;color:#3a55a0;font-weight:700;text-transform:uppercase;letter-spacing:.05em}'
    + 'blockquote{border-left:3px solid #999;margin:8px 0;padding:4px 12px;color:#333;font-style:italic}'
    + 'td,th,li,p{font-size:13px;line-height:1.5}@media print{.noprint{display:none}}'
    + '</style></head><body><h1>' + document.title + '</h1>' + body
    + '<p class="noprint"><button onclick="window.print()">Stampa / Salva come PDF</button></p>'
    + '<script>setTimeout(function(){window.print()},400)<\/script></body></html>');
  w.document.close();
}
const _bPrnt = _safe('btnPrint');
if (_bPrnt) _bPrnt.onclick = printLesson;
const _bPrev = _safe('btnPrev');
if (_bPrev) _bPrev.onclick = () => (lessonMode === 'exam' ? goExam(-1) : go(cur - 1));
const _bNext = _safe('btnNext');
if (_bNext) _bNext.onclick = () => { if (lessonMode === 'exam' && !examDone) { if (cur === LAST) finishExam(); else goExam(1); } else if (cur < LAST) go(cur + 1); else { const f = document.querySelector('#slide .fin') || document.querySelector('.fin'); if (f) f.scrollIntoView({ behavior: 'smooth', block: 'start' }); } };
document.addEventListener('keydown', e => {
  const tag = (e.target && e.target.tagName) || '';
  const inField = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  if (FOCUS) {
    // In focus resta solo il percorso dell'attività: niente scorciatoie di
    // ricerca/esame; la navigazione resta limitata alle slide condivise.
    if (e.key === 'Escape') {
      const kb = _btn('kbd'); if (kb) kb.classList.remove('open');
      const nb = _btn('nmbox'); if (nb) nb.hidden = true;
    }
    else if (e.key === 'ArrowRight' && !inField) {
      if (cur < LAST) { go(cur + 1); e.preventDefault(); }
    }
    else if (e.key === 'ArrowLeft' && !inField && cur > FOCUS_STOP) {
      go(cur - 1); e.preventDefault();
    }
    else if (e.key === ' ') {
      if (tag === 'BUTTON' || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      e.preventDefault();
      if (audio.src) { if (audio.paused) { tryPlay(); } else { audio.pause(); } }
    }
    return;
  }
  if (e.key === 'ArrowRight' && !inField) { go(cur + 1); e.preventDefault(); }
  else if (e.key === 'ArrowLeft' && !inField) { go(cur - 1); e.preventDefault(); }
  else if (e.key === 'Home' && !inField) { go(0); e.preventDefault(); }
  else if (e.key === 'End' && !inField) { go(LAST); e.preventDefault(); }
  else if (e.key === '?' || (e.shiftKey && e.key === '/')) {
    const kb = _btn('kbd'); if (kb) kb.classList.toggle('open');
  }
  else if (e.key === 'p' || e.key === 'P') {
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    e.preventDefault();
    printLesson();
  }
  else if (e.key === 'f' || e.key === 'F') {
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    const bs = _btnSearch;
    if (bs && bs.onclick) bs.onclick();
  }
  else if (e.key === 's' || e.key === 'S') {
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    e.preventDefault();
    // durante il ripasso: S passa alla segnalata successiva, alla fine conclude
    if (RIPASSO.length) {
      if (RIPASSO_POS < RIPASSO.length - 1) { go(RIPASSO[RIPASSO_POS + 1]); return; }
      RIPASSO = []; RIPASSO_POS = 0;
      try { localStorage.setItem(DATA_KEY + '-ripasso', String(Date.now())); } catch (e) {}
      const chip = _btn('rvw');
      if (chip) {
        chip.hidden = false;
        chip.textContent = '🎉 ripasso finito! riprova tra 3 giorni';
        clearTimeout(chip._t);
        chip._t = setTimeout(() => { chip.hidden = true; }, 3200);
      }
      return;
    }
    review.has(cur) ? unmarkReview(cur) : markReview(cur);
    if (review.has(cur)) unlockBadge('ripasso');
    const chip = _btn('rvw');
    if (chip) {
      chip.hidden = false;
      chip.textContent = review.has(cur) ? 'segno rimosso' : '🔖 da rivedere';
      clearTimeout(chip._t);
      chip._t = setTimeout(() => { chip.hidden = true; }, 1800);
    }
  }
  else if ((e.ctrlKey && e.altKey && (e.key === 't' || e.key === 'T'))) {
    e.preventDefault(); toggleContrast();
  }
  else if (e.key === 'u' || e.key === 'U') {
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    e.preventDefault();
    const box = _btn('nmbox');
    if (box) { box.hidden = !box.hidden; const inp = _btn('nminput'); if (inp && !box.hidden) inp.focus(); }
  }
  else if (e.key === 'Escape') {
    const kb = _btn('kbd'); if (kb) kb.classList.remove('open');
    if (sbox) { sbox.classList.remove('open'); const r = _btn('sres'); if (r) r.innerHTML = ''; }
    const nb = _btn('nmbox'); if (nb) nb.hidden = true;
  }
  else if (e.key === 'r' || e.key === 'R') {
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (audio.src) { audio.currentTime = 0; tryPlay(); }
  }
  else if (e.key === ' ') {
    if (tag === 'BUTTON' || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    e.preventDefault();
    if (audio.src) { if (audio.paused) { tryPlay(); } else { audio.pause(); } }
  }
});
if (FOCUS) {
  // Percorso "solo attività": si parte dalla slide dell'attività e si può
  // proseguire (Avanti/Indietro, audio, quiz) senza mai risalire prima di
  // quella slide né saltare altrove. Il riepilogo finale resta visibile.
  document.documentElement.dataset.focus = '1';
  try {
    const st = document.getElementById('map'); if (st) st.style.display = 'none';
    const pb = document.getElementById('pbar'); if (pb) pb.style.display = 'none';
    ['btnSearch', 'btnModeExam'].forEach(id => {
      const b = document.getElementById(id); if (b) b.style.display = 'none';
    });
    const dots = document.getElementById('dots');
    if (dots) dots.innerHTML = '<span class="stlab">🎯 Attività: si prosegue fino in fondo</span>';
    const chip = document.getElementById('prog');
    if (chip) chip.textContent = '🎯 Attività ' + (focusAct + 1) + ' / ' + slides.length;
  } catch (e) {}
  // Niente salti fuori dal percorso: Avanti/Indietro restano nel range
  // [focusAct .. ultima slide], quindi l'attività si svolge per intero.
  const _origGo = go;
  go = function(i) { _origGo(Math.max(FOCUS_STOP, Math.min(LAST, i))); };
  // la barra dei moduli porta agli altri moduli: nel percorso non serve
  const _origRender = render;
  render = function(i) {
    _origRender(i);
    try { document.querySelectorAll('.modnav').forEach(n => n.remove()); } catch (e) {}
  };
  go(focusAct);
} else if (cur > 0) {
  render(cur);
  // Ripresa a metà percorso: avviso chiaro e revocabile, così la prima
  // pagina resta sempre raggiungibile con un clic (e non sembra "sparita").
  const rip = document.createElement('div');
  rip.style.cssText = 'position:fixed;bottom:74px;left:50%;transform:translateX(-50%);'
    + 'z-index:60;background:#1d2b45;color:#eaf1ff;border:1px solid #3a4c6b;'
    + 'border-radius:10px;padding:10px 14px;font-size:14px;display:flex;gap:10px;'
    + 'align-items:center;box-shadow:0 6px 20px rgba(0,0,0,.35)';
  const lbl = document.createElement('span');
  lbl.textContent = '⏵ Ripresa dalla slide ' + (cur + 1) + ' di ' + slides.length;
  const daCapo = document.createElement('button');
  daCapo.className = 'primary';
  daCapo.textContent = '⏮ Ricomincia dall\'inizio';
  daCapo.onclick = () => { go(0); rip.remove(); };
  const chiudi = document.createElement('button');
  chiudi.textContent = '✕';
  chiudi.setAttribute('aria-label', 'Chiudi avviso di ripresa');
  chiudi.onclick = () => rip.remove();
  rip.appendChild(lbl); rip.appendChild(daCapo); rip.appendChild(chiudi);
  document.body.appendChild(rip);
  setTimeout(() => { if (rip.parentNode) rip.remove(); }, 10000);
  } else {
    render(cur);   // percorso normale, prima slide
  }
  paintMap();    // mappa del percorso alla prima apertura

// ------------------------------------------------------------ superficie di test
// Esposta VOLUTAMENTE, come già fatto per LOG e review. Serve ai test per
// verificare la logica di valutazione (denominatore fisso, soglia d'esame,
// ordine stabile delle opzioni) che prima non aveva alcuna copertura: sono
// cambiamenti che incidono sui voti dello studente, e un errore lì non si vede
// guardando la pagina. Utile anche in console per il docente.
Object.defineProperty(window, 'LESSON_STATE', {
  get: function () {
    return { results: results, cur: cur, slides: slides, activeIdx: activeIdx,
             review: review, STREAK: STREAK, LOG: LOG, lessonMode: lessonMode };
  }
});
window.LESSON_API = {
  scoreMetrics: scoreMetrics, examMetrics: examMetrics, examIdx: examIdx,
  grade: grade, attempted: attempted, stableOrder: stableOrder,
  shuffleOpts: shuffleOpts, renderBlock: renderBlock,
  paintScore: paintScore, paintDots: paintDots, go: go,
  buildExport: buildExport, buildProgress: buildProgress,
  audio: audio, slideHaAttivita: slideHaAttivita
};
