#!/usr/bin/env python3
"""12_graphics_spec.md 기준 프록시 프레임 생성기. 의존성 없음. 재실행하면 SVG를 다시 만든다."""
import io, os

os.chdir(os.path.dirname(os.path.abspath(__file__)))   # 어디서 실행하든 이 폴더에 쓴다

W, H = 1920, 1080
SAFE_W = H * 9 / 16              # 607.5 — 9:16 안전영역
SAFE_X = (W - SAFE_W) / 2
C = dict(bg="#0E1113", ground="#2A3136", line="#E8E3D9", alert="#C2452D",
         eco="#5E8C6A", act="#4A7C94", text="#E8E3D9", sub="#8A9299")
# 다큐 조판: 연도·수치·핵심 문장은 명조(무게), 자막·라벨·주석은 고딕(가독).
# 이 컨테이너엔 두 폰트 모두 없어 프록시는 대체 폰트로 렌더된다. 자형·자간은 프록시로 판단하지 말 것.
FD = "'Noto Serif KR','본명조','Source Han Serif K','KoPubWorld바탕체','나눔명조','NanumMyeongjo',serif"
FT = "'Pretendard','Noto Sans KR','본고딕','Source Han Sans K','나눔고딕','NanumGothic',sans-serif"
FONT = FT   # 하위 호환

def frame(body, title, srcmark=None, guides=True):
    g = ""
    if guides:
        g = f"""<g class="guide" opacity="0.30">
    <rect x="{SAFE_X:.1f}" y="0" width="{SAFE_W:.1f}" height="{H}" fill="none" stroke="{C['alert']}" stroke-width="2" stroke-dasharray="12 10"/>
    <text x="{SAFE_X+10:.1f}" y="34" font-family="{FONT}" font-size="22" fill="{C['alert']}">9:16 안전영역 607×1080</text>
    <line x1="0" y1="{H-180}" x2="{W}" y2="{H-180}" stroke="{C['sub']}" stroke-width="1" stroke-dasharray="6 8"/>
    <text x="24" y="{H-150}" font-family="{FONT}" font-size="20" fill="{C['sub']}">자막 여백 180px</text>
  </g>"""
    sm = ""
    if srcmark:
        sm = f'<text x="48" y="{H-56}" font-family="{FONT}" font-size="24" letter-spacing="2" fill="{C["sub"]}">{srcmark}</text>'
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{title}">
  <title>{title}</title>
  <rect width="{W}" height="{H}" fill="{C['bg']}"/>
{body}
{sm}
{g}
</svg>"""

def t(x, y, s, size=42, fill=None, weight=700, anchor="middle", ls=0, op=1, f=None):
    return (f'<text x="{x}" y="{y}" font-family="{f or FT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill or C["text"]}" text-anchor="{anchor}" letter-spacing="{ls}" opacity="{op}">{s}</text>')

def d(*a, **k):
    """디스플레이(명조) — 연도·수치·핵심 문장 전용."""
    k["f"] = FD
    return t(*a, **k)

CX = W / 2
out = {}

# ── 0장 0-4 : 2026 출처 카드 ─────────────────────────────
out["00_cold_open_source_card.svg"] = frame(f"""
  {d(CX, 400, "2026. 9. 21", 140, ls=8)}
  {d(CX, 502, "장병 3명 부상", 60, C['alert'])}
  <rect x="{CX-430}" y="600" width="860" height="112" fill="none" stroke="{C['sub']}" stroke-width="2"/>
  {t(CX, 672, "국방부 중간 판단 (2026. 9. 28)", 44, C['sub'], 500)}
  {t(CX, 790, "※ '중간'을 빼지 말 것 — 유엔사 합동조사 최종 발표 이전이다", 28, C['alert'], 400)}
""", "0장 2026 출처 카드", "MND BRIEFING")

# ── 0장 0-6 : 2015 귀속 3행 병기 ─────────────────────────
rows = [("국방백서", "북한 지뢰 도발로 수록"),
        ("북측 (당시)", "소행 부인"),
        ("8·25 공동보도문", "부상에 대한 유감 표명")]
r = "".join(
    f'{t(CX-340, 560+i*104, a, 40, C["sub"], 500, "end")}'
    f'<line x1="{CX-300}" y1="{530+i*104}" x2="{CX-300}" y2="{578+i*104}" stroke="{C["sub"]}" stroke-width="2"/>'
    f'{t(CX-260, 560+i*104, b, 40, C["text"], 500, "start")}'
    for i, (a, b) in enumerate(rows))
out["00_2015_attribution.svg"] = frame(f"""
  {d(CX, 300, "2015. 8. 4", 104, ls=6)}
  {d(CX, 382, "장병 2명 부상", 50, C['alert'])}
  {r}
  {t(CX, 920, "세 항목은 같은 크기로 병기한다 — 한 문장으로 합치지 않는다", 28, C['alert'], 400)}
