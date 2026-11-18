# -*- coding: utf-8 -*-
"""성서 40주간 사이트 제작 — 서버가 직접 실행하는 부분.

개발팀 '홈페이지' 직원의 두 가지 서버 실행 작업:
  · art    = 성화 그림 생성 (OpenAI, 돈이 드는 일 → 결재 후 실행)
  · deploy = 완성된 사이트를 Cloudflare Pages 에 올리기 (무료)

사무실 서버는 표준 라이브러리만 쓰므로, 그림 생성은 성서 40주간 폴더 안에
따로 만들어 둔 파이썬 환경(제작도구/pyenv)과 생성 스크립트를 '하위 프로세스'로 돌린다.
진행 상황은 images 폴더의 그림 개수를 세어 실시간으로 알려 준다.
"""
import os
import re
import time
import shutil
import subprocess
import tempfile


def cfg_defaults():
    proj = ""   # 나눔판: 개인 사이트 기능은 꺼 두었어요
    tools = os.path.join(proj, "제작도구")
    return {
        "project_dir": proj,
        "site_dir": os.path.join(proj, "홈페이지배포"),
        "images_dir": os.path.join(proj, "홈페이지배포", "images"),
        "gen_dir": os.path.join(proj, "도구_성화생성"),
        "gen_script": "generate_art.py",
        "venv_py": os.path.join(tools, "pyenv", "bin", "python"),
        "node_bin": os.path.join(tools, "node", "bin"),
        "wrangler": os.path.join(tools, "node", "bin", "wrangler"),
        "openai_key_file": os.path.join(tools, "openai_key.txt"),
        "project": "",
        "site_url": "",
        "price_per_image": 0.17,
        "total": 365,
    }


# ---- 요청 알아보기 (교수님이 성서 40주간 제작을 맡겼는지)
def is_request(text):
    return False   # 나눔판: 이 기능은 꺼 두었어요
    t = text or ""
    if "gpt" in t.lower():       # ChatGPT 화면으로 그리는 일은 이미지 담당이 맡음 (API 작업 아님)
        return False
    if "성서" not in t:
        return False
    if not ("40" in t or "사십" in t):
        return False
    # 그림(성화)은 이제 이미지 담당이 ChatGPT 로 그려요 → 기획실장이 계획을 짬.
    # 미리 짠 계획(돈 드는 OpenAI 그림 + 서버가 직접 배포)은 '사이트를 올려 달라'는 말에만 씀.
    kws = ["배포", "올려", "올리", "업로드"]
    return any(k in t for k in kws)


def wants_test(text):
    t = text or ""
    return any(k in t for k in ["테스트", "시험", "3장", "세 장", "샘플", "조금만", "먼저"])


# ---- 도우미
def count_art(cfg):
    d = cfg["images_dir"]
    try:      # 서버는 맥 보호 때문에 바탕화면 폴더를 못 읽을 수 있어요 → 0 으로 보고 계속
        if not os.path.isdir(d):
            return 0
        return len([f for f in os.listdir(d) if re.match(r"art_\d+\.(jpg|png)$", f)])
    except OSError:
        return 0


def read_key(cfg):
    p = cfg["openai_key_file"]
    if not os.path.exists(p):
        return ""
    try:
        m = re.search(r"sk-[A-Za-z0-9_-]{20,}", open(p, encoding="utf-8").read())
    except OSError:
        return ""
    return m.group(0) if m else ""


