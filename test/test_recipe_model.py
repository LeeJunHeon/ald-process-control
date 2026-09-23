"""recipe_model 테스트 — 밸브 계산, 시간 계산, 금지 조합 검출, 반복 그룹."""
import copy

import recipe_model as rm


# ===================== 밸브 계산 =====================
def test_step_open_tags_from_mode(cfg1):
    """열리는 밸브 = 선택 라인 × mode 역할 + always_open. 코드에 조합을 두지 않는다."""
    step = {"lines": [{"line": "P1", "mode": "vapor"}]}
    tags = rm.step_open_tags(cfg1, step)
    assert set(tags) == {"P1-OUT", "P1-BYP", "P1-ALD", "PN-ALD", "RN-ALD"}

    step = {"lines": [{"line": "P2", "mode": "carrier"}]}
    assert set(rm.step_open_tags(cfg1, step)) == {"P2-IN", "P2-OUT", "P2-ALD", "PN-ALD", "RN-ALD"}


def test_purge_step_opens_only_always_open(cfg1):
    assert set(rm.step_open_tags(cfg1, {"lines": []})) == {"PN-ALD", "RN-ALD"}


def test_gas_line_direct_mode(cfg1):
    assert set(rm.step_open_tags(cfg1, {"lines": [{"line": "R2", "mode": "direct"}]})) \
        == {"R2-ALD", "PN-ALD", "RN-ALD"}


# ===================== 시간 계산 =====================
def test_cycle_and_total_time(cfg1):
    r = rm.sample_recipe(cfg1)
    # 1 사이클 = 0.10 + 10 + 0.10 + 15 = 25.2 s
    assert rm.block_cycle_seconds(r["blocks"][1]) == 25.2
    assert rm.block_seconds(r["blocks"][1]) == 25.2 * 300
    # 총 = 60 + 7560 + 120 = 7740 s = 2:09:00
    assert rm.total_seconds(r) == 7740.0


def test_repeat_group_multiplies_range(cfg1):
    r = rm.sample_recipe(cfg1)
    base = rm.total_seconds(r)
    block1 = rm.block_seconds(r["blocks"][1])
    r["groups"] = [{"from_block": 1, "to_block": 1, "repeat": 3}]
    assert rm.total_seconds(r) == base + block1 * 2


def test_summary_shape(cfg1):
    s = rm.summarize(cfg1, rm.sample_recipe(cfg1))
    assert len(s["blocks"]) == 3
    assert s["max_cycles"] == 300
    assert s["blocks"][1]["seq"].count("→") == 3


# ===================== 검증 =====================
def _msgs(errs):
    return " | ".join(e["msg"] for e in errs)


def test_sample_recipes_validate(cfg1, cfg2):
    for cfg in (cfg1, cfg2):
        assert rm.validate(cfg, rm.sample_recipe(cfg)) == []


def test_precursor_and_reactant_together_is_rejected(cfg1):
    """★ ALD 의 전제가 무너지는 조합 — 어떤 경우에도 통과하면 안 된다."""
    r = rm.sample_recipe(cfg1)
    r["blocks"][1]["steps"][0]["lines"].append({"line": "R1", "mode": "vapor"})
    assert "동시에 열립니다" in _msgs(rm.validate(cfg1, r))


def test_precursor_and_reactant_ok_if_mode_does_not_open_ald(cfg1):
    """ALD 밸브를 열지 않는 mode 라면 챔버에서 만나지 않는다 → 통과해야 한다."""
    cfg = copy.deepcopy(cfg1)
    cfg["modes"]["prep"] = {"label": "예열", "open": ["IN", "OUT"]}
    r = rm.sample_recipe(cfg)
    r["blocks"][1]["steps"][0]["lines"].append({"line": "R1", "mode": "prep"})
    assert rm.validate(cfg, r) == []


def test_too_short_step(cfg1):
    r = rm.sample_recipe(cfg1)
    r["blocks"][1]["steps"][0]["time_s"] = 0.01
    assert "최소값보다 짧" in _msgs(rm.validate(cfg1, r))


def test_unknown_and_disabled_line(cfg1):
    r = rm.sample_recipe(cfg1)
    r["blocks"][1]["steps"][0]["lines"] = [{"line": "ZZ", "mode": "vapor"}]
    assert "없는 라인" in _msgs(rm.validate(cfg1, r))
    r["blocks"][1]["steps"][0]["lines"] = [{"line": "P3", "mode": "carrier"}]   # enabled=false
    assert "미장착 라인" in _msgs(rm.validate(cfg1, r))


def test_unsupported_mode(cfg1):
    """P2 는 BYP 가 없어 증기압(vapor)을 지원하지 않는다."""
    r = rm.sample_recipe(cfg1)
    r["blocks"][1]["steps"][0]["lines"] = [{"line": "P2", "mode": "vapor"}]
    assert "지원하지 않는 공급 방식" in _msgs(rm.validate(cfg1, r))


def test_repeat_below_one(cfg1):
    r = rm.sample_recipe(cfg1)
    r["blocks"][1]["repeat"] = 0
    assert "1 이상" in _msgs(rm.validate(cfg1, r))


def test_group_range_errors(cfg1):
    r = rm.sample_recipe(cfg1)
    r["groups"] = [{"from_block": 1, "to_block": 9, "repeat": 2}]
    assert "범위가 레시피를 벗어" in _msgs(rm.validate(cfg1, r))
    r["groups"] = [{"from_block": 2, "to_block": 1, "repeat": 2}]
    assert "시작 블록이 끝 블록보다 뒤" in _msgs(rm.validate(cfg1, r))
    r["groups"] = [{"from_block": 0, "to_block": 1, "repeat": 0}]
    assert "1 이상" in _msgs(rm.validate(cfg1, r))


def test_preview_bundles_summary_errors_opens(cfg1):
    pv = rm.preview(cfg1, rm.sample_recipe(cfg1))
    assert pv["ok"] is True
    assert pv["summary"]["total_s"] == 7740.0
    assert len(pv["opens"]) == 1 + 4 + 1          # 블록별 스텝 수의 합