""", "0장 2015 귀속 3행", "MND WHITE PAPER · MOU")

# ── 2장 : 연도카드 (현재 2개 / 3개 복원 대비) ─────────────
def card(x, y, yr, txt, mark):
    return (f'<rect x="{x}" y="{y}" width="520" height="300" fill="{C["ground"]}" stroke="{C["sub"]}" stroke-width="2"/>'
            f'{d(x+260, y+124, yr, 92, ls=4)}'
            f'{d(x+260, y+198, txt, 38, C["text"], 500)}'
            f'{t(x+260, y+258, mark, 22, C["sub"], 400, ls=2)}')
out["02_year_cards.svg"] = frame(f"""
  {card(CX-560, 340, "1968", "무장침투", "NATIONAL ARCHIVES")}
  {card(CX+40, 340, "1976", "판문점", "NATIONAL ARCHIVES")}
  <rect x="{CX+640}" y="340" width="200" height="300" fill="none" stroke="{C['sub']}" stroke-width="2" stroke-dasharray="10 8" opacity="0.5"/>
  {d(CX+740, 472, "1970s", 38, C['sub'], 700, op=0.5)}
  {t(CX+740, 522, "보류", 26, C['alert'], 500, op=0.7)}
  {t(CX, 760, "카드 2개 · 카드당 체류 22.5초 (카드 위에서 컷 분할)", 32, C['sub'], 500)}
  {t(CX, 820, "점선 = 1970년대 땅굴. 근거 확보 시 복원되도록 3개 기준으로 레이아웃", 26, C['alert'], 400)}
""", "2장 연도카드")

# ── 4장 : 생태 데이터 그래픽 (국립생태원 없이) ─────────────
out["04_ecology_data.svg"] = frame(f"""
  {d(CX, 192, "DMZ 일원", 50, C['eco'])}
  {t(CX, 250, "접경지역을 포함한 범위다 — 'DMZ 안'이 아니다", 26, C['alert'], 400)}
  <rect x="{CX-480}" y="330" width="960" height="120" fill="{C['ground']}"/>
  <rect x="{CX-480}" y="330" width="15.4" height="120" fill="{C['eco']}"/>
  {d(CX-480, 502, "1,557㎢ = 국토의 1.6%", 38, C['text'], 600, "start")}
  {t(CX+480, 500, "← 띠의 폭이 실제 1.6% 비율", 26, C['sub'], 400, "end")}
  {d(CX-480, 652, "4,873", 104, C['eco'], 700, "start")}
  {d(CX-170, 652, "종 확인", 42, C['text'], 500, "start")}
  {t(CX-480, 702, "환경부·국립생태원 종합 · 2016 발간 · 40여년 조사", 26, C['sub'], 400, "start")}
  {d(CX+130, 652, "91", 104, C['alert'], 700, "start")}
  {d(CX+260, 652, "종 멸종위기", 42, C['text'], 500, "start")}
  {t(CX+130, 702, "절대수만 표기 · 41%는 모수(222종) 병기 없이 쓰지 않는다", 26, C['alert'], 400, "start")}
  {t(CX, 900, "동물 클로즈업 없이 규모로 받는다 — 외부 권리 의존 0", 30, C['sub'], 500)}
""", "4장 생태 데이터", "ME/NIE 2016")

# ── 7장 : 수렴 ──────────────────────────────────────────
out["07_converge.svg"] = frame(f"""
  {d(CX-480, 420, "SAFETY", 70, C['alert'], 700, ls=6)}
  {d(CX, 420, "PEACE", 70, C['act'], 700, ls=6)}
  {d(CX+480, 420, "NATURE", 70, C['eco'], 700, ls=6)}
  <path d="M {CX-480} 470 Q {CX-480} 600 {CX} 640" fill="none" stroke="{C['sub']}" stroke-width="2"/>
  <path d="M {CX} 470 L {CX} 640" fill="none" stroke="{C['sub']}" stroke-width="2"/>
  <path d="M {CX+480} 470 Q {CX+480} 600 {CX} 640" fill="none" stroke="{C['sub']}" stroke-width="2"/>
  {d(CX, 748, "DMZ", 124, C['text'], 700, ls=20)}
  {t(CX, 900, "여유율 27% — 가장 빠듯한 장. 0·6장에서 각 4초 회수 권고", 30, C['alert'], 500)}
""", "7장 수렴")

for name, svg in out.items():
    io.open(name, "w", encoding="utf-8").write(svg)

cards = "".join(
    f'<figure><img src="{n}" alt="{n}"><figcaption>{n}</figcaption></figure>' for n in out)
io.open("index.html", "w", encoding="utf-8").write(f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DMZ 프록시 보드</title>
<style>
:root{{color-scheme:dark;--bg:#16191c;--fg:#E8E3D9;--sub:#8A9299}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font-family:{FT}}}
h1{{font-size:20px;margin:8px 0 4px}}
p{{color:var(--sub);font-size:14px;margin:0 0 20px;line-height:1.6}}
figure{{margin:0 0 28px}}
img{{width:100%;height:auto;display:block;border:1px solid #2A3136}}
figcaption{{color:var(--sub);font-size:13px;margin-top:8px}}
</style>
<h1>DMZ 프록시 보드 — 자체 제작 그래픽</h1>
<p>사양: <code>preproduction/12_graphics_spec.md</code> · 붉은 점선 = 9:16 안전영역 ·
구도와 문구 검토용이며 최종 렌더링이 아니다. 폰트(A14) 라이선스 확인 전이라 자막은 확정이 아니다.</p>
{cards}
</html>""")
print("생성:", ", ".join(out), "+ index.html")
