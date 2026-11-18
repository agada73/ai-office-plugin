# 사무실 그림 뽑기 도구 (복도 중심 마주보기 배치)
# 참고_시안의 v4·v5 그림 도우미(블록 인형, 책상, 창문, 책장 등)를 불러와서
# 복도를 가운데 두고 위·아래 방이 마주 보는 사무실을 그리고,
# 브라우저에서 직원이 걷고 앉을 수 있도록 "배경 / 직원 그림 / 책상 앞면"을 따로 뽑아 scene.json 으로 저장합니다.
# 사용: python3 -X utf8 tools/build_scene.py
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "참고_시안")
OUT = os.path.join(ROOT, "app", "web", "scene.json")

roster = json.load(open(os.path.join(ROOT, "app", "roster.json"), encoding="utf-8"))
EMP = {e["name"]: e for e in roster["employees"]}
FLOOR = 1      # 이 그림이 몇 층인지 (roster.json 의 floor 가 같은 직원만 그림)

# ---------- 시안의 그리기 도우미 불러오기 ----------
ns = {"__file__": os.path.join(SRC, "make_office_v6.py"), "__name__": "scene"}
sys.argv = [sys.argv[0], os.devnull]  # v4 코드가 sys.argv[1]을 읽기 때문
v4 = open(os.path.join(SRC, "make_office_v4.py"), encoding="utf-8").read()
exec(compile(v4.split("# ---------- 사무실 배치")[0], "v4", "exec"), ns)
v5 = open(os.path.join(SRC, "make_office_v5.py"), encoding="utf-8").read()
exec(compile(v5.split("ROOMS = [")[0].split("exec(_src")[1].split("\n", 1)[1], "v5", "exec"), ns)
exec("defs = '''" + v5.split("defs = '''")[1].split("'''")[0] + "'''", ns)

T = ns["T"]
R, window, shelf, plant, desk_set = ns["R"], ns["window"], ns["shelf"], ns["plant"], ns["desk_set"]
figure, LOOKS, hair_svg, acc_svg, SKIN, SKIN_LINE = ns["figure"], ns["LOOKS"], ns["hair_svg"], ns["acc_svg"], ns["SKIN"], ns["SKIN_LINE"]
legs_svg = ns["legs_svg"]
defs = ns["defs"]
LOOKS["영상 검토 담당"] = dict(hair="ponytail", hc="#3b2414", shirt="#e84393", pants="#40407a", top="plain", face="neutral", acc=["headphones"])

MW, MH = 30, 18.8      # 맨 아래에 건물 앞면(이름표 벽)을 그릴 자리를 조금 더함
FH = .75               # 아래쪽 벽 앞면의 높이(칸)
HALL_Y0, HALL_Y1 = 7, 11          # 복도(가운데 넓은 공용 홀)
LANE_TOP, LANE_BOTTOM = 8.0, 10.0  # 직원이 복도에서 걷는 줄
CONNECTOR_X = 13.5


# ---------- 식물 9종 (방마다 다르게) ----------
def _g(x, y, s, body):
    return (f'<g transform="translate({x*T:.1f},{y*T:.1f}) scale({s})"><ellipse cx="0" cy="0" rx="10" ry="3.5" fill="rgba(0,0,0,.15)"/>{body}</g>')


def p_cactus(x, y, s=1.2):
    return _g(x, y, s, '<path d="M-8 -12 L8 -12 L6 0 L-6 0 Z" fill="#f4f1ea" stroke="#d6d0c4"/><rect x="-9" y="-14" width="18" height="3.5" rx="1" fill="#e6e0d2"/>'
                       '<rect x="-4.5" y="-38" width="9" height="26" rx="4.5" fill="#3f9b4b"/><path d="M-1.5 -36 V-14 M1.5 -36 V-14" stroke="#2e7d32" stroke-width=".8"/>'
                       '<path d="M4 -26 H9 Q12 -26 12 -29 V-33" stroke="#3f9b4b" stroke-width="5" fill="none" stroke-linecap="round"/>'
                       '<path d="M-4 -22 H-9 Q-12 -22 -12 -25 V-28" stroke="#3f9b4b" stroke-width="5" fill="none" stroke-linecap="round"/>'
                       '<circle cx="0" cy="-39" r="3.2" fill="#ff6fa5"/><circle cx="0" cy="-39" r="1.2" fill="#ffe082"/>')


