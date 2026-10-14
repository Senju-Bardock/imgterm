"""
imgterm.py
Display a PNG image in the terminal (Windows, Linux, macOS) using ONLY the
Python standard library: nothing to pip install.

It picks the best renderer your terminal supports:

    sixel   real pixels (Windows Terminal, WezTerm, foot, xterm -ti vt340, ...)
    kitty   real pixels (kitty, Ghostty)
    iterm2  real pixels (iTerm2, WezTerm)
    blocks  universal fallback: quadrant characters, 2x2 sub-pixels per cell,
            best-fit glyph and gamma-correct resizing

Usage as a module:
    from imgterm import show_image
    show_image("photo.png")                         # relative or absolute path
    show_image("Senju_Bardock.png", folder="visual")
    show_image("https://site.com/image.png")        # URLs work too
    show_image("photo.png", max_width=60)           # limit width (columns)
    show_image("photo.png", mode="blocks")          # force a renderer

Usage from the terminal:
    python imgterm.py photo.png
    python imgterm.py photo.png --folder visual --width 60 --mode sixel

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
from functools import lru_cache
from itertools import accumulate, chain, groupby
from pathlib import Path

__all__ = ["show_image", "preload", "read_png", "render", "detect_mode"]

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


def read_png(source, background=(0, 0, 0)):
    """Read a PNG from a path or URL and return (width, height, rows)."""
    source = str(source)
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=15) as response:
            data = response.read()
    else:
        data = Path(source).read_bytes()
    return _decode_png(data, background)


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


def _render_kitty(png, cols):
    b64 = base64.standard_b64encode(png).decode("ascii")
    pieces = [b64[i:i + 4096] for i in range(0, len(b64), 4096)]
    out = []
    for n, piece in enumerate(pieces):
        more = 1 if n < len(pieces) - 1 else 0
        control = "a=T,f=100,q=2,c=%d,m=%d" % (cols, more) if n == 0 else "m=%d" % more
        out.append("\x1b_G%s;%s\x1b\\" % (control, piece))
    return "".join(out)


def _render_iterm2(png, cols):
    b64 = base64.standard_b64encode(png).decode("ascii")
    return "\x1b]1337;File=inline=1;size=%d;width=%d;preserveAspectRatio=1:%s\x07" % (
        len(png), cols, b64)


# ---------------------------------------------------------------------------
# 5. Public API
# ---------------------------------------------------------------------------
def render(width, height, rows, max_width=None, max_height=None, mode="auto", cell=None, _term_size=None):
    """Turn decoded pixels into a string ready to print with the chosen renderer."""
    caps = _terminal_caps()
    if mode in (None, "auto"):
        mode = caps["mode"]
    if mode not in MODES[1:]:
        raise ValueError("mode must be one of: " + ", ".join(MODES))
    cw, ch = cell or caps["cell"]

    columns, lines = _term_size or shutil.get_terminal_size((80, 24))
    cols_avail = max(1, min(columns, max_width or columns))
    rows_avail = max(1, max_height or max(1, lines - 2))
    box_w, box_h = cols_avail * cw, rows_avail * ch

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
    return _render_kitty(png, cols) if mode == "kitty" else _render_iterm2(png, cols)


# Rendered output is kept in memory: showing the same image again (a menu that
# redraws on every key press) is a dictionary lookup instead of a decode + render.
_CACHE = {}
_PENDING = {}
_LOCK = threading.Lock()
_CACHE_LIMIT = 32


def _resolve(path, folder):
    """Return (source, error_message). source is a Path or a URL string."""
    path = str(path)
    if path.startswith(("http://", "https://")):
        return path, None
    target = Path(path).expanduser()
    if folder is not None and not target.is_absolute():
        target = Path(folder).expanduser() / target
    if target.suffix.lower() != ".png":
        return None, (f"Only PNGs can be displayed without external libraries "
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
            width, height, rows = read_png(source, background)
            output = render(width, height, rows, max_width, max_height, mode, cell, _term_size=size)
            with _LOCK:
                if len(_CACHE) >= _CACHE_LIMIT:
                    _CACHE.pop(next(iter(_CACHE)))
                _CACHE[key] = output
            return output
        finally:
            with _LOCK:
                _PENDING.pop(key, None)
            event.set()


def show_image(path, folder=None, max_width=None, max_height=None, mode="auto", background=(0, 0, 0), cell=None):
    """
    Draw a PNG image in the terminal.

    path:        path (relative or absolute) or URL of the image.
    folder:      folder to search in if 'path' is relative (optional).
    max_width:   maximum width in columns (optional).
    max_height:  maximum height in text lines (optional).
    mode:        "auto" (default), "sixel", "kitty", "iterm2" or "blocks".
    background:  (r, g, b) color that transparent pixels are blended with.
    cell:        (width, height) of a character cell in pixels (optional).

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
            _cached_output(key, source, max_width, max_height, mode, background, cell, size)
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
    parser = argparse.ArgumentParser(description="Show a PNG in the terminal.")
    parser.add_argument("image", help="path or URL of the PNG")
    parser.add_argument("-f", "--folder", help="folder where the image is searched")
    parser.add_argument("-w", "--width", type=int, help="maximum width in columns")
    parser.add_argument("-H", "--height", type=int, help="maximum height in lines")
    parser.add_argument("-m", "--mode", choices=MODES, default="auto",
                        help="renderer (default: auto-detect)")
    parser.add_argument("--bg", default="000000", help="background for transparency, RRGGBB")
    parser.add_argument("--cell", help="character cell size in pixels, e.g. 10x20")
    args = parser.parse_args(argv)

    cell = None
    if args.cell:
        m = re.fullmatch(r"(\d+)[xX](\d+)", args.cell)
        if not m:
            parser.error("--cell must look like 10x20")
        cell = (int(m.group(1)), int(m.group(2)))
    show_image(args.image, folder=args.folder, max_width=args.width, max_height=args.height,
                mode=args.mode, background=_parse_color(args.bg), cell=cell)


if __name__ == "__main__":
    main()