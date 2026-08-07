"""Draw the brand icon for home-assistant/brands.

Home Assistant does not read an integration's logo from its own repository - it
fetches it from brands.home-assistant.io, which is fed by a pull request to
home-assistant/brands. So this only generates the files; submitting them is a
separate, human step.

Needs Pillow, which is deliberately not a dependency of the integration itself
(the manifest keeps `requirements: []`):

    python -m pip install pillow
    python scripts/make_brand_icon.py
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

# mirrors the layout of home-assistant/brands, so the folder can be copied in
OUT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "brands"
    / "custom_integrations"
    / "battery_management"
)

SLATE = (55, 71, 79, 255)      # body, dark enough to read on a light background
GREEN = (67, 160, 71, 255)     # charge
SIZE = 512                     # drawn large, downscaled for the 1x versions


def draw(size: int = SIZE) -> Image.Image:
    """A battery, split into two cells - which is what this integration is for."""
    scale = size / 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def px(*values):
        return [v * scale for v in values]

    stroke = 28 * scale
    # terminal
    d.rounded_rectangle(px(200, 40, 312, 92), radius=16 * scale, fill=SLATE)
    # body
    body = px(136, 88, 376, 464)
    d.rounded_rectangle(body, radius=36 * scale, outline=SLATE, width=int(stroke))

    # inside the walls
    left, top = body[0] + stroke, body[1] + stroke
    right, bottom = body[2] - stroke, body[3] - stroke
    middle = (top + bottom) / 2
    gap = 16 * scale

    # lower cell full, upper cell part full: reads as two packs at a glance
    d.rounded_rectangle(
        [left, middle + gap / 2, right, bottom], radius=10 * scale, fill=GREEN
    )
    upper_top = top + (middle - gap / 2 - top) * 0.45
    d.rounded_rectangle(
        [left, upper_top, right, middle - gap / 2], radius=10 * scale, fill=GREEN
    )
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    large = draw()
    small = large.resize((256, 256), Image.LANCZOS)

    # icon only: with no separate wordmark, brands falls back to the icon for
    # the logo slot, and shipping an identical logo.png just invites a review
    # comment
    for name, image in (("icon.png", small), ("icon@2x.png", large)):
        path = OUT / name
        image.save(path, "PNG", optimize=True)
        print(f"{path.name:14s} {image.size[0]}x{image.size[1]}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