def p_monstera(x, y, s=1.2):
    leaf = '<path d="M0 0 C-12 -4 -14 -18 -4 -24 C2 -20 4 -26 10 -22 C16 -14 10 -2 0 0 Z" fill="#2e7d32"/><path d="M0 0 L-2 -20 M-6 -16 L-1 -14 M-8 -9 L-2 -8 M5 -16 L-1 -14" stroke="#1b5e20" stroke-width="1"/>'
    return _g(x, y, s, '<path d="M-8 -12 L8 -12 L6 0 L-6 0 Z" fill="#78909c"/><rect x="-9" y="-14" width="18" height="3.5" rx="1" fill="#607d8b"/>'
                       f'<g transform="translate(-4,-14) rotate(-28)">{leaf}</g><g transform="translate(4,-14) rotate(26) scale(.85)">{leaf}</g>'
                       f'<g transform="translate(0,-14) scale(1.05)">{leaf}</g>')


def p_flower(x, y, s=1.2):
    heads = "".join(f'<circle cx="{cx}" cy="{cy}" r="4.2" fill="{c}"/><circle cx="{cx}" cy="{cy}" r="1.6" fill="#ffd54f"/>'
                    for cx, cy, c in [(-7, -30, "#ff6fa5"), (0, -36, "#fff"), (7, -29, "#ff9f43"), (-2, -26, "#f48fb1")])
    return _g(x, y, s, '<path d="M-8 -12 L8 -12 L6 0 L-6 0 Z" fill="#f4c542"/><rect x="-9" y="-14" width="18" height="3.5" rx="1" fill="#e1ad2a"/>'
                       '<path d="M-6 -14 L-7 -29 M0 -14 L0 -35 M6 -14 L7 -28 M-1 -14 L-2 -25" stroke="#43a047" stroke-width="1.6"/>'
                       '<path d="M-3 -20 Q-9 -22 -8 -17 Z M3 -22 Q9 -24 8 -18 Z" fill="#66bb6a"/>' + heads)


def p_bamboo(x, y, s=1.2):
    st = "".join(f'<rect x="{cx}" y="{top}" width="3.2" height="{-top-12}" rx="1.4" fill="#9ccc65"/>'
                 + "".join(f'<rect x="{cx-.3}" y="{ny}" width="3.8" height="1.2" fill="#689f38"/>' for ny in range(top + 8, -12, 9))
                 for cx, top in [(-6, -34), (-1.5, -44), (3, -38), (7, -30)])
    lv = '<path d="M5 -44 Q14 -46 16 -40 Q9 -40 5 -44 Z M-3 -36 Q-12 -38 -13 -32 Q-6 -32 -3 -36 Z" fill="#7cb342"/>'
    return _g(x, y, s, st + lv + '<path d="M-8 -12 L8 -12 L7 0 L-7 0 Z" fill="#455a64"/><rect x="-9" y="-14" width="18" height="3.5" rx="1" fill="#37474f"/>')


def p_fern(x, y, s=1.2):
    fr = "".join(f'<path d="M0 -14 Q{dx*.6} {-30-h*.2} {dx} {-14-h}" stroke="#4caf50" stroke-width="3.2" fill="none" stroke-linecap="round"/>'
                 f'<path d="M0 -14 Q{dx*.6} {-30-h*.2} {dx} {-14-h}" stroke="#a5d6a7" stroke-width="1" fill="none" stroke-dasharray="2 2"/>'
                 for dx, h in [(-14, 10), (-9, 22), (-3, 28), (4, 24), (10, 20), (15, 9)])
    return _g(x, y, s, fr + '<path d="M-8 -12 L8 -12 L6 0 L-6 0 Z" fill="#4f83cc"/><rect x="-9" y="-14" width="18" height="3.5" rx="1" fill="#3a6db5"/>')


def p_palm(x, y, s=1.2):
    fronds = "".join(f'<path d="M0 -46 Q{dx*.5} {-56+dy} {dx} {-42+dy*1.5}" stroke="#2e7d32" stroke-width="4.5" fill="none" stroke-linecap="round"/>'
                     for dx, dy in [(-18, 2), (-12, -6), (0, -9), (12, -6), (18, 2), (-8, 6), (9, 6)])
    return _g(x, y, s, '<path d="M-9 -12 L9 -12 L8 0 L-8 0 Z" fill="#c8a46a"/><path d="M-8 -9 H8 M-8 -5 H8" stroke="#a9854b" stroke-width="1"/>'
                       '<path d="M0 -12 Q-3 -30 0 -46" stroke="#795548" stroke-width="4" fill="none"/>' + fronds)


