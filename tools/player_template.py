# -*- coding: utf-8 -*-
"""Genera il player completo della lezione (index.html + main.css + main.js)
senza alcuna cartella template_player: il player è scritto qui e adattato alla
lezione (titolo, tema, palette accent, numero di moduli/attività) a ogni build.

Attività supportate nelle slide:
  {"quiz": {...}}     scelta multipla
  {"match": {...}}    abbinamenti termine/definizione
  {"vf": [...]}       affermazioni Vero/Falso con spiegazione
  {"seq": {...}}      ordina la sequenza di passaggi
  {"compila": [...]}  compila il vuoto (menu di scelta)
  {"scenario": {...}} scenario decisionale "cosa faresti?"
  {"errore": {...}}   trova l'errore nel brano + correzione

Uso:  write_player(out_dir, titolo, tema='dark'|'light')
"""
import colorsys
import hashlib
import json
import re
from pathlib import Path

from common import write_text_atomic


# ------------------------------------------------------------- palette temi
# Entrambe le palette finiscono nel CSS: il toggle del player cambia
# data-theme su <html> al volo (con persistenza in localStorage).
THEMES = {
    'dark': {
        'bg': '#0b111d', 'bg2': '#101a2c', 'card': '#141f33', 'card2': '#1a2740',
        'text': '#eaf1ff', 'muted': '#9db0d0', 'line': '#26365a',
        'cardsh': 'rgba(0, 0, 0, .45)',
        'dot': 'rgba(150, 178, 224, .08)',
        'ok': '#3ddc84', 'ko': '#ff6b6b', 'warn': '#ffd166',
    },
    'light': {
        'bg': '#eef2fb', 'bg2': '#e3eaf7', 'card': '#ffffff', 'card2': '#f1f5fd',
        'text': '#17233b', 'muted': '#5a6a8a', 'line': '#d5dff0',
        'cardsh': 'rgba(25, 45, 85, .14)',
        'dot': 'rgba(35, 65, 125, .07)',
        'ok': '#128a4a', 'ko': '#d64545', 'warn': '#8a5a00',
    },
}


