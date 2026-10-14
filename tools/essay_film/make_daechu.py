#!/usr/bin/env python3
"""「대추」 낭독 에세이 영상 제작 파이프라인.

    python3 make_daechu.py daechu                 # 전체 실행
    python3 make_daechu.py daechu --check-only    # 얼굴 검사·배치 계획만 출력
    python3 make_daechu.py daechu --transcript t.json   # 기존 전사 결과 사용

원본(daechu/ 안의 narration.mp3, script.txt, bgm.mp3, clips/)은 읽기만 한다.
중간 파일은 daechu/work/, 최종본은 daechu/output.mp4.

타임라인:
    0~3s  타이틀(검은 배경, 흰 글씨 페이드인) → 1s 디졸브 → 클립들(컷 5~8s, 1s 디졸브)
    3s    내레이션 시작
    끝    마지막 문장을 화면 중앙에 크게: 낭독이 끝난 뒤 2s 유지 → 3s 페이드아웃
"""
import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

W, H, FPS = 1920, 1080, 30
XF = 1.0            # 크로스 디졸브 길이
TITLE_SEC = 3.0     # 타이틀 단독 노출 시간 (= 내레이션 시작 시각)
CUT_MIN, CUT_MAX, CUT_TARGET = 5.0, 8.0, 6.5
END_HOLD, END_FADE = 2.0, 3.0
LAST_SENTENCE = "올 가을 대추는 유난히 붉고 오래도록 마음에 남는다."
FONT = "NanumMyeongjo"
LINE_CHARS = 24     # 자막 한 줄 최대 글자 수
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts"}
# 따뜻하게 + 채도 약간 낮춤
GRADE = "colorbalance=rs=0.05:gs=0.01:bs=-0.05:rm=0.04:bm=-0.04,eq=saturation=0.85:contrast=0.98"


class AskUser(Exception):
    """자동으로 판단하지 않고 사용자에게 물어야 하는 상황."""


def run(cmd, quiet=True):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.exit(f"명령 실패: {' '.join(map(str, cmd))[:600]}\n{r.stderr[-3000:]}")
    return r.stdout


def probe(path):
    out = json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)]))
    v = next((s for s in out.get("streams", []) if s.get("codec_type") == "video"), {})
    return float(out["format"].get("duration") or 0), v.get("width"), v.get("height")


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


# ───────────────────────── 1. 전사 + 원고 대조 ─────────────────────────

def transcribe(narration, model_name, out_json):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(narration), language="ko", word_timestamps=True,
                                   vad_filter=True, beam_size=5)
    words = [{"w": w.word, "s": w.start, "e": w.end}
             for seg in segments for w in (seg.words or [])]
    out_json.write_text(json.dumps(words, ensure_ascii=False, indent=1), encoding="utf-8")
    return words


def split_sentences(text):
    sents = []
    for para in re.split(r"\n+", text.strip()):
        para = re.sub(r"\s+", " ", para).strip()
        if para:
            sents += [s.strip() for s in re.split(r"(?<=[.!?…])[”’\"']?\s+", para) if s.strip()]
    return sents


def norm_chars(s):
    return [c for c in s if c.isalnum()]


def align(sentences, words):
    """원고 글자열과 전사 글자열을 맞대어 원고 글자마다 시각을 붙인다."""
    w_chars, w_times = [], []
    for w in words:
        cs = norm_chars(w["w"])
        for i, c in enumerate(cs):
            w_chars.append(c)
            step = (w["e"] - w["s"]) / max(len(cs), 1)
            w_times.append((w["s"] + i * step, w["s"] + (i + 1) * step))

    s_chars, owner = [], []
    for k, s in enumerate(sentences):
        for c in norm_chars(s):
            s_chars.append(c)
            owner.append(k)

    t = [None] * len(s_chars)
    sm = difflib.SequenceMatcher(None, s_chars, w_chars, autojunk=False)
    for a, b, n in sm.get_matching_blocks():
        for i in range(n):
            t[a + i] = w_times[b + i]
    matched = sum(x is not None for x in t)
    ratio = matched / max(len(s_chars), 1)

    # 매칭 안 된 글자는 앞뒤 매칭 글자 사이를 선형 보간
    known = [i for i, x in enumerate(t) if x is not None]
    if not known:
        raise AskUser("전사 결과와 원고가 전혀 일치하지 않습니다. narration.mp3와 script.txt가 같은 글인지 확인이 필요합니다.")
    for i in range(len(t)):
        if t[i] is None:
            prev = max((k for k in known if k < i), default=None)
            nxt = min((k for k in known if k > i), default=None)
            if prev is None:
                t[i] = (t[nxt][0], t[nxt][0])
            elif nxt is None:
                t[i] = (t[prev][1], t[prev][1])
            else:
                f = (i - prev) / (nxt - prev)
                x = t[prev][1] + f * (t[nxt][0] - t[prev][1])
                t[i] = (x, x)

    spans = []
    for k, s in enumerate(sentences):
        idx = [i for i, o in enumerate(owner) if o == k]
        spans.append({"text": s, "chars": [t[i] for i in idx],
                      "start": t[idx[0]][0] if idx else None,
                      "end": t[idx[-1]][1] if idx else None})
    return spans, ratio


