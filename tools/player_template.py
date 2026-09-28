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
    dark, light = THEMES['dark'], THEMES['light']
    acc = (t['accent'], t['accent2'], t.get('accentink') or '#ffffff')
    return (_theme_block(":root, [data-theme=\"dark\"]", dark, acc)
            + "\n"
            + _theme_block("[data-theme=\"light\"]", light, acc)
            + """
/* ------------------------------------------------ base + sfondo aurora */
/* testo solo per lettori di schermo */
.sr { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  color: var(--text);
  font-family: "Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", sans-serif;
  display: flex; flex-direction: column;
  /* 100dvh, non 100vh: sui browser mobili 100vh non include l'area sotto la
     barra degli indirizzi, quindi i controlli in fondo finivano tagliati. */
  min-height: 100vh; min-height: 100dvh;
  transition: background .35s ease, color .35s ease;
  background: var(--bg); overflow-x: hidden;
}
body::before {
  content: ''; position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background:
    radial-gradient(1100px 620px at 6% -12%, var(--blob1), transparent 62%),
    radial-gradient(950px 600px at 104% 4%, var(--blob2), transparent 60%),
    radial-gradient(760px 520px at 50% 118%, var(--blob3), transparent 66%);
  animation: aurora 30s ease-in-out infinite alternate;
  /* L'aurora sta DIETRO a tre backdrop-filter (header, barra audio, nav):
     finché il gradiente si muove il blur deve rileggerlo e rifiltrarlo a
     ogni frame, per sempre. Con `body.idle` (nessuna interazione da 2,5 s)
     l'animazione si ferma: da 3 blur/frame continui a 0 su slide ferma. */
  animation-play-state: running;
  will-change: opacity;
}
body.idle body::before { animation-play-state: paused; }
@media (prefers-reduced-motion: reduce) {
  body::before { animation: none; }
}
body::after {
  content: ''; position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background-image: radial-gradient(var(--dot) 1px, transparent 1.5px);
  background-size: 30px 30px;
  -webkit-mask-image: radial-gradient(1000px 640px at 50% 18%, #000 25%, transparent 78%);
  mask-image: radial-gradient(1000px 640px at 50% 18%, #000 25%, transparent 78%);
}
:root {
  --blob1: color-mix(in srgb, var(--accent) 26%, transparent);
  --blob2: color-mix(in srgb, var(--accent2) 22%, transparent);
  --blob3: color-mix(in srgb, var(--accent) 12%, transparent);
}
@keyframes aurora {
  from { transform: translate3d(-1.5%, -1%, 0) scale(1); }
  to   { transform: translate3d(1.5%, 1.5%, 0) scale(1.05); }
}

/* ------------------------------------------------ header + progressione */
header {
  position: relative; z-index: 5; display: flex; align-items: center; gap: 12px;
  padding: 10px 18px;
  background: color-mix(in srgb, var(--bg2) 78%, transparent);
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line);
  transition: background .3s ease, border-color .3s ease;
}
header .logo {
  width: 36px; height: 36px; border-radius: 11px; flex: 0 0 auto;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  display: flex; align-items: center; justify-content: center; font-size: 18px;
  box-shadow: 0 4px 16px color-mix(in srgb, var(--accent) 45%, transparent),
              inset 0 0 0 1px rgba(255, 255, 255, .22);
}
header h1 {
  font-size: 15px; font-weight: 700; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; letter-spacing: .01em;
  min-width: 0; flex: 1 1 auto;
}
header .spacer { flex: 1; }
.hchip {
  font-size: 12px; font-weight: 600; white-space: nowrap; padding: 4px 11px;
  border-radius: 999px; border: 1px solid var(--line);
  color: var(--muted); background: color-mix(in srgb, var(--card) 60%, transparent);
}
#prog { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; }
#stat { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 35%, transparent);
  background: color-mix(in srgb, var(--ok) 12%, transparent); }
#score { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  background: color-mix(in srgb, var(--accent) 13%, transparent);
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; }
.hbtn {
  border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 11px; padding: 6px 12px; cursor: pointer; font-size: 15px;
  transition: border-color .2s, transform .15s, box-shadow .2s;
}
.hbtn:hover { border-color: var(--accent); transform: translateY(-1px);
  box-shadow: 0 4px 14px color-mix(in srgb, var(--accent) 22%, transparent); }
.hbtn .hbtntxt { font-size: 12.5px; font-weight: 700; margin-left: 2px; }
#hmenu { position: relative; flex: 0 0 auto; }
#hdrop {
  position: absolute; right: 0; top: calc(100% + 10px); z-index: 80;
  min-width: 230px; display: flex; flex-direction: column; gap: 4px;
  padding: 8px; border-radius: 14px; border: 1px solid var(--line);
  background: var(--card); box-shadow: 0 18px 50px rgba(0, 0, 0, .45);
  animation: rise .18s both;
}
#hdrop[hidden] { display: none; }
#hdrop > button {
  display: flex; align-items: center; gap: 11px; width: 100%;
  background: transparent; border: none; border-radius: 10px;
  color: var(--text); font: inherit; font-size: 14px; font-weight: 600;
  padding: 10px 12px; cursor: pointer; text-align: left;
}
#hdrop > button:hover { background: color-mix(in srgb, var(--accent) 14%, transparent); }
#hdrop > button .ic { font-size: 16px; flex: 0 0 auto; }
#hdrop #accMenu { position: static; flex-direction: row; gap: 8px;
  border-top: 1px solid var(--line); padding-top: 8px; margin-top: 4px; }
#pbar { position: relative; z-index: 5; height: 4px; background: color-mix(in srgb, var(--line) 55%, transparent); }
/* scaleX invece di width: animare la LARGHEZZA forza il layout (reflow) a ogni
   frame della transizione; scaleX resta sul compositorio. */
#pfill {
  height: 100%; width: 100%; border-radius: 0 4px 4px 0;
  background: linear-gradient(90deg, var(--accent), var(--accent2));
  background-size: 200% 100%;
  animation: progshine 3s linear infinite;
  transform: scaleX(0); transform-origin: left center;
  transition: transform .5s cubic-bezier(.2, .7, .3, 1);
  box-shadow: 0 0 14px color-mix(in srgb, var(--accent) 60%, transparent);
}
@keyframes progshine {
  from { background-position: 0% 0; }
  to   { background-position: 200% 0; }
}

/* ------------------------------------------------ stage + slide card */
main { position: relative; z-index: 1; flex: 1; display: flex; min-height: 0; }
#stage { flex: 1; display: flex; align-items: flex-start; justify-content: center; padding: 22px; overflow: auto; }
#stage #slide { margin: auto; }
#slide {
  position: relative; overflow: hidden;
  width: min(940px, 100%);
  background: linear-gradient(165deg, var(--card) 0%, var(--card2) 135%);
  border: 1px solid color-mix(in srgb, var(--line) 85%, transparent);
  border-radius: 22px; padding: 34px 40px;
  /* 80px di blur su un elemento a tutto schermo: il paint della card costava
     piu' del contenuto. 46px danno la stessa separazione visiva. */
  box-shadow: 0 24px 46px var(--cardsh),
              0 2px 0 color-mix(in srgb, var(--accent) 22%, transparent) inset;
  transition: background .3s ease, border-color .3s ease;
}
#slide::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 5px;
  background: linear-gradient(90deg, var(--accent), var(--accent2) 60%, transparent);
  border-radius: 22px 22px 0 0;
}
#slide::after {
  content: ''; position: absolute; top: -40px; right: -60px; width: 300px; height: 220px;
  background: radial-gradient(closest-side, color-mix(in srgb, var(--accent) 13%, transparent), transparent);
  pointer-events: none;
}
#slide > * { position: relative; }
#slide h1 { font-size: clamp(22px, 3.1vw, 32px); line-height: 1.22; margin-bottom: 12px;
  font-weight: 800; letter-spacing: -.015em; }
@supports (-webkit-background-clip: text) or (background-clip: text) {
  #slide h1 {
    background: linear-gradient(95deg, var(--text) 30%,
      color-mix(in srgb, var(--text) 55%, var(--accent)) 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
}
#slide h2 {
  font-size: 20px; margin: 20px 0 10px; font-weight: 700;
  display: flex; align-items: center; gap: 10px;
}
#slide h2::before { content: ''; width: 5px; height: 20px; border-radius: 3px;
  background: linear-gradient(var(--accent), var(--accent2)); }
/* ------------------------------------------------ leggibilità (proiettore)
   Il corpo testo era 16px fissi: a 3 m da uno schermo da 55" equivalgono a
   ~1,6 mm di altezza, cioè poco più di 5 mm — sotto la soglia di leggibilità
   (la regola pratica è 1/30 dell'altezza dello schermo). Qui la dimensione
   scala con la larghezza del viewport, così su un proiettore da 1920 px il
   corpo passa a 21 px e su un tablet da 1024 px resta comodo.
   `--slide-fs` è l'unica leva: i livelli di ingrandimento manuale (tasto F)
   la moltiplicano, non la sovrascrivono. */
:root { --slide-fs: var(--slide-fs-base); }
#slide { font-size: var(--slide-fs); }
/* lunghezza della riga: 940px di larghezza a 16px davano ~106 caratteri per
   riga, oltre i 75 oltre i quali l'occhio perde il ritorno a inizio riga.
   68ch con lo spazio di respiro del padding. */
#slide p, #slide li, #slide blockquote, .scn .situ, .erra .brano, .vfq {
  max-width: 68ch;
}
#slide p { font-size: 1em; line-height: 1.72; color: var(--text); margin: 8px 0; }
.callout {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: .74em; font-weight: 800; letter-spacing: .09em;
  text-transform: uppercase; color: var(--accent);
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 15%, transparent),
    color-mix(in srgb, var(--accent2) 12%, transparent));
  border: 1px solid color-mix(in srgb, var(--accent) 38%, transparent);
  border-radius: 999px; padding: 5px 14px; margin-bottom: 16px;
}
.callout::before { content: '✦'; font-size: 11px; }
#slide ul { margin: 12px 0 6px 6px; list-style: none; }
#slide li {
  font-size: .97em; line-height: 1.65; margin: 8px 0; padding-left: 26px; position: relative;
}
#slide li::before {
  content: ''; position: absolute; left: 4px; top: .68em; width: 7px; height: 7px;
  border-radius: 2px; transform: rotate(45deg);
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  box-shadow: 0 0 8px color-mix(in srgb, var(--accent) 55%, transparent);
}
#slide blockquote {
  border-left: 3px solid var(--accent); padding: 10px 18px; margin: 20px 0 6px;
  font-size: 18px; font-style: italic; color: var(--muted);
  background: color-mix(in srgb, var(--accent) 6%, transparent);
  border-radius: 0 12px 12px 0;
}
#slide .attr { font-size: 13px; color: var(--muted); margin-bottom: 8px; }
#slide .icon {
  font-size: 46px; line-height: 1; margin-bottom: 12px; display: inline-block;
  filter: drop-shadow(0 6px 16px color-mix(in srgb, var(--accent) 55%, transparent));
  animation: rise .5s both, floaty 3.4s ease-in-out .7s infinite alternate;
}
@keyframes floaty { from { transform: translateY(0); } to { transform: translateY(-5px); } }
@keyframes pop { from { transform: scale(.6); opacity: 0; } to { transform: scale(1); opacity: 1; } }
.banner {
  margin-bottom: 18px; padding: 12px 16px; border-radius: 12px; font-size: 14px; font-weight: 600;
  display: flex; gap: 10px; align-items: center;
  background: color-mix(in srgb, var(--ko) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--ko) 40%, transparent);
}

/* ------------------------------------------------ quiz + scenario (opzioni) */
.quiz q { display: block; font-size: clamp(18px, 2.4vw, 22px); font-weight: 800;
  line-height: 1.45; margin-bottom: 16px; letter-spacing: -.01em; }
.opt {
  display: flex; gap: 12px; align-items: center; width: 100%; text-align: left;
  background: var(--card2); border: 1px solid var(--line); color: var(--text);
  border-radius: 14px; padding: 13px 16px; margin: 9px 0; cursor: pointer; font-size: 15px;
  box-shadow: 0 1px 0 rgba(0, 0, 0, .08);
  transition: border-color .2s, transform .15s, background .3s ease, box-shadow .2s;
}
.opt:hover { border-color: var(--accent); transform: translateX(4px);
  box-shadow: 0 4px 18px color-mix(in srgb, var(--accent) 16%, transparent); }
.opt .letter {
  flex: 0 0 auto; width: 30px; height: 30px; border-radius: 50%; font-weight: 800; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--accent);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  transition: background .2s, color .2s;
}
.quiz .revealed .opt:not(.correct):not(.wrong), .scn .revealed .opt:not(.correct):not(.wrong) { opacity: .55; }
.opt.correct { border-color: var(--ok);
  background: color-mix(in srgb, var(--ok) 14%, transparent);
  box-shadow: 0 4px 20px color-mix(in srgb, var(--ok) 22%, transparent); }
.opt.correct .letter { background: var(--ok); color: #fff; border-color: var(--ok); }
.opt.wrong { border-color: var(--ko); background: color-mix(in srgb, var(--ko) 11%, transparent); }
.opt.wrong .letter { background: var(--ko); color: #fff; border-color: var(--ko); }
.fb { margin-top: 14px; padding: 13px 16px; border-radius: 12px; font-size: 14.5px; display: none;
  line-height: 1.6; border: 1px solid transparent; }
.fb.ok { display: block; background: color-mix(in srgb, var(--ok) 10%, transparent);
  border-color: color-mix(in srgb, var(--ok) 35%, transparent); }
.adapt { margin-top: 10px; padding: 11px 15px; border-radius: 12px; font-size: 14px;
  line-height: 1.55; border: 1px dashed var(--accent);
  background: color-mix(in srgb, var(--accent) 9%, transparent); }
.adapt button { margin-left: 8px; }
.fb.ko { display: block; background: color-mix(in srgb, var(--ko) 9%, transparent);
  border-color: color-mix(in srgb, var(--ko) 35%, transparent); }
.fb .verdict { font-weight: 800; font-size: 15px; margin-bottom: 4px; }
.fb.ok .verdict { color: var(--ok); }
.fb.ko .verdict { color: var(--ko); }
.fb .item { margin: 4px 0; }

/* ------------------------------------------------ matching */
.match .pairs { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 14px; }
.match .col { display: flex; flex-direction: column; gap: 9px; }
.match .collabel { font-size: 11px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 2px; padding-left: 4px; }
.match .chip {
  background: var(--card2); border: 1px solid var(--line); color: var(--text);
  border-radius: 12px; padding: 11px 14px; font-size: 14px; cursor: pointer; text-align: left;
  position: relative; transition: border-color .2s, box-shadow .2s, transform .15s, opacity .3s;
}
.match .col.L .chip { border-left: 3px solid var(--accent); }
.match .col.R .chip { border-right: 3px solid var(--accent2); }
.match .chip:hover { border-color: var(--accent); transform: translateY(-1px); }
.match .chip.sel { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 16%, transparent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 35%, transparent); }
.match .chip.done { opacity: .5; cursor: default; border-color: var(--ok) !important; }
.match .chip.done::after { content: ' ✓'; color: var(--ok); font-weight: 800; }
.match .chip.err { border-color: var(--ko); animation: shake .4s; }
.match .msg { margin-top: 14px; font-size: 14px; color: var(--muted); min-height: 20px; font-weight: 600; }

/* ------------------------------------------------ vero / falso */
.vf { display: flex; flex-direction: column; gap: 14px; margin-top: 8px; }
.vfcard {
  background: var(--card2); border: 1px solid var(--line); border-radius: 16px;
  padding: 16px 18px; position: relative; overflow: hidden;
  transition: background .3s ease, border-color .3s ease;
}
.vfcard::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
  background: linear-gradient(var(--accent), var(--accent2)); opacity: 0; transition: opacity .3s; }
.vfcard.answered::before { opacity: .9; }
.vfq { font-size: 16px; font-weight: 600; margin-bottom: 12px; line-height: 1.5; padding-right: 6px; }
.vfrow { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
/* non solo colore: lo stato porta anche un simbolo, cosic' funziona per chi
   non distingue il rosso dal verde (daltonismo) e a bassa luminosita' */
.vfbtn::after { font-size: 1.05em; font-weight: 900; }
.vfbtn[data-ok="1"]::after { content: "✓"; color: var(--ok); }
.vfbtn[data-ok="0"]::after { content: "✕"; color: var(--ko); }
.vfbtn[data-ok="1"] { border-color: var(--ok); border-width: 2px; }
.vfbtn[data-ok="0"] { border-color: var(--ko); border-width: 2px; }
.vfbtn {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 12px; padding: 10px 0; font-size: 14px; font-weight: 800; cursor: pointer;
  transition: border-color .2s, background .2s, transform .15s;
}
.vfbtn:hover { border-color: var(--accent); transform: translateY(-2px); }
.vfbtn.ok { border-color: var(--ok); background: color-mix(in srgb, var(--ok) 22%, transparent); color: var(--ok); }
.vfbtn.ko { border-color: var(--ko); background: color-mix(in srgb, var(--ko) 16%, transparent); color: var(--ko); }
.vffb { margin-top: 12px; font-size: 14px; padding: 11px 14px; border-radius: 11px; display: none;
  line-height: 1.55; font-weight: 600; }
.vffb.ok { display: block; background: color-mix(in srgb, var(--ok) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--ok) 40%, transparent); color: var(--ok); }
.vffb.ko { display: block; background: color-mix(in srgb, var(--ko) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--ko) 40%, transparent); color: var(--ko); }

/* ------------------------------------------------ sequenza */
.seq > p { font-weight: 600; }
.seqpool, .seqline { display: flex; flex-direction: column; gap: 9px; margin-top: 12px; }
.seqline { min-height: 34px; padding-top: 10px; border-top: 2px dashed var(--line); margin-top: 16px; }
.seqline .seqlabel { font-size: 11px; color: var(--muted); text-transform: uppercase;
  letter-spacing: .1em; font-weight: 800; margin-bottom: 4px; }
.seqchip {
  background: var(--card2); border: 1px solid var(--line); color: var(--text);
  border-radius: 12px; padding: 11px 15px; font-size: 14px; cursor: pointer; text-align: left;
  display: flex; align-items: center; gap: 12px;
  transition: border-color .2s, background .3s ease, transform .15s, opacity .3s;
}
.seqpool .seqchip::before { content: '⇅'; color: var(--muted); font-size: 13px; flex: 0 0 auto; }
.seqchip:hover { border-color: var(--accent); transform: translateX(4px); }
.seqline .seqchip { animation: rise .35s both; }
.seqline .seqchip::before { content: none; }
.seqchip.ok { border-color: var(--ok);
  background: color-mix(in srgb, var(--ok) 12%, transparent);
  cursor: default; font-weight: 700; opacity: 1; }
.seqchip.ok::after { content: '✓'; margin-left: auto; color: var(--ok); font-weight: 900; }
.seqchip.err { border-color: var(--ko); animation: shake .4s; }
.seqmsg { margin-top: 14px; font-size: 15px; color: var(--ok); font-weight: 800; }

@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-6px); }
  75% { transform: translateX(6px); }
}

/* ------------------------------------------------ flashcards (nuova attività) */
.flash > p { font-weight: 600; }
.fcdeck { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 12px; justify-content: center; }
.fcard { width: 168px; height: 118px; cursor: pointer; perspective: 900px; }
.fcard.fcon .fcinner { box-shadow: 0 10px 26px color-mix(in srgb, var(--accent) 30%, transparent); }
.fcinner { position: relative; width: 100%; height: 100%; transition: transform .5s cubic-bezier(.3, .8, .3, 1);
  transform-style: preserve-3d; }
.fcard.flip .fcinner { transform: rotateY(180deg); }
.fcface { position: absolute; inset: 0; backface-visibility: hidden; -webkit-backface-visibility: hidden;
  border-radius: 15px; padding: 12px 13px; display: flex; flex-direction: column; overflow: hidden; }
.fcfront { background: linear-gradient(150deg, var(--card2), color-mix(in srgb, var(--accent) 14%, var(--card)));
  border: 1.5px solid color-mix(in srgb, var(--accent) 40%, var(--line)); align-items: flex-start; }
.fcback { background: var(--card); border: 1.5px solid var(--line); transform: rotateY(180deg); }
.fclabel { font-size: 10px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); font-weight: 800; }
.fcterm { font-weight: 800; font-size: 15.5px; margin-top: 8px; line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
/* la definizione completa si legge premendo: prima veniva tagliata a meta'
   senza nessun indizio, quindi lo studente restava con una definizione
   monca e non poteva sapere che il resto esisteva */
.fcdef { font-size: 12.5px; line-height: 1.55; margin-top: 8px;
  overflow-y: auto; max-height: 9em; }
.fchint { margin-top: auto; font-size: 11px; color: var(--muted); font-style: italic; }
.fcdots { display: flex; gap: 7px; justify-content: center; margin-top: 12px; }
.fcdots span { width: 9px; height: 9px; border-radius: 50%; background: var(--line); cursor: pointer; transition: background .2s, transform .2s; }
.fcdots span.on { background: linear-gradient(135deg, var(--accent), var(--accent2)); transform: scale(1.25); }
.fcctrl { display: flex; gap: 9px; justify-content: center; margin-top: 10px; }
.fcctrl button { border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 10px; padding: 8px 16px; cursor: pointer; font-weight: 800; font-size: 13.5px; font-family: inherit;
  transition: border-color .2s, transform .15s; }
.fcctrl button:hover { border-color: var(--accent); transform: translateY(-1px); }
.fcverify { margin-top: 18px; border-top: 2px dashed var(--line); padding-top: 14px; display: flex; flex-direction: column; gap: 10px; }
.fcverify > p { font-weight: 700; }
.fcrow { display: grid; grid-template-columns: minmax(110px, 190px) 1fr; gap: 10px; align-items: center; }
/* su telefono la griglia a due colonne schiacciava le opzioni a 100 px */
@media (max-width: 560px) { .fcrow { grid-template-columns: 1fr; gap: 6px; } }
.fcrow > .fcterm { margin: 0; font-size: 14px; }
.fcrowopts { display: flex; gap: 7px; flex-wrap: wrap; }
.opt.small { font-size: 12px; padding: 9px 12px; border-radius: 9px; text-align: left;
  white-space: normal; line-height: 1.4; }

/* ------------------------------------------------ glossario strutturato */
.glos > p { font-weight: 600; }
.glosearch { width: 100%; margin: 12px 0 4px; padding: 11px 15px; border-radius: 12px;
  border: 1px solid var(--line); background: var(--card2); color: var(--text);
  font: inherit; font-size: 14.5px; }
.glosearch:focus { outline: none; border-color: var(--accent); }
.glocount { font-size: 12.5px; color: var(--muted); margin: 2px 0 10px; }
.glogroup { margin: 12px 0 4px; font-size: 13px; font-weight: 800; letter-spacing: .04em;
  text-transform: uppercase; color: var(--accent); }
.gloterm { border: 1px solid var(--line); border-radius: 12px; margin: 7px 0;
  background: var(--card2); overflow: hidden; }
.gloterm > button { width: 100%; display: flex; justify-content: space-between; align-items: center;
  gap: 10px; background: none; border: none; color: var(--text); font: inherit;
  font-weight: 700; font-size: 15px; padding: 12px 15px; cursor: pointer; text-align: left; }
.gloterm > button .arrow { color: var(--accent); font-size: 12px; transition: transform .2s; }
.gloterm.open > button .arrow { transform: rotate(90deg); }
.glodef { display: none; padding: 0 15px 13px; font-size: 14px; line-height: 1.6; color: var(--text); }
.gloterm.open .glodef { display: block; }
.glomod { display: inline-block; margin-top: 8px; font-size: 12.5px; font-weight: 700;
  color: var(--accent); background: none; border: none; cursor: pointer; padding: 0; font-family: inherit; }
.glomod:hover { text-decoration: underline; }
.gloempty { color: var(--muted); font-style: italic; padding: 12px 4px; }

/* ------------------------------------------------ compila il vuoto */
.cmp { display: flex; flex-direction: column; gap: 16px; margin-top: 8px; }
.cmpfrase { font-size: 16.5px; line-height: 1.9; }
.cmpblank {
  display: inline-block; min-width: 100px; border: none; cursor: pointer;
  border-bottom: 2px dashed var(--accent);
  background: color-mix(in srgb, var(--accent) 9%, transparent); color: var(--text);
  font: inherit; font-weight: 800; text-align: center; padding: 2px 10px; margin: 0 4px;
  border-radius: 10px 10px 0 0; position: relative;
  transition: background .2s, border-color .2s;
}
.cmpblank::after { content: '▾'; font-size: 10px; margin-left: 5px; color: var(--accent); }
.cmpblank:hover { background: color-mix(in srgb, var(--accent) 20%, transparent); }
.cmpblank.done { border-bottom-style: solid; cursor: default; }
.cmpblank.done::after { content: none; }
.cmpblank.ok { border-color: var(--ok); color: var(--ok);
  background: color-mix(in srgb, var(--ok) 13%, transparent); }
.cmpblank.ko { border-color: var(--ko); color: var(--ko);
  background: color-mix(in srgb, var(--ko) 11%, transparent); }
.cmpmenu {
  position: fixed; z-index: 50; display: none; flex-direction: column; gap: 5px;
  background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 7px; box-shadow: 0 18px 50px var(--cardsh), 0 0 0 1px color-mix(in srgb, var(--accent) 20%, transparent);
  min-width: 200px; backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
}
.cmpmenu.open { display: flex; animation: rise .18s both; }
.cmpmenu button {
  border: 1px solid transparent; background: transparent; color: var(--text);
  border-radius: 9px; padding: 9px 13px; text-align: left; cursor: pointer; font-size: 14.5px;
  transition: background .15s, border-color .15s;
}
.cmpmenu button:hover { background: color-mix(in srgb, var(--accent) 14%, transparent);
  border-color: color-mix(in srgb, var(--accent) 35%, transparent); }
.cmpmsg { margin-top: 14px; font-size: 14.5px; font-weight: 700; min-height: 20px; color: var(--muted);
  transition: color .3s; }
.cmpmsg.ok { color: var(--ok); }
.cmpmsg.ko { color: var(--ko); }

/* ------------------------------------------------ scenario decisionale */
.scn .situ { font-size: 1em; line-height: 1.72; padding: 15px 18px; border-radius: 13px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 10%, transparent),
    color-mix(in srgb, var(--accent2) 8%, transparent));
  border-left: 3px solid var(--accent); margin-bottom: 14px; font-weight: 500; }
.scn .expl { margin-top: 10px; font-size: 14px; color: var(--muted); line-height: 1.6; font-style: italic; }

/* ------------------------------------------------ trova l'errore */
.erra .brano { font-size: 17px; line-height: 2; padding: 16px 18px; border-radius: 14px;
  background: var(--card2); border: 1px dashed var(--line); }
.erra .w { cursor: pointer; border-radius: 5px; padding: 1px 3px; transition: background .15s; }
.erra .w:hover { background: color-mix(in srgb, var(--accent) 22%, transparent); }
.erra .w.pick { background: color-mix(in srgb, var(--accent) 32%, transparent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 45%, transparent); }
.erra .w.hit { background: color-mix(in srgb, var(--ok) 38%, transparent); font-weight: 800;
  text-decoration: underline wavy var(--ok) 1.5px; }
.erra .w.miss { background: color-mix(in srgb, var(--ko) 28%, transparent); }
.erra .msg { margin-top: 12px; font-size: 14px; color: var(--muted); min-height: 20px; font-weight: 600; }
.erra .fixbox {
  margin-top: 12px; display: none; gap: 10px; align-items: center; flex-wrap: wrap;
  padding: 13px 16px; border-radius: 13px;
  background: color-mix(in srgb, var(--ok) 9%, transparent);
  border: 1px solid color-mix(in srgb, var(--ok) 38%, transparent);
  animation: rise .25s both;
}
.erra .fixbox.show { display: flex; }
.erra .fixbox select {
  border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 9px; padding: 9px 12px; font-size: 14px; cursor: pointer; font-weight: 600;
}
.erra .fixbox button {
  border: none; background: linear-gradient(135deg, var(--accent), var(--accent2)); color: var(--accent-ink);
  border-radius: 10px; padding: 9px 18px; cursor: pointer; font-size: 14px; font-weight: 800;
  box-shadow: 0 4px 14px color-mix(in srgb, var(--accent) 40%, transparent);
  transition: transform .15s;
}
.erra .fixbox button:hover { transform: translateY(-1px); }
.erra .expl { margin-top: 10px; font-size: 14px; color: var(--ok); line-height: 1.6; font-weight: 700; }

/* --- trova l'errore: passaggi guidati --- */
.erra .steps { display: flex; align-items: center; gap: 7px; margin-bottom: 12px; flex-wrap: wrap; }
.erra .snum { width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-size: 13px; font-weight: 800; font-family: ui-monospace, Consolas, monospace;
  border: 2px solid var(--line); color: var(--muted); background: var(--card2);
  transition: border-color .25s, color .25s, background .25s, box-shadow .25s; }
.erra .snum.cur { border-color: var(--accent); color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent); }
.erra .snum.done { border-color: var(--ok); color: var(--ok);
  background: color-mix(in srgb, var(--ok) 14%, transparent); }
.erra .stxt { font-size: 12.5px; color: var(--muted); font-weight: 700; margin-left: 6px; letter-spacing: .3px; }
.erra .hint { min-height: 20px; font-size: 14.5px; font-weight: 700; color: var(--muted);
  margin-top: 12px; transition: color .25s; }
.erra .fixbox { flex-direction: column; align-items: stretch; }
.erra .flab { font-weight: 800; font-size: 14px; }
.erra .flopt { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
  border: 1.5px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 11px; padding: 11px 14px; cursor: pointer; font-size: 14.5px; font-weight: 600;
  font-family: inherit; transition: border-color .2s, transform .15s, background .2s; }
.erra .flopt:hover { border-color: var(--accent); transform: translateX(3px); }
.erra .flopt .letter { flex: 0 0 auto; width: 26px; height: 26px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center; font-size: 12.5px; font-weight: 800;
  border: 1.5px solid var(--line); background: var(--card2); transition: background .2s, border-color .2s; }
.erra .flopt.wrong { border-color: var(--ko); background: color-mix(in srgb, var(--ko) 10%, transparent);
  animation: shake .4s; }
.erra .flopt.wrong .letter { background: var(--ko); color: #fff; border-color: var(--ko); }
.erra .why { margin-top: 12px; font-size: 14px; font-weight: 700; color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  border: 1px dashed color-mix(in srgb, var(--accent) 40%, transparent);
  border-radius: 11px; padding: 10px 14px; animation: rise .25s both; }

/* ------------------------------------------------ audio bar */
#audioBar {
  position: relative; z-index: 5; display: flex; align-items: center; gap: 10px;
  flex-wrap: wrap; padding: 9px 18px;
  border-top: 1px solid var(--line);
  background: color-mix(in srgb, var(--bg2) 80%, transparent);
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
  transition: background .3s ease, border-color .3s ease;
}
#btnPlay {
  width: 42px; height: 42px; border-radius: 50%; border: none; cursor: pointer;
  background: linear-gradient(135deg, var(--accent), var(--accent2)); color: var(--accent-ink); font-size: 16px;
  flex: 0 0 auto; box-shadow: 0 4px 16px color-mix(in srgb, var(--accent) 45%, transparent);
  transition: transform .15s;
}
#btnPlay:hover { transform: scale(1.1); }
.abar {
  border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 10px; min-width: 40px; height: 34px; padding: 0 10px; cursor: pointer;
  font-size: 13px; font-weight: 700; flex: 0 0 auto; font-family: inherit;
  transition: border-color .2s, color .2s, box-shadow .2s, transform .15s;
}
.abar:hover { border-color: var(--accent); transform: translateY(-1px); }
/* stato attivo dei toggle: usato dai tasti velocita e play continuo */
.abar.on { color: var(--accent-ink); background: var(--accent);
  border-color: var(--accent); font-weight: 800; }
#seek { flex: 1; height: 7px; border-radius: 4px;
  background: color-mix(in srgb, var(--line) 75%, transparent); position: relative; cursor: pointer;
  overflow: hidden; }
#fill { position: absolute; inset: 0; border-radius: 4px;
  background: linear-gradient(90deg, var(--accent), var(--accent2));
  transform: scaleX(0); transform-origin: left center; will-change: transform; }
#tt { font-size: 12.5px; color: var(--muted); min-width: 92px; text-align: right;
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; }
#cap { min-height: 22px; font-size: .82em; color: var(--muted); font-weight: 600;
  text-align: center; flex: 1; }
/* I sottotitoli NON spariscono su schermi piccoli: sotto i 760 px passavano
   a display:none, cioè lo studente su telefono restava senza la trascrizione
   della lezione. Su schermi stretti vanno a capo, non eliminati. */
@media (max-width: 760px) {
  #cap { font-size: .78em; min-height: 32px; }
  #audioBar { flex-wrap: wrap; }
}
#audioErr { color: var(--warn, #ffd166); font-size: 12px; font-weight: 600; flex: 0 0 auto;
  border: 1px solid color-mix(in srgb, var(--warn, #ffd166) 40%, transparent);
  border-radius: 999px; padding: 3px 10px;
  background: color-mix(in srgb, var(--warn, #ffd166) 10%, transparent); }
#audioUnlock { display: flex; align-items: center; gap: 8px; padding: 7px 12px; border-radius: 10px; flex: 0 0 auto;
  background: color-mix(in srgb, var(--warn, #ffd166) 16%, transparent);
  border: 1px solid color-mix(in srgb, var(--warn, #ffd166) 40%, transparent); font-size: 13px; font-weight: 700; }
#audioUnlock[hidden] { display: none !important; }
#audioUnlock button { border: none; background: linear-gradient(135deg, var(--accent), var(--accent2)); color: var(--accent-ink);
  border-radius: 8px; padding: 6px 12px; cursor: pointer; font-weight: 800; font-family: inherit; }

/* ------------------------------------------------ ricerca */
#sbox { position: fixed; z-index: 60; top: 58px; right: 16px; width: min(360px, 92vw);
  display: none; flex-direction: column; border: 1px solid var(--line);
  border-radius: 14px; background: var(--card); box-shadow: 0 18px 50px rgba(0,0,0,.35);
  overflow: hidden; }
#sbox.open { display: flex; animation: rise .18s both; }
#sinput { border: none; outline: none; background: var(--card2); color: var(--text);
  padding: 12px 14px; font-size: 14.5px; border-bottom: 1px solid var(--line);
  font-family: inherit; }
#sinput::placeholder { color: var(--muted); }
#sres { max-height: 44vh; overflow: auto; }
#sres button { display: block; width: 100%; text-align: left; border: none;
  background: transparent; color: var(--text); padding: 10px 14px; cursor: pointer;
  font-size: 13.5px; font-family: inherit; border-bottom: 1px solid var(--line); }
#sres button:hover { background: color-mix(in srgb, var(--accent) 15%, transparent); }
#sres .nores { padding: 12px 14px; color: var(--muted); font-size: 13px; }

/* ------------------------------------------------ guida tastiera */
#kbd { position: fixed; z-index: 70; inset: auto 16px 76px auto; display: none;
  flex-direction: column; gap: 7px; padding: 16px 18px; border-radius: 14px;
  border: 1px solid var(--line); background: var(--card);
  box-shadow: 0 18px 50px rgba(0,0,0,.35); max-width: 320px; }
#kbd.open { display: flex; animation: rise .18s both; }
#kbd .ktitle { font-weight: 800; font-size: 14px; margin-bottom: 4px; }
#kbd .krow { display: flex; gap: 10px; align-items: center; font-size: 13px; color: var(--muted); }
#kbd .kkey { min-width: 52px; text-align: center; font-family: ui-monospace, Consolas, monospace;
  font-size: 11.5px; font-weight: 700; color: var(--text);
  border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 6px; padding: 3px 6px;
  background: var(--card2); }

/* ------------------------------------------------ accessibilità */
/* I livelli di ingrandimento moltiplicano `--slide-fs`: prima scrivevano
   `font-size` in percentuale e sovrascrivevano la scala con il viewport,
   quindi su proiettore ("livello 0") il testo restava illeggibile. */
html[data-fs="1"] { --slide-fs: calc(var(--slide-fs-base) * 1.14); }
html[data-fs="2"] { --slide-fs: calc(var(--slide-fs-base) * 1.30); }
html[data-fs="3"] { --slide-fs: calc(var(--slide-fs-base) * 1.48); }
:root { --slide-fs-base: clamp(16px, 0.95vw + 8px, 22px); }
#slide { font-size: var(--slide-fs); }
html[data-contrast="1"] { --text: #ffffff; --muted: #e6e6e6; --bg: #000000; --bg2: #0a0a0a;
  --card: #111111; --card2: #1a1a1a; --line: #555555; --accent: #ffd166; --accent2: #8ecaff; }
html[data-contrast="1"] body { background: #000; }
html[data-contrast="1"] body::before, html[data-contrast="1"] body::after { display: none; }
html[data-contrast="1"] .opt, html[data-contrast="1"] .flopt, html[data-contrast="1"] .cmpmenu button {
  border-width: 2px; }
html[data-contrast="1"] header, html[data-contrast="1"] nav, html[data-contrast="1"] #audioBar {
  background: #000; }
#accMenu { position: absolute; top: 100%; right: 0; display: flex; flex-direction: column;
  gap: 6px; padding: 10px; border: 1px solid var(--line); border-radius: 12px;
  background: var(--card); box-shadow: 0 12px 34px rgba(0,0,0,.3); z-index: 80; }
#accMenu[hidden] { display: none; }
#btnZoom { min-width: 64px; }
#nmbox { position: fixed; z-index: 65; bottom: 84px; right: 16px; display: flex;
  align-items: center; gap: 9px; padding: 12px 15px; border-radius: 13px;
  border: 1px solid var(--line); background: var(--card);
  box-shadow: 0 16px 44px rgba(0,0,0,.35); max-width: min(380px, 92vw);
  animation: rise .2s both; }
#nmbox[hidden] { display: none; }
#nmbox span { font-size: 13px; font-weight: 700; color: var(--muted); }
#nminput { border: 1.5px solid var(--line); background: var(--card2); color: var(--text);
  border-radius: 9px; padding: 8px 11px; font-size: 14px; font-family: inherit;
  min-width: 130px; }
#nminput:focus { border-color: var(--accent); outline: none; }
#nmok { border: none; background: linear-gradient(135deg, var(--accent), var(--accent2));
  color: #fff; border-radius: 9px; padding: 8px 15px; cursor: pointer;
  font-size: 13px; font-weight: 800; font-family: inherit; }
#examTimer { color: var(--warn); font-size: 12px; font-weight: 800; }
#examBanner { margin: 8px auto; max-width: 940px; padding: 9px 14px; border-radius: 10px;
  background: color-mix(in srgb, var(--warn) 13%, transparent); border: 1px solid var(--warn);
  color: var(--text); font-size: 13px; font-weight: 700; }
/* Modalità esame: il banner promette "feedback nascosto fino alla fine", ma
   nascondevano solo i TESTI: `.opt.correct`/.opt.wrong` restavano visibili, e
   l'opzione giusta veniva evidenziata in verde a ogni domanda. In un test a
   15 minuti le domande successive erano già state rivelate. */
html[data-mode="exam"] .fb, html[data-mode="exam"] .vffb,
html[data-mode="exam"] .seqmsg, html[data-mode="exam"] .cmpmsg,
html[data-mode="exam"] .clmsg { display: none !important; }
html[data-mode="exam"] .opt.correct, html[data-mode="exam"] .opt.wrong,
html[data-mode="exam"] .chip.ok, html[data-mode="exam"] .chip.no { display: none !important; }
html[data-mode="exam"] .opt .letter { color: inherit !important; }
html[data-mode="exam"] #map { opacity: .35; pointer-events: none; }

/* ------------------------------------------------ export docente */
.exprow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px;
  padding: 11px 14px; border-radius: 12px; background: color-mix(in srgb, var(--accent) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); }
.exprow .explab { font-size: 13px; font-weight: 700; color: var(--muted); }
.exprow button { border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 9px; padding: 7px 13px; cursor: pointer; font-size: 13px; font-weight: 700;
  font-family: inherit; transition: border-color .2s, transform .15s; }
.exprow button:hover { border-color: var(--accent); transform: translateY(-1px); }
.namerow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
.namerow input { border: 1.5px solid var(--line); background: var(--card2); color: var(--text);
  border-radius: 9px; padding: 9px 12px; font-size: 14px; font-family: inherit; min-width: 180px; }
.namerow input:focus { border-color: var(--accent); outline: none; }

/* ------------------------------------------------ nav */
nav {
  position: relative; z-index: 5;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 11px 18px; border-top: 1px solid var(--line);
  background: color-mix(in srgb, var(--bg2) 80%, transparent);
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
  transition: background .3s ease, border-color .3s ease;
}
nav button {
  border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 12px; padding: 10px 20px; cursor: pointer; font-size: 14px; font-weight: 700;
  transition: border-color .2s, transform .15s, box-shadow .2s;
}
nav button:hover { border-color: var(--accent); }
nav button.primary {
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  border: none; color: var(--accent-ink); box-shadow: 0 6px 20px color-mix(in srgb, var(--accent) 40%, transparent);
}
nav button.primary:hover { transform: translateY(-2px); }
nav button:disabled { opacity: .35; cursor: default; transform: none !important; box-shadow: none !important; }
button:active:not(:disabled) { transform: scale(.96); }
#dots { display: flex; gap: 7px; flex-wrap: wrap; justify-content: center; align-items: center; }
#dots button { position: relative; padding: 0; border: none; font: inherit;
  width: 12px; height: 12px; border-radius: 50%;
  background: var(--line); background-clip: padding-box;
  border: 5px solid transparent; /* area di tocco allargata, pallino invariato */
  cursor: pointer; transition: background .2s, transform .2s, box-shadow .2s;
}
#dots button:hover { transform: scale(1.3); }
#dots button.on {
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  transform: scale(1.3);
  box-shadow: 0 0 10px color-mix(in srgb, var(--accent) 60%, transparent);
}
#dots button.done { background: var(--ok); }
#dots button.rv { box-shadow: 0 0 0 2px color-mix(in srgb, var(--ko) 75%, transparent); }
#dots button.rv::after { content: '🔖'; position: absolute; top: -11px; right: -6px; font-size: 10px; }
#dots button.done.on { background: linear-gradient(135deg, var(--accent), var(--accent2)); }

/* streak: serie di risposte corrette consecutive */
#streak.streak { color: #ff9f43; border-color: color-mix(in srgb, #ff9f43 50%, transparent);
  background: color-mix(in srgb, #ff9f43 14%, transparent);
  animation: pop .3s both; }

/* ------------------------------------------------ navigazione moduli */
.modnav { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.modnav button {
  border: 1px solid var(--line); background: color-mix(in srgb, var(--card2) 70%, transparent);
  color: var(--muted); border-radius: 999px; padding: 5px 13px; cursor: pointer;
  font-size: 12.5px; font-weight: 700; font-family: inherit;
  transition: border-color .2s, color .2s, background .2s, transform .15s;
}
.modnav button:hover { border-color: var(--accent); color: var(--text); transform: translateY(-1px); }
.modnav button.on {
  color: var(--accent-ink); border-color: transparent;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  box-shadow: 0 3px 12px color-mix(in srgb, var(--accent) 40%, transparent);
}
.modnav .mnlabel { font-size: 11px; color: var(--muted); align-self: center;
  font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }

/* ------------------------------------------------ pannello finale + sfida */
.fin { margin-top: 22px; padding-top: 18px; border-top: 1px dashed var(--line); }
.fin .callout { margin-top: 4px; }
.sumrow {
  display: flex; gap: 14px; flex-wrap: wrap; align-items: stretch; margin: 8px 0 16px;
}
.sumcard {
  flex: 1 1 150px; min-width: 150px; text-align: center;
  background: var(--card2); border: 1px solid var(--line); border-radius: 16px;
  padding: 14px 12px;
}
.sumcard .big { font-size: 26px; font-weight: 900; line-height: 1.1;
  background: linear-gradient(95deg, var(--accent), var(--accent2));
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.sumcard .lab { font-size: 11px; color: var(--muted); margin-top: 5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em; }
.medal { font-size: 54px; line-height: 1; text-align: center; margin: 6px 0 2px;
  filter: drop-shadow(0 6px 18px color-mix(in srgb, var(--accent) 45%, transparent));
  animation: floaty 3s ease-in-out infinite alternate; }
.stars { text-align: center; font-size: 26px; letter-spacing: 4px; margin: 4px 0 10px; }
.stars .off { opacity: .22; }
.fin .actrow { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; margin-top: 8px; }
.fin .actrow button {
  border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 12px; padding: 10px 18px; cursor: pointer; font-size: 14px; font-weight: 700;
  font-family: inherit; transition: border-color .2s, transform .15s, box-shadow .2s;
}
.fin .actrow button:hover { border-color: var(--accent); transform: translateY(-2px); }
.fin .actrow button.primary {
  border: none; color: var(--accent-ink);
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  box-shadow: 0 6px 20px color-mix(in srgb, var(--accent) 40%, transparent);
}
.chal { margin-top: 16px; }
.chal .qtop { display: flex; align-items: center; gap: 10px; margin: 10px 0 6px;
  font-size: 12.5px; color: var(--muted); font-weight: 700; }
.chal .qtop .qnum { margin-left: auto; font-family: ui-monospace, Consolas, monospace; }
.chal .sc { color: var(--accent); }
.chal .start {
  border: none; cursor: pointer; margin: 6px 0; font-family: inherit;
  background: linear-gradient(135deg, var(--accent), var(--accent2)); color: var(--accent-ink);
  border-radius: 14px; padding: 13px 26px; font-size: 15px; font-weight: 800;
  box-shadow: 0 6px 22px color-mix(in srgb, var(--accent) 45%, transparent);
  transition: transform .15s;
}
.chal .start:hover { transform: translateY(-2px); }
.chal .fb { margin-top: 10px; }
.chal .finish { text-align: center; padding: 8px 0; }
.chal .finish .res { font-size: 24px; font-weight: 900; margin: 8px 0 2px; }

.chal q { display: block; font-size: 18px; font-weight: 700; line-height: 1.5;
  margin: 2px 0 6px; }
.chal .fb { margin-top: 4px; }

/* ------------------------------------------------ classifica (smista) */
.classify .clzones { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-top: 10px; }
.clzone { border: 2px dashed var(--line); border-radius: 14px; padding: 10px; min-height: 110px;
  background: color-mix(in srgb, var(--card2) 55%, transparent); cursor: pointer;
  transition: border-color .2s, background .2s; }
.clzone:hover, .clzone.over { border-color: var(--accent); }
.clzone.done { border-style: solid; border-color: var(--ok);
  background: color-mix(in srgb, var(--ok) 10%, transparent); cursor: default; }
.clzname { font-weight: 800; font-size: 14.5px; margin-bottom: 8px; color: var(--text); }
.clzlist { display: flex; flex-direction: column; gap: 6px; min-height: 30px; }
.clpool { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 4px; min-height: 40px; }
.clpill { border: 1.5px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 999px; padding: 8px 14px; cursor: grab; font-size: 13.5px; font-weight: 600;
  font-family: inherit; transition: border-color .15s, opacity .2s; }
.clpill:hover { border-color: var(--accent); }
.clpill.sel { border-color: var(--accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 45%, transparent); }
.clpill.in { opacity: .92; cursor: default;
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  border-color: color-mix(in srgb, var(--accent) 55%, transparent); }
.clhint { font-size: 12.5px; color: var(--muted); }
.clmsg { margin-top: 10px; font-weight: 800; color: var(--ok); animation: rise .3s both; }
@keyframes shakeX { 0%,100% { transform: translateX(0); } 25% { transform: translateX(-5px); } 75% { transform: translateX(5px); } }
.clpill.shake { animation: shakeX .35s; border-color: var(--ko); }
  /* il colore da solo non basta: lo shake e il bordo spesso segnalano l'errore */

/* ------------------------------------------------ mappa del percorso */
#map { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-start; justify-content: center;
  padding: 10px 18px 6px; }
#map:empty { display: none; }
#map .st { display: flex; flex-direction: column; align-items: center; gap: 3px; min-width: 74px; cursor: pointer; }
#map .stico { width: 34px; height: 34px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-size: 16px; border: 2px solid var(--line); background: var(--card);
  position: relative;
  transition: border-color .2s, box-shadow .2s, transform .2s; }
#map .stlab { font-size: 10.5px; color: var(--muted); font-weight: 700; text-align: center; max-width: 100px; }
#map .st.reached .stico { border-color: var(--ok); background: color-mix(in srgb, var(--ok) 16%, transparent); }
/* pulseS su un pseudo-elemento, non su box-shadow: animare box-shadow
   richiede un repaint dell'elemento e del suo alone a ogni frame, per sempre.
   Sul pseudo-elemento dedicato è una questione di compositorio. */
#map .st.now .stico { border-color: var(--accent); transform: scale(1.1); }
#map .st.now .stico::after {
  content: ''; position: absolute; inset: -3px; border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--accent) 55%, transparent);
  animation: pulseS 1.6s ease-in-out infinite alternate;
  pointer-events: none;
}
#map .st.now .stlab { color: var(--text); }
@keyframes pulseS {
  from { opacity: .85; transform: scale(.9); }
  to   { opacity: .35; transform: scale(1.25); }
}

/* ------------------------------------------------ badge */
.badge-toast { position: fixed; z-index: 95; bottom: 84px; right: 16px; display: flex; gap: 10px;
  align-items: center; padding: 12px 16px; border-radius: 14px; background: var(--card);
  border: 1px solid var(--accent); box-shadow: 0 14px 40px rgba(0,0,0,.4); animation: rise .3s both;
  max-width: 300px; }
.badge-toast .bicon { font-size: 26px; }
.badge-toast .btit { font-weight: 800; font-size: 13.5px; }
.badge-toast .bdesc { font-size: 12px; color: var(--muted); }
.badgerow { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 12px; justify-content: center; }
.badgerow .badgelab { font-size: 12px; font-weight: 800; color: var(--muted);
  text-transform: uppercase; letter-spacing: .06em; }
.badge { border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
  background: color-mix(in srgb, var(--accent) 12%, transparent); border-radius: 999px;
  padding: 5px 12px; font-size: 12.5px; font-weight: 700; }
.bdov { position: fixed; inset: 0; z-index: 90; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; padding: 20px; }
.bdbox { background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 22px;
  max-width: 560px; width: 100%; max-height: 86vh; overflow: auto;
  box-shadow: 0 24px 70px rgba(0,0,0,.5); }
.bdbox h2 { margin: 0 0 14px; }
.bdgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; margin-bottom: 14px; }
.bdcard { border: 1px solid var(--line); border-radius: 12px; padding: 12px 8px; text-align: center; opacity: .55; }
.bdcard.got { opacity: 1; border-color: color-mix(in srgb, var(--accent) 55%, transparent);
  background: color-mix(in srgb, var(--accent) 9%, transparent); }
.bdcard .bdico { font-size: 26px; }
.bdcard .bdnome { font-weight: 800; font-size: 13px; margin-top: 4px; }
.bdcard .bddesc { font-size: 11px; color: var(--muted); margin-top: 2px; }
.bdbox button.primary { border: none; color: var(--accent-ink); border-radius: 10px; padding: 9px 18px;
  cursor: pointer; font-weight: 800; font-family: inherit;
  background: linear-gradient(135deg, var(--accent), var(--accent2)); }

/* ------------------------------------------------ confetti */
.confetti {
  position: fixed; top: -14px; z-index: 99; pointer-events: none;
  width: 10px; height: 14px; border-radius: 2px; opacity: .95;
  animation: confettiFall linear forwards;
}
@keyframes confettiFall {
  to { transform: translateY(112vh) rotate(720deg); opacity: 0; }
}

/* ------------------------------------------------ animazioni */
@keyframes slideIn {
  from { opacity: 0; transform: translateY(28px) scale(.985); }
  to   { opacity: 1; transform: none; }
}
@keyframes slideInF {
  from { opacity: 0; transform: translateX(46px) scale(.99); }
  to   { opacity: 1; transform: none; }
}
@keyframes slideInB {
  from { opacity: 0; transform: translateX(-46px) scale(.99); }
  to   { opacity: 1; transform: none; }
}
@keyframes rise {
  from { opacity: 0; transform: translateY(15px); }
  to   { opacity: 1; transform: none; }
}
#slide { animation: slideIn .45s cubic-bezier(.2, .7, .3, 1); }
#slide > * { animation: rise .5s both; }
#slide > *:nth-child(2) { animation-delay: .05s; }
#slide > *:nth-child(3) { animation-delay: .1s; }
/* l'anello di focus usa --text, non --accent: sull'accent chiaro (tema light)
   il contrasto dell'anello scendeva a 1,53:1 e l'indicatore di tastiera
   diventava praticamente invisibile. --text è sempre >= 7:1 sul fondo. */
:focus-visible { outline: 3px solid var(--text); outline-offset: 2px; }
/* Coarse pointer = dito: sotto i 44px i bersagli di tocco sono troppo piccoli
   per un bambino. Non si applica al mouse (pointer: fine), dove un bottone
   piccolo è comodo. */
@media (pointer: coarse) {
  nav button, .opt, .vfbtn, .match .chip, .fcctrl button, .cmpmenu button,
  .bdbox button, .fin button, .actrow button, .abar, #btnPlay,
  #hdrop button, .accMenu button, #dots button, #fcard, .bdcard button {
    min-height: 44px;
  }
  #dots button { min-width: 44px; }
  .fcctrl button, .cmpmenu button, .bdbox button, .fin button,
  .actrow button, .abar { padding: 11px 18px; }
  #btnPlay { width: 48px; height: 48px; font-size: 19px; }
  .match .chip { padding: 14px 16px; }
}
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; }
  html { scroll-behavior: auto !important; }
}
@media (max-width: 640px) {
  #slide { padding: 22px 20px; }
  #slide h1 { font-size: 23px; }
  #slide li { padding-left: 22px; }
  .match .pairs { grid-template-columns: 1fr; }
  .vfrow { grid-template-columns: 1fr; }
  /* header compatto: resta solo il contatore, il resto va nel menu */
  header { gap: 8px; padding: 9px 12px; }
  header .spacer { display: none; }
  header .hchip { display: none; }
  header #prog.hchip { display: inline-block; }
  header h1 { font-size: 13.5px; }
  .hbtn .hbtntxt { display: none; }
  .hbtn { padding: 6px 10px; }
  /* nav su due righe: dots sopra, pulsanti sotto a tutta larghezza */
  nav { flex-wrap: wrap; gap: 9px; padding: 10px 12px; }
  #dots { order: -1; flex: 1 1 100%; gap: 9px; }
  nav button { flex: 1 1 0; padding: 11px 8px; font-size: 13.5px; white-space: nowrap; }
  #audioBar { gap: 8px; padding: 8px 12px; }
  #tt { min-width: 0; }
  .clzones { grid-template-columns: 1fr; }
  #map { gap: 7px; padding: 8px 12px 2px; }
  #map .stlab { display: none; }
  #map .stico { width: 28px; height: 28px; font-size: 13px; }
  .badge-toast { bottom: 130px; }
}
"""
    )


