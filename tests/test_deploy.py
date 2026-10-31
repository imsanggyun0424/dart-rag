"""배포용 기능 테스트: 가벼운 모드, 하루 한도, 비밀번호."""
from src import config, guard
from src.chunker import chunk_document
from src.parser import parse_document
from src.store import HybridStore, NullEmbedder
from tests.test_pipeline import FIXTURE, FakeEmbedder


def test_keyword_mode_reuses_existing_index_without_model(tmp_path):
    path = tmp_path / "chroma"
    full = HybridStore(embedder=FakeEmbedder(), path=path)          # 로컬에서 만든 색인 (256차원)
    full.add(chunk_document(parse_document(FIXTURE.read_bytes()), "R1", "20260301"))
    del full
    light = HybridStore(path=path, mode="keyword")                  # 배포 서버에서 다시 열기
    assert isinstance(light.embedder, NullEmbedder) and light.embedder.dim == 256
    hits = light.search("특허 소송 우발부채", k=3, mode="hybrid")    # hybrid 를 요청해도 키워드로
    assert any("우발부채" in h["metadata"]["section"] for h in hits)
    # 서버에서 새 회사를 수집해도 에러 없이 들어간다
    doc = parse_document(FIXTURE.read_bytes())
    doc.company = "새회사"
    light.add(chunk_document(doc, "R2", "20260301"))
    assert light.search("HBM", rcept_nos=["R2"])


def test_daily_limit(tmp_path):
    p = tmp_path / "daily.json"
    assert guard.remaining(0, p) is None and guard.try_consume(0, p)   # 0 = 무제한
    p.unlink()
    assert guard.remaining(2, p) == 2
    assert guard.try_consume(2, p) and guard.try_consume(2, p)
    assert not guard.try_consume(2, p) and guard.remaining(2, p) == 0
