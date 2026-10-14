#!/usr/bin/env python3
"""수필 낭독 영상 자동 조립기.

입력 폴더 구조:
    <project>/
    ├─ script.txt      수필 전문 (자막 생성용)
    ├─ narration.mp3   낭독 녹음 (영상 길이의 기준)
    ├─ bgm.mp3         배경음악 (반복·덕킹·페이드 처리)
    └─ clips/          영상·사진 (파일명 순서대로 배치)

출력:
    <project>/out/<project>.mp4   1920x1080 30fps H.264 + AAC
    <project>/out/<project>.srt   자막 (문장 단위, 글자 수 비례 타이밍 — 검수 필요)

사용:
    python3 make_essay_film.py daechu
    python3 make_essay_film.py daechu --burn-subs --bgm-volume 0.2
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

W, H, FPS = 1920, 1080, 30
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts"}


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.exit(f"명령 실패: {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r.stdout


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "json", str(path)])
    return float(json.loads(out)["format"]["duration"])


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def plan_timeline(clips, total, image_sec, min_image_sec):
    """각 클립에 화면 시간을 배정한다. 영상은 원본 길이까지, 사진은 남은 시간을 나눠 갖는다."""
    items = [{"path": c, "kind": "video" if c.suffix.lower() in VIDEO_EXT else "image",
              "src_dur": duration(c) if c.suffix.lower() in VIDEO_EXT else None}
             for c in clips]

    # 소재가 부족하면 순서대로 반복해 총 길이를 채운다.
    natural = sum(i["src_dur"] if i["kind"] == "video" else image_sec for i in items)
    base = list(items)
    while natural < total:
        for i in base:
            items.append(dict(i))
            natural += i["src_dur"] if i["kind"] == "video" else image_sec
            if natural >= total:
                break

    videos = [i for i in items if i["kind"] == "video"]
    images = [i for i in items if i["kind"] == "image"]
    video_sum = sum(i["src_dur"] for i in videos)
    image_budget = total - video_sum

    if images and image_budget >= min_image_sec * len(images):
        for i in images:
            i["dur"] = image_budget / len(images)
        for i in videos:
            i["dur"] = i["src_dur"]
    else:
        # 영상이 너무 길면 사진 최소 시간을 확보한 뒤 영상을 비례로 잘라낸다.
        img_each = min(image_sec, total / max(len(items), 1)) if images else 0
        for i in images:
            i["dur"] = img_each
        scale = (total - img_each * len(images)) / video_sum if video_sum else 0
        for i in videos:
            i["dur"] = i["src_dur"] * scale

    # 반올림 오차는 마지막 클립이 흡수
    drift = total - sum(i["dur"] for i in items)
    items[-1]["dur"] += drift
    return [i for i in items if i["dur"] > 0.2]


def render_segment(item, idx, out, fade):
    d = item["dur"]
    fade_out_at = max(d - fade, 0)
    fades = f"fade=t=in:st=0:d={fade},fade=t=out:st={fade_out_at:.3f}:d={fade}"
    fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
           f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    if item["kind"] == "image":
        frames = int(round(d * FPS))
        # 켄 번스: 짝수 번째는 확대, 홀수 번째는 축소. 떨림 방지를 위해 4배 업스케일 후 zoompan.
        zoom = ("min(1+0.0008*on,1.12)" if idx % 2 == 0
                else "max(1.12-0.0008*on,1.0)")
        vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=decrease,"
              f"pad={W*2}:{H*2}:(ow-iw)/2:(oh-ih)/2:color=black,"
              f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={W}x{H}:fps={FPS},setsar=1,{fades}")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(item["path"]),
               "-vf", vf, "-frames:v", str(frames)]
    else:
        vf = f"{fit},fps={FPS},{fades}"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(item["path"]),
               "-t", f"{d:.3f}", "-vf", vf, "-an"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(out)]
    run(cmd)


def split_sentences(text):
    text = re.sub(r"[ \t]+", " ", text.strip())
    parts = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        parts += [s.strip() for s in re.split(r"(?<=[.!?。…”\"])\s+", para) if s.strip()]
    # 너무 긴 문장은 쉼표 기준으로 한 번 더 나눈다 (자막 한 줄 가독성).
    out = []
    for s in parts:
        if len(s) > 42 and "," in s:
            chunks = [c.strip() + "," for c in s.split(",")]
            chunks[-1] = chunks[-1][:-1]
            out += [c for c in chunks if c.strip(",")]
        else:
            out.append(s)
    return out


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(sentences, start, narr_dur, path):
    weights = [max(len(re.sub(r"\s", "", s)), 1) for s in sentences]
    total_w = sum(weights)
    t = start
    lines = []
    for n, (s, w) in enumerate(zip(sentences, weights), 1):
        d = narr_dur * w / total_w
        lines.append(f"{n}\n{srt_time(t)} --> {srt_time(t + d - 0.05)}\n{s}\n")
        t += d
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--head", type=float, default=2.0, help="낭독 시작 전 여백(초)")
    ap.add_argument("--tail", type=float, default=4.0, help="낭독 종료 후 여백(초)")
    ap.add_argument("--image-sec", type=float, default=6.0, help="사진 1장 기본 노출 시간")
    ap.add_argument("--min-image-sec", type=float, default=3.0)
    ap.add_argument("--fade", type=float, default=0.6, help="클립 간 블랙 페이드(초)")
    ap.add_argument("--bgm-volume", type=float, default=0.25)
    ap.add_argument("--no-duck", action="store_true", help="낭독 중 BGM 자동 낮춤 끄기")
    ap.add_argument("--burn-subs", action="store_true", help="자막을 화면에 입히기")
    ap.add_argument("--font", default="Noto Sans CJK KR", help="자막 폰트 이름")
    ap.add_argument("--keep-temp", action="store_true")
    a = ap.parse_args()

    p = a.project
    narration, bgm, script, clip_dir = p / "narration.mp3", p / "bgm.mp3", p / "script.txt", p / "clips"
    for f in (narration, clip_dir):
        if not f.exists():
            sys.exit(f"필수 파일 없음: {f}")
    clips = sorted((c for c in clip_dir.iterdir()
                    if c.suffix.lower() in IMAGE_EXT | VIDEO_EXT), key=natural_key)
    if not clips:
        sys.exit(f"{clip_dir}에 사용할 영상·사진이 없습니다.")

    out_dir = p / "out"
    out_dir.mkdir(exist_ok=True)
    output = a.output or out_dir / f"{p.resolve().name}.mp4"
    srt = output.with_suffix(".srt")

    narr_dur = duration(narration)
    total = a.head + narr_dur + a.tail
    print(f"낭독 {narr_dur:.1f}s → 전체 {total:.1f}s, 클립 {len(clips)}개")

    if script.exists():
        write_srt(split_sentences(script.read_text(encoding="utf-8")), a.head, narr_dur, srt)
        print(f"자막 초안: {srt}")

    tmp = Path(tempfile.mkdtemp(prefix="essay_film_"))
    try:
        timeline = plan_timeline(clips, total, a.image_sec, a.min_image_sec)
        segs = []
        for n, item in enumerate(timeline):
            seg = tmp / f"seg_{n:03}.mp4"
            print(f"  [{n+1}/{len(timeline)}] {item['path'].name} {item['dur']:.1f}s")
            render_segment(item, n, seg, min(a.fade, item["dur"] / 3))
            segs.append(seg)
        concat_list = tmp / "list.txt"
        concat_list.write_text("".join(f"file '{s}'\n" for s in segs))
        video = tmp / "video.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(video)])

        # 오디오: 낭독(head만큼 지연) + BGM(반복, 덕킹, 페이드 인/아웃)
        delay = int(a.head * 1000)
        inputs = ["-i", str(video), "-i", str(narration)]
        fc = [f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
              f"adelay={delay}|{delay},apad,atrim=0:{total:.3f}[narr]"]
        if bgm.exists():
            inputs += ["-stream_loop", "-1", "-i", str(bgm)]
            fc.append(f"[2:a]aresample=48000,aformat=channel_layouts=stereo,"
                      f"atrim=0:{total:.3f},volume={a.bgm_volume},"
                      f"afade=t=in:st=0:d=2,afade=t=out:st={total-3:.3f}:d=3[bgm]")
            if a.no_duck:
                fc.append("[narr]asplit=1[n1]")
                fc.append("[bgm]anull[b1]")
            else:
                fc.append("[narr]asplit=2[n1][key]")
                fc.append("[bgm][key]sidechaincompress=threshold=0.03:ratio=6:"
                          "attack=20:release=600[b1]")
            fc.append("[n1][b1]amix=inputs=2:duration=first:normalize=0,"
                      "loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
        else:
            fc.append("[narr]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")

        vmap = "0:v"
        if a.burn_subs and srt.exists():
            style = (f"FontName={a.font},FontSize=22,PrimaryColour=&H00FFFFFF,"
                     "OutlineColour=&H80000000,BorderStyle=1,Outline=1.2,Shadow=0,MarginV=60")
            esc = str(srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            fc.append(f"[0:v]subtitles='{esc}':force_style='{style}'[vout]")
            vmap = "[vout]"

        cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
               "-filter_complex", ";".join(fc), "-map", vmap, "-map", "[aout]"]
        cmd += (["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
                if vmap != "0:v" else ["-c:v", "copy"])
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-t", f"{total:.3f}", "-movflags", "+faststart", str(output)]
        run(cmd)
        print(f"완성: {output} ({duration(output):.1f}s)")
    finally:
        if a.keep_temp:
            print(f"임시 파일: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
