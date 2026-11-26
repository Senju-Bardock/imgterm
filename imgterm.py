"""
imgterm.py
Display a PNG, JPEG or (animated) GIF image in the terminal (Windows, Linux,
macOS) using ONLY the Python standard library: nothing to pip install.

It picks the best renderer your terminal supports:

    sixel   real pixels (Windows Terminal, WezTerm, foot, xterm -ti vt340, ...)
    kitty   real pixels (kitty, Ghostty)
    iterm2  real pixels (iTerm2, WezTerm)
    blocks  universal fallback: quadrant characters, 2x2 sub-pixels per cell,
            best-fit glyph and gamma-correct resizing

Usage as a module:
    from imgterm import show_image
    show_image("photo.png")                         # relative or absolute path
    show_image("photo.jpg")                         # JPEG works the same way
    show_image("dance.gif")                         # GIFs are animated (Ctrl+C stops it)
    show_image("dance.gif", loops=2)                # play it twice (0 = forever)
    show_image("Senju_Bardock.png", folder="visual")
    show_image("https://site.com/image.png")        # URLs work too
    show_image("photo.png", max_width=60)           # limit width (columns)
    show_image("photo.png", mode="blocks")          # force a renderer

Usage from the terminal:
    python imgterm.py photo.png
    python imgterm.py photo.png --folder visual --width 60 --mode sixel
    python imgterm.py dance.gif --loops 3

Environment variables (optional):
    IMGTERM_MODE   sixel | kitty | iterm2 | blocks   (skips auto-detection)
    IMGTERM_CELL   WxH size of one character cell in pixels, e.g. 10x20
"""

import argparse
import base64
import heapq
import math
import os
import re
import shutil
import struct
import sys
import threading
import time
import urllib.request
import zlib
from array import array
from functools import lru_cache
from itertools import accumulate, chain, count, groupby
from pathlib import Path

__all__ = ["show_image", "preload", "read_image", "read_gif", "read_png", "render", "detect_mode"]

MODES = ("auto", "sixel", "kitty", "iterm2", "blocks")
DEFAULT_CELL = (9, 18)  # pixels (width, height) of a character cell if unknown


# ---------------------------------------------------------------------------
# 1. Terminal preparation and capability detection
# ---------------------------------------------------------------------------
def prepare_terminal():
    """Enable ANSI escape sequences on Windows and UTF-8 output everywhere."""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            os.system("")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


_DA1 = re.compile(r"\x1b\[\?([\d;]*)c")
_CELL = re.compile(r"\x1b\[6;(\d+);(\d+)t")


def _query_terminal(timeout=0.5):
    """
    Ask the terminal two things in one go:
        CSI 16 t -> size of a character cell in pixels
        CSI c    -> device attributes (the list contains 4 when sixel is supported)
    Returns (supports_sixel, (cell_w, cell_h) or None).
    """
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False, None
    except Exception:
        return False, None

    query = "\x1b[16t\x1b[c"
    buf = ""
    try:
        if os.name == "nt":
            import ctypes
            import msvcrt

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-10)
            old = ctypes.c_ulong()
            have_mode = kernel32.GetConsoleMode(handle, ctypes.byref(old))
            if have_mode:
                # 0x0200 = ENABLE_VIRTUAL_TERMINAL_INPUT (receive the reply raw)
                kernel32.SetConsoleMode(handle, old.value | 0x0200)
            try:
                sys.stdout.write(query)
                sys.stdout.flush()
                end = time.time() + timeout
                while time.time() < end and not _DA1.search(buf):
                    if msvcrt.kbhit():
                        buf += msvcrt.getwch()
                    else:
                        time.sleep(0.005)
                time.sleep(0.02)
                while msvcrt.kbhit():  # drain leftovers so input() stays clean
                    msvcrt.getwch()
            finally:
                if have_mode:
                    kernel32.SetConsoleMode(handle, old.value)
        else:
            import select
            import termios
            import tty

            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                sys.stdout.write(query)
                sys.stdout.flush()
                end = time.time() + timeout
                while time.time() < end and not _DA1.search(buf):
                    ready, _, _ = select.select([fd], [], [], max(0.0, end - time.time()))
                    if ready:
                        buf += os.read(fd, 256).decode("latin-1")
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
                termios.tcflush(fd, termios.TCIFLUSH)
    except Exception:
        return False, None

    sixel = False
    cell = None
    match = _DA1.search(buf)
    if match:
        sixel = "4" in match.group(1).split(";")
    match = _CELL.search(buf)
    if match and int(match.group(1)) > 0 and int(match.group(2)) > 0:
        cell = (int(match.group(2)), int(match.group(1)))
    return sixel, cell


_CAPS = None


def _terminal_caps():
    """Detect (once) the best renderer and the cell size. Cached."""
    global _CAPS
    if _CAPS is not None:
        return _CAPS
    prepare_terminal()
    env = os.environ

    sixel, cell = _query_terminal()

    mode = "blocks"
    forced = env.get("IMGTERM_MODE", "").strip().lower()
    if forced in MODES[1:]:
        mode = forced
    elif env.get("KITTY_WINDOW_ID") or env.get("TERM") == "xterm-kitty" \
            or env.get("TERM_PROGRAM") == "ghostty":
        mode = "kitty"
    elif env.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm"):
        mode = "iterm2"
    elif sixel:
        mode = "sixel"

    custom = re.fullmatch(r"(\d+)[xX](\d+)", env.get("IMGTERM_CELL", "").strip())
    if custom:
        cell = (int(custom.group(1)), int(custom.group(2)))
    _CAPS = {"mode": mode, "cell": cell or DEFAULT_CELL}
    return _CAPS


def detect_mode():
    """Return the renderer that auto-detection would use."""
    return _terminal_caps()["mode"]


def _truecolor():
    if os.environ.get("IMGTERM_COLORS") == "256":
        return False
    if os.name == "nt":
        return True
    return os.environ.get("TERM", "") not in ("linux", "dumb", "vt100", "vt220")


# ---------------------------------------------------------------------------
# 2. PNG decoder (zlib + struct only): all color types, 1-16 bit, tRNS, Adam7
# ---------------------------------------------------------------------------
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def _samples(line, width, channels, depth):
    total = width * channels
    if depth == 8:
        return line[:total]
    if depth == 16:
        return line[0:total * 2:2]  # keep the high byte
    mask = (1 << depth) - 1
    per_byte = 8 // depth
    output = []
    for byte in line:
        for k in range(per_byte):
            output.append((byte >> (8 - depth * (k + 1))) & mask)
    return output[:total]


