"""Generate the Windows icon and app image from logo.jpeg.

Run after replacing the logo:

    .venv\\Scripts\\python tools\\make_icon.py

The source artwork is landscape, but icons must be square. It is padded with
the colour already used for its border rather than cropped, which would cut off
the lettering.
"""

from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "logo.jpeg"
ASSETS = ROOT / "bot_kalung" / "assets"
ICON = ASSETS / "icon.ico"
PNG = ASSETS / "logo.png"

# Windows picks the closest size; supplying all of them avoids blurry scaling
# in the taskbar, alt-tab and Explorer's large-icon view.
SIZES = [16, 24, 32, 48, 64, 128, 256]


def border_colour(image: Image.Image) -> tuple[int, int, int]:
    """The artwork's background colour, sampled just inside its outline.

    Sampling the outermost pixels picks up the thin dark keyline instead of the
    field behind the illustration, which makes the padding read as a visible
    band. An inset of a few percent lands inside the background proper.
    """
    width, height = image.size
    inset_x = max(2, width // 25)
    inset_y = max(2, height // 25)
    pixels = []
    for x in range(inset_x, width - inset_x, max(1, width // 80)):
        pixels.append(image.getpixel((x, inset_y)))
        pixels.append(image.getpixel((x, height - 1 - inset_y)))
    for y in range(inset_y, height - inset_y, max(1, height // 80)):
        pixels.append(image.getpixel((inset_x, y)))
        pixels.append(image.getpixel((width - 1 - inset_x, y)))
    return Counter(pixels).most_common(1)[0][0]


def main() -> int:
    if not SOURCE.is_file():
        print(f"missing {SOURCE}")
        return 1

    ASSETS.mkdir(parents=True, exist_ok=True)
    image = Image.open(SOURCE).convert("RGB")
    fill = border_colour(image)

    side = max(image.size)
    square = Image.new("RGB", (side, side), fill)
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    square = square.convert("RGBA")

    square.resize((256, 256), Image.LANCZOS).save(PNG)
    square.save(ICON, format="ICO",
                sizes=[(size, size) for size in SIZES])

    print(f"source      : {image.size[0]}x{image.size[1]}")
    print(f"pad colour  : #{fill[0]:02x}{fill[1]:02x}{fill[2]:02x}")
    print(f"icon        : {ICON.relative_to(ROOT)}  "
          f"({ICON.stat().st_size // 1024} KB, sizes {SIZES})")
    print(f"png         : {PNG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
