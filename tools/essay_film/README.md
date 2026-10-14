# 수필 낭독 영상 조립기

```
daechu/
├─ script.txt      수필 전문 → 자막 초안(.srt)
├─ narration.mp3   낭독 녹음 → 영상 전체 길이의 기준
├─ bgm.mp3         배경음악 → 반복·낭독 중 자동 낮춤(덕킹)·페이드
└─ clips/          영상·사진 → 파일명 순서대로 배치 (01_, 02_ … 접두사 권장)
```

```bash
python3 tools/essay_film/make_essay_film.py daechu               # 자막은 .srt 파일로만
python3 tools/essay_film/make_essay_film.py daechu --burn-subs   # 자막을 화면에 입힘
```

결과물: `daechu/out/daechu.mp4` (1920×1080, 30fps, -16 LUFS), `daechu/out/daechu.srt`

- 길이 = 앞 여백 2초 + 낭독 + 뒤 여백 4초
- 영상 클립은 원본 길이만큼 쓰고(원본 소리는 끔), 남은 시간을 사진들이 나눠 가짐(천천히 확대·축소 효과).
- 소재가 모자라면 처음부터 반복, 영상이 넘치면 비례로 잘라냄.
- 자막 타이밍은 글자 수 비례 추정치. 실제 낭독과 어긋날 수 있으니 검수 필요.
- 주요 옵션: `--bgm-volume 0.25`, `--image-sec 6`, `--fade 0.6`, `--head/--tail`, `--no-duck`, `--font`.
- 필요: ffmpeg(libass 포함), 자막을 입힐 경우 한글 폰트.

## 「대추」 전용: `make_daechu.py`

```bash
pip install faster-whisper "opencv-python-headless==4.10.0.84"   # + apt fonts-nanum
python3 tools/essay_film/make_daechu.py daechu --check-only   # 얼굴 검사·전사·컷 배치 계획만
python3 tools/essay_film/make_daechu.py daechu                # daechu/output.mp4, daechu/subtitles.srt
```

- 3초 타이틀 → 1초 디졸브 → 컷(5~8초, 사진은 줌인, 1초 디졸브), 따뜻하고 채도 낮춘 색감
- faster-whisper 단어 타임스탬프와 script.txt를 글자 단위로 대조해 원문 자막 생성 (최대 2줄, 페이드)
- 마지막 문장은 화면 중앙에 크게, 낭독 후 2초 유지 → 3초 페이드아웃
- 내레이션 100%, BGM 20% + 덕킹, 3초 페이드인 / 5초 페이드아웃
- 소스 부족·과다, 원고 불일치 등은 임의 처리 없이 멈추고 이유를 출력(종료 코드 2)
- 중간 파일은 daechu/work/, 원본은 읽기만 함
