"""
imgterm.py
Displays a PNG image in the terminal (Windows, Linux, macOS) using
ONLY the Python standard library: no pip install needed.

Usage as a module:
    from imgterm import show_image
    show_image("photo.png")                          # relative or absolute path
    show_image("Senju_Bardock.png", folder="visual") # look inside a folder
    show_image("https://example.com/image.png")      # URLs work too
    show_image("photo.png", max_width=60)            # limit width (in columns)

Usage from the terminal:
    python imgterm.py photo.png
    python imgterm.py photo.png --folder visual --width 60
"""

import argparse
import os
import shutil
import struct
import sys
import urllib.request
import zlib
from pathlib import Path

__all__ = ["show_image", "read_png", "render"]


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
# 2. PNG decoder (only zlib + struct, both from the standard library)
# ---------------------------------------------------------------------------
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


def _decode_png(data):
    """Returns (width, height, rows) where rows = list of lists of (r, g, b)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
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

    rows = []
    previous = bytearray(stride)
    p = 0
    for _ in range(height):
        filter_type = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride

        if filter_type == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                line[i] = (line[i] + pr) & 255

        rows.append(_row_to_rgb(line, width, channels, depth, color, palette))
        previous = line

    return width, height, rows


def _row_to_rgb(line, width, channels, depth, color, palette):
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


def read_png(source):
    """Reads a PNG from a path or a URL. Returns (width, height, rows)."""
    source = str(source)
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=15) as response:
            data = response.read()
    else:
        data = Path(source).read_bytes()
    return _decode_png(data)


# ---------------------------------------------------------------------------
# 3. Resize and draw
# ---------------------------------------------------------------------------
def _resize(rows, width, height, new_width, new_height):
    """Averages the pixels of each area (looks better than nearest neighbor)."""
    result = []
    for ty in range(new_height):
        y0 = ty * height // new_height
        y1 = max(y0 + 1, (ty + 1) * height // new_height)
        new_row = []
        for tx in range(new_width):
            x0 = tx * width // new_width
            x1 = max(x0 + 1, (tx + 1) * width // new_width)
            r = g = b = n = 0
            for y in range(y0, y1):
                row = rows[y]
                for x in range(x0, x1):
                    pr, pg, pb = row[x]
                    r += pr
                    g += pg
                    b += pb
                    n += 1
            new_row.append((r // n, g // n, b // n))
        result.append(new_row)
    return result


def render(width, height, rows, max_width=None, max_height=None):
    """Turns pixels into a string of ANSI codes, ready to print."""
    columns, lines = shutil.get_terminal_size((80, 24))
    max_w = min(columns, max_width or columns)
    max_h = max(2, (max_height or (lines - 2)) * 2)  # each text line = 2 pixels

    scale = min(max_w / width, max_h / height)
    new_width = max(1, int(width * scale))
    new_height = max(2, int(height * scale))
    new_height -= new_height % 2  # must be even

    img = _resize(rows, width, height, new_width, new_height)

    out = []
    for y in range(0, new_height, 2):
        parts = []
        for x in range(new_width):
            r1, g1, b1 = img[y][x]
            r2, g2, b2 = img[y + 1][x]
            # "▀" = upper half (text color) + lower half (background color)
            parts.append(f"\x1b[38;2;{r1};{g1};{b1}m\x1b[48;2;{r2};{g2};{b2}m▀")
        out.append("".join(parts) + "\x1b[0m")
    return "\n".join(out)


def show_image(path, folder=None, max_width=None, max_height=None):
    """
    Draws a PNG image in the terminal.

    path        Path (relative or absolute) or URL of the image.
    folder      Folder to look in if 'path' is relative (optional).
    max_width   Maximum width in columns (optional).
    max_height  Maximum height in text lines (optional).
    """
    prepare_terminal()
    path = str(path)
    is_url = path.startswith(("http://", "https://"))

    if not is_url:
        target = Path(path).expanduser()
        if folder is not None and not target.is_absolute():
            target = Path(folder).expanduser() / target
        if target.suffix.lower() != ".png":
            print(f"Only PNG files can be displayed without external libraries (received: {target.suffix or 'no extension'}).")
            return
        if not target.exists():
            print(f"Image not found: {target.resolve()}")
            return
        path = target

    try:
        width, height, rows = read_png(path)
    except Exception as error:
        print(f"Could not read the image: {error}")
        return

    print(render(width, height, rows, max_width, max_height))


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
