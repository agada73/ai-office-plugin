"""카카오톡 '나에게 보내기': 비서 아침 보고 같은 글을 교수님 카톡으로 보냄.

열쇠(REST API 키·토큰)는 코드에 적지 않고 data/kakao.json 에만 둠 (깃허브에 안 올라감).
처음 한 번은 tools/kakao_setup.py 로 카카오 로그인을 해서 data/kakao.json 을 만들어야 함.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import store

CONF = os.path.join(store.DATA, "kakao.json")
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
LIMIT = 200          # 카카오 글 한 통에 들어가는 최대 글자 수
MAX_MSGS = 8         # 너무 길면 이만큼만 보내고 나머지는 사무실에서 보도록


class KakaoError(Exception):
    pass


def ready():
    return os.path.exists(CONF)


def load():
    try:
        with open(CONF, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise KakaoError("카카오 설정(data/kakao.json)이 없어요. 사무실 폴더에서 tools/kakao_setup.py 를 한 번 실행해 주세요.")


def save(conf):
    store.ensure_dirs()
    tmp = CONF + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONF)
    try:
        os.chmod(CONF, 0o600)
    except OSError:
        pass


def _post(url, fields, token=None):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded;charset=utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise KakaoError("카카오 오류 %d: %s" % (e.code, body))
    except urllib.error.URLError as e:
        raise KakaoError("카카오에 연결하지 못했어요: %s" % e.reason)


def apply_token(conf, tok):
    """카카오가 돌려준 토큰을 설정에 넣음 (새 refresh_token 이 오면 그것도 바꿈)"""
    now = time.time()
    conf["access_token"] = tok["access_token"]
    conf["access_expires"] = now + int(tok.get("expires_in", 21599)) - 60
    if tok.get("refresh_token"):
        conf["refresh_token"] = tok["refresh_token"]
        conf["refresh_expires"] = now + int(tok.get("refresh_token_expires_in", 5184000))
    return conf


def access_token():
    conf = load()
    if conf.get("access_token") and time.time() < conf.get("access_expires", 0):
        return conf["access_token"]
    if not conf.get("refresh_token"):
        raise KakaoError("카카오 로그인 정보가 없어요. tools/kakao_setup.py 로 다시 로그인해 주세요.")
    fields = {"grant_type": "refresh_token", "client_id": conf["rest_api_key"], "refresh_token": conf["refresh_token"]}
    if conf.get("client_secret"):
        fields["client_secret"] = conf["client_secret"]
    try:
        tok = _post(TOKEN_URL, fields)
    except KakaoError as ex:
        raise KakaoError("카카오 로그인이 만료됐어요(약 두 달마다). tools/kakao_setup.py 로 다시 로그인해 주세요. (%s)" % ex)
    save(apply_token(conf, tok))
    return conf["access_token"]


def to_plain(md):
    """마크다운 표시(#, **, 표 줄)를 걷어 내 카톡에서 읽기 좋은 글로"""
    out = []
    for line in md.splitlines():
        s = line.rstrip()
        if set(s.strip()) <= set("|-: ") and s.strip():
            continue                      # 표 구분 줄
        s = s.lstrip("#").strip() if s.lstrip().startswith("#") else s
        s = s.replace("**", "").replace("__", "")
        if s.strip().startswith("|"):
            s = " · ".join(c.strip() for c in s.strip().strip("|").split("|") if c.strip())
        out.append(s)
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def split(text, limit=LIMIT):
    """줄 단위로 끊어서 한 통에 limit 자 이하가 되게 나눔"""
    parts, cur = [], ""
    for line in text.splitlines():
        while len(line) > limit:          # 한 줄이 너무 길면 잘라서
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        cand = (cur + "\n" + line) if cur else line
        if len(cand) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = cand
    if cur.strip():
        parts.append(cur)
    return [p for p in parts if p.strip()]


def send_text(text, link_url="", button="사무실에서 보기"):
    """글 한 통 보내기 (200자 이하). link_url 이 있으면 버튼을 붙임 (카카오 앱에 등록한 주소만 열림)"""
    obj = {"object_type": "text", "text": text[:LIMIT],
           "link": ({"web_url": link_url, "mobile_web_url": link_url} if link_url else {})}
    if link_url:
        obj["button_title"] = button
    res = _post(SEND_URL, {"template_object": json.dumps(obj, ensure_ascii=False)}, access_token())
    if res.get("result_code") != 0:
        raise KakaoError("카카오가 보내기를 거절했어요: %s" % res)


def send_report(title, md, link_url=""):
    """보고서를 카톡 여러 통으로 나눠 보냄. 보낸 통 수를 돌려줌"""
    parts = split(to_plain(md), LIMIT - len(title) - 10)      # 머리말 "(1/3)" 자리를 남김
    if not parts:
        parts = [title]
    if len(parts) > MAX_MSGS:
        parts = parts[:MAX_MSGS - 1] + ["(보고가 길어서 나머지는 사무실에서 보세요)"]
    total = len(parts)
    for i, p in enumerate(parts):
        head = "%s (%d/%d)\n" % (title, i + 1, total) if total > 1 else title + "\n"
        body = p if len(head) + len(p) <= LIMIT else p[:LIMIT - len(head)]
        send_text(head + body, link_url if i == total - 1 else "")
        time.sleep(0.4)
    return total
