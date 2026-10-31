"""DART 공시 분석 챗봇 (Streamlit).  실행: streamlit run app.py"""
import hmac
import re

import pandas as pd
import streamlit as st

from src import config, feedback, guard
from src.analysis import compare, report, to_html
from src.dart_client import DartClient, DartError
from src.financials import fmt_krw, get_financials, to_text
from src.ingest import ingest_company
from src.rag import DartRAG, cited_numbers
from src.store import HybridStore
from src.usage import Usage

st.set_page_config(page_title="DART 공시 분석 챗봇", page_icon="📑", layout="wide")

# ---------------- 비밀번호 (배포용, APP_PASSWORD 가 있을 때만) ----------------
if config.APP_PASSWORD and not st.session_state.get("authed"):
    st.title("📑 DART 공시 분석 챗봇")
    st.caption("포트폴리오 데모입니다. 안내받은 비밀번호를 입력해 주세요.")
    pw = st.text_input("비밀번호", type="password")
    if pw:
        if hmac.compare_digest(pw, config.APP_PASSWORD):
            st.session_state.authed = True
            st.rerun()
        st.error("비밀번호가 맞지 않습니다.")
    st.stop()


def allow_claude() -> bool:
    """하루 사용 한도 확인. 넘었으면 안내하고 False."""
    if guard.try_consume():
        return True
    st.warning(f"오늘 데모 사용 한도({config.DAILY_LIMIT}회)를 모두 썼어요. 내일 다시 이용해 주세요.")
    return False


# ---------------- 공용 객체 ----------------
@st.cache_resource(show_spinner="검색 엔진 준비 중...")
def get_store() -> HybridStore:
    return HybridStore()


@st.cache_resource
def get_rag() -> DartRAG:
    return DartRAG(get_store())


@st.cache_resource
def get_dart():
    try:
        return DartClient()
    except DartError:
        return None


@st.cache_data(show_spinner="상장사 목록 불러오는 중...")
def corp_options() -> list[str]:
    return sorted(f"{c.corp_name} ({c.stock_code})" for c in get_dart().load_corps())


@st.cache_data(ttl=86400, show_spinner="재무제표 불러오는 중...")
def financials(company: str, latest_year: int | None, corp_code: str = ""):
    dart = get_dart()
    code = corp_code or dart.find_corp(company).corp_code   # 예전에 색인한 보고서는 이름으로 찾는다
    return get_financials(dart, code, latest_year)


def annual_year(doc_name: str) -> int | None:
    """'사업보고서 (2025.12)' → 2025, '반기보고서 (2026.06)' → 2025 (직전 사업연도)"""
    m = re.search(r"\((\d{4})\.\d{2}\)", doc_name)
    if not m:
        return None
    y = int(m.group(1))
    return y if "사업보고서" in doc_name else y - 1


def fin_or_none(company: str, year: int | None):
    try:
        return financials(company, year, latest.get(company, {}).get("corp_code", ""))
    except Exception as e:  # 재무 API 문제로 다른 기능까지 멈추지 않게
        st.warning(f"{company} 재무제표를 불러오지 못했습니다: {e}")
        return pd.DataFrame(), "연결"


store, dart = get_store(), get_dart()
ss = st.session_state
ss.setdefault("messages", [])        # {"role","content","sources","usage","question","rated"}
ss.setdefault("total_usage", Usage())
ss.setdefault("compare_result", None)
ss.setdefault("report_results", {})

reports = store.list_reports()
latest: dict[str, dict] = {}          # 회사별 가장 최근 보고서
for r in reports:
    if r["company"] not in latest or r["rcept_dt"] > latest[r["company"]]["rcept_dt"]:
        latest[r["company"]] = r
companies = sorted(latest)


