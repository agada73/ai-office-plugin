"""사무실 엔진: 일 받기 → 계획 → 결재 → 직원 작업 → 보고 의 흐름을 돌립니다.
직원은 한 번에 한 명씩 일합니다(첫 버전).
"""
import json
import os
import queue
from collections import deque
import re
import shutil
import subprocess
import threading
import time

import docs
import kakao
import ko_speech
import lecture_compose
import prompts
import seongseo
import store

ROOT = store.ROOT
ROSTER = json.load(open(os.path.join(ROOT, "app", "roster.json"), encoding="utf-8"))
EMP = {e["name"]: e for e in ROSTER["employees"]}
KNOW_DIR = os.path.join(ROOT, "data", "knowledge")
LIBRARY = os.path.join(ROOT, "자료실_library")   # 📚 자료실: 교수님이 올린 파일·폴더를 모두 보관 (다음 일에서도 찾음, 직원은 읽기만)   # 기획실장 배경지식 (교수님 이력·작업 지도, 깃에 안 올림)
TRAIN_EMP = "교육 담당"          # 교육 담당: 결과(규칙)는 교수님 결재 뒤 직원 수첩에 들어감
VIDEO_REVIEW_EMP = "영상 검토 담당"
SEC_EMP = "비서"          # 비서: 교수님 구글 캘린더를 읽기만 해서 일정 보고
GCAL = "mcp__claude_ai_Google_Calendar"
CANVA = "mcp__claude_ai_Canva"          # 캔바 (교수님 Pro 구독, 대부분 크레딧 없음)
HIGGS = "mcp__claude_ai_higgsfield"     # 힉스필드 (크레딧이 듦 → 늘 교수님 결재)
HIGGS_READ = ["balance", "models_explore", "get_presets", "get_preset_instructions"]
MEDIA_DIR = os.path.join(ROOT, "tools", "media")
# 🎞 무료 영상 도구 (맥에서 돌아감, 크레딧 없음): 자동 자막(Whisper)·말 없는 부분 자르기(auto-editor)·설명 애니메이션(Manim)·카드 모션 영상(Remotion)·ffmpeg
MEDIA_BASH = ["Bash(%s/subtitles.sh:*)" % MEDIA_DIR, "Bash(%s/remotion_render.sh:*)" % MEDIA_DIR,
              "Bash(auto-editor:*)", "Bash(manim:*)", "Bash(ffmpeg:*)", "Bash(ffprobe:*)", "Bash(ls:*)"]
GCAL_READ = ["list_events", "list_calendars", "get_event", "search_events", "suggest_time"]

# 직급 (점수제, 교수님 결정 11-09): (점수, 한 번에 통과 비율 %, 이름). 둘 다 넘어야 올라가요. 승진은 어렵게.
#   점수: 단계 승인 +10, 한 번에 승인 +5 / 수정 요청 −10 / 최종 보고 반려 −15 / 대표 지적 −10 (같은 일에서 또 하면 −15). 오류로 멈춘 건 깎지 않음
RANKS = [(0, 0, "사원"), (100, 70, "주임"), (300, 75, "대리"), (700, 80, "과장"), (1300, 85, "차장"), (2200, 90, "부장")]
# 기획실장은 처음부터 실장급, 전용 사다리 (세운 계획으로 점수). 맨 위는 대표님 바로 아래 부사장
PLANNER_RANKS = [(0, 0, "실장"), (300, 80, "본부장"), (800, 80, "이사"), (1600, 85, "상무"), (3000, 90, "부사장")]
PTS_DONE, PTS_FIRST, PTS_REVISE, PTS_FINAL_BACK, PTS_SCOLD, PTS_SCOLD_AGAIN = 10, 5, -10, -15, -10, -15


def ladder_of(name):
    return PLANNER_RANKS if name == "기획실장" else RANKS
QUIET_MIN = int(os.environ.get("OFFICE_QUIET_MIN", "10"))   # 직원이 이 분 넘게 아무 소식이 없으면 현황판에 '오래 소식 없음'을 띄워요


class RunnerError(Exception):
    def __init__(self, friendly, raw=""):
        super().__init__(friendly)
        self.friendly = friendly
        self.raw = raw


class RunnerLimit(RunnerError):
    """Claude 구독 사용량 한도에 걸림: reset_at(초) 에 다시 시도"""
    def __init__(self, friendly, raw="", reset_at=None):
        super().__init__(friendly, raw)
        self.reset_at = reset_at


def parse_limit(text):
    """Claude 의 '사용량 한도' 안내를 알아보고, 다시 쓸 수 있는 시각(초)을 돌려줌. 한도 안내가 아니면 None"""
    low = (text or "").lower()
    if not any(k in low for k in ("usage limit", "limit reached", "hit your limit", "rate limit", "limit will reset", "resets at", "· resets", "resets ")):
        return None
    if not any(k in low for k in ("limit", "한도")):
        return None
    now = time.time()
    m = re.search(r"limit reached\|(\d{10})", low)
    if m:
        return int(m.group(1)) + 60
    m = re.search(r"resets?(?: at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", low)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        lt = time.localtime(now)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, 0, 0, 0, -1))
        if t <= now:
            t += 86400
        return t + 90
    return now + 30 * 60      # 시각을 모르면 30분 뒤


class DuplicateTask(Exception):
    """이미 비슷한 일을 하고 있을 때: 새로 시작하지 않고 교수님께 먼저 알려요"""
    def __init__(self, info):
        super().__init__("이미 비슷한 일이 있어요.")
        self.info = info


DUP_RATIO = 0.6      # 글이 이만큼 비슷하면 같은 일로 봐요 (0~1)
DUP_DONE_HOURS = 6   # 끝난 일은 이 시간 안에 끝난 것만 비교해요


class RunnerCancelled(Exception):
    """교수님이 멈추기를 눌러서 하던 작업을 끊었을 때"""


def default_settings():
    return {
        "input_dirs": [os.path.join(ROOT, "자료_inputs")],
        "output_root": os.path.join(ROOT, "산출물_outputs"),
        "fake": False,
        "host": "127.0.0.1",
        "port": 8840,
        "heygen": {"avatar_id": "", "avatar_name": "", "voice_id": "", "voice_name": "",
                   "resolution": "1080p", "aspect_ratio": "16:9",
                   "avatar_resolution": "720p", "avatar_scale": 0.26, "first_scale": 0.38,
                   "chars_per_min": 330, "credits_per_min_low": 20, "credits_per_min_high": 31},
        "seongseo": seongseo.cfg_defaults(),
    }


LIBRARY_DIR = os.path.join(ROOT, "자료실_library")


def load_settings():
    s = default_settings()
    saved = store.load("settings", {})
    hg = dict(s["heygen"]); hg.update(saved.get("heygen", {}))
    s.update(saved)
    s["heygen"] = hg
    if os.environ.get("OFFICE_FAKE") == "1":
        s["fake"] = True
    if LIBRARY_DIR not in s["input_dirs"]:      # 📚 자료실은 늘 자료 폴더에 들어감 (읽기만)
        s["input_dirs"] = list(s["input_dirs"]) + [LIBRARY_DIR]
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    return s