def p_topiary(x, y, s=1.2):
    return _g(x, y, s, '<path d="M-8 -12 L8 -12 L6 0 L-6 0 Z" fill="#fafafa" stroke="#e0e0e0"/><rect x="-9" y="-14" width="18" height="3.5" rx="1" fill="#eeeeee"/>'
                       '<rect x="-1.5" y="-28" width="3" height="16" fill="#8d6e63"/><circle cx="0" cy="-36" r="11" fill="#7cb342"/>'
                       '<circle cx="-4" cy="-39" r="4" fill="#9ccc65"/><circle cx="4" cy="-33" r="3" fill="#689f38"/>')


def p_succulent(x, y, s=1.0):
    lv = "".join(f'<path d="M0 -12 L{a} {b} L{a*.45} -12 Z" fill="{c}"/>' for a, b, c in
                 [(-9, -22, "#4db6ac"), (9, -22, "#4db6ac"), (-5, -27, "#80cbc4"), (5, -27, "#80cbc4"), (0, -29, "#a7ffeb")])
    return _g(x, y, s, '<path d="M-6 -10 L6 -10 L5 0 L-5 0 Z" fill="#d7a37a"/><rect x="-7" y="-12" width="14" height="3" rx="1" fill="#c78f63"/>' + lv)


def p_bonsai(x, y, s=1.2):
    return _g(x, y, s, '<rect x="-11" y="-6" width="22" height="6" rx="2" fill="#37474f"/><rect x="-12" y="-8" width="24" height="3" rx="1" fill="#455a64"/>'
                       '<path d="M0 -8 Q-6 -18 2 -24 Q8 -30 0 -34" stroke="#6d4c41" stroke-width="3.5" fill="none" stroke-linecap="round"/>'
                       '<ellipse cx="-8" cy="-26" rx="8" ry="5" fill="#558b2f"/><ellipse cx="7" cy="-34" rx="9" ry="5.5" fill="#689f38"/><ellipse cx="-1" cy="-40" rx="6" ry="4" fill="#7cb342"/>')


PLANTS = dict(tree=lambda x, y: plant(x, y, True), cactus=p_cactus, monstera=p_monstera, flower=p_flower, bamboo=p_bamboo,
              fern=p_fern, palm=p_palm, topiary=p_topiary, succulent=p_succulent, bonsai=p_bonsai)


# ---------- 뒷모습 직원 (복도를 보고 앉은 모습) ----------
def figure_back(name):
    L = LOOKS[name]
    hc = L["hc"]
    acc = acc_svg(["headphones"] if "headphones" in L["acc"] else [])
    return f'''
      <ellipse cx="0" cy="0" rx="11" ry="4" fill="rgba(0,0,0,.18)"/>
      {legs_svg(L)}
      <path d="M-10 -17 L-7.5 -33 L7.5 -33 L10 -17 Z" fill="{L["shirt"]}" stroke="rgba(0,0,0,.12)" stroke-width=".8"/>
      <path d="M0 -32 L0 -18" stroke="rgba(0,0,0,.12)" stroke-width=".8"/>
      <path d="M-7.5 -33 L-12.5 -21 L-9.5 -20 L-6 -29 Z" fill="{L["shirt"]}" stroke="rgba(0,0,0,.12)" stroke-width=".6"/>
      <path d="M7.5 -33 L12.5 -21 L9.5 -20 L6 -29 Z" fill="{L["shirt"]}" stroke="rgba(0,0,0,.12)" stroke-width=".6"/>
      <circle cx="-11.5" cy="-19" r="2.6" fill="{SKIN}" stroke="{SKIN_LINE}" stroke-width=".7"/>
      <circle cx="11.5" cy="-19" r="2.6" fill="{SKIN}" stroke="{SKIN_LINE}" stroke-width=".7"/>
      <rect x="-3.5" y="-36" width="7" height="3.5" fill="{SKIN}"/>
      <rect x="-8" y="-50" width="16" height="15" rx="4" fill="{hc}" stroke="rgba(0,0,0,.2)" stroke-width=".8"/>
      <path d="M-5 -47 Q0 -44 5 -47" stroke="rgba(255,255,255,.18)" stroke-width="1.4" fill="none"/>
      {hair_svg(L["hair"], hc)}
      {acc}'''


