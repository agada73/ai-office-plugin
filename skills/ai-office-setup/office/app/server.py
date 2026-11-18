"""AI 파트너스 사무실 서버 (파이썬 기본 기능만 사용)
실행: python3 -X utf8 app/server.py
기본은 이 컴퓨터에서만 열립니다(127.0.0.1).
다른 컴퓨터에서 열려면 data/settings.json 에 host 와 passcode(접속 비밀번호)를 함께 적어야 합니다.
"""
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.parse
import zipfile
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as eng  # noqa: E402
import store  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ENGINE = None
SETTINGS = None
TOKEN = None
FAILS = {}  # 접속 비밀번호를 틀린 기록 (주소 → 시각들)
ALL_FAILS = []  # 모든 곳에서 틀린 기록 (여러 곳에서 한꺼번에 맞혀 보기를 막음)
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded",
                 "Tailscale-Funnel-Request", "Tailscale-User-Login", "Tailscale-Headers-Info")
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

LOGIN_HTML = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 파트너스 사무실</title>
%%META%%
<body style="font-family:-apple-system,'Malgun Gothic',sans-serif;background:#ece7df;color:#4a4038;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0">
<form onsubmit="go(event)" style="background:#fff;color:#222;padding:28px;border-radius:14px;width:min(90vw,320px);box-shadow:0 4px 20px rgba(74,64,56,.12)">
<h2 style="margin:0 0 6px">🏢 AI 파트너스</h2><p style="margin:0 0 14px;color:#666">접속 비밀번호를 입력해 주세요</p>
<input id="pw" type="password" autofocus style="width:100%;box-sizing:border-box;padding:10px;font-size:16px;border:1px solid #bbb;border-radius:8px">
<button style="margin-top:12px;width:100%;padding:11px;font-size:16px;border:0;border-radius:8px;background:#7c5cff;color:#fff;font-weight:700">들어가기</button>
<p id="m" style="color:#c0392b;min-height:1.2em;margin:8px 0 0"></p></form>
<script>async function go(e){e.preventDefault();const r=await fetch('/api/login',{method:'POST',body:JSON.stringify({passcode:document.getElementById('pw').value})});
if(r.ok)location.reload();else{let t='비밀번호가 맞지 않아요';try{t=(await r.json()).error||t}catch(_){}document.getElementById('m').textContent=t}}</script>"""


PUBLIC_FILES = ("/og.png", "/favicon.png", "/apple-touch-icon.png", "/icon-512.png")   # 미리보기·아이콘 그림만 비밀번호 없이


def meta_tags(host):
    """카톡·문자 미리보기(썸네일)와 탭·홈 화면 아이콘"""
    host = (host or "").strip()
    base = ("https://" if host.split(":")[0].endswith(".ts.net") else "http://") + host if host else ""
    return ('<meta property="og:type" content="website"><meta property="og:title" content="AI 파트너스 사무실">'
            '<meta property="og:description" content="AI 직원들이 함께 일해요">'
                        '<meta name="theme-color" content="#ece7df">'   # 나눔판: 미리보기 그림(og.png)은 각자 만들어 넣으세요
            '<link rel="icon" type="image/png" href="/favicon.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png">')


def make_token(passcode):
    secret_file = os.path.join(store.DATA, "secret.txt")
    if not os.path.exists(secret_file):
        store.ensure_dirs()
        with open(secret_file, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(secret_file, 0o600)
    secret = open(secret_file).read().strip()
    return hmac.new(secret.encode(), passcode.encode(), hashlib.sha256).hexdigest()



def inline_html(fp):
    """홈페이지(html)를 파일 하나로: 옆에 있는 사진·CSS·글꼴을 html 안에 넣음 (11-13)
    → 미리보기 창에서도, 내려받아 다른 컴퓨터에서 열어도 사진이 X 로 나오지 않음"""
    import base64
    import re as _re
    base = os.path.dirname(os.path.realpath(fp))
    html = open(fp, encoding="utf-8", errors="replace").read()
    cache = {}

    def data_uri(ref):
        ref0 = urllib.parse.unquote(ref.split("#")[0].split("?")[0])
        if not ref0 or _re.match(r"^(data:|https?:|//|mailto:|tel:|#|javascript:)", ref, _re.I):
            return None
        p2 = os.path.realpath(os.path.join(base, ref0))
        if not p2.startswith(base + os.sep) or not os.path.isfile(p2) or os.path.getsize(p2) > 15 * 1024 * 1024:
            return None
        if p2 not in cache:
            ct = mimetypes.guess_type(p2)[0] or "application/octet-stream"
            cache[p2] = "data:%s;base64,%s" % (ct, base64.b64encode(open(p2, "rb").read()).decode("ascii"))
        return cache[p2]

    def rep_attr(m):
        u = data_uri(m.group(3))
        return m.group(1) + m.group(2) + (u or m.group(3)) + m.group(2)
    html = _re.sub(r'(\b(?:src|poster|data-src)\s*=\s*)(["\'])([^"\']+)\2', rep_attr, html, flags=_re.I)
    html = _re.sub(r'(<link\b[^>]*\bhref\s*=\s*)(["\'])([^"\']+\.(?:png|jpe?g|gif|webp|svg|ico))\2', rep_attr, html, flags=_re.I)

    def rep_css(m):
        u = data_uri(m.group(2))
        return "url(%s%s%s)" % (m.group(1), u or m.group(2), m.group(1))
    html = _re.sub(r'url\(\s*(["\']?)([^"\')]+)\1\s*\)', rep_css, html)

    def rep_style(m):      # <link rel=stylesheet href="style.css"> → <style>…</style>
        ref = m.group(2)
        p2 = os.path.realpath(os.path.join(base, urllib.parse.unquote(ref)))
        if _re.match(r"^(https?:|//)", ref) or not p2.startswith(base + os.sep) or not os.path.isfile(p2):
            return m.group(0)
        return "<style>\n%s\n</style>" % open(p2, encoding="utf-8", errors="replace").read()
    html = _re.sub(r'<link\b[^>]*rel\s*=\s*["\']?stylesheet["\']?[^>]*href\s*=\s*(["\'])([^"\']+\.css)\1[^>]*>', rep_style, html, flags=_re.I)
    return html.encode("utf-8")

class Handler(BaseHTTPRequestHandler):
    server_version = "AIOffice/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---- 도우미
    def _client_ip(self):
        try:
            return ipaddress.ip_address(self.client_address[0].split("%")[0])
        except ValueError:
            return None

    def _is_direct_local(self):
        """이 맥에서 직접 연 화면인가? 인터넷 주소(Funnel)로 들어온 요청도 맥 안에서 넘겨받기 때문에
        넘겨받은 표시(헤더)가 있거나 주소 이름이 localhost 가 아니면 '바깥'으로 봐요."""
        ip = self._client_ip()
        if ip is None or not ip.is_loopback:
            return False
        if any(self.headers.get(h) for h in PROXY_HEADERS):
            return False
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("localhost", "127.0.0.1", "::1")

    def _login_key(self):
        if self._client_ip() is not None and self._client_ip().is_loopback:
            xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if xff:
                return "fwd:" + xff
        return self.client_address[0]

    def _client_allowed(self):
        """맥 자신(루프백)과 Tailscale 통로(100.64.0.0/10)에서 오는 요청만 허용"""
        ip = self._client_ip()
        if ip is None:
            return False
        if ip.is_loopback:
            return True
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return ip in TAILSCALE_V4 or ip in TAILSCALE_V6

    def _authed(self):
        if self._is_direct_local():
            return True  # 이 맥에서 직접 여는 화면은 비밀번호 없이 (인터넷 주소로 들어온 건 꼭 비밀번호)
        if not SETTINGS.get("passcode"):
            return True
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "office_token" and hmac.compare_digest(v, TOKEN):
                return True
        return False

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return {}

    def _err(self, msg, code=400):
        self._send(code, {"error": msg})

    # ---- GET
    def do_GET(self):
        if not self._client_allowed():
            return self._send(403, "이 기기에서는 열 수 없어요", "text/plain; charset=utf-8")
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        q = urllib.parse.parse_qs(u.query)
        if path in PUBLIC_FILES:
            with open(os.path.join(WEB, path.lstrip("/")), "rb") as f:
                return self._send(200, f.read(), "image/png", {"Cache-Control": "public, max-age=86400"})
        if not self._authed():
            if path.startswith("/api/"):
                return self._err("로그인이 필요해요", 401)
            return self._send(200, LOGIN_HTML.replace("%%META%%", meta_tags(self.headers.get("Host"))), "text/html; charset=utf-8")
        try:
            if path == "/api/state":
                st = ENGINE.state()
                st["here"] = self._is_direct_local()   # 사무실 맥에서 직접 연 화면인지 (아니면 윈도우·휴대폰 등)
                return self._send(200, st)
            if path == "/api/approvals":
                return self._send(200, ENGINE.approvals_list())
            if path.startswith("/api/compare/"):
                return self._send(200, ENGINE.compare_view(path.split("/")[3]))
            if path == "/api/claude_projects":
                return self._send(200, {**ENGINE.claude_projects(), "syncing": bool(getattr(ENGINE, "_cp_syncing", False))})
            if path == "/api/projects":
                return self._send(200, ENGINE.projects_board())
            if path == "/api/reports":
                return self._send(200, ENGINE.reports_list())
            if path.startswith("/api/task/"):
                t = ENGINE.get_task(path.split("/")[3])
                if not t:
                    return self._err("그런 일이 없어요", 404)
                t = dict(t)
                t["files"] = ENGINE.task_files(t)
                t.update(ENGINE.outputs(t))
                t["timeline"] = ENGINE.timeline(t)
                t["now"] = ENGINE.now_info(ENGINE.get_task(path.split("/")[3]))
                t.pop("outdir", None)
                return self._send(200, t)
            if path.startswith("/api/employee/"):
                p = ENGINE.profile(path.split("/", 3)[3])
                return self._send(200, p) if p else self._err("그런 직원이 없어요", 404)
            if path == "/api/zip":
                return self._zip(q.get("task", [""])[0], q.get("which", ["final"])[0])
            if path == "/api/file":
                return self._file(q.get("task", [""])[0], q.get("path", [""])[0], q.get("dl", ["0"])[0] == "1")
            if path in ("/", "/index.html"):
                path = "/index.html"
            fp = os.path.realpath(os.path.join(WEB, path.lstrip("/")))
            if not fp.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(fp):
                return self._send(404, "없는 페이지예요", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json", "application/javascript"):
                ctype += "; charset=utf-8"
            with open(fp, "rb") as f:
                return self._send(200, f.read(), ctype)
        except Exception as ex:
            store.log("GET %s 오류: %r" % (path, ex))
            return self._err("서버에서 문제가 생겼어요: %s" % ex, 500)

    def _zip(self, tid, which):
        """결과물을 zip 하나로 내려받기 — 윈도우·휴대폰처럼 사무실 맥이 아닌 곳에서 폴더째 가져갈 때.
        which=final: 최종본 폴더 전체 / which=main: 꼭 볼 파일들. 압축하지 않고 묶기만 해서(영상은 이미 압축됨) 바로 보내요."""
        t = ENGINE.get_task(tid)
        if not t:
            return self._err("그런 일이 없어요", 404)
        items = []     # (실제 파일, zip 안 이름)
        if which == "final":
            base = t.get("final_dir") or ""
            if not os.path.isdir(base):
                return self._err("이 일에는 최종본 폴더가 없어요", 404)
            name = os.path.basename(base.rstrip(os.sep))
            for root, _, fs in os.walk(base):
                for f in sorted(fs):
                    if f.startswith("."):
                        continue
                    fp = os.path.join(root, f)
                    items.append((fp, os.path.join(name, os.path.relpath(fp, base))))
        else:
            base = os.path.realpath(t["outdir"])
            name = re.sub(r'[\\/:*?"<>|]+', "_", t.get("final_name") or t.get("title") or tid)
            for rel in ENGINE.main_files(t):
                fp = os.path.realpath(os.path.join(base, rel))
                if fp.startswith(base + os.sep) and os.path.isfile(fp):
                    items.append((fp, os.path.join(name, rel)))
        if not items:
            return self._err("받을 파일이 없어요", 404)
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(name + ".zip"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with zipfile.ZipFile(self.wfile, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for fp, arc in items:
                    zf.write(fp, arc.replace(os.sep, "/"))
            store.log("zip 내려받기: %s %s (%d개)" % (tid, which, len(items)))
        except (BrokenPipeError, ConnectionResetError):
            store.log("zip 내려받기 중간에 끊김: %s" % tid)

    def _file(self, tid, rel, download):
        t = ENGINE.get_task(tid)
        if not t:
            return self._err("그런 일이 없어요", 404)
        base = os.path.realpath(t["outdir"])
        fp = os.path.realpath(os.path.join(base, rel))
        if not fp.startswith(base + os.sep) or not os.path.isfile(fp):
            return self._err("파일을 찾지 못했어요", 404)
        low = fp.lower()
        if low.endswith((".html", ".htm")):      # 홈페이지: 사진을 안에 넣어서 보여 주고 내려줌 (미리보기 창에서 바로 보임)
            body = inline_html(fp)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if download:
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(fp)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        extra = {"Accept-Ranges": "bytes"}
        if low.endswith((".md", ".txt")) and not download:
            ctype = "text/plain; charset=utf-8"
        elif low.endswith((".mp4", ".webm")) and not download:
            pass   # 브라우저에서 바로 재생
        else:
            extra["Content-Disposition"] = "attachment; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(fp))
        size = os.path.getsize(fp)
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes=") and size:
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    end = min(size - 1, int(b)) if b else size - 1
                if start > end or start >= size:
                    raise ValueError
                code = 206
                extra["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
        length = end - start + 1 if size else 0
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            with open(fp, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass   # 보던 사람이 창을 닫음

    # ---- POST
    def do_POST(self):
        if not self._client_allowed():
            return self._send(403, "이 기기에서는 열 수 없어요", "text/plain; charset=utf-8")
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == "/api/upload":      # 첨부 파일: 글(JSON)이 아니라 파일 그대로 받음
            if not self._authed():
                return self._err("로그인이 필요해요", 401)
            try:
                name = urllib.parse.unquote(self.headers.get("X-Filename") or "파일")
                return self._send(200, ENGINE.save_upload(name, self.rfile, int(self.headers.get("Content-Length") or 0),
                                                          self.headers.get("X-Group") or None,
                                                          urllib.parse.unquote(self.headers.get("X-Relpath") or "") or None))
            except ValueError as ex:
                self.close_connection = True
                return self._err(str(ex), 400)
            except Exception as ex:
                self.close_connection = True
                store.log("첨부 받기 오류: %r" % ex)
                return self._err("파일을 받다가 문제가 생겼어요: %s" % ex, 500)
        body = self._body()
        if path == "/api/login":
            ip = self._login_key()
            fails = FAILS.setdefault(ip, [])
            now = time.time()
            fails[:] = [t for t in fails if now - t < 600]
            ALL_FAILS[:] = [t for t in ALL_FAILS if now - t < 1800]
            if len(fails) >= 5:
                return self._err("비밀번호를 여러 번 틀려서 잠시 막았어요. 10분 뒤에 다시 해 주세요.", 429)
            if len(ALL_FAILS) >= 30:
                store.log("로그인 잠금: 30분 안에 여러 곳에서 30번 틀림")
                return self._err("여러 곳에서 비밀번호를 많이 틀려서 잠시 모두 막았어요. 30분 뒤에 다시 해 주세요. (이 맥에서는 바로 열려요)", 429)
            pc = SETTINGS.get("passcode")
            if pc and hmac.compare_digest(str(body.get("passcode", "")), pc):
                FAILS.pop(ip, None)
                store.log("로그인 성공: %s" % ip)
                return self._send(200, {"ok": True}, extra={"Set-Cookie": "office_token=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000" % TOKEN})
            fails.append(now)
            ALL_FAILS.append(now)
            time.sleep(1)      # 빠르게 여러 번 맞혀 보는 것을 늦춤
            return self._err("비밀번호가 맞지 않아요", 403)
        if not self._authed():
            return self._err("로그인이 필요해요", 401)
        if path == "/api/clientlog":
            store.log("화면 오류: %s | %s | %s" % (str(body.get("msg", ""))[:300], str(body.get("page", ""))[:80], str(body.get("stack", ""))[:600].replace("\n", " / ")))
            return self._send(200, {"ok": True})
        try:
            if path == "/api/task":
                try:
                    return self._send(200, ENGINE.public(ENGINE.create_task(body.get("text", ""), body.get("project") or None,
                                                                            bool(body.get("compare")), bool(body.get("force")),
                                                                            body.get("attachments") or None)))
                except eng.DuplicateTask as dup:
                    return self._send(409, {"error": "이미 비슷한 일이 있어요.", "duplicate": dup.info})
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "message":
                return self._send(200, ENGINE.public(ENGINE.add_message(parts[2], body.get("text", ""), body.get("attachments") or None)))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "retry":
                return self._send(200, ENGINE.public(ENGINE.retry(parts[2])))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "stop":
                return self._send(200, ENGINE.public(ENGINE.stop_task(parts[2])))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "finish":
                return self._send(200, ENGINE.public(ENGINE.finish_task(parts[2])))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "reveal_final":
                return self._send(200, ENGINE.reveal_final(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "reveal":
                return self._send(200, ENGINE.reveal_folder(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "restart":
                return self._send(200, ENGINE.public(ENGINE.restart_task(parts[2])))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "resume":
                return self._send(200, ENGINE.public(ENGINE.resume_task(parts[2])))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "project":
                return self._send(200, ENGINE.public(ENGINE.set_task_project(parts[2], body.get("project", ""))))
            if parts == ["api", "deploy", "check"]:      # 배포 준비 확인 (실제로 올리지 않음)
                ok, url, msg = ENGINE.runner.deploy_site(ENGINE.settings["output_root"], check=True)
                return self._send(200, {"ok": ok, "msg": msg})
            if parts == ["api", "claude_projects", "sync"]:
                return self._send(200, ENGINE.sync_claude_projects())
            if len(parts) == 4 and parts[:2] == ["api", "project"] and parts[3] == "claude":
                return self._send(200, ENGINE.link_claude_project(parts[2], body.get("url", "")))
            if parts == ["api", "demo", "meeting"]:      # 회의실 모이는 모습 보기 (일은 안 바뀜, 40초)
                ENGINE.meet_hold["demo"] = (["기획실장", "강의자료", "이미지 담당", "검토 담당"], eng.time.time() + 40, "회의 시연 중")
                return self._send(200, {"ok": True})
            if parts == ["api", "project"]:
                return self._send(200, ENGINE.add_project(body.get("name"), body.get("goal"), body.get("folder"), body.get("keywords")))
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "cancel":
                task, note = ENGINE.cancel_task(parts[2])
                return self._send(200, {"task": ENGINE.public(task), "note": note})
            if len(parts) == 4 and parts[:2] == ["api", "task"] and parts[3] == "ask":
                return self._send(200, ENGINE.public(ENGINE.ask_status(parts[2], body.get("question", ""))))
            if len(parts) == 5 and parts[:2] == ["api", "employee"] and parts[3:] == ["score", "undo"]:
                return self._send(200, ENGINE.undo_score(parts[2], str(body.get("id", ""))))
            if len(parts) == 4 and parts[:2] == ["api", "employee"] and parts[3] == "rule":
                return self._send(200, {"rules": ENGINE.add_rule(parts[2], body.get("text", ""))})
            if len(parts) == 5 and parts[:2] == ["api", "employee"] and parts[3:] == ["rule", "delete"]:
                return self._send(200, {"rules": ENGINE.remove_rule(parts[2], int(body.get("index", -1)))})
            if parts == ["api", "stop-all"]:
                return self._send(200, {"stopped": ENGINE.stop_all()})
            if len(parts) == 3 and parts[:2] == ["api", "approval"]:
                return self._send(200, ENGINE.decide(parts[2], body.get("decision", ""), body.get("reason", ""), body.get("choice", ""),
                                                  body.get("attachments") or None))
        except ValueError as ex:
            return self._err(str(ex), 400)
        except Exception as ex:
            store.log("POST %s 오류: %r" % (path, ex))
            return self._err("서버에서 문제가 생겼어요: %s" % ex, 500)
        return self._err("알 수 없는 요청이에요", 404)


def main():
    global ENGINE, SETTINGS, TOKEN
    SETTINGS = eng.load_settings()
    port = int(SETTINGS["port"])
    remote = bool(SETTINGS.get("remote"))
    host = "0.0.0.0" if remote else SETTINGS["host"]
    local = host in ("127.0.0.1", "localhost", "::1")
    if not local and not SETTINGS.get("passcode"):
        print("⚠ 다른 컴퓨터에서도 열려면 접속 비밀번호가 꼭 필요해요.")
        print("  터미널에서 python3 -X utf8 tools/set_passcode.py 를 실행해 비밀번호를 정해 주세요.")
        sys.exit(1)
    TOKEN = make_token(SETTINGS["passcode"]) if SETTINGS.get("passcode") else ""
    ENGINE = eng.Engine()
    srv = ThreadingHTTPServer((host, port), Handler)
    mode = "연습 모드(가짜 직원)" if SETTINGS["fake"] else "실제 모드"
    print("사무실이 열렸어요 → http://127.0.0.1:%d  (%s)" % (port, mode))
    if remote:
        print("원격 접속 켜짐: Tailscale 로 연결된 내 기기에서만 접속 비밀번호로 열 수 있어요.")
    print("끄려면 이 창에서 Ctrl + C 를 누르세요.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n사무실을 닫았어요.")


if __name__ == "__main__":
    main()
