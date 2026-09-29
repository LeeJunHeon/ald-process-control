"""레시피 — 검증 · 시간 계산 · PLC 표 변환 · 역변환.

★ 시간 계산은 PLC 시퀀서 동작을 그대로 따라가야 한다. 화면이 보여 주는 남은 시간이
  실제와 다르면 운전자가 장비 앞을 언제 떠나도 되는지 판단할 수 없다.
"""
import pytest

from peald import addresses as A
from peald import device as DEV
from peald import recipe as R
from peald.convert import Converters


# ===================== 도우미 =====================
def step(name="s", ms=1000, valves=(), pause_ok=False, rf=False):
    s = {"name": name, "time_ms": ms, "valves": list(valves), "pause_ok": pause_ok}
    if DEV.HAS_RF:
        s["rf"] = rf
    return s


def block(name="b", repeat=1, steps=None, mfc=None):
    b = R.empty_block(name)
    b["repeat"] = repeat
    b["steps"] = steps if steps is not None else [step()]
    b["mfc_sccm"] = list(mfc) if mfc else [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    return b


def recipe(blocks=None, groups=None, name="시험"):
    r = R.empty_recipe(name)
    r["blocks"] = blocks if blocks is not None else [block()]
    r["groups"] = groups or []
    return r


# ===================== 실효 스텝 시간 =====================
@pytest.mark.parametrize("t,new,vmin,want", [
    (1000, False, 200, 1000),
    (50, True, 200, 200),          # 새로 열림 → 최소 열림으로 늘어난다
    (50, False, 200, 50),          # 이미 열려 있던 밸브면 그대로
    (250, True, 200, 250),
    (60_000, False, 200, 60_000),  # 경계: 60 s 까지는 1 ms 타이머
    (60_150, False, 200, 60_100),  # 넘으면 100 ms 단위, 나머지 버림
    (123_456, False, 200, 123_400),
])
def test_effective_step_ms(t, new, vmin, want):
    assert R.effective_step_ms(t, new, vmin) == want


def test_first_cycle_vs_repeat_cycle_new_valve():
    """사이클 반복 때 첫 스텝의 직전 출력은 그 블록의 마지막 스텝이다.
    ★ 마지막 스텝과 첫 스텝의 밸브가 같으면 두 번째 사이클부터는 '새로 열림'이 아니다."""
    b = block(steps=[step("a", 50, ["PV-1"]), step("b", 50, ["PV-1"])])
    first, rep = R.step_times(b, 200)
    assert first[0] == 200      # 첫 사이클: 직전이 전부 닫힘 → 늘어난다
    assert rep[0] == 50         # 반복 사이클: 마지막 스텝도 PV-1 → 새로 열리지 않는다
    assert first[1] == rep[1] == 50


def test_block_ms_includes_prep(cfg):
    cfg["params"]["mfc_stable_s"] = 3
    cfg["params"]["valve_min_ms"] = 0
    b = block(repeat=4, steps=[step("a", 100), step("b", 200)])
    vmin, prep = R._prm(cfg)
    assert prep == 3000
    assert R.block_ms(b, vmin, prep) == 3000 + 300 + 3 * 300


def test_zero_params_are_honoured(cfg):
    """★ 0 은 뜻이 있는 값이다 — 기본값으로 바뀌면 안 된다."""
    cfg["params"]["mfc_stable_s"] = 0
    cfg["params"]["valve_min_ms"] = 0
    vmin, prep = R._prm(cfg)
    assert (vmin, prep) == (0, 0)


def test_total_ms_with_groups(cfg):
    cfg["params"]["mfc_stable_s"] = 0
    cfg["params"]["valve_min_ms"] = 0
    r = recipe([block("A", 2, [step("a", 100)]),
                block("B", 1, [step("b", 100)]),
                block("C", 1, [step("c", 100)])],
               groups=[{"from_block": 1, "to_block": 2, "repeat": 3}])
    # (A 2사이클 200 + B 100) × 3 + C 100
    assert R.total_ms(cfg, r) == (200 + 100) * 3 + 100


def test_total_ms_without_groups(cfg):
    cfg["params"]["mfc_stable_s"] = 0
    cfg["params"]["valve_min_ms"] = 0
    r = recipe([block("A", 3, [step("a", 100), step("b", 150)])])
    assert R.total_ms(cfg, r) == 250 * 3


def test_remaining_ms_counts_down(cfg):
    cfg["params"]["mfc_stable_s"] = 0
    cfg["params"]["valve_min_ms"] = 0
    r = recipe([block("A", 2, [step("a", 1000), step("b", 1000)])])
    total = R.total_ms(cfg, r)
    at_start = R.remaining_ms(cfg, r, {"block": 1, "step": 1, "cycle": 1,
                                       "group_pass": 1, "step_elapsed_ms": 0})
    assert at_start == total
    mid = R.remaining_ms(cfg, r, {"block": 1, "step": 2, "cycle": 2,
                                  "group_pass": 1, "step_elapsed_ms": 400})
    assert mid == 600
    assert mid < at_start


def test_remaining_ms_frozen_while_paused(cfg):
    """일시정지 중에는 남은 시간이 줄지 않는다."""
    cfg["params"]["mfc_stable_s"] = 0
    cfg["params"]["valve_min_ms"] = 0
    r = recipe([block("A", 1, [step("a", 1000)])])
    pos = {"block": 1, "step": 1, "cycle": 1, "group_pass": 1, "step_elapsed_ms": 700}
    assert R.remaining_ms(cfg, r, dict(pos, paused=False)) == 300
    assert R.remaining_ms(cfg, r, dict(pos, paused=True)) == 1000


def test_remaining_ms_large_group_repeat_is_fast(cfg):
    """★ 그룹 반복이 65535 여도 하나씩 세면 안 된다 — 구간 합으로 계산한다."""
    cfg["params"]["mfc_stable_s"] = 0
    cfg["params"]["valve_min_ms"] = 0
    r = recipe([block("A", 1_000_000, [step("a", 20)])],
               groups=[{"from_block": 1, "to_block": 1, "repeat": 65535}])
    assert R.total_ms(cfg, r) > 0        # 돌아오기만 하면 된다(시간 안에)


# ===================== 레시피 번호 =====================
def test_number_is_stable_and_ignores_memo():
    r1 = recipe()
    r2 = recipe()
    r2["memo"] = "메모를 바꿔도 번호는 같아야 한다"
    r2["modified"] = "2000-01-01 00:00:00"
    assert R.recipe_number(r1) == R.recipe_number(r2)


def test_number_changes_with_content():
    r1 = recipe([block("A", 1, [step("a", 100)])])
    r2 = recipe([block("A", 1, [step("a", 200)])])
    assert R.recipe_number(r1) != R.recipe_number(r2)


def test_number_is_never_zero(monkeypatch):
    monkeypatch.setattr(R.zlib, "crc32", lambda _b: 0x10000)
    assert R.recipe_number(recipe()) == 1


# ===================== 검증 — 오류 =====================
def msgs(res, key="errors"):
    return " | ".join(e["msg"] for e in res[key])


def test_valid_recipe_passes(cfg):
    r = recipe([block("A", 1, [step("a", 1000, ["PV-1"])])])
    res = R.validate(cfg, r)
    assert res["errors"] == [], msgs(res)


def test_wrong_format_is_rejected(cfg):
    r = recipe()
    r["format"] = "OTHER-recipe/1"
    assert "이 장비의 레시피가 아닙니다" in msgs(R.validate(cfg, r))


def test_precursor_and_reactant_together(cfg):
    """★ PLC 가 둘 다 막고 중대 알람을 낸다 — 레시피 단계에서 금지한다."""
    r = recipe([block("A", 1, [step("a", 1000, [DEV.PRECURSOR_TAGS[0], DEV.REACTANT_TAGS[0]])])])
    res = R.validate(cfg, r)
    assert "같이 열 수 없습니다" in msgs(res)
    assert res["errors"][0]["block"] == 1 and res["errors"][0]["step"] == 1


def test_assist_needs_its_canister(cfg):
    assist, need = list(DEV.ASSIST_PAIR.items())[0]
    r = recipe([block("A", 1, [step("a", 1000, [assist])])])
    assert f"{assist} 는 {need} 가 열리는 스텝에서만" in msgs(R.validate(cfg, r))
    r2 = recipe([block("A", 1, [step("a", 1000, [assist, need])])])
    assert R.validate(cfg, r2)["errors"] == []


def test_unknown_valve(cfg):
    r = recipe([block("A", 1, [step("a", 1000, ["PV-ZZ"])])])
    assert "이 장비에 없는 밸브" in msgs(R.validate(cfg, r))


@pytest.mark.parametrize("ms", [0, 19, 3_276_701])
def test_step_time_range(cfg, ms):
    r = recipe([block("A", 1, [step("a", ms)])])
    assert "스텝 시간은" in msgs(R.validate(cfg, r))


def test_block_repeat_range(cfg):
    r = recipe([block("A", 0, [step()])])
    assert "블록 반복은" in msgs(R.validate(cfg, r))
    r = recipe([block("A", R.BLOCK_REPEAT_MAX + 1, [step()])])
    assert "블록 반복은" in msgs(R.validate(cfg, r))


def test_counts_limits(cfg):
    r = recipe([block(f"B{i}", 1, [step()]) for i in range(R.BLOCK_MAX + 1)])
    assert "블록 개수는" in msgs(R.validate(cfg, r))
    r = recipe([block("A", 1, [step() for _ in range(R.STEP_MAX + 1)])])
    assert "전체 스텝 개수는" in msgs(R.validate(cfg, r))


def test_block_needs_a_step(cfg):
    r = recipe([block("A", 1, [])])
    assert "스텝이 하나도 없습니다" in msgs(R.validate(cfg, r))


def test_mfc_range(cfg):
    # 음수는 풀스케일을 몰라도 항상 오류
    r = recipe([block("A", 1, [step()], mfc=[-1.0] + [0.0] * (DEV.MFC_COUNT - 1))])
    assert "MFC1 설정이 범위를 벗어납니다" in msgs(R.validate(cfg, r))
    # 풀스케일이 정해진 장비에서는 그 위도 오류
    fs = cfg["mfc"][0]["full_scale_sccm"]
    if fs is not None:
        r = recipe([block("A", 1, [step()], mfc=[fs + 1] + [0.0] * (DEV.MFC_COUNT - 1))])
        assert "MFC1 설정이 범위를 벗어납니다" in msgs(R.validate(cfg, r))


def test_group_rules(cfg):
    b = [block("A", 1, [step()]), block("B", 1, [step()]), block("C", 1, [step()])]
    # 범위 밖
    assert "블록 범위를 벗어납니다" in msgs(R.validate(
        cfg, recipe(b, [{"from_block": 1, "to_block": 9, "repeat": 2}])))
    # 시작 > 끝
    assert "시작 블록이 끝 블록보다 뒤" in msgs(R.validate(
        cfg, recipe(b, [{"from_block": 3, "to_block": 2, "repeat": 2}])))
    # 겹침 / 순서
    assert "겹치거나 순서가 어긋" in msgs(R.validate(
        cfg, recipe(b, [{"from_block": 1, "to_block": 2, "repeat": 2},
                        {"from_block": 2, "to_block": 3, "repeat": 2}])))
    # 반복 범위
    assert "반복은 1~" in msgs(R.validate(
        cfg, recipe(b, [{"from_block": 1, "to_block": 2, "repeat": 0}])))
    # 정상
    assert R.validate(cfg, recipe(b, [{"from_block": 1, "to_block": 2, "repeat": 2}]))["errors"] == []


def test_too_many_groups(cfg):
    b = [block(f"B{i}", 1, [step()]) for i in range(R.GROUP_MAX + 1)]
    gs = [{"from_block": i + 1, "to_block": i + 1, "repeat": 2} for i in range(R.GROUP_MAX + 1)]
    assert "반복 그룹은 최대" in msgs(R.validate(cfg, recipe(b, gs)))


# ===================== 검증 — 경고 =====================
def test_warn_min_open_extension(cfg):
    cfg["params"]["valve_min_ms"] = 200
    r = recipe([block("A", 1, [step("a", 50, ["PV-1"])])])
    res = R.validate(cfg, r)
    assert res["errors"] == []
    assert "200 ms 로 늘립니다" in msgs(res, "warnings")


def test_warn_long_step_rounding(cfg):
    r = recipe([block("A", 1, [step("a", 60_150)])])
    assert "100 ms 단위" in msgs(R.validate(cfg, r), "warnings")


def test_warn_mfc1_zero(cfg):
    r = recipe([block("A", 1, [step()], mfc=[0.0] * DEV.MFC_COUNT)])
    assert "MFC1" in msgs(R.validate(cfg, r), "warnings")


# ===================== PLC 표 변환 =====================
def test_table_size_and_checksum(cfg):
    conv = Converters(cfg)
    r = recipe([block("A", 2, [step("a", 100, ["PV-1"]), step("b", 200)])])
    t = R.to_plc_words(cfg, conv, r)
    assert len(t["words"]) == A.RCP_AREA_COUNT == 1120
    assert R.checksum_of(t["words"]) == t["checksum"]
    # 합계 칸 자신은 합계에서 빠진다
    assert t["words"][A.D_RCP_SUM - A.RCP_SUM_BASE] == t["checksum"]


def test_table_step_numbering_across_blocks(cfg):
    conv = Converters(cfg)
    r = recipe([block("A", 1, [step("a1", 100), step("a2", 100)]),
                block("B", 1, [step("b1", 100)])])
    t = R.to_plc_words(cfg, conv, r)
    w = t["words"]

    def g(addr):
        return w[addr - A.RCP_SUM_BASE]

    assert g(A.D_RCP_STEP_COUNT) == 3
    assert g(A.D_RCP_BLOCK_COUNT) == 2
    b1 = A.D_RCP_BLOCK_BASE
    b2 = A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_STRIDE
    assert (g(b1 + A.RCP_BLOCK_FIRST), g(b1 + A.RCP_BLOCK_LAST)) == (1, 2)
    assert (g(b2 + A.RCP_BLOCK_FIRST), g(b2 + A.RCP_BLOCK_LAST)) == (3, 3)


def test_table_step_words(cfg):
    conv = Converters(cfg)
    r = recipe([block("A", 1, [step("a", 1234, ["PV-1"], pause_ok=True, rf=True)])])
    w = R.to_plc_words(cfg, conv, r)["words"]

    def g(addr):
        return w[addr - A.RCP_SUM_BASE]

    base = A.D_RCP_STEP_BASE
    assert g(base + A.RCP_STEP_VALVE_LO) == 1 << DEV.valve_bit("PV-1")
    assert A.dword(g(base + A.RCP_STEP_TIME_LO), g(base + A.RCP_STEP_TIME_LO + 1)) == 1234
    flags = g(base + A.RCP_STEP_FLAGS)
    assert flags & A.RCP_FLAG_PAUSE_OK
    assert bool(flags & A.RCP_FLAG_RF_ON) == DEV.HAS_RF


def test_table_block_repeat_is_dword(cfg):
    conv = Converters(cfg)
    r = recipe([block("A", 100_000, [step()])])
    w = R.to_plc_words(cfg, conv, r)["words"]
    b = A.D_RCP_BLOCK_BASE - A.RCP_SUM_BASE
    assert A.dword(w[b + A.RCP_BLOCK_REPEAT_LO], w[b + A.RCP_BLOCK_REPEAT_LO + 1]) == 100_000


def test_table_unused_mfc_is_zero(cfg):
    conv = Converters(cfg)
    r = recipe([block("A", 1, [step()], mfc=[100.0] * DEV.MFC_COUNT)])
    w = R.to_plc_words(cfg, conv, r)["words"]
    b = A.D_RCP_BLOCK_BASE - A.RCP_SUM_BASE
    for mi in range(DEV.MFC_COUNT, 8):
        assert w[b + A.RCP_BLOCK_MFC + mi] == 0, f"MFC{mi + 1} 는 이 장비에 없다"


def test_roundtrip_through_plc_words(cfg):
    conv = Converters(cfg)
    r = recipe([block("A", 5, [step("a", 100), step("b", 200)]),
                block("B", 2, [step("c", 300)])],
               groups=[{"from_block": 1, "to_block": 2, "repeat": 4}])
    t = R.to_plc_words(cfg, conv, r)
    back = R.from_plc_words(t["words"])
    assert back["number"] == t["number"] == R.recipe_number(r)
    assert back["checksum"] == t["checksum"]
    assert back["step_count"] == 3 and back["block_count"] == 2 and back["group_count"] == 1
    assert back["blocks"][0]["repeat"] == 5
    assert back["blocks"][1]["first_step"] == 3
    assert back["groups"][0] == {"from_block": 1, "to_block": 2, "repeat": 4}


def test_step_index_map(cfg):
    r = recipe([block("A", 1, [step(), step()]), block("B", 1, [step()])])
    assert R.step_index_map(r) == [(1, 0), (1, 1), (2, 0)]