def wrap2(text):
    """한 줄이면 그대로, 길면 가운데 근처 공백에서 두 줄로."""
    if len(text) <= LINE_CHARS:
        return text
    spaces = [m.start() for m in re.finditer(" ", text)]
    if not spaces:
        return text
    # 쉼표 뒤 공백을 우선하되, 줄 길이가 너무 치우치지 않는 범위 안에서 고른다
    def cost(i):
        if max(i, len(text) - i - 1) > LINE_CHARS + 2:
            return 1000 + abs(i - len(text) / 2)
        return abs(i - len(text) / 2) - (8 if text[i - 1] in ",，" else 0)
    cut = min(spaces, key=cost)
    return text[:cut] + r"\N" + text[cut + 1:]


def chunk_sentence(span):
    """두 줄(약 2×LINE_CHARS)을 넘는 문장은 쉼표·공백에서 나눠 여러 자막으로."""
    text = span["text"]
    limit = LINE_CHARS * 2
    if len(text) <= limit:
        return [(text, span["start"], span["end"])]
    pieces, cur = [], ""
    for tok in re.split(r"(?<=[,，])\s*|\s+", text):
        cand = (cur + " " + tok).strip() if cur else tok
        if len(cand) > limit and cur:
            pieces.append(cur)
            cur = tok
        else:
            cur = cand
    if cur:
        pieces.append(cur)
    out, pos = [], 0
    for p in pieces:
        n = len(norm_chars(p))
        cs = span["chars"][pos:pos + n]
        pos += n
        out.append((p, cs[0][0], cs[-1][1]))
    return out


def ass_time(t):
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def srt_time(t):
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{FONT},54,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,1,0,1,0,2.5,2,160,160,70,1
Style: End,{FONT},84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,2,0,1,0,3,5,160,160,0,1
Style: Title,{FONT},150,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,12,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_subtitles(spans, offset, work, out_srt):
    """하단 자막(.ass/.srt)과 마지막 문장 중앙 자막을 만든다. 반환: 마지막 문장 낭독 종료 시각(영상 기준)."""
    last = spans[-1]
    if norm_chars(last["text"]) != norm_chars(LAST_SENTENCE):
        raise AskUser(f"script.txt의 마지막 문장이 지정 문장과 다릅니다:\n  원고: {last['text']}\n  지정: {LAST_SENTENCE}")
    body = spans[:-1]

    cues = []
    for sp in body:
        cues += chunk_sentence(sp)
    events, srt = [], []
    for n, (text, s, e) in enumerate(cues):
        start = s + offset - 0.1
        end = e + offset + 0.35  # 문장이 끝난 뒤 살짝 남겼다가 사라짐
        if n + 1 < len(cues):
            end = min(end, cues[n + 1][1] + offset - 0.15)
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Sub,,0,0,0,,{{\\fad(200,450)}}{wrap2(text)}")
        srt.append(f"{n+1}\n{srt_time(start)} --> {srt_time(end)}\n{wrap2(text).replace(chr(92)+'N', chr(10))}\n")

    end_start = last["start"] + offset - 0.1
    end_spoken = last["end"] + offset
    end_out = end_spoken + END_HOLD + END_FADE
    fade_in_ms = 800
    fade_out_ms = int(END_FADE * 1000)
    events.append(f"Dialogue: 1,{ass_time(end_start)},{ass_time(end_out)},End,,0,0,0,,"
                  f"{{\\fad({fade_in_ms},{fade_out_ms})}}{wrap2(last['text'])}")
    srt.append(f"{len(cues)+1}\n{srt_time(end_start)} --> {srt_time(end_out)}\n{last['text']}\n")

    (work / "subs.ass").write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")
    out_srt.write_text("\n".join(srt), encoding="utf-8")
    return end_spoken


