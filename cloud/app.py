"""Streamlit Community Cloud 배포용 시작 파일.

무료 서버는 메모리가 작아서, 같은 폴더의 가벼운 requirements.txt(임베딩 모델 제외)로 설치하고
키워드 검색 모드로 원래 app.py 를 실행한다. Streamlit Cloud 의 'Main file path' 에 cloud/app.py 를 지정.

서버의 저장소 폴더는 데이터베이스가 쓰기를 못 할 수 있어서(Chroma 시작 실패),
저장소에 올린 data/ 를 쓰기 가능한 임시 폴더로 한 번 복사해서 연다.
"""
import os
import runpy
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SEARCH_MODE", "keyword")

work = Path(tempfile.gettempdir()) / "dart-rag-data"
if not (work / "chroma" / "chroma.sqlite3").exists() and (ROOT / "data" / "chroma").exists():
    shutil.copytree(ROOT / "data", work, dirs_exist_ok=True)
work.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATA_DIR", str(work))

runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
