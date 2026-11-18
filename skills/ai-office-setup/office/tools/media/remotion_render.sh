#!/bin/bash
# 🎞 Remotion 카드·자막 모션 영상: props.json → mp4 (사무실 틀 tools/remotion_template 사용)
# 사용: remotion_render.sh props.json 출력.mp4
# props.json: {"title":"제목","subtitle":"부제","secondsPerScene":4,"bg":"#f4efe7","accent":"#7c5cff","width":1920,"height":1080,
#              "scenes":[{"heading":"장면 제목","body":"설명","emoji":"📚"}]}   (세로 숏폼은 width 1080, height 1920)
set -e
PROPS="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; OUT="$2"
case "$OUT" in /*) ;; *) OUT="$(pwd)/$OUT";; esac
cd "$(dirname "$0")/../remotion_template"
npx remotion render src/index.ts Slides "$OUT" --props="$PROPS" --log=error
echo "영상을 만들었어요: $OUT"