# ───────────────────────── 2. 얼굴 검사 ─────────────────────────

def face_report(clips, threshold):
    import cv2
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    flagged = []
    for c in clips:
        frames = []
        if c.suffix.lower() in IMAGE_EXT:
            img = cv2.imread(str(c))
            if img is not None:
                frames.append((0.0, img))
        else:
            cap = cv2.VideoCapture(str(c))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            for f in range(0, n, max(int(fps), 1)):
                cap.set(cv2.CAP_PROP_POS_FRAMES, f)
                ok, img = cap.read()
                if ok:
                    frames.append((f / fps, img))
            cap.release()
        best = (0.0, None)
        for ts, img in frames:
            h, w = img.shape[:2]
            scale = 960 / max(w, h)
            small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            sh, sw = gray.shape
            for (x, y, fw, fh) in det.detectMultiScale(gray, 1.1, 6, minSize=(40, 40)):
                ratio = fw * fh / (sw * sh)
                if ratio > best[0]:
                    best = (ratio, ts)
        if best[0] >= threshold:
            flagged.append((c.name, best[0], best[1]))
    return flagged


# ───────────────────────── 3. 컷 배치 ─────────────────────────

def plan_cuts(clips, total):
    """total = 최종 영상 길이. 타이틀(TITLE_SEC+XF) 뒤에 컷 N개를 1s 디졸브로 잇는다.
    길이 관계: TITLE_SEC+XF + Σd - N·XF = total  →  Σd = total - TITLE_SEC - XF + N·XF"""
    srcs = []
    for c in clips:
        if c.suffix.lower() in VIDEO_EXT:
            d, _, _ = probe(c)
            srcs.append({"path": c, "kind": "video", "len": d, "cuts": 1})
        else:
            srcs.append({"path": c, "kind": "image", "len": None, "cuts": 1})

    def bounds(s):
        if s["kind"] == "image":
            return [(CUT_MIN, CUT_MAX)]
        per = s["len"] / s["cuts"]
        return [(min(CUT_MIN, per), min(CUT_MAX, per))] * s["cuts"]

    def need(n):
        return total - TITLE_SEC - XF + n * XF

    # 소스가 모자라면 긴 영상을 여러 컷으로 나눠 쓴다 (반복은 하지 않음)
    while True:
        b = [x for s in srcs for x in bounds(s)]
        if sum(hi for _, hi in b) >= need(len(b)):
            break
        cand = [s for s in srcs if s["kind"] == "video" and s["len"] / (s["cuts"] + 1) >= CUT_MIN]
        if not cand:
            raise AskUser(
                f"소스가 부족합니다: 컷 {len(b)}개를 모두 최대 {CUT_MAX:.0f}초로 써도 "
                f"{sum(hi for _, hi in b) - len(b)*XF + TITLE_SEC + XF:.1f}초로, 필요한 {total:.1f}초에 못 미칩니다. "
                "사진/영상을 반복 사용할지, 컷 길이를 8초 이상으로 늘릴지 정해 주세요.")
        max(cand, key=lambda s: s["len"] / (s["cuts"] + 1))["cuts"] += 1

    b = [x for s in srcs for x in bounds(s)]
    target = need(len(b))
    if sum(lo for lo, _ in b) > target:
        raise AskUser(
            f"소스가 너무 많습니다: {len(b)}컷을 모두 {CUT_MIN:.0f}초로 써도 {total:.1f}초를 넘습니다. "
            "일부 클립을 뺄지, 5초보다 짧은 컷을 허용할지 정해 주세요.")

    # 물 채우기: 모든 컷을 하한에서 시작해 상한까지 고르게 늘린다
    durs = [lo for lo, _ in b]
    remain = target - sum(durs)
    while remain > 1e-6:
        open_ = [i for i, (lo, hi) in enumerate(b) if durs[i] < hi - 1e-9]
        step = min(remain / len(open_), min(b[i][1] - durs[i] for i in open_))
        for i in open_:
            durs[i] += step
        remain -= step * len(open_)

    cuts, k = [], 0
    for s in srcs:
        for j in range(s["cuts"]):
            cut = {"path": s["path"], "kind": s["kind"], "dur": durs[k]}
            if s["kind"] == "video":
                portion = s["len"] / s["cuts"]
                cut["ss"] = j * portion + max(0.0, (portion - durs[k]) / 2)
            cuts.append(cut)
            k += 1
    short = [c for c in cuts if c["dur"] < CUT_MIN - 0.05]
    return cuts, short


