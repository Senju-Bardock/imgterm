#!/usr/bin/env python3
"""
Build the C engine of imgterm by hand:

    python build_native.py              # uses cc / gcc / clang (or the CC variable)

The result is imgterm_native/imgterm_core.so (.dll on Windows, .dylib on macOS), the
place where imgterm.py looks for it. You normally do NOT need this: imgterm also builds
the engine by itself the first time it is used when it finds a C compiler (the result is
cached), and `pip install` builds it too.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import imgterm  # noqa: E402


def main():
    source = HERE / "imgterm_native" / "imgterm_core.c"
    target = HERE / "imgterm_native" / imgterm._lib_name()
    print("compiling %s ..." % source.name)
    if not imgterm._compile_native(source, target):
        print("FAILED: no working C compiler found (tried $CC, cc, gcc, clang).")
        print("imgterm keeps working in pure Python, only slower.")
        return 1
    print("built %s" % target)
    imgterm._NATIVE = False  # forget a previous failed attempt in this process
    print("native engine active:", imgterm.native_available(), "(%s)" % imgterm._NATIVE_WHY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
