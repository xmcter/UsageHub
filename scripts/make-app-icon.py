# -*- coding: utf-8 -*-
"""生成 UsageHub.app 的 macOS 图标（.icns）。

沿用 web 面板已用的品牌视觉：暖橙渐变仪表盘（弧 + 指针 + 圆点），深色 squircle 圆角方底，
与 web/index.html 的 favicon / 页头 logo 同一设计，保持三处（App/网页标题栏/浏览器标签）一致。

仅构建期用（需 Pillow，不进运行时依赖）：
    uv pip install --python .venv/bin/python pillow
    .venv/bin/python scripts/make-app-icon.py
    bash scripts/make-app.sh   # 重新打包，把新图标塞进 App bundle

产物：usagehub/assets/app_icon.icns
"""
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "usagehub" / "assets"
OUT_ICNS = ASSETS / "app_icon.icns"

BG = (11, 12, 16, 255)         # 深色底，同 web 面板 --bg-main
ORANGE_A = (200, 114, 44)      # #c8722c
ORANGE_B = (224, 168, 69)      # #e0a845
LIGHT = (243, 244, 246, 255)   # 指针/圆点


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def draw_icon(size=1024):
    """与 web favicon 同一造型：上半圆规（缺口在下）+ 指针指向右上 + 圆点。

    统一用「屏幕角度」约定：0°=3点钟方向，顺时针递增（90°=6点钟…270°=12点钟），
    坐标一律 x=cx+R*cos(θ)、y=cy+R*sin(θ)，不做任何符号翻转——避免不同坐标系混用出错。
    """
    S = 2  # 超采样再缩小，边缘更平滑
    W = size * S
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # squircle 圆角方底（macOS Big Sur 风格，半径约图标宽度的 22%）
    r = int(W * 0.224)
    d.rounded_rectangle([0, 0, W - 1, W - 1], radius=r, fill=BG)

    cx, cy = W * 0.5, W * 0.60
    R = W * 0.28
    stroke = int(W * 0.075)

    # 上半圆弧：180°(9点/左) → 360°(3点/右)，途经 270°(12点/顶)，缺口在底部，与 favicon 一致
    start_deg, end_deg = 180.0, 360.0
    steps = 48
    for i in range(steps):
        t0 = i / steps
        t1 = (i + 1) / steps
        a0 = start_deg + (end_deg - start_deg) * t0   # start<end 全程递增，避免反向画出大弧的坑
        a1 = start_deg + (end_deg - start_deg) * t1
        color = _lerp(ORANGE_A, ORANGE_B, t0) + (255,)
        d.arc([cx - R, cy - R, cx + R, cy + R], a0, a1, fill=color, width=stroke)
    # 弧两端加圆头收口
    cap = stroke / 2
    for ang, col in ((start_deg, ORANGE_A), (end_deg, ORANGE_B)):
        px = cx + R * math.cos(math.radians(ang))
        py = cy + R * math.sin(math.radians(ang))
        d.ellipse([px - cap, py - cap, px + cap, py + cap], fill=col + (255,))

    # 指针：300°（12点与3点之间，即右上方），同一约定
    needle_ang = 300.0
    nx = cx + R * 0.92 * math.cos(math.radians(needle_ang))
    ny = cy + R * 0.92 * math.sin(math.radians(needle_ang))
    d.line([cx, cy, nx, ny], fill=LIGHT, width=int(W * 0.028))
    dot_r = W * 0.034
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=LIGHT)

    return img.resize((size, size), Image.LANCZOS)


def build_icns():
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = draw_icon(1024)

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        # macOS iconset 命名约定：base size + @2x（实际都是渲染到对应像素）
        specs = [
            ("icon_16x16", 16), ("icon_16x16@2x", 32),
            ("icon_32x32", 32), ("icon_32x32@2x", 64),
            ("icon_128x128", 128), ("icon_128x128@2x", 256),
            ("icon_256x256", 256), ("icon_256x256@2x", 512),
            ("icon_512x512", 512), ("icon_512x512@2x", 1024),
        ]
        for name, px in specs:
            master.resize((px, px), Image.LANCZOS).save(iconset / "{}.png".format(name))

        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_ICNS)],
                       check=True)
    print("saved", OUT_ICNS)


if __name__ == "__main__":
    build_icns()