# ───────────────────────── 4. 렌더 ─────────────────────────

def frame_filter(w, h, upscale):
    """원본 비율이 16:9와 비슷하면 꽉 채워 자르고, 아니면 흐린 배경 위에 전체를 얹는다."""
    OW, OH = W * upscale, H * upscale
    if w and h and abs((w / h) / (W / H) - 1) < 0.12:
        return (f"scale={OW}:{OH}:force_original_aspect_ratio=increase,"
                f"crop={OW}:{OH},setsar=1")
    return (f"split[a][b];[a]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
            f"gblur=sigma=40,eq=brightness=-0.08[bg];[b]scale={OW}:{OH}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")


def render_cut(cut, out):
    d = cut["dur"]
    frames = int(round(d * FPS))
    _, w, h = probe(cut["path"])
    if cut["kind"] == "image":
        # 켄 번스 줌인: 1.00 → 1.08, 2배 해상도에서 계산해 떨림을 줄인다
        z = f"1+0.08*on/{frames}"
        vf = (f"{frame_filter(w, h, 2)},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={W}x{H}:fps={FPS},{GRADE},format=yuv420p")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(cut["path"]),
               "-filter_complex", vf, "-frames:v", str(frames)]
    else:
        vf = f"{frame_filter(w, h, 1)},fps={FPS},{GRADE},format=yuv420p,tpad=stop_mode=clone:stop_duration=1"
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{cut['ss']:.3f}", "-i", str(cut["path"]),
               "-filter_complex", vf, "-frames:v", str(frames), "-an"]
    run(cmd + ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-r", str(FPS), str(out)])


def render_title(work, title, out):
    ass = work / "title.ass"
    ass.write_text(ASS_HEADER + f"Dialogue: 0,0:00:00.00,0:00:10.00,Title,,0,0,0,,{{\\fad(1500,0)}}{title}\n",
                   encoding="utf-8")
    d = TITLE_SEC + XF
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r={FPS}:d={d}",
         "-vf", f"ass={ass}:fontsdir=/usr/share/fonts/truetype/nanum,format=yuv420p",
         "-c:v", "libx264", "-preset", "fast", "-crf", "16", str(out)])


def xfade_concat(segs, durs, out):
    inputs, fc = [], []
    for s in segs:
        inputs += ["-i", str(s)]
    prev, acc = "[0:v]", durs[0]
    for i in range(1, len(segs)):
        off = acc - XF
        lab = f"[x{i}]"
        fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XF}:offset={off:.3f}{lab}")
        prev, acc = lab, acc + durs[i] - XF
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(fc),
         "-map", prev, "-c:v", "libx264", "-preset", "fast", "-crf", "16",
         "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])
    return acc


