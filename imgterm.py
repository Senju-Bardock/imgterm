"""
imgterm.py
Displays a PNG image in the terminal (Windows, Linux, macOS) using
ONLY the Python standard library: no pip install needed.

Usage as a module:
    from imgterm import show_image, preload
    show_image("photo.png")                          # relative or absolute path
    show_image("Senju_Bardock.png", folder="visual") # look inside a folder
    show_image("https://example.com/image.png")      # URLs work too
    show_image("photo.png", max_width=60)            # limit width (in columns)

Speed tips (menus, games, anything that redraws the same image often):
    * Every rendered image is cached in memory, so showing the same image again
      (same terminal size and limits) is practically instant.
    * Call preload() once at startup to pay the decoding cost in the background,
      before the image is first needed:
          preload("Senju_Bardock.png", folder="visual", max_width=40)
      Use the same folder / max_width / max_height you will pass to show_image().

Usage from the terminal:
    python imgterm.py photo.png
    python imgterm.py photo.png --folder visual --width 60
"""

import argparse
import os
import shutil
import struct
import sys
import threading
import urllib.request
import zlib
from itertools import accumulate
from pathlib import Path

__all__ = ["show_image", "preload", "read_png", "render"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# 1. Prepare the terminal (ANSI colors on Windows)
# ---------------------------------------------------------------------------
def prepare_terminal():
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # standard output
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            os.system("")  # classic trick to enable ANSI
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 2. PNG decoder (only zlib + struct + itertools, all standard library)
#
# Rows are kept as three "planes" (R, G, B) instead of lists of tuples:
# slicing and sum() work at C speed on planes, which makes both decoding and
# resizing much faster than looping pixel by pixel.
# ---------------------------------------------------------------------------
_MASKS = {}


def _masks(n):
    masks = _MASKS.get(n)
    if masks is None:
        masks = (int.from_bytes(b"\x7f" * n, "little"), int.from_bytes(b"\x80" * n, "little"))
        _MASKS[n] = masks
    return masks


def _add_bytes(a, b):
    """Adds two equal-length byte rows byte by byte (mod 256) using big-int math."""
    n = len(a)
    low, high = _masks(n)
    x = int.from_bytes(a, "little")
    y = int.from_bytes(b, "little")
    return bytearray((((x & low) + (y & low)) ^ ((x ^ y) & high)).to_bytes(n, "little"))


def _unfilter_sub(line, bpp):
    for c in range(bpp):
        line[c::bpp] = bytes([v & 255 for v in accumulate(line[c::bpp])])


def _unfilter_average(line, previous, bpp):
    for c in range(bpp):
        out = []
        append = out.append
        a = 0
        for x, b in zip(line[c::bpp], previous[c::bpp]):
            a = (x + ((a + b) >> 1)) & 255
            append(a)
        line[c::bpp] = bytes(out)


def _unfilter_paeth(line, previous, bpp):
    for c in range(bpp):
        out = []
        append = out.append
        a = cc = 0  # a = left pixel, cc = upper-left pixel (same channel)
        for x, b in zip(line[c::bpp], previous[c::bpp]):
            pa = b - cc
            pb = a - cc
            pc = pa + pb
            if pa < 0:
                pa = -pa
            if pb < 0:
                pb = -pb
            if pc < 0:
                pc = -pc
            if pa <= pb and pa <= pc:
                a = (x + a) & 255
            elif pb <= pc:
                a = (x + b) & 255
            else:
                a = (x + cc) & 255
            append(a)
            cc = b
        line[c::bpp] = bytes(out)


def _samples(line, width, channels, depth):
    """Converts a row of bytes into a list of values per channel."""
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


def _row_to_rgb(line, width, channels, depth, color, palette):
    """Generic (slower) row converter: handles every bit depth / color type."""
    s = _samples(line, width, channels, depth)
    scale = 255 // ((1 << depth) - 1) if depth < 8 else 1
    px = []
    for i in range(width):
        if color == 0:  # grayscale
            g = s[i] * scale
            px.append((g, g, g))
        elif color == 2:  # RGB
            j = i * 3
            px.append((s[j], s[j + 1], s[j + 2]))
        elif color == 3:  # palette
            px.append(palette[s[i]])
        elif color == 4:  # grayscale + alpha
            j = i * 2
            g = s[j] * s[j + 1] // 255
            px.append((g, g, g))
        else:  # RGBA (blended with a black background)
            j = i * 4
            a = s[j + 3]
            px.append((s[j] * a // 255, s[j + 1] * a // 255, s[j + 2] * a // 255))
    return px


def _plane_converter(width, channels, depth, color, palette):
    """Returns a function: unfiltered row -> (R, G, B) planes."""
    w = width
    if depth == 8 and color == 2:  # RGB
        return lambda l: (l[0::3], l[1::3], l[2::3])

    if depth == 8 and color == 6:  # RGBA (blended with a black background)
        def convert(l):
            r, g, b, a = l[0::4], l[1::4], l[2::4], l[3::4]
            if a.count(255) == w:  # fully opaque row: nothing to blend
                return r, g, b
            return (
                [x * k // 255 for x, k in zip(r, a)],
                [x * k // 255 for x, k in zip(g, a)],
                [x * k // 255 for x, k in zip(b, a)],
            )
        return convert

    if depth == 8 and color == 0:  # grayscale
        def convert(l):
            g = l[:w]
            return g, g, g
        return convert

    if depth == 8 and color == 4:  # grayscale + alpha
        def convert(l):
            g = [x * k // 255 for x, k in zip(l[0::2], l[1::2])]
            return g, g, g
        return convert

    if depth == 8 and color == 3 and palette:  # palette
        pad = [0] * (256 - len(palette))
        table_r = bytes([p[0] for p in palette] + pad)
        table_g = bytes([p[1] for p in palette] + pad)
        table_b = bytes([p[2] for p in palette] + pad)

        def convert(l):
            idx = bytes(l[:w])
            return idx.translate(table_r), idx.translate(table_g), idx.translate(table_b)
        return convert

    def convert(l):
        return tuple(zip(*_row_to_rgb(l, w, channels, depth, color, palette)))
    return convert


def _decode_png(data):
    """Returns (width, height, planes) where planes[y] = (R, G, B) rows."""
    if data[:8] != _PNG_SIGNATURE:
        raise ValueError("The file is not a valid PNG.")

    pos = 8
    idat = bytearray()
    palette = None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlaced = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break

    if interlaced:
        raise ValueError("Interlaced PNG (Adam7) is not supported. Save it without interlacing.")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    bits_pixel = channels * depth
    bpp = max(1, bits_pixel // 8)
    stride = (width * bits_pixel + 7) // 8
    raw = zlib.decompress(bytes(idat))
    convert = _plane_converter(width, channels, depth, color, palette)

    planes = []
    previous = bytearray(stride)
    p = 0
    for _ in range(height):
        filter_type = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride

        if filter_type == 1:  # Sub
            _unfilter_sub(line, bpp)
        elif filter_type == 2:  # Up
            line = _add_bytes(line, previous)
        elif filter_type == 3:  # Average
            _unfilter_average(line, previous, bpp)
        elif filter_type == 4:  # Paeth
            _unfilter_paeth(line, previous, bpp)

        planes.append(convert(line))
        previous = line

    return width, height, planes


def _read_bytes(source):
    source = str(source)
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=15) as response:
            return response.read()
    return Path(source).read_bytes()


def read_png(source):
    """Reads a PNG from a path or a URL. Returns (width, height, rows)."""
    width, height, planes = _decode_png(_read_bytes(source))
    return width, height, [list(zip(r, g, b)) for r, g, b in planes]


# ---------------------------------------------------------------------------
# 3. Resize and draw
# ---------------------------------------------------------------------------
def _resize_planes(planes, width, height, new_width, new_height):
    """
    Averages the pixels of each area (looks better than nearest neighbor).
    Works on planes with C-speed slicing/sum, same result as pixel-by-pixel.
    """
    spans = []
    for tx in range(new_width):
        x0 = tx * width // new_width
        x1 = max(x0 + 1, (tx + 1) * width // new_width)
        spans.append((x0, x1))
    span_widths = [x1 - x0 for x0, x1 in spans]

    # Horizontal pass: sum of every area, row by row
    summed = [
        (
            [sum(r[x0:x1]) for x0, x1 in spans],
            [sum(g[x0:x1]) for x0, x1 in spans],
            [sum(b[x0:x1]) for x0, x1 in spans],
        )
        for r, g, b in planes
    ]

    # Vertical pass: add the rows of every area and divide by its pixel count
    result = []
    for ty in range(new_height):
        y0 = ty * height // new_height
        y1 = max(y0 + 1, (ty + 1) * height // new_height)
        rows = y1 - y0
        if rows == 1:
            sr, sg, sb = summed[y0]
        else:
            block = summed[y0:y1]
            sr = list(map(sum, zip(*[t[0] for t in block])))
            sg = list(map(sum, zip(*[t[1] for t in block])))
            sb = list(map(sum, zip(*[t[2] for t in block])))
        result.append((
            [v // (w * rows) for v, w in zip(sr, span_widths)],
            [v // (w * rows) for v, w in zip(sg, span_widths)],
            [v // (w * rows) for v, w in zip(sb, span_widths)],
        ))
    return result


def _render_planes(width, height, planes, max_width, max_height, columns, lines):
    max_w = min(columns, max_width or columns)
    max_h = max(2, (max_height or (lines - 2)) * 2)  # each text line = 2 pixels

    scale = min(max_w / width, max_h / height)
    new_width = max(1, int(width * scale))
    new_height = max(2, int(height * scale))
    new_height -= new_height % 2  # must be even

    img = _resize_planes(planes, width, height, new_width, new_height)

    out = []
    for y in range(0, new_height, 2):
        r1, g1, b1 = img[y]
        r2, g2, b2 = img[y + 1]
        # "▀" = upper half (text color) + lower half (background color)
        parts = [
            f"\x1b[38;2;{a};{b};{c}m\x1b[48;2;{d};{e};{f}m▀"
            for a, b, c, d, e, f in zip(r1, g1, b1, r2, g2, b2)
        ]
        out.append("".join(parts) + "\x1b[0m")
    return "\n".join(out)


def render(width, height, rows, max_width=None, max_height=None):
    """Turns pixels into a string of ANSI codes, ready to print."""
    columns, lines = shutil.get_terminal_size((80, 24))
    planes = [tuple(zip(*row)) for row in rows]
    return _render_planes(width, height, planes, max_width, max_height, columns, lines)


# ---------------------------------------------------------------------------
# 4. Cache + preload (what makes repeated redraws instant)
# ---------------------------------------------------------------------------
_DECODED = {}   # decoded images: re-rendering after a terminal resize is fast
_RENDERED = {}  # final ANSI strings: showing the same image again is instant
_DECODED_LIMIT = 4
_RENDERED_LIMIT = 32
_LOCK = threading.RLock()


def _source_key(source):
    if isinstance(source, Path):
        info = source.stat()
        return (str(source.resolve()), info.st_mtime_ns, info.st_size)
    return (str(source), None, None)  # URL


def _remember(cache, key, value, limit):
    cache[key] = value
    while len(cache) > limit:
        cache.pop(next(iter(cache)))


def _get_rendered(source, max_width, max_height):
    columns, lines = shutil.get_terminal_size((80, 24))
    source_key = _source_key(source)
    key = (source_key, columns, lines, max_width, max_height)
    with _LOCK:
        text = _RENDERED.get(key)
        if text is not None:
            return text
        decoded = _DECODED.get(source_key)
        if decoded is None:
            decoded = _decode_png(_read_bytes(source))
            _remember(_DECODED, source_key, decoded, _DECODED_LIMIT)
        width, height, planes = decoded
        text = _render_planes(width, height, planes, max_width, max_height, columns, lines)
        _remember(_RENDERED, key, text, _RENDERED_LIMIT)
        return text


def _resolve(path, folder, quiet=False):
    """Returns a Path (or a URL string) to load, or None if it cannot be shown."""
    path = str(path)
    if path.startswith(("http://", "https://")):
        return path

    target = Path(path).expanduser()
    if folder is not None and not target.is_absolute():
        target = Path(folder).expanduser() / target
    if target.suffix.lower() != ".png":
        if not quiet:
            print(f"Only PNG files can be displayed without external libraries (received: {target.suffix or 'no extension'}).")
        return None
    if not target.exists():
        if not quiet:
            print(f"Image not found: {target.resolve()}")
        return None
    return target


def show_image(path, folder=None, max_width=None, max_height=None):
    """
    Draws a PNG image in the terminal.

    path        Path (relative or absolute) or URL of the image.
    folder      Folder to look in if 'path' is relative (optional).
    max_width   Maximum width in columns (optional).
    max_height  Maximum height in text lines (optional).
    """
    prepare_terminal()
    source = _resolve(path, folder)
    if source is None:
        return

    try:
        text = _get_rendered(source, max_width, max_height)
    except Exception as error:
        print(f"Could not read the image: {error}")
        return

    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def preload(path, folder=None, max_width=None, max_height=None, background=True):
    """
    Decodes and renders an image ahead of time so that the first show_image()
    call is instant. Use the same arguments you will pass to show_image().

    With background=True (default) the work runs in a daemon thread and this
    function returns immediately (it returns the thread). Nothing is printed
    and errors are ignored: show_image() reports them when it is called.
    """
    def work():
        try:
            source = _resolve(path, folder, quiet=True)
            if source is not None:
                _get_rendered(source, max_width, max_height)
        except Exception:
            pass

    if not background:
        work()
        return None
    thread = threading.Thread(target=work, name="imgterm-preload", daemon=True)
    thread.start()
    return thread


def main(argv=None):
    parser = argparse.ArgumentParser(description="Displays a PNG image in the terminal.")
    parser.add_argument("image", help="path or URL of the PNG")
    parser.add_argument("-f", "--folder", help="folder to look for the image in")
    parser.add_argument("-w", "--width", type=int, help="maximum width in columns")
    parser.add_argument("-H", "--height", type=int, help="maximum height in text lines")
    args = parser.parse_args(argv)
    show_image(args.image, folder=args.folder, max_width=args.width, max_height=args.height)


if __name__ == "__main__":
    main()
