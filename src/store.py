"""하이브리드 검색 저장소: 벡터 검색(Chroma) + 키워드 검색(BM25) → RRF 로 합치기.

왜 하이브리드인가?
- 벡터 검색은 "매출이 줄어든 이유" 같은 의미 질문에 강하다.
- 공시는 '영업이익', 'HBM', '2025년' 같은 정확한 용어·숫자가 핵심이라 키워드 검색이 더 잘 맞는 경우가 많다.
둘을 Reciprocal Rank Fusion 으로 합치면 한쪽만 쓸 때보다 안정적이다. (eval/ 에서 비교 가능)
"""
from __future__ import annotations

import re
from typing import Protocol

import chromadb
from rank_bm25 import BM25Okapi

from . import config
from .chunker import Chunk

TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣.%]+")


def tokenize(text: str) -> list[str]:
    """형태소 분석기 없이 쓰는 한국어 토크나이저: 어절 + 글자 2-gram.
    '영업이익이' 와 '영업이익' 이 2-gram('영업','업이','이익')으로 겹치게 된다."""
    tokens = []
    for w in TOKEN_RE.findall(text.lower()):
        tokens.append(w)
        if len(w) > 2 and re.search(r"[가-힣]", w):
            tokens.extend(w[i:i + 2] for i in range(len(w) - 1))
    return tokens


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = config.EMBED_MODEL):
        self.model_name = model_name
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # 무거운 import 는 필요할 때만
            self._model = SentenceTransformer(self.model_name)
        return self._model.encode(texts, normalize_embeddings=True, batch_size=32,
                                  show_progress_bar=len(texts) > 64).tolist()


class NullEmbedder:
    """가벼운 모드용: 임베딩 모델을 불러오지 않는다. (벡터 검색은 끄고 키워드 검색만 사용)
    새로 색인할 때는 자리만 채우는 고정 벡터를 넣는다. (0 벡터는 코사인 계산에서 0으로 나누기가 된다)"""

    def __init__(self, dim: int = 768):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]


class HybridStore:
    def __init__(self, embedder: Embedder | None = None, path: str | None = None,
                 collection: str = "dart", mode: str | None = None):
        self.mode = (mode or config.search_mode()) if embedder is None else (mode or "hybrid")
        self.client = chromadb.PersistentClient(path=str(path or config.CHROMA_DIR))
        self.col = self.client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}, embedding_function=None)
        if embedder is not None:
            self.embedder = embedder
        elif self.mode == "keyword":
            got = self.col.get(limit=1, include=["embeddings"])["embeddings"]
            self.embedder = NullEmbedder(len(got[0]) if got is not None and len(got) else 768)
        else:
            self.embedder = SentenceTransformerEmbedder()
        self._bm25_cache: tuple[int, list[str], list[str], list[dict], BM25Okapi] | None = None

    # ---------- 쓰기 ----------
    def add(self, chunks: list[Chunk], batch: int = 128) -> int:
        for i in range(0, len(chunks), batch):
            part = chunks[i:i + batch]
            self.col.upsert(
                ids=[c.id for c in part],
                documents=[c.text for c in part],
                metadatas=[c.metadata for c in part],
                embeddings=self.embedder.embed([c.text for c in part]),
            )
        self._bm25_cache = None
        return len(chunks)

    def has_report(self, rcept_no: str) -> bool:
        return bool(self.col.get(where={"rcept_no": rcept_no}, limit=1)["ids"])

    def delete_report(self, rcept_no: str) -> None:
        self.col.delete(where={"rcept_no": rcept_no})
        self._bm25_cache = None

    def list_reports(self) -> list[dict]:
        metas = self.col.get(include=["metadatas"])["metadatas"] or []
        seen = {}
        for m in metas:
            r = seen.setdefault(m["rcept_no"], {**{k: m[k] for k in ("rcept_no", "company", "doc_name", "rcept_dt", "url")},
                                               "corp_code": m.get("corp_code", ""), "chunks": 0})
            r["chunks"] += 1
        return sorted(seen.values(), key=lambda r: (r["company"], r["rcept_dt"]), reverse=False)

    # ---------- 검색 ----------
    def _bm25(self):
        n = self.col.count()
        if self._bm25_cache is None or self._bm25_cache[0] != n:
            data = self.col.get(include=["documents", "metadatas"])
            ids, docs, metas = data["ids"], data["documents"], data["metadatas"]
            bm25 = BM25Okapi([tokenize(d) for d in docs]) if docs else None
            self._bm25_cache = (n, ids, docs, metas, bm25)
        return self._bm25_cache

    def vector_search(self, query: str, n: int, rcept_nos: list[str] | None = None) -> list[str]:
        if self.col.count() == 0:
            return []
        where = {"rcept_no": {"$in": rcept_nos}} if rcept_nos else None
        res = self.col.query(query_embeddings=self.embedder.embed([query]), n_results=n, where=where)
        return res["ids"][0]

    def keyword_search(self, query: str, n: int, rcept_nos: list[str] | None = None) -> list[str]:
        _, ids, _, metas, bm25 = self._bm25()
        if bm25 is None:
            return []
        scores = bm25.get_scores(tokenize(query))
        allowed = set(rcept_nos) if rcept_nos else None
        ranked = sorted(
            (i for i in range(len(ids)) if allowed is None or metas[i]["rcept_no"] in allowed),
            key=lambda i: scores[i], reverse=True)
        return [ids[i] for i in ranked[:n] if scores[i] > 0]

    def search(self, query: str, k: int = config.TOP_K, rcept_nos: list[str] | None = None,
               mode: str | None = None, rrf_k: int = 60) -> list[dict]:
        """mode: 'hybrid' | 'vector' | 'keyword' (비우면 저장소 기본값)"""
        mode = mode or self.mode
        if self.mode == "keyword":
            mode = "keyword"   # 가벼운 모드에서는 벡터가 0 이라 키워드 검색만 의미가 있다
        pool = max(k * 4, 20)
        lists = []
        if mode in ("hybrid", "vector"):
            lists.append(self.vector_search(query, pool, rcept_nos))
        if mode in ("hybrid", "keyword"):
            lists.append(self.keyword_search(query, pool, rcept_nos))

        fused: dict[str, float] = {}
        for ranked in lists:
            for rank, cid in enumerate(ranked):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
        top = sorted(fused, key=fused.get, reverse=True)[:k]
        if not top:
            return []
        got = self.col.get(ids=top, include=["documents", "metadatas"])
        by_id = {i: (d, m) for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])}
        return [{"id": cid, "text": by_id[cid][0], "metadata": by_id[cid][1], "score": fused[cid]}
                for cid in top if cid in by_id]
