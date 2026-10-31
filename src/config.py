"""프로젝트 전역 설정. 값은 .env 파일이나 환경변수로 덮어쓸 수 있다."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Streamlit Cloud 에서는 키를 .env 대신 'Secrets' 에 넣는다. 그 값을 환경변수로 옮겨 같은 방식으로 읽는다.
try:
    import streamlit as _st
    for _k, _v in _st.secrets.items():
        if isinstance(_v, (str, int, float, bool)):
            os.environ.setdefault(_k, str(_v))
except Exception:   # 로컬에 secrets 파일이 없으면 그냥 넘어간다
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"          # DART에서 받은 원문 ZIP/XML 캐시
CHROMA_DIR = DATA_DIR / "chroma"    # 벡터DB 저장 위치
CORP_CODE_PATH = DATA_DIR / "CORPCODE.xml"
FIN_DIR = DATA_DIR / "fin"          # 재무제표 API 응답 캐시
FEEDBACK_PATH = DATA_DIR / "feedback.jsonl"
DAILY_USAGE_PATH = DATA_DIR / "daily_usage.json"

DART_API_KEY = os.getenv("DART_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

# 비용 계산용 (Claude Sonnet 5.5 정가: 100만 토큰당 입력 $2, 출력 $10). 모델을 바꾸면 같이 바꾸세요.
PRICE_INPUT_PER_MTOK = float(os.getenv("PRICE_INPUT_PER_MTOK", 2))
PRICE_OUTPUT_PER_MTOK = float(os.getenv("PRICE_OUTPUT_PER_MTOK", 10))
USD_KRW = float(os.getenv("USD_KRW", 1400))   # 대략적인 원화 환산용 환율

# 한국어 문장 임베딩 모델 (Hugging Face). 첫 실행 때 자동 다운로드된다.
EMBED_MODEL = os.getenv("EMBED_MODEL", "jhgan/ko-sroberta-multitask")

# 청킹 / 검색 파라미터
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 800))       # 글자 수 기준
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 150))
TOP_K = int(os.getenv("TOP_K", 6))

# 검색 방식: hybrid(기본, 임베딩 모델 필요) | keyword(가벼운 모드, 무료 서버 배포용)
SEARCH_MODE = os.getenv("SEARCH_MODE", "hybrid").lower()


def search_mode() -> str:
    """실행 시점의 검색 방식 (배포 시작 파일이 나중에 환경변수를 바꿔도 반영되도록 매번 읽는다)"""
    return os.getenv("SEARCH_MODE", SEARCH_MODE).lower()

# 배포용 안전장치: 비밀번호(비우면 없음), 하루 Claude 사용 횟수 한도(0 이면 무제한)
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", 0))

for d in (DATA_DIR, RAW_DIR, CHROMA_DIR, FIN_DIR):
    d.mkdir(parents=True, exist_ok=True)
