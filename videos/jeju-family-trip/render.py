"""효주네 가족 제주 여행 — 15초 세로(9:16) 숏폼 렌더러.

Vox 스타일(종이 질감 · 폴라로이드 콜라주 · 형광펜 · 손글씨 주석)과
키네틱 타이포그래피, 시네마틱 카메라(켄번스 · 휩팬 · 줌 트랜지션 · 필름 그레인)를
Pillow + numpy 로 프레임 단위 합성한 뒤 ffmpeg 로 인코딩한다.

사용법:
  PHOTO_DIR=... FONT_DIR=... OUT_DIR=... python3 render.py
  - PHOTO_DIR: p1.jpg ~ p8.jpg (가족 사진, 순서는 아래 SCENE 주석 참고)
  - FONT_DIR : BlackHanSans-Regular.ttf, NanumPenScript-Regular.ttf, NotoSansKR.ttf (Google Fonts, OFL)
"""
import math
import os
import random
import subprocess
import sys
import wave
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H, FPS, DUR = 1080, 1920, 30, 15.0
N = int(FPS * DUR)
BASE = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.environ.get("PHOTO_DIR", os.path.join(BASE, "photos"))
FONT_DIR = os.environ.get("FONT_DIR", os.path.join(BASE, "fonts"))
OUT_DIR = os.environ.get("OUT_DIR", os.path.join(BASE, "build"))

INK = (22, 22, 26)
RED = (214, 40, 40)
YELLOW = (255, 220, 40)
PAPER_RGB = (238, 230, 212)
WHITE = (255, 255, 255)

# 휩팬으로 넘어가는 컷 지점(초). 8→10초 지도 씬은 줌 트랜지션으로 따로 처리.
WHIP_CUTS = [2.0, 5.0, 8.0, 12.5]
WHIP_T = 0.16


# ---------------------------------------------------------------- easing
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, t0, t1):
    return clamp((t - t0) / (t1 - t0))


def eo(x):
    return 1 - (1 - x) ** 3


def eio(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def eback(x, s=1.9):
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def lerp(a, b, p):
    return a + (b - a) * p


# ---------------------------------------------------------------- fonts
_font_cache = {}


def font(name, size):
    key = (name, size)
    if key not in _font_cache:
        files = {"heavy": "BlackHanSans-Regular.ttf", "pen": "NanumPenScript-Regular.ttf", "sans": "NotoSansKR.ttf"}
        f = ImageFont.truetype(os.path.join(FONT_DIR, files[name]), size)
        if name == "sans":
            try:
                f.set_variation_by_axes([800])
            except Exception:
                pass
        _font_cache[key] = f
    return _font_cache[key]


# ---------------------------------------------------------------- assets
A = {}


def load_photo(i):
    im = ImageOps.exif_transpose(Image.open(os.path.join(PHOTO_DIR, f"p{i}.jpg"))).convert("RGB")
    s = 2000 / max(im.size)
    return im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)


def cover(im, w, h, cx=0.5, cy=0.5, zoom=1.0):
    iw, ih = im.size
    ar = w / h
    if iw / ih > ar:
        ch, cw = ih, ih * ar
    else:
        cw, ch = iw, iw / ar
    cw /= zoom
    ch /= zoom
    x0 = clamp(cx * iw - cw / 2, 0, iw - cw)
    y0 = clamp(cy * ih - ch / 2, 0, ih - ch)
    return im.resize((w, h), Image.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch))


