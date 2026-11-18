# 사무실 전체 분위기 시안 만들기: 지금 사무실 그림(scene.json)의 색을 바꿔서 3가지 모습을 미리 봄
# 사용: python3 tools/office_themes.py  → 참고_시안/사무실_시안_*.html (그림은 qlmanage 로 뽑음)
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scene = json.load(open(os.path.join(ROOT, "app", "web", "scene.json"), encoding="utf-8"))
OUTDIR = os.path.join(ROOT, "참고_시안")

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from themes import THEMES, recolor  # noqa: E402


def people_svg():
    out = []
    for n, P in scene["people"].items():
        x, y = P["seat"]
        out.append('<g transform="translate(%.1f,%.1f)">%s<text y="15" font-size="11" text-anchor="middle" font-weight="700" '
                   'fill="#3d3027" paint-order="stroke" stroke="#fff" stroke-width="3">%s</text></g>' % (x * 32, y * 32, P["seatFigure"], n))
    return "".join(out)


def page(title, desc, bgc, svg, fg="#fff"):
    return f"""<!doctype html><meta charset="utf-8"><style>
body{{margin:0;background:{bgc};font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;width:800px}}
.h{{padding:12px 16px 6px;color:{fg}}}
.h b{{font-size:17px}} .h span{{font-size:11px;margin-left:8px;opacity:.85}}
svg{{display:block;width:780px;margin:0 10px 10px;border-radius:8px}}
</style><div class="h"><b>{title}</b><span>{desc}</span></div>
<svg viewBox="{scene['viewBox']}" xmlns="http://www.w3.org/2000/svg">{svg}</svg>"""


people = people_svg()
base = scene["defs"] + scene["bg"] + people + scene["front"]
open(os.path.join(OUTDIR, "사무실_시안_0_지금.html"), "w", encoding="utf-8").write(
    page("지금", "지금 쓰는 모습 (비교용)", "#3e3129", base))
for k, th in THEMES.items():
    open(os.path.join(OUTDIR, "사무실_시안_%s.html" % k), "w", encoding="utf-8").write(
        page(th["name"], th["desc"], th["bgcolor"], recolor(base, th["map"]), th.get("fg", "#fff")))
print("ok")
