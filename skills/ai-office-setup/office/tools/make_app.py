# 바탕화면 아이콘 앱 만들기 도구 (맥 전용)
# tools/office_icon.svg 그림으로 아이콘을 만들고, 두 번 누르면 사무실 화면이 열리는 앱(.app)을 만듭니다.
# 사용: python3 -X utf8 tools/make_app.py [만들 폴더, 기본: ~/Desktop]
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(HERE, "office_icon.svg")
APP_NAME = "AI 사무실"
LABEL = "kr.aioffice.office"
PORT = 8840


def run(*cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_bmp(path):
    d = open(path, "rb").read()
    off = struct.unpack_from("<I", d, 10)[0]
    w, h = struct.unpack_from("<ii", d, 18)
    bpp = struct.unpack_from("<H", d, 28)[0]
    bottom_up = h > 0
    h = abs(h)
    bytespp = bpp // 8
    stride = (w * bytespp + 3) // 4 * 4
    rows = []
    for y in range(h):
        src = off + (h - 1 - y if bottom_up else y) * stride
        row = d[src:src + w * bytespp]
        out = bytearray(w * 4)
        for x in range(w):
            b, g, r = row[x * bytespp], row[x * bytespp + 1], row[x * bytespp + 2]
            out[x * 4:x * 4 + 4] = bytes((r, g, b, 255))
        rows.append(out)
    return w, h, rows


def clear_outside(w, h, rows):
    """네 모서리에서 이어지는 '흰 바깥 부분'을 투명하게"""
    def white(x, y):
        p = rows[y]
        i = x * 4
        return p[i] >= 244 and p[i + 1] >= 244 and p[i + 2] >= 244
    seen = bytearray(w * h)
    stack = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= w or y >= h or seen[y * w + x] or not white(x, y):
            continue
        seen[y * w + x] = 1
        rows[y][x * 4 + 3] = 0
        stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]


def write_png(path, w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_icns(work, out_icns):
    run("qlmanage", "-t", "-s", "1024", "-o", work, SVG)
    png = os.path.join(work, "office_icon.svg.png")
    bmp = os.path.join(work, "icon.bmp")
    run("sips", "-s", "format", "bmp", png, "--out", bmp)
    w, h, rows = read_bmp(bmp)
    clear_outside(w, h, rows)
    big = os.path.join(work, "icon_1024.png")
    write_png(big, w, h, rows)
    iconset = os.path.join(work, "icon.iconset")
    os.makedirs(iconset, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        run("sips", "-z", str(base), str(base), big, "--out", os.path.join(iconset, f"icon_{base}x{base}.png"))
        run("sips", "-z", str(base * 2), str(base * 2), big, "--out", os.path.join(iconset, f"icon_{base}x{base}@2x.png"))
    run("iconutil", "-c", "icns", iconset, "-o", out_icns)


LAUNCH = f"""#!/bin/bash
# AI 파트너스 사무실 열기: 사무실이 꺼져 있으면 켜고, 화면(브라우저)을 엽니다.
PORT={PORT}
URL="http://127.0.0.1:$PORT"
UIDN=$(/usr/bin/id -u)
if ! /usr/sbin/lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  /bin/launchctl kickstart "gui/$UIDN/{LABEL}" >/dev/null 2>&1 || \\
    /bin/launchctl bootstrap "gui/$UIDN" "$HOME/Library/LaunchAgents/{LABEL}.plist" >/dev/null 2>&1
  for i in $(/usr/bin/seq 1 25); do
    /usr/sbin/lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1 && break
    /bin/sleep 1
  done
fi
/usr/bin/open "$URL"
"""


def build(dest_dir):
    app = os.path.join(dest_dir, APP_NAME + ".app")
    if os.path.exists(app):
        shutil.rmtree(app)
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    os.makedirs(os.path.join(app, "Contents", "Resources"))
    with tempfile.TemporaryDirectory() as work:
        make_icns(work, os.path.join(app, "Contents", "Resources", "icon.icns"))
    exe = os.path.join(app, "Contents", "MacOS", "launch")
    with open(exe, "w", encoding="utf-8") as f:
        f.write(LAUNCH)
    os.chmod(exe, 0o755)
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({
            "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": "kr.aioffice.office.launcher", "CFBundleExecutable": "launch",
            "CFBundleIconFile": "icon", "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0",
            "LSUIElement": True,  # 독에 아이콘이 튀지 않게
        }, f)
    os.utime(app, None)
    return app


if __name__ == "__main__":
    dest = os.path.expanduser(sys.argv[1]) if len(sys.argv) > 1 else os.path.expanduser("~/Desktop")
    print("만들었어요:", build(dest))