def make_paper(seed=3):
    rng = np.random.default_rng(seed)
    low = rng.normal(0, 1, (H // 24, W // 24))
    low = Image.fromarray(np.clip(low * 40 + 128, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    low = (np.asarray(low, np.float32) - 128) * 0.11
    fib = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(fib)
    for _ in range(260):
        x, y = rng.uniform(0, W), rng.uniform(0, H)
        a, l = rng.uniform(0, math.pi), rng.uniform(8, 40)
        d.line([(x, y), (x + l * math.cos(a), y + l * math.sin(a))], fill=int(rng.uniform(20, 60)), width=1)
    fib = np.asarray(fib.filter(ImageFilter.GaussianBlur(0.8)), np.float32) * 0.25
    hi = rng.normal(0, 3.5, (H, W))
    arr = np.array(PAPER_RGB, np.float32)[None, None, :] + (low + hi - fib)[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def make_vignette():
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    r = ((x - W / 2) / (W * 0.62)) ** 2 + ((y - H / 2) / (H * 0.62)) ** 2
    return (1 - 0.38 * np.clip(r, 0, 1.6) ** 1.3)[..., None].astype(np.float32)


def make_leak():
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    g = np.exp(-(((x - W * 1.05) / 520) ** 2 + ((y - H * 0.15) / 760) ** 2))
    g2 = np.exp(-(((x + W * 0.1) / 420) ** 2 + ((y - H * 0.9) / 600) ** 2)) * 0.6
    col = np.array([255, 150, 60], np.float32) / 255
    return ((g + g2)[..., None] * col[None, None, :]).astype(np.float32)


def blurred_bg(im, darken=0.5):
    bg = cover(im, W // 4, H // 4).filter(ImageFilter.GaussianBlur(9)).resize((W, H), Image.BICUBIC)
    arr = np.asarray(bg, np.float32) * darken
    return Image.fromarray(arr.astype(np.uint8))


PAD = 70


class Card:
    """흰 테두리 인화지 + 그림자 + 마스킹테이프. 아래 여백이 두꺼우면 폴라로이드 + 손글씨 캡션."""

    def __init__(self, inner, border=22, bottom=None, caption=None, seed=0):
        rng = random.Random(seed)
        iw, ih = inner.size
        b, bb = border, (bottom or border)
        self.inner = inner
        self.cw, self.ch = iw + 2 * b, ih + b + bb
        self.size = (self.cw + 2 * PAD, self.ch + 2 * PAD)
        self.inner_off = (PAD + b, PAD + b)

        sh = Image.new("L", self.size, 0)
        ImageDraw.Draw(sh).rectangle([PAD + 8, PAD + 16, PAD + self.cw + 8, PAD + self.ch + 16], fill=130)
        self.shadow = Image.new("RGBA", self.size, (25, 18, 10, 0))
        self.shadow.putalpha(sh.filter(ImageFilter.GaussianBlur(16)))

        self.frame = Image.new("RGBA", self.size, (0, 0, 0, 0))
        fd = ImageDraw.Draw(self.frame)
        fd.rectangle([PAD, PAD, PAD + self.cw - 1, PAD + self.ch - 1], fill=(250, 248, 242, 255))
        if caption:
            f = font("pen", 66)
            tw = f.getlength(caption)
            fd.text((PAD + (self.cw - tw) / 2, PAD + b + ih + (bb - 66) / 2 - 6), caption, font=f, fill=INK)

        self.tape = Image.new("RGBA", self.size, (0, 0, 0, 0))
        tw, th = 190, 54
        t = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        pts = [(0, 0), (tw, 0)]
        for k in range(1, 6):
            pts.append((tw - (6 if k % 2 else 0), th * k / 6))
        pts.append((tw, th))
        pts.append((0, th))
        for k in range(5, 0, -1):
            pts.append(((6 if k % 2 else 0), th * k / 6))
        ImageDraw.Draw(t).polygon(pts, fill=(236, 226, 190, 185))
        t = t.rotate(rng.uniform(-9, 9), Image.BICUBIC, expand=True)
        self.tape.alpha_composite(t, (int(PAD + self.cw / 2 - t.width / 2 + rng.uniform(-60, 60)), int(PAD - t.height / 2 + 4)))

    def compose(self, inner=None, draw_fn=None):
        img = self.shadow.copy()
        img.alpha_composite(self.frame)
        img.paste(inner or self.inner, self.inner_off)
        if draw_fn:
            draw_fn(img, self.inner_off)
        img.alpha_composite(self.tape)
        return img


def init():
    if A:
        return
    P = {i: load_photo(i) for i in range(1, 9)}
    A["P"] = P
    A["paper"] = make_paper()
    A["vig"] = make_vignette()
    A["leak"] = make_leak()
    A["C1"] = Card(cover(P[1], 1000, 563), seed=1)
    A["C3"] = Card(cover(P[3], 760, 428, cx=0.45), seed=3)
    A["C2"] = Card(cover(P[2], 440, 700, cy=0.55), bottom=95, caption="인삼 손질 장인", seed=12)
    A["C4"] = Card(cover(P[4], 440, 700, cy=0.45), bottom=95, caption="인삼 들고 찰칵!", seed=14)
    A["C8"] = Card(cover(P[8], 440, 560, cy=0.45), bottom=95, caption="오늘의 모델", seed=18)
    A["C5"] = Card(cover(P[6], 520, 420, cx=0.38, cy=0.58, zoom=1.3), bottom=95, caption="전망대 인증샷", seed=15)
    A["C7"] = Card(cover(P[7], 960, 620, cx=0.56, cy=0.71, zoom=1.25), seed=7)
    A["bg6"] = blurred_bg(P[5], 0.42)
    A["bg7"] = blurred_bg(P[7], 0.40)
    A["map"] = make_map_base()


# ---------------------------------------------------------------- drawing helpers
def place(canvas, layer, cx, cy, angle=0.0, scale=1.0, alpha=1.0):
    if scale <= 0.01 or alpha <= 0.005:
        return
    im = layer
    if abs(scale - 1) > 1e-3:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.BICUBIC)
    if abs(angle) > 0.01:
        im = im.rotate(angle, Image.BICUBIC, expand=True)
    if alpha < 1:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
    canvas.paste(im, (round(cx - im.width / 2), round(cy - im.height / 2)), im)


_glyph_cache = {}


def glyph(ch, fname, size, fill, stroke=0, stroke_fill=None):
    key = (ch, fname, size, fill, stroke, stroke_fill)
    if key not in _glyph_cache:
        f = font(fname, size)
        asc, desc = f.getmetrics()
        m = 16 + stroke
        w = int(f.getlength(ch)) + 2 * m
        im = Image.new("RGBA", (w, asc + desc + 2 * m), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((m, m), ch, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        _glyph_cache[key] = im
    return _glyph_cache[key]


def line_box(txt, fname, size, cx, cy):
    f = font(fname, size)
    asc, desc = f.getmetrics()
    w = f.getlength(txt)
    return cx - w / 2, cy - (asc + desc) / 2, cx + w / 2, cy + (asc + desc) / 2


def kinetic(canvas, txt, fname, size, cx, cy, t, t0, stagger=0.06, dur=0.32, fill=INK, stroke=0,
            stroke_fill=None, rise=50, tilt=0.0):
    """글자별 팝업(오버슈트 스케일 + 상승)."""
    f = font(fname, size)
    x = cx - f.getlength(txt) / 2
    for i, ch in enumerate(txt):
        adv = f.getlength(ch)
        p = prog(t, t0 + i * stagger, t0 + i * stagger + dur)
        if p > 0 and ch != " ":
            g = glyph(ch, fname, size, fill, stroke, stroke_fill)
            ang = tilt * (1 if i % 2 else -1) * (1 - p)
            place(canvas, g, x + adv / 2, cy + (1 - eo(p)) * rise, ang, eback(p), clamp(p * 4))
        x += adv


def text_img(txt, fname, size, fill, stroke=0, stroke_fill=None):
    f = font(fname, size)
    asc, desc = f.getmetrics()
    m = 16 + stroke
    im = Image.new("RGBA", (int(f.getlength(txt)) + 2 * m, asc + desc + 2 * m), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((m, m), txt, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return im


def reveal(canvas, im, cx, cy, p, alpha=1.0):
    """왼→오 손글씨 쓰듯 드러내기."""
    if p <= 0:
        return
    w = max(1, int(im.width * p))
    part = im.crop((0, 0, w, im.height))
    canvas.paste(part, (round(cx - im.width / 2), round(cy - im.height / 2)), part)


def highlight(canvas, box, p, color=YELLOW, seed=1, alpha=225):
    """형광펜: 글자 아래 60%를 왼→오로 칠한다. 텍스트보다 먼저 그릴 것."""
    if p <= 0:
        return
    rng = random.Random(seed)
    x0, y0, x1, y1 = box
    y0 = y0 + (y1 - y0) * 0.38
    x0 -= 18
    x1 += 18
    xe = x0 + (x1 - x0) * eo(p)
    lw, lh = int(x1 - x0 + 40), int(y1 - y0 + 40)
    layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ox, oy = x0 - 20, y0 - 20
    top = [(x - ox, y0 - oy + rng.uniform(-3, 3) - 4 * (x - x0) / (x1 - x0)) for x in np.linspace(x0, xe, 12)]
    bot = [(x - ox, y1 - oy + rng.uniform(-3, 3)) for x in np.linspace(xe, x0, 12)]
    d.polygon(top + bot, fill=color + (alpha,))
    canvas.paste(layer, (round(ox), round(oy)), layer)


def pen_circle(d, cx, cy, rx, ry, p, color=RED, width=9, seed=0.7):
    if p <= 0:
        return
    a0 = -2.3
    total = 2 * math.pi * 1.12
    n = max(2, int(90 * p))
    pts = []
    for k in range(n + 1):
        a = a0 + total * p * k / n
        g = 1 + 0.07 * (a - a0) / total + 0.035 * math.sin(3 * a + seed)
        pts.append((cx + rx * g * math.cos(a), cy + ry * g * math.sin(a)))
    d.line(pts, fill=color, width=width, joint="curve")


def pen_arrow(d, p0, c, p1, p, color=RED, width=8):
    if p <= 0:
        return
    n = 30
    pts = []
    for k in range(int(n * p) + 1):
        s = k / n
        pts.append(((1 - s) ** 2 * p0[0] + 2 * (1 - s) * s * c[0] + s * s * p1[0],
                    (1 - s) ** 2 * p0[1] + 2 * (1 - s) * s * c[1] + s * s * p1[1]))
    if len(pts) > 1:
        d.line(pts, fill=color, width=width, joint="curve")
    if p >= 1:
        ang = math.atan2(p1[1] - c[1], p1[0] - c[0])
        for da in (2.6, -2.6):
            d.line([p1, (p1[0] + 34 * math.cos(ang + da), p1[1] + 34 * math.sin(ang + da))], fill=color, width=width)


def chip(canvas, txt, cx, cy, p, size=44, bg=INK, fg=WHITE):
    if p <= 0:
        return
    f = font("sans", size)
    tw = f.getlength(txt)
    asc, desc = f.getmetrics()
    w, h = int(tw + 56), int(asc + desc + 22)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=bg)
    d.text((28, 9), txt, font=f, fill=fg)
    place(canvas, im, cx + (1 - eo(p)) * -80, cy, 0, 1, clamp(p * 2))


def camera(img, zoom=1.0, dx=0.0, dy=0.0, cx=W / 2, cy=H / 2):
    if abs(zoom - 1) < 1e-4 and abs(dx) < 0.05 and abs(dy) < 0.05:
        return img
    w, h = W / zoom, H / zoom
    x0, y0 = cx - w / 2 - dx, cy - h / 2 - dy
    return img.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + w, y0 + h))


def shake(t, hits, amp=16, dur=0.22):
    dx = dy = 0.0
    for h in hits:
        if h <= t < h + dur:
            k = 1 - (t - h) / dur
            dx += amp * k * math.sin((t - h) * 95)
            dy += amp * k * math.cos((t - h) * 77)
    return dx, dy


# ---------------------------------------------------------------- SCENE 1 (0~2s) 타이틀
def scene1(t):
    c = A["paper"].copy()
    d = ImageDraw.Draw(c)
    # 장식 테이프 & 별 낙서
    for (x, y, a) in ((90, 200, 30), (990, 1720, -25)):
        tp = Image.new("RGBA", (220, 56), (236, 226, 190, 170))
        place(c, tp, x, y, a)
    chip(c, "FAMILY TRIP · JEJU", W / 2, 560, prog(t, 0.05, 0.4))
    box = line_box("가족", "heavy", 270, W / 2, 1085)
    highlight(c, box, prog(t, 0.75, 1.05), seed=4)
    kinetic(c, "효주네", "heavy", 270, W / 2, 800, t, 0.15, stagger=0.09, tilt=8)
    kinetic(c, "가족", "heavy", 270, W / 2, 1085, t, 0.45, stagger=0.09, tilt=8)
    reveal(c, text_img("제주로 떠납니다!", "pen", 128, RED), W / 2, 1330, eio(prog(t, 1.05, 1.6)))
    sp = prog(t, 1.25, 1.5)
    if sp > 0:
        for (sx, sy, r) in ((170, 720, 40), (930, 960, 34), (880, 640, 24)):
            rr = r * eback(sp)
            for k in range(4):
                a = k * math.pi / 4
                d.line([(sx - rr * math.cos(a), sy - rr * math.sin(a)), (sx + rr * math.cos(a), sy + rr * math.sin(a))],
                       fill=RED, width=6)
    return camera(c, 1 + 0.05 * eio(prog(t, 0, 2.0)))


# ---------------------------------------------------------------- SCENE 2 (2~5s) 마당 웃음
def scene2(t):
    c = A["paper"].copy()
    chip(c, "#우리집마당", W / 2, 120, prog(t, 2.05, 2.35))
    box = line_box("웃음이 끊이지 않는 집", "heavy", 96, W / 2, 250)
    highlight(c, box, prog(t, 2.3, 2.7), seed=7)
    kinetic(c, "웃음이 끊이지 않는 집", "heavy", 96, W / 2, 250, t, 2.05, stagger=0.025, dur=0.25)

    C1 = A["C1"]
    pc = prog(t, 3.2, 3.65)

    def ann(img, off):
        pen_circle(ImageDraw.Draw(img), off[0] + 425, off[1] + 205, 88, 102, eio(pc))

    card = C1.compose(draw_fn=ann)
    pd = prog(t, 2.0, 2.45)
    y = lerp(-500, 860, eo(pd))
    place(c, card, W / 2, y, lerp(-12, -3, eo(pd)), 1 + 0.04 * prog(t, 2.45, 5.0))

    p3 = prog(t, 2.75, 3.15)
    place(c, A["C3"].compose(), lerp(1500, 600, eback(p3, 1.2)), 1470, lerp(14, 4, eo(p3)))

    reveal(c, text_img("우리 집 대장님!", "pen", 100, RED), 330, 480, eio(prog(t, 3.45, 3.85)))
    pen_arrow(ImageDraw.Draw(c), (470, 540), (520, 600), (470, 668), eio(prog(t, 3.6, 3.85)))
    reveal(c, text_img("꿀맛 인삼 타임 ♪", "pen", 92, INK), 560, 1800, eio(prog(t, 4.0, 4.45)))
    return camera(c, 1.02 + 0.02 * prog(t, 2, 5), dx=6 * math.sin(t * 1.3), dy=5 * math.cos(t * 1.1))


# ---------------------------------------------------------------- SCENE 3 (5~8s) 콜라주 비트컷
S3 = [  # card, cx, cy, angle, slam time
    ("C2", 300, 640, -6, 5.05),
    ("C4", 785, 700, 5, 5.5),
    ("C8", 320, 1330, 4, 5.95),
    ("C5", 760, 1400, -5, 6.4),
]


def scene3(t):
    c = A["paper"].copy()
    reveal(c, text_img("출발 전, 우리 가족 소개", "pen", 92, INK), W / 2, 140, eio(prog(t, 5.0, 5.4)))
    for name, x, y, a, ts in S3:
        p = prog(t, ts - 0.05, ts + 0.13)
        if p > 0:
            place(c, A[name].compose(), x, y, a + 12 * (1 - eo(p)), lerp(1.7, 1.0, eo(p)), clamp(p * 3))
    txt = "준비물: 웃음 · 설렘 · 가족"
    box = line_box(txt, "heavy", 80, W / 2, 1790)
    highlight(c, box, prog(t, 7.0, 7.35), seed=9)
    kinetic(c, txt, "heavy", 80, W / 2, 1790, t, 6.85, stagger=0.025, dur=0.22)
    dx, dy = shake(t, [s[4] + 0.08 for s in S3])
    return camera(c, 1.04, dx, dy)


# ---------------------------------------------------------------- SCENE 4 (8~10s) 지도 애니메이션
JEJU = (640, 1390)
SEOUL = (300, 330)
CTRL = (1000, 700)
PLANE_END = (610, 1330)


def make_map_base():
    sea = np.array([196, 218, 224], np.float32)
    paper = np.asarray(make_paper(5), np.float32)
    base = Image.fromarray(np.clip(paper * (sea / np.array(PAPER_RGB, np.float32)), 0, 255).astype(np.uint8))
    land = Image.new("L", (W, H), 0)
    rng = random.Random(4)
    coast = [(x, 820 + 70 * math.sin(x / 95) + 35 * math.sin(x / 33 + 1) + rng.uniform(-8, 8)) for x in range(W + 40, -41, -20)]
    poly = [(-40, -40), (W + 40, -40)] + coast
    ImageDraw.Draw(land).polygon(poly, fill=255)
    mask = land.filter(ImageFilter.GaussianBlur(1.2))
    base.paste(A["paper"], (0, 0), mask)
    d = ImageDraw.Draw(base)
    d.line(coast, fill=(120, 108, 92), width=5, joint="curve")
    for k in range(1, 4):  # 해안 물결선
        d.line([(x, y + 26 * k) for x, y in coast], fill=(170, 196, 205), width=3, joint="curve")
    # 작은 섬들
    for (ix, iy, r) in ((180, 1000, 22), (860, 960, 16), (420, 1060, 12)):
        d.ellipse([ix - r, iy - r * 0.6, ix + r, iy + r * 0.6], fill=PAPER_RGB, outline=(120, 108, 92), width=3)
    return base


def bez(s):
    return ((1 - s) ** 2 * SEOUL[0] + 2 * (1 - s) * s * CTRL[0] + s * s * PLANE_END[0],
            (1 - s) ** 2 * SEOUL[1] + 2 * (1 - s) * s * CTRL[1] + s * s * PLANE_END[1])


def plane_img():
    im = Image.new("RGBA", (180, 180), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # 오른쪽(+x)을 향한 비행기
    body = [(160, 90), (140, 80), (40, 80), (22, 90), (40, 100), (140, 100)]
    wing = [(110, 84), (70, 20), (55, 20), (78, 84), (78, 96), (55, 160), (70, 160), (110, 96)]
    tail = [(52, 84), (30, 52), (20, 52), (32, 88), (32, 92), (20, 128), (30, 128), (52, 96)]
    for poly in (wing, tail, body):
        d.polygon(poly, fill=WHITE, outline=INK, width=5)
    return im


def scene4(t):
    tl = t - 8.0
    c = A["map"].copy()
    d = ImageDraw.Draw(c)
    # 제주도
    pts = []
    for k in range(64):
        a = 2 * math.pi * k / 64
        g = 1 + 0.06 * math.sin(3 * a + 0.5) + 0.04 * math.sin(7 * a + 1.3)
        x, y = 300 * g * math.cos(a), 128 * g * math.sin(a)
        r = math.radians(-6)
        pts.append((JEJU[0] + x * math.cos(r) - y * math.sin(r), JEJU[1] + x * math.sin(r) + y * math.cos(r)))
    ji = eo(prog(tl, 1.25, 1.55))
    d.polygon(pts, fill=tuple(int(lerp(a_, b_, ji)) for a_, b_ in zip(PAPER_RGB, YELLOW)), outline=(90, 80, 65), width=6)
    d.polygon([(JEJU[0] - 40, JEJU[1] + 10), (JEJU[0], JEJU[1] - 45), (JEJU[0] + 40, JEJU[1] + 10)], outline=(90, 80, 65), width=5)

    # 서울 핀
    d.ellipse([SEOUL[0] - 22, SEOUL[1] - 22, SEOUL[0] + 22, SEOUL[1] + 22], fill=RED, outline=WHITE, width=6)
    place(c, text_img("서울", "heavy", 72, INK), SEOUL[0] + 120, SEOUL[1] - 4, 0, 1, clamp(prog(tl, 0.05, 0.25)))

    # 점선 경로 + 비행기
    pp = eio(prog(tl, 0.25, 1.3))
    if pp > 0:
        n = 60
        for k in range(int(n * pp)):
            if k % 2 == 0:
                d.line([bez(k / n), bez(min(pp, (k + 1) / n))], fill=INK, width=7)
        x, y = bez(pp)
        x2, y2 = bez(min(1, pp + 0.01))
        x1, y1 = bez(max(0, pp - 0.01))
        ang = -math.degrees(math.atan2(y2 - y1, x2 - x1))
        place(c, plane_img(), x, y, ang, 1.0 - 0.25 * prog(tl, 1.1, 1.35))

    kinetic(c, "제주!", "heavy", 150, 880, 1215, tl, 1.35, stagger=0.06, fill=RED, tilt=10)
    box = line_box("서울에서 제주까지", "heavy", 100, W / 2, 1700)
    highlight(c, box, prog(tl, 0.35, 0.7), seed=11)
    kinetic(c, "서울에서 제주까지", "heavy", 100, W / 2, 1700, tl, 0.1, stagger=0.03, dur=0.25)
    reveal(c, text_img("비행기로 약 1시간 슝~", "pen", 88, INK), W / 2, 1830, eio(prog(tl, 0.8, 1.2)))

    z = eio(prog(tl, 1.55, 2.0))
    out = camera(c, 1 + 2.2 * z, cx=lerp(W / 2, JEJU[0], z), cy=lerp(H / 2, JEJU[1], z))
    fl = prog(tl, 1.8, 2.0)
    if fl > 0:
        out = Image.blend(out, Image.new("RGB", (W, H), WHITE), fl)
    return out


# ---------------------------------------------------------------- SCENE 5 (10~12.5s) 시네마틱 단체샷
BOX_A = (255, 133, 1605, 1126)
BOX_B = (420, 376, 1440, 1126)


def scene5(t):
    tl = t - 10.0
    c = camera(A["bg6"], 1 + 0.03 * tl)
    k = eio(prog(tl, 0, 2.5))
    box = tuple(lerp(a, b, k) for a, b in zip(BOX_A, BOX_B))
    band = A["P"][5].resize((W, 794), Image.LANCZOS, box=box)
    pin = eo(prog(tl, 0, 0.35))
    bw = int(W * lerp(0.86, 1.0, pin))
    bh = int(794 * lerp(0.86, 1.0, pin))
    band = band.resize((bw, bh), Image.BICUBIC)
    c.paste(band, ((W - bw) // 2, (H - bh) // 2))
    d = ImageDraw.Draw(c)
    d.rectangle([(W - bw) // 2, (H - bh) // 2, (W + bw) // 2 - 1, (H + bh) // 2 - 1], outline=WHITE, width=4)

    kinetic(c, "다 같이,", "heavy", 170, W / 2, 330, tl, 0.2, stagger=0.07, fill=WHITE, rise=80)
    hb = line_box("제주로!", "heavy", 190, W / 2, 1530)
    highlight(c, hb, prog(tl, 0.65, 0.95), seed=13, alpha=255)
    kinetic(c, "제주로!", "heavy", 190, W / 2, 1530, tl, 0.6, stagger=0.07, rise=80)
    reveal(c, text_img("효주네 총출동!", "pen", 100, WHITE), W / 2, 1745, eio(prog(tl, 1.2, 1.6)))

    arr = np.asarray(c, np.float32) / 255
    lk = 0.85 * (1 - prog(tl, 0.0, 0.7)) + 0.25
    arr = 1 - (1 - arr) * (1 - A["leak"] * lk)
    fl = 1 - prog(tl, 0.0, 0.3)
    if fl > 0:
        arr = arr * (1 - fl) + fl
    return Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))


# ---------------------------------------------------------------- SCENE 6 (12.5~15s) 엔딩
def scene6(t):
    tl = t - 12.5
    c = A["bg7"].copy()
    p = prog(tl, 0.0, 0.4)
    place(c, A["C7"].compose(), W / 2, 1000, lerp(-8, -2, eo(p)), lerp(0.6, 1.0, eback(p)) + 0.04 * prog(tl, 0.4, 2.5),
          clamp(p * 3))
    kinetic(c, "JEJU", "heavy", 330, W / 2, 420, tl, 0.25, stagger=0.09, fill=YELLOW, stroke=12, stroke_fill=INK,
            rise=90, tilt=14)
    hb = line_box("효주네 가족", "heavy", 130, W / 2, 1490)
    highlight(c, hb, prog(tl, 0.8, 1.1), seed=17, alpha=255)
    kinetic(c, "효주네 가족", "heavy", 130, W / 2, 1490, tl, 0.75, stagger=0.05, dur=0.28)
    place(c, text_img("제주 여행 갑니다!", "sans", 62, WHITE), W / 2, 1655, 0, 1, prog(tl, 1.15, 1.45))
    out = camera(c, 1.0 + 0.03 * tl)
    fade = prog(tl, 1.95, 2.5)
    if fade > 0:
        out = Image.blend(out, Image.new("RGB", (W, H), (0, 0, 0)), eio(fade))
    return out


# ---------------------------------------------------------------- compositor
def scene_at(t):
    if t < 2.0:
        return scene1(t)
    if t < 5.0:
        return scene2(t)
    if t < 8.0:
        return scene3(t)
    if t < 10.0:
        return scene4(t)
    if t < 12.5:
        return scene5(t)
    return scene6(t)


def whip(img, offset, blur):
    arr = np.asarray(img, np.float32)
    acc = np.zeros_like(arr)
    taps = 10
    for s in np.linspace(-blur / 2, blur / 2, taps):
        acc += np.roll(arr, int(offset + s), axis=1)
    return Image.fromarray((acc / taps).astype(np.uint8))


def render_frame(i):
    init()
    t = i / FPS
    img = scene_at(t)
    for cut in WHIP_CUTS:
        if cut - WHIP_T <= t < cut:
            p = prog(t, cut - WHIP_T, cut)
            img = whip(img, -W * 0.6 * p ** 2, 40 + 420 * p)
        elif cut <= t < cut + WHIP_T:
            p = prog(t, cut, cut + WHIP_T)
            img = whip(img, W * 0.6 * (1 - p) ** 2, 40 + 420 * (1 - p))
    # 필름 룩: 비네팅 + 그레인
    arr = np.asarray(img, np.float32) * A["vig"]
    rng = np.random.default_rng(i)
    g = rng.normal(0, 7, (H // 2, W // 2)).astype(np.float32)
    g = np.repeat(np.repeat(g, 2, 0), 2, 1)[..., None]
    arr = np.clip(arr + g, 0, 255).astype(np.uint8)
    Image.fromarray(arr).save(os.path.join(OUT_DIR, "frames", f"f{i:04d}.png"), compress_level=1)
    return i


# ---------------------------------------------------------------- audio (직접 합성: 120BPM 업비트 + 효과음)
SR = 48000


def synth_audio(path):
    n = int(SR * DUR)
    tt = np.arange(n) / SR
    out = np.zeros(n, np.float64)
    rng = np.random.default_rng(1)

    def add(sig, at, gain=1.0):
        s = int(at * SR)
        if s >= n:
            return
        e = min(n, s + len(sig))
        out[s:e] += sig[: e - s] * gain

    def env(length, attack=0.005, decay=0.2):
        x = np.arange(int(length * SR)) / SR
        return np.minimum(1, x / attack) * np.exp(-x / decay)

    def band_noise(length, lo, hi):
        m = int(length * SR)
        sp = np.fft.rfft(rng.normal(0, 1, m))
        f = np.fft.rfftfreq(m, 1 / SR)
        sp[(f < lo) | (f > hi)] = 0
        x = np.fft.irfft(sp, m)
        return x / (np.abs(x).max() + 1e-9)

    # 코드 패드: C - G - Am - F (2초씩)
    chords = [(261.6, 329.6, 392.0), (196.0, 246.9, 293.7), (220.0, 261.6, 329.6), (174.6, 220.0, 261.6)]
    for ci in range(8):
        t0 = ci * 2.0
        seg = np.arange(int(2.0 * SR)) / SR
        sig = np.zeros_like(seg)
        for fr in chords[ci % 4]:
            for h in range(1, 6):
                for det in (-0.6, 0.6):
                    sig += np.sin(2 * np.pi * (fr * h + det) * seg) / (h * 1.6)
        a = np.minimum(1, seg / 0.08) * np.minimum(1, (2.0 - seg) / 0.15)
        add(sig * a / 30, t0, 0.55)
        root = chords[ci % 4][0] / 2
        for b8 in range(8):
            if t0 + b8 * 0.25 < 2.0:
                continue
            bs = np.arange(int(0.22 * SR)) / SR
            add(np.tanh(2 * np.sin(2 * np.pi * root * bs)) * np.exp(-bs / 0.12), t0 + b8 * 0.25, 0.22)

    # 드럼 (2초부터 본격 시작)
    kick_len = 0.35
    kx = np.arange(int(kick_len * SR)) / SR
    kick = np.sin(2 * np.pi * (45 * kx + (120 - 45) * 0.04 * (1 - np.exp(-kx / 0.04)))) * np.exp(-kx / 0.14)
    hat = band_noise(0.05, 6000, 16000) * env(0.05, 0.001, 0.015)
    clap = band_noise(0.2, 900, 5000) * env(0.2, 0.002, 0.06)
    b = 2.0
    while b < 14.4:
        add(kick, b, 0.9)
        add(hat, b + 0.25, 0.18)
        if int(round((b - 2.0) / 0.5)) % 2 == 1:
            add(clap, b, 0.35)
        b += 0.5

    # 인트로 라이저 → 2초 컷
    rl = 1.8
    riser = band_noise(rl, 500, 9000) * np.linspace(0, 1, int(rl * SR)) ** 2
    add(riser, 0.2, 0.25)
    # 휩 효과음
    for cut in WHIP_CUTS + [10.0]:
        wl = 0.4
        w = band_noise(wl, 300, 4000) * np.sin(np.linspace(0, np.pi, int(wl * SR))) ** 2
        add(w, cut - 0.22, 0.4)
    # 콜라주 슬램 (쿵 + 종이 소리)
    for _, _, _, _, ts in S3:
        add(kick, ts + 0.08, 0.7)
        add(band_noise(0.12, 1500, 8000) * env(0.12, 0.001, 0.03), ts + 0.08, 0.35)
    # 글자 팝 (JEJU)
    for k in range(4):
        ps = np.arange(int(0.08 * SR)) / SR
        add(np.sin(2 * np.pi * (900 + 300 * k) * ps) * np.exp(-ps / 0.02), 12.5 + 0.25 + 0.09 * k + 0.08, 0.25)
    # 엔딩 심벌
    add(band_noise(1.6, 4000, 16000) * env(1.6, 0.002, 0.5), 12.5, 0.25)

    out *= np.minimum(1, np.maximum(0, (DUR - tt) / 0.9))
    out = np.tanh(out * 1.2)
    out = out / (np.abs(out).max() + 1e-9) * 0.89
    stereo = np.stack([out, out], 1)
    pcm = (stereo * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


def main():
    os.makedirs(os.path.join(OUT_DIR, "frames"), exist_ok=True)
    frames = range(N) if len(sys.argv) < 2 else [int(x) for x in sys.argv[1].split(",")]
    with Pool(os.cpu_count()) as pool:
        for k, _ in enumerate(pool.imap_unordered(render_frame, frames, chunksize=4)):
            if k % 50 == 0:
                print(f"frame {k}/{len(frames)}", flush=True)
    if len(sys.argv) >= 2:
        return
    wav = os.path.join(OUT_DIR, "audio.wav")
    synth_audio(wav)
    mp4 = os.path.join(OUT_DIR, "jeju_family_trip_15s.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                    "-i", os.path.join(OUT_DIR, "frames", "f%04d.png"), "-i", wav,
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", mp4], check=True)
    print("done:", mp4)


if __name__ == "__main__":
    main()