def ensure_venv(cfg):
    """성화 생성용 파이썬 환경이 없으면 만들어 둔다 (openai, pillow)."""
    if os.path.exists(cfg["venv_py"]):
        return True, ""
    base = os.path.dirname(os.path.dirname(cfg["venv_py"]))  # 제작도구/pyenv
    try:
        subprocess.run(["/usr/bin/python3", "-m", "venv", base], check=True,
                       capture_output=True, text=True, timeout=300)
        pip = os.path.join(base, "bin", "pip")
        subprocess.run([pip, "install", "--quiet", "--upgrade", "pip"],
                       capture_output=True, text=True, timeout=300)
        r = subprocess.run([pip, "install", "--quiet", "openai", "pillow"],
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            return False, "그림 생성 준비(openai·pillow 설치)에 실패했어요. 인터넷 연결을 확인해 주세요."
    except (OSError, subprocess.SubprocessError) as ex:
        return False, "그림 생성 준비 중 문제가 생겼어요: %s" % ex
    return os.path.exists(cfg["venv_py"]), ""


def target_count(cfg, art_range):
    if art_range:
        return int(art_range[1]) - int(art_range[0]) + 1
    return max(0, cfg["total"] - count_art(cfg))


def est_cost(cfg, art_range):
    return target_count(cfg, art_range) * cfg["price_per_image"]


# ---- 계획서(단계) 만들기
def build_plan(cfg, test=False):
    have = count_art(cfg)
    missing = max(0, cfg["total"] - have)
    rng = [1, 3] if test else None
    n = 3 if test else missing
    steps = []
    if n > 0:
        steps.append({
            "employee": "홈페이지",
            "title": "성화 그림 생성" + (" (테스트 3장)" if test else " (남은 %d장)" % missing),
            "instruction": "성서 40주간 성화 그림을 OpenAI 로 만듭니다. 돈이 드는 일이라 결재 후에만 시작합니다. "
                           "완성된 그림은 홈페이지배포/images 에 저장되고, 원본 글·데이터는 건드리지 않습니다.",
            "job": "art", "artRange": rng,
            "needs_approval": False, "approvedToRun": False,
            "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": [],
        })
    steps.append({
        "employee": "홈페이지",
        "title": "사이트에 올리기(배포)",
        "instruction": "완성된 홈페이지배포 폴더를 인터넷(%s)에 올립니다. Cloudflare Pages 자동 배포." % cfg["site_url"],
        "job": "deploy",
        "needs_approval": False, "approvedToRun": False,
        "status": "pending", "revisions": 0, "feedback": [], "result": "", "files": [],
    })
    if test:
        plan_n = "성화 3장(테스트)"
    elif missing > 0:
        plan_n = "남은 성화 %d장" % missing
    else:
        plan_n = "그림은 이미 다 있어 배포만"
    summary = ("성서 40주간 사이트를 제작·배포합니다. "
               "개발팀 홈페이지 직원이 %s을 만들고(돈 드는 일은 결재), 완성된 사이트를 인터넷(%s)에 올립니다. "
               "지금 그림 %d/%d장 완성." % (plan_n, cfg["site_url"], have, cfg["total"]))
    return {"summary": summary, "steps": steps}


# ---- 성화 생성 실행 (서버에서)
def run_art(cfg, fake, on_progress, art_range, should_abort):
    """반환: (성공여부, 최종_그림수, 메시지)
    on_progress(done, total): 지금까지 만든 수 / 이번에 만들 전체 수."""
    total = cfg["total"]
    if fake:
        # 연습 모드: 실제 파일·돈 없이 숫자만 올려 '일하는 모습'만 보여 준다
        start = count_art(cfg)
        make = 3 if art_range else min(8, max(1, total - start))  # 연습은 짧게
        for i in range(1, make + 1):
            if should_abort():
                return False, start + i - 1, "교수님이 멈췄어요."
            on_progress(i, make)
            time.sleep(1.2)
        return True, start + make, "연습 모드라 실제 그림은 만들지 않았어요."

    key = read_key(cfg)
    if not key:
        return False, count_art(cfg), "OpenAI API 키가 없어요. '제작도구/openai_key.txt' 에 키(sk-...)를 넣어 주세요."
    ok, msg = ensure_venv(cfg)
    if not ok:
        return False, count_art(cfg), msg
    if not os.path.exists(os.path.join(cfg["gen_dir"], cfg["gen_script"])):
        return False, count_art(cfg), "성화 생성 스크립트를 찾지 못했어요: %s" % cfg["gen_script"]

    env = dict(os.environ)
    env["OPENAI_API_KEY"] = key
    env["PATH"] = cfg["node_bin"] + os.pathsep + env.get("PATH", "")
    cmd = [cfg["venv_py"], cfg["gen_script"]]
    if art_range:
        cmd += ["--range", str(art_range[0]), str(art_range[1])]
    baseline = count_art(cfg)
    batch_total = (int(art_range[1]) - int(art_range[0]) + 1) if art_range else total
    logf = tempfile.NamedTemporaryFile(prefix="art_", suffix=".log", delete=False, mode="w", encoding="utf-8")

    def report():
        cur = count_art(cfg)
        done = min(batch_total, cur - baseline) if art_range else cur
        on_progress(max(0, done), batch_total)
    try:
        proc = subprocess.Popen(cmd, cwd=cfg["gen_dir"], stdout=logf, stderr=subprocess.STDOUT, env=env)
    except OSError as ex:
        logf.close()
        return False, count_art(cfg), "성화 생성을 시작하지 못했어요: %s" % ex
    try:
        while proc.poll() is None:
            report()
            if should_abort():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False, count_art(cfg), "교수님이 멈췄어요."
            time.sleep(2)
    finally:
        logf.close()
    rc = proc.returncode
    final = count_art(cfg)
    report()
    if rc != 0:
        tail = ""
        try:
            tail = open(logf.name, encoding="utf-8", errors="replace").read()[-400:]
        except OSError:
            pass
        low = tail.lower()
        if "api key" in low or "apikey" in low or "401" in tail or "authentication" in low:
            return False, final, "OpenAI API 키에 문제가 있어요. 키가 맞는지, 결제가 등록됐는지 확인해 주세요."
        if "insufficient" in low or "quota" in low or "billing" in low:
            return False, final, "OpenAI 결제/한도 문제로 멈췄어요. OpenAI 결제 상태를 확인해 주세요."
        return False, final, "그림 생성 중 문제가 생겼어요: %s" % (tail.strip()[-200:] or "알 수 없는 오류")
    try:
        os.remove(logf.name)
    except OSError:
        pass
    return True, final, ""


# ---- 배포 실행 (서버에서)
def run_deploy(cfg, fake, on_progress):
    """반환: (성공여부, 사이트주소, 메시지)"""
    if fake:
        on_progress("사이트에 올리는 중… (연습)")
        time.sleep(3)
        return True, cfg["site_url"], "연습 모드라 실제로 올리지 않았어요."
    if not os.path.isdir(cfg["site_dir"]):
        return False, None, "올릴 폴더(홈페이지배포)를 찾지 못했어요."
    if not os.path.exists(cfg["wrangler"]):
        return False, None, "배포 도구(wrangler)를 찾지 못했어요. 제작도구 설치를 확인해 주세요."
    on_progress("사이트에 올리는 중…")
    stage = tempfile.mkdtemp(prefix="deploy_")
    try:
        subprocess.run(["rsync", "-a", "--exclude", ".git", "--exclude", ".DS_Store",
                        "--exclude", "*.command", cfg["site_dir"] + "/", stage + "/"],
                       check=True, capture_output=True, text=True, timeout=300)
        env = dict(os.environ)
        env["PATH"] = cfg["node_bin"] + os.pathsep + env.get("PATH", "")
        r = subprocess.run([cfg["wrangler"], "pages", "deploy", stage,
                            "--project-name", cfg["project"], "--branch", "main", "--commit-dirty=true"],
                           capture_output=True, text=True, env=env, timeout=1800)
    except (OSError, subprocess.SubprocessError) as ex:
        shutil.rmtree(stage, ignore_errors=True)
        return False, None, "배포 중 문제가 생겼어요: %s" % ex
    shutil.rmtree(stage, ignore_errors=True)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0:
        m = re.search(r"https://[0-9a-z\-]+\.%s\.pages\.dev" % re.escape(cfg["project"]), out)
        return True, (m.group(0) if m else cfg["site_url"]), ""
    low = out.lower()
    if "not logged in" in low or "login" in low or "auth" in low or "token" in low:
        return (False, None,
                "Cloudflare 로그인이 만료됐어요. 한 번만 로그인하면 됩니다. "
                "터미널에서 아래 한 줄을 실행하고 브라우저에서 허용을 눌러 주세요:\n"
                "\"%s\" login" % cfg["wrangler"])
    return False, None, "배포 중 문제가 생겼어요: %s" % (out.strip()[-200:] or "알 수 없는 오류")