# ---------- 방 배치 ----------
# 이름, 색, x, y, w, h, 출입구(x 범위), 문이 있는 쪽, 바닥
# 위 줄 방은 아래 벽에, 아래 줄 방은 위 벽에 출입구가 있어 모두 복도로 열려요.
ROOMS = [
    ("기획실", "#7c5cff", 0, 0, 7, 7, (2.5, 4.5), "bottom", "wood"),
    ("아이디어팀", "#ff9f43", 7, 0, 8, 7, (10, 12), "bottom", "wood"),
    ("개발팀", "#2ecc71", 15, 0, 8, 7, (18, 20), "bottom", "wood"),
    ("☕ 휴게실", "#43a047", 23, 0, 7, 7, (27, 29), "bottom", "green"),
    ("강의팀", "#3498db", 0, 11, 7, 7, (2.5, 4.5), "top", "wood"),
    ("영상팀", "#e84393", 7, 11, 10, 7, (10, 12), "top", "wood"),
    ("검토실", "#636e72", 17, 11, 6, 7, (19, 21), "top", "wood"),
    ("회의실", "#455a64", 23, 11, 7, 7, (25, 27), "top", "gray"),
]
WALL, WALL_LINE, BAND = "#f3e9da", "#c9b597", "#5d4a3c"

floor, walls_front, items, bands, plates = [], [], [], [], []
# 아래 줄(복도 아래쪽 방들)은 따로 모아서, 화면에서 복도 넓이에 맞춰 통째로 아래로 내려요 (현황판 높이에 맞추기)
lfloor, lwalls, litems, lbands, lplates = [], [], [], [], []
hall = []      # 복도 가운데 장식 (복도가 넓어지면 가운데로 옮겨요)
_top = (floor, walls_front, items, bands, plates)
# 바닥 타일은 화면이 복도 넓이에 맞춰 직접 깔아요
lfloor.append(R(0, 18, MW, MH - 18, BAND))   # 건물 바깥 바닥 띠

for name, color, x, y, w, h, door, side, fl in ROOMS:
    a, b = door
    if side == "top":      # 아래 줄 방
        floor, walls_front, items, bands, plates = lfloor, lwalls, litems, lbands, lplates
    else:
        floor, walls_front, items, bands, plates = _top
    floor.append(R(x, y, w, h, f"url(#{fl})"))
    if fl == "wood":
        floor.append(R(x, y, w, h, "url(#wood2)"))
    # 위쪽 벽(안쪽에서 보이는 벽). 아래 줄 방은 이 벽에 출입구가 뚫려 있음
    if side == "top":
        segs = [(x, a), (b, x + w)]
        for s0, s1 in segs:
            walls_front.append(R(s0, y, s1 - s0, 1.5, WALL) + R(s0, y + 1.38, s1 - s0, .12, WALL_LINE))
        gap_fill = "url(#wood)" if fl == "wood" else f"url(#{fl})"
        walls_front.append(R(a, y, b - a, 1.5, gap_fill))
        walls_front.append(R(a - .08, y, .16, 1.5, "#8d6e63") + R(b - .08, y, .16, 1.5, "#8d6e63") + R(a - .08, y, b - a + .16, .14, "#8d6e63"))
        floor.append(R(a + .1, y - .55, b - a - .2, .5, "#bfae92", 'rx="3"'))   # 복도 쪽 현관 매트
    else:
        walls_front.append(R(x, y, w, 1.5, WALL) + R(x, y + 1.38, w, .12, WALL_LINE))
    # 아래쪽 벽의 앞면(복도에서·건물 앞에서 보이는 벽)과 이름표: 이름표는 출입구를 피해 벽이 넓은 쪽에 답
    if side == "bottom":      # 위 줄 방: 복도 쪽 아래 벽, 출입구가 뚫려 있음
        segs = [(x, a), (b, x + w)]
        for s0, s1 in segs:
            walls_front.append(R(s0, y + h, s1 - s0, FH, WALL) + R(s0, y + h + FH - .1, s1 - s0, .1, WALL_LINE))
        walls_front.append(R(a - .08, y + h, .16, FH, "#8d6e63") + R(b - .08, y + h, .16, FH, "#8d6e63"))
        seg = max(segs, key=lambda sg: (sg[1] - sg[0], sg[0]))      # 더 넓은 쪽(같으면 오른쪽)
        pcx, room_w = (seg[0] + seg[1]) / 2, seg[1] - seg[0]
    else:                     # 아래 줄 방: 건물 맨 아래 벽(건물 앞면)
        walls_front.append(R(x, y + h, w, FH, WALL) + R(x, y + h + FH - .1, w, .1, WALL_LINE))
        pcx, room_w = x + w / 2, w
    pw = len(name) * 13 + 22
    fs = 12
    if pw / T > room_w - .2:   # 벽이 좁으면 글자를 줄여서 맞춤
        fs = 10
        pw = len(name) * 11 + 16
    plates.append(f'<rect x="{pcx*T - pw/2:.1f}" y="{(y+h)*T + 4:.1f}" width="{pw}" height="16" rx="4" fill="{color}"/>'
                  f'<text x="{pcx*T:.1f}" y="{(y+h)*T + 16:.1f}" font-size="{fs}" text-anchor="middle" font-weight="800" fill="#fff">{name}</text>')
    # 벽 윗면 띠
    if side == "top":
        bands.append(R(x - .1, y - .1, a - x + .1, .2, BAND) + R(b, y - .1, x + w - b + .1, .2, BAND))
    else:
        bands.append(R(x - .1, y - .1, w + .2, .2, BAND))
    bands.append(R(x - .1, y - .1, .2, h + .2, BAND))
    bands.append(R(x + w - .1, y - .1, .2, h + .2, BAND))
    # 아래쪽 벽
    if side == "bottom":
        items.append((y + h, R(x - .1, y + h - .1, a - x + .1, .2, BAND) + R(b, y + h - .1, x + w - b + .1, .2, BAND)
                      + R(a + .1, y + h + FH - .2, b - a - .2, .5, "#bfae92", 'rx="3"')))   # 복도 쪽 현관 매트
    else:
        items.append((y + h, R(x - .1, y + h - .1, w + .2, .2, BAND)))
