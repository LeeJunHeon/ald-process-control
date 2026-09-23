"""
storage.py — 레시피 파일 I/O.

- atomic_write_json: 임시 파일에 쓰고 rename → 원자적 저장(중간에 죽어도 파일이 안 깨진다).
- safe_read_json   : 읽기 실패 시 예외로 죽지 않고 None.
- valid_recipe_name: 경로 탈출·Windows 금지문자 차단.

경로는 paths.py 가 해석한다(개발/exe 환경 + 챔버별 폴더). 여기서 직접 조립하지 않는다.
"""

import os
import json
from typing import Any

import logger
import paths


def atomic_write_json(path: str, obj: Any) -> None:
    """임시 파일에 쓰고 rename → 원자적 저장."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def safe_read_json(path: str):
    """읽기 실패 시 예외로 죽지 않고 None 반환."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # 첫 실행에는 레시피가 없는 게 정상이다 — 오류로 보이면 안 되므로 조용히 None.
        return None
    except Exception as e:  # noqa: BLE001
        logger.early("warn", f"JSON 읽기 실패: {os.path.basename(path)} ({e})")
        return None


# Windows 파일명 제약. 리눅스에서는 통과하는 이름이 Windows에서만 저장에 실패해
# 원인을 찾기 어려우므로, 플랫폼과 무관하게 같은 규칙으로 거른다.
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL",
                 *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_WIN_BAD_CHARS = set('<>:"|?*') | {chr(i) for i in range(32)}


def valid_recipe_name(name: str) -> bool:
    """슬래시/역슬래시/상위경로 금지, 레시피 폴더 밖 금지,
    Windows 금지문자·예약어·끝 공백/점·과도한 길이 금지."""
    if not name or not isinstance(name, str):
        return False
    if "/" in name or "\\" in name or ".." in name or name != os.path.basename(name):
        return False
    if any(ch in _WIN_BAD_CHARS for ch in name):
        return False
    if name != name.rstrip(" ."):        # Windows가 끝 공백·점을 잘라 파일명이 달라진다
        return False
    if name.split(".")[0].upper() in _WIN_RESERVED:   # 'CON.old' 같은 형태도 예약된다
        return False
    if len(name) > 80:
        return False
    base = paths.RECIPES_DIR or paths.data_path("recipes")
    target = os.path.abspath(os.path.join(base, name + ".json"))
    return os.path.dirname(target) == os.path.abspath(base)


def recipe_path(name: str) -> str:
    base = paths.RECIPES_DIR or paths.data_path("recipes")
    return os.path.join(base, name + ".json")


def list_recipes() -> list:
    try:
        files = os.listdir(paths.RECIPES_DIR or paths.data_path("recipes"))
    except Exception:  # noqa: BLE001
        return []
    names = [f[:-5] for f in files if f.endswith(".json")]
    names.sort()
    return names


def load_recipe(name: str):
    if not valid_recipe_name(name):
        return None
    return safe_read_json(recipe_path(name))


def save_recipe(name: str, recipe: dict) -> bool:
    if not valid_recipe_name(name):
        return False
    try:
        atomic_write_json(recipe_path(name), recipe)
        return True
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"레시피 저장 실패: {name} ({e})")
        return False


def delete_recipe(name: str) -> bool:
    if not valid_recipe_name(name):
        return False
    try:
        os.remove(recipe_path(name))
        return True
    except FileNotFoundError:
        return False
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"레시피 삭제 실패: {name} ({e})")
        return False
