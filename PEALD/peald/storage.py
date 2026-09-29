"""
storage.py — 레시피 파일 I/O.

- atomic_write_json: 임시 파일에 쓰고 rename → 원자적 저장(중간에 죽어도 파일이 안 깨진다)
- valid_name: 경로 탈출·Windows 금지문자·예약어 차단
- 다른 장비 형식의 레시피는 목록에도 넣지 않는다 — 밸브 이름이 같아 보여도 배관이 달라
  그대로 실행하면 엉뚱한 곳을 연다.
"""

import os
import json

from . import logger
from . import paths
from . import device as DEV

# Windows 파일명 제약. 리눅스에서는 통과하는 이름이 Windows 에서만 저장에 실패해
# 원인을 찾기 어려우므로, 플랫폼과 무관하게 같은 규칙으로 거른다.
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL",
                 *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_WIN_BAD = set('<>:"|?*/\\') | {chr(i) for i in range(32)}


def valid_name(name) -> bool:
    if not name or not isinstance(name, str):
        return False
    if ".." in name or name != os.path.basename(name):
        return False
    if any(ch in _WIN_BAD for ch in name):
        return False
    if name != name.rstrip(" ."):          # Windows 가 끝 공백·점을 잘라 이름이 달라진다
        return False
    if name.split(".")[0].upper() in _WIN_RESERVED:
        return False
    if len(name) > 80:
        return False
    target = os.path.abspath(os.path.join(paths.RECIPES_DIR, name + ".json"))
    return os.path.dirname(target) == os.path.abspath(paths.RECIPES_DIR)


def path_of(name: str) -> str:
    return os.path.join(paths.RECIPES_DIR, name + ".json")


def atomic_write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        logger.write("warn", f"레시피 읽기 실패: {os.path.basename(path)} ({e})")
        return None


def list_recipes() -> list:
    """이 장비 형식의 레시피만. [{name, memo, modified, number}]"""
    from .recipe import recipe_number
    out = []
    try:
        files = sorted(os.listdir(paths.RECIPES_DIR))
    except Exception:  # noqa: BLE001
        return out
    for f in files:
        if not f.endswith(".json"):
            continue
        data = read_json(os.path.join(paths.RECIPES_DIR, f))
        if not isinstance(data, dict) or data.get("format") != DEV.RECIPE_FORMAT:
            continue
        out.append({
            "name": f[:-5],
            "memo": data.get("memo", ""),
            "modified": data.get("modified", ""),
            "number": recipe_number(data),
            "block_count": len(data.get("blocks") or []),
        })
    return out


def load(name: str):
    """이름으로 읽는다. 형식이 다르면 None(열지 않는다)."""
    if not valid_name(name):
        return None
    data = read_json(path_of(name))
    if not isinstance(data, dict) or data.get("format") != DEV.RECIPE_FORMAT:
        return None
    return data


def save(name: str, recipe: dict) -> bool:
    if not valid_name(name):
        return False
    try:
        os.makedirs(paths.RECIPES_DIR, exist_ok=True)
        atomic_write_json(path_of(name), recipe)
        return True
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"레시피 저장 실패: {name} ({e})")
        return False


def delete(name: str) -> bool:
    if not valid_name(name):
        return False
    try:
        os.remove(path_of(name))
        return True
    except FileNotFoundError:
        return False
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"레시피 삭제 실패: {name} ({e})")
        return False


def exists(name: str) -> bool:
    return valid_name(name) and os.path.isfile(path_of(name))


def find_by_number(number: int):
    """PLC 에 올라가 있는 번호와 같은 로컬 레시피 이름. 없으면 None.
    ★ PC 를 다시 켰을 때 PLC 가 이미 공정 중이면 이것으로 이름을 되찾는다."""
    for r in list_recipes():
        if r["number"] == int(number):
            return r["name"]
    return None
