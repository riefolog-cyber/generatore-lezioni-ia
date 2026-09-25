# -*- coding: utf-8 -*-
"""QR code minimali (solo stdlib): encoder QR byte-mode + renderer SVG/PNG.

Supporta solo quello che serve al pannello (URL brevi LAN):
  - qr_matrix(text) -> (size, is_dark(x, y))
  - qr_svg(text) -> str SVG
  - qr_png_bytes(text, scale=6, border=4) -> bytes PNG (senza Pillow)

Byte-mode, EC level L, auto-versione 1..10 (URL LAN < 200 byte).
"""
import struct
import zlib

_CAP = [0, 17, 32, 53, 78, 106, 134, 154, 192, 230, 271]
# versione -> (gruppi (n_blocchi, data_codewords), ec_per_blocco)
# da RS_BLOCK_TABLE L (qrcode-generator): [n, totale, data] -> ec = totale-data
_EC = [None,
       ([(1, 19)], 7), ([(1, 34)], 10), ([(1, 55)], 15),
       ([(1, 80)], 20), ([(1, 108)], 26), ([(2, 68)], 18),
       ([(2, 78)], 20), ([(2, 97)], 24), ([(2, 116)], 30),
       ([(2, 68), (2, 69)], 18)]
_ALIGN_POS = [None, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34],
              [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]]

_EXP = [0] * 512
_LOG = [0] * 256


def _init_gf():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_gf()


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_gen(nsym):
    # come qrcode-generator: g(x) = prod_{i=0..nsym-1} (x + a^i),
    # con a^i = _EXP[i] e somma in GF = XOR.
    g = [1]
    for i in range(nsym):
        prev = list(g)
        g = [0] * (len(prev) + 1)
        for j, v in enumerate(prev):
            g[j] ^= _gf_mul(v, _EXP[i])
            g[j + 1] ^= v
    return g


def _rs_encode(data, nsym):
    g = _rs_gen(nsym)
    out = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = out[i]
        if coef:
            for j in range(len(g)):
                out[i + j] ^= _gf_mul(g[j], coef)
    return out[len(data):]


def _pick_version(text):
    n = len(text.encode("utf-8"))
    for v in range(1, 11):
        if n <= _CAP[v]:
            return v
    raise ValueError("Testo troppo lungo per QR (max ~271 byte)")


