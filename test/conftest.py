"""pytest 공통 설정 — backend/ 를 import 경로에 올리고, 데이터 폴더를 임시 경로로 돌린다."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

CFG1 = os.path.join(ROOT, "config", "chamber1.example.json")
CFG2 = os.path.join(ROOT, "config", "chamber2.example.json")


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """★ 테스트가 진짜 data/ 폴더에 레시피를 쓰면 개발자 환경이 오염된다.
    DATA_ROOT 를 임시 폴더로 바꾸고 챔버 폴더를 다시 만든다."""
    import paths
    monkeypatch.setattr(paths, "DATA_ROOT", str(tmp_path))
    paths.init_chamber("test")
    yield


@pytest.fixture
def cfg1():
    import config
    cfg, _ = config.load(CFG1)
    return cfg


@pytest.fixture
def cfg2():
    import config
    cfg, _ = config.load(CFG2)
    return cfg
