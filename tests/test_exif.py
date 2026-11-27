"""EXIF orientation of JPEG files (stdlib only):  python -m unittest discover -s tests"""
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import imgterm  # noqa: E402

BASE = (HERE / "fixtures" / "corners.jpg").read_bytes()  # 48x32: R G / B Y quadrants, no EXIF
COLORS = {"R": (255, 0, 0), "G": (0, 200, 0), "B": (0, 0, 255), "Y": (255, 255, 0)}
# orientation -> (width, height, top-left, top-right, bottom-left, bottom-right) once upright
EXPECTED = {
    1: (48, 32, "R", "G", "B", "Y"), 2: (48, 32, "G", "R", "Y", "B"),
    3: (48, 32, "Y", "B", "G", "R"), 4: (48, 32, "B", "Y", "R", "G"),
    5: (32, 48, "R", "B", "G", "Y"), 6: (32, 48, "B", "R", "Y", "G"),
    7: (32, 48, "Y", "G", "B", "R"), 8: (32, 48, "G", "Y", "R", "B"),
}


def with_exif(orientation, order="II", extra_tag=False):
    """The base JPEG with an APP1/EXIF segment holding the orientation tag."""
    e = "<" if order == "II" else ">"
    entries = [(0x0112, 3, 1, orientation)]
    if extra_tag:  # another tag before it, like real cameras (Make = ASCII, offset-valued)
        entries.insert(0, (0x010F, 2, 4, 0))
    ifd = struct.pack(e + "H", len(entries))
    for tag, typ, count, value in entries:
        ifd += struct.pack(e + "HHI", tag, typ, count) + (struct.pack(e + "H", value) + b"\0\0"
                                                          if typ == 3 else struct.pack(e + "I", value))
    tiff = order.encode() + struct.pack(e + "HI", 42, 8) + ifd + struct.pack(e + "I", 0)
    body = b"Exif\0\0" + tiff
    return BASE[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + BASE[2:]


def corner(pic, w, h, x, y):
    if isinstance(pic, list):
        return pic[y][x]
    i = (y * w + x) * 3
    return tuple(pic[i:i + 3])


def name_of(rgb):
    return min(COLORS, key=lambda k: sum((a - b) ** 2 for a, b in zip(COLORS[k], rgb)))


class Orientation(unittest.TestCase):
    def check(self, data, o, flat, box=None):
        w, h, pic = imgterm._decode_image(data, box=box, flat=flat)
        ew, eh, *corners = EXPECTED[o]
        if box is None:
            self.assertEqual((w, h), (ew, eh), "orientation %d" % o)
        pts = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)]
        got = [name_of(corner(pic, w, h, x, y)) for x, y in pts]
        self.assertEqual(got, corners, "orientation %d flat=%s" % (o, flat))
        return w, h

    def run_all(self):
        for flat in (False, True):
            for o in range(1, 9):
                for order in ("II", "MM"):
                    self.check(with_exif(o, order, extra_tag=(o % 2 == 0)), o, flat)

    def test_native_engine(self):
        if not imgterm.native_available():
            self.skipTest("C engine not available: " + imgterm._NATIVE_WHY)
        self.run_all()

    def test_pure_python(self):
        saved = imgterm._NATIVE
        imgterm._NATIVE = None  # force pure Python
        try:
            self.run_all()
        finally:
            imgterm._NATIVE = saved

    def test_no_exif_and_bad_exif_are_upright(self):
        self.assertEqual(imgterm._jpeg_orientation(BASE), 1)
        self.assertEqual(imgterm._jpeg_orientation(with_exif(9)), 1)   # out of range
        self.assertEqual(imgterm._jpeg_orientation(with_exif(6)[:30]), 1)  # truncated
        self.check(BASE, 1, False)

    def test_reduced_decode_swaps_the_box(self):
        # a portrait target box for a photo turned a quarter: the decoder must receive it swapped
        w, h = self.check(with_exif(6), 6, False, box=(16, 24))
        self.assertEqual((w, h), (16, 24))


if __name__ == "__main__":
    unittest.main()