def _encode_data(text, version):
    raw = text.encode("utf-8")
    groups, ec_len = _EC[version]
    total_dc = sum(n * dc for n, dc in groups)
    bits = [0, 1, 0, 0]
    cbits = 16 if version >= 10 else 8
    for i in range(cbits - 1, -1, -1):
        bits.append((len(raw) >> i) & 1)
    for byte in raw:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    cap_bits = total_dc * 8
    bits += [0] * min(4, cap_bits - len(bits))
    while len(bits) % 8:
        bits.append(0)
    data = []
    for i in range(0, len(bits), 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        data.append(v)
    pad = 0xEC
    while len(data) < total_dc:
        data.append(pad)
        pad = 0x11 if pad == 0xEC else 0xEC
    blocks = []
    pos = 0
    for n, dc in groups:
        for _ in range(n):
            blocks.append(data[pos:pos + dc])
            pos += dc
    ecc = [_rs_encode(b, ec_len) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_len):
        for e in ecc:
            out.append(e[i])
    return out


def _fmt_bits_l():
    """15 bit formato per EC-L + mask 0, calcolati come qrcode-generator."""
    G15 = ((1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2)
           | (1 << 1) | (1 << 0))
    MASK = (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1)
    d = 8 << 10  # data = (L=01 << 3) | mask0 = 8
    while d.bit_length() - G15.bit_length() >= 0:
        d ^= G15 << (d.bit_length() - G15.bit_length())
    return ((8 << 10) | d) ^ MASK


def _build_matrix(codewords, version):
    n = 21 + (version - 1) * 4
    m = [[None] * n for _ in range(n)]
    func = [[False] * n for _ in range(n)]

    def set_func(x, y, v):
        m[y][x] = v
        func[y][x] = True

    def finder(cx, cy):
        # come qrcode-generator setupPositionProbePattern(row=cy, col=cx):
        # 7x7 + bordo: righe cy-1..cy+7, col cx-1..cx+7
        for r in range(-1, 8):
            for c in range(-1, 8):
                x, y = cx + c, cy + r
                if 0 <= x < n and 0 <= y < n:
                    if ((0 <= r <= 6 and (c == 0 or c == 6))
                            or (0 <= c <= 6 and (r == 0 or r == 6))
                            or (2 <= r <= 4 and 2 <= c <= 4)):
                        set_func(x, y, True)
                    else:
                        set_func(x, y, False)

    finder(0, 0)
    finder(n - 7, 0)
    finder(0, n - 7)
    for i in range(8, n - 8):
        set_func(i, 6, i % 2 == 0)
        set_func(6, i, i % 2 == 0)
    for ax in _ALIGN_POS[version]:
        for ay in _ALIGN_POS[version]:
            if m[ay][ax] is not None:
                continue
            if (ax < 9 and ay < 9) or (ax >= n - 8 and ay < 9) \
                    or (ax < 9 and ay >= n - 8):
                continue  # sovrapposto a un finder
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    set_func(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    set_func(8, 4 * version + 9, True)  # dark module = (col 8, riga n-8)
    # riserva celle formato (verranno scritte DOPO i dati)
    # verticale: righe 0-5,7,8 col 8 + righe n-7..n-1 col 8
    for i in range(6):
        if m[i][8] is None:
            set_func(8, i, False)
        if m[8][i] is None:
            set_func(i, 8, False)
    if m[7][8] is None:
        set_func(8, 7, False)
    if m[8][8] is None:
        set_func(8, 8, False)
    if m[8][7] is None:
        set_func(7, 8, False)
    # copia B: colonna 8 righe n-7..n-1 e riga 8 col n-8..n-1
    for r in range(n - 7, n):
        if m[r][8] is None:
            set_func(8, r, False)
    for c in range(n - 8, n):
        if m[8][c] is None:
            set_func(c, 8, False)
    bits = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    bi = 0
    x = n - 1
    upward = True
    while x > 0:
        if x == 6:
            x -= 1
        for k in range(n):
            y = n - 1 - k if upward else k
            for dx in (0, -1):
                xx = x + dx
                if not func[y][xx]:
                    v = bool(bits[bi]) if bi < len(bits) else False
                    bi += 1
                    if (xx + y) % 2 == 0:
                        v = not v
                    m[y][xx] = v
        upward = not upward
        x -= 2
    fmt = _fmt_bits_l()
    # verticale _modules[riga][8]: bit0-5 -> righe 0-5, bit6-7 -> righe 7-8,
    # bit8-14 -> righe n-7..n-1
    for i in range(6):
        set_func(8, i, bool((fmt >> i) & 1))
    set_func(8, 7, bool((fmt >> 6) & 1))
    set_func(8, 8, bool((fmt >> 7) & 1))
    for i in range(8, 15):
        set_func(8, n - 15 + i, bool((fmt >> i) & 1))
    # orizzontale _modules[8][col]: bit0-7 -> col n-1..n-8,
    # bit8 -> col 7, bit9-14 -> col 5..0
    for i in range(8):
        set_func(n - 1 - i, 8, bool((fmt >> i) & 1))
    set_func(7, 8, bool((fmt >> 8) & 1))
    for i in range(9, 15):
        set_func(14 - i, 8, bool((fmt >> i) & 1))
    return m


def qr_matrix(text):
    v = _pick_version(text)
    m = _build_matrix(_encode_data(text, v), v)
    return len(m), lambda x, y: bool(m[y][x])


def qr_svg(text, scale=4, border=2, dark="#111", light="#fff"):
    size, at = qr_matrix(text)
    n = size + border * 2
    parts = []
    for y in range(size):
        x = 0
        while x < size:
            if at(x, y):
                x0 = x
                while x < size and at(x, y):
                    x += 1
                parts.append('<rect x="%d" y="%d" width="%d" height="1"/>'
                            % (x0 + border, y + border, x - x0))
            else:
                x += 1
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d"'
            ' viewBox="0 0 %d %d" shape-rendering="crispEdges">'
            '<rect width="%d" height="%d" fill="%s"/><g fill="%s">%s</g></svg>'
            % (n * scale, n * scale, n, n, n, n, light, dark, "".join(parts)))


def qr_png_bytes(text, scale=5, border=4):
    size, at = qr_matrix(text)
    n = size + border * 2
    px = n * scale
    raw = bytearray()
    for y in range(px):
        raw.append(0)
        my = y // scale - border
        for x in range(px):
            mx = x // scale - border
            dark = (0 <= mx < size and 0 <= my < size and at(mx, my))
            raw.append(0 if dark else 255)

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", px, px, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