def _unfilter(raw, pos, width, height, bits_pixel):
    """Undo the PNG scanline filters of one (sub)image. Returns (rows, new_pos)."""
    bpp = max(1, bits_pixel // 8)
    stride = (width * bits_pixel + 7) // 8
    channels = range(min(bpp, stride))
    rows = []
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride

        if filter_type == 1:  # Sub: running sum along each channel
            for c in channels:
                line[c::bpp] = bytes([v & 255 for v in accumulate(line[c::bpp])])
        elif filter_type == 2:  # Up
            line = bytearray([(x + y) & 255 for x, y in zip(line, previous)])
        elif filter_type == 3:  # Average
            for c in channels:
                left = 0
                out = []
                for x, b in zip(line[c::bpp], previous[c::bpp]):
                    left = (x + ((left + b) >> 1)) & 255
                    out.append(left)
                line[c::bpp] = bytes(out)
        elif filter_type == 4:  # Paeth
            for c in channels:
                a = cc = 0
                out = []
                for x, b in zip(line[c::bpp], previous[c::bpp]):
                    p = a + b - cc
                    pa = p - a
                    pb = p - b
                    pc = p - cc
                    if pa < 0:
                        pa = -pa
                    if pb < 0:
                        pb = -pb
                    if pc < 0:
                        pc = -pc
                    if pa <= pb and pa <= pc:
                        pr = a
                    elif pb <= pc:
                        pr = b
                    else:
                        pr = cc
                    a = (x + pr) & 255
                    out.append(a)
                    cc = b
                line[c::bpp] = bytes(out)

        rows.append(line)
        previous = line
    return rows, pos


def _row_to_rgb(line, width, channels, depth, color, palette, trns, bg):
    """One decoded scanline -> list of (r, g, b), transparency blended over bg."""
    s = _samples(line, width, channels, depth)
    scale = 255 // ((1 << depth) - 1) if depth < 8 else 1
    br, bgg, bb = bg

    # Fast paths (vectorised with slices/zip); exactly the same maths as the
    # generic loop below. For palette images 'palette' is already blended.
    if trns is None:
        if color == 6:  # RGBA
            r, g, b, a = s[0::4], s[1::4], s[2::4], s[3::4]
            if min(a) == 255:
                return list(zip(r, g, b))
            return [(r_, g_, b_) if a_ == 255 else
                    ((r_ * a_ + br * (255 - a_)) // 255,
                     (g_ * a_ + bgg * (255 - a_)) // 255,
                     (b_ * a_ + bb * (255 - a_)) // 255)
                    for r_, g_, b_, a_ in zip(r, g, b, a)]
        if color == 2:  # RGB
            return list(zip(s[0::3], s[1::3], s[2::3]))
        if color == 3:  # palette
            return [palette[i] for i in s]
        if color == 0:  # gray
            if scale == 1:
                return [(v, v, v) for v in s]
            return [(v * scale,) * 3 for v in s]
        if color == 4:  # gray + alpha
            g, a = s[0::2], s[1::2]
            return [(v, v, v) if a_ == 255 else
                    (((v * a_ + br * (255 - a_)) // 255),
                     ((v * a_ + bgg * (255 - a_)) // 255),
                     ((v * a_ + bb * (255 - a_)) // 255))
                    for v, a_ in zip(g, a)]

    px = []
    for i in range(width):
        a = 255
        if color == 0:  # gray
            v = s[i]
            if trns is not None and v == trns:
                a = 0
            r = g = b = v * scale
        elif color == 2:  # RGB
            j = i * 3
            r, g, b = s[j], s[j + 1], s[j + 2]
            if trns is not None and (r, g, b) == trns:
                a = 0
        elif color == 3:  # palette
            idx = s[i]
            r, g, b = palette[idx]
            if trns is not None and idx < len(trns):
                a = trns[idx]
        elif color == 4:  # gray + alpha
            j = i * 2
            r = g = b = s[j]
            a = s[j + 1]
        else:  # RGBA
            j = i * 4
            r, g, b, a = s[j], s[j + 1], s[j + 2], s[j + 3]
        if a != 255:
            r = (r * a + br * (255 - a)) // 255
            g = (g * a + bgg * (255 - a)) // 255
            b = (b * a + bb * (255 - a)) // 255
        px.append((r, g, b))
    return px


def _decode_png(data, background=(0, 0, 0)):
    """Returns (width, height, rows) with rows = list of lists of (r, g, b)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("The file is not a valid PNG.")

    pos = 8
    idat = bytearray()
    palette = None
    trns_raw = None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlaced = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            trns_raw = bytes(body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    bits_pixel = channels * depth

    trns = None
    if trns_raw is not None:
        if color == 3:
            trns = list(trns_raw)
        elif color == 0:
            v = struct.unpack(">H", trns_raw[:2])[0]
            trns = v >> 8 if depth == 16 else v
        elif color == 2:
            vals = struct.unpack(">3H", trns_raw[:6])
            trns = tuple(v >> 8 for v in vals) if depth == 16 else vals

    if color == 3 and palette is not None:
        # blend transparency into the palette once instead of once per pixel
        br, bgg, bb = background
        blended = []
        for n, (r, g, b) in enumerate(palette):
            a = trns[n] if trns is not None and n < len(trns) else 255
            if a != 255:
                r = (r * a + br * (255 - a)) // 255
                g = (g * a + bgg * (255 - a)) // 255
                b = (b * a + bb * (255 - a)) // 255
            blended.append((r, g, b))
        palette, trns = blended, None

    raw = zlib.decompress(bytes(idat))

    if not interlaced:
        lines, _ = _unfilter(raw, 0, width, height, bits_pixel)
        rows = [_row_to_rgb(l, width, channels, depth, color, palette, trns, background)
                for l in lines]
        return width, height, rows

    # Adam7: seven smaller images interleaved
    rows = [[(0, 0, 0)] * width for _ in range(height)]
    pos = 0
    for xs, ys, dx, dy in _ADAM7:
        pw = (width - xs + dx - 1) // dx
        ph = (height - ys + dy - 1) // dy
        if pw <= 0 or ph <= 0:
            continue
        lines, pos = _unfilter(raw, pos, pw, ph, bits_pixel)
        for j, line in enumerate(lines):
            px = _row_to_rgb(line, pw, channels, depth, color, palette, trns, background)
            row = rows[ys + j * dy]
            for i, p in enumerate(px):
                row[xs + i * dx] = p
    return width, height, rows


# ---------------------------------------------------------------------------
# 2b. JPEG decoder (stdlib only): baseline + progressive, Huffman, 8-bit,
#     grayscale or YCbCr/RGB, any chroma subsampling, restart intervals.
#     Not supported: arithmetic coding, lossless, 12-bit, CMYK/YCCK.
# ---------------------------------------------------------------------------
_ZIGZAG = (0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48,
           41, 34, 27, 20, 13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15,
           23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63)

_JPEG_SCAN_END = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")  # first real marker after scan data
_JPEG_RESTART = re.compile(rb"\xff[\xd0-\xd7]")
_JPEG_UNSUPPORTED = (0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)

# 1-D 8-point inverse DCT constants: K[n] = cos(n*pi/16) / 2
_K1, _K2, _K3, _K4, _K5, _K6, _K7 = (0.5 * math.cos(i * math.pi / 16) for i in range(1, 8))

# Clamp table indexed with y + offset where the offset may be negative (Python
# wraps negative indexes): 0..255 -> same, 256..511 -> 255, -256..-1 -> 0.
_CLAMP = bytes(range(256)) + bytes([255]) * 256 + bytes(256)
_CR_R = [round(1.402 * (i - 128)) for i in range(256)]
_CB_B = [round(1.772 * (i - 128)) for i in range(256)]
_CB_G = [round(-0.344136 * (i - 128)) for i in range(256)]
_CR_G = [round(-0.714136 * (i - 128)) for i in range(256)]


def _idct_matrix(n):
    """n-point inverse DCT matrix used to decode at 1/2, 1/4 or 1/8 size."""
    return [[math.sqrt(n / 8) * (math.sqrt(1 / n) if u == 0 else math.sqrt(2 / n))
             * math.cos((2 * x + 1) * u * math.pi / (2 * n)) for u in range(n)]
            for x in range(n)]


_IDCT_SMALL = {2: _idct_matrix(2), 4: _idct_matrix(4)}


class _Component:
    __slots__ = ("id", "h", "v", "tq", "bw", "bh", "bwm", "bhm", "coef", "pred", "dc", "ac")


def _huffman_table(counts, symbols):
    """16-bit lookup table: table[next 16 bits] = (code length << 8) | symbol."""
    table = [0] * 65536
    code = k = 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            lo = code << (16 - length)
            hi = (code + 1) << (16 - length)
            if hi > 65536:
                raise ValueError("Corrupt Huffman table.")
            table[lo:hi] = [(length << 8) | symbols[k]] * (hi - lo)
            code += 1
            k += 1
        code <<= 1
    return table


def _jpeg_scan(chunks, mcus_x, mcus_y, scomps, ri, progressive, ss, se, ah, al):
    """Entropy-decode one scan into the coefficient arrays of its components."""
    zz = _ZIGZAG
    data = b""
    pos = buf = cnt = eobrun = 0
    # Bit reader: 'buf' holds 'cnt' valid bits in its low end. Every handler tops
    # it up to >= 32 bits before a Huffman code (<= 16 bits) plus its extra bits.

    def baseline(comp, off):
        nonlocal pos, buf, cnt
        coef, dc, ac = comp.coef, comp.dc, comp.ac
        p, b, c = pos, buf, cnt
        if c < 32:
            b = ((b & ((1 << c) - 1)) << 32) | int.from_bytes(data[p:p + 4], "big")
            c += 32
            p += 4
        e = dc[(b >> (c - 16)) & 0xFFFF]
        c -= e >> 8
        s = e & 255
        if s:
            v = (b >> (c - s)) & ((1 << s) - 1)
            c -= s
            comp.pred += v if v >= (1 << (s - 1)) else v - (1 << s) + 1
        coef[off] = comp.pred
        k = 1
        while k < 64:
            if c < 32:
                b = ((b & ((1 << c) - 1)) << 32) | int.from_bytes(data[p:p + 4], "big")
                c += 32
                p += 4
            e = ac[(b >> (c - 16)) & 0xFFFF]
            c -= e >> 8
            rs = e & 255
            s = rs & 15
            if s == 0:
                if rs != 0xF0:  # end of block
                    break
                k += 16  # run of 16 zeros
                continue
            k += rs >> 4
            if k > 63:
                break
            v = (b >> (c - s)) & ((1 << s) - 1)
            c -= s
            coef[off + zz[k]] = v if v >= (1 << (s - 1)) else v - (1 << s) + 1
            k += 1
        pos, buf, cnt = p, b, c

    def dc_first(comp, off):
        nonlocal pos, buf, cnt
        dc = comp.dc
        p, b, c = pos, buf, cnt
        if c < 32:
            b = ((b & ((1 << c) - 1)) << 32) | int.from_bytes(data[p:p + 4], "big")
            c += 32
            p += 4
        e = dc[(b >> (c - 16)) & 0xFFFF]
        c -= e >> 8
        s = e & 255
        if s:
            v = (b >> (c - s)) & ((1 << s) - 1)
            c -= s
            comp.pred += v if v >= (1 << (s - 1)) else v - (1 << s) + 1
        comp.coef[off] = comp.pred * (1 << al)
        pos, buf, cnt = p, b, c

    def dc_refine(comp, off):
        nonlocal pos, buf, cnt
        p, b, c = pos, buf, cnt
        if c < 32:
            b = ((b & ((1 << c) - 1)) << 32) | int.from_bytes(data[p:p + 4], "big")
            c += 32
            p += 4
        c -= 1
        if (b >> c) & 1:
            comp.coef[off] |= 1 << al
        pos, buf, cnt = p, b, c

    def ac_first(comp, off):
        nonlocal pos, buf, cnt, eobrun
        if eobrun > 0:  # this block is inside a run of empty blocks
            eobrun -= 1
            return
        coef, ac = comp.coef, comp.ac
        p, b, c = pos, buf, cnt
        k = ss
        while k <= se:
            if c < 32:
                b = ((b & ((1 << c) - 1)) << 32) | int.from_bytes(data[p:p + 4], "big")
                c += 32
                p += 4
            e = ac[(b >> (c - 16)) & 0xFFFF]
            c -= e >> 8
            rs = e & 255
            s = rs & 15
            r = rs >> 4
            if s == 0:
                if r < 15:  # EOBn: this block and (2^r + extra - 1) more are empty
                    n = 1 << r
                    if r:
                        n += (b >> (c - r)) & ((1 << r) - 1)
                        c -= r
                    eobrun = n - 1
                    break
                k += 16
                continue
            k += r
            if k > se:
                break
            v = (b >> (c - s)) & ((1 << s) - 1)
            c -= s
            if v < (1 << (s - 1)):
                v -= (1 << s) - 1
            coef[off + zz[k]] = v * (1 << al)
            k += 1
        pos, buf, cnt = p, b, c

    def ac_refine(comp, off):
        nonlocal pos, buf, cnt, eobrun
        coef, ac = comp.coef, comp.ac
        p1 = 1 << al
        m1 = -p1
        p, b, c = pos, buf, cnt
        k = ss
        if eobrun == 0:
            while k <= se:
                if c < 32:
                    b = ((b & ((1 << c) - 1)) << 32) | int.from_bytes(data[p:p + 4], "big")
                    c += 32
                    p += 4
                e = ac[(b >> (c - 16)) & 0xFFFF]
                c -= e >> 8
                rs = e & 255
                r = rs >> 4
                value = 0
                if rs & 15:  # a newly non-zero coefficient: sign bit follows
                    c -= 1
                    value = p1 if (b >> c) & 1 else m1
                elif r != 15:  # EOBn
                    eobrun = 1 << r
                    if r:
                        eobrun += (b >> (c - r)) & ((1 << r) - 1)
                        c -= r
                    break
                # skip 'r' zero coefficients, refining the non-zero ones on the way
                while k <= se:
                    if c < 32:
                        b = ((b & ((1 << c) - 1)) << 32) | int.from_bytes(data[p:p + 4], "big")
                        c += 32
                        p += 4
                    z = off + zz[k]
                    v = coef[z]
                    if v:
                        c -= 1
                        if (b >> c) & 1 and not v & p1:
                            coef[z] = v + p1 if v >= 0 else v + m1
                    else:
                        r -= 1
                        if r < 0:
                            break
                    k += 1
                if value and k <= se:
                    coef[off + zz[k]] = value
                k += 1
        if eobrun > 0:  # rest of the band: only refine the already non-zero coefficients
            while k <= se:
                if c < 32:
                    b = ((b & ((1 << c) - 1)) << 32) | int.from_bytes(data[p:p + 4], "big")
                    c += 32
                    p += 4
                z = off + zz[k]
                v = coef[z]
                if v:
                    c -= 1
                    if (b >> c) & 1 and not v & p1:
                        coef[z] = v + p1 if v >= 0 else v + m1
                k += 1
            eobrun -= 1
        pos, buf, cnt = p, b, c

    if not progressive:
        handler = baseline
    elif ss == 0:
        handler = dc_first if ah == 0 else dc_refine
    else:
        handler = ac_first if ah == 0 else ac_refine

    single = len(scomps) == 1
    first = scomps[0]
    total = first.bw * first.bh if single else mcus_x * mcus_y
    interval = ri or total
    mcu = 0
    for chunk in chunks:  # one chunk per restart interval
        if mcu >= total:
            break
        data = chunk + bytes(8)
        pos = buf = cnt = eobrun = 0
        for sc in scomps:
            sc.pred = 0
        end = min(mcu + interval, total)
        if single:
            while mcu < end:
                row, col = divmod(mcu, first.bw)
                handler(first, (row * first.bwm + col) * 64)
                mcu += 1
        else:
            while mcu < end:
                mrow, mcol = divmod(mcu, mcus_x)
                for sc in scomps:
                    for j in range(sc.v):
                        base = (mrow * sc.v + j) * sc.bwm + mcol * sc.h
                        for i in range(sc.h):
                            handler(sc, (base + i) * 64)
                mcu += 1


def _idct8(x0, x1, x2, x3, x4, x5, x6, x7,
           K1=_K1, K2=_K2, K3=_K3, K4=_K4, K5=_K5, K6=_K6, K7=_K7):
    """1-D 8-point inverse DCT (even/odd decomposition)."""
    a = x0 * K4
    e0 = a + x2 * K2 + x4 * K4 + x6 * K6
    e1 = a + x2 * K6 - x4 * K4 - x6 * K2
    e2 = a - x2 * K6 - x4 * K4 + x6 * K2
    e3 = a - x2 * K2 + x4 * K4 - x6 * K6
    o0 = x1 * K1 + x3 * K3 + x5 * K5 + x7 * K7
    o1 = x1 * K3 - x3 * K7 - x5 * K1 - x7 * K5
    o2 = x1 * K5 - x3 * K1 + x5 * K7 + x7 * K3
    o3 = x1 * K7 - x3 * K5 + x5 * K3 - x7 * K1
    return (e0 + o0, e1 + o1, e2 + o2, e3 + o3, e3 - o3, e2 - o2, e1 - o1, e0 - o0)


def _byte(v):
    v = int(v + 128.5)  # level shift + round
    return 0 if v < 0 else 255 if v > 255 else v


def _jpeg_block(blk, qt, n):
    """Dequantize + inverse DCT of one block, keeping an n x n output (n = 8, 4, 2, 1)."""
    if n == 1 or not any(blk[1:]):  # 1/8 size needs only the DC; or the block is flat
        return [bytes([_byte(blk[0] * qt[0] / 8)]) * n] * n
    if n == 8:
        q = [c * f for c, f in zip(blk, qt)]
        tmp = []
        for o in range(0, 64, 8):
            r0, r1, r2, r3, r4, r5, r6, r7 = q[o:o + 8]
            if r1 or r2 or r3 or r4 or r5 or r6 or r7:
                tmp.append(_idct8(r0, r1, r2, r3, r4, r5, r6, r7))
            else:
                tmp.append((r0 * _K4,) * 8)
        cols = [_idct8(*col) for col in zip(*tmp)]
        return [bytes([0 if t < 0 else 255 if t > 255 else t
                       for t in [int(v + 128.5) for v in row]])
                for row in zip(*cols)]
    m = _IDCT_SMALL[n]
    f = [[blk[8 * v + u] * qt[8 * v + u] for u in range(n)] for v in range(n)]
    tmp = [[sum(m[x][u] * f[v][u] for u in range(n)) for x in range(n)] for v in range(n)]
    return [bytes([_byte(sum(m[y][v] * tmp[v][x] for v in range(n))) for x in range(n)])
            for y in range(n)]


def _jpeg_plane(comp, qt, n):
    """Decode every block of a component -> list of bytearray rows (bwm*n wide)."""
    coef = comp.coef
    rows = []
    for br in range(comp.bhm):
        lines = [bytearray() for _ in range(n)]
        base = br * comp.bwm * 64
        for off in range(base, base + comp.bwm * 64, 64):
            out = _jpeg_block(coef[off:off + 64].tolist(), qt, n)
            for y in range(n):
                lines[y] += out[y]
        rows += lines
    return rows


def _jpeg_upsample(plane, comp, hmax, vmax, out_w, out_h):
    """Stretch a (sub-sampled) component plane to the full image size."""
    h, v = comp.h, comp.v
    if h == hmax and v == vmax:
        return [r[:out_w] for r in plane[:out_h]]
    cache = {}
    out = []
    for y in range(out_h):
        sy = y * v // vmax
        row = cache.get(sy)
        if row is None:
            src = plane[sy]
            if hmax % h == 0:
                f = hmax // h
                wide = bytearray(len(src) * f)
                for i in range(f):
                    wide[i::f] = src
                row = wide[:out_w]
            else:
                row = bytes([src[x * h // hmax] for x in range(out_w)])
            cache[sy] = row
        out.append(row)
    return out


def _decode_jpeg(data, box=None):
    """
    Returns (width, height, rows) with rows = list of lists of (r, g, b).

    box: optional (pixels wide, pixels high) the picture will be drawn in. A big
    photo is then decoded at 1/2, 1/4 or 1/8 size (using only the low frequencies
    of every block), which is much faster and never goes below the needed size.
    """
    if data[:2] != b"\xff\xd8":
        raise ValueError("The file is not a valid JPEG.")

    qts, dc_tabs, ac_tabs = {}, {}, {}
    comps = None
    progressive = False
    ri = 0
    adobe = None
    width = height = mcus_x = mcus_y = hmax = vmax = 0
    pos = 2
    size = len(data)
    while pos + 2 <= size:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        pos += 2
        if marker == 0x00 or marker == 0x01 or 0xD0 <= marker <= 0xD8:  # no payload
            continue
        if marker == 0xD9:  # end of image
            break
        length = (data[pos] << 8) | data[pos + 1]
        body = data[pos + 2:pos + length]
        pos += length

        if marker == 0xDB:  # quantization tables
            i = 0
            while i < len(body):
                precision, tq = body[i] >> 4, body[i] & 15
                i += 1
                table = [0] * 64
                for k in range(64):
                    if precision:
                        table[_ZIGZAG[k]] = (body[i] << 8) | body[i + 1]
                        i += 2
                    else:
                        table[_ZIGZAG[k]] = body[i]
                        i += 1
                qts[tq] = table
        elif marker == 0xC4:  # Huffman tables
            i = 0
            while i < len(body):
                tc, th = body[i] >> 4, body[i] & 15
                counts = body[i + 1:i + 17]
                total = sum(counts)
                table = _huffman_table(counts, body[i + 17:i + 17 + total])
                (ac_tabs if tc else dc_tabs)[th] = table
                i += 17 + total
        elif marker in (0xC0, 0xC1, 0xC2):  # start of frame
            if body[0] != 8:
                raise ValueError("Only 8-bit JPEG is supported (this one is %d-bit)." % body[0])
            height = (body[1] << 8) | body[2]
            width = (body[3] << 8) | body[4]
            count = body[5]
            if width == 0 or height == 0:
                raise ValueError("Invalid JPEG size.")
            if count not in (1, 3):
                raise ValueError("Only grayscale and color (3 channel) JPEG is supported "
                                 "(this one has %d channels, e.g. CMYK)." % count)
            progressive = marker == 0xC2
            comps = []
            for i in range(count):
                comp = _Component()
                comp.id = body[6 + 3 * i]
                comp.h, comp.v = body[7 + 3 * i] >> 4, body[7 + 3 * i] & 15
                comp.tq = body[8 + 3 * i]
                comp.pred = 0
                comp.dc = comp.ac = None
                if not (1 <= comp.h <= 4 and 1 <= comp.v <= 4):
                    raise ValueError("Invalid JPEG sampling factors.")
                comps.append(comp)
            hmax = max(c.h for c in comps)
            vmax = max(c.v for c in comps)
            mcus_x = -(-width // (8 * hmax))
            mcus_y = -(-height // (8 * vmax))
            for comp in comps:
                comp.bw = -(-(-(-width * comp.h // hmax)) // 8)
                comp.bh = -(-(-(-height * comp.v // vmax)) // 8)
                comp.bwm = mcus_x * comp.h
                comp.bhm = mcus_y * comp.v
                comp.coef = array("h", [0]) * (comp.bwm * comp.bhm * 64)
        elif marker in _JPEG_UNSUPPORTED:
            raise ValueError("This kind of JPEG is not supported (arithmetic coding, "
                             "lossless or hierarchical).")
        elif marker == 0xDD:  # restart interval
            ri = (body[0] << 8) | body[1]
        elif marker == 0xEE and body[:5] == b"Adobe" and len(body) > 11:
            adobe = body[11]  # color transform: 0 = RGB, 1 = YCbCr
        elif marker == 0xDA:  # start of scan
            if comps is None:
                raise ValueError("Corrupt JPEG (scan before frame header).")
            ns = body[0]
            scomps = []
            for i in range(ns):
                cid, tables = body[1 + 2 * i], body[2 + 2 * i]
                comp = next(c for c in comps if c.id == cid)
                comp.dc = dc_tabs.get(tables >> 4)
                comp.ac = ac_tabs.get(tables & 15)
                scomps.append(comp)
            ss, se, successive = body[1 + 2 * ns], body[2 + 2 * ns], body[3 + 2 * ns]
            found = _JPEG_SCAN_END.search(data, pos)
            end = found.start() if found else size
            chunks = [c.replace(b"\xff\x00", b"\xff")
                      for c in _JPEG_RESTART.split(data[pos:end])]
            _jpeg_scan(chunks, mcus_x, mcus_y, scomps, ri, progressive,
                       ss, se, successive >> 4, successive & 15)
            pos = end

    if comps is None:
        raise ValueError("No image found in the JPEG file.")

    n = 8  # output size of every 8x8 block
    if box:
        scale = min(box[0] / width, box[1] / height)
        for d in (8, 4, 2):
            if d * scale <= 1:
                n = 8 // d
                break
    out_w = -(-width * n // 8)
    out_h = -(-height * n // 8)

    ups = [_jpeg_upsample(_jpeg_plane(comp, qts[comp.tq], n), comp, hmax, vmax, out_w, out_h)
           for comp in comps]
    if len(comps) == 1:
        rows = [list(zip(r, r, r)) for r in ups[0]]
    elif adobe == 0 or tuple(c.id for c in comps) == (82, 71, 66):  # stored as plain RGB
        rows = [list(zip(r, g, b)) for r, g, b in zip(*ups)]
    else:  # YCbCr -> RGB
        cl, crr, cbb, cbg, crg = _CLAMP, _CR_R, _CB_B, _CB_G, _CR_G
        rows = [[(cl[y + crr[cr]], cl[y + cbg[cb] + crg[cr]], cl[y + cbb[cb]])
                 for y, cb, cr in zip(yr, cbr, crr_)]
                for yr, cbr, crr_ in zip(*ups)]
    return out_w, out_h, rows


# ---------------------------------------------------------------------------
# 2c. GIF decoder (stdlib only): GIF87a/GIF89a, LZW, interlacing, transparency,
#     disposal methods and animation. Every frame is composed on the full-size
#     canvas, so the caller just gets complete pictures.
# ---------------------------------------------------------------------------
_GIF_MAGIC = (b"GIF87a", b"GIF89a")
_GIF_INTERLACE = ((0, 8), (4, 8), (2, 4), (1, 2))  # (first row, step) of the 4 passes


def _gif_palette(raw):
    """Color table bytes -> list of 256 (r, g, b) tuples (padded with black)."""
    table = [tuple(raw[i:i + 3]) for i in range(0, len(raw) - 2, 3)]
    return table + [(0, 0, 0)] * (256 - len(table))


def _gif_subblocks(data, pos):
    """Join the data sub-blocks that start at 'pos'. Returns (bytes, position after)."""
    chunks = []
    size = len(data)
    while pos < size:
        n = data[pos]
        pos += 1
        if n == 0:
            break
        chunks.append(data[pos:pos + n])
        pos += n
    return b"".join(chunks), pos


def _gif_lzw(data, min_code, limit):
    """GIF flavour of LZW (LSB first, codes of 2-12 bits) -> bytearray of palette indexes."""
    clear = 1 << min_code
    eoi = clear + 1
    first_size = min_code + 1
    base = [bytes((i,)) for i in range(clear)]
    table = base + [b"", b""]
    size = first_size
    mask = (1 << size) - 1
    nxt = eoi + 1  # next free code == len(table)
    out = bytearray()
    prev = None
    buf = bits = 0
    for byte in data:
        buf |= byte << bits
        bits += 8
        while bits >= size:
            code = buf & mask
            buf >>= size
            bits -= size
            if code == clear:
                del table[eoi + 1:]
                size = first_size
                mask = (1 << size) - 1
                nxt = eoi + 1
                prev = None
                continue
            if code == eoi:
                return out
            if prev is None:  # first code after a clear: must be a literal
                if code >= clear:
                    raise ValueError("Corrupt GIF data.")
                entry = table[code]
            elif code < nxt:
                entry = table[code]
            elif code == nxt:  # the code being defined right now (KwKwK case)
                entry = prev + prev[:1]
            else:
                raise ValueError("Corrupt GIF data.")
            out += entry
            if prev is not None and nxt < 4096:
                table.append(prev + entry[:1])
                nxt += 1
                if nxt == (1 << size) and size < 12:
                    size += 1
                    mask = (1 << size) - 1
            prev = entry
        if len(out) >= limit:
            break
    return out


def _gif_fill(canvas, rect, color):
    """Paint a rectangle of the canvas with one color (rows are replaced, never edited)."""
    x0, y0, x1, y1 = rect
    for y in range(y0, y1):
        row = canvas[y][:]
        row[x0:x1] = [color] * (x1 - x0)
        canvas[y] = row


def _decode_gif(data, background=(0, 0, 0)):
    """
    Returns (width, height, info, frames).

    frames is a generator of (rows, delay_in_seconds). Each rows is the COMPLETE
    picture (previous frames, transparency and disposal methods already applied)
    as a list of lists of (r, g, b); treat it as read-only, rows are shared
    between frames. Decoding is lazy: a frame is only decoded when it is asked for.

    info["loops"]: None = the file has no loop extension (play once),
                   0 = loop forever, n = repeat n more times.
    """
    if data[:6] not in _GIF_MAGIC:
        raise ValueError("The file is not a valid GIF.")
    if len(data) < 13:
        raise ValueError("Corrupt GIF header.")
    width, height, flags = struct.unpack("<HHB", data[6:11])
    if width == 0 or height == 0:
        raise ValueError("Invalid GIF size.")
    pos = 13
    global_table = None
    if flags & 0x80:
        n = 3 * (2 << (flags & 7))
        global_table = _gif_palette(data[pos:pos + n])
        pos += n
    info = {"loops": None}
    bg = tuple(background)

    def frames(pos):
        canvas = [[bg] * width for _ in range(height)]
        size = len(data)
        disposal = delay = 0  # of the NEXT image, set by the graphic control extension
        trans = None
        prev_disposal = 0
        prev_rect = saved = None
        produced = 0
        try:
            while pos < size:
                block = data[pos]
                pos += 1
                if block == 0x3B:  # trailer
                    break
                if block == 0x21:  # extension
                    label = data[pos]
                    body, pos = _gif_subblocks(data, pos + 1)
                    if label == 0xF9 and len(body) >= 4:  # graphic control
                        disposal = (body[0] >> 2) & 7
                        delay = body[1] | (body[2] << 8)
                        trans = body[3] if body[0] & 1 else None
                    elif label == 0xFF and body[:11] in (b"NETSCAPE2.0", b"ANIMEXTS1.0") \
                            and body[11:12] == b"\x01" and len(body) >= 14:
                        info["loops"] = body[12] | (body[13] << 8)
                    continue
                if block != 0x2C:  # not an image descriptor: skip garbage
                    continue

                left, top, fw, fh, fl = struct.unpack("<HHHHB", data[pos:pos + 9])
                pos += 9
                palette = global_table
                if fl & 0x80:
                    n = 3 * (2 << (fl & 7))
                    palette = _gif_palette(data[pos:pos + n])
                    pos += n
                if palette is None:  # no color table at all: fall back to grays
                    palette = [(i, i, i) for i in range(256)]
                min_code = data[pos]
                pos += 1
                if not 1 <= min_code <= 11:
                    raise ValueError("Corrupt GIF data.")
                body, pos = _gif_subblocks(data, pos)
                need = fw * fh
                idx = _gif_lzw(body, min_code, need)
                if len(idx) < need:
                    idx += bytes(need - len(idx))

                # 1) undo what the previous frame asked for
                if prev_disposal == 2 and prev_rect:
                    _gif_fill(canvas, prev_rect, bg)
                elif prev_disposal == 3 and saved is not None:
                    canvas = saved
                saved = list(canvas) if disposal == 3 else None

                # 2) draw this frame (rows are copied before being changed)
                if fl & 0x40:
                    order = [y for start, step in _GIF_INTERLACE for y in range(start, fh, step)]
                else:
                    order = range(fh)
                x0, x1 = left, min(left + fw, width)
                y1 = min(top + fh, height)
                if x1 > x0:
                    span = x1 - x0
                    for k, y in enumerate(order):
                        cy = top + y
                        if cy >= height:
                            continue
                        line = idx[k * fw:k * fw + span]
                        old = canvas[cy]
                        new = old[:]
                        if trans is None:
                            new[x0:x1] = [palette[i] for i in line]
                        else:
                            new[x0:x1] = [o if i == trans else palette[i]
                                          for i, o in zip(line, old[x0:x1])]
                        canvas[cy] = new

                seconds = delay / 100
                if seconds < 0.02:  # browsers treat 0 and 1 centiseconds as 10
                    seconds = 0.1
                prev_disposal, prev_rect = disposal, (x0, top, x1, y1)
                disposal = delay = 0
                trans = None
                produced += 1
                yield list(canvas), seconds
        except Exception as error:
            if produced == 0:
                if isinstance(error, ValueError):
                    raise
                raise ValueError("Corrupt or unsupported GIF.") from error
            return  # truncated file: keep the frames that were fine
        if produced == 0:
            raise ValueError("No image found in the GIF file.")

    return width, height, info, frames(pos)


def _decode_image(data, background=(0, 0, 0), box=None):
    """Decode PNG, JPEG or GIF bytes (detected from the first bytes, not from the name).
    For an animated GIF this returns the first frame."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _decode_png(data, background)
    if data[:2] == b"\xff\xd8":
        try:
            return _decode_jpeg(data, box)
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("Corrupt or unsupported JPEG.") from error
    if data[:6] in _GIF_MAGIC:
        width, height, _, frames = _decode_gif(data, background)
        return width, height, next(frames)[0]
    raise ValueError("Unsupported image format (only PNG, JPEG and GIF).")


def _read_bytes(source):
    """Contents of a file path or http(s) URL."""
    source = str(source)
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=15) as response:
            return response.read()
    return Path(source).read_bytes()


def read_image(source, background=(0, 0, 0), box=None):
    """
    Read a PNG, JPEG or GIF from a path or URL and return (width, height, rows).
    For an animated GIF this is the first frame (see read_gif for all of them).

    background: (r, g, b) color that PNG/GIF transparency is blended with.
    box:        optional (pixels wide, pixels high) of the area the picture will be
                drawn in; lets big JPEGs be decoded at a reduced size (faster).
    """
    return _decode_image(_read_bytes(source), background, box)


def read_gif(source, background=(0, 0, 0)):
    """
    Read a GIF from a path or URL. Returns (width, height, frames, loops):

        frames  list of (rows, delay_in_seconds); every rows is the complete picture
                (list of lists of (r, g, b)), do not modify it
        loops   None = play once, 0 = forever, n = repeat n more times
    """
    width, height, info, frames = _decode_gif(_read_bytes(source), background)
    return width, height, list(frames), info["loops"]


def read_png(source, background=(0, 0, 0)):
    """Read an image (PNG, JPEG or GIF) from a path or URL: (width, height, rows)."""
    return read_image(source, background)


# ---------------------------------------------------------------------------
# 3. Resizing: area averaging (shrink) / bilinear (enlarge) in LINEAR light
# ---------------------------------------------------------------------------
_TO_LINEAR = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
                for c in (i / 255 for i in range(256))]
_N_LIN = 4096
_FROM_LINEAR = [round(255 * (12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055))
                for v in (i / (_N_LIN - 1) for i in range(_N_LIN))]


def _weights(src_n, dst_n):
    """For every destination index: list of (source index, weight); weights sum to 1."""
    out = []
    ratio = src_n / dst_n
    if ratio >= 1:  # shrinking: exact box coverage
        for d in range(dst_n):
            a, b = d * ratio, (d + 1) * ratio
            taps = []
            for i in range(int(a), min(src_n, int(math.ceil(b)))):
                w = min(b, i + 1) - max(a, i)
                if w > 1e-9:
                    taps.append((i, w / ratio))
            out.append(taps or [(min(src_n - 1, int(a)), 1.0)])
    else:  # enlarging: bilinear
        for d in range(dst_n):
            c = (d + 0.5) * ratio - 0.5
            i0 = math.floor(c)
            f = c - i0
            lo = min(max(i0, 0), src_n - 1)
            hi = min(max(i0 + 1, 0), src_n - 1)
            out.append([(lo, 1 - f), (hi, f)] if lo != hi and f > 1e-9 else [(lo, 1.0)])
    return out


def _mix(vectors, taps_list):
    """Weighted sum of whole vectors: one output vector per entry of taps_list."""
    out = []
    for taps in taps_list:
        if len(taps) == 1:
            i, w = taps[0]
            out.append(vectors[i] if w == 1.0 else [w * v for v in vectors[i]])
            continue
        i, w = taps[0]
        acc = [w * v for v in vectors[i]]
        for i, w in taps[1:]:
            acc = [a + w * v for a, v in zip(acc, vectors[i])]
        out.append(acc)
    return out


def _resize(rows, width, height, new_w, new_h):
    if (new_w, new_h) == (width, height):
        return rows
    wy = _weights(height, new_h)
    wx = _weights(width, new_w)

    # sRGB -> linear light, one list of floats per channel and row
    get = _TO_LINEAR.__getitem__
    planes = ([], [], [])
    for row in rows:
        r, g, b = zip(*row)
        planes[0].append(list(map(get, r)))
        planes[1].append(list(map(get, g)))
        planes[2].append(list(map(get, b)))

    resized = []
    for plane in planes:
        vert = _mix(plane, wy)                    # new_h rows x width
        cols = _mix(list(zip(*vert)), wx)         # new_w columns x new_h (transposed trick)
        resized.append(list(zip(*cols)))          # back to new_h rows x new_w
        del plane[:], vert, cols

    top = _N_LIN - 1
    f = _FROM_LINEAR
    result = []
    for y in range(new_h):
        result.append([(f[min(top, int(r * top + 0.5))],
                        f[min(top, int(g * top + 0.5))],
                        f[min(top, int(b * top + 0.5))])
                        for r, g, b in zip(resized[0][y], resized[1][y], resized[2][y])])
    return result


def _fit(width, height, box_w, box_h):
    scale = min(box_w / width, box_h / height)
    return max(1, int(width * scale)), max(1, int(height * scale))


# ---------------------------------------------------------------------------
# 4a. Renderer "blocks": quadrant characters, best-fit glyph per cell
# ---------------------------------------------------------------------------
# bit 0 = upper-left, 1 = upper-right, 2 = lower-left, 3 = lower-right
_PARTS = []
for _mask, _glyph in ((0, " "), (1, "\u2598"), (2, "\u259d"), (4, "\u2596"), (8, "\u2597"),
                        (3, "\u2580"), (5, "\u258c"), (6, "\u259e")):
    _PARTS.append((_glyph,
                    [i for i in range(4) if _mask >> i & 1],
                    [i for i in range(4) if not _mask >> i & 1]))


def _group(px, ids):
    n = len(ids)
    sr = sg = sb = sq = 0
    for i in ids:
        r, g, b = px[i]
        sr += r
        sg += g
        sb += b
        sq += r * r + g * g + b * b
    mean = (int(sr / n + 0.5), int(sg / n + 0.5), int(sb / n + 0.5))
    return mean, sq - (sr * sr + sg * sg + sb * sb) / n


@lru_cache(maxsize=1 << 16)
def _best_cell(px):
    """Pick the glyph + (fg, bg) colors that best reproduce 4 sub-pixels."""
    if px[0] == px[1] == px[2] == px[3]:
        return " ", None, px[0]
    best = None
    for glyph, fg_ids, bg_ids in _PARTS:
        if not fg_ids:
            bg, err = _group(px, bg_ids)
            fg = None
        else:
            fg, e1 = _group(px, fg_ids)
            bg, e2 = _group(px, bg_ids)
            err = e1 + e2
        if best is None or err < best[0]:
            best = (err, glyph, fg, bg)
    return best[1], best[2], best[3]


_LEVELS = (0, 95, 135, 175, 215, 255)


def _to_256(r, g, b):
    ci = [min(range(6), key=lambda k: abs(_LEVELS[k] - v)) for v in (r, g, b)]
    cr, cg, cb = (_LEVELS[k] for k in ci)
    cube = 16 + 36 * ci[0] + 6 * ci[1] + ci[2]
    gi = min(23, max(0, (r + g + b) // 3 - 3) // 10)
    gv = 8 + 10 * gi
    d_cube = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
    d_gray = (r - gv) ** 2 + (g - gv) ** 2 + (b - gv) ** 2
    return cube if d_cube <= d_gray else 232 + gi


def _sgr(base, rgb, truecolor):
    if truecolor:
        return "\x1b[%d;2;%d;%d;%dm" % (base, rgb[0], rgb[1], rgb[2])
    return "\x1b[%d;5;%dm" % (base, _to_256(*rgb))


def _render_blocks(img, w, h, truecolor):
    lines = []
    for y in range(0, h, 2):
        top, bottom = img[y], img[y + 1]
        cur_fg = cur_bg = None
        parts = []
        for x in range(0, w, 2):
            glyph, fg, bg = _best_cell((top[x], top[x + 1], bottom[x], bottom[x + 1]))
            if fg is not None and fg != cur_fg:
                parts.append(_sgr(38, fg, truecolor))
                cur_fg = fg
            if bg != cur_bg:
                parts.append(_sgr(48, bg, truecolor))
                cur_bg = bg
            parts.append(glyph)
        lines.append("".join(parts) + "\x1b[0m")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4b. Renderer "sixel": median-cut palette (256 colors) + sixel encoding
# ---------------------------------------------------------------------------
def _quantize(img, colors=256):
    """Median cut on a 6-bit-per-channel histogram. Returns (palette, index rows)."""
    bins = {}
    for row in img:
        for r, g, b in row:
            key = ((r >> 2) << 12) | ((g >> 2) << 6) | (b >> 2)
            e = bins.get(key)
            if e is None:
                bins[key] = [1, r, g, b]
            else:
                e[0] += 1
                e[1] += r
                e[2] += g
                e[3] += b

    items = [(k, e[0], e[1], e[2], e[3]) for k, e in bins.items()]
    shifts = (12, 6, 0)

    def make_box(box):
        count = sum(i[1] for i in box)
        best_axis, best_range = 0, 0
        for axis, sh in enumerate(shifts):
            vals = [(i[0] >> sh) & 63 for i in box]
            rng = max(vals) - min(vals)
            if rng > best_range:
                best_axis, best_range = axis, rng
        return count * best_range, best_axis, box

    heap = []
    serial = 0
    score, axis, box = make_box(items)
    heapq.heappush(heap, (-score, serial, axis, box))
    while len(heap) < colors:
        neg, _, axis, box = heap[0]
        if neg == 0:  # nothing left to split
            break
        heapq.heappop(heap)
        sh = shifts[axis]
        box.sort(key=lambda i: (i[0] >> sh) & 63)
        half = sum(i[1] for i in box) / 2
        acc = 0
        cut = 1
        for n, item in enumerate(box):
            acc += item[1]
            if acc >= half:
                cut = min(max(n + 1, 1), len(box) - 1)
                break
        for part in (box[:cut], box[cut:]):
            serial += 1
            s, a, b = make_box(part)
            heapq.heappush(heap, (-s, serial, a, b))

    palette = []
    lookup = {}
    for index, (_, _, _, box) in enumerate(heap):
        n = sum(i[1] for i in box)
        palette.append((sum(i[2] for i in box) // n,
                        sum(i[3] for i in box) // n,
                        sum(i[4] for i in box) // n))
        for item in box:
            lookup[item[0]] = index

    idx = [[lookup[((r >> 2) << 12) | ((g >> 2) << 6) | (b >> 2)] for r, g, b in row]
            for row in img]
    return palette, idx


def _rle(arr):
    res = []
    for value, group in groupby(arr):
        n = sum(1 for _ in group)
        ch = chr(63 + value)
        res.append("!%d%s" % (n, ch) if n > 3 else ch * n)
    return "".join(res)


def _render_sixel(img, w, h):
    palette, idx = _quantize(img)
    out = ['\x1bP0;1;0q"1;1;%d;%d' % (w, h)]
    for i, (r, g, b) in enumerate(palette):
        out.append("#%d;2;%d;%d;%d" % (i, round(r * 100 / 255), round(g * 100 / 255),
                                        round(b * 100 / 255)))
    for y0 in range(0, h, 6):
        band = {}
        for dy in range(min(6, h - y0)):
            bit = 1 << dy
            for x, ci in enumerate(idx[y0 + dy]):
                column = band.get(ci)
                if column is None:
                    column = band[ci] = bytearray(w)
                column[x] |= bit
        out.append("$".join("#%d%s" % (ci, _rle(col.rstrip(b"\x00")))
                            for ci, col in band.items()))
        out.append("-")
    out.append("\x1b\\")
    return "".join(out)


# ---------------------------------------------------------------------------
# 4c. Renderers "kitty" and "iterm2": the terminal draws a real PNG
# ---------------------------------------------------------------------------
def _encode_png(img, w, h):
    raw = bytearray()
    for row in img:
        raw.append(0)
        raw += bytes(chain.from_iterable(row))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def _render_kitty(png, cols, image_id=None):
    """image_id is used by animations: the previous frame with that id is deleted first."""
    b64 = base64.standard_b64encode(png).decode("ascii")
    pieces = [b64[i:i + 4096] for i in range(0, len(b64), 4096)]
    extra = ",i=%d" % image_id if image_id else ""
    out = ["\x1b_Ga=d,d=I,i=%d,q=2\x1b\\" % image_id] if image_id else []
    for n, piece in enumerate(pieces):
        more = 1 if n < len(pieces) - 1 else 0
        control = "a=T,f=100,q=2,c=%d%s,m=%d" % (cols, extra, more) if n == 0 else "m=%d" % more
        out.append("\x1b_G%s;%s\x1b\\" % (control, piece))
    return "".join(out)


def _render_iterm2(png, cols):
    b64 = base64.standard_b64encode(png).decode("ascii")
    return "\x1b]1337;File=inline=1;size=%d;width=%d;preserveAspectRatio=1:%s\x07" % (
        len(png), cols, b64)


# ---------------------------------------------------------------------------
# 5. Public API
# ---------------------------------------------------------------------------
def _pixel_box(max_width, max_height, cell, term_size):
    """Size in pixels of the area an image may fill: (width, height)."""
    cw, ch = cell
    columns, lines = term_size
    cols_avail = max(1, min(columns, max_width or columns))
    rows_avail = max(1, max_height or max(1, lines - 2))
    return cols_avail * cw, rows_avail * ch


def render(width, height, rows, max_width=None, max_height=None, mode="auto", cell=None, _term_size=None,
           _kitty_id=None):
    """Turn decoded pixels into a string ready to print with the chosen renderer."""
    caps = _terminal_caps()
    if mode in (None, "auto"):
        mode = caps["mode"]
    if mode not in MODES[1:]:
        raise ValueError("mode must be one of: " + ", ".join(MODES))
    cw, ch = cell or caps["cell"]

    box_w, box_h = _pixel_box(max_width, max_height, (cw, ch),
                                _term_size or shutil.get_terminal_size((80, 24)))

    if mode == "blocks":
        pw, ph = _fit(width, height, box_w, box_h)
        cols = max(1, round(pw / cw))
        text_rows = max(1, round(ph / ch))
        img = _resize(rows, width, height, cols * 2, text_rows * 2)
        return _render_blocks(img, cols * 2, text_rows * 2, _truecolor())

    if mode == "sixel":
        if not (max_width or max_height):  # don't blow tiny images up more than 2x
            box_w, box_h = min(box_w, 2 * width), min(box_h, 2 * height)
        pw, ph = _fit(width, height, box_w, box_h)
        return _render_sixel(_resize(rows, width, height, pw, ph), pw, ph)

    # kitty / iterm2: send the PNG, the terminal scales it on the GPU
    pw, ph = _fit(width, height, box_w, box_h)
    cols = max(1, round(pw / cw))
    if pw < width:
        png = _encode_png(_resize(rows, width, height, pw, ph), pw, ph)
    else:
        png = _encode_png(rows, width, height)
    return _render_kitty(png, cols, _kitty_id) if mode == "kitty" else _render_iterm2(png, cols)


# Rendered output is kept in memory: showing the same image again (a menu that
# redraws on every key press) is a dictionary lookup instead of a decode + render.
_CACHE = {}
_PENDING = {}
_LOCK = threading.Lock()
_CACHE_LIMIT = 32
_KITTY_IDS = count(1)  # one kitty image id per animation


def _gif_delays(data):
    """
    Frame delays (seconds) of a GIF, read by walking its blocks WITHOUT decoding any
    pixel (takes a few milliseconds). Used to know the length of the animation before
    it is rendered. Returns None if the structure can not be followed.
    """
    try:
        size = len(data)
        flags = data[10]
        pos = 13 + (3 * (2 << (flags & 7)) if flags & 0x80 else 0)
        delays = []
        delay = 0
        while pos < size:
            block = data[pos]
            pos += 1
            if block == 0x3B:
                break
            if block == 0x21:
                label = data[pos]
                if label == 0xF9 and data[pos + 1] >= 4:
                    delay = data[pos + 3] | (data[pos + 4] << 8)
                _, pos = _gif_subblocks(data, pos + 1)
            elif block == 0x2C:
                fl = data[pos + 8]
                pos += 9
                if fl & 0x80:
                    pos += 3 * (2 << (fl & 7))
                _, pos = _gif_subblocks(data, pos + 1)  # skip min code size + LZW data
                seconds = delay / 100
                delays.append(0.1 if seconds < 0.02 else seconds)
                delay = 0
        return delays or None
    except Exception:
        return None


class _Animation:
    """
    A GIF being rendered frame by frame by ONE background thread, while the main
    thread only plays what is ready. Frames are kept as ready to print strings, so
    every later loop is just a lookup. Thread safe: preload() and show_image() can
    share the same animation.
    """

    MARGIN = 1.3  # safety factor over the measured render time per frame

    def __init__(self, data, background, max_width, max_height, mode, cell, size):
        self.width, self.height, self.info, self._frames = _decode_gif(data, background)
        caps = _terminal_caps()  # detected here, in the caller's thread
        self.mode = caps["mode"] if mode in (None, "auto") else mode
        if self.mode not in MODES[1:]:
            raise ValueError("mode must be one of: " + ", ".join(MODES))
        self._args = (max_width, max_height, self.mode, cell)
        self._size = size
        self._kitty_id = next(_KITTY_IDS)
        self.outputs = []  # rendered frames
        self.delays = []   # seconds each frame stays on screen
        self.done = False
        self.error = None
        self._busy = 0.0   # seconds spent decoding + rendering so far
        self._cond = threading.Condition()
        self._worker = None
        plan = _gif_delays(data)  # whole timeline, known before rendering anything
        self.plan = plan
        self._cum = [0.0] + list(accumulate(plan)) if plan else None
        self.rows = self._text_rows(max_width, max_height, cell, size, caps)

    def _text_rows(self, max_width, max_height, cell, size, caps):
        """How many text lines one frame covers (to reserve room for the animation)."""
        cw, ch = cell or caps["cell"]
        width, height = self.width, self.height
        box_w, box_h = _pixel_box(max_width, max_height, (cw, ch), size)
        if self.mode == "sixel" and not (max_width or max_height):
            box_w, box_h = min(box_w, 2 * width), min(box_h, 2 * height)
        pw, ph = _fit(width, height, box_w, box_h)
        if self.mode == "blocks":
            return max(1, round(ph / ch))
        if self.mode == "sixel":
            return max(1, -(-ph // ch))
        cols = max(1, round(pw / cw))  # kitty / iterm2: the terminal keeps the aspect ratio
        return max(1, math.ceil(cols * cw * height / width / ch))

    # -- producer ---------------------------------------------------------
    def start(self):
        """Start rendering in the background (only the first call does anything)."""
        with self._cond:
            if self._worker is None and not self.done:
                self._worker = threading.Thread(target=self._run, name="imgterm-render", daemon=True)
                self._worker.start()

    def _run(self):
        max_width, max_height, mode, cell = self._args
        while True:
            began = time.perf_counter()
            try:
                rows, delay = next(self._frames)
                output = render(self.width, self.height, rows, max_width, max_height, mode,
                                cell, _term_size=self._size, _kitty_id=self._kitty_id)
            except StopIteration:
                break
            except Exception as error:
                if not self.outputs:
                    self.error = error
                break  # a damaged tail only shortens the animation
            spent = time.perf_counter() - began
            with self._cond:
                self.outputs.append(output)
                self.delays.append(delay)
                self._busy += spent
                self._cond.notify_all()
        with self._cond:
            self.done = True
            self._cond.notify_all()

    # -- consumer ---------------------------------------------------------
    def get(self, index):
        """Wait until frame 'index' is rendered. Returns False if the GIF has no such frame."""
        self.start()
        with self._cond:
            while len(self.outputs) <= index and not self.done:
                self._cond.wait()
            if index < len(self.outputs):
                return True
        if index == 0 and self.error is not None:
            raise self.error
        return False

    def load_all(self):
        self.start()
        with self._cond:
            while not self.done:
                self._cond.wait()

    def _can_play_smoothly(self):
        """True when, starting to play right now, no frame would be late (cond held)."""
        k = len(self.outputs)
        if self.done:
            return True
        if k == 0:
            return False
        if self._cum is None:  # timeline unknown: start as soon as there is a picture
            return True
        total = len(self._cum) - 1
        if k >= total:
            return True
        rate = self._busy / k * self.MARGIN
        cum = self._cum
        # frame j is drawn at cum[j] seconds from now; it is ready after (j - k + 1) renders
        return all((j - k + 1) * rate <= cum[j] for j in range(k, total))

    def wait_until_smooth(self, on_wait=None, patience=0.25):
        """Block until playing can not stutter. on_wait(done, total) is called while waiting."""
        self.start()
        began = time.perf_counter()
        with self._cond:
            while not self._can_play_smoothly():
                self._cond.wait(0.05)
                if on_wait and time.perf_counter() - began > patience:
                    total = len(self._cum) - 1 if self._cum else 0
                    on_wait(len(self.outputs), total)
        if self.error is not None and not self.outputs:
            raise self.error


def _animate(anim, loops=None, smooth=True):
    """Play an animation in place: every frame is drawn over the previous one."""
    out = sys.stdout
    if not anim.get(0):
        raise ValueError("No image found in the GIF file.")
    if not out.isatty() or not anim.get(1):  # output is not a terminal, or a single frame
        out.write(anim.outputs[0] + "\n")
        out.flush()
        return

    if loops is None:  # follow the GIF: no extension = once, 0 = forever, n = n repeats
        file_loops = anim.info["loops"]
        loops = 1 if file_loops is None else (0 if file_loops == 0 else file_loops + 1)

    rows = anim.rows
    # Reserve the lines first (so nothing scrolls while playing), go back to the top
    # of that area and remember it (ESC 7); every frame starts with ESC 8 to return.
    out.write("\r\x1b[?25l" + "\n" * rows + "\x1b[%dA" % rows + "\x1b7")
    out.flush()
    switch = sys.getswitchinterval()
    sys.setswitchinterval(0.001)  # the render thread must not make the player wait for the GIL
    try:
        if smooth:  # first pass: wait just long enough to never stutter afterwards
            shown = []

            def progress(done, total):
                text = "buffering %d%%" % (100 * done // total) if total else "buffering..."
                out.write("\x1b8\x1b[2K" + text)
                out.flush()
                shown.append(1)

            anim.wait_until_smooth(progress)
            if shown:
                out.write("\x1b8\x1b[2K")
        played = 0
        deadline = time.perf_counter()
        while loops == 0 or played < loops:
            index = 0
            while True:
                if len(anim.outputs) <= index:  # not ready yet (only with smooth=False)
                    if not anim.get(index):
                        break
                    deadline = time.perf_counter()  # don't try to catch up
                out.write("\x1b8" + anim.outputs[index])
                out.flush()
                deadline += anim.delays[index]
                wait = deadline - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                else:
                    deadline = time.perf_counter()
                index += 1
            played += 1
    except KeyboardInterrupt:
        pass
    finally:  # leave the cursor right below the picture, like a still image would
        sys.setswitchinterval(switch)
        out.write("\x1b8\x1b[%dB\r\x1b[?25h" % rows)
        out.flush()


def _resolve(path, folder):
    """Return (source, error_message). source is a Path or a URL string."""
    path = str(path)
    if path.startswith(("http://", "https://")):
        return path, None
    target = Path(path).expanduser()
    if folder is not None and not target.is_absolute():
        target = Path(folder).expanduser() / target
    if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif"):
        return None, (f"Only PNG, JPEG and GIF images can be displayed without external libraries "
                        f"(received: {target.suffix or 'no extension'}).")
    if not target.exists():
        return None, f"Image not found: {target.resolve()}"
    return target, None


def _make_key(source, max_width, max_height, mode, background, cell):
    caps = _terminal_caps()
    if isinstance(source, Path):
        st = source.stat()
        ident = (str(source.resolve()), st.st_mtime_ns, st.st_size)  # reload if the file changes
    else:
        ident = (source,)
    size = tuple(shutil.get_terminal_size((80, 24)))
    return (ident, max_width, max_height, mode, tuple(background),
            tuple(cell) if cell else None, size, caps["mode"], caps["cell"], _truecolor())


def _cached_output(key, source, max_width, max_height, mode, background, cell, size):
    """Return the rendered string for 'key', building it at most once (even across threads)."""
    while True:
        with _LOCK:
            if key in _CACHE:
                return _CACHE[key]
            event = _PENDING.get(key)
            if event is None:
                event = _PENDING[key] = threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            event.wait()  # another thread (preload) is already building it
            continue
        try:
            box = _pixel_box(max_width, max_height, cell or _terminal_caps()["cell"], size)
            data = _read_bytes(source)
            if data[:6] in _GIF_MAGIC:  # animation: frames are rendered on demand
                output = _Animation(data, background, max_width, max_height, mode, cell, size)
            else:
                width, height, rows = _decode_image(data, background, box)
                output = render(width, height, rows, max_width, max_height, mode, cell,
                                _term_size=size)
            with _LOCK:
                if len(_CACHE) >= _CACHE_LIMIT:
                    _CACHE.pop(next(iter(_CACHE)))
                _CACHE[key] = output
            return output
        finally:
            with _LOCK:
                _PENDING.pop(key, None)
            event.set()


def show_image(path, folder=None, max_width=None, max_height=None, mode="auto", background=(0, 0, 0), cell=None,
               loops=None, smooth=True):
    """
    Draw a PNG, JPEG or GIF image in the terminal. GIFs are animated in place.

    path:        path (relative or absolute) or URL of the image.
    folder:      folder to search in if 'path' is relative (optional).
    max_width:   maximum width in columns (optional).
    max_height:  maximum height in text lines (optional).
    mode:        "auto" (default), "sixel", "kitty", "iterm2" or "blocks".
    background:  (r, g, b) color that transparent pixels are blended with.
    cell:        (width, height) of a character cell in pixels (optional).
    loops:       GIF only: how many times to play it. None (default) follows the
                 file (most GIFs loop forever: press Ctrl+C to stop), 0 = forever,
                 1 = once, 2 = twice...  show_image() returns when it has finished.
    smooth:      GIF only: True (default) waits, only if needed, until the animation can
                 be played from the first frame to the last without stuttering (the rest
                 is rendered in the background while it plays). False starts at once and
                 may stutter during the first pass if rendering is slower than the GIF.

    The rendered result is cached: drawing the same image again is instant.
    """
    prepare_terminal()
    source, problem = _resolve(path, folder)
    if problem:
        print(problem)
        return

    try:
        key = _make_key(source, max_width, max_height, mode, background, cell)
        output = _cached_output(key, source, max_width, max_height, mode, background, cell,
                                tuple(shutil.get_terminal_size((80, 24))))
        if isinstance(output, _Animation):
            _animate(output, loops, smooth)
            return
    except Exception as error:
        print(f"The image could not be read: {error}")
        return

    sys.stdout.write(output + "\n")
    sys.stdout.flush()


def preload(path, folder=None, max_width=None, max_height=None, mode="auto", background=(0, 0, 0), cell=None, threaded=True):
    """
    Decode and render an image ahead of time so the first show_image() is instant.

    Call it with the same arguments you will pass to show_image(). By default the
    work runs in a background thread and preload() returns immediately; use
    threaded=False to wait until it is done. Errors are ignored here: show_image()
    will report them when it is called.
    """
    try:
        prepare_terminal()
        source, problem = _resolve(path, folder)
        if problem:
            return None
        # Terminal detection talks to stdin/stdout, so do it here in the caller's
        # thread (never in the background one, which would fight with key input).
        key = _make_key(source, max_width, max_height, mode, background, cell)
        size = tuple(shutil.get_terminal_size((80, 24)))
    except Exception:
        return None

    def work():
        try:
            output = _cached_output(key, source, max_width, max_height, mode, background, cell, size)
            if isinstance(output, _Animation):
                output.load_all()  # every frame, so the first play has no waits
        except Exception:
            pass

    if not threaded:
        work()
        return None
    thread = threading.Thread(target=work, name="imgterm-preload", daemon=True)
    thread.start()
    return thread


def _parse_color(text):
    text = text.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Show a PNG, JPEG or GIF in the terminal.")
    parser.add_argument("image", help="path or URL of the PNG, JPEG or GIF")
    parser.add_argument("-f", "--folder", help="folder where the image is searched")
    parser.add_argument("-w", "--width", type=int, help="maximum width in columns")
    parser.add_argument("-H", "--height", type=int, help="maximum height in lines")
    parser.add_argument("-m", "--mode", choices=MODES, default="auto",
                        help="renderer (default: auto-detect)")
    parser.add_argument("--bg", default="000000", help="background for transparency, RRGGBB")
    parser.add_argument("--cell", help="character cell size in pixels, e.g. 10x20")
    parser.add_argument("-l", "--loops", type=int,
                        help="GIF: times to play it, 0 = forever (default: what the file says; Ctrl+C stops)")
    parser.add_argument("--no-smooth", action="store_true",
                        help="GIF: start at once even if the first pass may stutter")
    args = parser.parse_args(argv)

    cell = None
    if args.cell:
        m = re.fullmatch(r"(\d+)[xX](\d+)", args.cell)
        if not m:
            parser.error("--cell must look like 10x20")
        cell = (int(m.group(1)), int(m.group(2)))
    try:
        show_image(args.image, folder=args.folder, max_width=args.width, max_height=args.height,
                    mode=args.mode, background=_parse_color(args.bg), cell=cell, loops=args.loops,
                    smooth=not args.no_smooth)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()