floor, walls_front, items, bands, plates = _top



def tag_monitor(svg, name, dx, dy):
    """책상 모니터 화면에 data-mon 이름표를 달아서 브라우저가 상태 색을 입힐 수 있게 함"""
    x, y = f"{(dx + .19) * T:.1f}", f"{(dy - .31) * T:.1f}"
    pat = re.compile(r'(<rect x="%s" y="%s" width="[^"]+" height="[^"]+" )fill="[^"]+"' % (re.escape(x), re.escape(y)))
    return pat.sub(lambda m: m.group(1) + 'data-mon="%s" fill="#475569"' % name, svg)


def desk_north_top(dx, dy, name):
    return (R(dx, dy, 2.2, .75, "#d8ad7f", 'rx="3"') + R(dx, dy + .75, 2.2, .45, "#a8784c", 'rx="2"')
            + R(dx + .7, dy - .45, .9, .62, "#37474f", 'rx="2"') + R(dx + 1.05, dy + .15, .2, .12, "#263238")
            + f'<circle data-mon="{name}" cx="{(dx + 1.15) * T:.1f}" cy="{(dy - .33) * T:.1f}" r="3.4" fill="#475569" stroke="#fff" stroke-width=".8"/>'
            + f'<circle cx="{(dx + 1.95) * T:.1f}" cy="{(dy + .35) * T:.1f}" r="4" fill="#fff" stroke="#c0392b" stroke-width="1.5"/>'
            + R(dx + .15, dy + .2, .5, .38, "#fff", 'rx="1" stroke="#cfd8dc"'))


def chair_back(dx, dy):
    px, py = (dx + 1.4) * T, (dy + 1.85) * T
    return (f'<rect x="{px-13:.1f}" y="{py-21:.1f}" width="26" height="10" rx="5" fill="#4b5563"/>'
            f'<rect x="{px-2:.1f}" y="{py-11:.1f}" width="4" height="7" fill="#374151"/>')


# 직원 자리: 이름 → (책상 왼쪽 x, 책상 y, 앉은 방향, 복도로 나갈 때 지나는 통로 x)
#   앉은 방향 S = 화면 앞쪽을 보고 앉음 / N = 복도(위쪽)를 보고 앉은 뒷모습
# 방마다 책상 위치와 배치를 다르게 했어요 (구석, 엇갈림, 마주 보는 책상 등).
LAYOUT = {
    "기획실장": (1.0, 2.9, "S", 3.7),      # 기획실: 왼쪽 책상 + 오른쪽 원탁
    "아이디어": (7.6, 2.6, "S", 10.3),     # 아이디어팀: 두 책상을 엇갈리게
    "자료조사": (12.0, 3.7, "S", 14.6),
    "앱 개발": (16.0, 2.5, "S", 18.7),     # 개발팀: 마주 보는 책상 + 원탁
    "홈페이지": (16.0, 3.8, "N", 18.7),
    "강의자료": (0.9, 14.5, "N", 3.7),     # 강의팀: 왼쪽 아래 구석 + 원탁
    "대본 작가": (8.0, 13.9, "S", 10.7),   # 영상팀: 마주 보는 책상 + 혼자 앉는 책상
    "이미지 담당": (8.0, 15.15, "N", 10.7),
    "영상 제작": (13.6, 14.6, "N", 13.0),
    "검토 담당": (20.4, 14.5, "N", 19.9),
    "영상 검토 담당": (17.3, 14.5, "N", 19.9),
}
SEATS = {}
front_layers = {}


