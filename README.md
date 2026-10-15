# imgterm

Display PNG, JPEG and animated GIF images in the terminal (Windows, Linux, macOS) using **only the Python standard library**. No dependencies.

## Installation

There are three ways to use `imgterm`. Pick the one that fits you:

| | Option 1: `pip install` | Option 2: no install | Option 3: copy the file |
| --- | --- | --- | --- |
| **Best for** | Using it in many projects, or from the command line | Quick scripts you want to share as one file | No internet, no pip, or no Python at all |
| **Needs Python** | Yes (3.8+) | Yes (3.8+) | `imgterm.exe`: no. `imgterm.py`: yes |
| **Needs internet** | Only once, to install | Every time the script runs | Only once, to download |
| **Adds the `imgterm` command** | Yes | No | `imgterm.exe` is the command itself |

No matter which option you choose, `imgterm` has **no dependencies**: only the Python standard library.

### Option 1: install with pip (recommended)

Installs it once, then you can `import imgterm` from any script and use the `imgterm` command in any terminal.

```bash
pip install git+https://github.com/Senju-Bardock/imgterm.git
```

Use it from Python:

```python
from imgterm import show_image

show_image("photo.png")
show_image("photo.jpg")  # JPEG works the same way (.jpg and .jpeg)
show_image("dance.gif")  # GIFs are animated in place (Ctrl+C stops them)
show_image("dance.gif", loops=2)  # play it twice (0 = forever)
show_image("Senju_Bardock.png", folder="visual")
show_image("photo.png", max_width=60)
show_image("https://example.com/image.png")
```

Or from the terminal:

```bash
imgterm photo.png --width 60
imgterm dance.gif --loops 3
```

To update it later: `pip install --upgrade git+https://github.com/Senju-Bardock/imgterm.git`

> If `pip` is not found, try `python -m pip ...` (Windows) or `python3 -m pip ...` (Linux/macOS).

### Option 2: no install, no download

Nothing is installed on your computer. Paste this at the top of your script and it loads the module into memory from GitHub every time it runs (**needs internet**):

```python
import types, urllib.request

URL = "https://raw.githubusercontent.com/Senju-Bardock/imgterm/main/imgterm.py"
imgterm = types.ModuleType("imgterm")
exec(urllib.request.urlopen(URL).read().decode(), imgterm.__dict__)

imgterm.show_image("photo.png")
```

Tip: use a tag or a commit instead of `main` (`.../imgterm/v0.1.0/imgterm.py`) so the code never changes without you knowing.

### Option 3: copy the file

Download one file, put it where you want it, done. Choose the version you need:

**Windows, no Python required: `imgterm.exe`**

