"""stdin ("-"), bytes input and --version (stdlib only):  python -m unittest discover -s tests"""
import contextlib
import io
import struct
import subprocess
import sys
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import imgterm  # noqa: E402

JPEG = (HERE / "fixtures" / "corners.jpg").read_bytes()
GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
       b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


def make_png(w=20, h=10):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\x00" + b"\x10\x80\xf0" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def run_cli(*args, stdin=b""):
    done = subprocess.run([sys.executable, str(ROOT / "imgterm.py"), *args], input=stdin,
                          capture_output=True, timeout=60, cwd=str(HERE))
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


class Stdin(unittest.TestCase):
    def test_each_format_through_a_pipe(self):
        for name, data in (("png", make_png()), ("jpeg", JPEG), ("gif", GIF)):
            code, out, err = run_cli("-", "--mode", "blocks", "-w", "20", stdin=data)
            self.assertEqual(code, 0, (name, err))
            self.assertIn("\x1b[", out, name)
            self.assertNotIn("could not be read", out, name)

    def test_same_output_as_the_file(self):
        path = HERE / "fixtures" / "corners.jpg"
        piped = run_cli("-", "--mode", "blocks", "-w", "20", stdin=JPEG)[1]
        direct = run_cli(str(path), "--mode", "blocks", "-w", "20")[1]
        self.assertEqual(piped, direct)

    def test_empty_and_garbage_input(self):
        out = run_cli("-", stdin=b"")[1]
        self.assertIn("empty input", out)
        out = run_cli("-", "--mode", "blocks", stdin=b"this is not an image")[1]
        self.assertIn("could not be read", out)


@unittest.skipUnless(sys.platform != "win32", "needs a pseudo-terminal (Linux/macOS)")
class TerminalDetectionWithPipe(unittest.TestCase):
    def test_sixel_and_cell_size_are_detected_when_stdin_is_a_pipe(self):
        import os
        import pty
        import select
        import time

        r, w = os.pipe()
        pid, master = pty.fork()
        if pid == 0:  # child: stdout is the terminal, stdin is the pipe
            try:
                os.dup2(r, 0)
                os.close(w)
                env = {k: v for k, v in os.environ.items() if k not in ("TERM_PROGRAM", "KITTY_WINDOW_ID")}
                env.update(TERM="xterm", IMGTERM_CELL="")
                os.execve(sys.executable, [sys.executable, str(ROOT / "imgterm.py"), "-", "-w", "20"], env)
            finally:
                os._exit(1)
        os.close(r)
        os.write(w, JPEG)
        os.close(w)
        out, answered, end = b"", False, time.time() + 15
        while time.time() < end:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                if b"\x1b[16t" in out and not answered:  # play the terminal: 10x20 px cells, sixel support
                    os.write(master, b"\x1b[6;20;10t\x1b[?64;1;4c")
                    answered = True
            elif os.waitpid(pid, os.WNOHANG)[0]:
                break
        os.close(master)
        self.assertTrue(answered, "imgterm never asked the terminal")
        self.assertIn(b"\x1bP0;1;0q\"1;1;200;", out)  # sixel, 20 columns x 10 px = 200 px wide


class PythonApi(unittest.TestCase):
    def test_show_image_accepts_bytes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            imgterm.show_image(make_png(), mode="blocks", max_width=10)
        self.assertIn("\x1b[", buf.getvalue())

    def test_read_image_accepts_bytes(self):
        w, h, rows = imgterm.read_image(make_png(7, 3))
        self.assertEqual((w, h, len(rows)), (7, 3, 3))


class Version(unittest.TestCase):
    def test_version_flag(self):
        for flag in ("--version", "-V"):
            code, out, _ = run_cli(flag)
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "imgterm " + imgterm.__version__)


if __name__ == "__main__":
    unittest.main()
