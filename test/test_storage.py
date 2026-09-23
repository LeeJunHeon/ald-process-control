"""storage 테스트 — 경로 탈출 파일명 거절 + 원자적 저장."""
import os

import pytest

import paths
import storage


@pytest.mark.parametrize("name", [
    "../탈출", "..\\탈출", "a/b", "a\\b", "..", "../../etc/passwd",
    "CON", "CON.old", "NUL", "COM1", "LPT9",
    'bad<name', 'bad>name', 'bad:name', 'bad"name', "bad|name", "bad?name", "bad*name",
    "끝점.", "끝공백 ", "", "x" * 81,
])
def test_rejects_bad_names(name):
    """★ 레시피 이름은 그대로 파일 경로가 된다 — 폴더 밖으로 나가는 이름을 전부 막는다."""
    assert storage.valid_recipe_name(name) is False


@pytest.mark.parametrize("name", ["Al2O3_TMA-H2O_200C", "레시피 1", "a.b", "x" * 80])
def test_accepts_good_names(name):
    assert storage.valid_recipe_name(name) is True


def test_escape_name_never_writes_outside(tmp_path):
    assert storage.save_recipe("../탈출", {"name": "x"}) is False
    assert storage.load_recipe("../탈출") is None
    assert storage.delete_recipe("../탈출") is False
    # 레시피 폴더 바깥에 파일이 생기지 않았다.
    assert not os.path.exists(os.path.join(os.path.dirname(paths.RECIPES_DIR), "탈출.json"))


def test_save_load_list_delete_roundtrip():
    r = {"name": "테스트", "blocks": []}
    assert storage.save_recipe("테스트", r) is True
    assert "테스트" in storage.list_recipes()
    assert storage.load_recipe("테스트")["name"] == "테스트"
    assert storage.delete_recipe("테스트") is True
    assert "테스트" not in storage.list_recipes()


def test_atomic_write_leaves_no_tmp(tmp_path):
    p = str(tmp_path / "a.json")
    storage.atomic_write_json(p, {"k": 1})
    assert os.path.exists(p) and not os.path.exists(p + ".tmp")
