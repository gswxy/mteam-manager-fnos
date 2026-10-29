"""生成 FPK 图标（64/256）：馒头造型 + 珊瑚红，圆角矩形画布。

用法: python scripts/gen_icons.py [输出目录]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

CORAL = (232, 93, 74, 255)
CREAM = (242, 231, 222, 255)
DARK = (60, 42, 38, 60)


def rounded_bg(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=CREAM)
    return img


def draw_bun(img: Image.Image) -> None:
    size = img.width
    d = ImageDraw.Draw(img)
    # 主体馒头：底部略平的圆包
    body_w = size * 0.66
    cx = size / 2
    top = size * 0.30
    bottom = size * 0.72
    body_h = bottom - top
    d.ellipse([cx - body_w / 2, top, cx + body_w / 2, bottom + body_h * 0.35], fill=CORAL)
    # 底部收平
    d.rectangle([cx - body_w / 2, bottom - size * 0.01, cx + body_w / 2,
                 bottom + body_h * 0.2], fill=CORAL)
    # 顶部蒸汽曲线（奶色）
    steam_y = size * 0.20
    lw = max(2, size // 22)
    for i, sx in enumerate((-0.16, 0.0, 0.16)):
        x = cx + sx * size
        d.arc([x - size * 0.055, steam_y - size * 0.06, x + size * 0.055, steam_y + size * 0.06],
              start=200 if i % 2 == 0 else 20, end=340 if i % 2 == 0 else 160, fill=CORAL, width=lw)
    # 高光小点
    for (px, py, r, alpha) in ((0.40, 0.52, 0.030, 70), (0.55, 0.46, 0.022, 55),
                               (0.47, 0.62, 0.018, 45)):
        d.ellipse([cx + px * size - size * r - size * 0.0, py * size - size * r,
                   cx + px * size + size * r, py * size + size * r],
                  fill=(255, 255, 255, alpha))


def make(size: int) -> Image.Image:
    img = rounded_bg(size)
    draw_bun(img)
    return img


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "fpk")
    out.mkdir(parents=True, exist_ok=True)
    img256 = make(256)
    img256.save(out / "ICON_256.PNG")
    img256.resize((64, 64), Image.LANCZOS).save(out / "ICON.PNG")
    ui_images = out / "app" / "ui" / "images"
    ui_images.mkdir(parents=True, exist_ok=True)
    img256.resize((256, 256)).save(ui_images / "icon_256.png")
    img256.resize((64, 64), Image.LANCZOS).save(ui_images / "icon_64.png")
    print(f"icons generated under {out}")


if __name__ == "__main__":
    main()
