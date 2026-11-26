"""
Builds the optional native engine (imgterm_native/imgterm_core.c) while installing.

It is compiled as a PLAIN shared library (imgterm loads it with ctypes), not as a Python
extension. If there is no C compiler, or the build fails, the installation still succeeds
and imgterm simply runs in pure Python (same output, slower).
"""
import os
import sys

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


class BuildPlainLibrary(build_ext):
    def get_export_symbols(self, ext):
        return ext.export_symbols  # no PyInit_xxx: this is not a Python extension

    def get_ext_filename(self, fullname):
        suffix = ".dll" if sys.platform == "win32" else ".dylib" if sys.platform == "darwin" else ".so"
        return os.path.join(*fullname.split(".")) + suffix


msvc = sys.platform == "win32" and not os.environ.get("CC", "").endswith("gcc")
native = Extension(
    "imgterm_native.imgterm_core",
    sources=["imgterm_native/imgterm_core.c"],
    # no -ffast-math and no fused multiply-add: the output must be identical to pure Python
    extra_compile_args=["/O2", "/fp:strict"] if msvc else ["-O3", "-ffp-contract=off", "-std=c99"],
    libraries=[] if sys.platform == "win32" else ["m"],
    optional=True,
)

setup(ext_modules=[native], cmdclass={"build_ext": BuildPlainLibrary})
