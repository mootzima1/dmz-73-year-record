#!/usr/bin/env python3
"""OpenAI 이미지 API로 「대추」 장면 그림을 만들어 daechu/clips/에 넣는다.

    export OPENAI_API_KEY=...        # 환경 설정의 시크릿으로 넣어 둘 것
    python3 gen_images.py daechu/clips            # 기본 7장면
    python3 gen_images.py daechu/clips --only 2 7 # 일부만 다시 생성

생성 이미지는 16:9로 가운데를 잘라 저장하므로 make_daechu.py가 화면을 꽉 채워 쓴다.
"""
import argparse
import base64
import io
import os
import sys
from pathlib import Path

import requests

STYLE = ("Style: quiet, lyrical Korean autumn. Soft natural late-afternoon light, warm tones, "
         "slightly muted saturation, gentle film grain, painterly but realistic. "
         "No text, no letters, no watermark, no human faces. Landscape composition; "
         "keep the lower 20% of the frame calm and uncluttered.")

SCENES = [
    ("tree", "An old jujube tree in a traditional Korean countryside yard in autumn, branches heavy "
             "with small red and green jujubes, a low stone wall and a tiled-roof house behind it."),
    ("closeup", "Close-up of ripe red jujubes hanging on a thin branch, a few still half green, "
                "shallow depth of field, backlit by low autumn sun."),
    ("drying", "Red jujubes spread out to dry on a woven bamboo tray in a sunny Korean courtyard, "
               "long autumn shadows."),
    ("hands", "Weathered hands of an elderly person gently holding a handful of red jujubes, "
              "soft focus background of a farmhouse yard, face not visible."),
    ("fallen", "A few fallen jujubes and yellow leaves on the dirt ground beneath a jujube tree, quiet and still."),
    ("maru", "An empty wooden veranda (maru) of a Korean country house in autumn afternoon light, "
             "a basket of jujubes beside the door."),
    ("ending", "A single branch of deep crimson jujubes against a pale autumn sky at dusk, minimal "
               "composition with a lot of empty space in the center."),
]


def crop_16x9(png_bytes, out):
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = im.size
    th = round(w * 9 / 16)
    if th <= h:
        top = (h - th) // 2
        im = im.crop((0, top, w, top + th))
    else:
        tw = round(h * 16 / 9)
        left = (w - tw) // 2
        im = im.crop((left, 0, left + tw, h))
    im.save(out, quality=95)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--only", type=int, nargs="*", help="장면 번호(1부터)만 생성")
    ap.add_argument("--model", default="gpt-image-1")
    ap.add_argument("--size", default="1536x1024")
    a = ap.parse_args()

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY 환경 변수가 없습니다.")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for n, (slug, prompt) in enumerate(SCENES, 1):
        if a.only and n not in a.only:
            continue
        out = a.out_dir / f"{n:02}_{slug}.jpg"
        print(f"[{n}/{len(SCENES)}] {out.name}")
        r = requests.post("https://api.openai.com/v1/images/generations",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": a.model, "prompt": f"{prompt}\n\n{STYLE}",
                                "size": a.size, "n": 1}, timeout=300)
        if r.status_code != 200:
            sys.exit(f"API 오류 {r.status_code}: {r.text[:500]}")
        item = r.json()["data"][0]
        data = (base64.b64decode(item["b64_json"]) if item.get("b64_json")
                else requests.get(item["url"], timeout=120).content)
        crop_16x9(data, out)
        (a.out_dir / f"{n:02}_{slug}.prompt.txt").write_text(f"{prompt}\n\n{STYLE}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
