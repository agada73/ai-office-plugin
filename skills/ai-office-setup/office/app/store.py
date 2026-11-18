"""데이터 저장소: data/ 폴더의 엄격한 JSON 파일을 안전하게 읽고 씁니다."""
import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NOTEBOOKS = os.path.join(DATA, "notebooks")
LOGS = os.path.join(DATA, "logs")

_lock = threading.RLock()


def ensure_dirs():
    for d in (DATA, NOTEBOOKS, LOGS):
        os.makedirs(d, exist_ok=True)


def _path(name):
    return os.path.join(DATA, name + ".json")


def load(name, default):
    with _lock:
        p = _path(name)
        if not os.path.exists(p):
            return default
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            # 깨진 파일은 옆에 보관하고 새로 시작
            try:
                os.replace(p, p + ".broken-%d" % int(time.time()))
            except OSError:
                pass
            return default


def save(name, obj):
    with _lock:
        ensure_dirs()
        p = _path(name)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(line):
    ensure_dirs()
    with _lock:
        with open(os.path.join(LOGS, time.strftime("%Y-%m-%d") + ".log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), line))


def notebook_path(name):
    return os.path.join(NOTEBOOKS, name + ".md")


def read_notebook(name):
    p = notebook_path(name)
    if os.path.exists(p):
        return open(p, encoding="utf-8").read()
    return ""


def append_notebook(name, text, section="승인된 방식"):
    """업무 수첩의 알맞은 제목(## section) 아래에 한 줄을 덧붙입니다."""
    ensure_dirs()
    with _lock:
        p = notebook_path(name)
        body = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
        head = "## " + section
        line = text.strip()
        if head not in body:
            body = body.rstrip() + "\n\n" + head + "\n"
        parts = body.split(head, 1)
        rest = parts[1]
        nxt = rest.find("\n## ")
        if nxt == -1:
            rest = rest.rstrip("\n") + "\n" + line + "\n"
        else:
            rest = rest[:nxt].rstrip("\n") + "\n" + line + "\n" + rest[nxt:]
        with open(p, "w", encoding="utf-8") as f:
            f.write(parts[0] + head + rest)


RULE_HEAD = "## 교수님이 정한 규칙"


def _split_rules(body):
    """수첩 글을 (앞부분, 규칙 줄들, 뒷부분)으로 나눔"""
    if RULE_HEAD not in body:
        body = body.rstrip() + "\n\n" + RULE_HEAD + "\n\n"
    pre, rest = body.split(RULE_HEAD, 1)
    nxt = rest.find("\n## ")
    sect, post = (rest, "") if nxt == -1 else (rest[:nxt], rest[nxt:])
    rules = [ln[2:].strip() for ln in sect.splitlines() if ln.startswith("- ") and ln[2:].strip()]
    return pre, rules, post


def get_rules(name):
    return _split_rules(read_notebook(name))[1]


def _write_rules(name, pre, rules, post):
    body = pre + RULE_HEAD + "\n" + "".join("- %s\n" % r for r in rules) + "\n" + post.lstrip("\n")
    with _lock:
        ensure_dirs()
        with open(notebook_path(name), "w", encoding="utf-8") as f:
            f.write(body)


def add_rule(name, text):
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("규칙 내용을 적어 주세요.")
    if len(text) > 400:
        raise ValueError("규칙은 400자 안으로 적어 주세요.")
    with _lock:
        pre, rules, post = _split_rules(read_notebook(name))
        if text in rules:
            raise ValueError("이미 있는 규칙이에요.")
        rules.append(text)
        _write_rules(name, pre, rules, post)
    return rules


def remove_rule(name, index):
    with _lock:
        pre, rules, post = _split_rules(read_notebook(name))
        if not isinstance(index, int) or index < 0 or index >= len(rules):
            raise ValueError("그런 규칙이 없어요.")
        gone = rules.pop(index)
        _write_rules(name, pre, rules, post)
    return gone
