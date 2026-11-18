"""아바타가 소리 내어 읽을 글을 '한글 소리 나는 대로' 바꾸는 도구
- 숫자: 6차시 → 육차시, 25분 → 이십오 분, 3가지 → 세 가지, 1924년 → 천구백이십사 년
- 알파벳 약자: ATU → 에이티유
읽는 소리가 헷갈리지 않게 하려는 거라, 글의 뜻은 그대로예요.
"""
import re

_SINO = "영일이삼사오육칠팔구"
_UNIT = ["", "십", "백", "천"]
_BIG = ["", "만", "억", "조"]


def sino(n):
    """0 이상 정수를 한자어 수로 (6 → 육, 25 → 이십오, 1924 → 천구백이십사)"""
    if n == 0:
        return "영"
    parts, g = [], 0
    while n > 0:
        chunk = n % 10000
        n //= 10000
        if chunk:
            s, digits = "", [int(c) for c in str(chunk).zfill(4)]
            for i, d in enumerate(digits):
                pos = 3 - i
                if d == 0:
                    continue
                s += ("" if (d == 1 and pos > 0) else _SINO[d]) + _UNIT[pos]
            if g > 0 and s == "일":
                s = ""
            parts.append(s + _BIG[g])
        g += 1
    return "".join(reversed(parts))


_NATIVE_ONES = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
_NATIVE_TENS = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]


def native_modifier(n):
    """1~99 를 세는 말 앞에 붙는 고유어 수로 (3 → 세, 20 → 스무, 12 → 열두)"""
    if not 1 <= n <= 99:
        return None
    if n == 20:
        return "스무"
    return _NATIVE_TENS[n // 10] + _NATIVE_ONES[n % 10]


# 한자어 수로 읽는 말 / 고유어 수로 읽는 말
_SINO_UNITS = ["차시", "쪽", "분", "초", "년", "월", "일", "학년", "단계", "주차", "주", "회", "장", "편", "권", "곳", "점", "번호", "호", "조", "항", "과", "교시", "페이지", "퍼센트", "%"]
_NATIVE_UNITS = ["번째", "가지", "개", "명", "마리", "살", "분께서", "사람", "그루", "송이", "채", "병", "잔", "켤레"]


def _num(s):
    return int(s.replace(",", ""))


def to_spoken(text):
    """글 속의 숫자와 알파벳 약자를 한글 소리로 바꿈"""
    t = text

    # 0) '여섯 차시' 처럼 글자로 쓴 고유어 수 + 차시 → 한자어로 (육차시)
    nat2sino = {"한": "일", "두": "이", "세": "삼", "네": "사", "다섯": "오", "여섯": "육", "일곱": "칠", "여덟": "팔", "아홉": "구", "열": "십"}
    t = re.sub(r"(여섯|일곱|여덟|아홉|다섯|열|한|두|세|네)\s*차시", lambda m: nat2sino[m.group(1)] + "차시", t)

    # 0-1) 6월·10월은 유월·시월
    t = re.sub(r"(?<![\d.])0?6\s*월", "유월", t)
    t = re.sub(r"(?<![\d.])10\s*월", "시월", t)
    # 0-2) 소수 '1.5' → 일 점 오
    t = re.sub(r"(\d+)\.(\d+)", lambda m: sino(_num(m.group(1))) + " 점 " + " ".join(_SINO[int(c)] for c in m.group(2)), t)
    # 0-3) 범위 '3~5' → 삼에서 오
    t = re.sub(r"(\d)\s*[~∼]\s*(\d)", r"\1에서 \2", t)

    # 1) 시각 '3시 20분' → 세 시 이십 분 (시 앞은 고유어)
    def hour(m):
        h = _num(m.group(1))
        return (native_modifier(h) or sino(h)) + " 시"
    t = re.sub(r"(\d{1,2})\s*시(?!작|간|대|험|설|도|행|민|선|청|각)", hour, t)

    # 2) 고유어로 세는 말
    for u in sorted(_NATIVE_UNITS, key=len, reverse=True):
        def nat(m, u=u):
            n = _num(m.group(1))
            w = native_modifier(n)
            return ((w + " ") if w else sino(n) + " ") + u
        t = re.sub(r"(\d[\d,]*)\s*" + re.escape(u), nat, t)

    # 3) 한자어로 세는 말 (붙여 읽는 것: 육차시, 이십오 분)
    def tight(m):
        return sino(_num(m.group(1))) + m.group(2)
    t = re.sub(r"(\d[\d,]*)\s*(차시|학년|교시|주차)", tight, t)

    for u in sorted(_SINO_UNITS, key=len, reverse=True):
        if u in ("차시", "학년", "교시", "주차", "%"):
            continue
        t = re.sub(r"(\d[\d,]*)\s*" + re.escape(u), lambda m, u=u: sino(_num(m.group(1))) + " " + u, t)
    t = re.sub(r"(\d[\d,]*)\s*%", lambda m: sino(_num(m.group(1))) + " 퍼센트", t)

    # 4) 번호 매김: '1)', '2.' 같은 건 읽지 않게 두고, 나머지 남은 숫자는 한자어로 읽음
    t = re.sub(r"\d[\d,]*", lambda m: sino(_num(m.group(0))), t)

    # 5) 알파벳 약자(대문자 2자 이상)는 글자 이름대로
    names = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                     ["에이", "비", "씨", "디", "이", "에프", "지", "에이치", "아이", "제이", "케이", "엘", "엠", "엔", "오", "피", "큐", "알", "에스", "티", "유", "브이", "더블유", "엑스", "와이", "지"]))
    t = re.sub(r"\b[A-Z]{2,5}\b", lambda m: "".join(names[c] for c in m.group(0)), t)
    return t


def apply_pronounce(text, mapping):
    """교수님이 들어 보고 정한 발음으로 바꿈 (예: 이솝 → 이 Sob). 영상에서 읽을 글에만 씀"""
    for k, v in sorted((mapping or {}).items(), key=lambda kv: -len(kv[0])):
        text = text.replace(k, v)
    return text


def undo_pronounce(text, mapping):
    """자막에는 원래 표기로 되돌림"""
    for k, v in sorted((mapping or {}).items(), key=lambda kv: -len(kv[1])):
        text = text.replace(v, k)
    return text


if __name__ == "__main__":
    for s in ["6차시 강의입니다", "총 25분, 3가지 갈래", "1924년과 2012년", "ATU 유형과 KHM 182", "오후 3시 20분에 12명이", "40%의 사람들", "1번 2번"]:
        print(s, "→", to_spoken(s))
