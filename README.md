# imgterm

Display PNG images in the terminal (Windows, Linux, macOS) using **only the Python standard library**. No dependencies.

## Option 1: install straight from GitHub (once)

```bash
pip install git+https://github.com/Senju-Bardock/imgterm.git
```

```python
from imgterm import show_image

show_image("photo.png")
show_image("Senju_Bardock.png", folder="visual")
show_image("photo.png", max_width=60)
show_image("https://example.com/image.png")
```

The `imgterm` command is also available:

```bash
imgterm photo.png --width 60
```

## Option 2: no install, no download

Paste this at the top of your script. It loads the module into memory from GitHub every time it runs (needs internet):

```python
import types, urllib.request

URL = "https://raw.githubusercontent.com/Senju-Bardock/imgterm/main/imgterm.py"
imgterm = types.ModuleType("imgterm")
exec(urllib.request.urlopen(URL).read().decode(), imgterm.__dict__)

imgterm.show_image("photo.png")
```

Tip: use a tag or a commit instead of `main` (`.../imgterm/v0.1.0/imgterm.py`) so the code never changes without you knowing.

## Option 3: copy the file

`imgterm.py` is a single file; just drop it next to your project.

## Speed: caching and preload

Decoding a PNG in pure Python takes a moment (roughly proportional to the number of pixels). `imgterm` keeps every rendered image in memory, so **showing the same image again is practically instant** (about 0.2 ms, identical output). The first time can be hidden by decoding in the background at startup:

```python
from imgterm import show_image, preload

preload("Senju_Bardock.png", folder="visual", max_width=40)  # returns immediately

# ...later, inside a menu that redraws on every key press:
show_image("Senju_Bardock.png", folder="visual", max_width=40)  # instant
```

Use the same `folder`, `max_width`, `max_height` (and `mode`, if you set it) in both calls. If `show_image` is called while the preload is still running, it simply waits for it instead of repeating the work. The cache is refreshed automatically if the file changes or the terminal is resized. The decoder and the resizer are also about 1.5-2x faster than in 0.2.0, with pixel-identical output. Tip: images much bigger than what is shown (e.g. 2000 px wide for a 40-column display) only slow down the first decode; a smaller PNG looks the same.

## Example

See [`example.py`](example.py) for a runnable demo:

```bash
python example.py
python example.py path/to/your_image.png
```

## API

| Function | Description |
| --- | --- |
| `show_image(path, folder=None, max_width=None, max_height=None)` | Draws a PNG (path or URL) in the terminal |
| `preload(path, folder=None, max_width=None, max_height=None, mode="auto", threaded=True)` | Decodes and renders ahead of time (in a background thread) so the first `show_image` is instant |
| `read_png(source)` | Decodes a PNG and returns `(width, height, rows)` |
| `render(width, height, rows, max_width=None, max_height=None)` | Returns the ANSI string, ready to print |

## Limitations

- PNG only (all color types, 1-16 bit, tRNS and Adam7 interlacing are supported).
- The `blocks` fallback needs a terminal with 24-bit color (set `IMGTERM_COLORS=256` otherwise).
