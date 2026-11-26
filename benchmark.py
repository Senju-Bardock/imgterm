#!/usr/bin/env python3
"""
Compare the C engine with pure Python on your own images:

    python benchmark.py photo.png dance.gif photo.jpg
"""
import importlib.util
import sys
import time
from pathlib import Path


def load(native):
    spec = importlib.util.spec_from_file_location("imgterm_%s" % ("c" if native else "py"),
                                                  Path(__file__).with_name("imgterm.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not native:
        module._NATIVE = None
        module._NATIVE_WHY = "benchmark: forced pure Python"
    return module


def run(module, path, mode, size=(120, 40), cell=(9, 18)):
    data = Path(path).read_bytes()
    began = time.perf_counter()
    if data[:6] in (b"GIF87a", b"GIF89a"):
        anim = module._Animation(data, (0, 0, 0), None, None, mode, cell, size)
        anim.load_all()
    else:
        box = module._pixel_box(None, None, cell, size)
        width, height, rows = module._decode_image(data, (0, 0, 0), box, flat=True)
        module.render(width, height, rows, None, None, mode, cell, _term_size=size)
    return time.perf_counter() - began


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    fast, slow = load(True), load(False)
    if not fast.native_available():
        sys.exit("The C engine is not available: " + fast._NATIVE_WHY)
    print("%-28s %-7s %12s %12s %8s" % ("image", "mode", "python", "C engine", "speedup"))
    for path in sys.argv[1:]:
        for mode in ("blocks", "sixel", "kitty"):
            run(fast, path, mode)  # warm-up (loads the library)
            t_c = min(run(fast, path, mode) for _ in range(3))
            t_py = run(slow, path, mode)
            print("%-28s %-7s %9.1f ms %9.1f ms %7.0fx" % (Path(path).name[:28], mode, t_py * 1000, t_c * 1000, t_py / t_c))


if __name__ == "__main__":
    main()
