# -*- coding: utf-8 -*-
"""Guardie HTTP condivise da panel.py e start_lesson.py.

Tre controlli indipendenti, in un posto solo (prima la stessa logica di path
era duplicata con criteri diversi in due file, con drift garantito):

- `safe_request_path()`  anti path-traversal per i file statici
- `host_allowed()`       anti DNS-rebinding: l'header Host deve essere un
                         indirizzo di QUESTA macchina
- `same_origin_post()`   anti CSRF sulle POST "semplici" (multipart, testo
                         piano, senza body), che non fanno preflight

Perché servono:

* DNS-rebinding: senza controllo dell'Host, un sito visitato dal docente
  può risolvere il proprio dominio a 127.0.0.1 e parlare con il pannello
  come se fosse same-origin. Il controllo `client_address` (loopback) passa,
  perché la connessione arriva davvero da 127.0.0.1.
* CSRF: un `<form>` o una fetch `no-cors` cross-origin non viene bloccata da
  CORS (che è impostato correttamente) ma vengono comunque eseguite dal
  server. La difesa è verificare `Origin`/`Sec-Fetch-Site`.
"""
import urllib.parse
from pathlib import Path

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def split_host_port(host_header):
    """'192.168.0.2:8341' -> ('192.168.0.2', '8341'). Gestisce IPv6 tra [ ]."""
    h = (host_header or "").strip().lower()
    if not h:
        return "", ""
    if h.startswith("["):
        host, sep, rest = h[1:].partition("]")
        if not sep:
            return h, ""
        return host, rest.lstrip(":")
    host, _, port = h.rpartition(":")
    # senza porta rpartition restituisce ("", "", h): correggi
    if not host:
        return h, ""
    if host.count(":") > 1:  # IPv6 senza parentesi
        return h, ""
    return host, port


def host_allowed(host_header, allowed, port=None):
    """True se l'header Host è uno degli indirizzi ammessi (e, se `port` è
    dato, la porta corrisponde)."""
    host, hport = split_host_port(host_header)
    if not host:
        return False
    if port is not None and hport and hport != str(port):
        return False
    if host in allowed:
        return True
    # normalizza la forma con parentesi quadre degli IPv6
    return ("[%s]" % host) in allowed


def same_origin_post(headers, allowed):
    """True se una POST può provenire dalla nostra stessa interfaccia.

    I browser moderni mandano sempre `Origin` (POST) o `Sec-Fetch-Site`. Se
    sono presenti devono essere coerenti con l'origine del pannello; se sono
    assenti si tratta di un client non-browser (curl, script, app.py), che non
    è un vettore CSRF e viene lasciato passare.
    """
    try:
        site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site not in ("same-origin", "none", "same-site"):
            return False
        origin = (headers.get("Origin") or "").strip()
        if origin and origin != "null":
            ohost, _ = split_host_port(urllib.parse.urlparse(origin).netloc)
            if ohost and not (ohost in allowed or ("[%s]" % ohost) in allowed):
                return False
        elif origin == "null":
            return False
        referer = (headers.get("Referer") or "").strip()
        if not origin and referer and site:
            rhost, _ = split_host_port(urllib.parse.urlparse(referer).netloc)
            if rhost and not (rhost in allowed or ("[%s]" % rhost) in allowed):
                return False
    except Exception:
        return False
    return True


def safe_request_path(url_path, base):
    """Risolve il path di una richiesta statica dentro `base`, rifiutando ogni
    uscita. Ritorna il Path risolto, oppure None se il path non è sicuro.

    Un path vuoto ("/" o "") è considerato sicuro e ritorna `base`: serve
    l'indice della radice, che è un percorso valido.

    Applicato sia da panel.py sia da start_lesson.py: la guardia finale non
    deve vivere in un file che ha un'altra responsibility.
    """
    try:
        dec = urllib.parse.unquote(url_path)
    except Exception:
        return None
    if "\x00" in dec or "\\" in dec:
        return None
    parts = [p for p in dec.strip("/").split("/") if p]
    if not parts:
        return Path(base).resolve()
    if ".." in parts:
        return None
    try:
        target = (Path(base) / "/".join(parts)).resolve()
        target.relative_to(Path(base).resolve())
    except Exception:
        return None
    return target
