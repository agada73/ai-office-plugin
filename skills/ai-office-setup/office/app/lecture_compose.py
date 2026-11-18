"""강의 영상 합성 도구 (파이썬 기본 기능 + ffmpeg)
- 강의자료 쪽 그림(슬라이드)이 화면 전체를 채우고
- 헤이젠 아바타 영상이 첫 쪽과 골라 둔 쪽에서만 오른쪽 아래 구석에 작게 나오도록 합칩니다.
- 소리는 아바타 영상의 소리(대본을 읽는 목소리)를 그대로 씁니다.
"""
import json
import os
import re
import shutil
import subprocess


def find_ffmpeg():
    for p in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if p and os.path.exists(p):
            return p
    return None


def find_ffprobe():
    for p in (shutil.which("ffprobe"), "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
        if p and os.path.exists(p):
            return p
    return None


def video_duration(path):
    probe = find_ffprobe()
    out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
                         capture_output=True, text=True)
    return float(out.stdout.strip())


# ---------------------------------------------------------------- 쪽 경계 시각 맞추기
def _norm(s):
    return re.sub(r"[\s\W_]+", "", s or "")


def parse_srt(path):
    """srt 자막 → [(시작초, 끝초, 글)]"""
    cues = []
    txt = open(path, encoding="utf-8", errors="ignore").read().replace("\r", "")
    for block in re.split(r"\n\s*\n", txt.strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        for i, l in enumerate(lines):
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", l)
            if m:
                g = list(map(int, m.groups()))
                a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                cues.append((a, b, " ".join(lines[i + 1:])))
                break
    return cues


def align_pages(cues, page_texts, total_seconds):
    """각 쪽이 시작하는 시각(초)을 구함. 자막 글자 수로 비례해서 정하고, 각 쪽 첫머리 글이 자막에 있으면 그 위치로 보정."""
    n = len(page_texts)
    if n == 0:
        return []
    if not cues:     # 자막이 없으면 글자 수 비례로만
        lens = [max(1, len(_norm(t))) for t in page_texts]
        tot = float(sum(lens))
        out, acc = [], 0.0
        for L in lens:
            out.append(total_seconds * acc / tot)
            acc += L
        return out
    # 자막 글자 → 시각 표 (글자마다 해당 자막 안에서 비례로 시각을 줌)
    chars, times = [], []
    for a, b, t in cues:
        nt = _norm(t)
        for k, ch in enumerate(nt):
            chars.append(ch)
            times.append(a + (b - a) * (k / max(1, len(nt))))
    srt_text = "".join(chars)
    lens = [max(1, len(_norm(t))) for t in page_texts]
    tot = float(sum(lens))
    starts, acc = [], 0.0
    for i, t in enumerate(page_texts):
        expect = int(len(srt_text) * acc / tot)
        pos = expect
        if i > 0:
            head = _norm(t)[:8]
            if head:
                lo, hi = max(0, expect - 400), min(len(srt_text), expect + 400)
                j = srt_text.find(head, lo, hi)
                if j >= 0:
                    pos = j
        pos = min(max(pos, 0), len(times) - 1)
        starts.append(0.0 if i == 0 else times[pos])
        acc += lens[i]
    # 시각이 거꾸로 가지 않게 정리
    for i in range(1, n):
        if starts[i] <= starts[i - 1]:
            starts[i] = min(total_seconds, starts[i - 1] + 0.5)
    return starts


# ---------------------------------------------------------------- 사람만 오리기
def build_cutout(avatar_video, mask_video, bbox_json, spans, tool, log=None):
    """avatar_cutout 도구로 사람 마스크 영상과 사람 범위(bbox)를 만듦. spans: [(시작초, 끝초)] 아바타가 나올 구간만 처리"""
    arg = ",".join("%.2f-%.2f" % (a, b) for a, b in spans)
    cmd = [tool, avatar_video, mask_video, bbox_json] + ([arg] if arg else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(bbox_json):
        raise RuntimeError("아바타에서 사람만 오리지 못했어요: " + (r.stdout + r.stderr)[-400:])
    if log:
        log("사람 오리기: " + r.stdout.strip())
    return json.load(open(bbox_json, encoding="utf-8"))


def video_size(path):
    probe = find_ffprobe()
    out = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip().split(",")
    return int(out[0]), int(out[1])


# ---------------------------------------------------------------- 합치기
def compose(slides, avatar_video, page_starts, avatar_pages, out_path, size=(1920, 1080), fps=25,
            avatar_scale=0.26, first_scale=0.38, margin=40, first_page_big=True, crf=20, log=None,
            mask_video=None, bbox=None, avatar_height=0.46, first_height=0.62, upper_body=0.80, first_margin=-40):
    """slides: 쪽 그림 파일 목록 / page_starts: 각 쪽 시작 시각(초) / avatar_pages: 아바타가 나오는 쪽 번호(1부터)
    mask_video 와 bbox 가 있으면 배경 없이 상반신만 오려서 오른쪽 아래에 붙이고, 없으면 네모난 창으로 넣어요."""
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("영상 합성 도구(ffmpeg)가 설치돼 있지 않아요.")
    W, H = size
    total = video_duration(avatar_video)
    n = len(slides)
    bounds = list(page_starts) + [total]
    durs = [max(0.2, bounds[i + 1] - bounds[i]) for i in range(n)]
    durs[-1] = max(0.2, total - bounds[n - 1])
    cutout = bool(mask_video and bbox)
    inputs = ["-i", avatar_video]
    if cutout:
        inputs += ["-i", mask_video]
    off = 2 if cutout else 1
    for sl, d in zip(slides, durs):
        inputs += ["-framerate", str(fps), "-loop", "1", "-t", "%.3f" % d, "-i", sl]
    parts = []
    for i in range(n):
        parts.append("[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:white,setsar=1,fps=%d,format=yuv420p[s%d]"
                     % (i + off, W, H, W, H, fps, i))
    parts.append("".join("[s%d]" % i for i in range(n)) + "concat=n=%d:v=1:a=0[base]" % n)

    # 아바타 나오는 시간 구간 (붙어 있는 쪽은 한 구간으로 합침)
    ap = sorted(set(p for p in avatar_pages if 1 <= p <= n))
    small, big = [], []
    for p in ap:
        a, b = bounds[p - 1], bounds[p]
        if first_page_big and p == 1:
            big.append((a, b))
        else:
            if small and abs(small[-1][1] - a) < 0.05:
                small[-1] = (small[-1][0], b)
            else:
                small.append((a, b))

    def enable(spans_):
        return "+".join("between(t,%.3f,%.3f)" % (a, b) for a, b in spans_)
    kinds = [(k, sp) for k, sp in (("small", small), ("big", big)) if sp]
    cur, idx = "base", 0
    if cutout and kinds:
        vw, vh = video_size(avatar_video)
        # 11-13: 사람 범위가 몇 장면 잘못 잡혀 화면 전체 너비(0.998)가 되면 1.04배가 화면보다 커져 ffmpeg 가 멈췄어요(-22).
        #        너비는 화면의 62% 까지만 쓰고(사람 가운데 기준), 자르는 크기는 절대 화면보다 크지 않게 해요.
        bw = min(float(bbox["w"]), 0.62)
        cw = max(2, min(vw, int(bw * vw * 1.04)) // 2 * 2)
        ch = max(2, min(vh, int(bbox["h"] * vh * upper_body)) // 2 * 2)
        cx = int(max(0, min(vw - cw, (bbox["x"] + bbox["w"] / 2) * vw - cw / 2)))
        cy = int(max(0, min(vh - ch, bbox["y"] * vh - 0.03 * vh)))
        parts.append("[0:v]crop=%d:%d:%d:%d,format=yuv420p[avc]" % (cw, ch, cx, cy))
        parts.append("[1:v]crop=%d:%d:%d:%d,format=gray[mk]" % (cw, ch, cx, cy))
        parts.append("[avc][mk]alphamerge[ava]")
        if len(kinds) == 2:
            parts.append("[ava]split=2[avs][avb]")
            src = {"small": "avs", "big": "avb"}
        else:
            src = {kinds[0][0]: "ava"}
        for k, sp in kinds:
            ah = int(H * (avatar_height if k == "small" else first_height)) // 2 * 2
            parts.append("[%s]scale=-2:%d[av%s]" % (src[k], ah, k))
            nxt = "o%d" % idx
            parts.append("[%s][av%s]overlay=x=W-w-%d:y=H-h:enable='%s':eof_action=pass[%s]" % (cur, k, margin if k == "small" else first_margin, enable(sp), nxt))
            cur = nxt
            idx += 1
    else:
        for k, sp in kinds:
            aw = int(W * (avatar_scale if k == "small" else first_scale)) // 2 * 2
            parts.append("[0:v]scale=%d:-2,setsar=1,format=yuv420p,drawbox=x=0:y=0:w=iw:h=ih:color=white@1:t=6[av%s]" % (aw, k))
            nxt = "o%d" % idx
            parts.append("[%s][av%s]overlay=x=W-w-%d:y=H-h-%d:enable='%s':eof_action=pass[%s]" % (cur, k, margin, margin, enable(sp), nxt))
            cur = nxt
            idx += 1
    cmd = [ff, "-y"] + inputs + ["-filter_complex", ";".join(parts), "-map", "[%s]" % cur, "-map", "0:a?",
                                 "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
                                 "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", "%.3f" % total, out_path]
    if log:
        log("ffmpeg 시작: 쪽 %d, 아바타 구간 %d, 길이 %.1f초, %s" % (n, len(small) + len(big), total, "배경 없이 상반신만" if cutout else "네모난 창"))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("영상 합치기에 실패했어요: " + (r.stderr or "")[-600:])
    return {"seconds": total, "pages": n, "avatar_spans": small + big, "cutout": cutout}


def concat_parts(parts, out_path, crf=18):
    """같은 크기의 아바타 영상 조각들을 한 편으로 이어 붙임 (소리 포함)"""
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("영상 합성 도구(ffmpeg)가 설치돼 있지 않아요.")
    ins = []
    for p in parts:
        ins += ["-i", p]
    n = len(parts)
    fg = "".join("[%d:v][%d:a]" % (i, i) for i in range(n)) + "concat=n=%d:v=1:a=1[v][a]" % n
    r = subprocess.run([ff, "-y"] + ins + ["-filter_complex", fg, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
                                           "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("아바타 영상 이어 붙이기에 실패했어요: " + (r.stderr or "")[-500:])


def _ts(x):
    ms = int(round(x * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def merge_srts(srt_paths, durations, out_path):
    """조각마다의 자막을 앞 조각 길이만큼 뒤로 밀어서 하나로 합침"""
    out, off, k = [], 0.0, 1
    for p, d in zip(srt_paths, durations):
        if p and os.path.exists(p):
            for a, b, t in parse_srt(p):
                out.append("%d\n%s --> %s\n%s\n" % (k, _ts(a + off), _ts(b + off), t))
                k += 1
        off += d
    open(out_path, "w", encoding="utf-8").write("\n".join(out))


def avatar_spans(page_starts, total, avatar_pages):
    """아바타가 나오는 쪽들의 시간 구간 목록"""
    bounds = list(page_starts) + [total]
    return [(bounds[p - 1], bounds[p]) for p in sorted(set(avatar_pages)) if 1 <= p <= len(page_starts)]


if __name__ == "__main__":
    import sys
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    print(compose(**spec, log=print))


# ---------------------------------------------------------------- 아껴 만들기: 아바타는 몇 쪽에만
def build_split_track(segments, out_path, size, log=None, fps=25):
    """쪽마다 소리를 한 줄로 이어 붙인 '바탕 영상'을 만듦 (아바타 쪽은 아바타 영상 조각, 나머지 쪽은 검은 화면 + 목소리).
    segments: [{"kind": "avatar", "src": 아바타영상, "start": 초, "end": 초} | {"kind": "voice", "src": mp3}]
    → 쪽마다의 길이(초) 목록"""
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("영상 합성 도구(ffmpeg)가 설치돼 있지 않아요.")
    w, h = size
    tmpdir = os.path.join(os.path.dirname(out_path), "_split_parts")
    os.makedirs(tmpdir, exist_ok=True)
    parts, durs = [], []
    venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "1"]
    for i, sg in enumerate(segments):
        p = os.path.join(tmpdir, "part_%03d.mp4" % (i + 1))
        if sg["kind"] == "silent":       # 읽을 글이 없는 쪽: 조용히 잠깐 보여 줌
            d = float(sg.get("seconds", 2.5))
            cmd = [ff, "-y", "-f", "lavfi", "-i", "color=c=black:s=%dx%d:r=%d" % (w, h, fps), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                   "-t", "%.3f" % d] + venc + [p]
        elif sg["kind"] == "avatar":
            d = max(0.3, float(sg["end"]) - float(sg["start"]))
            cmd = [ff, "-y", "-ss", "%.3f" % sg["start"], "-t", "%.3f" % d, "-i", sg["src"],
                   "-vf", "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (w, h, w, h)] + venc + [p]
        else:
            d = video_duration(sg["src"])
            cmd = [ff, "-y", "-f", "lavfi", "-i", "color=c=black:s=%dx%d:r=%d" % (w, h, fps), "-i", sg["src"],
                   "-t", "%.3f" % d, "-shortest"] + venc + [p]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("쪽 %d 조각을 만들지 못했어요: %s" % (i + 1, (r.stderr or "")[-300:]))
        parts.append(p)
        durs.append(video_duration(p))
    lst = os.path.join(tmpdir, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("".join("file '%s'\n" % os.path.basename(p) for p in parts))
    r = subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out_path],
                       capture_output=True, text=True, cwd=tmpdir)
    if r.returncode != 0:
        raise RuntimeError("쪽 조각을 이어 붙이지 못했어요: " + (r.stderr or "")[-300:])
    if log:
        log("아껴 만들기: 쪽 %d개를 이어 바탕 영상을 만들었어요 (%.0f초)" % (len(parts), sum(durs)))
    return durs


def write_page_srt(page_texts, starts, durs, out_path):
    """쪽마다의 시작 시각과 길이로 자막을 만듦 (쪽 안에서는 문장 길이에 비례해서 나눔)"""
    out, k = [], 1
    for text, s0, d in zip(page_texts, starts, durs):
        sents = [x.strip() for x in re.split(r"(?<=[.?!。])\s+", (text or "").strip()) if x.strip()]
        if not sents:
            continue
        tot = float(sum(len(x) for x in sents)) or 1.0
        t = s0
        for x in sents:
            dt = d * len(x) / tot
            out.append("%d\n%s --> %s\n%s\n" % (k, _ts(t), _ts(t + dt - 0.05), x))
            k += 1
            t += dt
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