def place_desk(n):
    dx, dy, face, _ax = LAYOUT[n]
    if face == "S":
        b, _p, t = desk_set(dx, dy, n, None)
        items.append((dy - .5, b))
        items.append((dy + .9, t))
        front_layers[n] = tag_monitor(t, n, dx, dy)
        SEATS[n] = (round(dx + 1.4, 2), round(dy + .55, 2))
    else:
        items.append((dy + .2, desk_north_top(dx, dy, n)))
        front_layers[n] = chair_back(dx, dy)
        SEATS[n] = (round(dx + 1.4, 2), round(dy + 1.85, 2))


def P(kind, x, y, sort=None):
    items.append((sort if sort is not None else y, PLANTS[kind](x, y)))


def stool(cx, cy, c):
    return f'<rect x="{cx*T-2}" y="{cy*T}" width="4" height="9" fill="#555"/><ellipse cx="{cx*T}" cy="{cy*T}" rx="10" ry="5.5" fill="{c}"/>'


def meeting_round(cx, cy, c="#5c6bc0"):
    """두 사람이 마주 앉는 작은 원탁 회의 테이블"""
    s = f'<ellipse cx="{cx*T}" cy="{(cy+.32)*T}" rx="32" ry="8" fill="rgba(0,0,0,.12)"/><rect x="{cx*T-3}" y="{cy*T}" width="6" height="12" fill="#6d4c41"/>'
    s += f'<ellipse cx="{cx*T}" cy="{cy*T}" rx="27" ry="14" fill="#fff8ee" stroke="#c9a77c" stroke-width="2"/>'
    s += R(cx - .5, cy - .22, .5, .34, "#fff", 'rx="1" stroke="#cfd8dc"') + R(cx + .1, cy - .12, .4, .26, "#b2bec3", 'rx="2"')
    s += f'<circle cx="{(cx+.62)*T}" cy="{(cy+.02)*T}" r="4" fill="#fff" stroke="#c0392b" stroke-width="1.5"/>'
    items.append((cy - .25, stool(cx - 1.25, cy - .1, c)))
    items.append((cy + .1, s))
    items.append((cy + .4, stool(cx + 1.25, cy + .1, c)))


# ---------- 위 줄 ----------
walls_front.append(window(.5, 0, 1.8))
items.append((1.5, shelf(5.1, .2)))
place_desk("기획실장")
meeting_round(5.3, 4.9, "#7c5cff")
P("topiary", 6.4, 6.5)

walls_front.append(window(7.4, 0, 1.7))
walls_front.append(R(13.1, .25, 1.8, 1, "#fff", 'rx="2" stroke="#ccc"')
                   + "".join(R(13.2 + i * .42, .38 + (i % 2) * .4, .34, .35, c, 'rx="1"') for i, c in enumerate(["#fff59d", "#f8bbd0", "#b3e5fc", "#c8e6c9"])))
place_desk("아이디어"); place_desk("자료조사")
P("flower", 8.1, 6.5)

walls_front.append(window(15.4, 0, 2.0))
items.append((1.5, shelf(20.3, .2, 2)))
place_desk("앱 개발"); place_desk("홈페이지")
meeting_round(21.3, 4.7, "#2ecc71")
P("cactus", 22.4, 6.5)

# ---------- 휴게실 (위 줄 오른쪽 끝) ----------
walls_front.append(window(23.3, 0, 1.6))
items.append((1.6, R(28.0, .4, 1.9, 1.1, "#bdc3c7", 'rx="3"') + R(28.1, .1, .7, .8, "#2d3436", 'rx="2"') + R(29.0, -.1, .8, 1.2, "#e3f2fd", 'rx="3" stroke="#90caf9"')))
items.append((3.8, R(23.8, 2.4, 3.6, .7, "#5c6bc0", 'rx="6"') + R(23.8, 3.1, 3.6, .7, "#7986cb", 'rx="6"')))
floor.append(R(23.7, 4.1, 3.9, 2.4, "#f3d9b1", 'rx="10"'))
items.append((5.3, R(24.3, 4.6, 2.4, .7, "#c49a6c", 'rx="4"') + R(24.3, 5.3, 2.4, .2, "#a3784e")))
items.append((5.0, R(27.9, 4.4, 1.1, 1, "#ffb142", 'rx="6"')))
items.append((4.2, R(29.0, 2.2, .9, 2.2, "#ff7675", 'rx="3"') + R(29.1, 2.35, .7, 1.3, "#ffeaa7", 'rx="2"')))
items.append((6.2, stool(24.9, 6.25, "#26a69a")))   # 원탁 앞 의자 두 개
items.append((6.2, stool(26.4, 6.25, "#ff9f43")))
P("palm", 23.7, 6.5); P("fern", 29.5, 6.5)

