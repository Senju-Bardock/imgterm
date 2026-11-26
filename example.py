"""
example.py
Shows how to use imgterm (https://github.com/Senju-Bardock/imgterm).

Run it with:
    python example.py                  -> demo with generated images (no files needed)
    python example.py path/to/img.png  -> also shows your own PNG
"""

import struct
import sys
import tempfile
import zlib
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Getting imgterm
#    - If it is installed (pip install git+https://github.com/Senju-Bardock/imgterm.git)
#      or sits next to this file, a normal import is used.
#    - Otherwise it is loaded straight from GitHub into memory: nothing to download.
# ---------------------------------------------------------------------------
try:
    import imgterm
except ImportError:
    import types
    import urllib.request

    URL = "https://raw.githubusercontent.com/Senju-Bardock/imgterm/main/imgterm.py"
    imgterm = types.ModuleType("imgterm")
    exec(urllib.request.urlopen(URL, timeout=15).read().decode(), imgterm.__dict__)


# ---------------------------------------------------------------------------
# 2. Helper: build a small PNG so the demo works without any image file
# ---------------------------------------------------------------------------
def make_gradient_png(path, width=160, height=90):
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0 (None)
        for x in range(width):
            rows += bytes((x * 255 // width, y * 255 // height, 180))

    def chunk(kind, body):
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows)))
        + chunk(b"IEND", b"")
    )
    Path(path).write_bytes(png)


def main():
    demo_folder = Path(tempfile.mkdtemp(prefix="imgterm_demo_"))
    make_gradient_png(demo_folder / "gradient.png")

    # --- A) Simplest use: a path ------------------------------------------
    print("\nA) Absolute path:")
    imgterm.show_image(demo_folder / "gradient.png")

    # --- B) Relative name + folder where to look for it -------------------
    print("\nB) Name + folder:")
    imgterm.show_image("gradient.png", folder=demo_folder)

    # --- C) Limiting the size (width in columns, height in text lines) ----
    print("\nC) Small version (max_width=30):")
    imgterm.show_image("gradient.png", folder=demo_folder, max_width=30)

    # --- D) Low-level API: decode once, render as a string ----------------
    print("\nD) read_png + render (you get a string you can print or reuse):")
    width, height, rows = imgterm.read_png(demo_folder / "gradient.png")
    print(f"   image size: {width}x{height}")
    print(imgterm.render(width, height, rows, max_width=40))

    # --- E) Your own image, passed as a command line argument -------------
    if len(sys.argv) > 1:
        print(f"\nE) Your image: {sys.argv[1]}")
        imgterm.show_image(sys.argv[1])

    # --- F) URLs also work (uncomment and use a real PNG link) ------------
    # imgterm.show_image("https://example.com/some_image.png", max_width=60)


if __name__ == "__main__":
    main()
