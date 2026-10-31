"""회사 비교 · 기업 요약 리포트.

둘 다 같은 방식이다.
1) 항목(사업, 연구개발, 인력 …)마다 회사별로 공시 조각을 검색하고
2) 재무제표 API 의 정확한 숫자와 함께 Claude 에 한 번에 넘겨
3) 출처 번호가 달린 결과를 받는다.
질문마다 따로 부르는 대신 한 번에 보내서 비용과 시간을 줄였다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from markdown_it import MarkdownIt

from .rag import DartRAG, cited_numbers, format_sources
from .usage import Usage

# (항목 이름, 검색어)
ASPECTS = [
    ("사업 구조", "주요 제품 서비스 사업부문별 매출 비중"),
    ("시장·경쟁", "시장 점유율 경쟁 요인 산업 특성"),
    ("연구개발", "연구개발비 매출액 대비 비율 연구개발 실적"),
    ("인력", "직원 수 평균 급여 근속연수"),
    ("주주", "최대주주 및 특수관계인 지분율"),
    ("배당", "주당 배당금 배당성향"),
    ("리스크", "소송 우발부채 제재 위험"),
]

COMPARE_SYSTEM = """당신은 한국 상장사 공시를 비교 분석하는 애널리스트입니다.
- 재무 수치는 반드시 <financials> 의 값을 그대로 쓰세요. (가장 정확한 출처)
- 나머지는 <sources> 발췌문에만 근거하고, 문장·칸마다 [번호]로 출처를 붙이세요.
- 발췌문에 없으면 '공시에서 확인 안 됨'이라고 쓰세요. 추측 금지.
- 투자 권유나 주가 전망은 하지 마세요."""

COMPARE_TASK = """{companies} 를 비교해 주세요.

형식:
1. 첫 줄에 한 문장 요약
2. 마크다운 표: 행 = {aspects}, 열 = 각 회사. 칸은 짧게(40자 이내) 쓰고 출처 번호를 붙이세요.
   표 맨 위에 '매출액', '영업이익률', '부채비율' 행을 <financials> 최근 연도 값으로 넣으세요.
3. '### 눈에 띄는 차이' 아래 3개 이하의 글머리표"""

REPORT_TASK = """{company} 의 기업 요약 리포트를 작성해 주세요. 처음 이 회사를 보는 사람이 5분 안에 파악할 수 있게.

형식 (마크다운):
# {company} 기업 요약
> 한 줄 요약

## 한눈에 보기
(최근 연도 매출액·영업이익·영업이익률·부채비율 — <financials> 값 사용, 3~5년 추세 한 줄)
## 무슨 사업을 하나
## 시장과 경쟁
## 연구개발
## 사람과 주주
(직원, 급여, 최대주주, 배당)
## 리스크
## 체크포인트
(다음 공시에서 지켜볼 점 3가지 — 공시에 근거한 것만)

각 문장 끝에 [번호] 출처를 붙이세요."""


@dataclass
class Analysis:
    text: str
    sources: list[dict]
    usage: Usage
    cited: list[int] = field(default_factory=list)


def gather(rag: DartRAG, company: str, rcept_no: str, per_aspect: int = 2) -> list[dict]:
    """항목별로 검색해서 겹치지 않게 모은다."""
    seen, out = set(), []
    for _, query in ASPECTS:
        for h in rag.store.search(f"{company} {query}", k=per_aspect, rcept_nos=[rcept_no]):
            if h["id"] not in seen:
                seen.add(h["id"])
                out.append(h)
    return out


def compare(rag: DartRAG, targets: list[dict], fin_texts: list[str]) -> Analysis:
    """targets: [{"company":..., "rcept_no":...}, ...]"""
    with rag.tracker.track() as u:
        hits = []
        for t in targets:
            hits += gather(rag, t["company"], t["rcept_no"])
        task = COMPARE_TASK.format(companies=", ".join(t["company"] for t in targets),
                                   aspects=", ".join(a for a, _ in ASPECTS))
        prompt = f"<financials>\n{chr(10).join(fin_texts)}\n</financials>\n\n{format_sources(hits)}\n\n{task}"
        text = rag.complete([{"role": "user", "content": prompt}], COMPARE_SYSTEM, max_tokens=3000)
    return Analysis(text, hits, u, cited_numbers(text, len(hits)))


def report(rag: DartRAG, company: str, rcept_no: str, fin_text: str) -> Analysis:
    with rag.tracker.track() as u:
        hits = gather(rag, company, rcept_no, per_aspect=3)
        prompt = (f"<financials>\n{fin_text}\n</financials>\n\n{format_sources(hits)}\n\n"
                  + REPORT_TASK.format(company=company))
        text = rag.complete([{"role": "user", "content": prompt}], COMPARE_SYSTEM, max_tokens=4000)
    return Analysis(text, hits, u, cited_numbers(text, len(hits)))


def to_html(title: str, markdown_text: str, sources: list[dict], used: list[int]) -> str:
    """다운로드용 HTML. 브라우저에서 열고 Ctrl+P → 'PDF로 저장' 하면 PDF 가 된다."""
    md = MarkdownIt("commonmark").enable("table")
    refs = "".join(
        f'<li id="s{i}"><b>[{i}]</b> {h["metadata"]["company"]} · {h["metadata"]["doc_name"]} · '
        f'{h["metadata"]["section"]} — <a href="{h["metadata"]["url"]}">원문</a></li>'
        for i, h in enumerate(sources, 1) if i in used)
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;max-width:820px;margin:40px auto;padding:0 24px;line-height:1.7;color:#222}}
h1{{border-bottom:2px solid #222;padding-bottom:8px}} h2{{margin-top:32px;color:#1a4d8f}}
blockquote{{margin:0;padding:8px 16px;background:#f3f6fb;border-left:4px solid #1a4d8f}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ccc;padding:6px 10px;text-align:left}}
th{{background:#f3f3f3}} .refs{{font-size:13px;color:#555;margin-top:40px;border-top:1px solid #ddd}}
.foot{{font-size:12px;color:#888;margin-top:24px}}
@media print{{body{{margin:0}} a{{color:#222}}}}
</style></head><body>
{md.render(markdown_text)}
<div class="refs"><h3>출처</h3><ol style="list-style:none;padding:0">{refs}</ol></div>
<p class="foot">DART 전자공시 원문과 재무제표 API를 바탕으로 AI(Claude)가 작성했습니다. 투자 권유가 아닙니다.</p>
</body></html>"""