# ---------- 아래 줄 (책상을 출입구에서 멀리 둠) ----------
walls_front, items = lwalls, litems
walls_front.append(window(.4, 11, 1.8))
items.append((12.5, shelf(5, 11.2)))
place_desk("강의자료")
meeting_round(5.5, 14.3, "#3498db")
items.append((17.4, R(4.4, 15.9, 2, 1.4, "#fff", 'rx="3" stroke="#bbb"') + R(4.5, 17.25, .1, .7, "#999") + R(6.2, 17.25, .1, .7, "#999")))
P("bonsai", .7, 17.4)

walls_front.append(window(7.6, 11) + window(13.4, 11))
place_desk("대본 작가"); place_desk("이미지 담당"); place_desk("영상 제작")
items.append((17.2, R(15.9, 16.2, .9, .9, "#2d3436", 'rx="3"') + f'<circle cx="{16.35*T}" cy="{16.65*T}" r="9" fill="#636e72"/><circle cx="{16.35*T}" cy="{16.65*T}" r="4" fill="#b2bec3"/>'))
P("monstera", 12.2, 17.4)

walls_front.append(window(17.3, 11, 1.5))
items.append((12.5, shelf(21.3, 11.2, 1.5)))
place_desk("검토 담당"); place_desk("영상 검토 담당")
items.append((16.6, R(21.9, 12.6, .95, 1.5, "#b0bec5", 'rx="3"') + "".join(R(22.0, 12.75 + i * .46, .75, .38, "#cfd8dc", 'rx="2" stroke="#90a4ae"') for i in range(3))))  # 서류함
P("bamboo", 17.6, 17.4)

# ---------- 회의실 (아래 줄 오른쪽 끝) ----------
walls_front.append(window(23.4, 11, 1.4))
walls_front.append(R(27.3, 11.2, 2.4, 1.05, "#fff", 'rx="2" stroke="#b0bec5"')
                   + f'<path d="M{27.6*T} {12.0*T} L{28.2*T} {11.6*T} L{28.7*T} {11.9*T} L{29.4*T} {11.5*T}" stroke="#e74c3c" stroke-width="2" fill="none"/>')
for cx_ in (24.5, 25.7, 26.9):
    items.append((14.6, R(cx_, 14.2, .8, .6, "#455a64", 'rx="4"')))
items.append((16.4, R(24.2, 14.8, 4.6, 1.5, "#eceff1", 'rx="6"') + R(24.2, 16.3, 4.6, .3, "#cfd8dc")))
for cx_ in (24.5, 25.7, 26.9):
    items.append((17.2, R(cx_, 16.6, .8, .6, "#455a64", 'rx="4"')))
P("topiary", 29.3, 17.4)

walls_front, items = _top[1], _top[2]

# ---------- 공용 복도 ----------
hall.append(R(8, 8.15, 14, 2.6, "#efe3cf", 'rx="14"'))      # 복도 카펫 (위 방 이름표에 붙지 않게 조금 아래로)
hall.append(R(8.3, 8.45, 13.4, 2.0, "none", 'rx="11" stroke="#d9c4a3" stroke-width="2" stroke-dasharray="6 5"'))
hall.append(f'<text x="{15*T}" y="{9.7*T}" font-size="26" font-weight="900" text-anchor="middle" fill="#d9c4a3" letter-spacing="2">{roster["company"]} · 1층</text>')


def bench(x, y, w=2.6, c="#8d6e63"):
    return (R(x, y, w, .35, c, 'rx="4"') + R(x, y + .35, w, .45, "#bcaaa4", 'rx="4"') + R(x + .1, y + .8, .12, .25, "#5d4037") + R(x + w - .22, y + .8, .12, .25, "#5d4037"))


def round_table(cx, cy):
    return (f'<ellipse cx="{cx*T}" cy="{(cy+.25)*T}" rx="22" ry="6" fill="rgba(0,0,0,.12)"/><rect x="{cx*T-3}" y="{cy*T}" width="6" height="10" fill="#6d4c41"/>'
            f'<ellipse cx="{cx*T}" cy="{cy*T}" rx="20" ry="11" fill="#fff8ee" stroke="#c9a77c" stroke-width="2"/>'
            f'<rect x="{cx*T-6}" y="{cy*T-9}" width="6" height="7" rx="1.5" fill="#fff" stroke="#8d6e63"/>')


def stool(cx, cy, c):
    return f'<rect x="{cx*T-2}" y="{cy*T}" width="4" height="9" fill="#555"/><ellipse cx="{cx*T}" cy="{cy*T}" rx="10" ry="5.5" fill="{c}"/>'


