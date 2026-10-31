"""Streamlit Community Cloud 배포용 시작 파일.

무료 서버는 메모리가 작아서, 같은 폴더의 가벼운 requirements.txt(임베딩 모델 제외)로 설치하고
키워드 검색 모드로 원래 app.py 를 실행한다. Streamlit Cloud 의 'Main file path' 에 cloud/app.py 를 지정.
"""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SEARCH_MODE", "keyword")

runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
