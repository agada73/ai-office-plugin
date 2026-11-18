#!/bin/bash
# AI 파트너스 사무실 켜기 — 이 파일을 두 번 누르면 사무실이 열려요.
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
PORT=8840
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "사무실이 이미 켜져 있어요. 화면만 열게요."
  open "http://127.0.0.1:$PORT"
  exit 0
fi
[ -f app/web/scene.json ] || python3 -X utf8 tools/build_scene.py
(sleep 2; open "http://127.0.0.1:$PORT") &
exec python3 -X utf8 app/server.py