def show_sources(sources: list[dict], answer: str):
    used = cited_numbers(answer, len(sources))
    with st.expander(f"📎 근거 공시 {len(used)}개 (검색 {len(sources)}개)"):
        for i, h in enumerate(sources, 1):
            m = h["metadata"]
            mark = "✅" if i in used else "▫️"
            st.markdown(f'{mark} **[{i}] {m["company"]} · {m["doc_name"]}**  \n'
                        f'`{m["section"]}` · [원문 보기]({m["url"]})')
            body = h["text"].split("\n", 1)[-1]
            st.text(body[:600] + ("..." if len(body) > 600 else ""))


# ---------------- 사이드바 ----------------
with st.sidebar:
    st.header("📥 공시 수집")
    missing = [k for k, v in {"DART_API_KEY": config.DART_API_KEY,
                              "ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY}.items() if not v]
    if missing:
        st.error(f".env 에 {', '.join(missing)} 를 설정하세요.")

    target = None
    if dart:
        try:
            pick = st.selectbox("회사 검색", corp_options(), index=None,
                                placeholder="회사명 일부만 입력해도 돼요 (예: 삼성)")
            target = pick.rsplit("(", 1)[1].rstrip(")") if pick else None   # 종목코드
        except Exception as e:
            st.caption(f"목록을 불러오지 못해 직접 입력합니다. ({e})")
            target = st.text_input("회사명 또는 종목코드") or None
    c1, c2 = st.columns(2)
    report_type = c1.selectbox("보고서", ["사업보고서", "반기보고서", "분기보고서"])
    limit = c2.number_input("최근 몇 건", 1, 4, 1)
    if st.button("수집하기", width="stretch", disabled=not target):
        with st.status("공시 수집 중... (처음엔 몇 분 걸려요)") as s:
            try:
                ingest_company(target, store, report_type, years=3, limit=int(limit), log=st.write)
                s.update(label="수집 완료", state="complete")
                st.rerun()
            except DartError as e:
                s.update(label=f"실패: {e}", state="error")

    st.divider()
    st.header("🔎 질문 범위")
    labels = {f'{r["company"]} · {r["doc_name"]}': r["rcept_no"] for r in reports}
    picked = st.multiselect("질문할 보고서 (비우면 전체)", list(labels))
    scope = [labels[p] for p in picked] or None
    top_k = st.slider("참고할 발췌문 수", 3, 12, config.TOP_K)

    st.divider()
    st.header("📈 사용 현황")
    tu = ss.total_usage
    n_fb, n_up = feedback.stats()
    m1, m2 = st.columns(2)
    m1.metric("이번 사용 비용", f"₩{tu.cost_krw:,.0f}", help=f"Claude 호출 {tu.calls}회 · "
              f"입력 {tu.input_tokens:,} / 출력 {tu.output_tokens:,} 토큰")
    m2.metric("답변 만족도", f"{n_up / n_fb:.0%}" if n_fb else "-", help=f"평가 {n_fb}건 중 👍 {n_up}건")
    st.caption(f"색인된 보고서 {len(reports)}건 · 청크 {sum(r['chunks'] for r in reports):,}개")
    left = guard.remaining()
    if left is not None:
        st.caption(f"오늘 남은 데모 사용 횟수: {left}/{config.DAILY_LIMIT}")
    if store.mode == "keyword":
        st.caption("⚡ 가벼운 모드: 무료 서버 메모리 한도 때문에 키워드 검색만 사용 중입니다.")
    if st.button("대화 지우기", width="stretch"):
        ss.messages = []
        st.rerun()

# ---------------- 본문 ----------------
st.title("📑 DART 공시 분석 챗봇")
st.caption("전자공시 원문과 재무제표를 바탕으로 Claude 가 출처와 함께 답합니다. 투자 권유가 아닌 공시 내용 요약입니다.")
if not reports:
    st.info("왼쪽에서 회사를 검색해 사업보고서를 먼저 수집해 주세요.")

tab_chat, tab_fin, tab_cmp, tab_rep = st.tabs(["💬 질문하기", "📊 실적 그래프", "⚖️ 회사 비교", "📄 요약 리포트"])

# ===== 💬 질문하기 =====
def _save_feedback(i: int):
    val = ss.get(f"fb_{i}")
    msg = ss.messages[i]
    if val is not None and not msg.get("rated"):
        feedback.save(msg.get("question", ""), msg["content"], val,
                      {"sources": [h["metadata"]["section"] for h in msg.get("sources", [])]})
        msg["rated"] = True


with tab_chat:
    examples = ["주요 제품별 매출 비중은?", "올해 영업이익이 변한 이유는?", "진행 중인 소송이나 우발채무가 있어?",
                "연구개발비는 매출 대비 몇 %야?"]
    cols = st.columns(len(examples))
    clicked = next((q for c, q in zip(cols, examples) if c.button(q, width="stretch")), None)

    box = st.container()
    with box:
        for i, msg in enumerate(ss.messages):
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg["role"] == "assistant":
                    if msg.get("sources"):
                        show_sources(msg["sources"], msg["content"])
                    c_a, c_b = st.columns([1, 5])
                    with c_a:
                        st.feedback("thumbs", key=f"fb_{i}", on_change=_save_feedback, args=(i,),
                                    disabled=msg.get("rated", False))
                    if msg.get("usage"):
                        c_b.caption(msg["usage"])

    question = st.chat_input("공시에 대해 물어보세요") or clicked
    if question and reports and allow_claude():
        history = [{"role": m["role"], "content": m["content"]} for m in ss.messages]
        ss.messages.append({"role": "user", "content": question})
        rag = get_rag()
        with box:
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                with rag.tracker.track() as u:
                    with st.spinner("공시 검색 중..."):
                        search_q, hits = rag.retrieve(question, history, scope, top_k)
                    if hits:
                        answer = st.write_stream(rag.stream(question, hits, history))
                    else:
                        answer = "관련된 공시 내용을 찾지 못했습니다."
                        st.markdown(answer)
        ss.messages.append({"role": "assistant", "content": answer, "sources": hits,
                            "usage": u.summary(), "question": question})
        ss.total_usage.merge(u)
        st.rerun()

# ===== 📊 실적 그래프 =====
with tab_fin:
    if not companies:
        st.info("수집한 회사가 있어야 그래프를 볼 수 있어요.")
    elif not dart:
        st.error("DART_API_KEY 가 필요합니다.")
    else:
        comp = st.selectbox("회사", companies, key="fin_company")
        df, basis = fin_or_none(comp, annual_year(latest[comp]["doc_name"]))
        if df.empty:
            st.warning("재무 데이터를 찾지 못했습니다. (금융업 등 일부 회사는 계정 이름이 달라 비어 있을 수 있어요)")
        else:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else None

            def delta(col):
                if prev is None or col not in df or pd.isna(prev.get(col)) or not prev.get(col):
                    return None
                return f"{(last[col] - prev[col]) / abs(prev[col]) * 100:+.1f}%"

            st.subheader(f"{comp} · {df.index[-1]}년 실적 ({basis})")
            k = st.columns(4)
            k[0].metric("매출액", fmt_krw(last.get("매출액")), delta("매출액"))
            k[1].metric("영업이익", fmt_krw(last.get("영업이익")), delta("영업이익"))
            k[2].metric("영업이익률", f'{last.get("영업이익률(%)", float("nan")):.1f}%',
                        f'{last["영업이익률(%)"] - prev["영업이익률(%)"]:+.1f}%p' if prev is not None and "영업이익률(%)" in df else None)
            k[3].metric("부채비율", f'{last.get("부채비율(%)", float("nan")):.1f}%' if "부채비율(%)" in df else "-",
                        delta_color="inverse",
                        delta=f'{last["부채비율(%)"] - prev["부채비율(%)"]:+.1f}%p' if prev is not None and "부채비율(%)" in df else None)

            money = [c for c in ["매출액", "영업이익", "당기순이익"] if c in df]
            big = df[money].abs().max().max() >= 1e12
            unit, div = ("조 원", 1e12) if big else ("억 원", 1e8)
            chart = (df[money] / div).round(2)
            chart.index = chart.index.astype(str)
            st.bar_chart(chart, stack=False, y_label=unit, x_label="연도", height=320)

            ratios = [c for c in ["영업이익률(%)", "순이익률(%)"] if c in df]
            if ratios:
                r = df[ratios].copy()
                r.index = r.index.astype(str)
                st.line_chart(r, y_label="%", x_label="연도", height=240)

            with st.expander("숫자로 보기"):
                view = pd.DataFrame(index=df.index.astype(str))
                for c in df.columns:   # 표 안을 모두 글자로 통일해야 화면에 표시된다
                    view[c] = [fmt_krw(v) if "(%)" not in c else ("-" if pd.isna(v) else f"{v:.1f}%")
                               for v in df[c]]
                st.dataframe(view.T, width="stretch")
            st.caption(f"출처: DART 재무제표 API ({basis} 기준). AI가 읽은 숫자가 아니라 금융감독원이 제공하는 공식 수치예요.")

# ===== ⚖️ 회사 비교 =====
with tab_cmp:
    if len(companies) < 2:
        st.info("회사를 2개 이상 수집하면 비교할 수 있어요. (예: 삼성전자, SK하이닉스)")
    else:
        sel = st.multiselect("비교할 회사 (2~3개)", companies, default=companies[:2], max_selections=3)
        if st.button("⚖️ 비교하기", disabled=len(sel) < 2, type="primary") and allow_claude():
            dfs, texts = {}, []
            for c in sel:
                d, b = fin_or_none(c, annual_year(latest[c]["doc_name"]))
                dfs[c] = d
                texts.append(to_text(d, c, b))
            with st.spinner("회사별 공시를 모아 비교하는 중... (30초~1분)"):
                res = compare(get_rag(), [{"company": c, "rcept_no": latest[c]["rcept_no"]} for c in sel], texts)
            ss.compare_result = {"companies": sel, "res": res, "dfs": dfs}
            ss.total_usage.merge(res.usage)

        cr = ss.compare_result
        if cr:
            res = cr["res"]
            margin = pd.DataFrame({c: d["영업이익률(%)"] for c, d in cr["dfs"].items()
                                   if not d.empty and "영업이익률(%)" in d})
            if not margin.empty:
                margin.index = margin.index.astype(str)
                st.markdown("**영업이익률 추이 (%)** · DART 재무제표 API")
                st.line_chart(margin, height=240)
            st.markdown(res.text)
            show_sources(res.sources, res.text)
            st.caption(res.usage.summary())

# ===== 📄 요약 리포트 =====
with tab_rep:
    if not companies:
        st.info("수집한 회사가 있어야 리포트를 만들 수 있어요.")
    else:
        comp = st.selectbox("회사", companies, key="rep_company")
        if st.button("📄 리포트 만들기", type="primary") and allow_claude():
            d, b = fin_or_none(comp, annual_year(latest[comp]["doc_name"]))
            with st.spinner("공시를 읽고 리포트를 쓰는 중... (30초~1분)"):
                res = report(get_rag(), comp, latest[comp]["rcept_no"], to_text(d, comp, b))
            ss.report_results[comp] = res
            ss.total_usage.merge(res.usage)

        res = ss.report_results.get(comp)
        if res:
            st.download_button("📥 리포트 내려받기 (HTML → 브라우저에서 Ctrl+P 로 PDF 저장)",
                               data=to_html(f"{comp} 기업 요약", res.text, res.sources, res.cited),
                               file_name=f"{comp}_기업요약.html", mime="text/html")
            st.markdown(res.text)
            show_sources(res.sources, res.text)
            st.caption(res.usage.summary())