1. Download [`imgterm.exe`](https://github.com/Senju-Bardock/imgterm/raw/main/imgterm.exe).
2. Open a terminal in the folder where you saved it and run:

```bat
imgterm.exe photo.png --width 60
```

Tip: put `imgterm.exe` in a folder that is in your `PATH` and you can run `imgterm photo.png` from anywhere.

**Any system, with Python: `imgterm.py`**

1. Download [`imgterm.py`](https://raw.githubusercontent.com/Senju-Bardock/imgterm/main/imgterm.py) and drop it next to your script (same folder).
2. Import it as usual:

```python
from imgterm import show_image

show_image("photo.png")
```

You can also run it directly: `python imgterm.py photo.png --width 60`

## Animated GIFs

`show_image("dance.gif")` plays the animation in place, with the GIF's own frame timing, on every renderer (sixel, kitty, iterm2 and blocks). `loops=None` (default) follows the file: no loop extension = once, "forever" = until you press **Ctrl+C** (the cursor is left right below the picture). Use `loops=1`, `2`... to play a fixed number of times, or `loops=0` to force forever. A GIF with a single frame is drawn like a PNG, and when the output is not a terminal (redirected to a file or pipe) only the first frame is printed.

Since 0.5.0 a GIF is smooth **from the first frame to the last**. A background thread decodes and renders the frames while the main thread only plays what is ready. Before playing, `imgterm` reads the whole timeline of the GIF (a few milliseconds, no pixels decoded), measures how fast frames are being rendered and starts as soon as playback can no longer catch up with the renderer. If your machine renders faster than the GIF plays, it starts immediately; if not, it shows `buffering N%` for the shortest time that guarantees no stutter. Every later loop is a lookup in memory. `show_image("dance.gif", smooth=False)` (or `--no-smooth`) starts at once and may stutter on the first pass, like 0.4.0 did.

`preload("dance.gif", max_width=40)` renders all the frames in the background, so even the buffering wait disappears. The waiting time of a heavy GIF is dominated by resizing, so a GIF much bigger than what is shown (or a smaller `max_width`) is the best way to shorten it.

## Speed: caching and preload

Decoding a PNG in pure Python takes a moment (roughly proportional to the number of pixels). `imgterm` keeps every rendered image in memory, so **showing the same image again is practically instant** (about 0.2 ms, identical output). The first time can be hidden by decoding in the background at startup:

```python
from imgterm import show_image, preload

preload("Senju_Bardock.png", folder="visual", max_width=40)  # returns immediately

# ...later, inside a menu that redraws on every key press:
show_image("Senju_Bardock.png", folder="visual", max_width=40)  # instant
```

Use the same `folder`, `max_width`, `max_height` (and `mode`, if you set it) in both calls. If `show_image` is called while the preload is still running, it simply waits for it instead of repeating the work. The cache is refreshed automatically if the file changes or the terminal is resized. The decoder and the resizer are also about 1.5-2x faster than in 0.2.0, with pixel-identical output. Tip: images much bigger than what is shown (e.g. 2000 px wide for a 40-column display) only slow down the first decode; a smaller PNG looks the same. Big JPEGs are already helped: when the picture is much larger than the area it will fill, `imgterm` decodes it at 1/2, 1/4 or 1/8 of its size (never below what is needed), roughly halving the time of a large photo.

## Example

See [`example.py`](example.py) for a runnable demo:

```bash
python example.py
python example.py path/to/your_image.png
```

## API

| Function | Description |
| --- | --- |
| `show_image(path, folder=None, max_width=None, max_height=None, mode="auto", background=(0, 0, 0), cell=None, loops=None, smooth=True)` | Draws a PNG, JPEG or GIF (path or URL) in the terminal; GIFs are animated (`smooth=True` buffers just enough to never stutter) |
| `preload(path, folder=None, max_width=None, max_height=None, mode="auto", threaded=True)` | Decodes and renders ahead of time (in a background thread) so the first `show_image` is instant |
| `read_image(source, background=(0, 0, 0), box=None)` | Decodes a PNG, JPEG or GIF (detected from the file contents) and returns `(width, height, rows)` (first frame for a GIF) |
| `read_gif(source, background=(0, 0, 0))` | Decodes every frame of a GIF: `(width, height, frames, loops)` with `frames = [(rows, delay_seconds), ...]` (each one is the complete picture) |
| `read_png(source)` | Same as `read_image` (kept for backwards compatibility) |
| `render(width, height, rows, max_width=None, max_height=None)` | Returns the ANSI string, ready to print |

## Limitations

- PNG: all color types, 1-16 bit, tRNS and Adam7 interlacing are supported.
- JPEG: baseline and progressive, grayscale and color, any chroma subsampling, restart markers. Not supported: arithmetic coding, 12-bit and CMYK/YCCK files. The EXIF orientation tag is ignored (a portrait photo from a phone may be drawn sideways).
- GIF: GIF87a and GIF89a, interlaced or not, local/global palettes, transparency, all disposal methods, animation and loop count. Transparent pixels are blended with `background` (black by default). Frame delays of 0 or 1 centisecond are played at 100 ms, like browsers do.
- Other formats (WebP, BMP, APNG...) are not supported.
- The `blocks` fallback needs a terminal with 24-bit color (set `IMGTERM_COLORS=256` otherwise).
