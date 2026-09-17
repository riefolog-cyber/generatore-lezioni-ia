# Promemoria — rete di classe e pannello lezioni
> Configurazione finale scelta: PC in Wi-Fi al router (lezione) + cavo USB-Ethernet alla presa scolastica (internet PC).

## 1. Router Netgear DGND3700 (rete di classe)
- Rete locale: `192.168.0.x` — pagina gestione: `http://192.168.0.1` (scrivere `http://` davanti, non `https`)
- Wi-Fi classe (2,4 GHz): SSID **`IRC`**, WPA2-PSK (AES), password impostata da te
- DHCP: **attivo**, intervallo **`192.168.0.10` – `192.168.0.254`**
  (il `.2` riservato deve restare FUORI dal pool, altrimenti la reservation viene ignorata)
- Reservation (**LAN Setup → Address Reservation**): MAC Wi-Fi PC `F4-28-9D-78-7F-2D` → IP **`192.168.0.2`**
  (quello `FA-F3-...` visto in precedenza era un MAC casuale: NON usarlo)
- Porta **gialla (WAN): vuota** — il cavo della scuola nella WAN non prende internet, e il firmware originale non fa da repeater Wi-Fi: la WAN resta inutilizzata

## 2. PC docente (doppia connessione)
- **Wi-Fi** → `IRC` (rete classe, serve le lezioni)
  - Windows: **indirizzi hardware casuali DISATTIVATI** per questa rete (altrimenti il MAC cambia e la reservation salta)
  - MAC reale: `F4-28-9D-78-7F-2D` → prende sempre `192.168.0.2`
- **Adattatore USB-Ethernet** (Realtek, MAC `00-E0-4C-69-49-D2`) → **presa di rete scolastica** (internet per il PC)
- Dopo modifiche alla reservation: disconnetti/riconnetti il Wi-Fi (o `ipconfig /release` + `ipconfig /renew`) e verifica con `ipconfig` di avere `192.168.0.2`

## 3. Pannello lezioni
- `config.json`: `"porta": 8341`, `"lan_ip_fisso": "192.168.0.2"` → il box "Condividi in classe" mostra sempre `http://192.168.0.2:8341/`
- Dopo ogni modifica a `config.json` o al codice: **chiudere TUTTI i server vecchi e rilanciare `AVVIA.bat`** (un processo vecchio sulla 8341 mostra la pagina vecchia)
- Alla prima richiesta del firewall Windows: consentire Python
- Il pannello ascolta su tutte le interfacce (`0.0.0.0`, verifica con `netstat -ano | findstr 8341`)
- A casa (altra rete) il `.0.2` non esiste: usare l'IP del Wi-Fi di casa oppure svuotare `lan_ip_fisso` e riavviare (lì l'auto-rilevamento funziona)

## 4. Routine in classe
1. Router acceso (WAN vuota)
2. PC: Wi-Fi su `IRC` + USB-Ethernet alla presa scolastica; `ipconfig` deve dare `192.168.0.2` sul Wi-Fi
3. Avviare `AVVIA.bat`, verificare che esista la cartella `*_lesson` (generare le lezioni **a casa**: in classe senza internet non si generano)
4. Studenti: Wi-Fi su `IRC` ("mantieni connessione" se chiede, dati mobili off per la prova), aprire `http://192.168.0.2:8341/`
5. A fine percorso lo studente preme **"🏆 Invia alla classifica di classe"** (compare solo se ha svolto almeno 1 attività) e deve leggere "✓ Inviato in classifica!"

## 5. Importante: come generare per la classe
- **NON** spuntare "Genera come file HTML unico, senza cartella": cancella la cartella `*_lesson`, svuota l'hub e rompe la classifica
- Il file `..._singola.html` serve solo per distribuire (download/email/Drive), non per l'uso col pannello

## 6. Fix già applicati al codice (non toccare)
- `tools/player_template.py`: `window.LESSON_DIR` senza escaping HTML (era `&quot;` → SyntaxError); pulsante "🏁 Fine" ora scorre ai risultati; rimossi `1×` e `⏭`, aggiunto interruttore globale **🔊 Audio** nell'header
- `panel.py` + `tools/common.py`: `lan_ip_fisso` in config; pulsanti "🗑 Svuota" per cronologia generazioni e classifica (`/api/clear_history`, `/api/reset_classifica`, solo localhost)

## 7. Se qualcosa non va (diagnosi rapida)
- `ping 192.168.0.1` OK ma pagina chiusa → usare `http://`, finestra anonima, VPN/proxy off, avviso certificato → "procedi comunque"
- Telefono: "impossibile raggiungere il sito" → deve stare su `IRC` (non dati/scuola), URL esatto con `http://` e `:8341`, pannello avviato, PC su `IRC` con `.0.2` (controllare lista dispositivi del router: devono esserci PC + telefoni)
- `localhost:8341` si apre ma `.0.2` no → server vecchio sulla porta o firewall: chiudere tutti i Python, riavviare, consentire Python nel firewall (reti private e pubbliche)
- Hub vuoto ("nessuna lezione") → manca la cartella `*_lesson`: rigenerare senza opzione file-singolo
- IP resta `.0.3`/altro nonostante reservation → MAC cambiato (ricontrollare indirizzi casuali) o reservation/DHCP scritti male (`.2` fuori dal pool, Apply premuto)
- Login router rifiutato → reset fisico 10 sec → `http://192.168.1.1`, `admin`/`password`, riconfigurare
