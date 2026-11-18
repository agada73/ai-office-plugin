"""문서 도구 (파이썬 기본 기능만 사용)
- pptx / docx 를 글자(텍스트)로 뽑기: 직원이 슬라이드 내용을 읽을 수 있게
- 마크다운(.md) 글을 워드(.docx)로 바꾸기: 직원이 쓴 글을 교수님이 열어 볼 수 있게
"""
import re
import zipfile
from xml.sax.saxutils import escape


def _strip_ns(xml, tag):
    return re.findall(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), xml, flags=re.S)


def pptx_to_text(path):
    out = []
    with zipfile.ZipFile(path) as z:
        slides = sorted(
            (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.findall(r"\d+", n)[0]),
        )
        for n in slides:
            xml = z.read(n).decode("utf-8", "ignore")
            paras = []
            for p in re.findall(r"<a:p>.*?</a:p>|<a:p [^>]*>.*?</a:p>", xml, flags=re.S):
                t = "".join(re.findall(r"<a:t[^>]*>(.*?)</a:t>", p, flags=re.S))
                t = _unescape(t).strip()
                if t:
                    paras.append(t)
            num = int(re.findall(r"\d+", n)[0])
            out.append("[슬라이드 %d]\n%s" % (num, "\n".join(paras)))
            notes = "ppt/notesSlides/notesSlide%d.xml" % num
            if notes in z.namelist():
                nx = z.read(notes).decode("utf-8", "ignore")
                nt = _unescape(" ".join(re.findall(r"<a:t[^>]*>(.*?)</a:t>", nx, flags=re.S))).strip()
                if nt:
                    out.append("(발표자 노트) " + nt)
    return "\n\n".join(out)


def docx_to_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    paras = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        t = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))
        paras.append(_unescape(t))
    return "\n".join(paras)


def _unescape(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&"))


def extract_text(path):
    low = path.lower()
    if low.endswith(".pptx"):
        return pptx_to_text(path)
    if low.endswith(".docx"):
        return docx_to_text(path)
    if low.endswith((".txt", ".md", ".csv", ".json")):
        return open(path, encoding="utf-8", errors="ignore").read()
    return None


# ---------- 마크다운 → 워드 ----------
def _run(text, bold=False):
    props = "<w:rPr><w:rFonts w:ascii=\"맑은 고딕\" w:eastAsia=\"맑은 고딕\" w:hAnsi=\"맑은 고딕\"/>%s</w:rPr>" % ("<w:b/>" if bold else "")
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (props, escape(text))


def _inline(text):
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    return "".join(_run(p[2:-2], True) if p.startswith("**") and p.endswith("**") else _run(p) for p in parts if p)


def md_to_docx(md_text, out_path, title=None):
    body = []
    if title:
        body.append('<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>%s</w:p>' % _run(title, True))
    for line in md_text.splitlines():
        s = line.rstrip()
        if not s.strip():
            body.append("<w:p/>")
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            level = len(m.group(1))
            body.append('<w:p><w:pPr><w:pStyle w:val="Heading%d"/></w:pPr>%s</w:p>' % (level, _run(m.group(2), True)))
            continue
        m = re.match(r"^\s*[-*]\s+(.*)$", s)
        if m:
            body.append('<w:p><w:pPr><w:ind w:left="360" w:hanging="240"/></w:pPr>%s</w:p>' % _inline("• " + m.group(1)))
            continue
        body.append("<w:p>%s</w:p>" % _inline(s))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>" % "".join(body)
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="맑은 고딕" w:eastAsia="맑은 고딕" w:hAnsi="맑은 고딕"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:sz w:val="40"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:spacing w:before="320" w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:spacing w:before="240" w:after="100"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:pPr><w:spacing w:before="200" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>'
        "</w:styles>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
