"""config 검증 테스트 — 정상 예시 2개 + 누락 키·중복 id·잘못된 mode·태그 규칙 위반."""
import copy
import json

import pytest

import config
from conftest import CFG1, CFG2


def test_examples_are_clean():
    """납품 예시 설정 두 개는 경고 하나 없이 통과해야 한다."""
    for path in (CFG1, CFG2):
        cfg, problems = config.load(path)
        assert problems == [], f"{path}: {problems}"


def test_missing_file_still_boots():
    """설정 파일이 없어도 기동은 계속된다(기본값 + 경고)."""
    cfg, problems = config.load("없는파일.json")
    assert cfg["chamber"]["id"]          # 기본값이 채워졌다
    assert any(lv == "warn" for lv, _ in problems)


def _msgs(cfg):
    return " | ".join(m for _, m in config.validate(cfg))


def test_missing_required_keys(cfg1):
    cfg = copy.deepcopy(cfg1)
    cfg["chamber"]["id"] = ""
    cfg["server"]["port"] = "abc"
    m = _msgs(cfg)
    assert "chamber.id" in m and "server.port" in m


def test_duplicate_line_id(cfg1):
    cfg = copy.deepcopy(cfg1)
    cfg["lines"].append(copy.deepcopy(cfg["lines"][1]))
    assert "id 가 중복" in _msgs(cfg)


def test_duplicate_valve_tag(cfg1):
    cfg = copy.deepcopy(cfg1)
    dup = copy.deepcopy(cfg["lines"][1])
    dup["id"] = "P9"
    dup["valves"] = {"ALD": "P1-ALD"}      # 다른 라인의 태그를 그대로 가져왔다
    cfg["lines"].append(dup)
    m = _msgs(cfg)
    assert "밸브 태그" in m


def test_bad_tag_rule(cfg1):
    cfg = copy.deepcopy(cfg1)
    cfg["lines"][1]["valves"]["ALD"] = "P1_ALD"     # '-' 가 아니다
    assert "태그 규칙 위반" in _msgs(cfg)


def test_tag_role_mismatch(cfg1):
    cfg = copy.deepcopy(cfg1)
    cfg["lines"][1]["valves"]["IN"] = "P2-IN"       # 라인 P1 인데 태그는 P2
    assert "라인·역할과 맞지 않" in _msgs(cfg)


def test_unknown_role_in_mode(cfg1):
    cfg = copy.deepcopy(cfg1)
    cfg["modes"]["vapor"]["open"] = ["OUT", "PURGE"]
    assert "알 수 없는 밸브 역할" in _msgs(cfg)


def test_always_open_unknown_tag(cfg1):
    cfg = copy.deepcopy(cfg1)
    cfg["process"]["always_open"] = ["ZZ-ALD"]
    assert "존재하지 않는 밸브 태그" in _msgs(cfg)


def test_line_missing_ald_valve(cfg1):
    cfg = copy.deepcopy(cfg1)
    del cfg["lines"][1]["valves"]["ALD"]
    assert "ALD 밸브가 없습니다" in _msgs(cfg)


def test_line_modes_follow_valves(cfg1, cfg2):
    """★ 밸브 조합을 코드에 두지 않는다는 규칙의 핵심 —
    지원 mode 는 '라인이 가진 역할'에서만 나온다."""
    # 챔버1 P1 은 BYP 가 있어 증기압을 지원한다.
    assert "vapor" in config.line_modes(cfg1, config.line_by_id(cfg1, "P1"))
    # 챔버1 P2 는 BYP 가 없어 증기압을 지원하지 않는다.
    assert "vapor" not in config.line_modes(cfg1, config.line_by_id(cfg1, "P2"))
    # 챔버2 P1(캐리어 전용)도 마찬가지.
    assert config.line_modes(cfg2, config.line_by_id(cfg2, "P1")) == ["carrier"]


def test_mode_kinds_narrow_support(cfg1):
    """★ 역할만으로는 부족한 경우 — 캐니스터도 ALD 밸브를 가지므로 '직공급'이
    기술적으로는 성립하지만 실제로는 아무것도 흐르지 않는다. kinds 로 막는다."""
    assert "direct" not in config.line_modes(cfg1, config.line_by_id(cfg1, "P1"))
    assert config.line_modes(cfg1, config.line_by_id(cfg1, "R2")) == ["direct"]
    assert config.line_modes(cfg1, config.line_by_id(cfg1, "PN")) == ["direct"]


def test_bad_mode_kind_is_reported(cfg1):
    cfg = copy.deepcopy(cfg1)
    cfg["modes"]["vapor"]["kinds"] = ["canister", "plasma"]
    assert "알 수 없는 라인 종류" in _msgs(cfg)


def test_two_chambers_differ():
    """두 예시 설정은 id·포트·테마·배치가 모두 달라야 동시에 띄울 수 있다."""
    a, _ = config.load(CFG1)
    b, _ = config.load(CFG2)
    assert a["chamber"]["id"] != b["chamber"]["id"]
    assert a["server"]["port"] != b["server"]["port"]
    assert a["ui"]["theme"] != b["ui"]["theme"]
    assert a["ui"]["window"]["side"] != b["ui"]["window"]["side"]
