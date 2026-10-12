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
| `read_png(source)` | Decodes a PNG and returns `(width, height, rows)` |
| `render(width, height, rows, max_width=None, max_height=None)` | Returns the ANSI string, ready to print |

## Limitations

- PNG only, and not interlaced (Adam7).
- Needs a terminal with 24-bit color (Windows Terminal and most Linux/macOS terminals).
