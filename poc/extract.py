#!/usr/bin/env python3
"""PoC: law.go.kr 원문 XML → 보관 메타 + 조문 트리 + 규칙 초안 추출.

docs/07-law-ingestion.md의 FETCH→PARSE→DRAFT 단계를 최소 구현으로 검증한다.
"""
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ARCHIVE = Path(__file__).parent / "law-archive/statutes/sodukse-sihaengryeong/2026-07-01_si-haeng_je36343ho"
LAW_XML = ARCHIVE / "law.xml"

# ── 1. ARCHIVE: 해시 + 메타 ──────────────────────────────────────
raw = LAW_XML.read_bytes()
sha256 = hashlib.sha256(raw).hexdigest()

tree = ET.fromstring(raw.decode("utf-8"))
basic = tree.find("기본정보")


def t(el, tag):
    node = el.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


meta = {
    "docId": "statutes/sodukse-sihaengryeong/2026-07-01_si-haeng_je36343ho",
    "title": t(basic, "법령명_한글"),
    "lawId": t(basic, "법령ID"),
    "promulgationDate": t(basic, "공포일자"),
    "promulgationNo": t(basic, "공포번호"),
    "effectiveDate": t(basic, "시행일자"),
    "revisionType": t(basic, "제개정구분"),
    "lawType": t(basic, "법종구분"),
    "sourceUrl": "https://www.law.go.kr/DRF/lawService.do?OC=test&target=law&MST=286211&type=XML&efYd=20260701",
    "fetchedAt": "2026-08-03",
    "sha256": sha256,
}
(ARCHIVE / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")

# ── 2. PARSE: 조문 트리 ──────────────────────────────────────────
articles = []
for jo in tree.iter("조문단위"):
    if t(jo, "조문여부") != "조문":
        continue
    num = t(jo, "조문번호")
    branch = t(jo, "조문가지번호")
    key = f"제{num}조" + (f"의{branch}" if branch and branch != "0" else "")
    content = t(jo, "조문내용")
    title_m = re.search(r"\(([^)]*)\)", content)
    paragraphs = []
    for hang in jo.findall("항"):
        hang_text = t(hang, "항내용")
        items = [t(ho, "호내용") for ho in hang.findall("호")]
        paragraphs.append({"text": hang_text, "items": items})
    articles.append({
        "article": key,
        "title": title_m.group(1) if title_m else "",
        "text": content,
        "paragraphs": paragraphs,
        "effectiveDate": t(jo, "조문시행일자"),
    })

parsed = {"docId": meta["docId"], "sha256": sha256, "articleCount": len(articles), "articles": articles}
(ARCHIVE / "parsed.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2), "utf-8")
print(f"조문 수: {len(articles)}, sha256: {sha256[:16]}…")

# ── 3. DRAFT: PoC 관심 조문에서 규칙 초안용 발췌 ─────────────────
TARGETS = {
    "제154조": "1세대 1주택 비과세 요건 (보유·거주기간)",
    "제155조": "1세대 1주택 특례 (일시적 2주택 등)",
    "제159조의4": "장기보유특별공제 (거주기간 요건)",
    "제167조의3": "1세대 3주택 이상 중과 범위",
    "제225조의2": "ISA 관련 (해당 시)",
}
by_key = {a["article"]: a for a in articles}
drafts = []
for art, why in TARGETS.items():
    a = by_key.get(art)
    if not a:
        print(f"  [없음] {art} ({why})")
        continue
    first_para = a["paragraphs"][0]["text"] if a["paragraphs"] else a["text"]
    drafts.append({
        "articlePath": art,
        "title": a["title"],
        "reviewNote": why,
        "quotedText": (a["text"] + " " + first_para)[:400],
        "source": {"docId": meta["docId"], "sha256": sha256, "articlePath": art,
                   "url": "https://law.go.kr/법령/소득세법시행령", "checkedAt": "2026-08-03"},
        "confidence": "drafted",
    })
    print(f"  [추출] {art} {a['title']} — 항 {len(a['paragraphs'])}개")

out = Path(__file__).parent / "extracted-rules-draft.json"
out.write_text(json.dumps(drafts, ensure_ascii=False, indent=2), "utf-8")
print(f"규칙 초안 발췌 → {out.name}")