def find_claude():
    p = shutil.which("claude")
    if p:
        return p
    for c in (os.path.expanduser("~/.local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if os.path.exists(c):
            return c
    return None


def extract_json(text):
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    cand = m.group(1) if m else None
    if cand is None:
        a, b = text.find("{"), text.rfind("}")
        if a == -1 or b <= a:
            return None
        cand = text[a:b + 1]
    try:
        return json.loads(cand)
    except ValueError:
        return None


def duration_text(a, b):
    """'2026-11-07 14:00:00' 두 시각 사이를 '3분 20초' 같은 글로"""
    try:
        t0 = time.mktime(time.strptime(a, "%Y-%m-%d %H:%M:%S"))
        t1 = time.mktime(time.strptime(b, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return ""
    sec = max(0, int(t1 - t0))
    if sec >= 3600:
        return "%d시간 %d분" % (sec // 3600, sec % 3600 // 60)
    return "%d분 %d초" % (sec // 60, sec % 60) if sec >= 60 else "%d초" % sec


def rank_info(score, done=0, first_pass=0, ladder=None):
    """지금 직급과 다음 직급까지 남은 것 (점수 + 한 번에 통과 비율)"""
    RANKS = ladder or globals()["RANKS"]
    rate = round(100 * first_pass / done) if done else None
    cur, nxt = RANKS[0], None
    for r in RANKS:
        if score >= r[0] and (rate is None or rate >= r[1] or r[0] == 0):
            cur = r
        else:
            nxt = r
            break
    out = {"rank": cur[2], "level": RANKS.index(cur), "passRate": rate, "score": score}
    return _with_next(out, RANKS, score, rate, nxt)


def _with_next(out, RANKS, score, rate, nxt):
    if nxt:
        need = []
        if score < nxt[0]:
            need.append("%d점 더" % (nxt[0] - score))
        if rate is not None and rate < nxt[1]:
            need.append("한 번에 통과 %d%% 이상 (지금 %d%%)" % (nxt[1], rate))
        out.update({"next": nxt[2], "need": need, "nextScore": nxt[0], "nextRate": nxt[1],
                    "progress": max(0, min(100, round(100 * score / nxt[0]))) if nxt[0] else 100})
    else:
        out.update({"next": "", "need": [], "progress": 100})
    return out


def rank_of(score, done=0, first_pass=0):
    return rank_info(score, done, first_pass)["rank"]


def rank_view(stat, name=""):
    """직원 통계로 직급 보기. 한 번 오른 직급은 점수가 깎여도 내려가지 않아요(저장된 level 을 지킴)."""
    RANKS = ladder_of(name)
    score, done, fp = stat.get("score", 0), stat.get("done", 0), stat.get("firstPass", 0)
    info = rank_info(score, done, fp, RANKS)
    kept = int(stat.get("level", 0))
    if kept > info["level"] and kept < len(RANKS):
        info["rank"], info["level"] = RANKS[kept][2], kept
        _with_next(info, RANKS, score, info["passRate"], RANKS[kept + 1] if kept + 1 < len(RANKS) else None)
    return info


# ---------------------------------------------------------------- 실행기
DEFAULT_PROJECTS = [
    {"id": "p-video", "name": "HeyGen 강의 영상", "goal": "PDF 슬라이드와 아바타로 차시별 강의 영상 자동 제작",
     "folder": "", "keywords": ["영상", "헤이젠", "heygen", "아바타"], "color": "#2563eb"},
    {"id": "p-lecture", "name": "강의자료 제작", "goal": "슬라이드·교안·수업 자료 만들기",
     "folder": "", "keywords": ["강의자료", "슬라이드", "ppt", "교안", "강의"], "color": "#059669"},
    {"id": "p-etc", "name": "기타", "goal": "어느 프로젝트에도 안 맞는 일", "folder": "", "keywords": [], "color": "#6b7280"},
]
IMG_EMP = "이미지 담당"
CHROME = "mcp__claude-in-chrome"
HG = "mcp__claude_ai_HeyGen"   # 교수님 Claude 계정에 연결된 헤이젠 커넥터


TTS_PER_MIN = 15                 # 헤이젠 목소리: 사무실 전체 1분에 15쪽까지 (헤이젠 제한 30의 절반)
TTS_LOCK = threading.Lock()
# 헤이젠 목소리가 '막힘'으로 보는 오류: 401(11-09~10 계정 막힘), 400 목소리 호환 안 됨(11-13, 예전 목소리를 새 엔진이 거절)
TTS_BLOCKED = re.compile(r"401|unauthori|not compatible|호환|invalid_parameter", re.I)


class RealRunner:
    LOGIN_HINTS = ("not logged in", "/login", "oauth session expired", "invalid api key", "please run")

    def __init__(self, settings):
        self.settings = settings
        self.procs = {}          # 일 번호 → 지금 돌고 있는 Claude 프로세스들
        self.cancelled = set()   # 멈추기를 누른 일 번호
        self.heard = {}          # 일 번호 → Claude 가 마지막으로 무엇이든 내보낸 시각 (생각 중 신호 포함)

    def abort(self, tag):
        """하던 작업을 바로 끊음"""
        self.cancelled.add(tag)
        for pr in list(self.procs.get(tag, [])):
            try:
                pr.terminate()
            except OSError:
                pass

    def _base_cmd(self, extra):
        exe = find_claude()
        if not exe:
            raise RunnerError("이 컴퓨터에서 Claude Code 프로그램(claude)을 찾지 못했어요.", "claude not found")
        return [exe, "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence"] + extra

    def _run(self, cmd, prompt, cwd, on_progress, timeout, tag=None):
        env = dict(os.environ)
        env["PATH"] = "/opt/homebrew/bin:" + os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    cwd=cwd, env=env, text=True, encoding="utf-8")
        except OSError as e:
            raise RunnerError("Claude를 실행하지 못했어요.", str(e))
        if tag:
            self.procs.setdefault(tag, []).append(proc)
            if tag in self.cancelled:        # 시작하자마자 멈추기가 눌린 경우
                proc.terminate()
        timed_out = []

        def _kill():
            timed_out.append(True)
            proc.kill()
        timer = threading.Timer(timeout, _kill) if timeout else None
        if timer:
            timer.start()
        final, raw_tail = None, []
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                raw_tail.append(line)
                raw_tail = raw_tail[-30:]
                if tag:
                    self.heard[tag] = time.time()
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "assistant":
                    for blk in (ev.get("message") or {}).get("content") or []:
                        if blk.get("type") == "tool_use" and on_progress:
                            on_progress(self._progress_text(blk))
                elif ev.get("type") == "result":
                    final = ev
            proc.wait()
        except (BrokenPipeError, OSError):
            if not (tag and tag in self.cancelled):
                raise
        finally:
            if timer:
                timer.cancel()
            if tag and proc in self.procs.get(tag, []):
                self.procs[tag].remove(proc)
        if tag and tag in self.cancelled:
            raise RunnerCancelled()
        if final is None and timed_out:
            raise RunnerError("작업이 %d분을 넘겨서 멈췄어요. 일이 너무 길었어요. 「다시 시도」를 누르면 처음부터 다시 해요." % (timeout // 60), "\n".join(raw_tail))
        if final is None:
            raise RunnerError("Claude에서 결과가 오지 않았어요. 잠시 뒤 다시 시도해 주세요.", "\n".join(raw_tail))
        text = final.get("result") or ""
        reset = parse_limit(text) if (final.get("is_error") or len(text) < 400) else None
        if reset:
            raise RunnerLimit("Claude 사용량 한도에 걸렸어요. %s 쯤 다시 채워지면 저절로 이어서 해요." % time.strftime("%H:%M", time.localtime(reset)), text, reset)
        if final.get("is_error"):
            low = text.lower()
            if any(h in low for h in self.LOGIN_HINTS):
                raise RunnerError("Claude에 로그인이 필요해요. 터미널에서 'claude /login' 을 실행해 로그인해 주세요.", text)
            # 진짜 이유를 한 줄로 붙여 보여 줌 (기록 파일을 찾아보지 않아도 알 수 있게)
            why = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
            why = re.sub(r"\s+", " ", why)[:120]
            raise RunnerError("Claude가 일을 하다가 오류가 났어요" + (": " + why if why else "."), text)
        return text

    @staticmethod
    def _progress_text(blk):
        name = blk.get("name", "")
        inp = blk.get("input") or {}
        path = os.path.basename(inp.get("file_path") or inp.get("path") or inp.get("pattern") or "")
        table = {"Read": "자료 읽는 중", "Glob": "파일 찾는 중", "Grep": "내용 찾는 중",
                 "Write": "파일 쓰는 중", "Edit": "글 고치는 중"}
        base = table.get(name, "일하는 중")
        return (base + (": " + path if path else ""))[:28]

    def plan(self, task, prompt, input_dirs, on_progress=None):
        self.cancelled.discard(task["id"])
        cmd = self._base_cmd(["--allowedTools", "Read", "Glob", "Grep", "--disallowedTools", HG, "--permission-mode", "default"]
                             + sum([["--add-dir", d] for d in input_dirs], []))
        return self._run(cmd, prompt, task["outdir"], on_progress, None, tag=task["id"])      # 시간 제한 없음 (교수님 결정)

    def review(self, task, prompt):
        """기획실장 결과 검토: 결과물 파일을 읽기만 하고 승인/보완/다시 를 JSON 으로"""
        cmd = self._base_cmd(["--allowedTools", "Read", "Glob", "Grep", "--disallowedTools", HG, "Bash", "Write", "Edit",
                              "--permission-mode", "default"])
        return self._run(cmd, prompt, task["outdir"], None, 900, tag=task["id"])

    def answer(self, task, snapshot, question):
        """교수님이 '지금 어디까지 됐어?' 처럼 물어볼 때: 기록만 보고 쉬운 말로 답함 (일은 바뀌지 않음)"""
        cmd = self._base_cmd(["--allowedTools", "Read", "--disallowedTools", HG, "Bash", "Write", "Edit", "WebFetch", "WebSearch",
                              "--permission-mode", "default"])
        prompt = (prompts.COMMON + "\n\n당신은 '기획실장'입니다. 교수님이 지금 일이 어떻게 되고 있는지 물으셨습니다. "
                  "아래 [현재 기록]만 보고 사실대로 쉬운 말로 3~6줄 안에 답하세요. 기록에 없는 것은 지어내지 말고 '기록에 없어요'라고 하세요. "
                  "일을 새로 시키거나 바꾸는 말은 하지 마세요.\n\n[현재 기록]\n%s\n\n[교수님 질문]\n%s" % (snapshot, question))
        return self._run(cmd, prompt, task["outdir"], None, 180)

    DEPLOY_SCRIPT = os.path.join(ROOT, "tools", "deploy_site.py")

    def deploy_site(self, cwd, check=False, tag=None):
        """사이트 배포를 직원(Claude Code)이 대신 실행: 서버는 맥 보호 때문에 바탕화면 폴더를 못 읽어요.
        반환: (성공여부, 주소, 메시지)"""
        line = "/usr/bin/python3 %s%s" % (self.DEPLOY_SCRIPT, " --check" if check else "")
        cmd = self._base_cmd(["--allowedTools", "Bash(/usr/bin/python3 %s:*)" % self.DEPLOY_SCRIPT,
                              "--disallowedTools", "Write", "Edit", "WebFetch", "WebSearch", HG, CHROME])
        prompt = ("Bash 도구로 아래 명령 하나만 그대로 실행하세요(시간이 몇 분 걸릴 수 있어요. timeout 은 1800000 으로). "
                  "다른 명령은 실행하지 마세요. 실행이 끝나면 출력의 마지막 'RESULT ' 로 시작하는 줄을 한 글자도 바꾸지 말고 그대로 답하세요.\n\n" + line)
        text = self._run(cmd, prompt, cwd, None, 2400, tag=tag)
        i = (text or "").rfind("RESULT ")
        data = extract_json(text[i + 7:]) if i >= 0 else None
        if not data:
            raise RunnerError("배포 결과를 알아듣지 못했어요.", text or "")
        return bool(data.get("ok")), data.get("url") or None, data.get("msg") or ""

    def list_claude_projects(self, cwd):
        """claude.ai 프로젝트 목록 읽기 (Chrome 화면에서 읽기만)"""
        cmd = self._base_cmd(["--chrome", "--allowedTools", CHROME, "--disallowedTools", CHROME + "__javascript_tool", "Bash", "Write", "Edit", HG])
        return self._run(cmd, prompts.LIST_CLAUDE_PROJECTS, cwd, None, 600)

    def step(self, emp, task, step, prompt, input_dirs, on_progress=None):
        self.cancelled.discard(task["id"])
        deny = ["Bash", "WebFetch", "WebSearch", "NotebookEdit"]
        allow = ["Read", "Glob", "Grep", "Write", "Edit"]
        eng_kind = step.get("engine") or "code"
        if eng_kind == "chat":      # 채팅 방식: 웹 검색을 열어 줌
            deny = ["Bash", "NotebookEdit"]
            allow += ["WebSearch", "WebFetch"]
        if step.get("heygen"):      # 크레딧 결재가 끝난 영상 만들기 단계에서만 영상 만들기 도구를 엶
            allow += [HG + "__create_video_from_avatar", HG + "__get_video"]
        else:
            deny.append(HG)
        if eng_kind == "media":         # 🎞 무료 영상 도구: 정해 둔 명령만 Bash 로 (다른 Bash 는 막음)
            if "Bash" in deny:
                deny.remove("Bash")
            allow += MEDIA_BASH
        if eng_kind == "canva":         # 캔바: 디자인 만들기·내보내기
            allow.append(CANVA)
        else:
            deny.append(CANVA)
        if eng_kind == "higgs":         # 힉스필드: 결재 전에는 견적(읽기)만, 결재 뒤에만 만들기
            if step.get("approvedToRun"):
                allow.append(HIGGS)
            else:
                allow += [HIGGS + "__" + t for t in HIGGS_READ]
        else:
            deny.append(HIGGS)
        if emp["name"] == SEC_EMP:      # 비서: 구글 캘린더는 읽기만 (일정 넣기·바꾸기·지우기·답장은 막음)
            allow += [GCAL + "__" + t for t in GCAL_READ]
            deny += [GCAL + "__create_event", GCAL + "__update_event", GCAL + "__delete_event", GCAL + "__respond_to_event"]
        else:
            deny.append(GCAL)
        chrome = []
        if emp["name"] != IMG_EMP and eng_kind in ("gpt", "claude_project"):      # 채팅 사이트 화면 조작: Chrome 만 엶 (사이트는 규칙으로 제한)
            allow.append(CHROME)
            deny.append(CHROME + "__javascript_tool")
            chrome = ["--chrome"]
        if emp["name"] == IMG_EMP and eng_kind not in ("canva", "higgs"):      # 그림 담당: ChatGPT 화면 조작에 Chrome 도구를 엶 (chatgpt.com 에서만 쓰도록 규칙으로 제한)
            allow.append(CHROME)
            deny.append(CHROME + "__javascript_tool")
            deny.remove("Bash")      # 내려받은 그림을 다운로드 폴더에서 작업 폴더로 복사할 때만 cp/ls 를 열어 줌 (서버는 다운로드 폴더를 못 읽어서)
            allow += ["Bash(cp:*)", "Bash(ls:*)"]
            chrome = ["--chrome"]
        for d in input_dirs:
            deny += ["Write(/%s/**)" % d, "Edit(/%s/**)" % d]
        save_dirs = []
        if emp["name"] == IMG_EMP:      # 다운로드 폴더: 쓰기 금지를 걸면 cp 로 꺼내 오는 것까지 막혀서, 열어만 둠 (지우기·고치기 금지는 규칙으로)
            save_dirs.append(os.path.expanduser("~/Downloads"))
        if emp["name"] == IMG_EMP:      # 그림을 바로 넣어 둘 프로젝트 폴더 (쓰기 허용, 같은 이름이 있으면 복사 안 함 — 규칙)
            if self.settings.get("image_save_dir"):      # 그림을 바로 넣어 둘 폴더 (설정에서 정함)
                save_dirs.append(self.settings["image_save_dir"])
        input_dirs = list(input_dirs) + save_dirs
        cmd = self._base_cmd(
            chrome + ["--allowedTools"] + allow + ["--disallowedTools"] + deny
            + ["--permission-mode", "acceptEdits",
               "--append-system-prompt", prompts.COMMON + "\n당신의 이름은 '%s'입니다. 맡은 일: %s" % (emp["name"], emp["role"])]
            + sum([["--add-dir", d] for d in input_dirs], []))
        # 단계마다 시간 제한 없음 (교수님 결정: 끝날 때까지 계속 일함. 멈추려면 「멈추기」)
        return self._run(cmd, prompt, task["outdir"], on_progress, None, tag=task["id"])

    # ---- 헤이젠: 남은 크레딧, 영상 상태 확인(읽기만), 완성된 영상 내려받기
    def credits(self, cwd):
        cmd = self._base_cmd(["--allowedTools", HG + "__get_current_user"])
        text = self._run(cmd, "HeyGen 의 get_current_user 도구를 한 번만 호출하고, subscription.credits.premium_credits.remaining 숫자를 "
                         "CREDITS=1234 처럼 한 줄로만 출력하세요. 다른 말은 쓰지 마세요.", cwd, None, 180)
        # 11-10: 첫 숫자만 집어서 1,379 를 25 로 잘못 읽은 적 있음 → CREDITS= 뒤 숫자만 믿음
        m = re.search(r"CREDITS\s*=\s*(\d[\d,]*)", text or "") or re.search(r"remaining\D{0,5}(\d[\d,]*)", text or "")
        return int(m.group(1).replace(",", "")) if m else None

    def video_status(self, video_id, cwd):
        cmd = self._base_cmd(["--allowedTools", HG + "__get_video"])
        prompt = ('HeyGen 의 get_video 도구를 videoId="%s" 로 한 번만 호출하세요. 결과를 아래 JSON 한 줄로만 출력하세요(다른 글 금지): '
                  '{"status":"도구가 알려 준 상태 그대로(pending, processing, completed, failed 등)","video_url":"완성된 영상 주소(없으면 빈 문자열)",'
                  '"caption_url":"자막 파일 주소(없으면 빈 문자열)","failure":"실패 이유(없으면 빈 문자열)"}' % video_id)
        text = self._run(cmd, prompt, cwd, None, 300)
        data = extract_json(text)
        if not data:
            raise RunnerError("헤이젠 영상 상태를 알아듣지 못했어요.", text)
        return data

    def tts_batch(self, items, voice_id, cwd, glossary=None):
        """목소리만 만들기 (아바타 없이). items: [(쪽 번호, 글 파일 경로)] → {쪽 번호: 소리 주소}"""
        cmd = self._base_cmd(["--allowedTools", "Read", "Write", HG + "__create_speech_without_player",
                              "--disallowedTools", "Bash", "Edit", HG + "__create_video_from_avatar", HG + "__create_speech",
                              "--permission-mode", "acceptEdits"])
        out = os.path.join(cwd, "_voice", "voice_jobs.json")
        lines = "\n".join("- 쪽 %d: %s" % (pg, fp) for pg, fp in items)
        prompt = ("HeyGen 의 create_speech_without_player 도구로 아래 글 파일마다 목소리를 **한 번씩만** 만드세요.\n"
                  "1. 파일마다 Read 로 글을 읽고, 그 글을 **한 글자도 바꾸지 말고** text 로 넣습니다.\n"
                  "2. 값: voiceId=%s, language=\"ko\"%s\n"
                  "3. **한 쪽씩 차례로** 만듭니다. 도구를 동시에 여러 번 부르지 말고, 앞 쪽 결과를 받은 뒤 다음 쪽을 부릅니다.\n"
                  "   (동시에 부르면 헤이젠 출입증이 바뀌는 순간 일부 쪽이 401 오류로 거절돼요)\n"
                  "4. 도구가 돌려준 audio_url 을 모아 '%s' 에 이 모양으로 Write 합니다: {\"쪽번호\": \"audio_url\", ...}\n"
                  "5. 401·unauthorized·token 오류가 나면 그 쪽만 **딱 한 번** 다시 부릅니다. 그 밖의 오류나 두 번째도 실패하면 "
                  "다시 시도하지 말고 그 쪽은 {\"쪽번호\": \"error: 오류 내용\"} 으로 적습니다.\n"
                  "   401 이 두 번 연속 나면 헤이젠 목소리가 막힌 것이니 **남은 쪽은 부르지 말고** 모두 \"error: 401 (막힘, 부르지 않음)\" 으로 적고 바로 Write 하세요.\n"
                  "6. 마지막 답변에는 몇 쪽을 만들었는지 한 줄만 적으세요.\n\n글 파일:\n%s") % (
                      voice_id, (", brandGlossaryId=%s" % glossary) if glossary else "", out, lines)
        if os.path.exists(out):
            os.remove(out)
        self._run(cmd, prompt, cwd, None, 1800)
        try:
            data = json.load(open(out, encoding="utf-8"))
        except (OSError, ValueError):
            raise RunnerError("목소리 만들기 결과를 받지 못했어요.", "")
        return {int(k): str(v) for k, v in data.items() if str(k).strip().isdigit()}

    def higgs_tts(self, items, ref_media, cwd):
        """대비 길: 힉스필드 Seed Audio 로 교수님 목소리(녹음 ref_media)를 복제해 쪽마다 읽게 함 → {쪽 번호: 소리 주소}
        12쪽씩 묶어 내고 끝날 때까지 기다림. 크레딧은 교수님이 헤이젠 영상 결재에서 함께 승인한 경우에만 부름."""
        res = self._higgs_tts_once(items, ref_media, cwd)
        # 힉스필드 속도 제한(429)에 걸린 쪽만 1분 쉬고 한 번 더 (크레딧은 성공한 쪽만 듦)
        again = [(pg, fp) for pg, fp in items if re.search(r"429|rate_limit", str(res.get(pg, "")), re.I)]
        if again:
            time.sleep(60)
            res.update(self._higgs_tts_once(again, ref_media, cwd))
        return res

    def _higgs_tts_once(self, items, ref_media, cwd):
        out = os.path.join(cwd, "_voice", "higgs_voice_jobs.json")
        res = {}
        for i in range(0, len(items), 8):
            part = items[i:i + 8]
            if i:
                time.sleep(20)          # 묶음 사이에 쉬어서 힉스필드 속도 제한(429)을 피함
            cmd = self._base_cmd(["--allowedTools", "Read", "Write", HIGGS + "__generate_audio_batch", HIGGS + "__jobs_wait",
                                  "--disallowedTools", "Bash", "Edit", HIGGS + "__generate_video", HIGGS + "__generate_image",
                                  "--permission-mode", "acceptEdits"])
            lines = "\n".join("- index %d: %s" % (pg, fp) for pg, fp in part)
            prompt = ("힉스필드 generate_audio_batch 도구로 아래 글 파일마다 교수님 목소리를 **한 번씩만** 만드세요.\n"
                      "1. 파일마다 Read 로 글을 읽고, 그 글을 **한 글자도 바꾸지 말고** prompt 로 넣습니다.\n"
                      "2. generate_audio_batch 를 **딱 한 번** 부르고 requests 에 아래 파일을 모두 넣습니다. 각 항목:\n"
                      "   index=쪽 번호, params={model:\"seed_audio\", prompt:글, medias:[{role:\"audio_references\", value:\"%s\"}], "
                      "format:\"mp3\", sample_rate:44100, use_unlim:false}\n"
                      "3. 돌려받은 job 들을 jobs_wait(timeout_seconds=15)로 모두 끝날 때까지 기다립니다 (all_terminal 이 true 가 될 때까지 여러 번).\n"
                      "4. 결과를 '%s' 에 이 모양으로 Write 합니다: {\"쪽번호\": \"result_url\", ...}. 실패한 쪽은 {\"쪽번호\": \"error: 내용\"}.\n"
                      "5. 같은 쪽을 다시 만들지 마세요(크레딧). 마지막 답변에는 몇 쪽을 만들었는지 한 줄만.\n\n글 파일:\n%s") % (ref_media, out, lines)
            if os.path.exists(out):
                os.remove(out)
            self._run(cmd, prompt, cwd, None, 1800)
            try:
                data = json.load(open(out, encoding="utf-8"))
            except (OSError, ValueError):
                raise RunnerError("힉스필드 목소리 결과를 받지 못했어요.", "")
            res.update({int(k): str(v) for k, v in data.items() if str(k).strip().isdigit()})
        return res

    def tts_paced(self, items, voice_id, cwd, glossary=None):
        """헤이젠 목소리를 사무실 전체에서 1분에 TTS_PER_MIN 쪽 이하로만 만듦.
        (11-09 낮 1~6차시를 동시에 ~120번 불러 헤이젠 제한(1분 30번)을 넘긴 뒤 목소리 기능이 401 로 막힘)"""
        res = {}
        for i in range(0, len(items), TTS_PER_MIN):
            part = items[i:i + TTS_PER_MIN]
            with TTS_LOCK:                  # 다른 일이 목소리를 만드는 중이면 차례를 기다림
                t0 = time.time()
                got = self.tts_batch(part, voice_id, cwd, glossary)
                res.update(got)
                if any(str(v).startswith("error") and TTS_BLOCKED.search(str(v)) for v in got.values()):
                    break                   # 막혀 있으면 더 부르지 않음 (대비 길로 넘어감)
                wait = 60 - (time.time() - t0)
                if wait > 0 and i + TTS_PER_MIN < len(items):
                    time.sleep(wait)
        return res

    def download(self, url, dest):
        import urllib.request
        if not str(url).startswith("https://"):
            raise RunnerError("영상 주소가 안전한 주소(https)가 아니에요.", str(url)[:100])
        tmp = dest + ".part"
        req = urllib.request.Request(url, headers={"User-Agent": "AIOffice/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, dest)


class FakeRunner:
    """로그인 없이 사무실 움직임을 시험하는 연습용 직원 (진짜 일은 하지 않음)"""

    def __init__(self, settings):
        self.settings = settings
        self.polls = {}
        self.cancelled = set()

    def abort(self, tag):
        self.cancelled.add(tag)

    def _nap(self, tag, secs):
        end = time.time() + secs
        while time.time() < end:
            if tag in self.cancelled:
                raise RunnerCancelled()
            time.sleep(0.2)

    def answer(self, task, snapshot, question):
        time.sleep(1.5)
        return "[연습 모드] 지금 기록으로는 일이 순서대로 진행되고 있어요. (이 답은 연습용이에요)"

    def credits(self, cwd):
        return 4273

    def list_claude_projects(self, cwd):
        return json.dumps({"projects": [{"name": "연습 프로젝트", "url": "https://claude.ai/project/fake"}]})

    def video_status(self, video_id, cwd):
        n = self.polls[video_id] = self.polls.get(video_id, 0) + 1
        if n < 3:
            return {"status": "processing", "video_url": "", "caption_url": "", "failure": ""}
        return {"status": "completed", "video_url": "https://fake.example/video.mp4", "caption_url": "https://fake.example/c.srt", "failure": ""}

    def tts_batch(self, items, voice_id, cwd, glossary=None):
        return {pg: "https://fake.example/voice_%d.mp3" % pg for pg, _ in items}

    def tts_paced(self, items, voice_id, cwd, glossary=None):
        return self.tts_batch(items, voice_id, cwd, glossary)

    def higgs_tts(self, items, ref_media, cwd):
        return {pg: "https://fake.example/higgs_%d.mp3" % pg for pg, _ in items}

    def download(self, url, dest):
        if dest.endswith(".mp3"):      # 연습: 짧은 소리
            ff = lecture_compose.find_ffmpeg()
            subprocess.run([ff, "-y", "-f", "lavfi", "-i", "sine=frequency=330", "-t", "3", "-c:a", "libmp3lame", dest], capture_output=True, check=True)
            return
        if dest.endswith(".srt"):
            with open(dest, "w", encoding="utf-8") as f:
                for i in range(6):
                    f.write("%d\n00:00:%02d,000 --> 00:00:%02d,900\n연습 자막 %d\n\n" % (i + 1, i * 2, i * 2 + 1, i + 1))
            return
        ff = lecture_compose.find_ffmpeg()
        if not ff:
            with open(dest, "wb") as f:
                f.write(b"FAKE-MP4 [practice mode]\n" * 50)
            return
        subprocess.run([ff, "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25", "-f", "lavfi", "-i", "sine=frequency=440",
                        "-t", "12", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", "-shortest", dest],
                       capture_output=True, check=True)

    def review(self, task, prompt):
        return '{"decision": "approve", "reason": "연습 모드"}'

    def plan(self, task, prompt, input_dirs, on_progress=None):
        self.cancelled.discard(task["id"])
        self._nap(task["id"], 2.5)
        users = [m for m in task["messages"] if m["role"] == "user"]
        if len(users) == 1:
            return json.dumps({"type": "question", "text": "[연습 모드] 몇 차시로 할까요? 1차시로 먼저 시험해 보는 걸 권해요."}, ensure_ascii=False)
        if any("영상" in m["text"] for m in users):
            return json.dumps({
                "type": "plan", "title": "연습 영상 일",
                "summary": "[연습 모드] 대본을 쓰고 점검한 뒤, 헤이젠 영상을 만들어요(크레딧 결재 후).",
                "steps": [
                    {"employee": "강의자료", "title": "나레이션 대본 작성", "instruction": "연습 모드", "output": "대본_script.md", "needs_approval": True},
                    {"employee": "검토 담당", "title": "대본 점검", "instruction": "연습 모드", "output": "검토_review.md", "needs_approval": False},
                    {"employee": "영상 제작", "title": "영상용 대본 정리", "instruction": "연습 모드", "output": "heygen_script.txt", "needs_approval": False, "pdf": "연습강의.pdf"},
                    {"employee": "영상 제작", "title": "헤이젠에서 영상 만들기", "instruction": "연습 모드", "output": "", "needs_approval": False, "heygen": True},
                    {"employee": "검토 담당", "title": "영상 결과 확인", "instruction": "연습 모드", "output": "영상확인_check.md", "needs_approval": False},
                ]}, ensure_ascii=False)
        return json.dumps({
            "type": "plan", "title": "연습 일",
            "summary": "[연습 모드] 강의자료가 대본 초안을 쓰고, 검토 담당이 점검하고, 영상 제작이 안내서를 씁니다.",
            "steps": [
                {"employee": "강의자료", "title": "나레이션 대본 초안", "instruction": "연습 모드", "output": "대본초안_script.md", "needs_approval": True},
                {"employee": "영상 제작", "title": "영상 제작 안내서", "instruction": "연습 모드", "output": "영상제작안내_guide.md", "needs_approval": False},
                {"employee": "검토 담당", "title": "최종 점검", "instruction": "연습 모드", "output": "검토결과_review.md", "needs_approval": False},
            ]}, ensure_ascii=False)

    def step(self, emp, task, step, prompt, input_dirs, on_progress=None):
        self.cancelled.discard(task["id"])
        for t in ("자료 읽는 중", "글 쓰는 중", "다듬는 중"):
            if on_progress:
                on_progress(t)
            self._nap(task["id"], 2.5)
        if step.get("output") == "heygen_script.txt" and step.get("pdf"):      # 연습: 쪽마다 대본과 아바타 나올 쪽을 나눔
            slides = sorted(x for x in os.listdir(os.path.join(task["outdir"], "_slides")) if x.endswith(".png"))
            pages = [{"page": i + 1, "text": "연습 모드 %d쪽 대본입니다. " % (i + 1) * 4, "avatar": i % 3 == 0} for i in range(len(slides))]
            with open(os.path.join(task["outdir"], "heygen_scenes.json"), "w", encoding="utf-8") as f:
                json.dump({"pages": pages}, f, ensure_ascii=False)
            return "[연습 모드] %d쪽으로 나눴어요" % len(pages)
        if step.get("heygen"):
            with open(os.path.join(task["outdir"], "video_job.json"), "w", encoding="utf-8") as f:
                json.dump({"video_id": "fake%028d" % int(time.time()), "title": task["title"]}, f)
            return "[연습 모드] 영상 제출 완료"
        out = step.get("output") or "결과_result.md"
        with open(os.path.join(task["outdir"], out), "w", encoding="utf-8") as f:
            if out.endswith(".txt"):
                f.write("안녕하세요, 연습 모드 강의 영상입니다. " * 120)
            else:
                f.write("# %s\n\n[연습 모드] %s 직원이 만든 가짜 결과물입니다.\n\n- 진짜 직원은 Claude 로그인 후 일해요.\n" % (step["title"], emp["name"]))
        return "[연습 모드] %s 완료" % out


# ---------------------------------------------------------------- 엔진
class Engine:
    def __init__(self):
        store.ensure_dirs()
        self.settings = load_settings()
        self.runner = FakeRunner(self.settings) if self.settings["fake"] else RealRunner(self.settings)
        self.q = queue.Queue()
        self.lock = threading.RLock()
        self.live = {}      # 이름 → {"state","text","since"}
        self.recent = {}    # 이름 → (시각, 글) 보고 완료 표시
        self.last_active = {e["name"]: time.time() for e in ROSTER["employees"]}
        self.notice = ""    # 화면 위에 띄울 안내 (로그인 필요 등)
        self.notice_tid = ""   # 그 안내가 어느 일의 오류인지 (그 일이 다시 돌거나 끝나면 안내를 지움, 11-13)
        self.rendering = {}  # 직원 이름 → 헤이젠가 영상 만드는 중 정보
        self.tts_blocked_until = 0   # 헤이젠 목소리가 401 로 막힌 것을 본 뒤 이 시각까지는 힉스필드 대비 길로 바로 감
        self.activity = {}   # 직원 이름 → 방금 한 일 기록 (시각, 글)
        self.promotions = []  # 이번에 서버가 켜진 뒤 승진한 기록 (화면 축하 알림용)
        self._seed_notebooks()
        self._recover()
        self._tlocks = {}
        self.chrome_lock = threading.Lock()
        self.meet_hold = {}   # 일 번호 → (모인 직원들, 끝나는 시각, 말풍선) : 계획이 정해진 뒤 잠깐 모이는 회의   # Chrome(ChatGPT) 화면은 한 번에 한 직원만 조작
        for _ in range(6):      # 서로 다른 프로젝트(일)는 동시에 진행해요. 같은 일 안에서는 순서대로.
            threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._secretary_loop, daemon=True).start()      # 📅 비서 아침 보고
        threading.Thread(target=self.build_office_map, daemon=True).start()     # 기획실장 배경지식: 사무실 작업 지도

    # ---- 시작할 때
    def _seed_notebooks(self):
        for e in ROSTER["employees"]:
            p = store.notebook_path(e["name"])
            if not os.path.exists(p):
                seed = prompts.NOTEBOOK_SEED.get(e["name"], "- 아직 쌓인 내용이 없어요.")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("# 업무 수첩 — %s\n\n## 교수님이 정한 규칙\n%s\n\n## 승인된 방식\n\n## 싫어하신 것과 이유\n\n" % (e["name"], seed))

    def _recover(self):
        """서버가 꺼졌다 켜졌을 때: 하던 단계는 처음부터 다시"""
        tasks = store.load("tasks", [])
        changed = False
        if self.settings.get("fake"):
            # 연습 사무실: 진짜 사무실에서 베껴 온 일도 결과는 반드시 연습 폴더 안에만 써요 (진짜 결과물 덮어쓰기 방지)
            root = os.path.realpath(self.settings["output_root"])
            for t in tasks:
                od = t.get("outdir")
                if od and not os.path.realpath(od).startswith(root + os.sep):
                    t["outdir"] = os.path.join(root, os.path.basename(od.rstrip(os.sep)))
                    os.makedirs(t["outdir"], exist_ok=True)
                    changed = True
        for t in tasks:
            if t["status"] == "planning":
                self.q.put(("plan", t["id"]))
            elif t["status"] == "running" and t.get("plan"):
                for i, s in enumerate(t["plan"]["steps"]):
                    if s["status"] == "running":
                        s["status"] = "pending"
                        changed = True
                    elif s["status"] == "composing":      # 합치던 영상: 헤이젠에는 다시 맡기지 않고 합치기만 다시 함
                        self.rendering[s["employee"]] = {"task": t["title"], "taskId": t["id"], "step": s["title"], "since": time.time(), "kind": "compose"}
                        threading.Thread(target=self._compose_lecture, args=(t["id"], i), daemon=True).start()
                    elif s["status"] == "rendering":      # 헤이젠가 만드는 중이던 영상: 다시 확인만 하면 됨(다시 만들지 않음)
                        self.rendering[s["employee"]] = {"task": t["title"], "taskId": t["id"], "step": s["title"], "since": time.time()}
                        self._schedule_poll(t["id"], i, 20)
                self.q.put(("next", t["id"]))
        for t in tasks:
            if t["status"] == "error" and t.get("autoRetryAt"):
                self._schedule_auto_retry(t["id"], t["autoRetryAt"])
        if changed:
            store.save("tasks", tasks)

    # ---- 저장소 도우미
    def _tasks(self):
        return store.load("tasks", [])

    def _save_task(self, task, force=False):
        with self.lock:
            tasks = self._tasks()
            for i, t in enumerate(tasks):
                if t["id"] == task["id"]:
                    if not force:
                        # 일꾼이 옛 정보를 들고 있다가 '멈춤/그만둠'과 멈춘 기록을 덮어쓰지 않게 함
                        if t.get("status") in ("paused", "cancelled") and task.get("status") not in ("paused", "cancelled"):
                            task["status"] = t["status"]
                            task["pausedFrom"] = t.get("pausedFrom")
                        task["events"] = t.get("events", task.get("events", []))
                        if t.get("heygen") and not task.get("heygen"):     # 일마다 정한 아바타(옷)를 옛 정보로 지우지 않게
                            task["heygen"] = t["heygen"]
                    # 그사이 교수님이 남긴 말을 옛 정보로 지우지 않게 (기획실장이 생각하는 중에 보낸 말 등)
                    seen = {(m.get("ts"), m.get("text")) for m in task.get("messages", [])}
                    extra = [m for m in t.get("messages", []) if (m.get("ts"), m.get("text")) not in seen]
                    if extra:
                        task["messages"] = sorted(task.get("messages", []) + extra, key=lambda m: m.get("ts", ""))
                    tasks[i] = task
                    break
            else:
                tasks.append(task)
            store.save("tasks", tasks)

    @staticmethod
    def public(task):
        t = dict(task)
        t.pop("outdir", None)
        return t

    def get_task(self, tid):
        for t in self._tasks():
            if t["id"] == tid:
                return t
        return None

    def _approvals(self):
        return store.load("approvals", [])

    def _add_approval(self, **kw):
        with self.lock:
            aps = self._approvals()
            ap = {"id": "a-%d-%d" % (int(time.time()), len(aps) + 1), "status": "pending", "reason": "",
                  "created": store.now(), "decided": "", "files": [], "stepIndex": None, "detail": ""}
            ap.update(kw)
            aps.append(ap)
            store.save("approvals", aps)
        self._auto_review(ap)
        return ap

    OWN_CHECK_WHO = re.compile(r"나한테|저한테|나에게|저에게|내게|내가|제가|교수님께|교수님이")
    OWN_CHECK_WHAT = re.compile(r"결재|확인|보여|들려|검토|골라|고를")

    def wants_own_check(self, task):
        """교수님이 '나한테 보여주고/들려주고/결재받고' 하라고 하신 일인지 (그렇다면 단계·크레딧 결재는 자동 승인하지 않음)"""
        for m in task.get("messages", []):
            if m.get("role") == "user" and m.get("kind") != "status":
                t = m.get("text", "")
                if self.OWN_CHECK_WHO.search(t) and self.OWN_CHECK_WHAT.search(t):
                    return True
        return False

    def _auto_policy(self):
        pol = {"plan": True, "step": True, "final": True, "credit_limit": 700}
        pol.update(self.settings.get("auto_approve") or {})
        return pol

    def _auto_review(self, ap):
        """기획실장이 직접 검토해서 승인해요. 질문은 교수님 몫, 실패 뒤 재시도·한도를 넘는 크레딧은 교수님께 물어봐요."""
        kind, pol = ap.get("kind"), self._auto_policy()
        if ap.get("manual"):          # 힉스필드 크레딧 등: 늘 교수님 결재
            return
        if kind not in ("plan", "step", "final", "credit") or not pol.get(kind if kind != "credit" else "credit_limit"):
            return
        if kind == "step":
            t0 = self.get_task(ap.get("taskId")) or {}
            st0 = ((t0.get("plan") or {}).get("steps") or [{}] * 99)
            if ap.get("stepIndex") is not None and ap["stepIndex"] < len(st0) and st0[ap["stepIndex"]].get("employee") == TRAIN_EMP:
                store.log("교육 내용은 교수님이 결재해요: %s" % ap["id"])
                return
        if kind in ("step", "credit") and self.wants_own_check(self.get_task(ap.get("taskId")) or {}):
            store.log("교수님이 직접 보겠다고 하신 일이라 자동 승인하지 않아요: %s" % ap["id"])
            return
        why = ""
        if kind == "credit":
            det = ap.get("detail", "")
            if det.startswith("⚠"):
                return          # 실패한 뒤 다시 만드는 건 교수님이 확인
            m = re.search(r"예상 크레딧: 약 ([\d,]+) ~ ([\d,]+)", det)
            r = re.search(r"남은 크레딧: ([\d,]+)", det)
            hi = int(m.group(2).replace(",", "")) if m else None
            left = int(r.group(1).replace(",", "")) if r else None
            if hi is None or hi > int(pol["credit_limit"]) or (left is not None and left < hi * 2):
                return
            why = "기획실장 자동 승인: 예상 크레딧 최대 %d, 정해 둔 한도 %d 이하" % (hi, pol["credit_limit"])
        elif kind == "final":
            t = self.get_task(ap["taskId"])
            steps = (t or {}).get("plan", {}).get("steps", [])
            if not steps or any(s.get("status") != "done" for s in steps):
                return
            # 최종 보고는 교수님이 보고 결재 (교수님 결정 11-09). 영상 일(크레딧 결재·발음 확인을 이미 받음)과 비서 아침 보고만 자동
            is_video = any(x.get("heygen") or x.get("employee") == "영상 제작" for x in steps)
            routine = self._request_text(t or {}).lstrip().startswith(("📅 아침 보고", "📚 자기계발"))
            if not (is_video or routine):
                store.log("최종 보고는 교수님이 결재해요: %s" % ap["id"])
                return
            why = "기획실장 자동 승인: 영상 일은 크레딧·발음 결재를 이미 받았어요" if is_video else "기획실장 자동 승인: 비서 아침 보고"
        else:
            why = "기획실장 자동 승인: 계획·결과를 검토했어요"

        def go():
            try:
                self.decide(ap["id"], "approve", why)
                store.log("자동 승인: %s (%s) %s" % (ap["id"], kind, why))
            except Exception as ex:
                store.log("자동 승인 실패 %s: %r" % (ap["id"], ex))
        t = threading.Timer(3.0, go)
        t.daemon = True
        t.start()

    def _update_approval(self, ap):
        with self.lock:
            aps = self._approvals()
            for i, a in enumerate(aps):
                if a["id"] == ap["id"]:
                    aps[i] = ap
            store.save("approvals", aps)

    def _stats(self):
        return store.load("employees", {})

    def _bump(self, name, first_pass, plan=False):
        """단계(기획실장은 계획)가 승인됨: +10점, 한 번에 승인이면 +5점 보너스. 오르면 승진 축하"""
        if name == "기획실장" and not plan:      # 기획실장은 '세운 계획'으로만 셈 (회의록 정리 같은 단계는 세지 않음)
            return
        self._score(name, PTS_DONE + (PTS_FIRST if first_pass else 0),
                    "계획 승인" + (" (한 번에)" if first_pass else "") if plan else "일 승인" + (" (한 번에)" if first_pass else ""),
                    count=True, first_pass=first_pass)

    def _score(self, name, delta, why, tid=None, count=False, first_pass=False):
        """점수 더하기/빼기 + 기록. 깎여도 한 번 오른 직급은 내려가지 않음"""
        if name not in EMP:
            return
        with self.lock:
            st = self._stats()
            s = st.setdefault(name, {"done": 0, "firstPass": 0, "score": 0})
            before = rank_view(s, name)
            s["score"] = s.get("score", 0) + delta
            if count:
                s["done"] = s.get("done", 0) + 1
                if first_pass:
                    s["firstPass"] = s.get("firstPass", 0) + 1
            log = s.setdefault("log", [])
            log.append({"id": "%d%s" % (time.time() * 1000, os.urandom(2).hex()), "ts": store.now(), "delta": delta, "why": why[:120], "tid": tid or ""})
            del log[:-200]
            after = rank_view(s, name)
            promoted = after["level"] > before["level"]
            s["level"] = max(int(s.get("level", 0)), after["level"])
            if promoted:
                s.setdefault("promotions", []).append({"rank": after["rank"], "ts": store.now(), "score": s["score"]})
            store.save("employees", st)
        if delta < 0:
            store.log("점수 %d: %s — %s" % (delta, name, why[:60]))
        if promoted:
            self.promotions.append({"id": "%s-%d" % (name, s.get("done", 0)), "name": name, "from": before["rank"],
                                    "rank": after["rank"], "ts": store.now(), "done": s.get("done", 0),
                                    "passRate": after.get("passRate")})
            self.promotions = self.promotions[-20:]
            self.recent[name] = (time.time(), "🎉 %s(으)로 승진!" % after["rank"])
            store.append_notebook(name, "- 🎉 %s → %s 승진 (%d점, 한 번에 통과 %s%%) (%s)" % (
                before["rank"], after["rank"], s["score"], after.get("passRate"), store.now()[:10]), "승인된 방식")
            store.log("승진: %s %s → %s" % (name, before["rank"], after["rank"]))

    def undo_score(self, name, entry_id):
        """교수님이 점수 기록을 취소 (예: 지적이 아니었는데 깎인 것)"""
        with self.lock:
            st = self._stats()
            s = st.get(name) or {}
            for e in s.get("log", []):
                if e.get("id") == entry_id and not e.get("undone"):
                    e["undone"] = True
                    s["score"] = s.get("score", 0) - e["delta"]
                    store.save("employees", st)
                    store.log("점수 취소: %s %+d (%s)" % (name, -e["delta"], e["why"][:40]))
                    return {"ok": True, "score": s["score"]}
        raise ValueError("그 기록을 찾지 못했어요.")

    # 대표 지적 알아보기: 교수님 말에 이런 말이 있으면 그 일을 맡은 직원(또는 기획실장)에게서 점수를 깎아요
    SCOLD = re.compile(r"왜\s*그래|왜\s*이래|이상(해|한데|하잖|하다)|틀렸|잘못(됐|했|되었)|엉뚱|몇\s*번(을|이나)?\s*(말|얘기|이야기)|그게\s*아니|아니라고|답답|또\s*(틀|그러|그래|이러)|제대로\s*(안|좀)|말\s*안\s*듣|이해\s*(못|를\s*못)")

    def _scold_check(self, task, text):
        """교수님 말이 지적이면 −10점 (같은 일에서 또 하면 −15점). 지금 일하는 직원, 없으면 기획실장"""
        if not text or not self.SCOLD.search(text):
            return
        steps = (task.get("plan") or {}).get("steps", [])
        who = "기획실장"
        if task.get("status") in ("running", "paused", "error", "done") and steps:
            cur = next((x for x in steps if x.get("status") in ("running", "waiting", "rendering", "composing")), None) \
                or next((x for x in reversed(steps) if x.get("status") == "done"), None)
            if cur:
                who = cur["employee"]
        n = task.get("scolds", 0)
        task["scolds"] = n + 1
        self._score(who, PTS_SCOLD_AGAIN if n else PTS_SCOLD, "대표 지적: '%s'" % " ".join(text.split())[:60], task["id"])

    # ---- 일 받기
    @staticmethod
    def _norm(text):
        return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")[:200]

    def find_duplicate(self, text):
        """새로 맡기려는 일과 비슷한, 아직 진행 중이거나 방금 끝난 일을 찾아요"""
        import difflib
        a = self._norm(text)
        if len(a) < 4:
            return None
        best = None
        now = time.time()
        for t in self._tasks():
            st = t.get("status")
            if st == "cancelled":
                continue
            if st == "done":
                try:
                    fin = time.mktime(time.strptime(t.get("finished") or "", "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    continue
                if now - fin > DUP_DONE_HOURS * 3600:
                    continue
            first = next((m["text"] for m in t.get("messages", []) if m["role"] == "user"), "")
            b = self._norm(first)
            if not b:
                continue
            r = 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()
            if r >= DUP_RATIO:
                key = (st != "done", r)      # 아직 하고 있는 일을 끝난 일보다 먼저 알려요
                if best is None or key > best[3]:
                    best = (r, t, first, key)
        if not best:
            return None
        r, t, first, _ = best
        steps = (t.get("plan") or {}).get("steps", [])
        cur = next((x for x in steps if x.get("status") in ("running", "rendering", "composing", "waiting")), None) \
            or next((x for x in steps if x.get("status") != "done"), None)
        return {"id": t["id"], "title": t["title"], "status": t["status"], "created": t["created"],
                "finished": t.get("finished", ""), "request": first[:300], "similar": round(r * 100),
                "active": t["status"] != "done",
                "canAdd": t["status"] in ("running", "asking", "plan_review", "review", "error"),
                "current": ("%s — %s" % (cur["employee"], cur["title"])) if cur and t["status"] != "done" else "",
                "done": sum(1 for x in steps if x.get("status") == "done"), "total": len(steps)}

    def create_task(self, text, project=None, compare=False, force=False, attachments=None):
        text = (text or "").strip()
        if not text:
            raise ValueError("일 내용을 적어 주세요.")
        if not force:
            dup = self.find_duplicate(text)
            if dup:
                store.log("비슷한 일 알림: '%s' ↔ %s (%d%%)" % (text[:30], dup["id"], dup["similar"]))
                raise DuplicateTask(dup)
        with self.lock:
            tasks = self._tasks()
            tid = "t-%s-%03d" % (time.strftime("%Y%m%d"), len(tasks) + 1)
            title = text.replace("\n", " ")[:24]
            outdir = os.path.join(self.settings["output_root"], "%s_%s" % (tid, re.sub(r"[^0-9A-Za-z가-힣]+", "_", title).strip("_")))
            os.makedirs(outdir, exist_ok=True)
            text += self._attach({"outdir": outdir}, attachments)
            task = {"id": tid, "title": title, "status": "planning", "created": store.now(), "outdir": outdir,
                    "messages": [{"role": "user", "text": text, "ts": store.now()}], "plan": None, "error": "",
                    "project": project if any(x["id"] == project for x in self.projects()) else self._guess_project(text),
                    "projectChosen": any(x["id"] == project for x in self.projects())}
            tasks.append(task)
            store.save("tasks", tasks)
        if compare:
            self._install_compare_plan(task)
            return self.get_task(tid)
        if text.startswith("🗣 대표 회의"):
            self._install_ceo_meeting_plan(task, text)
            return self.get_task(tid)
        if text.startswith("📅 아침 보고"):
            self._install_secretary_plan(task)
            return self.get_task(tid)
        if text.startswith("📚 자기계발"):
            self._install_study_plan(task, text)
            return self.get_task(tid)
        if seongseo.is_request(text):
            self._install_seongseo_plan(task, text)
            return self.get_task(tid)
        self.q.put(("plan", tid))
        return task

    # ---- 비교 모드: 같은 일을 Claude 와 GPT 에게 동시에 맡기고, 검토 담당이 비교·합본, 마지막 선택은 교수님
    def _install_compare_plan(self, task):
        def st(emp, title, **kw):
            d = {"employee": emp, "title": title, "instruction": "", "output": "", "needs_approval": False,
                 "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": []}
            d.update(kw)
            return d
        plan = {"summary": "같은 일을 Claude(강의자료)와 GPT(이미지 담당이 Chrome 의 ChatGPT 로)에게 동시에 맡겨요. "
                           "검토 담당이 두 답을 나란히 비교하고 좋은 부분만 모은 합본을 만들어요. 어느 것을 쓸지는 교수님이 골라요.",
                "steps": [st("강의자료", "Claude·GPT 동시에 답안 만들기", job="compare", sides={}),
                          st("검토 담당", "두 답안 비교하고 합본 만들기", instruction=prompts.COMPARE_REVIEW, output=prompts.COMPARE_REPORT)]}
        task["compare"] = True
        if not task["title"].startswith("⚖"):
            task["title"] = ("⚖ " + task["title"])[:30]
        task["plan"] = plan
        task["status"] = "plan_review"
        task["messages"].append({"role": "planner", "text": "Claude 와 GPT 에게 같이 맡겨서 비교할게요.\n" + plan["summary"], "ts": store.now()})
        self._save_task(task, force=True)
        self._add_approval(taskId=task["id"], kind="plan", owner="기획실장", title="계획서: " + task["title"],
                           detail=plan["summary"] + "\n\n" + "\n".join("%d. %s — %s" % (i + 1, s["employee"], s["title"]) for i, s in enumerate(plan["steps"])))

    def background(self):
        """기획실장 배경지식 (교수님 이력·작업 지도 요약). data/knowledge/기획실장_배경지식.md, 너무 길면 앞부분만"""
        try:
            return open(os.path.join(KNOW_DIR, "기획실장_배경지식.md"), encoding="utf-8").read()[:6000]
        except OSError:
            return ""

    def build_office_map(self):
        """사무실에서 한 일을 주제별로 모은 작업 지도 → data/knowledge/작업지도_사무실.md (켤 때·매일 새로 씀)"""
        try:
            os.makedirs(KNOW_DIR, exist_ok=True)
            tasks, ps = self._tasks(), self.projects()
            name = {p["id"]: p["name"] for p in ps}
            by = {}
            for t in tasks:
                by.setdefault(name.get(self.task_project(t), "기타"), []).append(t)
            lines = ["# 사무실 작업 지도 (자동, %s)" % store.now()[:16], "",
                     "> 사무실에서 맡긴 일을 주제별 프로젝트로 모음. 최근 것이 위. ✅끝남 ⏳진행 ⚠멈춤 ✕취소", ""]
            mark = {"done": "✅", "error": "⚠", "cancelled": "✕"}
            for pn, ts in by.items():
                lines.append("## %s (%d건)" % (pn, len(ts)))
                for t in sorted(ts, key=lambda x: x.get("created", ""), reverse=True)[:25]:
                    lines.append("- %s %s %s%s" % (mark.get(t["status"], "⏳"), t.get("created", "")[:10], t.get("final_name") or t["title"],
                                                  (" — 최종본: " + os.path.relpath(t["final_dir"], ROOT)) if t.get("final_dir") else ""))
                lines.append("")
            with open(os.path.join(KNOW_DIR, "작업지도_사무실.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as ex:
            store.log("작업 지도 만들기 오류: %r" % ex)

    def office_brief(self):
        """비서 아침 보고에 넣을 사무실 현황 (결재 대기·진행 중·멈춘 일)"""
        tasks, aps = self._tasks(), self._approvals()
        pend = [a for a in aps if a["status"] == "pending"]
        run = [t for t in tasks if t["status"] in ("running", "planning", "plan_review", "asking", "paused")]
        err = [t for t in tasks if t["status"] == "error"]
        out = ["- 결재 대기 %d건%s" % (len(pend), (": " + ", ".join(a.get("title", "")[:30] for a in pend[:6])) if pend else "")]
        out.append("- 진행 중인 일 %d건%s" % (len(run), (": " + ", ".join(t["title"] for t in run[:8])) if run else ""))
        out.append("- 멈춘 일(오류) %d건%s" % (len(err), (": " + ", ".join("%s(%s)" % (t["title"], (t.get("error") or "")[:40]) for t in err[:6])) if err else ""))
        return "\n".join(out)

    def _install_secretary_plan(self, task):
        """📅 비서 아침 보고: 기획실장 계획 없이 비서 한 단계로 (구글 캘린더 읽기 + 사무실 현황)"""
        today = time.strftime("%Y-%m-%d")
        step = {"employee": SEC_EMP, "title": "오늘의 일정 보고", "engine": "chat", "needs_approval": False,
                "instruction": prompts.SECRETARY_MORNING.format(today=today, brief=self.office_brief()),
                "output": "오늘의_일정_보고_daily.md", "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": []}
        task["plan"] = {"summary": "비서가 구글 캘린더로 오늘·내일 일정을 읽고, 사무실 현황과 함께 아침 보고를 올려요.", "steps": [step]}
        task["final_name"] = "비서 아침 보고"
        task["status"] = "plan_review"
        task["messages"].append({"role": "planner", "text": "비서가 아침 보고를 준비해요.", "ts": store.now()})
        self._save_task(task, force=True)
        self._add_approval(taskId=task["id"], kind="plan", owner="기획실장", title="계획서: " + task["title"], detail=task["plan"]["summary"])

    def _install_ceo_meeting_plan(self, task, text):
        """🗣 대표 회의: 참석 직원마다 자기 일 쪽 의견 → 기획실장이 회의록·결정·할 일 정리 (3층 회의실 2 에 모임)"""
        topic = text.split("\n")[0].replace("🗣 대표 회의:", "").strip()
        who = []
        for line in text.split("\n"):
            if line.startswith("참석:"):
                who = [w.strip() for w in line[3:].split(",") if w.strip() in EMP and EMP[w.strip()]["active"] and w.strip() != "기획실장"]
        steps = []
        for w in who:
            steps.append({"employee": w, "title": "회의 의견 내기", "engine": "chat", "needs_approval": False,
                          "instruction": prompts.CEO_MEETING_OPINION.format(topic=topic, role=EMP[w]["role"]),
                          "output": "회의_의견_%s.md" % re.sub(r"[^0-9A-Za-z가-힣]+", "_", w), "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": []})
        steps.append({"employee": "기획실장", "title": "회의록·할 일 정리", "engine": "chat", "needs_approval": False,
                      "instruction": prompts.CEO_MEETING_MINUTES.format(topic=topic, who=", ".join(who)),
                      "output": "회의록_meeting.md", "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": []})
        task["plan"] = {"summary": "대표 회의 — %s. 참석: %s. 직원마다 의견을 내고 기획실장이 회의록과 할 일을 정리해요." % (topic, ", ".join(who)), "steps": steps}
        task["title"] = ("🗣 " + topic)[:30]
        task["final_name"] = ("회의록 — " + topic)[:40]
        task["ceoMeeting"] = who
        task["status"] = "plan_review"
        task["messages"].append({"role": "planner", "text": "3층 회의실 2 에서 대표 회의를 열어요. 참석: " + ", ".join(who), "ts": store.now()})
        self._save_task(task, force=True)
        self._add_approval(taskId=task["id"], kind="plan", owner="기획실장", title="계획서: " + task["title"], detail=task["plan"]["summary"])

    def _install_study_plan(self, task, text):
        """📚 자기계발: 그 직원 혼자 자기 일 쪽 최신 흐름·방법을 공부하고 공부노트를 씀 (크레딧 없음)"""
        who = text.split(":", 1)[-1].strip().split()[0] if ":" in text else ""
        if who not in EMP:
            who = next((n for n in EMP if n in text), "")
        topic = prompts.STUDY_TOPICS.get(who, "내 일을 더 잘하는 최신 방법")
        task["study"], task["studyTopic"], task["noFinal"] = who, topic, True
        task["plan"] = {"summary": "%s 직원 자기계발 — %s" % (who, topic), "steps": [{
            "employee": who, "title": "자기계발 공부", "engine": "chat", "needs_approval": False,
            "instruction": prompts.STUDY.format(role=EMP[who]["role"], topic=topic),
            "output": "공부노트_study.md", "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": []}]}
        task["title"] = ("📚 %s 자기계발" % who)[:30]
        task["status"] = "plan_review"
        self._save_task(task, force=True)
        self._add_approval(taskId=task["id"], kind="plan", owner="기획실장", title="계획서: " + task["title"], detail=task["plan"]["summary"])

    def _study_tick(self):
        """하루 한 번(settings.study.time, 기본 14:00 이후) 쉬고 있는 직원 두 명에게 자기계발 공부를 맡김"""
        cfg = {"time": "14:00", "per_day": 2, "enabled": True}
        cfg.update(self.settings.get("study") or {})
        if not cfg.get("enabled") or time.strftime("%H:%M") < str(cfg["time"]):
            return
        st = store.load("study", {})
        today = time.strftime("%Y-%m-%d")
        if st.get("day") == today:
            return
        busy = {x["employee"] for t in self._tasks() if t["status"] in ("running", "planning", "plan_review")
                for x in (t.get("plan") or {}).get("steps", [])}
        last = st.get("last", {})
        cands = [e["name"] for e in ROSTER["employees"] if e["active"] and e["name"] not in ("기획실장", SEC_EMP) and e["name"] not in busy]
        cands.sort(key=lambda n: last.get(n, ""))
        for n in cands[:int(cfg["per_day"])]:
            self.create_task("📚 자기계발: %s" % n, None, False, True)
            last[n] = today
        st.update({"day": today, "last": last})
        store.save("study", st)

    def _secretary_loop(self):
        """매일 정해진 시각(settings.secretary.morning, 기본 08:00)에 비서 아침 보고를 한 번 맡김"""
        while True:
            time.sleep(60)
            try:
                cfg = {"morning": "08:00", "enabled": True}
                cfg.update(self.settings.get("secretary") or {})
                if not cfg.get("enabled") or self.settings.get("fake") or not (EMP.get(SEC_EMP) or {}).get("active"):
                    continue
                today = time.strftime("%Y-%m-%d")
                st = store.load("secretary", {})
                try:
                    self._study_tick()          # 📚 자기계발 (하루 한 번)
                except Exception as ex2:
                    store.log("자기계발 맡기기 오류: %r" % ex2)
                if st.get("map") != today:      # 하루 한 번 사무실 작업 지도 새로 쓰기
                    self.build_office_map()
                    st["map"] = today
                    store.save("secretary", st)
                if st.get("last") == today or time.strftime("%H:%M") < str(cfg["morning"]):
                    continue
                st["last"] = today
                store.save("secretary", st)
                t = self.create_task("📅 아침 보고 (%s)" % today, None, False, True)
                store.log("비서 아침 보고 맡김: %s" % t["id"])
            except Exception as ex:
                store.log("비서 아침 보고 오류: %r" % ex)

    def _request_text(self, task):
        return "\n\n".join(m["text"] for m in task["messages"] if m["role"] == "user" and m.get("kind") != "status")

    def _do_compare_step(self, task, idx, step):
        tid = task["id"]
        a_emp, b_emp = step["employee"], IMG_EMP
        out = task["outdir"]
        sides = step.setdefault("sides", {})
        for k, f in (("A", prompts.COMPARE_A), ("B", prompts.COMPARE_B)):      # 이미 받아 둔 쪽은 다시 하지 않음
            if sides.get(k) == "done" and not os.path.exists(os.path.join(out, f)):
                sides[k] = ""
        step["status"] = "running"
        step["started"] = store.now()
        step["finished"] = ""
        self._save_task(task)
        texts = self._prepare_texts(task)
        errs = {}

        def run_a():
            name = a_emp
            try:
                sub = dict(step, title="Claude 답안 쓰기", output=prompts.COMPARE_A, instruction=prompts.COMPARE_CLAUDE)
                self._set_live(name, "작업 중", "Claude 답안 쓰는 중", task=task["title"], step=sub["title"])
                self.live[name]["taskId"] = tid
                prompt = prompts.step_prompt(task, sub, idx, store.read_notebook(name), step.get("feedback", []), texts, [], out)
                self.runner.step(EMP[name], task, sub, prompt, self.settings["input_dirs"], lambda t: self._set_live(name, "작업 중", t))
                if not os.path.exists(os.path.join(out, prompts.COMPARE_A)):
                    raise RunnerError("Claude 쪽 답안 파일이 만들어지지 않았어요.", "")
                sides["A"] = "done"
                self._clear_live(name, "Claude 답안 완료")
            except RunnerCancelled:
                errs["cancel"] = True
                self._clear_live(name)
            except Exception as ex:
                errs["A"] = ex
                self._clear_live(name)

        def run_b():
            name = b_emp
            try:
                self._set_live(name, "작업 중", "Chrome 차례 기다리는 중", task=task["title"], step="GPT 답 받아 오기")
                self.live[name]["taskId"] = tid
                with self.chrome_lock:
                    if errs.get("cancel") or (self.get_task(tid) or {}).get("status") != "running":
                        raise RunnerCancelled()
                    self._set_live(name, "작업 중", "ChatGPT 에 묻는 중")
                    sub = dict(step, title="GPT 답 받아 오기", output=prompts.COMPARE_B)
                    prompt = prompts.gpt_answer_prompt(self._request_text(task), prompts.COMPARE_B, step.get("feedback", []))
                    res = self.runner.step(EMP[name], task, sub, prompt, [], lambda t: self._set_live(name, "작업 중", t))
                if not os.path.exists(os.path.join(out, prompts.COMPARE_B)):
                    raise RunnerError("GPT 답을 받아 오지 못했어요: " + (res or "").strip()[:300], res or "")
                sides["B"] = "done"
                self._clear_live(name, "GPT 답 받아 오기 완료")
            except RunnerCancelled:
                errs["cancel"] = True
                self._clear_live(name)
            except Exception as ex:
                errs["B"] = ex
                self._clear_live(name)

        started = time.time() - 1
        ths = []
        for k, fn in (("A", run_a), ("B", run_b)):
            if sides.get(k) != "done":
                th = threading.Thread(target=fn, daemon=True)
                th.start()
                ths.append(th)
        for th in ths:
            th.join()
        fresh = self.get_task(tid) or task
        fs = fresh["plan"]["steps"][idx]
        fs["sides"] = sides
        if errs.get("cancel") or fresh.get("status") in ("paused", "cancelled"):
            fs["status"] = "pending"
            self._save_task(fresh)
            return
        if errs.get("A") or errs.get("B"):
            fs["status"] = "pending"
            parts = []
            for k, label in (("A", "Claude"), ("B", "GPT")):
                ex = errs.get(k)
                if ex:
                    parts.append("%s 쪽: %s" % (label, ex.friendly if isinstance(ex, RunnerError) else ex))
            msg = " / ".join(parts) + " — 된 쪽은 그대로 두고, 「다시 시도」를 누르면 안 된 쪽만 다시 해요."
            self._fail(fresh, a_emp if errs.get("A") else b_emp, RunnerError(msg, ""))
            return
        fs["files"] = sorted(set(fs.get("files", []) + self._changed_files(fresh, started)) | {prompts.COMPARE_A, prompts.COMPARE_B})
        fs["result"] = "Claude 답안(%s)과 GPT 답안(%s)을 받았어요." % (prompts.COMPARE_A, prompts.COMPARE_B)
        fs["finished"] = store.now()
        fs["status"] = "done"
        self._bump(a_emp, fs.get("revisions", 0) == 0)
        self._bump(b_emp, fs.get("revisions", 0) == 0)
        self._save_task(fresh)
        self.q.put(("next", tid))

    def compare_view(self, tid):
        """나란히 보기: 두 답안, 비교 보고서, 합본, 추천, 교수님 선택"""
        task = self.get_task(tid)
        if not task or not task.get("compare"):
            raise ValueError("비교 모드로 맡긴 일이 아니에요.")

        def rd(f):
            pth = os.path.join(task["outdir"], f)
            try:
                with open(pth, encoding="utf-8") as fh:
                    return fh.read()
            except OSError:
                return ""
        rep = rd(prompts.COMPARE_REPORT)
        m = re.search(r"추천\s*[:：]\s*\**\s*(A|B|합본)", rep)
        ap = next((a for a in self._approvals() if a["taskId"] == tid and a["kind"] == "pick" and a["status"] == "pending"), None)
        return {"id": tid, "title": task["title"], "status": task["status"], "request": self._request_text(task),
                "a": rd(prompts.COMPARE_A), "b": rd(prompts.COMPARE_B), "report": rep, "merged": rd(prompts.COMPARE_MERGED),
                "recommend": m.group(1) if m else "", "approval": ap["id"] if ap else "", "pick": task.get("pick"),
                "stats": store.load("compare_stats", {"A": 0, "B": 0, "M": 0})}

    def _install_seongseo_plan(self, task, text):
        """성서 40주간 사이트 제작: 기획실장(claude) 대신 미리 짠 계획을 바로 결재함에 올림."""
        cfg = self.settings["seongseo"]
        test = seongseo.wants_test(text)
        plan = seongseo.build_plan(cfg, test=test)
        if task.get("title", "").strip() in ("", text.replace("\n", " ")[:24]):
            task["title"] = "성서 40주간 사이트 제작" + (" (테스트)" if test else "")
        task["plan"] = plan
        task["status"] = "plan_review"
        task["messages"].append({"role": "planner",
                                 "text": "계획서를 올렸어요. 결재함에서 확인해 주세요.\n" + plan["summary"], "ts": store.now()})
        self._save_task(task, force=True)
        detail = plan["summary"] + "\n\n" + "\n".join(
            "%d. %s — %s%s" % (i + 1, s["employee"], s["title"],
                               " (비용 결재 후 시작)" if s.get("job") == "art" else "")
            for i, s in enumerate(plan["steps"]))
        self._add_approval(taskId=task["id"], kind="plan", owner="기획실장",
                           title="계획서: " + task["title"], detail=detail)

    # ---- 첨부 파일: 먼저 data/uploads 에 올려 두고, 일에 붙일 때 그 일의 폴더(첨부_attachments)로 옮김
    UPLOAD_DIR = os.path.join(store.DATA, "uploads")
    MAX_UPLOAD = 500 * 1024 * 1024

    @staticmethod
    def safe_name(name):
        name = os.path.basename((name or "").replace("\\", "/")).strip() or "파일"
        name = re.sub(r'[\x00-\x1f/:*?"<>|]+', "_", name)
        return name[:120] or "파일"

    def _keep_in_library(self, path):
        """올린 파일·폴더를 자료실에도 복사(맥 복제라 자리 거의 안 씀). 같은 이름이 있으면 없는 파일만 더함"""
        try:
            dst = os.path.join(LIBRARY, os.path.basename(path.rstrip(os.sep)))
            if os.path.isdir(path):
                for cur, _d, files in os.walk(path):
                    for f in files:
                        s_ = os.path.join(cur, f)
                        d_ = os.path.join(dst, os.path.relpath(s_, path))
                        if not os.path.exists(d_):
                            self._clone(s_, d_)
            elif not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(path):
                if os.path.exists(dst):
                    b, e = os.path.splitext(dst)
                    dst = "%s_%s%s" % (b, time.strftime("%m%d%H%M"), e)
                self._clone(path, dst)
        except Exception as ex:
            store.log("자료실 보관 실패 %s: %r" % (path, ex))

    def save_upload(self, name, stream, length, group=None, relpath=None):
        """파일 하나 받기. group(폴더 묶음 번호)과 relpath(폴더 안 위치)가 오면 폴더 모양 그대로 한 묶음에 모아 둠"""
        if length <= 0:
            raise ValueError("빈 파일이에요.")
        if length > self.MAX_UPLOAD:
            raise ValueError("파일이 너무 커요 (한 번에 500MB까지).")
        if group and relpath:
            if not re.fullmatch(r"\d{10,13}[0-9a-f]{8}", str(group)):
                raise ValueError("폴더 묶음 번호가 이상해요.")
            uid = "u" + group
            parts = [self.safe_name(x) for x in str(relpath).replace("\\", "/").split("/") if x not in ("", ".", "..")]
            if not parts:
                raise ValueError("폴더 안 위치가 이상해요.")
            d = os.path.join(self.UPLOAD_DIR, uid, *parts[:-1])
            name = parts[-1]
            if len(parts) > 12:
                raise ValueError("폴더가 너무 깊어요.")
        else:
            uid = "u%d%s" % (int(time.time()), os.urandom(4).hex())
            d = os.path.join(self.UPLOAD_DIR, uid)
            name = self.safe_name(name)
        os.makedirs(d, exist_ok=True)
        left = length
        with open(os.path.join(d, name), "wb") as f:
            while left > 0:
                chunk = stream.read(min(1024 * 1024, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass
            raise ValueError("파일을 다 받지 못했어요. 다시 올려 주세요.")
        store.log("첨부 받음: %s (%d바이트)%s" % (name, length, " · 폴더 " + uid if group else ""))
        return {"id": uid, "name": name, "size": length}

    def _attach(self, task, ids):
        """올려 둔 파일을 일의 폴더로 옮기고, 직원이 읽을 수 있게 안내 글을 돌려줌"""
        got = []
        for uid in ids or []:
            if not re.fullmatch(r"u\d+[0-9a-f]{8}", str(uid)):
                continue
            src_dir = os.path.join(self.UPLOAD_DIR, uid)
            if not os.path.isdir(src_dir):
                continue
            dst_dir = os.path.join(task["outdir"], "첨부_attachments")
            os.makedirs(dst_dir, exist_ok=True)
            for fn in os.listdir(src_dir):
                base, ext = os.path.splitext(fn)
                dst, n = os.path.join(dst_dir, fn), 2
                while os.path.exists(dst):
                    dst, n = os.path.join(dst_dir, "%s_%d%s" % (base, n, ext)), n + 1
                shutil.move(os.path.join(src_dir, fn), dst)
                self._keep_in_library(dst)
                rel = os.path.relpath(dst, task["outdir"])
                if os.path.isdir(dst):      # 📁 폴더째 올린 것: 폴더 이름과 파일 수를 알려 줌
                    n_files = sum(len(fs) for _, _, fs in os.walk(dst))
                    rel = "%s/ (폴더, 파일 %d개 — 안의 파일을 모두 살펴보기)" % (rel, n_files)
                got.append(rel)
            shutil.rmtree(src_dir, ignore_errors=True)
        if not got:
            return ""
        return "\n\n📎 첨부 파일 (작업 폴더 안, 꼭 열어서 읽기): " + ", ".join(got) + "\n(📚 맥북 자료실에도 보관했어요: %s — 다음 일에서도 찾을 수 있어요)" % LIBRARY

    def add_message(self, tid, text, attachments=None):
        text = (text or "").strip()
        task = self.get_task(tid)
        if not task:
            raise ValueError("그런 일을 찾지 못했어요.")
        if not text and not attachments:
            raise ValueError("내용을 적어 주세요.")
        if task["status"] == "cancelled":
            raise ValueError("그만둔 일이라 말을 받을 수 없어요. 「+ 새 일 맡기기」로 새로 맡겨 주세요.")
        self._scold_check(task, text)
        text = (text or "파일을 보내요.") + self._attach(task, attachments)
        if task["status"] in ("running", "planning", "paused"):
            # 일하는 중·생각하는 중·멈춤: 말만 남겨 두고 다음 차례(다음 단계, 다음 계획, 다시 시작)에 읽음
            task["messages"].append({"role": "user", "text": text, "ts": store.now(), "note": True})
            self._save_task(task)
            return task
        # 답할 차례(질문·결재·오류)이거나 끝난 일이면: 같은 일(같은 폴더)에서 기획실장이 이어서 계획을 세움
        if task["status"] == "done":
            task.pop("finished", None)
        # 열려 있던 결재/질문은 닫기
        for ap in self._approvals():
            if ap["taskId"] == tid and ap["status"] == "pending" and ap["kind"] in ("question", "plan", "final"):
                ap["status"] = "answered" if ap["kind"] == "question" else "revised"
                ap["reason"] = text if ap["kind"] != "question" else ""
                ap["decided"] = store.now()
                self._update_approval(ap)
        task["messages"].append({"role": "user", "text": text, "ts": store.now()})
        task["status"] = "planning"
        task["error"] = ""
        self._save_task(task)
        if task.get("compare"):
            self._install_compare_plan(task)
            return self.get_task(tid)
        if seongseo.is_request(text):
            self._install_seongseo_plan(task, text)
            return self.get_task(tid)
        self.q.put(("plan", tid))
        return task

    # ---- 결재
    def decide(self, aid, decision, reason="", choice="", attachments=None):
        ap = next((a for a in self._approvals() if a["id"] == aid), None)
        if not ap or ap["status"] != "pending":
            raise ValueError("이미 처리됐거나 없는 결재예요.")
        task = self.get_task(ap["taskId"])
        if attachments and decision == "revise":
            reason = (reason.strip() or "첨부 파일을 보고 고쳐 주세요.") + self._attach(task, attachments)
        if decision not in ("approve", "revise"):
            raise ValueError("승인 또는 수정 요청만 가능해요.")
        if decision == "revise" and not reason.strip():
            raise ValueError("수정 요청에는 이유를 적어 주세요.")
        if ap["kind"] == "pick" and decision == "approve" and choice not in ("A", "B", "M"):
            raise ValueError("Claude 답안, GPT 답안, 합본 중 하나를 골라 주세요.")
        ap["status"] = "approved" if decision == "approve" else "revised"
        ap["reason"] = reason.strip()
        ap["decided"] = store.now()
        self._update_approval(ap)
        kind = ap["kind"]
        if kind == "plan":
            if decision == "approve":
                try:
                    self._close_replaced(task)
                except Exception as ex:      # noqa: BLE001
                    store.log("대신한 일 정리 실패: %r" % (ex,))
                self._bump("기획실장", not task.get("planRevised"), plan=True)
                members = ["기획실장"] + [s["employee"] for s in (task.get("plan") or {}).get("steps", [])]
                self.meet_hold[task["id"]] = (list(dict.fromkeys(members)), time.time() + 25, "계획 나누는 중")
                task["status"] = "running"
                self._save_task(task)
                self.q.put(("next", task["id"]))
            else:
                task["planRevised"] = True
                self._score("기획실장", PTS_REVISE, "계획 수정 요청: " + ap["reason"], task["id"])
                task["messages"].append({"role": "user", "text": "계획을 이렇게 고쳐 주세요: " + ap["reason"], "ts": store.now()})
                task["status"] = "planning"
                self._save_task(task)
                self.q.put(("plan", task["id"]))
        elif kind == "step":
            step = task["plan"]["steps"][ap["stepIndex"]]
            if decision == "approve":
                step["status"] = "done"
                if step["employee"] == TRAIN_EMP:
                    self._apply_training(task, step)
                store.append_notebook(step["employee"], "- %s — %s (%s)" % (step["title"], task["title"], store.now()[:10]), "승인된 방식")
                self._bump(step["employee"], step.get("revisions", 0) == 0)
                self._save_task(task)
                self.q.put(("next", task["id"]))
            else:
                step["status"] = "pending"
                step["revisions"] = step.get("revisions", 0) + 1
                step.setdefault("feedback", []).append(ap["reason"])
                self._score(step["employee"], PTS_REVISE, "수정 요청: %s — %s" % (step["title"], ap["reason"]), task["id"])
                store.append_notebook(step["employee"], "- %s — 이유: %s (%s)" % (step["title"], ap["reason"], store.now()[:10]), "싫어하신 것과 이유")
                self._save_task(task)
                self.q.put(("step", task["id"], ap["stepIndex"]))
        elif kind == "credit":
            step = task["plan"]["steps"][ap["stepIndex"]]
            if decision == "approve":
                step["approvedToRun"] = True
                step["status"] = "pending"
                label = "성화 그림 만들기 승인" if step.get("job") == "art" else "헤이젠 영상 만들기 승인"
                store.append_notebook(step["employee"], "- %s — %s (%s)" % (label, task["title"], store.now()[:10]), "승인된 방식")
                self._save_task(task)
                self.q.put(("next", task["id"]))
            elif step.get("job") == "art":
                # 비용을 보류함: 다시 짜지 않고 일을 멈춰 둔다 (교수님이 나중에 다시 시작)
                step["status"] = "pending"
                step["approvedToRun"] = False
                task["status"] = "paused"
                task["pausedFrom"] = "running"
                task.setdefault("events", []).append({"ts": store.now(), "text": "교수님이 성화 생성 비용을 보류했어요: " + ap["reason"]})
                self._save_task(task, force=True)
            else:
                j = next((k for k in range(ap["stepIndex"] - 1, -1, -1)
                          if task["plan"]["steps"][k].get("output") == "heygen_script.txt"), None)
                step["status"] = "pending"
                step["approvedToRun"] = False
                if j is None:
                    task["messages"].append({"role": "user", "text": "영상 만들기를 이렇게 고쳐 주세요: " + ap["reason"], "ts": store.now()})
                    task["status"] = "planning"
                    self._save_task(task)
                    self.q.put(("plan", task["id"]))
                else:
                    prev = task["plan"]["steps"][j]
                    prev["status"] = "pending"
                    prev["revisions"] = prev.get("revisions", 0) + 1
                    prev.setdefault("feedback", []).append(ap["reason"])
                    store.append_notebook(prev["employee"], "- %s — 이유: %s (%s)" % (prev["title"], ap["reason"], store.now()[:10]), "싫어하신 것과 이유")
                    self._save_task(task)
                    self.q.put(("step", task["id"], j))
        elif kind == "pick":
            self._decide_pick(task, ap, decision, choice)
        elif kind == "final":
            if decision == "approve":
                self._mark_done(task)
                self._save_task(task)
                store.append_notebook("기획실장", "- %s 일을 마쳤어요 (%s)" % (task["title"], store.now()[:10]), "승인된 방식")
            else:
                for who in dict.fromkeys(["기획실장"] + [x["employee"] for x in task["plan"].get("steps", [])]):
                    self._score(who, PTS_FINAL_BACK, "최종 보고 반려: %s — %s" % (task["title"], ap["reason"]), task["id"])
                task["messages"].append({"role": "user", "text": "결과를 이렇게 고쳐 주세요: " + ap["reason"], "ts": store.now()})
                task["status"] = "planning"
                self._save_task(task)
                self.q.put(("plan", task["id"]))
        return ap

    def _decide_pick(self, task, ap, decision, choice):
        steps = task["plan"]["steps"]
        if decision == "revise":      # 합본을 고쳐 달라: 검토 담당이 이유를 반영해 비교·합본을 다시 씀
            last = len(steps) - 1
            steps[last]["status"] = "pending"
            steps[last]["revisions"] = steps[last].get("revisions", 0) + 1
            steps[last].setdefault("feedback", []).append(ap["reason"])
            task["status"] = "running"
            self._save_task(task)
            self.q.put(("next", task["id"]))
            return
        src = {"A": prompts.COMPARE_A, "B": prompts.COMPARE_B, "M": prompts.COMPARE_MERGED}[choice]
        label = {"A": "Claude 답안", "B": "GPT 답안", "M": "합본"}[choice]
        sp = os.path.join(task["outdir"], src)
        dst = os.path.join(task["outdir"], "최종_final.md")
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as f:
                body = f.read()
            with open(dst, "w", encoding="utf-8") as f:
                f.write(body)
            try:
                docs.md_to_docx(body, dst[:-3] + ".docx", task["title"])
            except Exception as ex:
                store.log("최종본 워드 만들기 실패: %r" % ex)
        task["pick"] = {"choice": choice, "label": label, "file": src, "ts": store.now(), "reason": ap.get("reason", "")}
        self._mark_done(task)
        self._save_task(task)
        with self.lock:
            stt = store.load("compare_stats", {"A": 0, "B": 0, "M": 0})
            stt[choice] = stt.get(choice, 0) + 1
            store.save("compare_stats", stt)
        store.append_notebook("기획실장", "- 비교 모드 '%s': 교수님이 %s 을(를) 골랐어요%s (%s)" % (
            task["title"], label, (" — " + ap["reason"]) if ap.get("reason") else "", store.now()[:10]), "승인된 방식")

    # ---- 화면용 상태
    def state(self):
        stats = self._stats()
        aps = [a for a in self._approvals() if a["status"] == "pending"]
        owners = {}
        for a in aps:
            owners.setdefault(a["owner"], a["title"])
        now = time.time()
        emps = []
        for e in ROSTER["employees"]:
            n = e["name"]
            st, text = "준비 중", "아직 일을 맡길 수 없어요"
            work = {}
            if e["active"]:
                live = self.live.get(n)
                rec = self.recent.get(n)
                if live:
                    st, text = live["state"], live["text"]
                    work = {"task": live.get("task", ""), "taskId": live.get("taskId", ""), "step": live.get("step", ""),
                            "recent": [{"t": t, "text": x} for t, x in list(self.activity.get(n, []))[-6:]][::-1],
                            "workSeconds": int(now - live.get("since", now)),
                            "startedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(live.get("since", now)))}
                elif n in self.rendering:
                    r = self.rendering[n]
                    st = "작업 중"
                    text = ("슬라이드와 아바타를 합치는 중 (%d분째)" if r.get("kind") == "compose" else "헤이젠가 영상 만드는 중 (%d분째)") % int((now - r["since"]) / 60)
                    work = {"task": r["task"], "taskId": r["taskId"], "step": r["step"], "workSeconds": int(now - r["since"]),
                            "startedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["since"]))}
                elif n in owners:
                    st, text = "결재 요청", owners[n]
                elif rec and now - rec[0] < 45:
                    st, text = "보고 완료", rec[1]
                else:
                    st, text = "대기 중", "일을 기다려요"
            s = stats.get(n, {"done": 0, "firstPass": 0})
            rk = rank_view(s, n)
            if st == "작업 중" and live and live.get("taskId"):
                heard = max(getattr(self.runner, "heard", {}).get(live["taskId"], 0), live.get("heard", 0), live.get("since", now))
                q = int((now - heard) // 60)
                if q >= QUIET_MIN:
                    work["quiet"] = q
            emps.append({"name": n, "team": e["team"], "active": e["active"], "state": st, "text": text,
                         "rank": rk["rank"], "level": rk["level"], "nextRank": rk.get("next", ""), "rankNeed": rk.get("need", []),
                         "rankProgress": rk.get("progress", 0), "passRate": rk.get("passRate"),
                         "done": s["done"], "firstPass": s["firstPass"],
                         "idle": int(now - self.last_active.get(n, now)), "role": e["role"], **work})
        tasks = self._tasks()
        quiet = {}
        for e in emps:
            if e.get("quiet") and e.get("taskId"):
                quiet[e["taskId"]] = max(quiet.get(e["taskId"], 0), e["quiet"])
        working = sum(1 for e in emps if e["state"] == "작업 중")
        reports = [t for t in tasks if t["status"] == "done"]
        active_tasks = []
        for t in tasks:
            if t["status"] in ("planning", "running", "review", "plan_review", "asking", "paused") and t.get("plan"):
                steps = t["plan"]["steps"]
                active_tasks.append({"id": t["id"], "title": t["title"], "status": t["status"], "created": t["created"],
                                     "quiet": quiet.get(t["id"], 0),
                                     "pausedAt": next((e["ts"] for e in reversed(t.get("events", [])) if "멈" in e.get("text", "")), ""),
                                     "allDone": bool(steps) and all(s["status"] == "done" for s in steps),
                                     "done": sum(1 for s in steps if s["status"] == "done"), "total": len(steps),
                                     "steps": [{"employee": s["employee"], "title": s["title"], "status": s["status"]} for s in steps]})
            elif t["status"] in ("planning", "asking", "plan_review", "paused"):
                active_tasks.append({"id": t["id"], "title": t["title"], "status": t["status"], "done": 0, "total": 0, "steps": []})
        if self.notice and self.notice_tid:      # 오류 난 일이 다시 돌거나 끝났으면(다시 시도·자동 재시도·다른 일로 대신) 빨간 안내를 내림
            nt = self.get_task(self.notice_tid)
            if not nt or nt.get("status") != "error":
                self.notice, self.notice_tid = "", ""
        return {"company": ROSTER["company"], "host": self.computer_name(), "employees": emps, "activeTasks": active_tasks, "notice": self.notice, "fake": self.settings["fake"],
                "counts": {"approvals": len(aps), "reports": len(reports), "working": working, "quiet": len(quiet)},
                "promotions": self.promotions[-5:], "quietMin": QUIET_MIN,
                "tasks": [{"id": t["id"], "title": t["title"], "status": t["status"], "created": t["created"], "finished": t.get("finished", ""),
                           "project": self.task_project(t)} for t in tasks[-30:]],
                "meetings": self.meetings(tasks),
                "projects": [{k: p[k] for k in ("id", "name", "color", "approvals", "working", "doneCount", "moving", "waiting")} | {"active": len(p["active"])}
                             for p in self.projects_board()]}

    def meetings(self, tasks=None):
        """회의실에 모일 직원들: 계획 세우기(기획실장), 계획 나누기(맡은 직원들 잠깐), 비교 회의(Claude·GPT·검토 담당)"""
        tasks = tasks if tasks is not None else self._tasks()
        out, busy, now = [], set(), time.time()

        def add(kind, tid, title, members, text):
            ms = [m for m in dict.fromkeys(members) if m in EMP and EMP[m]["active"] and m not in busy]
            if ms:
                busy.update(ms)
                out.append({"kind": kind, "taskId": tid, "title": title, "members": ms, "text": text})
        byid = {t["id"]: t for t in tasks}
        for t in tasks:      # 비교 회의: 검토 담당이 두 답을 비교하는 동안
            st = (t.get("plan") or {}).get("steps", [])
            if t.get("compare") and t["status"] == "running" and len(st) > 1 and st[1]["status"] == "running":
                add("compare", t["id"], t["title"], [st[1]["employee"], st[0]["employee"], IMG_EMP], "비교 회의 중")
        pl = self.live.get("기획실장") or {}
        if pl.get("step") == "계획서 작성":
            t = byid.get(pl.get("taskId"))
            add("plan", pl.get("taskId", ""), (t or {}).get("title", ""), ["기획실장"], "계획 세우는 중")
        for tid, hold in list(self.meet_hold.items()):
            members, until, text = hold[:3]
            if until < now:
                self.meet_hold.pop(tid, None)
                continue
            add(hold[3] if len(hold) > 3 else "brief", tid, (byid.get(tid) or {}).get("title", ""), members, text)
        for t in tasks:      # 🗣 대표 회의: 회의하는 동안 참석 직원과 기획실장이 3층 회의실 2 에 (교수님도)
            if t["status"] == "running" and t.get("ceoMeeting"):
                add("ceo", t["id"], t["title"], list(t["ceoMeeting"]) + ["기획실장"], "대표 회의 중")
        for t in tasks:      # 💡 아이디어 회의: 회의하는 동안 참여 직원이 회의실에 모여 있음
            if t["status"] == "running" and t.get("messages") and t["messages"][0].get("text", "").startswith("💡 아이디어 메모"):
                ms = [x["employee"] for x in (t.get("plan") or {}).get("steps", [])]
                if len(ms) > 1:
                    add("idea", t["id"], t["title"], ms, "아이디어 회의 중")
        return out

    def approvals_list(self):
        out = []
        for a in reversed(self._approvals()):
            t = self.get_task(a["taskId"]) or {}
            d = dict(a)
            d["taskTitle"] = t.get("title", "")
            out.append(d)
        return out

    def reports_list(self):
        out = []
        for t in reversed(self._tasks()):
            if t["status"] == "done":
                out.append({"id": t["id"], "title": t["title"], "created": t["created"], "finished": t.get("finished", ""),
                            "took": duration_text(t["created"], t.get("finished")), "files": self.task_files(t), **self.outputs(t)})
        return out

    # ---- 프로젝트: 여러 일을 묶어서 동시에 보는 단위
    def projects(self):
        ps = store.load("projects", None)
        if ps is None:
            ps = [dict(x) for x in DEFAULT_PROJECTS]
            store.save("projects", ps)
        return ps

    def _guess_project(self, text):
        """기획실장이 일 내용을 보고 어느 프로젝트 일인지 정해요 (낱말로 판단, 비용 없음)."""
        low = (text or "").lower()
        for pr in self.projects():
            if any(k.lower() in low for k in pr.get("keywords", [])):
                return pr["id"]
        return "p-etc"

    @staticmethod
    def _nums(title):
        """제목 속 장·차시·편 번호 모음: '1~6장' → {1..6}, '5·6차시' → {5,6}"""
        out = set()
        for m in re.finditer(r"(\d+(?:\s*[~\-·,]\s*\d+)*)\s*(장|차시|편|회차?)", title or ""):
            for a, b in re.findall(r"(\d+)\s*[~\-]\s*(\d+)", m.group(1)):
                out |= set(range(int(a), int(b) + 1))
            out |= {int(x) for x in re.findall(r"\d+", m.group(1))}
        return out

    def _close_replaced(self, task):
        """새 일이 시작될 때, 같은 프로젝트에서 같은 장·차시를 하다 멈춘(오류) 예전 일은 '다른 일로 다시 함'으로 정리 (11-13)
        → 상황판에 '멈춤'이 계속 남지 않게. 빨간 안내도 함께 내림."""
        new_n = self._nums(task.get("title"))
        if not new_n:
            return
        kind = "영상" if "영상" in task.get("title", "") else ""
        for t in self._tasks():
            if t["id"] == task["id"] or t.get("status") != "error" or self.task_project(t) != self.task_project(task):
                continue
            old_n = self._nums(t.get("title"))
            if not old_n or not old_n <= new_n or (kind and kind not in t.get("title", "")):
                continue
            t["status"] = "cancelled"
            t["replacedBy"] = task["id"]
            t.pop("autoRetryAt", None)
            t.setdefault("events", []).append({"ts": store.now(), "text": "새 일 「%s」(%s)에서 다시 하므로 이 일은 정리했어요." % (task["title"], task["id"])})
            self._save_task(t, force=True)
            store.log("대신한 일 정리: %s → %s" % (t["id"], task["id"]))
            if self.notice_tid == t["id"]:
                self.notice, self.notice_tid = "", ""

    def task_project(self, t):
        return t.get("project") or self._guess_project(t["messages"][0]["text"] if t.get("messages") else t.get("title", ""))

    @staticmethod
    def task_engines(t):
        """이 일을 어느 AI 가 하고 있나: 이미지 담당(ChatGPT 화면 조작) = GPT, 헤이젠 영상 = HeyGen, 나머지 = Claude"""
        out = []
        for s in (t.get("plan") or {}).get("steps", []):
            ek = s.get("engine")
            ks = ["Claude", "GPT"] if s.get("job") == "compare" else \
                ["GPT"] if s["employee"] == IMG_EMP or ek == "gpt" else ["HeyGen"] if s.get("heygen") else \
                ["Claude 프로젝트"] if ek == "claude_project" else ["Claude 채팅"] if ek == "chat" else ["Claude"]
            for k in ks:
                if k not in out:
                    out.append(k)
        return out or ["Claude"]

    def add_project(self, name, goal="", folder="", keywords=""):
        name = (name or "").strip()
        if not name:
            raise ValueError("프로젝트 이름을 적어 주세요.")
        with self.lock:
            ps = self.projects()
            if any(x["name"] == name for x in ps):
                raise ValueError("같은 이름의 프로젝트가 이미 있어요.")
            kws = [k.strip() for k in re.split(r"[,，\n]", keywords or "") if k.strip()] or [name]
            colors = ["#7c5cff", "#ef4444", "#0ea5e9", "#84cc16", "#f97316", "#ec4899"]
            pr = {"id": "p-%d" % int(time.time()), "name": name, "goal": (goal or "").strip(), "folder": (folder or "").strip(),
                  "keywords": kws, "color": colors[len(ps) % len(colors)]}
            ps.insert(len(ps) - 1 if ps and ps[-1]["id"] == "p-etc" else len(ps), pr)
            store.save("projects", ps)
        return pr

    def set_task_project(self, tid, pid):
        if not any(x["id"] == pid for x in self.projects()):
            raise ValueError("그런 프로젝트가 없어요.")
        task = self.get_task(tid)
        if not task:
            raise ValueError("그런 일을 찾지 못했어요.")
        task["project"] = pid
        task["projectChosen"] = True
        self._save_task(task)
        return task

    def projects_board(self):
        """프로젝트 현황판: 프로젝트마다 지금 도는 일, 결재 대기, 끝난 일, 최근 결과물"""
        tasks = self._tasks()
        aps = [a for a in self._approvals() if a["status"] == "pending"]
        live = {}
        for n, l in self.live.items():
            if l.get("taskId"):
                live.setdefault(l["taskId"], []).append({"name": n, "step": l.get("step", ""), "text": l.get("text", "")})
        for n, r in self.rendering.items():
            live.setdefault(r["taskId"], []).append({"name": n, "step": r["step"], "text": "영상 만드는 중"})
        ACTIVE = ("planning", "asking", "plan_review", "running", "review", "paused", "error")
        out = []
        for pr in self.projects():
            mine = [t for t in tasks if self.task_project(t) == pr["id"]]
            act, done = [], []
            for t in reversed(mine):
                steps = (t.get("plan") or {}).get("steps", [])
                cur = next((s for s in steps if s["status"] != "done"), None)
                row = {"id": t["id"], "title": t["title"], "status": t["status"], "created": t["created"],
                       "finished": t.get("finished", ""), "done": sum(1 for s in steps if s["status"] == "done"), "total": len(steps),
                       "current": ("%s — %s" % (cur["employee"], cur["title"])) if cur and t["status"] in ACTIVE else "",
                       "workers": live.get(t["id"], []), "engines": self.task_engines(t),
                       "compare": bool(t.get("compare")), "pick": (t.get("pick") or {}).get("label", ""),
                       "approvals": [a["title"] for a in aps if a["taskId"] == t["id"]]}
                if t["status"] in ACTIVE:
                    act.append(row)
                elif t["status"] == "done":
                    row["files"] = [f for f in self.task_files(t) if not f.endswith(".docx")][:6]
                    done.append(row)
            out.append({**{k: pr.get(k, "") for k in ("id", "name", "goal", "folder", "color", "claudeProject")},
                        "active": act, "done": done[:5], "doneCount": len(done),
                        "approvals": sum(len(r["approvals"]) for r in act),
                        "working": sum(1 for r in act if r["workers"]),
                        "moving": sum(1 for r in act if r["status"] in ("planning", "running", "plan_review", "review")),
                        "waiting": sum(1 for r in act if r["status"] in ("paused", "asking", "error"))})
        return out

    def task_files(self, task):
        base = task["outdir"]
        files = []
        if os.path.isdir(base):
            for dp, dn, fn in os.walk(base):
                dn[:] = [d for d in dn if not d.startswith("_")]
                for f in sorted(fn):
                    if not f.startswith("."):
                        files.append(os.path.relpath(os.path.join(dp, f), base))
        return files

    def main_files(self, task):
        """결과물 가운데 교수님이 실제로 쓰실 파일만 골라 중요한 순서로 (작업용 파일은 뺌)"""
        files = self.task_files(task)
        names = set(files)

        def helper(f):
            b = os.path.basename(f)
            return (b.startswith("_") or b in ("video_job.json",) or b.startswith("heygen_")
                    or b.endswith((".srt", ".json", ".log"))
                    or (b.endswith(".md") and f[:-3] + ".docx" in names))
        has_lecture = any(f.endswith("lecture.mp4") for f in files)

        def rank(f):
            low = f.lower()
            b = os.path.basename(low)
            if "review" in b or "검토" in b or b.startswith(("비교_", "답안")):
                return 6   # 검토·비교 보고서는 맨 뒤
            if low.endswith("lecture.mp4"):
                return 0
            if low.endswith((".mp4", ".webm")):
                return 1
            if b.startswith("최종") or low.endswith((".pptx", ".pdf", ".html")):
                return 2
            if low.endswith((".png", ".jpg", ".jpeg", ".webp")):
                return 3
            if low.endswith((".docx", ".xlsx", ".hwp", ".hwpx")):
                return 4
            return 5
        keep = [f for f in files if not helper(f) and not (has_lecture and f.endswith("_video.mp4"))]
        return sorted(keep, key=lambda f: (rank(f), f))

    def outputs(self, task):
        return {"folder": task.get("outdir", ""), "main": self.main_files(task), "final": task.get("final_dir", ""), "final_host": task.get("final_host", "")}

    def _after_study(self, task):
        """📚 자기계발이 끝나면: 공부노트의 '핵심' 몇 줄을 그 직원 수첩 '공부한 것'에 적고 +5점"""
        who = task.get("study")
        if not who:
            return
        try:
            txt = open(os.path.join(task["outdir"], "공부노트_study.md"), encoding="utf-8").read()
        except OSError:
            return
        key = [l.strip("-• ").strip() for l in txt.split("## 핵심", 1)[-1].split("\n## ")[0].split("\n") if l.strip().startswith(("-", "•", "1", "2", "3"))][:3]
        for k in key:
            store.append_notebook(who, "- %s (%s 자기계발)" % (k[:150], store.now()[:10]), "공부한 것")
        self._score(who, 5, "자기계발 공부: " + (task.get("studyTopic") or ""), task["id"])

    def _mark_done(self, task):
        """일을 끝남으로 옮기고, 결과물이 어디 있는지 대화창에 남김"""
        if task.get("study"):
            self._after_study(task)
        task["status"] = "done"
        task["finished"] = store.now()
        main = self.main_files(task)
        folder = task.get("outdir", "")
        if os.path.isdir(folder) and main:
            lines = ["✅ 일이 끝났어요. 결과물은 이 폴더에 있어요:", "📂 " + folder, "", "주요 파일:"]
            lines += ["· " + f for f in main[:8]]
            if len(main) > 8:
                lines.append("· 그 밖에 %d개" % (len(main) - 8))
            try:
                fd = None if task.get("noFinal") else self.publish_finals(task)
            except Exception as ex:
                fd = None
                lines += ["", "⚠ 최종본 폴더에 복사하지 못했어요: %s" % ex]
                store.log("최종본 복사 실패 %s: %r" % (task["id"], ex))
            if fd:
                lines += ["", "⭐ 쓰실 파일은 최종본 폴더에도 복사해 두었어요 (%s · 드라이브 앱이 켜져 있으면 드라이브에도 올라가요):" % self.computer_name(), "📁 " + fd]
            task["messages"].append({"role": "planner", "text": "\n".join(lines), "ts": store.now(), "kind": "result"})
        if self._request_text(task).lstrip().startswith("📅 아침 보고"):
            threading.Thread(target=self._kakao_morning, args=(task["id"],), daemon=True).start()

    def _kakao_morning(self, tid):
        """📅 비서 아침 보고를 교수님 카톡(나에게 보내기)으로. 카카오 연결 전이면 조용히 넘어감"""
        if not kakao.ready():
            return
        task = self.get_task(tid)
        if not task:
            return
        try:
            md = open(os.path.join(task["outdir"], "오늘의_일정_보고_daily.md"), encoding="utf-8").read()
            n = kakao.send_report("📅 비서 아침 보고", md, kakao.load().get("public_url", ""))
            msg = "💬 아침 보고를 카톡으로 보냈어요 (%d통)." % n
        except (OSError, kakao.KakaoError) as ex:
            msg = "⚠ 아침 보고를 카톡으로 보내지 못했어요: %s" % ex
            self.notice = msg
        store.log(msg)
        task = self.get_task(tid)
        if task:
            task["messages"].append({"role": "planner", "text": msg, "ts": store.now()})
            self._save_task(task, force=True)

    # ---- 최종본 폴더: 교수님이 쓰실 파일만 프로젝트별로 복사해 둠 (원래 결과물은 그대로)
    _HOST = None

    def computer_name(self):
        """이 사무실이 돌아가는 컴퓨터 이름 (settings.computer_name 으로 바꿀 수 있음). 최종본에 '어디서 만들었나'로 적음"""
        if self.settings.get("computer_name"):
            return str(self.settings["computer_name"])
        if Engine._HOST is None:
            name = ""
            try:
                name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:
                pass
            if not name:
                import platform
                name = os.environ.get("COMPUTERNAME") or platform.node() or "이 컴퓨터"
            Engine._HOST = name
        return Engine._HOST

    def final_root(self):
        return self.settings.get("final_root") or os.path.join(ROOT, "최종본_final")

    def final_files(self, task):
        """최종본으로 옮길 파일: 주요 파일에서 검토·비교 보고서는 뺌"""
        main = self.main_files(task)
        has_final_doc = any(os.path.basename(f).startswith("최종") for f in main)
        skip_words = ("review", "검토", "보고서", "report", "_이전", "발음_확인표", "발음_미리듣기", "progress_check", "upload_check")
        out = []
        for f in main:
            b = os.path.basename(f).lower()
            if (any(w in b for w in skip_words) or b.startswith(("비교_", "답안")) or b.endswith((".py", ".sh"))
                    or (has_final_doc and b.startswith("합본"))):
                continue
            out.append(f)
        return out

    def final_extras(self, task):
        """작업 폴더 밖이거나 작업용으로 빠지는 파일 중 최종본에 꼭 넣을 것: 강의 영상 자막, 쓰인 원본 슬라이드(PDF·PPTX)"""
        out = []
        base = task.get("outdir", "")
        for f in self.task_files(task):
            b = os.path.basename(f)
            if b.endswith("lecture_자막.srt") and "_이전" not in b:
                out.append(os.path.join(base, f))
        names = [s.get("pdf") for s in (task.get("plan") or {}).get("steps", []) if s.get("pdf")]
        if (task.get("lecture") or {}).get("pdf"):
            names.append(task["lecture"]["pdf"])
        for n in dict.fromkeys(names):
            pdf = self._find_pdf(n, task)
            if pdf:
                out.append(pdf)
                pptx = os.path.splitext(pdf)[0] + ".pptx"
                if os.path.isfile(pptx):
                    out.append(pptx)
        return out

    @staticmethod
    def _clone(src, dst):
        """맥에서는 복제(cp -c)로 — 디스크 자리를 거의 쓰지 않음. 안 되면 보통 복사"""
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            subprocess.run(["cp", "-c", src, dst], check=True, capture_output=True)
        except Exception:
            shutil.copy2(src, dst)

    def publish_finals(self, task, name=None, project_id=None):
        """일 하나의 최종 파일을 최종본_final/{프로젝트}/{최종본 이름}/ 에 복사.
        같은 이름이 이미 있으면 옛 것은 {프로젝트}/_이전판/ 으로 옮겨서, 폴더에는 늘 최신 하나만 남음"""
        name = (name if name is not None else task.get("final_name", task.get("title", ""))) or ""
        name = self.safe_name(name.lstrip("⚖ ").strip().replace("/", "·"))[:40].strip()
        files = self.final_files(task)
        if not name or not files or not os.path.isdir(task.get("outdir", "")):
            return None
        pid = project_id or self.task_project(task)
        proj = next((p for p in self.projects() if p["id"] == pid), None)
        pname = self.safe_name(proj["name"] if proj else "기타")
        pdir = os.path.join(self.final_root(), pname)
        dest = os.path.join(pdir, name)
        info_name = "_어디서_왔나.txt"
        if os.path.isdir(dest):
            old_from = ""
            try:
                old_from = open(os.path.join(dest, info_name), encoding="utf-8").read().split("\n")[0]
            except OSError:
                pass
            if task["id"] in old_from:
                shutil.rmtree(dest)            # 같은 일을 다시 옮기는 것 → 그냥 새로
            else:
                prev = os.path.join(pdir, "_이전판", "%s_%s" % (name, time.strftime("%Y%m%d-%H%M%S")))
                os.makedirs(os.path.dirname(prev), exist_ok=True)
                shutil.move(dest, prev)
        for f in files:
            self._clone(os.path.join(task["outdir"], f), os.path.join(dest, f))
        for src in self.final_extras(task):
            self._clone(src, os.path.join(dest, os.path.basename(src)))
        with open(os.path.join(dest, info_name), "w", encoding="utf-8") as fp:
            fp.write("일 번호: %s\n일 이름: %s\n끝난 때: %s\n만든 컴퓨터: %s\n원래 결과물 폴더: %s\n" % (
                task["id"], task.get("title", ""), task.get("finished") or store.now(), self.computer_name(), task.get("outdir", "")))
        task["final_dir"] = dest
        task["final_host"] = self.computer_name()
        return dest

    def reveal_final(self, tid):
        task = self.get_task(tid)
        folder = (task or {}).get("final_dir") or self.final_root()
        if not os.path.isdir(folder):
            folder = self.final_root()
            os.makedirs(folder, exist_ok=True)
        if not self.settings.get("fake"):
            subprocess.Popen(["open", folder])
        return {"ok": True, "folder": folder}

    def reveal_folder(self, tid):
        task = self.get_task(tid)
        if not task:
            raise ValueError("그런 일이 없어요.")
        folder = task.get("outdir", "")
        if not os.path.isdir(folder):
            raise ValueError("결과물 폴더가 아직 없어요.")
        if not self.settings.get("fake"):
            subprocess.Popen(["open", folder])
        return {"ok": True, "folder": folder}

    def timeline(self, task):
        """일 하나의 시간 기록: 맡김 → 결재 → 작업 시작/끝 → 보고 → 완료 (이미 있는 일도 저장된 시각으로 채움)"""
        ev = []

        def add(ts, text, kind):
            if ts:
                ev.append({"ts": ts, "text": text, "kind": kind})
        add(task["created"], "일을 맡겼어요", "start")
        for i, m in enumerate(task["messages"]):
            if i and m["role"] == "user" and m.get("kind") != "status":
                add(m["ts"], "교수님 말씀: " + m["text"].replace("\n", " ")[:40], "talk")
        for evn in task.get("events", []):
            add(evn["ts"], evn["text"], "ctl")
        for a in self._approvals():
            if a["taskId"] != task["id"]:
                continue
            add(a["created"], "결재 요청 — " + a["title"], "ask")
            if a["decided"]:
                res = {"approved": "승인", "revised": "수정 요청", "answered": "답변함"}.get(a["status"], a["status"])
                add(a["decided"], "%s — %s%s" % (res, a["title"], (" (%s)" % a["reason"][:30]) if a.get("reason") else ""), "decide")
        for st in (task.get("plan") or {}).get("steps", []):
            if st.get("started"):
                add(st["started"], "%s 작업 시작 — %s" % (st["employee"], st["title"]), "workstart")
            if st.get("renderStarted"):
                add(st["renderStarted"], "%s — 헤이젠에 영상 만들기를 맡겼어요" % st["employee"], "workstart")
            if st.get("finished"):
                d = duration_text(st.get("started"), st.get("finished"))
                add(st["finished"], "%s 작업 끝 — %s%s" % (st["employee"], st["title"], (" (%s)" % d) if d else ""), "workend")
        add(task.get("finished"), "일이 모두 끝났어요", "done")
        # 같은 초에 일어난 일은 자연스러운 순서로: 말 → 작업 끝 → 결재 요청 → 결정 → 작업 시작 → 완료
        order = {"start": 0, "talk": 1, "workend": 2, "ask": 3, "decide": 4, "ctl": 4, "workstart": 5, "done": 6}
        ev.sort(key=lambda e: (e["ts"], order.get(e["kind"], 9)))
        return ev

    def _apply_training(self, task, step):
        """교육 담당이 만든 교육_규칙_rules.json 을 읽어 직원마다 업무 수첩 규칙에 넣고, 2층 강당에 모여 교육받는 모습을 보여 줌"""
        rules_fp = None
        for cur, _d, files in os.walk(task["outdir"]):
            for f in files:
                if f.endswith("rules.json") and "교육" in f:
                    rules_fp = os.path.join(cur, f)
        if not rules_fp:
            task["messages"].append({"role": "planner", "ts": store.now(), "text": "⚠ 교육 규칙 파일(교육_규칙_rules.json)을 찾지 못해서 수첩에는 넣지 못했어요."})
            return
        try:
            data = json.load(open(rules_fp, encoding="utf-8"))
        except (OSError, ValueError) as ex:
            task["messages"].append({"role": "planner", "ts": store.now(), "text": "⚠ 교육 규칙 파일을 읽지 못했어요: %s" % ex})
            return
        done, members = [], []
        title = task.get("final_name") or task.get("title", "")
        for name, rules in (data.items() if isinstance(data, dict) else []):
            if name not in EMP:
                continue
            for r in (rules if isinstance(rules, list) else [rules]):
                r = " ".join(str(r).split())
                if r:
                    store.add_rule(name, "%s (교육: %s, %s)" % (r, title[:20], store.now()[:10]))
                    done.append(name)
            members.append(name)
        self.meet_hold["train-" + task["id"]] = ([TRAIN_EMP] + members, time.time() + 60, "🎓 교육 중", "training")
        store.log("교육 적용: %s → %s" % (task["id"], ", ".join(dict.fromkeys(done))))
        task["messages"].append({"role": "planner", "ts": store.now(),
            "text": "🎓 2층 강당에서 교육했어요. 규칙 %d개를 %s 업무 수첩에 넣었어요. (직원 프로필 카드에서 보고 지울 수 있어요)" % (len(done), ", ".join(dict.fromkeys(done)) or "아무 직원도 없음")})

    def add_rule(self, name, text):
        if name not in EMP:
            raise ValueError("그런 직원이 없어요.")
        store.add_rule(name, text)
        store.log("규칙 추가: %s — %s" % (name, " ".join(text.split())[:60]))
        return store.get_rules(name)

    def remove_rule(self, name, index):
        if name not in EMP:
            raise ValueError("그런 직원이 없어요.")
        gone = store.remove_rule(name, index)
        store.log("규칙 삭제: %s — %s" % (name, gone[:60]))
        return store.get_rules(name)

    def history(self, name):
        """직원이 한 일의 기록 (최근 것이 위로)"""
        out = []
        for t in self._tasks():
            if name == "기획실장":
                if t.get("plan"):
                    state = {"plan_review": "계획서 결재 기다림", "running": "계획 승인됨", "review": "계획 승인됨", "done": "계획 승인됨"}.get(t["status"], "진행 중")
                    out.append({"taskId": t["id"], "taskTitle": t["title"], "title": "계획서 작성", "status": state,
                                "date": t["created"], "files": [], "revisions": 0, "summary": t["plan"].get("summary", "")})
                continue
            for s in (t.get("plan") or {}).get("steps", []):
                if s["employee"] != name:
                    continue
                state = {"pending": "차례를 기다림", "running": "작업 중", "waiting": "결재 기다림", "rendering": "헤이젠가 영상 만드는 중", "composing": "슬라이드와 아바타 합치는 중", "done": "끝냄", "error": "오류"}.get(s["status"], s["status"])
                out.append({"taskId": t["id"], "taskTitle": t["title"], "title": s["title"], "status": state,
                            "date": t["created"], "started": s.get("started", ""), "finished": s.get("finished", ""),
                            "took": duration_text(s.get("started"), s.get("finished")),
                            "files": s.get("files", []), "revisions": s.get("revisions", 0),
                            "summary": (s.get("result") or "")[:300]})
        out.sort(key=lambda h: h["date"], reverse=True)
        return out

    def profile(self, name):
        e = EMP.get(name)
        if not e:
            return None
        s = self._stats().get(name, {"done": 0, "firstPass": 0})
        done = s["done"]
        rk = rank_view(s, name)
        return {"name": name, "team": e["team"], "role": e["role"], "active": e["active"], "rank": rk["rank"],
                "level": rk["level"], "nextRank": rk.get("next", ""), "rankNeed": rk.get("need", []),
                "rankProgress": rk.get("progress", 0), "promotions": s.get("promotions", []),
                "ranks": [{"rank": r[2], "score": r[0], "rate": r[1]} for r in ladder_of(name)], "unit": "계획" if name == "기획실장" else "일",
                "score": s.get("score", 0), "scoreLog": list(reversed(s.get("log", [])))[:40],
                "intro": e.get("intro", ""), "strengths": e.get("strengths", []),
                "done": done, "firstPass": s["firstPass"],
                "passRate": round(100 * s["firstPass"] / done) if done else None,
                "history": self.history(name),
                "rules": store.get_rules(name),
                "notebook": store.read_notebook(name)}

    # ---- 일꾼
    def _set_live(self, name, state, text, task=None, step=None):
        """직원의 지금 상태. task/step 을 주면 새 작업 시작, 안 주면 하던 작업의 진행 문구만 바꿈"""
        with self.lock:
            old = self.live.get(name) or {}
            fresh = task is not None
            if fresh:
                self.activity[name] = deque(maxlen=20)
            log = self.activity.setdefault(name, deque(maxlen=20))
            if not log or log[-1][1] != text:
                log.append((time.strftime("%H:%M:%S"), text))
            self.live[name] = {"state": state, "text": text,
                               "since": time.time() if fresh or not old else old.get("since", time.time()),
                               "task": task if fresh else old.get("task", ""),
                               "taskId": old.get("taskId", "") if not fresh else "",
                               "step": step if fresh else old.get("step", ""),
                               "heard": time.time()}
            self.last_active[name] = time.time()

    def _clear_live(self, name, report_text=None):
        with self.lock:
            self.live.pop(name, None)
            self.last_active[name] = time.time()
            if report_text:
                self.recent[name] = (time.time(), report_text)

    def _worker(self):
        while True:
            job = self.q.get()
            with self.lock:
                tl = self._tlocks.setdefault(job[1], threading.Lock())
            tl.acquire()
            try:
                if job[0] == "plan":
                    self._do_plan(job[1])
                elif job[0] == "next":
                    self._do_next(job[1])
                elif job[0] == "step":
                    self._do_step(job[1], job[2])
                elif job[0] == "poll":
                    self._do_poll(job[1], job[2])
            except Exception as ex:  # 어떤 일이든 서버가 죽지 않게
                store.log("worker error: %r" % (ex,))
                self.notice = "예상 못한 오류가 났어요: %s" % ex
            finally:
                tl.release()

    def _prior_results(self, exclude_tid=None):
        """끝난(승인된) 지난 일의 글 결과물: [(일, 파일이름, 전체 경로)]"""
        out = []
        for t in self._tasks():
            if t["id"] == exclude_tid or t["status"] != "done":
                continue
            for rel in self.task_files(t):
                if rel.lower().endswith((".md", ".txt")):
                    out.append((t, rel, os.path.join(t["outdir"], rel)))
        return out

    def _input_files(self, library=True):
        out = []
        for d in self.settings["input_dirs"]:
            if not os.path.isdir(d):
                continue
            if os.path.realpath(d) == os.path.realpath(LIBRARY):
                if not library:
                    continue
                lib = []
                for cur, dirs, files in os.walk(d):
                    dirs[:] = [x for x in dirs if not x.startswith(".")]
                    for f in files:
                        if not f.startswith("."):
                            fp = os.path.join(cur, f)
                            lib.append((os.path.getmtime(fp), fp))
                out += ["(📚 자료실) " + fp for _, fp in sorted(lib, reverse=True)[:300]]
                continue
            for f in sorted(os.listdir(d)):
                if not f.startswith("."):
                    out.append(os.path.join(d, f))
        return out

    def _do_plan(self, tid):
        task = self.get_task(tid)
        if not task or task["status"] != "planning":
            return
        self._set_live("기획실장", "작업 중", "계획 세우는 중", task=task["title"], step="계획서 작성")
        self.live["기획실장"]["taskId"] = tid
        try:
            files = self._input_files() + ["(지난 일에서 승인된 결과) [%s] %s" % (t["title"], rel)
                                           for t, rel, _ in self._prior_results(tid)]
            prompt = prompts.planner_prompt(task, [e for e in ROSTER["employees"]], files, store.read_notebook("기획실장"),
                                            self.projects(), bool(task.get("projectChosen")),
                                            self._cp_keyed(), self._linked_cp_name(task),
                                            self.background(), KNOW_DIR)
            raw = self.runner.plan(task, prompt, self.settings["input_dirs"],
                                   lambda t: self._set_live("기획실장", "작업 중", t))
            data = extract_json(raw)
            if not data or data.get("type") not in ("question", "plan"):
                raise RunnerError("기획실장의 답을 알아듣지 못했어요. 다시 말씀해 주시겠어요?", raw)
            self.notice = ""
            self._planner_project(task, data)
            if data["type"] == "question":
                task["messages"].append({"role": "planner", "text": data["text"], "ts": store.now()})
                task["status"] = "asking"
                self._save_task(task)
                self._add_approval(taskId=tid, kind="question", owner="기획실장", title="기획실장이 물어봐요", detail=data["text"])
            else:
                steps = []
                for s in data.get("steps", []):
                    e = EMP.get(s.get("employee"))
                    if not e or not e["active"]:
                        continue
                    steps.append({"employee": e["name"], "title": s.get("title", "일"), "instruction": s.get("instruction", ""),
                                  "output": s.get("output") or "", "needs_approval": bool(s.get("needs_approval")) or e["name"] == TRAIN_EMP,
                                  "heygen": bool(s.get("heygen")) and e["name"] == "영상 제작", "approvedToRun": False,
                                  "pdf": str(s.get("pdf") or "").strip() if e["name"] == "영상 제작" else "",
                                  "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": []})
                    self._set_step_engine(steps[-1], s, task)
                if not steps:
                    raise RunnerError("계획에 일할 수 있는 직원이 없어요. 다시 말씀해 주시겠어요?", raw)
                if any(x.get("heygen") for x in steps) and not any(x.get("pdf") for x in steps):
                    # 강의 영상은 PDF 강의자료가 꼭 있어야 해요 → 계획 대신 PDF 위치를 물어봄
                    q = "영상을 만들려면 강의자료 PDF 가 필요해요. 이번 차시의 PDF 파일 이름을 알려 주시고, 그 파일을 자료 폴더에 넣어 주세요."
                    task["messages"].append({"role": "planner", "text": q, "ts": store.now()})
                    task["status"] = "asking"
                    self._save_task(task)
                    self._add_approval(taskId=tid, kind="question", owner="기획실장", title="기획실장이 물어봐요", detail=q)
                    self._clear_live("기획실장")
                    return
                is_idea = self._request_text(task).lstrip().startswith("💡 아이디어 메모")
                if is_idea and len(steps) > 1:      # 아이디어 회의는 기획실장 회의록이 마지막 (점검 단계 없음)
                    steps[:] = [x for x in steps if x["employee"] not in ("검토 담당", VIDEO_REVIEW_EMP)] or steps
                is_video = any(x.get("heygen") or x["employee"] == "영상 제작" for x in steps)
                reviewer = VIDEO_REVIEW_EMP if is_video and VIDEO_REVIEW_EMP in EMP and EMP[VIDEO_REVIEW_EMP]["active"] else "검토 담당"
                only_sec = all(x["employee"] == SEC_EMP for x in steps)
                if not is_idea and not only_sec and steps[-1]["employee"] not in ("검토 담당", VIDEO_REVIEW_EMP) and steps[-1]["employee"] != TRAIN_EMP:
                    steps.append({"employee": reviewer, "title": "최종 점검" if reviewer == "검토 담당" else "영상 결과 확인",
                                  "instruction": "앞 단계 결과물을 규칙과 사실에 맞게 점검하고 고칠 곳을 알려 주세요." if reviewer == "검토 담당" else
                                  "만들어진 영상의 길이·숫자와 이름 발음·아바타 위치·자막을 점검하고 고칠 곳을 시각과 함께 알려 주세요.",
                                  "output": "검토결과_review.md", "needs_approval": False, "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": []})
                if data.get("title"):
                    task["title"] = data["title"][:30]
                if "final_name" in data:
                    task["final_name"] = str(data.get("final_name") or "").strip()[:40]
                if data.get("compare") and not task.get("compare"):      # 기획실장이 비교가 좋겠다고 판단 → 비교 모드
                    self._event(task, "기획실장이 Claude·GPT 비교 모드로 하기로 했어요")
                    self._install_compare_plan(task)
                    self._clear_live("기획실장")
                    return
                task["plan"] = {"summary": data.get("summary", ""), "steps": steps}
                task["status"] = "plan_review"
                task["messages"].append({"role": "planner", "text": "계획서를 올렸어요. 결재함에서 확인해 주세요.\n" + data.get("summary", ""), "ts": store.now()})
                self._save_task(task)
                detail = data.get("summary", "") + "\n\n" + "\n".join(
                    "%d. %s — %s%s%s" % (i + 1, s["employee"], s["title"], self._engine_note(s),
                                      " (결재)" if s["needs_approval"] else " (크레딧 결재 후 헤이젠에서 만듦)" if s.get("heygen") else "") for i, s in enumerate(steps))
                self._add_approval(taskId=tid, kind="plan", owner="기획실장", title="계획서: " + task["title"], detail=detail)
            self._clear_live("기획실장")
        except RunnerCancelled:
            self._clear_live("기획실장")
        except RunnerError as ex:
            self._fail(task, "기획실장", ex)

    # ---- 단계마다 도구 고르기: Claude Code / Claude 채팅 / ChatGPT / claude.ai 프로젝트
    ENGINE_LABEL = {"code": "Claude Code", "chat": "Claude 채팅", "gpt": "ChatGPT", "claude_project": "Claude 프로젝트", "canva": "캔바", "higgs": "힉스필드", "media": "무료 영상 도구"}

    def claude_projects(self):
        return store.load("claude_projects", {"projects": [], "synced": ""})

    def _cp_keyed(self):
        return [{"key": "C%d" % (i + 1), **c} for i, c in enumerate(self.claude_projects().get("projects", []))]

    def _linked_cp_name(self, task):
        url = next((p.get("claudeProject", "") for p in self.projects() if p["id"] == self.task_project(task)), "")
        return next((c["name"] for c in self.claude_projects().get("projects", []) if c["url"] == url), "")

    def _set_step_engine(self, step, s, task):
        want = str(s.get("engine") or "").strip()
        if want in ("canva", "higgs", "media") and not step.get("heygen"):      # 캔바·힉스필드는 그림·영상 직원도 고를 수 있음
            step["engine"] = want
            return
        if step["employee"] in (IMG_EMP, "영상 제작", "검토 담당", VIDEO_REVIEW_EMP, "홈페이지"):
            return
        ek = str(s.get("engine") or "code").strip()
        if ek not in self.ENGINE_LABEL:
            ek = "code"
        if ek == "claude_project":
            want = str(s.get("claude_project") or "").strip()
            cps = self._cp_keyed()
            hit = next((c for c in cps if c["key"].lower() == want.lower() or c["url"] == want), None) \
                or next((c for c in cps if c["name"] == want), None)
            if not hit:      # 연결해 둔 프로젝트가 있으면 그것으로
                url = next((p.get("claudeProject", "") for p in self.projects() if p["id"] == self.task_project(task)), "")
                hit = next((c for c in cps if c["url"] == url), None) if url else None
            if not hit:
                ek = "chat"
            else:
                step["claudeProject"], step["claudeProjectName"] = hit["url"], hit["name"]
        step["engine"] = ek
        if not step.get("output"):
            step["output"] = "결과_result.md"

    def _engine_note(self, s):
        ek = s.get("engine")
        if not ek or ek == "code":
            return ""
        return " [%s%s]" % (self.ENGINE_LABEL[ek], (": " + s["claudeProjectName"]) if ek == "claude_project" and s.get("claudeProjectName") else "")

    def sync_claude_projects(self):
        """claude.ai 프로젝트 목록을 Chrome 으로 읽어 와서 저장 (1~2분, 뒤에서)"""
        if getattr(self, "_cp_syncing", False):
            return {"syncing": True}
        self._cp_syncing = True

        def go():
            try:
                with self.chrome_lock:
                    raw = self.runner.list_claude_projects(self.settings["output_root"])
                data = extract_json(raw) or {}
                if data.get("error") == "login":
                    self.notice = "claude.ai 에 로그인이 필요해요. Chrome 에서 claude.ai 에 로그인해 주세요."
                    return
                ps = [{"name": str(p.get("name", ""))[:60], "url": p["url"]} for p in data.get("projects", [])
                      if str(p.get("url", "")).startswith("https://claude.ai/project/")]
                if ps:
                    store.save("claude_projects", {"projects": ps, "synced": store.now()})
                    store.log("claude.ai 프로젝트 %d개를 불러왔어요" % len(ps))
            except Exception as ex:
                store.log("claude.ai 프로젝트 불러오기 실패: %r" % (ex,))
            finally:
                self._cp_syncing = False
        threading.Thread(target=go, daemon=True).start()
        return {"syncing": True}

    def link_claude_project(self, pid, url):
        with self.lock:
            ps = self.projects()
            pr = next((p for p in ps if p["id"] == pid), None)
            if not pr:
                raise ValueError("그런 프로젝트가 없어요.")
            if url and not any(c["url"] == url for c in self.claude_projects().get("projects", [])):
                raise ValueError("그런 claude.ai 프로젝트가 없어요. 목록을 다시 불러와 주세요.")
            pr["claudeProject"] = url or ""
            store.save("projects", ps)
        return pr

    def _do_web_chat_step(self, task, idx, step):
        """ChatGPT 나 claude.ai 프로젝트 화면에 묻고 답을 파일로 받아 옴 (Chrome 은 한 번에 한 직원)"""
        tid, name = task["id"], step["employee"]
        out = step.get("output") or "결과_result.md"
        step["output"] = out
        step["status"] = "running"
        step["started"] = store.now()
        step["finished"] = ""
        self._save_task(task)
        label = self.ENGINE_LABEL[step["engine"]]
        self._set_live(name, "작업 중", "Chrome 차례 기다리는 중", task=task["title"], step=step["title"])
        self.live[name]["taskId"] = tid
        try:
            prev = []
            for s in task["plan"]["steps"][:idx]:
                for f in s.get("files", []):
                    if f.endswith((".md", ".txt")):
                        try:
                            with open(os.path.join(task["outdir"], f), encoding="utf-8") as fh:
                                prev.append("[%s]\n%s" % (f, fh.read()[:4000]))
                        except OSError:
                            pass
            req = "%s\n\n[이번에 할 일: %s]\n%s" % (self._request_text(task), step["title"], step.get("instruction", ""))
            if prev:
                req += "\n\n[앞 단계 결과 — 참고]\n" + "\n\n".join(prev)[:9000]
            prompt = prompts.web_chat_prompt(step["engine"], req, out, step.get("claudeProject", ""), step.get("claudeProjectName", ""), step.get("feedback", []))
            started = time.time() - 1
            with self.chrome_lock:
                if (self.get_task(tid) or {}).get("status") != "running":
                    raise RunnerCancelled()
                self._set_live(name, "작업 중", "%s 에 묻는 중" % label)
                res = self.runner.step(EMP[name], task, step, prompt, [], lambda t: self._set_live(name, "작업 중", t))
            if not os.path.exists(os.path.join(task["outdir"], out)):
                raise RunnerError("%s 에서 답을 받아 오지 못했어요: %s" % (label, (res or "").strip()[:300]), res or "")
            step["result"] = (res or "")[:2000]
            if out.endswith(".md"):
                mdp = os.path.join(task["outdir"], out)
                docs.md_to_docx(open(mdp, encoding="utf-8").read(), mdp[:-3] + ".docx", step["title"])
            step["files"] = self._changed_files(task, started)
            step["finished"] = store.now()
            last = idx == len(task["plan"]["steps"]) - 1
            if step.get("needs_approval") and (not last or name == TRAIN_EMP):      # 교육은 마지막 단계여도 교수님 결재
                step["status"] = "waiting"
                self._save_task(task)
                self._add_approval(taskId=tid, kind="step", owner=name, title="%s: %s" % (name, step["title"]),
                                   detail=step["result"], files=step["files"], stepIndex=idx)
                self._clear_live(name)
            else:
                step["status"] = "done"
                self._bump(name, step.get("revisions", 0) == 0)
                self._save_task(task)
                self._clear_live(name, step["title"] + " 완료")
                self.q.put(("next", tid))
        except RunnerCancelled:
            fresh = self.get_task(tid) or task
            for st in (fresh.get("plan") or {}).get("steps", []):
                if st.get("status") == "running":
                    st["status"] = "pending"
            self._save_task(fresh)
            self._clear_live(name)
        except RunnerError as ex:
            step["status"] = "pending"
            self._fail(task, name, ex)

    GENERIC_WORDS = {"영상", "강의", "강의자료", "슬라이드", "ppt", "대본", "나레이션", "자료", "그림", "이미지", "헤이젠", "heygen",
                     "아바타", "책", "편집", "출판", "홈페이지", "수업", "교안", "만들기", "제작"}

    def _planner_project(self, task, data):
        """기획실장이 정한 프로젝트: 있는 것에 넣거나, 맞는 게 없으면 새 프로젝트를 만들어 현황판에 칸을 추가"""
        if task.get("projectChosen"):
            return
        try:
            np = data.get("new_project")
            if isinstance(np, dict) and str(np.get("name") or "").strip():
                name = str(np["name"]).strip()[:20]
                guess = self._guess_project(self._request_text(task))
                if guess != "p-etc":       # 이미 있는 프로젝트 낱말에 걸리면 새로 만들지 않고 거기에 넣음
                    task["project"] = guess
                    return
                same = next((p for p in self.projects() if p["name"] == name), None)
                if same:
                    pid = same["id"]
                else:
                    kws = np.get("keywords") or []
                    kws = [str(k) for k in kws] if isinstance(kws, list) else re.split(r"[,，]", str(kws))
                    # 흔한 말(영상·강의 등)은 빼요 — 들어가면 다른 주제의 일까지 이 프로젝트로 끌려와요
                    kws = ", ".join(k.strip() for k in kws if k.strip() and k.strip().lower() not in self.GENERIC_WORDS) or name
                    pid = self.add_project(name, str(np.get("goal") or ""), "", kws)["id"]
                    task["messages"].append({"role": "planner", "ts": store.now(),
                                             "text": "새 프로젝트 '%s' 를 만들어 현황판에 칸을 추가했어요. 다른 프로젝트로 옮기려면 위의 '프로젝트' 칸에서 바꿔 주세요." % name})
                    store.log("기획실장이 새 프로젝트를 만듦: %s (%s)" % (name, task["id"]))
                task["project"] = pid
            elif data.get("project") and any(p["id"] == data["project"] for p in self.projects()):
                task["project"] = data["project"]
        except Exception as ex:      # 프로젝트 정하기가 잘못돼도 일은 계속
            store.log("프로젝트 정하기 실패 %s: %r" % (task["id"], ex))

    TRANSIENT = ("결과가 오지 않았어요", "실행하지 못했어요", "알아듣지 못했어요")

    def _fail(self, task, name, ex):
        store.log("실패 %s: %s | %s" % (name, ex.friendly, ex.raw[-500:] if ex.raw else ""))
        self._clear_live(name)
        task["status"] = "error"
        task["error"] = ex.friendly
        at = None
        if isinstance(ex, RunnerLimit):      # 사용량 한도: 다시 채워지는 시각에 저절로
            at = ex.reset_at or time.time() + 1800
            task["autoRetries"] = 0
        elif any(k in ex.friendly for k in self.TRANSIENT) and task.get("autoRetries", 0) < 2:      # 잠깐 생긴 오류: 3분 뒤 두 번까지
            at = time.time() + 180
            task["autoRetries"] = task.get("autoRetries", 0) + 1
        if at:
            task["autoRetryAt"] = at
            task["error"] = ex.friendly + ("" if isinstance(ex, RunnerLimit) else " (%s 에 저절로 다시 해요)" % time.strftime("%H:%M", time.localtime(at)))
            self._schedule_auto_retry(task["id"], at)
        else:
            task.pop("autoRetryAt", None)
            self.notice = ex.friendly
            self.notice_tid = task["id"]
        self._save_task(task)

    def _schedule_auto_retry(self, tid, at):
        t = threading.Timer(max(5, at - time.time()), self._auto_retry, args=(tid,))
        t.daemon = True
        t.start()

    def _auto_retry(self, tid):
        task = self.get_task(tid)
        if not task or task["status"] != "error" or not task.get("autoRetryAt"):
            return
        if task["autoRetryAt"] > time.time() + 30:      # 그사이 시각이 바뀜
            return self._schedule_auto_retry(tid, task["autoRetryAt"])
        try:
            self._event(task, "기획실장이 저절로 다시 시도해요")
            task.pop("autoRetryAt", None)
            self._save_task(task, force=True)
            self.retry(tid)
            store.log("저절로 다시 시도: %s" % tid)
        except Exception as ex:
            store.log("저절로 다시 시도 실패 %s: %r" % (tid, ex))

    def _do_next(self, tid):
        task = self.get_task(tid)
        if not task or task["status"] != "running":
            return
        steps = task["plan"]["steps"]
        for i, s in enumerate(steps):
            if s["status"] == "pending":
                return self._do_step(tid, i)
            if s["status"] in ("waiting", "running", "rendering", "composing"):
                return
        # 모든 단계가 끝남 → 최종 보고
        task["status"] = "review"
        self._save_task(task)
        files = self.task_files(task)
        if task.get("compare"):      # 비교 모드: 기획실장이 대신 승인하지 않고 교수님이 하나를 골라요
            rep = ""
            try:
                with open(os.path.join(task["outdir"], prompts.COMPARE_REPORT), encoding="utf-8") as f:
                    rep = f.read()
            except OSError:
                pass
            m = re.search(r"추천\s*[:：]\s*\**\s*(A|B|합본)", rep)
            rec = {"A": "Claude 답안", "B": "GPT 답안", "합본": "합본"}.get(m.group(1) if m else "", "")
            self._add_approval(taskId=tid, kind="pick", owner="기획실장", title="고르기: " + task["title"],
                               detail="Claude 와 GPT 가 같은 일을 따로 했어요. 「⚖ 나란히 보기」로 비교해 보고 하나를 골라 주세요."
                                      + ("\n검토 담당 추천: " + rec if rec else ""), files=files)
            self._clear_live("기획실장", "비교 결과를 올렸어요")
            return
        note = ""
        if self._needs_pm_review(task):
            back, note = self._pm_review(task)
            if back:
                return
        files = self.main_files(task) or self.task_files(task)      # 꼭 볼 파일부터 (결재함에서 바로 미리보기)
        self._add_approval(taskId=tid, kind="final", owner="기획실장", title="보고: " + task["title"],
                           detail=task["plan"].get("summary", "") + (("\n\n🧐 기획실장 검토: " + note) if note else ""), files=files)
        self._set_live("기획실장", "작업 중", "보고서 정리 중")
        self._clear_live("기획실장", "일을 마쳤어요")

    PM_ROUNDS = 2      # 기획실장이 돌려보내는 건 한 일에 두 번까지 (그다음은 의견을 붙여 교수님께)

    def _needs_pm_review(self, task):
        steps = (task.get("plan") or {}).get("steps", [])
        if not steps or task.get("compare"):
            return False
        if any(x.get("heygen") or x.get("employee") == "영상 제작" for x in steps):      # 영상은 크레딧·발음 결재로 이미 확인
            return False
        if self._request_text(task).lstrip().startswith(("📅 아침 보고", "📚 자기계발")):
            return False
        return True

    def _pm_review(self, task):
        """기획실장이 결과를 진짜로 검토: 승인 / 보완(그 단계 고치기) / 다시(그 단계부터 처음부터). → (돌려보냈나, 메모)"""
        tid = task["id"]
        steps = task["plan"]["steps"]
        self._set_live("기획실장", "작업 중", "결과 검토 중", task=task["title"], step="결과 검토")
        try:
            listing = "\n".join("%d. %s — %s → %s" % (i + 1, x["employee"], x["title"], x.get("output") or ", ".join(x.get("files", [])[:4]))
                                 for i, x in enumerate(steps))
            style = ""
            try:
                style = open(os.path.join(KNOW_DIR, "교수님_일_스타일.md"), encoding="utf-8").read()[:3500]
            except OSError:
                pass
            prompt = prompts.PM_REVIEW.format(request=self._request_text(task)[:3000], summary=task["plan"].get("summary", ""),
                                              steps=listing, style=style, rounds=task.get("pmRounds", 0), maxr=self.PM_ROUNDS)
            data = extract_json(self.runner.review(task, prompt) or "") or {}
        except Exception as ex:
            store.log("기획실장 검토 실패 %s: %r" % (tid, ex))
            self._clear_live("기획실장")
            return False, ""
        dec = str(data.get("decision") or "approve").lower()
        reason = " ".join(str(data.get("reason") or "").split())[:400]
        try:
            idx = int(data.get("step") or 0) - 1
        except (TypeError, ValueError):
            idx = -1
        if not (0 <= idx < len(steps)):
            idx = max(0, len(steps) - 1)
        if dec in ("revise", "redo") and task.get("pmRounds", 0) < self.PM_ROUNDS and reason:
            task["pmRounds"] = task.get("pmRounds", 0) + 1
            emp = steps[idx]["employee"]
            for j in range(idx, len(steps)):          # 그 단계와 뒤 단계를 다시
                st = steps[j]
                st["status"] = "pending"
                if j == idx:
                    st["revisions"] = st.get("revisions", 0) + 1
                    st.setdefault("feedback", []).append(("처음부터 다시: " if dec == "redo" else "보완: ") + "기획실장 검토 — " + reason)
            word = "처음부터 다시 하라고" if dec == "redo" else "보완하라고"
            self._score(emp, PTS_REVISE, "기획실장 검토 — %s: %s" % ("다시" if dec == "redo" else "보완", reason), tid)
            task["status"] = "running"
            task["messages"].append({"role": "planner", "ts": store.now(),
                                     "text": "🧐 결과를 검토했어요. %s에게 %s 돌려보냈어요 (%d/%d번째).\n이유: %s" % (emp, word, task["pmRounds"], self.PM_ROUNDS, reason)})
            self._event(task, "기획실장이 %s에게 %s 했어요" % (emp, word))
            self._save_task(task)
            store.log("기획실장 검토 → %s %s: %s" % (emp, dec, reason[:80]))
            self._clear_live("기획실장", "검토 의견을 줬어요")
            self.q.put(("next", tid))
            return True, ""
        self._clear_live("기획실장")
        if dec in ("revise", "redo") and reason:
            return False, "아직 아쉬운 점 — " + reason + " (두 번 돌려보낸 뒤라 교수님 판단을 받아요)"
        return False, reason or "요청하신 내용과 규칙에 맞게 되었어요."

    def _higgs_quote(self, task, idx, step, emp, name):
        """힉스필드: 먼저 견적만 받아(만들지 않음) 교수님께 크레딧 결재를 올림"""
        step["status"] = "running"
        self._save_task(task)
        self._set_live(name, "작업 중", "힉스필드 견적 내는 중", task=task["title"], step=step["title"])
        try:
            prompt = prompts.step_prompt(task, step, idx, store.read_notebook(name), step.get("feedback", []), [], [], task["outdir"]) \
                + "\n\n" + prompts.HIGGS_QUOTE
            self.runner.step(emp, task, step, prompt, self.settings["input_dirs"], lambda t: self._set_live(name, "작업 중", t))
            q = {}
            try:
                q = json.load(open(os.path.join(task["outdir"], "higgs_견적_quote.json"), encoding="utf-8"))
            except (OSError, ValueError):
                pass
            step["status"] = "waiting"
            step["higgsQuote"] = q
            self._save_task(task)
            lines = ["힉스필드에서 만들어요. 크레딧이 들어요.",
                     "· 만들 것: %s" % (q.get("plan") or step["title"]),
                     "· 모델: %s" % (q.get("model") or "-"),
                     ("· 예상 크레딧: 약 %s" % q["credits"]) if q.get("credits") is not None else "· 예상 크레딧: 힉스필드가 미리 알려 주지 않는 모델이에요 (보통 그림 한 장은 몇~수십 크레딧)",
                     "· 남은 크레딧: %s" % (q.get("balance") if q.get("balance") is not None else "-"),
                     "승인하면 그대로 한 번만 만들어요. 실패하거나 다시 만들 때는 새 결재를 받아요."]
            self._add_approval(taskId=task["id"], kind="credit", owner=name, title="힉스필드 만들기 (크레딧): " + task["title"],
                               detail="\n".join(lines), stepIndex=idx, manual=True)
            self._clear_live(name, "견적을 올렸어요")
        except RunnerError as ex:
            step["status"] = "pending"
            self._fail(task, name, ex)

    def _fetch_tool_results(self, task, step):
        """캔바·힉스필드 결과: 직원이 적어 둔 주소(…_결과_result.json)의 파일을 작업 폴더로 내려받음"""
        for fn in ("canva_결과_result.json", "higgs_결과_result.json"):
            fp = os.path.join(task["outdir"], fn)
            if not os.path.exists(fp):
                continue
            try:
                data = json.load(open(fp, encoding="utf-8"))
            except ValueError:
                continue
            for i, it in enumerate(data.get("files", []) if isinstance(data, dict) else []):
                url, nm = (it.get("url"), it.get("name")) if isinstance(it, dict) else (it, "")
                if not url or not str(url).startswith("https://"):
                    continue
                nm = self.safe_name(nm or ("%s_%d%s" % (fn.split("_")[0], i + 1, os.path.splitext(str(url).split("?")[0])[1] or ".bin")))
                try:
                    self.runner.download(url, os.path.join(task["outdir"], nm))
                except Exception as ex:
                    store.log("결과 내려받기 실패 %s: %r" % (nm, ex))

    def _prepare_texts(self, task):
        """슬라이드·워드 자료를 글자 파일로 뽑아 산출물 폴더 안 _자료_text 에 둠"""
        tdir = os.path.join(task["outdir"], "_자료_text")
        os.makedirs(tdir, exist_ok=True)
        out = []
        for p in self._input_files(library=False):      # 자료실은 너무 커서 글자 뽑기는 하지 않음 (필요하면 직원이 직접 읽음)
            if os.path.isdir(p):
                continue
            dst = os.path.join(tdir, os.path.splitext(os.path.basename(p))[0] + ".txt")
            if not os.path.exists(dst):
                try:
                    t = docs.extract_text(p)
                except Exception as ex:
                    t = None
                    store.log("글자 뽑기 실패 %s: %r" % (p, ex))
                if t:
                    with open(dst, "w", encoding="utf-8") as f:
                        f.write(t)
            if os.path.exists(dst):
                out.append(dst)
        for t, rel, src in self._prior_results(task["id"]):     # 지난 일에서 승인된 결과물도 읽을 수 있게
            dst = os.path.join(tdir, "지난결과_%s_%s.txt" % (t["id"], os.path.splitext(os.path.basename(rel))[0]))
            if not os.path.exists(dst):
                try:
                    shutil.copyfile(src, dst)
                except OSError as ex:
                    store.log("지난 결과 복사 실패 %s: %r" % (src, ex))
                    continue
            out.append(dst)
        return out

    # ---- 성서 40주간 사이트 제작 (성화 생성 + 배포) — 서버가 직접 함
    def _request_art_approval(self, task, idx):
        """성화 생성 직전의 비용 결재: 몇 장을 만들지·예상 비용을 보여 줌."""
        cfg = self.settings["seongseo"]
        step = task["plan"]["steps"][idx]
        have = seongseo.count_art(cfg)
        rng = step.get("artRange")
        target = seongseo.target_count(cfg, rng)
        cost = seongseo.est_cost(cfg, rng)
        lines = [
            "성서 40주간 성화 그림을 OpenAI 로 만들어요. 돈이 들어요.",
            "· 지금 완성: %d / %d장" % (have, cfg["total"]),
            "· 이번에 만들 그림: %d장%s" % (target, " (테스트)" if rng else ""),
            "· 예상 비용: 약 $%.0f (그림 1장당 약 $%.2f, 교수님 OpenAI 결제)" % (cost, cfg["price_per_image"]),
            "· 저장 위치: 홈페이지배포/images (글·데이터 원본은 건드리지 않아요)",
            "",
            "승인하면 바로 그림을 만들기 시작하고, 화면에서 '성화 ○/365장 그리는 중'이 실시간으로 보여요.",
            "더 생각해 보려면 「수정 요청」에 이유를 적어 주세요. (일은 잠시 멈춰 둬요)",
        ]
        step["status"] = "waiting"
        step["approvedToRun"] = False
        self._save_task(task)
        self._add_approval(taskId=task["id"], kind="credit", owner=step["employee"],
                           title="성화 그림 만들기 (비용): " + task["title"], detail="\n".join(lines), stepIndex=idx)
        self._clear_live(step["employee"])

    def _do_site_step(self, task, idx, step, name):
        cfg = self.settings["seongseo"]
        tid = task["id"]
        job = step.get("job")
        if job == "art" and not step.get("approvedToRun"):
            # 돈 드는 일: 결재 없이는 절대 실행하지 않음
            self._request_art_approval(task, idx)
            return
        step["status"] = "running"
        step["started"] = store.now()
        step["finished"] = ""
        self._save_task(task)
        self._set_live(name, "작업 중", "준비 중…", task=task["title"], step=step["title"])
        self.live[name]["taskId"] = tid
        try:
            if job == "art":
                rng = step.get("artRange")

                def prog(done, total):
                    self._set_live(name, "작업 중", "성화 %d/%d장 그리는 중" % (done, total))
                ok, final_c, msg = seongseo.run_art(
                    cfg, self.settings["fake"], prog, rng,
                    lambda: (self.get_task(tid) or {}).get("status") in ("paused", "cancelled"))
                if not ok:
                    step["status"] = "pending"
                    self._fail(task, name, RunnerError(msg, ""))
                    return
                step["result"] = "성화 생성을 마쳤어요 — 지금 %d/%d장 완성.%s" % (
                    final_c, cfg["total"], (" " + msg) if msg else "")
            else:  # deploy
                self._set_live(name, "작업 중", "사이트에 올리는 중…")
                if self.settings["fake"]:
                    ok, url, msg = seongseo.run_deploy(cfg, True, lambda m: self._set_live(name, "작업 중", m))
                else:      # 서버 대신 직원(Claude Code)이 배포 실행 (바탕화면 폴더 접근 때문)
                    ok, url, msg = self.runner.deploy_site(task["outdir"], tag=tid)
                if not ok:
                    step["status"] = "pending"
                    self._fail(task, name, RunnerError(msg, ""))
                    return
                url = url or cfg["site_url"]
                try:
                    with open(os.path.join(task["outdir"], "배포결과.txt"), "w", encoding="utf-8") as f:
                        f.write("성서 40주간 사이트가 인터넷에 올라갔어요.\n주소: %s\n올린 시각: %s\n%s\n"
                                % (url, store.now(), ("(연습 모드)" if self.settings["fake"] else "")))
                    step["files"] = ["배포결과.txt"]
                except OSError:
                    pass
                step["result"] = "배포를 마쳤어요! 사이트 주소: %s%s" % (url, " (연습 모드라 실제로는 안 올렸어요)" if self.settings["fake"] else "")
            step["finished"] = store.now()
            step["status"] = "done"
            self._bump(name, step.get("revisions", 0) == 0)
            self._save_task(task)
            self._clear_live(name, step["title"] + " 완료")
            self.q.put(("next", tid))
        except RunnerCancelled:
            fresh = self.get_task(tid) or task
            for st in (fresh.get("plan") or {}).get("steps", []):
                if st.get("status") == "running":
                    st["status"] = "pending"
                    st["finished"] = ""
            self._save_task(fresh)
            self._clear_live(name)
        except Exception as ex:
            step["status"] = "pending"
            self._fail(task, name, RunnerError("작업 중 문제가 생겼어요: %s" % ex, str(ex)))

    def _do_step(self, tid, idx):
        task = self.get_task(tid)
        if not task or task["status"] != "running":
            return
        step = task["plan"]["steps"][idx]
        emp = EMP[step["employee"]]
        name = emp["name"]
        if step.get("job") in ("art", "deploy"):
            return self._do_site_step(task, idx, step, name)
        if step.get("job") == "compare":
            return self._do_compare_step(task, idx, step)
        if step.get("engine") in ("gpt", "claude_project") and name != IMG_EMP:
            return self._do_web_chat_step(task, idx, step)
        if step.get("engine") == "higgs" and not step.get("approvedToRun"):
            return self._higgs_quote(task, idx, step, emp, name)
        if step.get("heygen"):
            hg = self._hg(task)
            if not hg.get("avatar_id") or not hg.get("voice_id"):
                self._fail(task, name, RunnerError("헤이젠 아바타와 목소리가 아직 정해지지 않았어요. 어떤 아바타와 목소리로 만들지 알려 주세요.", ""))
                return
            if not self._script_path(task):
                self._fail(task, name, RunnerError("영상용 대본 파일(heygen_script.txt)이 없어요. 대본 정리 단계를 먼저 해야 해요.", ""))
                return
            # 11-13: 대본 정리 1~6장을 먼저 다 하고 영상 1~6장을 만들면, 대본 정리마다 같은 폴더(_slides·heygen_scenes)를 덮어써서
            #        모든 장이 마지막 장(6장) 내용으로 만들어졌어요. → 이 영상의 짝 대본 정리가 바로 앞 대본 정리가 아니면,
            #        짝 대본 정리를 이 영상 바로 앞으로 옮겨 다시 하게 해요 (크레딧 쓰기 전에 바로잡음).
            steps = task["plan"]["steps"]
            scr = [j for j, s2 in enumerate(steps) if s2.get("output") == "heygen_script.txt"]
            vids = [j for j, s2 in enumerate(steps) if s2.get("heygen")]
            def _lab(t):      # "3장 헤이젠 영상 만들기" → "3장"
                m2 = re.search(r"(\d+)\s*(장|차시|편|회)", t or "")
                return m2.group(1) + m2.group(2)[0] if m2 else ""
            if len(scr) > 1 and len(scr) == len(vids) and idx in vids:
                labs = {j: _lab(steps[j].get("title")) for j in scr}
                mine = _lab(step.get("title"))
                if mine and list(labs.values()).count(mine) == 1:
                    want = next(j for j in scr if labs[j] == mine)      # 이름(1장·2장)으로 짝 찾기 (순서로 찾으면 옮긴 뒤 어긋남)
                else:
                    want = scr[vids.index(idx)]
                last = max((j for j in scr if j < idx), default=None)
                if want != last and want < idx:
                    mv = steps.pop(want)
                    mv["status"] = "pending"
                    mv["started"] = mv["finished"] = ""
                    steps.insert(idx - 1, mv)
                    task.setdefault("events", []).append({"ts": store.now(), "text": "「%s」 짝 대본 정리(%s)를 영상 바로 앞으로 옮겨 다시 해요 (다른 장 대본으로 만들지 않게)." % (step["title"], mv["title"])})
                    store.log("대본 짝 맞추기: %s → %s 앞 (%s)" % (mv["title"], step["title"], tid))
                    self._save_task(task)
                    self.q.put(("step", task["id"], idx - 1))
                    return
            if task.get("lecture") or step.get("pdf") or os.path.exists(os.path.join(task["outdir"], "heygen_scenes.json")):
                why = self._preflight(task, step)      # 🔎 미리 점검: 크레딧 쓰기 전에 대본 짝·목소리·합치기를 확인
                if why:
                    self._fail(task, name, RunnerError("🔎 미리 점검에서 멈췄어요 (크레딧은 쓰지 않았어요): " + why, ""))
                    return
            if not step.get("approvedToRun"):      # 크레딧이 드는 일: 결재 없이는 절대 실행하지 않음
                self._request_credit_approval(task, idx)
                return
            old = os.path.join(task["outdir"], "video_job.json")
            if os.path.exists(old):
                os.remove(old)
        step["status"] = "running"
        step["started"] = store.now()
        step["finished"] = ""
        self._save_task(task)
        self._set_live(name, "작업 중", step["title"], task=task["title"], step=step["title"])
        self.live[name]["taskId"] = tid
        try:
            if step.get("heygen") and (task.get("lecture") or {}).get("mode") == "split":
                self._make_voices(task, step, emp, name)      # 목소리만 쪽 먼저 (실패하면 아바타 영상은 만들지 않음 → 크레딧 안 씀)
            if step.get("heygen") and len((task.get("lecture") or {}).get("chunks") or []) > 1:
                return self._submit_chunks(task, idx, step, emp, name)
            texts = self._prepare_texts(task)
            prev = []
            for s in task["plan"]["steps"][:idx]:
                prev += s.get("files", [])
            if step.get("heygen"):
                hg2 = dict(self._hg(task))
                if task.get("lecture"):      # 슬라이드 구석에 작게 나오는 아바타라서 낮은 화질로 충분함(크레딧도 아낌)
                    hg2["resolution"] = hg2.get("avatar_resolution", "720p")
                prompt = prompts.heygen_prompt(task, step, hg2, self._script_path(task), task["title"], store.get_rules(name))
            else:
                prompt = prompts.step_prompt(task, step, idx, store.read_notebook(name), step.get("feedback", []), texts, prev, task["outdir"])
                if step.get("engine") == "chat":
                    prompt += "\n\n" + prompts.CHAT_MODE_NOTE
                if step.get("output") == "heygen_script.txt" and step.get("pdf"):
                    pngs, ptexts = self._prepare_slides(task, step)       # PDF → 쪽 그림 (없으면 여기서 친절한 오류)
                    prompt += "\n\n" + prompts.lecture_scenes_rules(len(pngs), ptexts, self._lecture_hint(name))
                elif step.get("output") == "heygen_script.txt":
                    prompt += "\n\n" + prompts.SCRIPT_CLEAN_RULES
                if name == IMG_EMP and step.get("engine") not in ("canva", "higgs"):
                    prompt += "\n\n" + prompts.IMAGE_RULES
                if step.get("engine") == "canva":
                    prompt += "\n\n" + prompts.CANVA_GUIDE
                if step.get("engine") == "media":
                    prompt += "\n\n" + prompts.MEDIA_GUIDE.format(media=MEDIA_DIR, out=task["outdir"])
                if step.get("engine") == "higgs":
                    prompt += "\n\n" + prompts.HIGGS_MAKE
            started = time.time() - 1
            if name == IMG_EMP and step.get("engine") not in ("canva", "higgs"):      # Chrome 화면은 한 번에 한 직원만 (비교 모드의 GPT 답 받아 오기와 겹치지 않게)
                self._set_live(name, "작업 중", "Chrome 차례 기다리는 중")
                with self.chrome_lock:
                    result = self.runner.step(emp, task, step, prompt, self.settings["input_dirs"],
                                              lambda t: self._set_live(name, "작업 중", t))
            else:
                result = self.runner.step(emp, task, step, prompt, self.settings["input_dirs"],
                                          lambda t: self._set_live(name, "작업 중", t))
            step["result"] = (result or "")[:2000]
            if name == IMG_EMP and step.get("engine") not in ("canva", "higgs"):
                self._collect_downloads(task, started)
            if step.get("engine") in ("canva", "higgs"):
                self._fetch_tool_results(task, step)
            if step.get("pdf") and step.get("output") == "heygen_script.txt":
                self._build_lecture(task, step)          # 쪽마다 나눈 대본 확인 → heygen_script.txt 만들기
            if step.get("heygen"):
                return self._after_heygen_submit(task, idx, step, name, result, started)
            step["finished"] = store.now()
            out = step.get("output")
            if out and out.endswith(".md"):
                mdp = os.path.join(task["outdir"], out)
                if os.path.exists(mdp):
                    docs.md_to_docx(open(mdp, encoding="utf-8").read(), mdp[:-3] + ".docx", step["title"])
            step["files"] = self._changed_files(task, started)
            last = idx == len(task["plan"]["steps"]) - 1
            if step.get("needs_approval") and (not last or name == TRAIN_EMP):      # 교육은 마지막 단계여도 교수님 결재
                step["status"] = "waiting"
                self._save_task(task)
                self._add_approval(taskId=tid, kind="step", owner=name, title="%s: %s" % (name, step["title"]),
                                   detail=step["result"], files=step["files"], stepIndex=idx)
                self._clear_live(name)
            else:
                step["status"] = "done"
                self._bump(name, step.get("revisions", 0) == 0)
                self._save_task(task)
                self._clear_live(name, step["title"] + " 완료")
                self.q.put(("next", tid))
        except RunnerCancelled:
            fresh = self.get_task(tid) or task
            for st in (fresh.get("plan") or {}).get("steps", []):
                if st.get("status") == "running":
                    st["status"] = "pending"
                    st["finished"] = ""
            self._save_task(fresh)
            self._clear_live(name)
        except RunnerError as ex:
            step["status"] = "pending"
            self._fail(task, name, ex)

    # ---- 강의자료 PDF → 슬라이드 그림, 쪽별 대본, 합치기
    def _find_pdf(self, name, task=None):
        """강의자료 PDF 찾기. 찾는 곳: ① 이 일의 작업 폴더(📎·📁로 올린 첨부_attachments 안, 폴더째 올린 것도) ② 자료 폴더.
        같은 이름을 먼저 찾고, 없으면 이름 일부가 겹치는 PDF를 찾음"""
        name = (name or "").strip().replace("\\", "/")
        if not name:
            return None
        base = os.path.basename(name)
        stem = os.path.splitext(base)[0].lower()
        roots = []
        if task and os.path.isdir(task.get("outdir") or ""):
            roots.append(task["outdir"])
            p = os.path.join(task["outdir"], name)
            if os.path.isfile(p):
                return p
        roots += [d for d in self.settings["input_dirs"] if os.path.isdir(d)]
        pdfs = []
        for r in roots:
            p = os.path.join(r, base)
            if os.path.isfile(p):
                return p
            for cur, dirs, files in os.walk(r):
                dirs[:] = sorted(x for x in dirs if not x.startswith((".", "_slides", "_voice", "_이전")))
                pdfs += [os.path.join(cur, f) for f in sorted(files) if f.lower().endswith(".pdf")]
        for p in pdfs:
            if os.path.basename(p).lower() == base.lower():
                return p
        for p in pdfs:
            if stem and stem in os.path.basename(p).lower():
                return p
        return None

    def _prepare_slides(self, task, step):
        """PDF의 쪽마다 그림(PNG)과 글자를 뽑아 산출물 폴더 안 _slides 에 둠 → (그림 목록, 글자 파일 목록)"""
        pdf = self._find_pdf(step["pdf"], task)
        if not pdf:
            raise RunnerError("강의자료 PDF('%s')를 찾지 못했어요. 이 일에 📎·📁로 올린 파일과 자료 폴더(%s)를 모두 찾아봤어요. "
                              "PDF를 이 일에 다시 붙여 주세요." % (
                step["pdf"], ", ".join(os.path.basename(d) for d in self.settings["input_dirs"])), "")
        out = os.path.join(task["outdir"], "_slides")
        shutil.rmtree(out, ignore_errors=True)
        swift = shutil.which("swift") or "/usr/bin/swift"
        try:
            r = subprocess.run([swift, os.path.join(ROOT, "tools", "pdf_to_png.swift"), pdf, out, "1920"],
                               capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as ex:
            raise RunnerError("PDF를 쪽 그림으로 바꾸지 못했어요.", str(ex))
        if r.returncode != 0:
            raise RunnerError("PDF를 쪽 그림으로 바꾸지 못했어요. PDF 파일이 열리는지 확인해 주세요.", (r.stdout + r.stderr)[-400:])
        pngs = sorted(os.path.join(out, f) for f in os.listdir(out) if f.endswith(".png"))
        if not pngs:
            raise RunnerError("PDF에서 쪽 그림을 만들지 못했어요.", r.stdout)
        txts = [p[:-4] + ".txt" for p in pngs if os.path.exists(p[:-4] + ".txt") and os.path.getsize(p[:-4] + ".txt") > 0]
        step["pdfPath"] = pdf
        return pngs, txts

    def _cutout_tool(self):
        """사람 오리기 도구(avatar_cutout). 없으면 소스에서 한 번 만들어 둠 (맥에 들어 있는 Swift 사용)"""
        tool = os.path.join(ROOT, "tools", "avatar_cutout")
        src = tool + ".swift"
        if not os.path.exists(tool) or os.path.getmtime(tool) < os.path.getmtime(src):
            swiftc = shutil.which("swiftc") or "/usr/bin/swiftc"
            r = subprocess.run([swiftc, "-O", src, "-o", tool], capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                raise RuntimeError("사람 오리기 도구를 만들지 못했어요: " + r.stderr[-300:])
        return tool

    def _lecture_hint(self, name):
        rules = store.get_rules(name)
        return ("- 교수님이 정해 두신 규칙도 지키세요:\n" + "\n".join("  · " + r for r in rules)) if rules else ""

    def _build_lecture(self, task, step):
        """직원이 만든 heygen_scenes.json 을 확인하고, 읽을 글(heygen_script.txt)과 합치기 정보를 정함"""
        sp = os.path.join(task["outdir"], "heygen_scenes.json")
        if not os.path.exists(sp):
            raise RunnerError("쪽마다 나눈 대본 파일(heygen_scenes.json)이 만들어지지 않았어요. 다시 시도해 주세요.", "")
        try:
            pages = sorted(json.load(open(sp, encoding="utf-8"))["pages"], key=lambda x: int(x["page"]))
        except (ValueError, KeyError, TypeError):
            raise RunnerError("쪽마다 나눈 대본 파일의 모양이 맞지 않아요. 다시 시도해 주세요.", "")
        slides = sorted(f for f in os.listdir(os.path.join(task["outdir"], "_slides")) if f.endswith(".png"))
        if len(pages) != len(slides):
            raise RunnerError("대본을 나눈 쪽 수(%d)와 PDF 쪽 수(%d)가 달라요. 대본과 PDF를 맞춰서 다시 시도해 주세요." % (len(pages), len(slides)), "")
        # 아바타가 소리 내어 읽을 글은 숫자·알파벳 약자를 한글 소리로 바꿈 (6차시 → 육차시). 자막 맞추기도 같은 글로 함.
        originals = [str(p.get("text") or "") for p in pages]
        for p in pages:
            p["text"] = ko_speech.apply_pronounce(ko_speech.to_spoken(str(p.get("text") or "")), self._hg(task).get("pronounce"))   # 교수님이 들어 보고 정한 발음
        json.dump({"pages": pages}, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        pron = self._pronunciation_check(task, pages, originals)
        texts = [str(p.get("text") or "").strip() for p in pages]
        if not any(texts):
            raise RunnerError("읽을 대본이 비어 있어요.", "")
        avatar_pages = sorted(int(p["page"]) for p in pages if p.get("avatar")) or [1]
        split = self._hg(task).get("lecture_mode", "split") == "split"
        voice_pages = []
        if split:
            # 크레딧 아끼기: 아바타는 아바타 쪽만 영상으로 만들고, 나머지 쪽은 목소리만 만들어 슬라이드에 입힘
            vdir = os.path.join(task["outdir"], "_voice")
            shutil.rmtree(vdir, ignore_errors=True)
            os.makedirs(vdir, exist_ok=True)
            for i, t in enumerate(texts, 1):
                if i not in avatar_pages and t:
                    with open(os.path.join(vdir, "page_%03d.txt" % i), "w", encoding="utf-8") as f:
                        f.write(t)
                    voice_pages.append(i)
            texts_for_avatar = [t if (i in avatar_pages) else "" for i, t in enumerate(texts, 1)]
        else:
            texts_for_avatar = texts
        with open(os.path.join(task["outdir"], "heygen_script.txt"), "w", encoding="utf-8") as f:
            f.write("\n\n".join(t for t in texts_for_avatar if t))
        texts = texts_for_avatar
        # 헤이젠은 한 영상에 대본 5000자까지만 받아요 → 쪽 경계에서 4900자 안쪽으로 나눠 여러 영상으로 만듦
        chunks, cur, cur_len, first = [], [], 0, 1
        for i, t in enumerate(texts):
            L = len(t) + 2
            if cur and cur_len + L > 4900:
                chunks.append((first, i, cur))
                cur, cur_len, first = [], 0, i + 1
            if t:
                cur.append(t)
            cur_len += L
        chunks.append((first, len(texts), cur))
        chunk_info = []
        for n, (a, b, ts) in enumerate(chunks, 1):
            fn = "heygen_script_%d.txt" % n
            with open(os.path.join(task["outdir"], fn), "w", encoding="utf-8") as f:
                f.write("\n\n".join(ts))
            chunk_info.append({"file": fn, "pages": [a, b], "chars": sum(len(x) for x in ts)})
        for s in task["plan"]["steps"]:
            s.pop("chunkVideos", None)
            s.pop("chunkDone", None)
        task["lecture"] = {"pdf": os.path.basename(step.get("pdfPath") or step["pdf"]), "pages": len(pages), "avatar_pages": avatar_pages,
                           "chunks": chunk_info if len(chunk_info) > 1 else [],
                           "mode": "split" if split else "full", "voice_pages": voice_pages, "pron_check": pron}
        store.log("강의 영상 준비: %s 쪽 %d, 아바타 쪽 %s%s" % (task["id"], len(pages), avatar_pages,
                                                         " (아바타 쪽만 영상, 나머지 %d쪽은 목소리만)" % len(voice_pages) if split else ""))

    PRON_FILE = "발음_확인표_pronunciation_check.md"

    def _pronunciation_check(self, task, pages, originals):
        """숫자·영어·정해 둔 발음처럼 소리가 바뀌는 문장을 모두 모아 '발음 확인표'를 만듦.
        숫자가 그대로 남아 있으면(틀리게 읽힐 수 있음) 영상을 만들지 않고 멈춤 — 교수님 결정: 발음은 절대 틀리면 안 됨"""
        pron = self._hg(task).get("pronounce") or {}
        left = []
        rows = []
        split_s = lambda x: [z.strip() for z in re.split(r"(?<=[.?!。])\s+", x) if z.strip()]
        for p, orig in zip(pages, originals):
            spoken = str(p.get("text") or "")
            if re.search(r"\d", spoken):
                left.append("%s쪽: %s" % (p["page"], re.sub(r"\s+", " ", spoken)[:60]))
            o_s, s_s = split_s(orig), split_s(spoken)
            pairs = list(zip(o_s, s_s)) if len(o_s) == len(s_s) else [(orig, spoken)]
            for o, sp_ in pairs:
                plain = sp_
                for v in pron.values():
                    plain = plain.replace(v, "")
                if o.strip() != sp_.strip() or re.search(r"[A-Za-z]", plain):
                    rows.append((p["page"], "아바타" if p.get("avatar") else "목소리", o.strip(), sp_.strip()))
        if left:
            raise RunnerError("영상에서 읽을 글에 숫자가 그대로 남아 있어 틀리게 읽힐 수 있어요. 숫자를 한글 소리로 풀어 쓴 뒤 다시 시도해 주세요: " + " / ".join(left[:5]), "")
        out = ["# 발음 확인표", "", "영상에서 소리 내어 읽을 때 글자와 소리가 달라지는 문장을 모두 모았어요(숫자·영어·교수님이 정한 발음).",
               "‘읽는 소리’ 칸이 맞는지 확인해 주세요. 자막과 슬라이드에는 ‘원래 글’이 그대로 나와요.", ""]
        if pron:
            out.append("정해 둔 발음: " + ", ".join("%s → %s" % kv for kv in pron.items()))
            out.append("")
        out += ["| 쪽 | 방식 | 원래 글 | 읽는 소리 |", "|---|---|---|---|"]
        cell = lambda x: x.replace("|", "／").replace("\n", " ")
        out += ["| %s | %s | %s | %s |" % (pg, kind, cell(o), cell(sp_)) for pg, kind, o, sp_ in rows] or ["| - | - | 바뀌는 문장이 없어요 | - |"]
        with open(os.path.join(task["outdir"], self.PRON_FILE), "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        return {"file": self.PRON_FILE, "count": len(rows)}

    def _hg(self, task):
        """헤이젠 설정. 프로젝트(주제)마다 아바타를 정해 둘 수 있고(projects.json 의 heygen), 일마다 정하면(task["heygen"]) 그 값이 가장 우선"""
        proj = next((p for p in self.projects() if p["id"] == self.task_project(task)), {}) if task else {}
        return {**self.settings.get("heygen", {}), **(proj.get("heygen") or {}), **((task or {}).get("heygen") or {})}

    def _video_title(self, task, step):
        """영상 파일 이름 앞부분. 한 일에 헤이젠 영상이 여럿이면(2차시·3차시 등) 단계마다 다른 이름을 써서 서로 덮어쓰지 않게 함"""
        safe = lambda x: re.sub(r"[^0-9A-Za-z가-힣]+", "_", x).strip("_")
        steps = (task.get("plan") or {}).get("steps", [])
        hsteps = [s for s in steps if s.get("heygen")]
        if len(hsteps) <= 1:
            return safe(task["title"]) or "video"
        def label(s):
            return safe(re.sub(r"헤이젠(에서)?|영상|만들기|제작", " ", s["title"]))
        lab = label(step)
        if not lab or [label(s) for s in hsteps].count(lab) > 1:
            lab = "%s_%d번째" % (safe(task["title"]) or "video", hsteps.index(step) + 1)
        return lab

    # ---- 🔎 영상 미리 점검 (11-13, 교수님 요청: 오류는 크레딧 쓰기 전에 잡기)
    KO_NUM = {1: ("일", "첫"), 2: ("이", "두"), 3: ("삼", "세"), 4: ("사", "네"), 5: ("오", "다섯"), 6: ("육", "여섯"),
              7: ("칠", "일곱"), 8: ("팔", "여덟"), 9: ("구", "아홉"), 10: ("십", "열"), 11: ("십일", "열한"), 12: ("십이", "열두"),
              13: ("십삼", "열세"), 14: ("십사", "열네"), 15: ("십오", "열다섯")}

    def _preflight(self, task, step):
        """문제가 있으면 쉬운 말로 이유를, 괜찮으면 None. 결과는 일 기록(events)에 남김"""
        base = task["outdir"]
        notes = []
        # ① 대본 짝: 쪽 수 = 슬라이드 수, 이 장(차시) 대본인지
        sp = os.path.join(base, "heygen_scenes.json")
        sd = os.path.join(base, "_slides")
        if os.path.exists(sp) and os.path.isdir(sd):
            try:
                pages = sorted(json.load(open(sp, encoding="utf-8"))["pages"], key=lambda x: int(x["page"]))
            except (ValueError, KeyError, TypeError):
                return "쪽마다 나눈 대본 파일(heygen_scenes.json)의 모양이 맞지 않아요."
            pngs = sorted(f for f in os.listdir(sd) if f.endswith(".png"))
            if len(pages) != len(pngs):
                return "대본 쪽 수(%d)와 슬라이드 쪽 수(%d)가 달라요." % (len(pages), len(pngs))
            m = re.search(r"(\d+)\s*(장|차시|편|회차?)", step.get("title", ""))
            if m and len([x for x in task["plan"]["steps"] if x.get("heygen")]) > 1:
                n, unit = int(m.group(1)), m.group(2)
                head = " ".join(str(p.get("text") or "") for p in pages[:2])
                for f in pngs[:2]:
                    tp = os.path.join(sd, f[:-4] + ".txt")
                    if os.path.exists(tp):
                        head += " " + open(tp, encoding="utf-8", errors="ignore").read()[:800]
                head = re.sub(r"\s+", "", head)
                han, nat = self.KO_NUM.get(n, ("", ""))
                pats = [r"(?<!\d)%d%s" % (n, unit[0]), r"제%d" % n]
                if han:
                    pats += ["제%s%s" % (han, unit[0]), "%s번째%s" % (nat, unit[0]), "%s%s" % (han, unit) if unit != "장" else "제%s장" % han]
                anyn = re.search(r"(?<!\d)\d+(장|차시|편|회)|[가-힣]{1,3}번째(장|차시|편|시간)|제[일이삼사오육칠팔구십]+(장|차시)", head)
                if anyn and not any(re.search(pt, head) for pt in pats):
                    return "「%s」인데 대본·슬라이드 첫머리가 다른 %s 내용 같아요(‘%s’). 대본 정리를 다시 해야 해요." % (step["title"], unit, anyn.group(0))
                notes.append("대본 짝 맞음(%d%s)" % (n, unit))
            else:
                notes.append("대본 쪽 수 맞음(%d쪽)" % len(pages))
            # ③ 합치기 10초 미리 해 보기 (가짜 아바타로, 크레딧 없음)
            try:
                self._compose_dryrun(base, [os.path.join(sd, f) for f in pngs[:3]])
                notes.append("합치기 10초 시험 통과")
            except Exception as ex:      # noqa: BLE001
                return "슬라이드와 아바타 합치기 시험이 실패했어요: %s" % str(ex)[-200:]
        # ② 목소리 한 마디 시험 (같은 목소리는 6시간에 한 번만)
        hg = self._hg(task)
        vid = hg.get("voice_id")
        if vid and not self.settings.get("fake") and time.time() > self.tts_blocked_until:
            ok = getattr(self, "voice_ok", {})
            self.voice_ok = ok
            if ok.get(vid, 0) < time.time():
                tp = os.path.join(base, "_voice_test.txt")
                with open(tp, "w", encoding="utf-8") as f:
                    f.write("안녕하세요.")
                try:
                    got = self.runner.tts_batch([(1, tp)], vid, base)
                    v = str(got.get(1, ""))
                except Exception as ex:      # noqa: BLE001
                    v = "error: %s" % ex
                if v.startswith("https://"):
                    ok[vid] = time.time() + 6 * 3600
                    notes.append("목소리 시험 통과(%s)" % hg.get("voice_name", vid))
                elif self._voice_backup() and TTS_BLOCKED.search(v):
                    self.tts_blocked_until = time.time() + 6 * 3600
                    notes.append("헤이젠 목소리가 막혀 있어 목소리 쪽은 힉스필드로 만들어요")
                else:
                    return "헤이젠 목소리 시험이 실패했어요: %s" % v[:160]
        task.setdefault("events", []).append({"ts": store.now(), "text": "🔎 미리 점검 통과 — " + ", ".join(notes)})
        return None

    def _compose_dryrun(self, base, slides):
        """가짜 아바타(색 화면+소리)로 슬라이드 3쪽을 10초 합쳐 봄. 실패하면 예외"""
        ff = lecture_compose.find_ffmpeg()
        if not ff:
            raise RuntimeError("영상 합성 도구(ffmpeg)가 없어요")
        if not slides:
            raise RuntimeError("슬라이드 그림이 없어요")
        d = os.path.join(base, "_미리점검")
        os.makedirs(d, exist_ok=True)
        av, mk = os.path.join(d, "av.mp4"), os.path.join(d, "mask.mp4")
        for args, out in ((["-f", "lavfi", "-i", "color=c=gray:s=1280x720:r=25:d=10", "-f", "lavfi", "-i", "sine=f=220:d=10", "-c:a", "aac", "-shortest"], av),
                          (["-f", "lavfi", "-i", "color=c=black:s=1280x720:r=25:d=10", "-vf", "drawbox=x=400:y=60:w=480:h=660:color=white:t=fill"], mk)):
            r = subprocess.run([ff, "-y", "-v", "error"] + args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", out], capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError(r.stderr[-200:])
        n = len(slides)
        starts = [i * 10.0 / n for i in range(n)]
        lecture_compose.compose(slides, av, starts, [1, n], os.path.join(d, "out.mp4"), mask_video=mk,
                                bbox={"x": 0.0, "y": 0.08, "w": 0.998, "h": 0.92})   # 일부러 넓은 범위(11-13 오류)도 견디는지
        shutil.rmtree(d, ignore_errors=True)

    def _compose_lecture(self, tid, idx):
        """헤이젠 아바타 영상 + 슬라이드 → 최종 강의 영상 (헤이젠 크레딧은 더 들지 않아요)"""
        task = self.get_task(tid)
        if not task or not task.get("plan"):
            return
        step = task["plan"]["steps"][idx]
        name = step["employee"]
        try:
            hg = self._hg(task)
            base = task["outdir"]
            slides = sorted(os.path.join(base, "_slides", f) for f in os.listdir(os.path.join(base, "_slides")) if f.endswith(".png"))
            pages = sorted(json.load(open(os.path.join(base, "heygen_scenes.json"), encoding="utf-8"))["pages"], key=lambda x: int(x["page"]))
            avatar = os.path.join(base, step["avatarVideo"])
            srt = avatar[:-4] + "_자막.srt"
            cues = lecture_compose.parse_srt(srt) if os.path.exists(srt) else []
            total = lecture_compose.video_duration(avatar)
            title = self._video_title(task, step)
            final = os.path.join(base, "%s_강의영상_lecture.mp4" % title)
            if task["lecture"].get("mode") == "split":
                # 아바타 영상은 아바타 쪽 글만 읽었음 → 쪽마다 잘라서 목소리만 쪽과 순서대로 이어 붙인 바탕 영상을 만듦
                av_pages = task["lecture"]["avatar_pages"]
                av_texts = [str(p.get("text") or "") for p in pages if int(p["page"]) in av_pages]
                av_starts = lecture_compose.align_pages(cues, av_texts, total)
                ends = av_starts[1:] + [total]
                if any(e - b < 0.8 for b, e in zip(av_starts, ends)):      # 자막 맞추기가 어긋나면 글 길이 비율로 나눔
                    lens = [max(1, len(t)) for t in av_texts]
                    av_starts, acc0 = [], 0.0
                    for L in lens:
                        av_starts.append(total * acc0 / sum(lens))
                        acc0 += L
                segs = []
                for p in pages:
                    pg = int(p["page"])
                    mp3 = os.path.join(base, "_voice", "page_%03d.mp3" % pg)
                    if pg in av_pages:
                        k = av_pages.index(pg)
                        segs.append({"kind": "avatar", "src": avatar, "start": av_starts[k],
                                     "end": av_starts[k + 1] if k + 1 < len(av_starts) else total})
                    elif os.path.exists(mp3):
                        segs.append({"kind": "voice", "src": mp3})
                    else:
                        segs.append({"kind": "silent", "seconds": 2.5})
                track = os.path.join(base, "_바탕영상.mp4")
                durs = lecture_compose.build_split_track(segs, track, lecture_compose.video_size(avatar), log=store.log)
                starts, acc = [], 0.0
                for d in durs:
                    starts.append(acc)
                    acc += d
                avatar, total = track, acc
                lecture_compose.write_page_srt([ko_speech.undo_pronounce(str(p.get("text") or ""), self._hg(task).get("pronounce")) for p in pages], starts, durs, final[:-4] + "_자막.srt")
            else:
                starts = lecture_compose.align_pages(cues, [str(p.get("text") or "") for p in pages], total)
            try:     # 나중에 다시 합칠 수 있게 이 영상의 쪽 나눔을 따로 보관 (다음 차시가 덮어써도 남도록)
                with open(os.path.join(base, "_장면_%s.json" % title), "w", encoding="utf-8") as f:
                    json.dump({"pages": pages, "lecture": task.get("lecture"), "avatarVideo": step["avatarVideo"]}, f, ensure_ascii=False, indent=1)
            except OSError as ex:
                store.log("쪽 나눔 보관 실패: %r" % (ex,))
            # 배경 없이 상반신만: 아바타가 나오는 구간에서만 사람을 오려 냄 (실패하면 네모난 창으로 대신함)
            mask_video = bbox = None
            try:
                tool = self._cutout_tool()
                mask_video = os.path.join(base, "_mask.mp4")
                spans = lecture_compose.avatar_spans(starts, total, task["lecture"]["avatar_pages"])
                bbox = lecture_compose.build_cutout(avatar, mask_video, os.path.join(base, "_mask_bbox.json"), spans, tool, log=store.log)
            except Exception as ex:
                store.log("사람 오리기 실패, 네모난 창으로 대신해요: %r" % (ex,))
                mask_video = bbox = None
            info = lecture_compose.compose(slides, avatar, starts, task["lecture"]["avatar_pages"], final,
                                           avatar_scale=hg.get("avatar_scale", 0.26), first_scale=hg.get("first_scale", 0.38),
                                           mask_video=mask_video, bbox=bbox,
                                           avatar_height=hg.get("avatar_height", 0.46), first_height=hg.get("first_height", 0.62),
                                           upper_body=hg.get("upper_body", 0.80), log=store.log)   # 허리까지 보이게 (교수님 선택 2026-11-09)
            fresh = self.get_task(tid)
            st = fresh["plan"]["steps"][idx]
            st["status"] = "done"
            st["finished"] = store.now()
            st["files"] = sorted(set(st.get("files", []) + [os.path.relpath(final, base)]))
            self.rendering.pop(name, None)
            self._bump(name, st.get("revisions", 0) == 0)
            self._save_task(fresh)
            self.notice = ""
            self._clear_live(name, st["title"] + " 완료")
            store.log("강의 영상 합치기 끝: %s %.0f초" % (tid, info["seconds"]))
            self.q.put(("next", tid))
        except Exception as ex:
            store.log("합치기 실패 %s: %r" % (tid, ex))
            self.rendering.pop(name, None)
            fresh = self.get_task(tid) or task
            fresh["status"] = "error"
            fresh["error"] = "슬라이드와 아바타를 합치지 못했어요: %s (헤이젠 영상은 이미 받아 두었어요. 「다시 시도」를 누르면 합치기만 다시 해요)" % ex
            self.notice = fresh["error"]
            self._save_task(fresh)

    # ---- 헤이젠(영상 만들기)
    def _collect_downloads(self, task, since):
        """Chrome 이 내려받은 그림(다운로드 폴더)을 이 일의 작업 폴더로 복사함"""
        dl = os.path.expanduser("~/Downloads")
        got = []
        try:
            names = sorted(os.listdir(dl), key=lambda f: os.path.getmtime(os.path.join(dl, f)))
        except OSError:
            return got      # 서버는 다운로드 폴더를 못 읽어요(맥 보호). 이미지 담당이 직접 복사해요.
        n = len([f for f in os.listdir(task["outdir"]) if f.startswith("그림_")])
        for f in names:
            p = os.path.join(dl, f)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and os.path.getmtime(p) >= since:
                n += 1
                dest = os.path.join(task["outdir"], "그림_%02d%s" % (n, os.path.splitext(f)[1].lower()))
                shutil.copy2(p, dest)
                got.append(os.path.basename(dest))
        store.log("그림 %d장 모음: %s %s" % (len(got), task["id"], got))
        return got

    def _script_path(self, task):
        p = os.path.join(task["outdir"], "heygen_script.txt")
        return p if os.path.exists(p) else None

    def _request_credit_approval(self, task, idx, failure=""):
        """영상 만들기 직전의 크레딧 결재: 예상 길이·예상 크레딧·남은 크레딧을 보여 줌"""
        step = task["plan"]["steps"][idx]
        hg = self._hg(task)
        sp = self._script_path(task)
        raw_script = open(sp, encoding="utf-8").read() if sp else ""
        chars = len(re.sub(r"\s+", "", raw_script))
        mins = chars / float(hg.get("chars_per_min", 330) or 330)
        length = ("약 %d초" % round(mins * 60)) if mins < 1.5 else ("약 %.0f분" % mins)
        preview = re.sub(r"\s+", " ", raw_script).strip()[:110]
        lo, hi = hg.get("credits_per_min_low", 20), hg.get("credits_per_min_high", 31)
        remaining = None
        try:
            remaining = self.runner.credits(task["outdir"])
        except Exception as ex:
            store.log("남은 크레딧 확인 실패: %r" % (ex,))
        lines = []
        if failure:
            lines.append("⚠ 지난번 시도는 실패했어요: " + failure)
            lines.append("다시 만들려면 새로 결재를 받아야 해요.\n")
        lines += [
            "헤이젠에서 실제로 영상을 만들어요. 크레딧이 들어요.",
            "· 대본: heygen_script.txt (공백 빼고 약 %s자)" % format(chars, ",") + ("  — 헤이젠은 한 영상에 5,000자까지라서 %d개 영상으로 나눠 만들고 이어 붙여요 (크레딧은 총 길이만큼)" % len(task["lecture"]["chunks"]) if (task.get("lecture") or {}).get("chunks") else ""),
            "· 예상 영상 길이: " + length,
        ] + ([
            "· 강의자료: %s (%d쪽). 쪽마다 대본을 나눠 두었어요." % (task["lecture"]["pdf"], task["lecture"]["pages"]),
            "· 아바타가 나오는 쪽: %s (첫 쪽은 크게, 나머지는 오른쪽 아래에 작게)" % ", ".join(str(x) for x in task["lecture"]["avatar_pages"]),
            "· 헤이젠가 아바타 영상을 만들면, 사무실이 슬라이드 위에 합쳐서 최종 영상을 만들어요.",
        ] + ([
            "· 크레딧 아끼기: 아바타 영상은 위 아바타 쪽의 글만 읽어요(예상 길이·크레딧도 그만큼만 셌어요).",
            "· 나머지 %d쪽은 교수님 목소리만 만들어 슬라이드에 입혀요. 목소리는 1분에 약 2~3 크레딧이에요 (아바타 영상의 약 10분의 1)." % len(task["lecture"].get("voice_pages", [])),
        ] + ([
            "· 🔁 대비: 헤이젠 목소리가 막혀 있으면(401) 그 쪽들은 **힉스필드**에서 교수님 목소리를 복제해 대신 만들어요. "
            "힉스필드 약 %d 크레딧 (한 쪽에 약 3). 이 결재를 승인하면 이 대비도 함께 승인한 것이에요." % (3 * len(task["lecture"].get("voice_pages", [])) + 10),
        ] if (task.get("lecture") or {}).get("mode") == "split" and self._voice_backup() else []) + [
        ] if (task.get("lecture") or {}).get("mode") == "split" else []) + [
        ] if task.get("lecture") else []) + [
            ("· 발음 확인표: 숫자·영어·이솝처럼 소리가 바뀌는 문장 %d곳을 모두 적어 두었어요 (%s). 승인 전에 꼭 확인해 주세요." % ((task.get("lecture") or {}).get("pron_check", {}).get("count", 0), self.PRON_FILE)
             if (task.get("lecture") or {}).get("pron_check") else "· 발음 확인표: 없음"),
            "· 읽을 내용(앞부분): “%s%s”" % (preview, "…" if len(re.sub(r"\s+", " ", raw_script).strip()) > 110 else ""),
            "· 예상 크레딧: 약 %s ~ %s (분당 %s~%s 크레딧으로 어림한 값이에요. 정확한 값은 헤이젠 요금 기준으로 정해져요)" % (
                format(int(mins * lo), ","), format(int(mins * hi), ","), lo, hi),
            "· 남은 크레딧: %s" % (format(remaining, ",") + "개" if remaining is not None else "확인하지 못했어요"),
            "· 아바타: %s / 목소리: %s" % (hg.get("avatar_name") or "정해 둔 아바타", hg.get("voice_name") or "정해 둔 목소리"),
            ("· 화질: 최종 영상 1080p (아바타는 구석에 작게 나와서 %s로 만들어요), 화면 비율 %s" % (hg.get("avatar_resolution", "720p"), hg.get("aspect_ratio", "16:9"))
             if task.get("lecture") else "· 화질: %s, 화면 비율 %s" % (hg.get("resolution", "1080p"), hg.get("aspect_ratio", "16:9"))),
            "",
            "승인하면 딱 한 번만 만들어요. 실패하거나 다시 만들 때는 새 결재를 받아요.",
            "대본을 더 고치고 싶으면 「수정 요청」에 이유를 적어 주세요.",
        ]
        step["status"] = "waiting"
        step["approvedToRun"] = False
        self._save_task(task)
        self._add_approval(taskId=task["id"], kind="credit", owner=step["employee"],
                           title="헤이젠 영상 만들기 (크레딧): " + task["title"], detail="\n".join(lines),
                           files=(["heygen_script.txt"] if sp else []) + ([self.PRON_FILE] if (task.get("lecture") or {}).get("pron_check") else []), stepIndex=idx)
        self._clear_live(step["employee"])

    def _voice_backup(self):
        """헤이젠 목소리가 막혔을 때 쓰는 힉스필드 대비 (settings.heygen.voice_backup.ref_media = 교수님 목소리 녹음)"""
        bk = (self.settings.get("heygen") or {}).get("voice_backup") or {}
        return bk if bk.get("ref_media") and bk.get("engine", "higgs") == "higgs" else None

    def _make_voices(self, task, step, emp, name):
        """아바타가 안 나오는 쪽의 목소리를 헤이젠에서 만들어 _voice/page_XXX.mp3 로 받아 둠 (이미 받은 쪽은 건너뜀)"""
        vdir = os.path.join(task["outdir"], "_voice")
        todo = [(pg, os.path.join(vdir, "page_%03d.txt" % pg)) for pg in task["lecture"].get("voice_pages", [])
                if not os.path.exists(os.path.join(vdir, "page_%03d.mp3" % pg))]
        if not todo:
            return
        hg = self._hg(task)
        urls = {}
        if time.time() > self.tts_blocked_until:
            self._set_live(name, "작업 중", "목소리만 만드는 중 (%d쪽)" % len(todo), task=task["title"], step=step["title"])
            urls = self.runner.tts_paced(todo, hg.get("voice_id"), task["outdir"], hg.get("brand_glossary_id"))
        blocked = time.time() <= self.tts_blocked_until or any(
            str(v).startswith("error") and TTS_BLOCKED.search(str(v)) for v in urls.values())
        bk = self._voice_backup()
        if blocked and bk and step.get("approvedToRun"):
            if time.time() > self.tts_blocked_until:
                self.tts_blocked_until = time.time() + 6 * 3600     # 6시간은 헤이젠 목소리를 다시 부르지 않음
                store.log("헤이젠 목소리 막힘(401·호환 안 됨) → 6시간 동안 힉스필드 대비 길 사용")
            rest = [(pg, fp) for pg, fp in todo if not str(urls.get(pg, "")).startswith("https://")]
            self._set_live(name, "작업 중", "헤이젠 목소리가 막혀 힉스필드로 대신 만드는 중 (%d쪽)" % len(rest), task=task["title"], step=step["title"])
            got = self.runner.higgs_tts(rest, bk["ref_media"], task["outdir"])
            for pg, u in got.items():
                urls[pg] = u
            task.setdefault("events", []).append({"ts": store.now(), "text": "헤이젠 목소리가 막혀서 %d쪽을 힉스필드 복제 목소리로 만들었어요." % len(got)})
        bad = []
        for pg, _fp in todo:
            u = urls.get(pg, "")
            if not u.startswith("https://") and not (self.settings.get("fake") and u):
                bad.append("%d쪽(%s)" % (pg, (u or "결과 없음")[:60]))
                continue
            try:
                self.runner.download(u, os.path.join(vdir, "page_%03d.mp3" % pg))
            except Exception as ex:
                bad.append("%d쪽(내려받기 실패: %s)" % (pg, ex))
        if bad:
            raise RunnerError("목소리를 다 만들지 못했어요: " + ", ".join(bad[:5]) + " — 다시 시도하면 못 만든 쪽만 다시 만들어요. (아바타 영상은 아직 만들지 않았어요)", "")
        store.log("목소리만 만들기 끝: %s %d쪽" % (task["id"], len(todo)))

    def _after_heygen_submit(self, task, idx, step, name, result, started):
        job = {}
        jp = os.path.join(task["outdir"], "video_job.json")
        if os.path.exists(jp):
            try:
                job = json.load(open(jp, encoding="utf-8"))
            except ValueError:
                job = {}
        vid = str(job.get("video_id") or "").strip()
        if not vid:
            m = re.search(r"\b[0-9A-Za-z]{24,40}\b", result or "")
            vid = m.group(0) if m else ""
        if not vid:
            reason = job.get("error") or (result or "")[:200] or "영상 번호를 받지 못했어요"
            self._heygen_failed(task, idx, "헤이젠에서 영상 만들기를 시작하지 못했어요: " + str(reason))
            return
        step["videoId"] = vid
        step["status"] = "rendering"
        step["renderStarted"] = store.now()
        step["finished"] = ""
        step["pollErrors"] = 0
        step["files"] = self._changed_files(task, started)
        self._save_task(task)
        self._clear_live(name)
        self.rendering[name] = {"task": task["title"], "taskId": task["id"], "step": step["title"], "since": time.time()}
        store.log("헤이젠 영상 제출: %s video_id=%s" % (task["id"], vid))
        self._schedule_poll(task["id"], idx, self.settings.get("poll_first", 60))

    def _submit_chunks(self, task, idx, step, emp, name):
        """긴 강의: 대본을 조각으로 나눠 헤이젠 영상을 조각마다 한 번씩 만듦 (이미 만든 조각은 다시 만들지 않음)"""
        hg2 = dict(self._hg(task))
        hg2["resolution"] = hg2.get("avatar_resolution", "720p")
        chunks = task["lecture"]["chunks"]
        ids = list(step.get("chunkVideos") or [])
        ids += [""] * (len(chunks) - len(ids))
        started = time.time() - 1
        jp = os.path.join(task["outdir"], "video_job.json")
        for i, ch in enumerate(chunks):
            if ids[i]:
                continue
            if os.path.exists(jp):
                os.remove(jp)
            title = "%s (%d/%d)" % (task["title"], i + 1, len(chunks))
            prompt = prompts.heygen_prompt(task, step, hg2, os.path.join(task["outdir"], ch["file"]), title, store.get_rules(name))
            result = self.runner.step(emp, task, step, prompt, self.settings["input_dirs"], lambda t: self._set_live(name, "작업 중", t))
            job = {}
            if os.path.exists(jp):
                try:
                    job = json.load(open(jp, encoding="utf-8"))
                except ValueError:
                    job = {}
            vid = str(job.get("video_id") or "").strip()
            if not vid:
                m = re.search(r"\b[0-9A-Za-z]{24,40}\b", result or "")
                vid = m.group(0) if m else ""
            step["chunkVideos"] = ids
            self._save_task(task)
            if not vid:
                reason = job.get("error") or (result or "")[:200] or "영상 번호를 받지 못했어요"
                self._heygen_failed(task, idx, "헤이젠에서 영상(%d/%d) 만들기를 시작하지 못했어요: %s" % (i + 1, len(chunks), reason))
                return
            ids[i] = vid
            step["chunkVideos"] = ids
            self._save_task(task)
            store.log("헤이젠 영상 제출: %s (%d/%d) video_id=%s" % (task["id"], i + 1, len(chunks), vid))
        step["videoId"] = ids[0]
        step["status"] = "rendering"
        step["renderStarted"] = store.now()
        step["finished"] = ""
        step["pollErrors"] = 0
        step["chunkDone"] = [False] * len(ids)
        step["files"] = self._changed_files(task, started)
        self._save_task(task)
        self._clear_live(name)
        self.rendering[name] = {"task": task["title"], "taskId": task["id"], "step": step["title"], "since": time.time()}
        self._schedule_poll(task["id"], idx, self.settings.get("poll_first", 60))

    def _poll_chunks(self, task, idx, step):
        tid, name = task["id"], step["employee"]
        ids = step["chunkVideos"]
        done = step.get("chunkDone") or [False] * len(ids)
        title = self._video_title(task, step)
        part = lambda i: os.path.join(task["outdir"], "_part_%s_%d.mp4" % (title, i + 1))
        for i, vid in enumerate(ids):
            if done[i]:
                continue
            try:
                res = self.runner.video_status(vid, task["outdir"])
            except RunnerError as ex:
                step["pollErrors"] = step.get("pollErrors", 0) + 1
                self._save_task(task)
                store.log("헤이젠 상태 확인 실패(%d): %s" % (step["pollErrors"], ex.friendly))
                if step["pollErrors"] >= 8:
                    self._heygen_failed(task, idx, "헤이젠 영상 상태를 계속 확인하지 못했어요. 헤이젠 화면에서 직접 확인해 주세요. (영상 번호 %s)" % vid)
                else:
                    self._schedule_poll(tid, idx, 120)
                return
            status = str(res.get("status", "")).lower()
            if status == "failed":
                ids[i] = ""      # 실패한 조각만 새 결재 뒤에 다시 만듦
                step["chunkVideos"], step["chunkDone"] = ids, done
                self._save_task(task)
                self._heygen_failed(task, idx, "헤이젠에서 영상(%d/%d) 만들기가 실패했어요: %s" % (i + 1, len(ids), res.get("failure") or "이유를 알 수 없어요"))
                return
            if status == "completed" and res.get("video_url"):
                try:
                    self.runner.download(res["video_url"], part(i))
                    if res.get("caption_url"):
                        try:
                            self.runner.download(res["caption_url"], part(i)[:-4] + ".srt")
                        except Exception as ex:
                            store.log("자막 내려받기 실패: %r" % (ex,))
                except Exception as ex:
                    step["pollErrors"] = step.get("pollErrors", 0) + 1
                    self._save_task(task)
                    store.log("영상 내려받기 실패(%d): %r" % (step["pollErrors"], ex))
                    self._schedule_poll(tid, idx, 90)
                    return
                done[i] = True
                step["chunkDone"] = done
                self._save_task(task)
                store.log("헤이젠 영상 완성·내려받기: %s (%d/%d)" % (tid, i + 1, len(ids)))
        if not all(done):
            self._schedule_poll(tid, idx, self.settings.get("poll_every", 90))
            return
        dest = os.path.join(task["outdir"], "%s_영상_video.mp4" % title)
        try:
            parts = [part(i) for i in range(len(ids))]
            lecture_compose.concat_parts(parts, dest)
            lecture_compose.merge_srts([p[:-4] + ".srt" for p in parts], [lecture_compose.video_duration(p) for p in parts], dest[:-4] + "_자막.srt")
        except Exception as ex:
            store.log("아바타 영상 이어 붙이기 실패: %r" % (ex,))
            self._heygen_failed(task, idx, "조각 영상을 이어 붙이지 못했어요: %s (조각 영상은 작업 폴더에 남아 있어요)" % ex)
            return
        self.notice = ""
        step["files"] = sorted(set(step.get("files", []) + [os.path.relpath(p, task["outdir"]) for p in (dest, dest[:-4] + "_자막.srt") if os.path.exists(p)]))
        step["status"] = "composing"
        step["avatarVideo"] = os.path.basename(dest)
        self.rendering[name] = {"task": task["title"], "taskId": tid, "step": step["title"], "since": time.time(), "kind": "compose"}
        self._save_task(task)
        threading.Thread(target=self._compose_lecture, args=(tid, idx), daemon=True).start()

    def _heygen_failed(self, task, idx, message):
        """실패하면 같은 영상을 멋대로 다시 만들지 않고, 새 크레딧 결재를 받음"""
        step = task["plan"]["steps"][idx]
        store.log("헤이젠 실패 %s: %s" % (task["id"], message))
        self.rendering.pop(step["employee"], None)
        self._clear_live(step["employee"])
        self.notice = message
        self._request_credit_approval(task, idx, failure=message)

    def _schedule_poll(self, tid, idx, delay=90):
        t = threading.Timer(delay, lambda: self.q.put(("poll", tid, idx)))
        t.daemon = True
        t.start()

    def _do_poll(self, tid, idx):
        task = self.get_task(tid)
        if not task or not task.get("plan"):
            return
        step = task["plan"]["steps"][idx]
        if step.get("status") != "rendering":
            return
        name = step["employee"]
        if task["status"] == "paused":      # 멈춘 동안에는 확인만 미룸 (헤이젠는 계속 만드는 중)
            self._schedule_poll(tid, idx, 120)
            return
        if task["status"] == "cancelled":
            self.rendering.pop(name, None)
            return
        try:
            started = time.mktime(time.strptime(step["renderStarted"], "%Y-%m-%d %H:%M:%S"))
        except (KeyError, ValueError):
            started = time.time()
        if time.time() - started > 3 * 3600:
            self._heygen_failed(task, idx, "3시간이 지나도 영상이 완성되지 않았어요. 헤이젠 화면에서 직접 확인해 주세요. (영상 번호 %s)" % step.get("videoId"))
            return
        if step.get("chunkVideos"):
            return self._poll_chunks(task, idx, step)
        try:
            res = self.runner.video_status(step["videoId"], task["outdir"])
        except RunnerError as ex:
            step["pollErrors"] = step.get("pollErrors", 0) + 1
            self._save_task(task)
            store.log("헤이젠 상태 확인 실패(%d): %s" % (step["pollErrors"], ex.friendly))
            if step["pollErrors"] >= 8:
                self._heygen_failed(task, idx, "헤이젠 영상 상태를 계속 확인하지 못했어요. 헤이젠 화면에서 직접 확인해 주세요. (영상 번호 %s)" % step.get("videoId"))
            else:
                self._schedule_poll(tid, idx, 120)
            return
        status = str(res.get("status", "")).lower()
        if status == "failed":
            self._heygen_failed(task, idx, "헤이젠에서 영상 만들기가 실패했어요: " + str(res.get("failure") or "이유를 알 수 없어요"))
            return
        if status == "completed" and res.get("video_url"):
            title = self._video_title(task, step)
            dest = os.path.join(task["outdir"], "%s_영상_video.mp4" % title)
            try:
                self.runner.download(res["video_url"], dest)
                if res.get("caption_url"):
                    try:
                        self.runner.download(res["caption_url"], dest[:-4] + "_자막.srt")
                    except Exception as ex:
                        store.log("자막 내려받기 실패: %r" % (ex,))
            except Exception as ex:
                step["pollErrors"] = step.get("pollErrors", 0) + 1
                self._save_task(task)
                store.log("영상 내려받기 실패(%d): %r" % (step["pollErrors"], ex))
                self.notice = "영상은 완성됐는데 내려받지 못했어요. 잠시 뒤 다시 시도해요."
                if step["pollErrors"] >= 8:
                    self._heygen_failed(task, idx, "영상은 완성됐지만 내려받지 못했어요. 헤이젠 화면에서 직접 받아 주세요. (영상 번호 %s)" % step.get("videoId"))
                else:
                    self._schedule_poll(tid, idx, 90)
                return
            self.notice = ""
            step["files"] = sorted(set(step.get("files", []) + [os.path.relpath(p, task["outdir"]) for p in
                                   (dest, dest[:-4] + "_자막.srt") if os.path.exists(p)]))
            if task.get("lecture"):          # 슬라이드 위에 아바타를 합쳐야 최종 영상
                step["status"] = "composing"
                step["avatarVideo"] = os.path.basename(dest)
                self.rendering[name] = {"task": task["title"], "taskId": tid, "step": step["title"], "since": time.time(), "kind": "compose"}
                self._save_task(task)
                threading.Thread(target=self._compose_lecture, args=(tid, idx), daemon=True).start()
                return
            self.rendering.pop(name, None)
            step["status"] = "done"
            step["finished"] = store.now()
            self._bump(name, step.get("revisions", 0) == 0)
            self._save_task(task)
            self._clear_live(name, step["title"] + " 완료")
            self.q.put(("next", tid))
            return
        self._schedule_poll(tid, idx, self.settings.get("poll_every", 90))   # 아직 만드는 중

    def _changed_files(self, task, since):
        out = []
        base = task["outdir"]
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if not d.startswith("_")]
            for f in fn:
                p = os.path.join(dp, f)
                if not f.startswith(".") and os.path.getmtime(p) >= since:
                    out.append(os.path.relpath(p, base))
        return sorted(out)

    # ---- 멈추기 · 다시 시작 · 그만두기
    def _event(self, task, text):
        task.setdefault("events", []).append({"ts": store.now(), "text": text})

    def stop_task(self, tid):
        task = self.get_task(tid)
        if not task:
            raise ValueError("그런 일을 찾지 못했어요.")
        if task["status"] in ("done", "cancelled", "paused"):
            raise ValueError("이미 끝났거나 멈춘 일이에요.")
        prev = task["status"]
        task["pausedFrom"] = prev
        task["status"] = "paused"
        self._event(task, "교수님이 일을 멈췄어요")
        self._save_task(task, force=True)
        self.runner.abort(tid)
        names = [st["employee"] for st in (task.get("plan") or {}).get("steps", []) if st.get("status") == "running"]
        if prev == "planning":
            names.append("기획실장")
        for n in names:
            self._clear_live(n)
        store.log("멈춤: %s (이전 상태 %s)" % (tid, prev))
        return task

    def resume_task(self, tid):
        task = self.get_task(tid)
        if not task or task["status"] != "paused":
            raise ValueError("멈춘 일이 아니에요.")
        prev = task.get("pausedFrom") or "running"
        task["status"] = prev
        task["pausedFrom"] = None
        self._event(task, "다시 시작했어요")
        self._save_task(task, force=True)
        if prev == "planning":
            self.q.put(("plan", tid))
        elif prev == "running":
            self.q.put(("next", tid))
        store.log("다시 시작: %s (%s)" % (tid, prev))
        return task

    def restart_task(self, tid):
        """오래 소식이 없는 일: 지금 하던 단계를 끊고 같은 단계를 처음부터 다시 시켜요 (끝난 단계는 그대로)"""
        task = self.get_task(tid)
        if not task or task["status"] not in ("planning", "running"):
            raise ValueError("지금 진행 중인 일이 아니에요.")
        self.stop_task(tid)
        task = self.resume_task(tid)
        ev = task.get("events", [])
        if len(ev) >= 2 and ev[-2]["text"] == "교수님이 일을 멈췄어요" and ev[-1]["text"] == "다시 시작했어요":
            del ev[-2:]      # 멈춤·다시 시작 기록 대신 '다시 시킴' 한 줄만 남김
        self._event(task, "오래 소식이 없어서 지금 단계를 다시 시켰어요")
        self._save_task(task, force=True)
        store.log("다시 시킴: %s" % tid)
        return self.get_task(tid)

    def finish_task(self, tid):
        """멈춰 있지만 단계가 다 끝난 일을 '끝난 일'로 정리 (결과물은 그대로)"""
        task = self.get_task(tid)
        if not task:
            raise ValueError("그런 일을 찾지 못했어요.")
        steps = (task.get("plan") or {}).get("steps", [])
        if task["status"] not in ("paused", "error", "review") or not steps or any(s["status"] != "done" for s in steps):
            raise ValueError("아직 끝나지 않은 단계가 있어서 정리할 수 없어요.")
        for ap in self._approvals():
            if ap["taskId"] == tid and ap["status"] == "pending":
                ap["status"] = "approved"
                ap["reason"] = "교수님이 끝난 일로 정리"
                ap["decided"] = store.now()
                self._update_approval(ap)
        self._mark_done(task)
        self._event(task, "교수님이 끝난 일로 정리했어요")
        self._save_task(task, force=True)
        return task

    def cancel_task(self, tid):
        task = self.get_task(tid)
        if not task:
            raise ValueError("그런 일을 찾지 못했어요.")
        if task["status"] in ("done", "cancelled"):
            raise ValueError("이미 끝났거나 그만둔 일이에요.")
        steps = (task.get("plan") or {}).get("steps", [])
        rendering = [st for st in steps if st.get("status") == "rendering"]
        task["status"] = "cancelled"
        task["finished"] = store.now()
        self._event(task, "교수님이 일을 그만뒀어요")
        self._save_task(task, force=True)
        self.runner.abort(tid)
        for ap in self._approvals():
            if ap["taskId"] == tid and ap["status"] == "pending":
                ap["status"] = "cancelled"
                ap["decided"] = store.now()
                self._update_approval(ap)
        for st in steps:
            if st.get("status") == "running":
                self._clear_live(st["employee"])
        for st in rendering:
            self.rendering.pop(st["employee"], None)
        store.log("그만둠: %s" % tid)
        note = ""
        if rendering:
            note = "헤이젠에 이미 맡긴 영상은 멈출 수 없어요. 크레딧은 이미 쓰였을 수 있어요. 헤이젠 화면에서 확인해 주세요. (영상 번호 %s)" % rendering[0].get("videoId", "")
        return task, note

    def stop_all(self):
        n = 0
        for t in self._tasks():
            if t["status"] in ("planning", "running"):
                try:
                    self.stop_task(t["id"])
                    n += 1
                except ValueError:
                    pass
        return n

    # ---- 지금 뭐 해요?
    STATUS_KO = {"planning": "기획실장이 계획 세우는 중", "asking": "교수님 답변 기다림", "plan_review": "계획서 결재 대기",
                 "running": "직원들이 일하는 중", "review": "최종 보고 결재 대기", "done": "끝남",
                 "error": "멈춤(오류)", "paused": "교수님이 멈춤", "cancelled": "그만둠"}
    STEP_KO = {"pending": "차례를 기다림", "running": "작업 중", "waiting": "결재 기다림",
               "rendering": "헤이젠가 영상 만드는 중", "composing": "슬라이드와 아바타 합치는 중", "done": "끝남", "error": "오류"}

    def now_info(self, task):
        """이 일의 지금 상황: 일하는 직원, 방금 한 일, 줄 선 일, 최근 파일"""
        workers = []
        now = time.time()
        for name, live in list(self.live.items()):
            if live.get("taskId") == task["id"]:
                workers.append({"name": name, "step": live.get("step", ""), "text": live.get("text", ""),
                                "seconds": int(now - live.get("since", now)),
                                "recent": [{"t": t, "text": x} for t, x in list(self.activity.get(name, []))[-6:]][::-1]})
        for name, r in list(self.rendering.items()):
            if r.get("taskId") == task["id"]:
                workers.append({"name": name, "step": r.get("step", ""),
                                "text": "슬라이드와 아바타 합치는 중" if r.get("kind") == "compose" else "헤이젠가 영상 만드는 중",
                                "seconds": int(now - r["since"]), "recent": []})
        queued = sum(1 for j in list(self.q.queue) if len(j) > 1 and j[1] == task["id"])
        files = []
        base = task["outdir"]
        if os.path.isdir(base):
            for dp, dn, fn in os.walk(base):
                dn[:] = [d for d in dn if not d.startswith("_")]
                for f in fn:
                    if not f.startswith("."):
                        fp = os.path.join(dp, f)
                        files.append((os.path.getmtime(fp), os.path.relpath(fp, base)))
        files.sort(reverse=True)
        return {"workers": workers, "queued": queued,
                "files": [{"name": n, "t": time.strftime("%H:%M:%S", time.localtime(m))} for m, n in files[:5]]}

    def _snapshot(self, task):
        lines = ["일: %s (상태: %s)" % (task["title"], self.STATUS_KO.get(task["status"], task["status"]))]
        for i, st in enumerate((task.get("plan") or {}).get("steps", [])):
            lines.append("  %d단계 %s — %s : %s (시작 %s, 끝 %s)" % (i + 1, st["employee"], st["title"], self.STEP_KO.get(st["status"], st["status"]),
                                                               st.get("started") or "-", st.get("finished") or "-"))
        info = self.now_info(task)
        for w in info["workers"]:
            lines.append("지금 일하는 직원: %s / %s / %d분째 / 방금 한 일: %s" % (
                w["name"], w["step"], w["seconds"] // 60, ", ".join(r["text"] for r in w["recent"][:4]) or "기록 없음"))
        if not info["workers"]:
            lines.append("지금 이 일을 하는 직원은 없어요.")
        lines.append("이 일을 기다리는 작업 대기: %d건" % info["queued"])
        pend = [a["title"] for a in self._approvals() if a["taskId"] == task["id"] and a["status"] == "pending"]
        lines.append("결재 대기: " + (", ".join(pend) if pend else "없음"))
        lines.append("최근 만든 파일: " + (", ".join(f["name"] for f in info["files"]) or "없음"))
        lines.append("최근 시간 기록: " + " / ".join("%s %s" % (e["ts"][11:], e["text"]) for e in self.timeline(task)[-6:]))
        return "\n".join(lines)

    def ask_status(self, tid, question):
        question = (question or "").strip()
        task = self.get_task(tid)
        if not task:
            raise ValueError("그런 일을 찾지 못했어요.")
        if not question:
            raise ValueError("물어볼 내용을 적어 주세요.")
        try:
            ans = self.runner.answer(task, self._snapshot(task), question)
        except RunnerError as ex:
            raise ValueError(ex.friendly)
        except RunnerCancelled:
            raise ValueError("답변을 하다가 끊겼어요. 다시 물어봐 주세요.")
        fresh = self.get_task(tid) or task
        fresh["messages"].append({"role": "user", "text": question, "ts": store.now(), "kind": "status"})
        fresh["messages"].append({"role": "planner", "text": (ans or "").strip(), "ts": store.now(), "kind": "status"})
        self._save_task(fresh)
        return fresh

    def retry(self, tid):
        """오류로 멈춘 일을 다시 시작"""
        task = self.get_task(tid)
        if task and task["status"] == "cancelled" and task.get("replacedBy"):      # 다른 일로 대신해 정리한 일도 교수님이 원하시면 다시 살림
            task["status"] = "error"
        if not task or task["status"] != "error":
            raise ValueError("다시 시작할 수 있는 일이 아니에요.")
        task["error"] = ""
        self.notice = ""
        if task.get("plan"):
            task["status"] = "running"
            self._save_task(task)
            comp = [i for i, st in enumerate(task["plan"]["steps"]) if st.get("status") == "composing"]
            if comp:       # 합치다 멈춘 일은 헤이젠에 다시 맡기지 않고 합치기만 다시 함
                st = task["plan"]["steps"][comp[0]]
                self.rendering[st["employee"]] = {"task": task["title"], "taskId": tid, "step": st["title"], "since": time.time(), "kind": "compose"}
                threading.Thread(target=self._compose_lecture, args=(tid, comp[0]), daemon=True).start()
                return task
            self.q.put(("next", tid))
        else:
            task["status"] = "planning"
            self._save_task(task)
            self.q.put(("plan", tid))
        return task
