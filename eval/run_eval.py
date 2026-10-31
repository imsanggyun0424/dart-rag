"""검색·답변 품질을 숫자로 측정한다. (포트폴리오 README 의 '성과' 표에 쓰기 좋다)

1) 검색 평가 (API 비용 없음)
   질문마다 정답 섹션(expect_section)이 상위 k개 안에 들어오는지 = Hit@k, 그리고 MRR.
   vector / keyword / hybrid 세 방식을 비교한다.
2) 답변 평가 (--answer, Claude 호출)
   - 인용률: 답변에 [n] 출처 표시가 있는 비율
   - 정답 키워드 포함률: expect_answer 의 단어가 모두 답변에 들어있는 비율 (적어둔 질문만)

예)
    python eval/run_eval.py --rcept 20260310000123
    python eval/run_eval.py --rcept 20260310000123 --answer
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag import DartRAG  # noqa: E402
from src.store import HybridStore  # noqa: E402


def retrieval_eval(store, qs, k, rcept_nos):
    print(f"\n[검색 평가] 질문 {len(qs)}개, 상위 {k}개 기준")
    print(f"{'방식':<8} {'Hit@k':>7} {'MRR':>6}")
    rows = {}
    for mode in ("vector", "keyword", "hybrid"):
        hits, rr = 0, 0.0
        for q in qs:
            res = store.search(q["question"], k=k, rcept_nos=rcept_nos, mode=mode)
            rank = next((i for i, h in enumerate(res, 1) if q["expect_section"] in h["metadata"]["section"]), None)
            hits += rank is not None
            rr += 1 / rank if rank else 0
        rows[mode] = (hits / len(qs), rr / len(qs))
        print(f"{mode:<8} {rows[mode][0]:>7.0%} {rows[mode][1]:>6.2f}")
    return rows


def answer_eval(store, qs, k, rcept_nos):
    rag = DartRAG(store)
    cited, kw_ok, kw_total, secs = 0, 0, 0, []
    print("\n[답변 평가]")
    for q in qs:
        t = time.time()
        a = rag.answer(q["question"], rcept_nos=rcept_nos, k=k)
        secs.append(time.time() - t)
        cited += bool(a.cited)
        if q.get("expect_answer"):
            kw_total += 1
            ok = all(w in a.text for w in q["expect_answer"])
            kw_ok += ok
        print(f"- {q['question']}\n  {a.text[:150].replace(chr(10), ' ')}...")
    print(f"\n인용률 {cited / len(qs):.0%} · 평균 응답 {sum(secs) / len(secs):.1f}초", end="")
    print(f" · 정답 키워드 포함 {kw_ok}/{kw_total}" if kw_total else "")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--questions", default=str(Path(__file__).parent / "questions.json"))
    p.add_argument("--rcept", nargs="*", help="평가할 보고서 접수번호 (비우면 전체)")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--answer", action="store_true", help="Claude 답변까지 평가 (API 비용 발생)")
    a = p.parse_args()

    qs = json.loads(Path(a.questions).read_text(encoding="utf-8"))
    store = HybridStore()
    if store.col.count() == 0:
        sys.exit("색인이 비어 있습니다. 먼저 python ingest.py 회사명 으로 수집하세요.")
    retrieval_eval(store, qs, a.k, a.rcept)
    if a.answer:
        answer_eval(store, qs, a.k, a.rcept)


if __name__ == "__main__":
    main()
