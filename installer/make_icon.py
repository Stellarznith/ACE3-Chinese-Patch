"""把源图转换成安装器用的多尺寸 ICO（保留透明通道）。"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SIZES = (16, 24, 32, 48, 64, 128, 256)


def build(src: Path, dst: Path) -> None:
    image = Image.open(src).convert("RGBA")
    side = max(image.size)
    if image.size != (side, side):
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        image = canvas
    frames = [image.resize((size, size), Image.LANCZOS) for size in SIZES]
    frames[-1].save(dst, format="ICO", sizes=[(size, size) for size in SIZES], append_images=frames[:-1])
    print(f"{dst}  {dst.stat().st_size} bytes  sizes={SIZES}")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
