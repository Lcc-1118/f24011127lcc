# -*- coding: utf-8 -*-
"""Pillow 图表/流程图生成器：不依赖 matplotlib，统一 2 倍超采样后缩放，保证中文清晰。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

NAVY = "#1F3B6B"
BLUE = "#2E6DA4"
TEAL = "#2A9D8F"
AMBER = "#E9A23B"
RED = "#C0504D"
GREY = "#6E6E6E"
LGREY = "#9AA5B1"
LIGHT = "#F3F6FA"
LIGHT_BLUE = "#E8F0FA"
LINE = "#D5DEE9"
WHITE = "#FFFFFF"
PALETTE = [NAVY, BLUE, TEAL, AMBER, RED, "#7C8FA6"]


def _f(size: int, bold: bool = False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def _text(draw, xy, text, size=22, fill="#222222", bold=False, anchor="la"):
    draw.text(xy, text, font=_f(size, bold), fill=fill, anchor=anchor)


def _wrap(draw, text, font, max_width):
    lines, cur = [], ""
    for ch in text:
        test = cur + ch
        if draw.textlength(test, font=font) > max_width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _canvas(w, h, title, subtitle="", source=""):
    s = 2
    img = Image.new("RGB", (w * s, h * s), WHITE)
    d = ImageDraw.Draw(img)
    # 顶部色带
    d.rectangle([0, 0, w * s, 6 * s], fill=NAVY)
    _text(d, (44 * s, 34 * s), title, 34 * s, NAVY, True)
    if subtitle:
        _text(d, (44 * s, 82 * s), subtitle, 20 * s, GREY)
    d.line([(44 * s, (118 if subtitle else 96) * s), ((w - 44) * s, (118 if subtitle else 96) * s)],
           fill=LINE, width=2 * s)
    if source:
        _text(d, (44 * s, (h - 34) * s), source, 16 * s, LGREY)
        d.line([(44 * s, (h - 46) * s), ((w - 44) * s, (h - 46) * s)], fill=LINE, width=1 * s)
    return img, d, s


def _save(img, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = img.size
    img.resize((w // 2, h // 2), Image.LANCZOS).save(out, quality=95)
    return out


def bar_chart(out, title, subtitle, categories, series, ylabel="",
              value_fmt="{:.0f}", source="", stacked=False, height=560, width=1100,
              ymax=None, colors=None):
    """series: {名称: [值...]}；分组柱状图。"""
    img, d, s = _canvas(width, height, title, subtitle, source)
    left, right, top, bottom = 110, 50, 150, 90
    plot_w, plot_h = width - left - right, height - top - bottom
    colors = colors or PALETTE
    max_val = max(max(v) for v in series.values()) * 1.18
    if ymax:
        max_val = ymax
    # 网格与 Y 轴刻度
    steps = 5
    for i in range(steps + 1):
        val = max_val * i / steps
        y = top + plot_h - plot_h * i / steps
        d.line([(left * s, y * s), ((left + plot_w) * s, y * s)], fill="#EDF0F4", width=1 * s)
        _text(d, ((left - 12) * s, y * s), value_fmt.format(val), 16 * s, LGREY, anchor="rm")
    if ylabel:
        _text(d, (24 * s, (top + plot_h / 2) * s), ylabel, 17 * s, GREY, anchor="lm")

    n = len(categories)
    m = len(series)
    group_w = plot_w / n
    bar_w = min(58, group_w * 0.62 / m)
    gap = 6
    for gi, cat in enumerate(categories):
        gx = left + group_w * gi + group_w / 2
        total_w = m * bar_w + (m - 1) * gap
        start = gx - total_w / 2
        for si, (name, vals) in enumerate(series.items()):
            v = vals[gi]
            bh = plot_h * (v / max_val)
            x0 = start + si * (bar_w + gap)
            y0 = top + plot_h - bh
            color = colors[si % len(colors)]
            d.rounded_rectangle([x0 * s, y0 * s, (x0 + bar_w) * s, (top + plot_h) * s],
                                radius=3 * s, fill=color)
            _text(d, ((x0 + bar_w / 2) * s, (y0 - 8) * s), value_fmt.format(v),
                  16 * s, color, True, anchor="mb")
        _text(d, (gx * s, (top + plot_h + 16) * s), cat, 17 * s, "#333333", anchor="ma")
    # 图例
    lx = left
    for si, name in enumerate(series):
        d.rectangle([lx * s, 128 * s, (lx + 12) * s, 140 * s], fill=colors[si % len(colors)])
        _text(d, ((lx + 18) * s, 130 * s), name, 16 * s, GREY, anchor="lm")
        lx += 26 + len(name) * 17
    return _save(img, out)


def hbar_chart(out, title, subtitle, labels, values, unit="%", source="",
               color_fn=None, width=1100, height=520, max_val=None):
    img, d, s = _canvas(width, height, title, subtitle, source)
    left, right, top, bottom = 210, 90, 150, 60
    plot_w = width - left - right
    max_val = max_val or max(values) * 1.2
    row_h = (height - top - bottom) / len(labels)
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = top + row_h * i + row_h * 0.2
        bh = row_h * 0.56
        d.rounded_rectangle([left * s, y * s, (left + plot_w * 1.0) * s, (y + bh) * s],
                            radius=4 * s, fill="#F1F4F8")
        w = plot_w * (v / max_val)
        c = color_fn(i, v) if color_fn else PALETTE[i % len(PALETTE)]
        d.rounded_rectangle([left * s, y * s, (left + w) * s, (y + bh) * s], radius=4 * s, fill=c)
        _text(d, ((left - 14) * s, (y + bh / 2) * s), lab, 17 * s, "#333333", anchor="rm")
        _text(d, ((left + w + 10) * s, (y + bh / 2) * s), f"{v}{unit}", 17 * s, c, True, anchor="lm")
    return _save(img, out)


def line_chart(out, title, subtitle, xlabels, series, ylabel="", unit="",
               source="", width=1100, height=540, colors=None):
    img, d, s = _canvas(width, height, title, subtitle, source)
    left, right, top, bottom = 100, 60, 150, 80
    plot_w, plot_h = width - left - right, height - top - bottom
    colors = colors or PALETTE
    all_vals = [v for vals in series.values() for v in vals]
    vmin, vmax = min(all_vals), max(all_vals)
    pad = (vmax - vmin) * 0.25 or 1
    vmin, vmax = vmin - pad, vmax + pad
    for i in range(5):
        y = top + plot_h * i / 4
        d.line([(left * s, y * s), ((left + plot_w) * s, y * s)], fill="#EDF0F4", width=1 * s)
        _text(d, ((left - 10) * s, y * s), f"{vmax - (vmax - vmin) * i / 4:.0f}",
              15 * s, LGREY, anchor="rm")
    for xi, lab in enumerate(xlabels):
        x = left + plot_w * xi / max(1, len(xlabels) - 1)
        _text(d, (x * s, (top + plot_h + 16) * s), lab, 16 * s, "#333333", anchor="ma")
    for si, (name, vals) in enumerate(series.items()):
        pts = []
        for xi, v in enumerate(vals):
            x = left + plot_w * xi / max(1, len(vals) - 1)
            y = top + plot_h * (1 - (v - vmin) / (vmax - vmin))
            pts.append((x, y))
        d.line([(x * s, y * s) for x, y in pts], fill=colors[si % len(colors)], width=3 * s)
        for xi, (x, y) in enumerate(pts):
            d.ellipse([(x - 4) * s, (y - 4) * s, (x + 4) * s, (y + 4) * s],
                      fill=WHITE, outline=colors[si % len(colors)], width=2 * s)
            if si == 0:
                _text(d, (x * s, (y - 12) * s), f"{vals[xi]}{unit}", 15 * s,
                      colors[si % len(colors)], True, anchor="mb")
    lx = left
    for si, name in enumerate(series):
        d.rectangle([lx * s, 128 * s, (lx + 12) * s, 140 * s], fill=colors[si % len(colors)])
        _text(d, ((lx + 18) * s, 130 * s), name, 16 * s, GREY, anchor="lm")
        lx += 26 + len(name) * 17
    return _save(img, out)


def donut(out, title, subtitle, segments, center_label="", source="",
          width=760, height=560):
    img, d, s = _canvas(width, height, title, subtitle, source)
    cx, cy, r = 250, 330, 120
    total = sum(v for _, v, _ in segments)
    start = -90
    for name, v, color in segments:
        extent = 360 * v / total
        d.arc([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
              start=start, end=start + extent, fill=color, width=26 * s)
        start += extent
    d.ellipse([(cx - r + 26) * s, (cy - r + 26) * s, (cx + r - 26) * s, (cy + r - 26) * s],
              fill=WHITE)
    _text(d, (cx * s, (cy - 10) * s), center_label, 30 * s, NAVY, True, anchor="mm")
    ly = 250
    for name, v, color in segments:
        d.rounded_rectangle([430 * s, ly * s, 448 * s, (ly + 14) * s], radius=3 * s, fill=color)
        _text(d, (462 * s, (ly + 7) * s), f"{name}  {v}", 17 * s, "#333333", anchor="lm")
        ly += 34
    return _save(img, out)


def matrix(out, title, subtitle, points, xlabel, ylabel, source="",
           width=1000, height=680):
    """points: (x, y, label, group)；四象限散点（价值-可行性）。"""
    img, d, s = _canvas(width, height, title, subtitle, source)
    left, right, top, bottom = 110, 60, 150, 90
    plot_w, plot_h = width - left - right, height - top - bottom
    d.rectangle([left * s, top * s, (left + plot_w) * s, (top + plot_h) * s],
                fill="#FBFCFE", outline=LINE, width=2 * s)
    d.line([((left + plot_w / 2) * s, top * s), ((left + plot_w / 2) * s, (top + plot_h) * s)],
           fill=LINE, width=2 * s)
    d.line([(left * s, (top + plot_h / 2) * s), ((left + plot_w) * s, (top + plot_h / 2) * s)],
           fill=LINE, width=2 * s)
    _text(d, ((left + plot_w * 0.72) * s, (top + 24) * s), "优先做（高价值·高可行）",
          17 * s, TEAL, True, anchor="ma")
    _text(d, ((left + plot_w * 0.28) * s, (top + plot_h - 60) * s), "观察（低价值·低可行）",
          17 * s, LGREY, anchor="ma")
    _text(d, (left * s, (top + plot_h + 20) * s), xlabel, 16 * s, GREY, anchor="la")
    _text(d, (24 * s, (top + plot_h / 2) * s), ylabel, 16 * s, GREY, anchor="lm")
    used = {}
    for idx, (x, y, label, group) in enumerate(points):
        # 同坐标点做轻微错位，避免完全重叠
        key = (round(x, 1), round(y, 1))
        n = used.get(key, 0)
        used[key] = n + 1
        x = x + (0.16 * (n - 0.5) if n else 0)
        y = y + (0.12 * n if n else 0)
        px = left + plot_w * ((x - 0.5) / 5)
        py = top + plot_h * (1 - (y - 0.5) / 5)
        color = {"P0": RED, "P1": AMBER, "P2": GREY}.get(group, BLUE)
        d.ellipse([(px - 17) * s, (py - 17) * s, (px + 17) * s, (py + 17) * s],
                  fill=color, outline=WHITE, width=2 * s)
        _text(d, (px * s, py * s), label, 13 * s, WHITE, True, anchor="mm")
    return _save(img, out)


def flow_diagram(out, title, subtitle, steps, source="", width=1150, height=980):
    """纵向流程图。steps: dict(no, actor, title, desc, kind=normal|ai|human|pain)。"""
    img, d, s = _canvas(width, height, title, subtitle, source)
    x0, box_w = 70, 660
    y = 150
    gap = 26
    h = (height - y - 50 - gap * (len(steps) - 1)) / len(steps)
    h = min(h, 92)
    for i, st in enumerate(steps):
        kind = st.get("kind", "normal")
        fill, border = {
            "normal": ("#FFFFFF", LINE),
            "ai": (LIGHT_BLUE, BLUE),
            "human": ("#FFF6E6", AMBER),
            "pain": ("#FBECEC", RED),
        }.get(kind, ("#FFFFFF", LINE))
        top = y + i * (h + gap)
        d.rounded_rectangle([x0 * s, top * s, (x0 + box_w) * s, (top + h) * s],
                            radius=8 * s, fill=fill, outline=border, width=2 * s)
        # 编号
        d.ellipse([(x0 + 16) * s, (top + h / 2 - 15) * s, (x0 + 46) * s, (top + h / 2 + 15) * s],
                  fill=border)
        _text(d, ((x0 + 31) * s, (top + h / 2) * s), str(i + 1), 18 * s, WHITE, True, anchor="mm")
        _text(d, ((x0 + 60) * s, (top + 14) * s), st["title"], 20 * s, NAVY, True)
        desc = _wrap(d, st["desc"], _f(15), (box_w - 90) * s)
        desc = desc[:2]
        for j, line in enumerate(desc):
            _text(d, ((x0 + 60) * s, (top + 42 + j * 20) * s), line, 15 * s, GREY)
        _text(d, ((x0 + box_w - 12) * s, (top + 14) * s), st.get("actor", ""),
              14 * s, LGREY, anchor="ra")
        # 箭头
        if i < len(steps) - 1:
            cx = x0 + 31
            d.line([(cx * s, (top + h) * s), (cx * s, (top + h + gap - 4) * s)],
                   fill=LGREY, width=2 * s)
            d.polygon([((cx - 5) * s, (top + h + gap - 6) * s),
                       ((cx + 5) * s, (top + h + gap - 6) * s),
                       (cx * s, (top + h + gap) * s)], fill=LGREY)
        # 右侧注解
        if st.get("note"):
            note_color = RED if kind == "pain" else (BLUE if kind == "ai" else (AMBER if kind == "human" else GREY))
            lines = _wrap(d, st["note"], _f(15), 330 * s)[:3]
            for j, line in enumerate(lines):
                _text(d, ((x0 + box_w + 26) * s, (top + 22 + j * 22) * s), line, 15 * s, note_color)
    return _save(img, out)


def arch_diagram(out, title, subtitle, layers, source="", width=1150, height=760):
    """layers: [(层名, 颜色, [组件...])]，自上而下。"""
    img, d, s = _canvas(width, height, title, subtitle, source)
    left, right = 60, 60
    top = 150
    lh = (height - top - 60) / len(layers)
    for li, (name, color, comps) in enumerate(layers):
        y = top + li * lh
        d.rounded_rectangle([left * s, y * s, (width - right) * s, (y + lh - 16) * s],
                            radius=10 * s, fill="#FAFBFD", outline=color, width=2 * s)
        d.rectangle([left * s, y * s, (left + 8) * s, (y + lh - 16) * s], fill=color)
        _text(d, ((left + 22) * s, (y + 16) * s), name, 18 * s, color, True)
        n = len(comps)
        cw = (width - right - left - 170) / max(n, 1)
        for ci, comp in enumerate(comps):
            x = left + 160 + ci * cw
            d.rounded_rectangle([x * s, (y + 42) * s, (x + cw - 14) * s, (y + lh - 34) * s],
                                radius=6 * s, fill=WHITE, outline=LINE, width=1 * s)
            lines = _wrap(d, comp, _f(14), (cw - 30) * s)[:2]
            for j, line in enumerate(lines):
                _text(d, ((x + (cw - 14) / 2) * s, (y + 52 + j * 18) * s), line,
                      14 * s, "#333333", anchor="ma")
    return _save(img, out)


def timeline(out, title, subtitle, events, source="", width=1180, height=460):
    """events: [(时间, 标题, 说明, 颜色)]，横向时间轴。"""
    img, d, s = _canvas(width, height, title, subtitle, source)
    y = 300
    d.line([(60 * s, y * s), ((width - 60) * s, y * s)], fill=LINE, width=4 * s)
    n = len(events)
    for i, (when, head, desc, color) in enumerate(events):
        x = 110 + (width - 220) * i / max(1, n - 1)
        d.ellipse([(x - 9) * s, (y - 9) * s, (x + 9) * s, (y + 9) * s], fill=color,
                  outline=WHITE, width=2 * s)
        _text(d, (x * s, (y - 46) * s), when, 15 * s, color, True, anchor="mb")
        _text(d, (x * s, (y + 22) * s), head, 16 * s, NAVY, True, anchor="ma")
        for j, line in enumerate(_wrap(d, desc, _f(14), 165 * s)[:3]):
            _text(d, (x * s, (y + 46 + j * 18) * s), line, 14 * s, GREY, anchor="ma")
    return _save(img, out)


def heatmap(out, title, subtitle, rows, cols, matrix, source="",
            width=1050, height=620):
    """权限矩阵：1=允许(绿)，0=拒绝(红)，-1=不适用(灰)。"""
    img, d, s = _canvas(width, height, title, subtitle, source)
    left, top = 300, 160
    cell_w = (width - left - 40) / len(cols)
    cell_h = min(56, (height - top - 40) / len(rows))
    for ci, c in enumerate(cols):
        _text(d, ((left + cell_w * ci + cell_w / 2) * s, (top - 20) * s), c,
              15 * s, GREY, True, anchor="mb")
    for ri, r in enumerate(rows):
        y = top + ri * cell_h
        _text(d, ((left - 14) * s, (y + cell_h / 2) * s), r, 14 * s, "#333333", anchor="rm")
        for ci in range(len(cols)):
            v = matrix[ri][ci]
            fill = {1: "#DFF0E3", 0: "#F8DCDC", -1: "#F0F0F0"}[v]
            mark = {1: "✓", 0: "✕", -1: "—"}[v]
            color = {1: TEAL, 0: RED, -1: LGREY}[v]
            x = left + ci * cell_w
            d.rectangle([(x + 2) * s, (y + 2) * s, (x + cell_w - 2) * s, (y + cell_h - 2) * s],
                        fill=fill, outline=WHITE, width=2 * s)
            _text(d, ((x + cell_w / 2) * s, (y + cell_h / 2) * s), mark, 16 * s, color, True, anchor="mm")
    return _save(img, out)