def _srgb_lum(hexcolor):
    """Luminanza relativa WCAG di un colore esadecimale."""
    c = hexcolor.lstrip('#')
    if len(c) == 3:
        c = ''.join(ch * 2 for ch in c)
    try:
        rgb = [int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        rgb = [0.5, 0.5, 0.5]
    out = []
    for v in rgb:
        out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast_ratio(fg, bg):
    """Rapporto di contrasto WCAG tra due colori esadecimali (1.0-21.0)."""
    a, b = _srgb_lum(fg), _srgb_lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _accent_from_title(titolo):
    """Palette accent derivata dal titolo: ogni lezione ha la sua tinta.

    Restituisce tre valori:
      accent     — il colore decorativo: aurora, gradienti, bordi, bagliori
      accent2    — la tinta complementare per i gradienti
      accent-ink — il colore di TESTO da mettere SOPRA l'accent

    Il principio: **la vivacità dipende dalla saturazione, la leggibilità dalla
    luminosità**. Le due cose vanno regolate separatamente. Prima la versione
    corretta del contrasto fissava anche la saturazione a 0,5 durante
    l'aggiustamento, e il verde acceso della lezione originale diventava
    verdolino spento; l'altra estremità (saturation 0,88 fisso) dava pulsanti con
    testo bianco a 1,53:1, illeggibili.

    Qui si parte da una saturazione alta e si tocca SOLO la luminosità finché
    il testo non raggiunge 4,5:1, scegliendo tra bianco e nero pieno (il nero
    pieno dà più margine del grigio-azzurro del tema, quindi serve meno
    schiacciamento del colore).
    """
    h = int(hashlib.sha1((titolo or 'lezione').encode('utf-8')).hexdigest(), 16)
    hue = h % 360
    sat = 0.74                       # vivace, ma non neon: niente frange sui proiettori
    lum = 0.62
    r, g, b = colorsys.hls_to_rgb(hue / 360, lum, sat)
    a = '#%02x%02x%02x' % (int(r * 255), int(g * 255), int(b * 255))
    r2, g2, b2 = colorsys.hls_to_rgb(((hue + 40) % 360) / 360, lum - 0.08, sat * 0.92)
    a2 = '#%02x%02x%02x' % (int(r2 * 255), int(g2 * 255), int(b2 * 255))

    def _ink_su(colore):
        """Tinta di testo leggibile su `colore`: bianco o nero, il migliore."""
        return ('#ffffff', contrast_ratio('#ffffff', colore)) \
            if contrast_ratio('#ffffff', colore) >= contrast_ratio('#000000', colore) \
            else ('#000000', contrast_ratio('#000000', colore))

    ink, best = _ink_su(a)
    # Se nessuna delle due tinte raggiunge 4,5:1 si regola la LUMINOSITA' (e
    # solo quella) finché non la raggiunge: la saturazione resta intatta, quindi
    # il colore resta vivo.
    if best < 4.5:
        for _ in range(16):
            # ogni passo allontana la luminosità dal punto di equilibrio (0,18)
            lum = lum * 0.86 if lum > 0.18 else lum * 1.30
            lum = min(0.94, max(0.04, lum))
            r, g, b = colorsys.hls_to_rgb(hue / 360, lum, sat)
            a = '#%02x%02x%02x' % (int(r * 255), int(g * 255), int(b * 255))
            ink, best = _ink_su(a)
            if best >= 4.5:
                break
    return a, a2, ink


def _esc(s):
    import html as _html
    return _html.escape(s or '', quote=True)


def player_version():
    """Impronta del player (JS + CSS + temi): cambia a ogni modifica del template.

    Serve al service worker: il nome della cache porta questo numero, quindi a
    ogni aggiornamento del player la vecchia cache viene eliminata invece di
    continuare a servire una lezione (o un index.html) superati.

    Il CSS entra nell'impronta con temi neutri, non con l'accent della lezione:
    altrimenti ogni lezione avrebbe una cache diversa, e soprattutto un
    ritocco di stile (che qui è successo: il contrasto dei pulsanti) non
    cambierebbe nulla e la vecchia grafica resterebbe in cache.
    """
    global _PLAYER_VERSION
    if _PLAYER_VERSION is None:
        h = hashlib.sha1()
        h.update(_js().encode('utf-8'))
        h.update(repr(THEMES).encode('utf-8'))
        h.update(_css(dict(THEMES['dark'], accent='#808080', accent2='#808080',
                           accentink='#ffffff')).encode('utf-8'))
        _PLAYER_VERSION = h.hexdigest()[:8]
    return _PLAYER_VERSION


_PLAYER_VERSION = None


def _theme_block(name, t, accent):
    """Un blocco CSS di variabili per un tema. `accent` è condiviso dai due temi.

    `accent[2]` è `--accent-ink`: il colore di testo che garantisce almeno
    4,5:1 sul pulsante con sfondo accent. Senza, il testo era bianco fisso e
    su certi titoli il contrasto scendeva a 1,53:1.
    """
    m = dict(t, accent=accent[0], accent2=accent[1], accentink=accent[2])
    return (name + " {\n"
            + "  --bg:%(bg)s; --bg2:%(bg2)s; --card:%(card)s; --card2:%(card2)s;\n"
              "  --text:%(text)s; --muted:%(muted)s; --line:%(line)s; --cardsh:%(cardsh)s;\n"
              "  --dot:%(dot)s; --ok:%(ok)s; --ko:%(ko)s; --warn:%(warn)s;\n"
              "  --accent:%(accent)s; --accent2:%(accent2)s; --accent-ink:%(accentink)s;\n"
            "}") % m


def _css(t):
    """Foglio di stile della lezione: blocchi di tema (generati qui, perché
    l'accent dipende dal titolo) + corpo stabile letto da tools/player_assets/base.css."""
    from player_assets import base_css
    dark, light = THEMES['dark'], THEMES['light']
    acc = (t['accent'], t['accent2'], t.get('accentink') or '#ffffff')
    return (_theme_block(":root, [data-theme=\"dark\"]", dark, acc)
            + "\n"
            + _theme_block("[data-theme=\"light\"]", light, acc)
            + "\n"
            + base_css())


def _js():
    """Script del player: letto da tools/player_assets/main.js.

    Era una stringa Python di ~2.450 righe dentro questo file: nessun editor di
    sintassi la poteva controllare e un errore JavaScript arrivava in produzione
    senza essere notato. Ora è un file reale, validato anche con `node --check`.
    """
    from player_assets import main_js
    return main_js()


def write_player(out_dir: Path, titolo: str, tema: str = 'dark'):
    """Scrive index.html, main.css, main.js adattati alla lezione.
    Il tema (dark/light) è il predefinito; l'utente può cambiarlo al volo dal
    player e la tinta accent deriva dal titolo. lesson-data.js viene scritto
    a parte dalla pipeline."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t = dict(THEMES.get(tema, THEMES['dark']))
    t['accent'], t['accent2'], t['accentink'] = _accent_from_title(titolo)
    html = f"""<!DOCTYPE html>
<html lang="it" data-theme="{_esc(tema)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(titolo)}</title>
<meta name="theme-color" content="#0b111d">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{_esc(titolo[:12])}">
<link rel="manifest" href="manifest.json">
<link rel="stylesheet" href="main.css?v=4">
<script>window.LESSON_DIR = {json.dumps(out_dir.name)};</script>
</head>
<body>
<header>
  <div class="logo">🎓</div>
  <h1 id="ttl">{_esc(titolo)}</h1>
  <div class="spacer"></div>
  <span id="prog" class="hchip">1 / 0</span>
  <span id="stat" class="hchip" hidden>✓</span>
  <span id="score" class="hchip" hidden>⭐ 0/0</span>
  <span id="streak" class="hchip streak" hidden>🔥</span>
  <span id="rvw" class="hchip" hidden>🔖</span>
  <span id="examTimer" class="hchip" hidden>⏱ --:--</span>
  <button id="btnModeExam" class="hbtn" title="Modalità esame: domande casuali e timer">⏱<span class="hbtntxt">Esame</span></button>
  <button id="btnMute" class="hbtn" title="Disattiva la voce per tutta la lezione" aria-pressed="false"><span class="hbtnico">🔊</span><span class="hbtntxt">Audio</span></button>
  <button id="btnSearch" class="hbtn" title="Cerca nella lezione (F)">🔍<span class="hbtntxt">Cerca</span></button>
  <div id="hmenu">
    <button id="btnMenu" class="hbtn" title="Altre azioni: stampa, tema, accessibilità">☰<span class="hbtntxt">Menu</span></button>
    <div id="hdrop" hidden>
      <button id="btnPrint" title="Stampa / PDF della lezione intera"><span class="ic">🖨</span><span>Stampa / PDF</span></button>
      <button id="btnTheme" title="Tema chiaro / scuro"><span class="ic">☀️</span><span>Tema chiaro / scuro</span></button>
      <button id="btnAcc" title="Accessibilità: testo più grande, alto contrasto (Ctrl+Alt+T)"><span class="ic">♿</span><span>Accessibilità</span></button>
      <button id="btnBadges" title="I tuoi badge collezionabili"><span class="ic">🏅</span><span>Badge</span></button>
      <div id="accMenu" hidden>
        <button id="btnZoom" class="abar" title="Dimensione del testo (F)">A</button>
        <button id="btnContrast" class="abar" title="Alto contrasto (Ctrl+Alt+T)">◐</button>
        <button id="btnSpeed" class="abar" title="Velocita di riproduzione dell'audio">1×</button>
        <button id="btnAuto" class="abar" title="Riproduzione continua: passa da solo alla slide successiva">⏭</button>
        <button id="btnGloss" class="abar" title="Glossario sempre raggiungibile">📖</button>
      </div>
    </div>
  </div>
</header>
<div id="sbox">
  <input id="sinput" type="text" placeholder="Cerca nella lezione…" autocomplete="off">
  <div id="sres"></div>
</div>
<div id="nmbox" hidden>
  <span>Come ti chiami? (per il report dei risultati)</span>
  <input id="nminput" type="text" maxlength="40" placeholder="il tuo nome" autocomplete="off">
  <button id="nmok">OK</button>
</div>
<div id="examBanner" hidden></div>
<div id="pbar"><div id="pfill"></div></div>
<div id="map"></div>
<main><div id="stage"><div id="slide"></div></div></main>
<!-- regione live DEDICATA: prima era aria-live sul contenitore della slide,
     quindi il lettore di schermo annunciava l'intero testo del capitolo a ogni
     cambio (oltre 100 parole, interrotto a metà) invece del titolo. -->
<div id="slideAnnounce" class="sr" aria-live="polite" aria-atomic="true"></div>
<div id="audioBar">
  <!-- aria-label sui controlli a solo glifo: al lettore di schermo "▶" e "⟲"
       non dicono nulla. -->
  <button id="btnPlay" title="Riproduci / pausa (Spazio)"
          aria-label="Riproduci o metti in pausa l'audio">▶</button>
  <button id="btnRestart" class="abar" title="Riascolta dall'inizio (R)"
          aria-label="Riascolta la slide dall'inizio">⟲</button>
  <div id="seek" role="slider" tabindex="0" aria-label="Posizione dell'audio"
       aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div id="fill"></div></div>
  <span id="tt">0:00 / 0:00</span>
  <span id="cap"></span>
  <span id="audioErr" hidden title="Traccia audio non disponibile per questa slide">⚠ audio</span>
  <span id="audioUnlock" hidden><button id="audioUnlockBtn" title="Attiva l'audio (richiesto dal browser)">🔊 Attiva audio</button></span>
</div>
<nav>
  <button id="btnPrev">← Indietro</button>
  <div id="dots"></div>
  <button id="btnNext" class="primary">Avanti →</button>
</nav>
<script src="lesson-data.js?v=4"></script>
<script src="main.js?v=4"></script>
<div id="kbd">
  <div class="ktitle">Scorciatoie da tastiera</div>
  <div class="krow"><span class="kkey">→</span><span>slide successiva</span></div>
  <div class="krow"><span class="kkey">←</span><span>slide precedente</span></div>
  <div class="krow"><span class="kkey">Spazio</span><span>riproduci / pausa</span></div>
  <div class="krow"><span class="kkey">R</span><span>riascolta da capo</span></div>
  <div class="krow"><span class="kkey">S</span><span>segna “da rivedere”</span></div>
  <div class="krow"><span class="kkey">F</span><span>cerca nella lezione</span></div>
  <div class="krow"><span class="kkey">P</span><span>stampa / PDF della lezione</span></div>
  <div class="krow"><span class="kkey">Home</span><span>prima slide</span></div>
  <div class="krow"><span class="kkey">End</span><span>ultima slide</span></div>
  <div class="krow"><span class="kkey">?</span><span>questo aiuto</span></div>
  <div class="krow"><span class="kkey">U</span><span>imposta il tuo nome (report)</span></div>
  <div class="krow"><span class="kkey">Ctrl+Alt+T</span><span>alto contrasto</span></div>
  <div class="krow"><span class="kkey">Esc</span><span>chiudi finestre</span></div>
</div>
</body>
</html>"""
    write_text_atomic(out_dir / 'index.html', html)
    write_text_atomic(out_dir / 'main.css', _css(t))
    write_text_atomic(out_dir / 'main.js', _js())
    # PWA offline: manifest + service worker (cachizza la lezione aperta)
    try:
        write_text_atomic(out_dir / 'manifest.json',
            '{"name": %s, "short_name": %s, "display": "standalone", '
            '"start_url": "./index.html", "background_color": "#0b111d", '
            '"theme_color": "#0b111d"}'
            % (json.dumps(titolo), json.dumps(titolo[:12])))
        # NB: il nome della cache porta l'impronta del player e quella della
        # lezione, così (a) a ogni aggiornamento del player la cache vecchia
        # viene eliminata, (b) due lezioni non condividono lo stesso bucket.
        # Soprattutto: la strategia è NETWORK-FIRST, quindi il service worker
        # non può continuare a servire un index.html precedente annullando
        # bust_cache(); la cache serve solo da ripiego quando la rete (o il
        # server locale) non risponde.
        cache_name = "lesson-%s-%s" % (
            player_version(),
            hashlib.sha1((titolo or "lezione").encode("utf-8")).hexdigest()[:8])
        write_text_atomic(out_dir / 'sw.js',
            "const C=%s;\n" % json.dumps(cache_name)
            + "self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll("
            "['./index.html','./main.css','./main.js','./lesson-data.js']).catch(()=>{}))"
            ".then(()=>self.skipWaiting()));});\n"
            # elimina TUTTE le cache che non sono quella corrente
            "self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all("
            "ks.filter(k=>k!==C).map(k=>caches.delete(k)))));self.clients.claim();});\n"
            # network-first: la rete (o il server) ha la precedenza, la cache
            # è il ripiego offline. ignoreSearch matcha './main.js?v=<hash>'
            # con la voce precaricata './main.js'.
            "self.addEventListener('fetch',e=>{const req=e.request; if(req.method!=='GET') return;\n"
            "const same=new URL(req.url).origin===self.location.origin; if(!same) return;\n"
            "e.respondWith(fetch(req).then(res=>{if(res&&res.ok&&res.type==='basic'){"
            "const copy=res.clone();caches.open(C).then(c=>c.put(req,copy)).catch(()=>{});} return res;})"
            ".catch(()=>caches.match(req,{ignoreSearch:true})"
            ".then(r=>r||req.mode==='navigate'?caches.match('./index.html',{ignoreSearch:true})"
            ":Response.error())));});\n")
    except OSError:
        pass


def bust_cache(out_dir: Path):
    """Versiona i riferimenti css/js/dati nell'index con l'hash del contenuto:
    il browser ricarica sempre la versione giusta, mai una cache stantia
    (niente più pagine bianche per index vecchio + js nuovo)."""
    out_dir = Path(out_dir)
    idx = out_dir / 'index.html'
    if not idx.exists():
        return
    try:
        html = idx.read_text(encoding='utf-8')
    except OSError:
        return
    for name in ('main.css', 'main.js', 'lesson-data.js'):
        p = out_dir / name
        if not p.exists():
            continue
        v = hashlib.sha1(p.read_bytes()).hexdigest()[:10]
        html = re.sub(rf'({re.escape(name)})(\?v=[0-9a-f]+)?', rf'\1?v={v}', html)
    # non riscrivere se il contenuto non cambia: evita di invalidare la mtime
    # (che farebbe rispondere 200 invece di 304) e di far rileggere index.html
    try:
        if idx.read_text(encoding='utf-8') == html:
            return
    except OSError:
        pass
    write_text_atomic(idx, html)
