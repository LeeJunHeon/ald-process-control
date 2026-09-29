"""
paths.py — 실행 환경별 경로 해석 (개발 / PyInstaller exe).

PyInstaller로 묶으면 폴더가 둘로 갈라진다.
  BUNDLE_ROOT : 번들 자원(읽기 전용). exe 안에 묶여 임시 폴더에 풀린다. → frontend/, assets/
  DATA_ROOT   : 사용자 데이터(쓰기). exe가 놓인 폴더에 영구 보존된다.
                → config.json, data/{logs,alarms,datalog,recipes}

개발 환경에서는 둘 다 이 프로그램 폴더(PEALD/)로 같은 값이 된다.
★ 새 파일 경로를 추가할 때는 반드시 둘 중 어디에 속하는지 먼저 판단할 것.

★ POWDERALD 와 데이터 폴더를 공유하지 않는다. 두 장비의 로그·알람 이력이 섞이면
  어느 장비의 사건인지 구분할 수 없어 사고 원인을 추적할 수 없다.
"""
import os
import sys

if getattr(sys, "frozen", False):          # PyInstaller 실행 중
    # onefile: _MEIPASS = 임시 압축해제 폴더 / onedir: _MEIPASS = <exe폴더>/_internal
    BUNDLE_ROOT = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    DATA_ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:                                       # 개발 환경 — 프로그램 폴더
    _PKG_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_ROOT = os.path.dirname(_PKG_DIR)
    DATA_ROOT = BUNDLE_ROOT

# --- 읽기 전용 자원 (BUNDLE_ROOT) ---
FRONTEND_DIR = os.path.join(BUNDLE_ROOT, "frontend")
INDEX_PATH = os.path.join(FRONTEND_DIR, "index.html")
ASSETS_DIR = os.path.join(BUNDLE_ROOT, "assets")
EXAMPLE_CONFIG = os.path.join(BUNDLE_ROOT, "config", "config.example.json")

# --- 쓰기 대상 (DATA_ROOT) ---
DEFAULT_CONFIG_PATH = os.path.join(DATA_ROOT, "config.json")
DATA_DIR = os.path.join(DATA_ROOT, "data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
ALARMS_DIR = os.path.join(DATA_DIR, "alarms")
DATALOG_DIR = os.path.join(DATA_DIR, "datalog")
RECIPES_DIR = os.path.join(DATA_DIR, "recipes")

# 폴더 생성 실패 사유. ★ logger를 import하면 순환이라 여기 보관만 하고,
#   server.py가 기동 시 읽어 로그로 남긴다.
DATA_DIR_ERROR = ""


def set_data_root(path: str):
    """테스트가 데이터 폴더를 임시 경로로 돌릴 때 쓴다.
    ★ 테스트가 진짜 data/ 에 쓰면 개발자 환경이 오염된다."""
    global DATA_ROOT, DEFAULT_CONFIG_PATH, DATA_DIR, LOGS_DIR, ALARMS_DIR, DATALOG_DIR, RECIPES_DIR
    DATA_ROOT = path
    DEFAULT_CONFIG_PATH = os.path.join(path, "config.json")
    DATA_DIR = os.path.join(path, "data")
    LOGS_DIR = os.path.join(DATA_DIR, "logs")
    ALARMS_DIR = os.path.join(DATA_DIR, "alarms")
    DATALOG_DIR = os.path.join(DATA_DIR, "datalog")
    RECIPES_DIR = os.path.join(DATA_DIR, "recipes")


def ensure_dirs() -> bool:
    """데이터 폴더를 만든다. 권한이 없으면 False.
    ★ 기동 시점에 예외로 죽으면 프로그램이 아예 안 뜨므로 절대 raise 하지 않는다."""
    global DATA_DIR_ERROR
    try:
        for d in (LOGS_DIR, ALARMS_DIR, DATALOG_DIR, RECIPES_DIR):
            os.makedirs(d, exist_ok=True)
        DATA_DIR_ERROR = ""
        return True
    except Exception as e:  # noqa: BLE001
        DATA_DIR_ERROR = f"데이터 폴더 생성 실패: {DATA_DIR} ({e})"
        return False


def check_writable():
    """실제로 쓸 수 있는지 확인한다.
    ★ makedirs 성공만으로는 부족하다 — 폴더가 이미 있으면 읽기 전용이어도 통과한다."""
    probe = os.path.join(DATA_DIR, ".write_test.tmp")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            os.remove(probe)
        except Exception:  # noqa: BLE001
            pass


def asset(name: str) -> str:
    return os.path.join(ASSETS_DIR, name)
