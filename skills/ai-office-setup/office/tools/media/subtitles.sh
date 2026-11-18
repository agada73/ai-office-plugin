#!/bin/bash
# 🎙 자동 자막 (Whisper, 맥에서 무료로): 영상·소리 파일 → .srt 자막
# 사용: subtitles.sh 입력파일 [출력.srt] [언어(기본 ko)]
set -e
IN="$1"; OUT="${2:-${1%.*}_자동자막.srt}"; LANG_="${3:-ko}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="$HERE/models/ggml-large-v3-turbo-q5_0.bin"
[ -f "$IN" ] || { echo "입력 파일이 없어요: $IN"; exit 1; }
TMP="$(mktemp -d)"
/opt/homebrew/bin/ffmpeg -loglevel error -y -i "$IN" -ar 16000 -ac 1 -c:a pcm_s16le "$TMP/a.wav"
/opt/homebrew/bin/whisper-cli -m "$MODEL" -l "$LANG_" -osrt -of "$TMP/out" "$TMP/a.wav" >/dev/null 2>&1
cp "$TMP/out.srt" "$OUT"; rm -rf "$TMP"
echo "자막을 만들었어요: $OUT"