def _js():
    return r"""
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

// Riproduzione continua: al termine dell'audio passa da solo alla slide
// successiva. Promessa dal manuale e mai implementata: chi ascoltava senza
// toccare lo schermo restava fermo sulla stessa slide.
let autoNext = true;
try { autoNext = localStorage.getItem(DATA_KEY + '-autonext') !== '0'; } catch (e) {}
function applyAutoNext() {
  const b = _safe('btnAuto');
  if (b) {
    b.classList.toggle('on', autoNext);
    b.title = autoNext ? 'Riproduzione continua: attiva (alla fine l\'audio passa alla slide seguente)'
                       : 'Riproduzione continua: spenta (resta sulla slide)';
  }
  try { localStorage.setItem(DATA_KEY + '-autonext', autoNext ? '1' : '0'); } catch (e) {}
}

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
const ORDER_CACHE_MAX = 400;
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
function blockKey(idx, bidx, kind) { return idx + ':' + bidx + ':' + kind; }
function clearOrder(idx, bidx) {
  // svuota SOLO il blocco modificato dal docente (salvataggio dal pannello
  // editor), non gli altri
  const pre = idx + ':' + bidx + ':';
  for (const k of Array.from(ORDER_CACHE.keys())) {
    if (k.startsWith(pre)) ORDER_CACHE.delete(k);
  }
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
// Riproduzione continua: al termine dell'audio si passa alla slide seguente.
//
// MA SOLO sulle slide di contenuto. Su una slide con attivita' la narrazione
// finisce mentre lo studente sta ancora leggendo la domanda, e veniva
// catapultato sulla slide successiva: non aveva piu' il tempo di rispondere,
// e il pulsante "Avanti" non serviva a niente. Ora sulle attivita' si resta
// fermi finche' lo studente non sceglie (o non preme Avanti).
function slideHaAttivita(i) {
  // Qualcosa da fare qui? Comprende anche le flashcard, che non sono
  // conteggiate nel punteggio (sono esercizio facoltativo) ma sono comunque
  // cliccabili: su quelle slide l'avanzamento automatico sarebbe sgradito.
  return (slides[i] && slides[i].blocks || []).some(b =>
    ACT_TYPES.some(t => b[t]) || b.flashcards || (b.quiz && b.quiz.q));
}
audio.addEventListener('ended', () => {
  if (!autoNext || lessonMode === 'exam') return;
  if (cur < LAST && !slideHaAttivita(cur)) go(cur + 1);
});
// velocità: applicata qui, ora che `audio` esiste
applyRate(); applyAutoNext();
if (_safe('btnSpeed')) _safe('btnSpeed').onclick = () => {
  rateIdx = (rateIdx + 1) % RATE_VALORI.length;
  applyRate();
};
if (_safe('btnAuto')) _safe('btnAuto').onclick = () => { autoNext = !autoNext; applyAutoNext(); };
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
  audio: audio, slideHaAttivita: slideHaAttivita,
  autoNext: function () { return autoNext; },
  setAutoNext: function (v) { autoNext = !!v; applyAutoNext(); }
};
"""


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
