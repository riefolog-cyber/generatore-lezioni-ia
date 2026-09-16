# Promemoria — rete di classe e pannello lezioni

## 1. Router Netgear N600 (rete di classe)
- Rete locale: `192.168.0.x` — pagina gestione: `http://192.168.0.1` (scrivere `http://` davanti, non `https`)
- Wi-Fi classe (2,4 GHz): SSID **`Classe-Lezione`**, WPA2-PSK (AES), password impostata da te
- DHCP: **attivo** (assegna gli IP da solo)
- Porta **gialla (WAN)**:
  - **vuota** = solo lezione, classe offline (telefoni: "mantieni connessione" + dati mobili off)
  - **cavo della presa scolastica** = classe online (lezione + internet per tutti)
- Il firmware originale NON può collegarsi in Wi-Fi alla rete scolastica (niente modalità repeater): per internet servono la presa cablata in WAN oppure una seconda connessione sul PC

## 2. PC docente (doppia connessione)
- **Adattatore USB Ethernet** (Realtek USB GbE, MAC `00-E0-4C-69-49-D2`) via cavo a una porta **numerata 1-4** del Netgear → rete classe
- **Wi-Fi del PC** → rete scuola/casa (con password) → internet per il docente
- Reservation nel Netgear (**LAN Setup → Address Reservation**): MAC `00-E0-4C-69-49-D2` → IP **`192.168.0.2`** (sempre lo stesso, anche dopo riavvii)

## 3. Pannello lezioni
- `config.json`: `"porta": 8341`, `"lan_ip_fisso": "192.168.0.2"` → il box "Condividi in classe" mostra sempre `http://192.168.0.2:8341/`
- Dopo ogni modifica a `config.json` o al codice: **chiudere e rilanciare `AVVIA.bat`**
- Alla prima richiesta del firewall Windows: consentire Python
- Il pannello ascolta su tutte le interfacce: raggiungibile sia dalla classe (`.0.2`) sia da casa (`.68.73`)

## 4. Routine in classe
1. Router acceso (WAN vuota o con cavo scuola, vedi punto 1)
2. PC: cavo al Netgear + (se serve) Wi-Fi alla scuola; controllare con `ipconfig` che l'Ethernet sia `192.168.0.2`
3. Avviare `AVVIA.bat`, verificare che esista la cartella `*_lesson` (generare le lezioni **a casa**: in classe senza internet non si generano)
4. Dettare agli studenti: collegarsi a `Classe-Lezione`, aprire `http://192.168.0.2:8341/`
5. A fine percorso lo studente preme **"🏆 Invia alla classifica di classe"** (compare solo se ha svolto almeno 1 attività) e deve leggere "✓ Inviato in classifica!"

## 5. Importante: come generare per la classe
- **NON** spuntare "Genera come file HTML unico, senza cartella": cancella la cartella `*_lesson`, svuota l'hub e rompe la classifica
- Il file `..._singola.html` serve solo per distribuire (download/email/Drive), non per l'uso col pannello

## 6. Fix già applicati al codice (non toccare)
- `tools/player_template.py`: `window.LESSON_DIR` senza escaping HTML (era `&quot;` → SyntaxError)
- Pulsante "🏁 Fine": ora scorre al riquadro risultati (prima non faceva nulla)
- Barra audio: rimossi `1×` e `⏭`; aggiunto interruttore globale **🔊 Audio** nell'header

## 7. Se qualcosa non va (diagnosi rapida)
- `ping 192.168.0.1` OK ma pagina chiusa → usare `http://`, finestra anonima, VPN/proxy off, avviso certificato → "procedi comunque"
- Telefono: "impossibile raggiungere il sito" → verificare che stia su `Classe-Lezione` (non dati mobili), URL esatto con `http://` e `:8341`, pannello avviato, PC su `Classe-Lezione`
- Hub vuoto ("nessuna lezione") → manca la cartella `*_lesson`: rigenerare senza opzione file-singolo
- Login router rifiutato → reset fisico 10 sec → `http://192.168.1.1`, `admin`/`password`, riconfigurare