items.append((8.5, "".join(R(.3, 7.7 + i * .55, 2.3, .5, "#c8b08a" if i % 2 else "#d9c4a3", 'rx="2"') for i in range(5)) + f'<text x="{1.45*T}" y="{7.55*T}" font-size="13" font-weight="900" text-anchor="middle" fill="#5d4a3c">⬆ 2층</text>'))

litems.append((10.5, PLANTS["fern"](28.8, 10.5)))      # 복도 야자수·하늘색 정수기는 뺌

items.sort(key=lambda t: t[0])
litems.sort(key=lambda t: t[0])



# ---------- 모으기 ----------
def exit_of(n):
    """자리에서 복도 줄까지 걸어 나가는 길 (책상 옆 통로 → 출입구 → 복도)"""
    e = EMP[n]
    ax = LAYOUT[n][3]
    sx, sy = SEATS[n]
    door = e["door"]["x"]
    if e["door"]["side"] == "top":      # 복도 위쪽 방
        return {"pts": [[ax, sy], [ax, 6.2], [door, 6.2], [door, LANE_TOP]], "lane": "top", "x": door}
    return {"pts": [[ax, sy], [ax, 12.9], [door, 12.9], [door, LANE_BOTTOM]], "lane": "bottom", "x": door}


bg_items = "".join(s for _, s in items)
low_items = "".join(s for _, s in litems)
for n, (dx, dy, face, _ax) in LAYOUT.items():
    if face == "S":
        bg_items = tag_monitor(bg_items, n, dx, dy)
        low_items = tag_monitor(low_items, n, dx, dy)
bg = "".join(floor) + "".join(walls_front) + "".join(bands) + "".join(plates) + bg_items
bg_low = "".join(lfloor) + "".join(lwalls) + "".join(lbands) + "".join(lplates) + low_items
hall_svg = "".join(hall)

front = ""
front_low = ""
people = {}
for e in roster["employees"]:
    if e.get("floor", 1) != FLOOR:
        continue
    n = e["name"]
    dx, dy, face, ax = LAYOUT[n]
    if LAYOUT[n][1] > HALL_Y1:
        front_low += front_layers[n]
    else:
        front += front_layers[n]
    team = roster["teams"][e["team"]]
    people[n] = {
        "team": e["team"], "color": team["color"], "seat": list(SEATS[n]), "exit": exit_of(n),
        "figure": figure(n), "seatFigure": figure_back(n) if face == "N" else figure(n), "back": figure_back(n),
        "idleCoffee": figure(n, carry="coffee"), "walk": figure(n, walking=True),
        "walkPaper": figure(n, walking=True, carry="paper"), "walkCoffee": figure(n, walking=True, carry="coffee"),
    }


sys.path.insert(0, HERE)
from themes import ACTIVE, THEMES  # noqa: E402
if ACTIVE:      # 교수님이 고른 색 시안을 바닥·벽·가구에 입힘 (직원 옷 색은 그대로)
    _mp = {k.lower(): v for k, v in THEMES[ACTIVE]["map"].items()}
    _rc = lambda s: re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: _mp.get(m.group(0).lower(), m.group(0)), s)
    defs, bg, front = _rc(defs), _rc(bg), _rc(front)
    bg_low, hall_svg, front_low = _rc(bg_low), _rc(hall_svg), _rc(front_low)

scene = {
    "company": roster["company"], "tile": T, "viewBox": f"-8 -8 {MW * T + 16} {MH * T + 16}",
    "defs": defs, "bg": bg, "front": front, "people": people,
    # 복도 넓히기: 아래 줄(bgLow·frontLow)은 화면에서 extra 칸만큼 내리고, 복도 장식(hall)은 그 절반만 내려요.
    # 직원 위치는 y가 stretch[0]~stretch[1] 사이에서 늘어나고 그 아래는 extra 만큼 내려가요.
    "bgLow": bg_low, "frontLow": front_low, "hall": hall_svg, "size": [MW, MH], "stretch": [LANE_TOP, LANE_BOTTOM],
    "lounge": [[24.7, 3.5], [26.5, 3.5], [24.9, 6.55], [26.4, 6.55], [28.4, 5.45], [27.9, 2.7]],
    "floor": FLOOR, "stairs": [1.45, 9.0],
    "lanes": {"top": LANE_TOP, "bottom": LANE_BOTTOM, "connectorX": CONNECTOR_X, "loungeDoorX": 28, "wallInY": 6.2, "wallInYBottom": 12.9},
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(scene, f, ensure_ascii=False)
print("ok", OUT, round(os.path.getsize(OUT) / 1024), "KB", "직원", len(people), "명")
