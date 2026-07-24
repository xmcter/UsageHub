# -*- coding: utf-8 -*-
"""生成状态栏「环形进度」单色模板图标（整套 0%~100%，每 5% 一张）。

仅构建期用（需 Pillow，不进运行时依赖）。产物随仓库提交，改样式才需重跑：
    uv pip install --python .venv/bin/python pillow
    .venv/bin/python scripts/make-menubar-icon.py

每张：浅色整圈轨道 + 一段实心弧（弧长 = 该百分比），透明底、圆头收口。
menu_bar.py 运行时按「当前最吃紧额度的已用%」选最近的一张切换（template=True，
macOS 自动跟随亮/暗色反白：弧实、轨道淡）。
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "usagehub" / "assets"


def draw_ring(pct):
    S = 8  # 高倍渲染再缩，圆弧更顺滑
    W = H = 44 * S
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx = cy = 22 * S
    R = 15 * S                 # 环半径（到描边中线）
    t = 3.5 * S                # 描边粗细：细一点更利落
    bbox = [cx - R, cy - R, cx + R, cy + R]

    # 轨道：整圈，淡
    d.arc(bbox, 0, 360, fill=(0, 0, 0, 70), width=int(t))

    p = max(0.0, min(100.0, pct)) / 100.0
    if p > 0:
        # 进度弧：从 12 点顺时针（PIL 角度从 3 点起、顺时针增）
        start = -90.0
        end = -90.0 + 360.0 * p
        d.arc(bbox, start, end, fill=(0, 0, 0, 255), width=int(t))
        # 弧端圆头收口
        cap = t / 2.0
        ends = [start] if p >= 1.0 else [start, end]
        for ang in ends:
            px = cx + R * math.cos(math.radians(ang))
            py = cy + R * math.sin(math.radians(ang))
            d.ellipse([px - cap, py - cap, px + cap, py + cap], fill=(0, 0, 0, 255))

    return img.resize((44, 44), Image.LANCZOS)


def bar_color(pct):
    """按用量给进度条上色：充裕=绿，偏紧=黄，快用完=红（与网页面板阈值一致）。"""
    if pct > 85:
        return (239, 68, 68, 255)    # red
    if pct > 65:
        return (245, 158, 11, 255)   # amber
    return (16, 185, 129, 255)       # green


def draw_bar(pct):
    """横向圆角进度条：中性轨道 + 按用量变色的填充（宽度=百分比）。彩色图，不用 template。"""
    S = 8
    W, H = 46 * S, 12 * S
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = H // 2
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=(140, 140, 140, 70))  # 中性轨道
    p = max(0.0, min(100.0, pct)) / 100.0
    if p > 0:
        w = max(H, int(round((W - 1) * p)))   # 至少一个圆头，极小值也可见
        d.rounded_rectangle([0, 0, w, H - 1], radius=r, fill=bar_color(pct))
    return img.resize((46, 12), Image.LANCZOS)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    for pct in range(0, 101, 5):
        draw_ring(pct).save(ASSETS / "ring_{:03d}.png".format(pct))
        draw_bar(pct).save(ASSETS / "bar_{:03d}.png".format(pct))
    # 兜底/初始状态栏图标（无数据时用）：空轨道环
    draw_ring(0).save(ASSETS / "menubar_icon.png")
    print("saved ring_/bar_ 000..100 (+menubar_icon.png) ->", ASSETS)


if __name__ == "__main__":
    main()
