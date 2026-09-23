"""
paths.py — 실행 환경별 경로 해석 (개발 / PyInstaller exe) + 챔버별 데이터 폴더.

PyInstaller로 묶으면 폴더가 둘로 갈라진다.
  BUNDLE_ROOT : 번들 자원(읽기 전용). exe 안에 묶여 임시 폴더에 풀린다.  → frontend/
  DATA_ROOT   : 사용자 데이터(쓰기). exe가 놓인 폴더에 영구 보존된다.
                → config.json, data/<챔버id>/{logs,recipes,datalog}

개발 환경에서는 둘 다 프로젝트 루트로 같은 값이 된다(동작 동일).
★ 새 파일 경로를 추가할 때는 반드시 둘 중 어디에 속하는지 먼저 판단할 것.

★ 챔버 분리: 데이터 폴더는 반드시 챔버 id 아래에 둔다. 두 챔버가 같은 exe를 쓰더라도
  레시피·로그가 섞이면 한쪽의 실수가 다른 쪽 공정을 망친다. init_chamber()를
  부르기 전에는 폴더 경로가 확정되지 않는다(서버 기동 초반에 config 로드 직후 부른다).
"""
import os
import sys

if getattr(sys, "frozen", False):          # PyInstaller 실행 중
    # onefile: _MEIPASS = 임시 압축해제 폴더 / onedir: _MEIPASS = <exe폴더>/_internal
    BUNDLE_ROOT = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    DATA_ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:                                       # 개발 환경 — 둘이 같다
    _BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_ROOT = os.path.dirname(_BACKEND_DIR)
    DATA_ROOT = BUNDLE_ROOT

# --- 읽기 전용 자원(BUNDLE_ROOT) ---
FRONTEND_DIR = os.path.join(BUNDLE_ROOT, "frontend")
INDEX_PATH = os.path.join(FRONTEND_DIR, "index.html")

# --- 쓰기 대상(DATA_ROOT) ---
# --config 인자가 없을 때 읽는 기본 설정 파일. 납품 시 exe 옆에 둔다.
DEFAULT_CONFIG_PATH = os.path.join(DATA_ROOT, "config.json")

# 챔버별 폴더. init_chamber()가 채운다.
CHAMBER_ID = ""
CHAMBER_DIR = ""
LOGS_DIR = ""
RECIPES_DIR = ""
DATALOG_DIR = ""

# 폴더 생성 실패 사유. ★ logger를 import하면 순환(logger→paths)이라 여기 보관만 하고,
#   server.py가 기동 시 읽어 로그로 남긴다.
DATA_DIR_ERROR = ""


def data_path(*parts: str) -> str:
    """DATA_ROOT 기준 경로. 상대경로 설정을 절대경로로 바꿀 때 쓴다."""
    return os.path.join(DATA_ROOT, *parts)


def init_chamber(chamber_id: str) -> bool:
    """챔버별 데이터 폴더를 확정하고 만든다. 권한이 없으면 False.
    ★ import/기동 시점에 예외로 죽으면 프로그램이 아예 안 뜨므로 절대 raise하지 않는다."""
    global CHAMBER_ID, CHAMBER_DIR, LOGS_DIR, RECIPES_DIR, DATALOG_DIR, DATA_DIR_ERROR
    CHAMBER_ID = chamber_id or "chamber"
    CHAMBER_DIR = os.path.join(DATA_ROOT, "data", CHAMBER_ID)
    LOGS_DIR = os.path.join(CHAMBER_DIR, "logs")
    RECIPES_DIR = os.path.join(CHAMBER_DIR, "recipes")
    DATALOG_DIR = os.path.join(CHAMBER_DIR, "datalog")
    try:
        for d in (LOGS_DIR, RECIPES_DIR, DATALOG_DIR):
            os.makedirs(d, exist_ok=True)
        DATA_DIR_ERROR = ""
        return True
    except Exception as e:  # noqa: BLE001
        DATA_DIR_ERROR = f"데이터 폴더 생성 실패: {CHAMBER_DIR} ({e})"
        return False


def check_writable():
    """데이터 폴더에 실제로 쓸 수 있는지 확인한다.
    ★ makedirs 성공만으로는 부족하다 — 폴더가 이미 있으면 읽기 전용이어도 통과한다.
    반환: (가능여부, 문제 설명). 예외를 던지지 마라."""
    base = CHAMBER_DIR or DATA_ROOT
    probe = os.path.join(base, ".write_test.tmp")
    try:
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
