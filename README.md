# imgterm

Displays PNG images in the terminal (Windows, Linux, macOS) using only the Python standard library. No dependencies.

## Option 1: Install directly from GitHub (one-time)

```bash
pip install git+https://github.com/Senju-Bardock/imgterm.git
```

```python
from imgterm import show_image

show_image("foto.png")
show_image("Senju_Bardock.png", folder="visual")
show_image("foto.png", max_width=60)
show_image("https://sitio.com/imagen.png")
```

The following command is also available:

```bash
imgterm foto.png --width 60
```

## Option 2: without installing or downloading anything

Paste this at the beginning of your script. It downloads the module into memory from GitHub each time it runs (requires an internet connection):

```python
import types, urllib.request

URL = "https://raw.githubusercontent.com/Senju-Bardock/imgterm/main/imgterm.py"
imgterm = types.ModuleType("imgterm")
exec(urllib.request.urlopen(URL).read().decode(), imgterm.__dict__)

imgterm.show_image("foto.png")
```

Tip: Instead of `main`, use a tag or a commit (`.../imgterm/v0.1.0/imgterm.py`) so the code doesn't change without you knowing.

## Option 3: Copy the file

`imgterm.py` is a single file; you simply need to place it alongside your project.

## Limitations

- PNG only, and without interlacing (Adam7).
- Requires a terminal with 24-bit color support (Windows Terminal, most Linux/macOS terminals).