def mix_audio(narration, bgm, total, out, bgm_vol):
    delay = int(TITLE_SEC * 1000)
    fc = (
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,adelay={delay}|{delay},"
        f"apad,atrim=0:{total:.3f},asplit=2[narr][key];"
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},volume={bgm_vol},"
        f"afade=t=in:st=0:d=3,afade=t=out:st={total-5:.3f}:d=5[bgm];"
        # 내레이션이 나오면 BGM을 추가로 약 6~8dB 더 낮춤
        f"[bgm][key]sidechaincompress=threshold=0.02:ratio=5:attack=30:release=800:makeup=1[duck];"
        f"[narr][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
    )
    run(["ffmpeg", "-y", "-v", "error", "-i", str(narration), "-stream_loop", "-1", "-i", str(bgm),
         "-filter_complex", fc, "-map", "[a]", "-c:a", "pcm_s16le", str(out)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--model", default="medium", help="faster-whisper 모델 이름 또는 로컬 경로")
    ap.add_argument("--transcript", type=Path, help="이미 만든 전사 JSON([{w,s,e},...]) 사용")
    ap.add_argument("--title", default="「대추」")
    ap.add_argument("--bgm-volume", type=float, default=0.20)
    ap.add_argument("--face-threshold", type=float, default=0.04,
                    help="얼굴이 화면 면적의 이 비율 이상이면 '크게 나옴'으로 보고")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    p = a.project
    narration, script, bgm, clip_dir = p / "narration.mp3", p / "script.txt", p / "bgm.mp3", p / "clips"
    missing = [str(f) for f in (narration, script, bgm, clip_dir) if not f.exists()]
    if missing:
        sys.exit("필수 소스 없음: " + ", ".join(missing))
    clips = sorted((c for c in clip_dir.iterdir() if c.suffix.lower() in IMAGE_EXT | VIDEO_EXT),
                   key=natural_key)
    work = p / "work"
    (work / "cuts").mkdir(parents=True, exist_ok=True)

    try:
        print("▶ 얼굴 검사")
        faces = face_report(clips, a.face_threshold)
        (work / "faces.json").write_text(json.dumps(faces, ensure_ascii=False, indent=1), encoding="utf-8")
        for name, ratio, ts in faces:
            print(f"  ⚠ {name}: 얼굴이 화면의 {ratio*100:.0f}% (t={ts:.0f}s)")
        if not faces:
            print("  크게 나오는 얼굴 없음")

        print("▶ 전사 및 원고 대조")
        tjson = work / "transcript.json"
        if a.transcript:
            words = json.loads(a.transcript.read_text(encoding="utf-8"))
        elif tjson.exists():
            words = json.loads(tjson.read_text(encoding="utf-8"))
        else:
            words = transcribe(narration, a.model, tjson)
        sentences = split_sentences(script.read_text(encoding="utf-8"))
        spans, ratio = align(sentences, words)
        print(f"  문장 {len(sentences)}개, 원고-전사 글자 일치율 {ratio*100:.1f}%")
        if ratio < 0.6:
            raise AskUser(f"원고와 전사의 일치율이 {ratio*100:.0f}%로 낮습니다. 낭독이 원고와 많이 다른지 확인이 필요합니다.")
        (work / "alignment.json").write_text(json.dumps(
            [{"text": s["text"], "start": round(s["start"], 2), "end": round(s["end"], 2)} for s in spans],
            ensure_ascii=False, indent=1), encoding="utf-8")
        end_spoken = build_subtitles(spans, TITLE_SEC, work, p / "subtitles.srt")

        narr_len, _, _ = probe(narration)
        total = max(TITLE_SEC + narr_len, end_spoken + END_HOLD + END_FADE)
        print(f"  내레이션 {narr_len:.1f}s, 마지막 문장 종료 {end_spoken:.1f}s → 최종 길이 {total:.1f}s")

        print("▶ 컷 배치")
        cuts, short = plan_cuts(clips, total)
        for c in cuts:
            extra = f" ({c['ss']:.1f}s부터)" if c["kind"] == "video" else ""
            print(f"  {c['path'].name:<28} {c['dur']:.1f}s{extra}")
        if short:
            raise AskUser("5초보다 짧을 수밖에 없는 영상 컷이 있습니다: " +
                          ", ".join(f"{c['path'].name}({c['dur']:.1f}s)" for c in short) +
                          ". 그대로 쓸지, 느리게 늘릴지, 뺄지 정해 주세요.")
        (work / "plan.json").write_text(json.dumps(
            [{**c, "path": c["path"].name} for c in cuts], ensure_ascii=False, indent=1), encoding="utf-8")
        if a.check_only:
            return

        print("▶ 렌더")
        segs, durs = [work / "cuts" / "000_title.mp4"], [TITLE_SEC + XF]
        render_title(work, a.title, segs[0])
        for n, c in enumerate(cuts, 1):
            seg = work / "cuts" / f"{n:03}.mp4"
            print(f"  [{n}/{len(cuts)}] {c['path'].name}")
            render_cut(c, seg)
            segs.append(seg)
            durs.append(c["dur"])
        picture = work / "picture.mp4"
        vlen = xfade_concat(segs, durs, picture)
        print(f"  영상 트랙 {vlen:.2f}s")

        print("▶ 오디오 믹스")
        mix = work / "mix.wav"
        mix_audio(narration, bgm, total, mix, a.bgm_volume)

        print("▶ 자막 합성 및 최종 인코딩")
        fade_st = total - END_FADE
        run(["ffmpeg", "-y", "-v", "error", "-i", str(picture), "-i", str(mix),
             "-vf", f"ass={work/'subs.ass'}:fontsdir=/usr/share/fonts/truetype/nanum,"
                    f"fade=t=out:st={fade_st:.3f}:d={END_FADE},format=yuv420p",
             "-map", "0:v", "-map", "1:a", "-t", f"{total:.3f}",
             "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-r", str(FPS),
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(p / "output.mp4")])
        d, w, h = probe(p / "output.mp4")
        print(f"✔ 완성: {p/'output.mp4'}  {d:.2f}s  {w}x{h}")
    except AskUser as e:
        print(f"\n⏸ 확인 필요 — 임의로 진행하지 않고 멈춥니다.\n{e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
