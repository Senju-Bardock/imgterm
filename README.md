# imgterm

Muestra imágenes PNG en la terminal (Windows, Linux, macOS) con **solo la biblioteca estándar de Python**. Sin dependencias.

## Opción 1: instalar directo desde GitHub (una vez)

```bash
pip install git+https://github.com/TU_USUARIO/imgterm.git
```

```python
from imgterm import show_image

show_image("foto.png")
show_image("Senju_Bardock.png", folder="visual")
show_image("foto.png", max_width=60)
show_image("https://sitio.com/imagen.png")
```

También queda disponible el comando:

```bash
imgterm foto.png --width 60
```

## Opción 2: sin instalar ni descargar nada

Pega esto al principio de tu script. Descarga el módulo en memoria desde GitHub cada vez que se ejecuta (necesita internet):

```python
import types, urllib.request

URL = "https://raw.githubusercontent.com/TU_USUARIO/imgterm/main/imgterm.py"
imgterm = types.ModuleType("imgterm")
exec(urllib.request.urlopen(URL).read().decode(), imgterm.__dict__)

imgterm.show_image("foto.png")
```

Consejo: en lugar de `main` usa una etiqueta o un commit (`.../imgterm/v0.1.0/imgterm.py`) para que el código no cambie sin que lo sepas.

## Opción 3: copiar el archivo

`imgterm.py` es un único archivo; basta con ponerlo junto a tu proyecto.

## Limitaciones

- Solo PNG, y sin entrelazado (Adam7).
- Necesita una terminal con colores de 24 bits (Windows Terminal, la mayoría de terminales de Linux/macOS).
