"""v0.4.5 — 레시피 · 공정 판단 · 장비별 조건.

편집기 반복 그룹(from_block / to_block) · 레시피 값 형식 검사 · 편집기 속성 이스케이프 /
그룹 반복 한계 32767(래더의 부호 있는 비교) · 시작 결과 3 문구 / 풀스케일 없는 MFC /
PRM 기본값 한 곳 / 끝 판정(정상 · 사이클 후 정지 · 운전자 중단 · 안전 정지) · 마지막 위치 ·
시작 스냅샷 / Powder O3 발생기 시작 조건 / 온도조절기 통신 없는 히터 채널 / 작은 것.
"""
import os
import json
import copy
import asyncio
import threading

import pytest

from powderald import addresses as A
from powderald import commands as C
from powderald import device as DEV
from powderald import recipe as R
from powderald import storage
from powderald import paths
from powderald.convert import Converters
from powderald.state import state

from conftest import wait_until, free_port
from test_process import (wired, notices, pumped, short_recipe, long_recipe,  # noqa: F401
                          _start, _seen_running, _run_to_end, _meta_and_list, _log, _notice)


def two_blocks(name="두블록"):
    r = short_recipe(name)
    b2 = copy.deepcopy(r["blocks"][0])
    b2["name"] = "b2"
    r["blocks"].append(b2)
    return r


# ===================== 1. 반복 그룹 키 =====================
def test_old_group_keys_are_read_as_new(cfg):
    """옛 파일의 from / to 는 불러올 때 from_block / to_block 으로 바뀌고, 검증 · PLC 표가 그 값을 쓴다."""
    rec = two_blocks("옛그룹")
    rec["groups"] = [{"from": 1, "to": 2, "repeat": 4}]
    os.makedirs(paths.RECIPES_DIR, exist_ok=True)
    with open(storage.path_of("옛그룹"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    got = storage.load("옛그룹")
    assert got["groups"] == [{"from_block": 1, "to_block": 2, "repeat": 4}]
    assert R.ok(R.validate(cfg, got)), R.validate(cfg, got)
    tbl = R.to_plc_words(cfg, Converters(cfg), got)
    back = R.from_plc_words(tbl["words"])
    assert back["groups"] == [{"from_block": 1, "to_block": 2, "repeat": 4}]
    # 옛 키로 된 편집 내용을 그대로 검증해도 같은 결과(번호도 같다)
    assert R.ok(R.validate(cfg, rec))
    assert R.recipe_number(rec) == R.recipe_number(got)


async def test_save_writes_new_keys_only(wired):
    lk, sim, cfg = wired
    rec = two_blocks("새키")
    rec["groups"] = [{"from": 1, "to": 2, "repeat": 3}]
    await C.handle_command({"cmd": "recipe_save", "name": "새키", "recipe": rec})
    with open(storage.path_of("새키"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["groups"] == [{"from_block": 1, "to_block": 2, "repeat": 3}]


# ===================== 2. 값 형식 검사 =====================
def _bad(path, value):
    def make():
        r = two_blocks("형식")
        r["groups"] = [{"from_block": 1, "to_block": 2, "repeat": 2}]
        obj = r
        for k in path[:-1]:
            obj = obj[k]
        obj[path[-1]] = value
        return r
    return make


@pytest.mark.parametrize("make,word", [
    (_bad(["blocks", 0, "repeat"], "2"), "정수"),
    (_bad(["blocks", 0, "repeat"], 2.5), "정수"),                      # 조용히 2 로 자르지 않는다
    (_bad(["blocks", 0, "steps", 0, "time_ms"], 150.7), "정수"),
    (_bad(["blocks", 0, "steps", 0, "time_ms"], "100"), "정수"),
    (_bad(["blocks", 0, "steps", 0, "time_ms"], -5), "스텝 시간"),     # 범위
    (_bad(["blocks", 0, "steps", 0, "time_ms"], 10 ** 400), "너무 크"),
    (_bad(["blocks", 0, "mfc_sccm", 0], float("inf")), "너무 크"),
    (_bad(["blocks", 0, "mfc_sccm", 0], "100"), "숫자"),
    (_bad(["blocks", 0, "mfc_sccm", 0], None), "비어"),
    (_bad(["blocks", 0, "mfc_sccm"], {"0": 1}), "목록"),
    (_bad(["blocks", 0, "steps"], {"a": 1}), "목록"),
    (_bad(["blocks"], {"b": 1}), "목록"),
    (_bad(["blocks", 0], [1, 2]), "사전"),
    (_bad(["blocks", 0, "steps", 0, "valves"], "PV-1"), "밸브"),
    (_bad(["blocks", 0, "steps", 0, "pause_ok"], "yes"), "참/거짓"),
    (_bad(["blocks", 0, "name"], 5), "문자열"),
    (_bad(["groups"], {"a": 1}), "목록"),
    (_bad(["groups", 0], [1, 2, 3]), "사전"),
    (_bad(["groups", 0, "repeat"], "4"), "정수"),
    (_bad(["groups", 0, "repeat"], 2.5), "정수"),
    (_bad(["groups", 0, "from_block"], None), "비어"),
    (_bad(["groups", 0, "repeat"], -1), "반복"),
])
def test_bad_values_are_validation_errors_not_exceptions(cfg, make, word):
    rec = make()
    res = R.validate(cfg, rec)
    assert res["errors"], rec
    assert any(word in e["msg"] for e in res["errors"]), res["errors"]
    # 요약 · 번호도 예외 없이
    R.summarize(cfg, rec)
    R.recipe_number(rec)


async def test_tampered_file_does_not_break_list_load_or_validate(wired):
    lk, sim, cfg = wired
    rec = two_blocks("조작")
    rec["blocks"][0]["repeat"] = '1"><img src=x onerror=alert(1)>'
    rec["blocks"] = rec["blocks"] + ["블록 아님"]
    rec["memo"] = {"x": 1}
    os.makedirs(paths.RECIPES_DIR, exist_ok=True)
    with open(storage.path_of("조작"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    items = {r["name"]: r for r in storage.list_recipes()}
    assert items["조작"]["memo"] == "" and items["조작"]["block_count"] == 3
    ok, _why = state.runner.select("조작")          # 고를 수는 있지만 표는 만들지 않는다
    assert ok and state.runner.table is None and state.recipe_check["errors"]
    await C.handle_command({"cmd": "recipe_upload", "name": "조작"})
    assert sim.reg[A.D_RCP_NO] != R.recipe_number(rec)


# ===================== 3. 그룹 반복 한계 =====================
def test_group_repeat_limit_is_signed_word(cfg):
    assert R.GROUP_REPEAT_MAX == A.RCP_GROUP_REPEAT_MAX == 32767
    rec = two_blocks("한계")
    rec["groups"] = [{"from_block": 1, "to_block": 2, "repeat": 32767}]
    assert R.ok(R.validate(cfg, rec))
    rec["groups"][0]["repeat"] = 32768
    errs = R.validate(cfg, rec)["errors"]
    assert any("1~32767" in e["msg"] for e in errs), errs


def test_simulator_reads_group_repeat_signed(cfg):
    """[1 ×2, 2 ×32768] — 그룹 1 을 다 돈 뒤 그룹 2 를 불러올 때 래더처럼 레시피 오류(8)."""
    from conftest import FakeSim
    rec = two_blocks("부호")
    for b in rec["blocks"]:
        b["repeat"] = 1
        b["steps"] = [{"name": "s", "time_ms": 20, "valves": [], "pause_ok": False,
                       **({"rf": False} if DEV.HAS_RF else {})}]
    rec["groups"] = [{"from_block": 1, "to_block": 1, "repeat": 2},
                     {"from_block": 2, "to_block": 2, "repeat": 32768}]
    cfg["params"]["mfc_stable_s"] = 0
    cfg["params"]["mfc_tol_sccm"] = 0
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    fs = FakeSim(cfg, o3=True)               # Powder: 공정 중 O3 허가가 없으면 래더가 b3 로 중단한다
    sim = fs.sim
    sim.write(A.RCP_SUM_BASE, tbl["words"])
    sim.reg.pc_set(A.D_PRM_MFC_STABLE, 0)
    sim.reg.pc_set(A.D_PRM_MFC_TOL, 0)
    assert sim._process_start() == A.RESULT_OK
    passes = set()
    for _ in range(400):
        fs.step(1, 0.005)
        if sim.running:
            passes.add((sim.blk, sim.group_pass))
        else:
            break
    assert not sim.running
    assert sim.reg[A.D_SEQ_STATE] == 8 and A.bit(sim.reg[A.D_ALARM0], A.ALM0_RECIPE)
    assert "그룹 2" in sim.end_reason
    assert (1, 2) in passes and not any(b == 2 for b, _ in passes), passes


async def test_start_refusal_3_splits_header_and_item(wired):
    lk, sim, cfg = wired
    runner = state.runner
    rec = two_blocks("결과3")
    tbl = R.to_plc_words(cfg, state.conv, rec)
    # (가) 표 머리 — 합계가 틀려 PLC 1 s 검사가 불합격
    words = list(tbl["words"])
    words[A.D_RCP_SUM - A.RCP_SUM_BASE] ^= 0x0101
    sim.write(A.RCP_SUM_BASE, words)
    assert await wait_until(lambda: sim.reg[A.D_RECIPE_OK] == 0, 3)
    msg = await runner._recipe_refusal(tbl)
    assert "표 머리(개수 · 합계)" in msg, msg
    # (나) 머리는 통과 — 그룹 반복 0 은 시작 때 적재에서 거절
    rec["groups"] = [{"from_block": 1, "to_block": 2, "repeat": 1}]
    t2 = R.to_plc_words(cfg, state.conv, rec)
    w2 = list(t2["words"])
    w2[A.D_RCP_GROUP_BASE + 2 - A.RCP_SUM_BASE] = 0
    w2[A.D_RCP_SUM - A.RCP_SUM_BASE] = 0
    w2[A.D_RCP_SUM - A.RCP_SUM_BASE] = R.checksum_of(w2)
    t2 = dict(t2, words=w2, checksum=R.checksum_of(w2))
    sim.write(A.RCP_SUM_BASE, w2)
    assert await wait_until(lambda: sim.reg[A.D_RECIPE_OK] == 1, 3)
    await pumped(lk, sim)
    r, _ = await lk.send_command(A.CMD_PROCESS_START)
    assert r == A.RESULT_RECIPE
    msg = await runner._recipe_refusal(t2)
    assert "블록 · 그룹 · 스텝 항목" in msg and "레시피 표 검증 실패" in msg, msg


# ===================== 4 · 5. 풀스케일 없는 MFC · PRM 기본값 =====================
def test_mfc_without_full_scale_rejects_nonzero(cfg):
    cfg["mfc"][1]["full_scale_sccm"] = None
    rec = short_recipe("풀스케일")
    rec["blocks"][0]["mfc_sccm"][1] = 100.0
    errs = R.validate(cfg, rec)["errors"]
    assert any("MFC2 풀스케일" in e["msg"] for e in errs), errs
    rec["blocks"][0]["mfc_sccm"][1] = 0.0
    assert not any("MFC2" in e["msg"] for e in R.validate(cfg, rec)["errors"])


def test_valve_min_default_comes_from_plc_default_table(cfg):
    from powderald.plclink import param_words
    cfg["params"].pop("valve_min_ms", None)
    assert R.prm_value(cfg, "valve_min_ms") == A.PRM_PLC_DEFAULTS[A.D_PRM_VALVE_MIN_MS] == 200
    assert param_words(cfg, Converters(cfg))[A.D_PRM_VALVE_MIN_MS][1] == 200
    rec = short_recipe("최소열림")                  # 첫 스텝 100 ms 에 밸브가 새로 열린다
    warns = R.validate(cfg, rec)["warnings"]
    assert any("200 ms 로 늘립니다" in w["msg"] for w in warns), warns
    cfg["params"]["valve_min_ms"] = 0              # 0 은 '최소 열림 없음' — 기본값으로 바꾸지 않는다
    assert not any("늘립니다" in w["msg"] for w in R.validate(cfg, rec)["warnings"])


# ===================== 6. 끝 판정 · 마지막 위치 · 스냅샷 =====================
def test_simulator_keeps_position_and_bumps_block_on_normal_end(cfg):
    from conftest import FakeSim
    rec = two_blocks("끝위치")
    for b in rec["blocks"]:
        b["repeat"] = 1
        b["steps"] = b["steps"][:1]
        b["steps"][0]["time_ms"] = 20
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    fs = FakeSim(cfg, o3=True)
    sim = fs.sim
    sim.write(A.RCP_SUM_BASE, tbl["words"])
    sim.reg.pc_set(A.D_PRM_MFC_STABLE, 0)
    sim.reg.pc_set(A.D_PRM_MFC_TOL, 0)
    assert sim._process_start() == A.RESULT_OK
    for _ in range(400):
        fs.step(1, 0.005)
        if not sim.running:
            break
    assert sim.reg[A.D_SEQ_STATE] == 6
    assert sim.reg[A.D_SEQ_BLOCK] == 3                     # 블록 수 + 1
    assert sim.reg[A.D_SEQ_STEP] == 2 and A.dword(sim.reg[A.D_SEQ_BLOCK_PASS], 0) == 1
    assert sim._process_start() == A.RESULT_OK             # 시작 때만 지운다
    fs.step(1)
    assert sim.reg[A.D_SEQ_BLOCK] == 1


def test_remaining_time_after_stop_after_cycle(cfg):
    rec = long_recipe("남은시간")
    pos = {"block": 1, "step": 1, "cycle": 3, "group_pass": 1, "step_elapsed_ms": 40}
    full = R.remaining_ms(cfg, rec, pos)
    cyc = R.remaining_ms(cfg, rec, dict(pos, stop_after_cycle=True))
    first, rep = R.step_times(rec["blocks"][0], R.prm_value(cfg, "valve_min_ms"))
    assert cyc == rep[0] - 40 + sum(rep[1:])
    assert full > cyc * 100
    # 일시정지 중 예약 — 멈춘 스텝(1)은 끝났고 이번 사이클의 나머지만
    paused = R.remaining_ms(cfg, rec, dict(pos, paused=True, stop_after_cycle=True))
    assert paused == R.effective_step_ms(rec["blocks"][0]["steps"][1]["time_ms"], False, 200)


async def test_stop_after_cycle_progress_shows_only_this_cycle(wired):
    lk, sim, cfg = wired
    await _start(lk, sim, cfg, "예약남은", long_recipe("예약남은"))
    assert await _seen_running()
    before = state.runner.progress()["remaining_ms"]
    await C.handle_command({"cmd": "process_pause"})
    await C.handle_command({"cmd": "process_stop_after_cycle"})
    p = state.runner.progress()
    assert p["stop_reserved"] and p["remaining_ms"] < 1000 < before, (p["remaining_ms"], before)
    await C.handle_command({"cmd": "process_abort"})


async def test_abort_after_end_does_not_change_result(wired, notices):
    """정상 종료 뒤에 누른 즉시 중단(거절)은 결과를 '운전자 중단'으로 바꾸지 않는다."""
    lk, sim, cfg = wired
    await _start(lk, sim, cfg, "끝난뒤", short_recipe("끝난뒤"))
    assert await _seen_running()
    seen, name = await _run_to_end()
    assert state.runner.last_result == "정상 종료", seen
    await C.handle_command({"cmd": "process_abort"})
    state.refresh()
    state.runner.tick(lambda lvl, msg: seen.append(msg))
    assert state.runner.last_result == "정상 종료"
    assert not state.runner._abort_sent
    assert _meta_and_list(name) == ("정상 종료", "정상 종료")


async def test_abort_after_safe_stop_keeps_safe_stop_reason(wired):
    lk, sim, cfg = wired
    await _start(lk, sim, cfg, "비상뒤")
    assert await _seen_running()
    sim.set_fault("emo", True)
    try:
        await asyncio.sleep(0.3)
        await C.handle_command({"cmd": "process_abort"})     # 이미 안전 정지 — 거절
        seen, name = await _run_to_end()
        assert state.runner.last_result.startswith("중단 (안전 정지 — ") and "비상정지" in \
            state.runner.last_result, state.runner.last_result
        assert "마지막 위치 블록 1 · 스텝 " in seen[0], seen
        assert _meta_and_list(name) == (state.runner.last_result,) * 2
    finally:
        sim.set_fault("emo", False)


async def test_second_process_datalog_uses_second_recipe(wired):
    """연속 두 공정 — 두 번째 데이터 로그 이름·레시피가 두 번째 것(스냅샷을 명령 1 직전에 세운다)."""
    from powderald import loops
    lk, sim, cfg = wired
    await _start(lk, sim, cfg, "첫째", short_recipe("첫째"))
    assert await _seen_running()
    await _run_to_end()
    assert state.runner.last_result == "정상 종료"
    # 두 번째 — 결과 0 보다 먼저 샘플링 루프가 '공정 중'을 보게, 시작과 나란히 tick 을 돌린다
    rec2 = short_recipe("둘째")
    assert storage.save("둘째", rec2)
    assert state.runner.select("둘째")[0]
    state.recipe_check = R.validate(cfg, rec2)

    async def sampler():
        for _ in range(1000):
            state.refresh()
            state.runner.tick(lambda lvl, msg: None)
            loops._datalog_tick()
            if state.datalog.name.endswith("둘째"):
                return
            await asyncio.sleep(0.02)
    task = asyncio.create_task(sampler())
    assert (await state.runner.start(_log, _notice))[0]
    await asyncio.wait_for(task, 10)
    assert state.datalog.name.endswith("_둘째"), state.datalog.name
    with open(os.path.join(paths.DATALOG_DIR, state.datalog.name + ".recipe.json"), encoding="utf-8") as f:
        assert json.load(f)["recipe"]["name"] == "둘째"
    await C.handle_command({"cmd": "process_abort"})


async def test_snapshot_restored_when_start_refused(wired, monkeypatch):
    lk, sim, cfg = wired
    await pumped(lk, sim)
    rec = short_recipe("거절")
    assert storage.save("거절", rec)
    assert state.runner.select("거절")[0]
    state.recipe_check = R.validate(cfg, rec)
    prev = {"name": "앞공정", "recipe": None, "table": None}
    state.runner.run = prev
    seen_run = []
    orig = lk.send_command

    async def refuse(code, args=None):
        if code == A.CMD_PROCESS_START:
            seen_run.append(state.runner.run)
            return A.RESULT_STATE, "상태 불가"
        return await orig(code, args)
    monkeypatch.setattr(lk, "send_command", refuse)
    ok, _msg = await state.runner.start(_log, _notice)
    assert not ok
    assert seen_run and seen_run[0]["name"] == "거절"       # 보내기 직전에 세웠고
    assert state.runner.run is prev                         # 거절되면 되돌렸다


# ===================== 7. Powder O3 발생기 =====================
def o3_recipe(name, o3):
    r = short_recipe(name)
    r["blocks"][0]["o3"] = o3
    return r


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 라인이 없는 장비")
async def test_o3_recipe_needs_generator_on(wired):
    lk, sim, cfg = wired
    cfg["process"]["o3_off_delay_s"] = 60          # 끄기 뒤에도 바이패스 라인(O3 허가)은 남긴다
    await pumped(lk, sim)
    await C.handle_command({"cmd": "manual_o3", "action": "off"})
    assert await wait_until(lambda: not A.bit(lk.status[A.D_AUX_OUT], A.AUX_O3_GEN), 3)
    assert A.bit(lk.status[A.D_INTERLOCK], A.ILK_O3_OK), "O3 허가는 남아 있어야 한다(래더는 이것만 본다)"
    # O3 를 쓰는 레시피 — 발생기가 꺼져 있으면 시작 거절
    rec = o3_recipe("오존", 50)
    assert storage.save("오존", rec)
    assert state.runner.select("오존")[0]
    state.recipe_check = R.validate(cfg, rec)
    checks = {c["key"]: c for c in state.runner.start_checks()}
    assert not checks["o3_gen"]["ok"] and checks["o3_gen"]["action"] == "o3_on"
    ok, msg = await state.runner.start(_log, _notice)
    assert not ok and "O3 발생기 켜짐" in msg, msg
    # O3 설정이 모두 0 인 레시피는 그대로
    rec0 = o3_recipe("오존없음", 0)
    assert storage.save("오존없음", rec0)
    assert state.runner.select("오존없음")[0]
    assert "o3_gen" not in {c["key"] for c in state.runner.start_checks()}
    # 켜면 통과
    assert state.runner.select("오존")[0]
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert await wait_until(lambda: A.bit(lk.status[A.D_AUX_OUT], A.AUX_O3_GEN), 10)
    ok, msg = await state.runner.start(_log, _notice)
    assert ok, msg
    await lk.send_command(A.CMD_ABORT)
    C._cancel_o3_timer()


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 라인이 없는 장비")
async def test_o3_on_refused_when_plc_o3_limit_zero(wired, notices):
    lk, sim, cfg = wired
    lk.prm_autofix = False
    sim.write(A.D_PRM_O3_MAX, [0])
    assert await wait_until(lambda: lk.prm_readback.get(A.D_PRM_O3_MAX) == 0, 3)
    before = sim.reg[A.D_MANUAL_AUX]
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert sim.reg[A.D_MANUAL_AUX] == before
    assert any("O3 한계" in m and "O3 허가가 나지 않습니다" in m for _l, m in notices), notices


# ===================== 8. 온도조절기 통신 없는 히터 채널 =====================
async def test_heater_power_refused_without_tc_comm(wired, notices):
    lk, sim, cfg = wired
    hs = {h["ch"]: h for h in cfg["heaters"]}
    a, b = 2, 5                                    # 국번 1 · 국번 2 (기본 배선 4채널씩)
    assert hs[a]["max_c"] is not None and hs[b]["max_c"] is not None
    sim.set_fault("tc1_comm", True)
    assert await wait_until(lambda: not A.bit(lk.status[A.D_TC_COMM], 0), 3)
    h2 = state.live()["heaters"][a - 1]
    assert h2["comm_ok"] is False and "과온 감시" in h2["power_block"]
    await C.handle_command({"cmd": "manual_heater", "power": {str(a): True}})
    assert not (sim.heater_power >> (a - 1)) & 1
    assert any("온도조절기 통신이 없어 PLC 과온 감시가 동작하지 않습니다" in m for _l, m in notices)
    # 다른 국번 채널은 켜진다
    await C.handle_command({"cmd": "manual_heater", "power": {str(b): True}})
    assert await wait_until(lambda: (sim.heater_power >> (b - 1)) & 1, 3)
    # 끄기는 언제나 — 통신이 없는 채널도
    sim.set_fault("tc1_comm", False)
    assert await wait_until(lambda: A.bit(lk.status[A.D_TC_COMM], 0), 3)
    await C.handle_command({"cmd": "manual_heater", "power": {str(a): True}})
    assert await wait_until(lambda: (sim.heater_power >> (a - 1)) & 1, 3)
    sim.set_fault("tc1_comm", True)
    assert await wait_until(lambda: not A.bit(lk.status[A.D_TC_COMM], 0), 3)
    await C.handle_command({"cmd": "manual_heater", "power": {str(a): False}})
    assert await wait_until(lambda: not (sim.heater_power >> (a - 1)) & 1, 3)
    # 설정 온도는 거절하지 않되 '전달 안 됨' 경고
    notices.clear()
    await C.handle_command({"cmd": "manual_heater", "sv": {str(a): 50}})
    assert any("온도조절기에 전달되지 않았습니다" in m for _l, m in notices), notices
    sim.set_fault("tc1_comm", False)


# ===================== 9. 작은 것 =====================
async def test_drop_reason_is_kept_for_queued_request(sim):
    from test_v044 import DropProxy
    from powderald.modbus import ModbusClient, ModbusError, ModbusTimeout
    s, port, _cfg = sim
    px = DropProxy(port, 0.0)
    await px.start()
    cl = ModbusClient("127.0.0.1", px.port, 1, 300)
    await cl.connect()
    try:
        px.drop_next = True
        r1, r2 = await asyncio.gather(cl.read_holding(0, 1), cl.read_holding(0, 1),
                                      return_exceptions=True)
        assert isinstance(r1, ModbusTimeout)
        assert isinstance(r2, ModbusError) and "앞 요청: 응답 시간 초과" in str(r2), repr(r2)
        await cl.connect()
        assert cl.drop_reason == ""
    finally:
        await cl.close()
        await px.stop()


async def test_datalog_list_says_writing_until_closed(wired):
    from powderald import logview
    from powderald.datalog import DataLog
    lk, sim, cfg = wired
    rec = short_recipe("기록중")
    dl = DataLog(state)
    dl.start("기록중", rec, R.to_plc_words(cfg, state.conv, rec), 1000)
    dl.tick(0)
    dl.note_end("중단 (운전자 중단)")              # 끝을 봤지만 꼬리를 쓰는 중
    item = next(i for i in logview.list_logs() if i["name"] == dl.name)
    assert item["result"] == "기록 중" and item["writing"] and not item["guessed"]
    dl.close()
    item = next(i for i in logview.list_logs() if i["name"] == dl.name)
    assert item["result"] == "중단 (운전자 중단)"


# ===================== 1 · 2. 화면 (headless 브라우저) =====================
@pytest.fixture
def served(cfg, tmp_path):
    """실제 서버(uvicorn) + 내장 시뮬레이터. 설정은 임시 파일."""
    uvicorn = pytest.importorskip("uvicorn")
    from powderald.server import create_app, uvicorn_config
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": True, "sim_port": free_port(), "sim_speed": 50})
    for m, fs in zip(raw.get("mfc") or [], (1000.0, 500.0)):
        if m.get("full_scale_sccm") is None:
            m["full_scale_sccm"] = fs
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    app = create_app(str(path), single_instance=False)
    port = free_port()
    server = uvicorn.Server(uvicorn_config(app, "127.0.0.1", port))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        yield port
    finally:
        server.should_exit = True
        th.join(10)
        state.link = None
        state.runner = None
        state.sim = None


async def _browser_page(pw, port):
    b = None
    for kw in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            b = await pw.chromium.launch(headless=True, **kw)
            break
        except Exception:  # noqa: BLE001
            continue
    if b is None:
        pytest.skip("headless 브라우저를 띄울 수 없습니다")
    page = await b.new_page()
    page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
    await page.goto(f"http://127.0.0.1:{port}/")
    # 기동 때 뜨는 알람 창이 클릭을 가리지 않게(이 시험은 편집기만 본다)
    await page.add_style_tag(content="#alarmModal, .modal:not(#confirmModal) { display: none !important; }")
    await page.wait_for_function("() => window.core && core.plcOk && core.plcOk() && core.canOperate()",
                                 timeout=20000)
    await page.evaluate("() => core.setTab('recipe')")
    return b, page


async def _open(page, name):
    await page.evaluate("(n) => app.send('recipe_load', {name: n})", name)
    await page.wait_for_function("(n) => (document.querySelector('[data-rcf=\"name\"]') || {}).value === n",
                                 arg=name, timeout=5000)


def _saved_groups(name):
    return (storage.load(name) or {}).get("groups")     # 저장 중(교체 순간)에 읽으면 None


async def _shows(page, bind, word, timeout=8000):
    """서버 검증 왕복(모아 보내기 250 ms + 처리)을 기다려 그 칸에 word 가 보일 때까지."""
    await page.wait_for_function(
        "([b, w]) => ((document.querySelector('[data-bind=\"' + b + '\"]') || {}).textContent || '')"
        ".includes(w)", arg=[bind, word], timeout=timeout)


async def test_editor_groups_in_browser(served):
    pw_api = pytest.importorskip("playwright.async_api")
    port = served
    rec = two_blocks("화면그룹")
    assert storage.save("화면그룹", rec)
    old = two_blocks("화면옛그룹")
    old["groups"] = [{"from": 1, "to": 2, "repeat": 4}]
    with open(storage.path_of("화면옛그룹"), "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False)

    async with pw_api.async_playwright() as pw:
        b, page = await _browser_page(pw, port)
        try:
            # 옛 키 파일 — 1 → 2 ×4 로 보인다('1 / 1 / 4' 가 아니라)
            await _open(page, "화면옛그룹")
            vals = await page.eval_on_selector_all(
                "[data-rcg]", "els => els.map(e => e.dataset.rcg + '=' + e.value)")
            assert vals == ["from_block=1", "to_block=2", "repeat=4"], vals

            # 그룹 추가 · 수정 · 저장
            await _open(page, "화면그룹")
            await page.click("[data-rcadd='group']")
            await page.fill("[data-rcg='from_block']", "1")
            await page.fill("[data-rcg='to_block']", "2")
            await page.fill("[data-rcg='repeat']", "3")
            await _shows(page, "rcSum", "그룹 1 /")          # 서버 검증 왕복이 끝났다
            errs = await page.inner_text("[data-bind='rcCheck']")
            assert "범위를 벗어납니다" not in errs, errs
            await page.click("[data-rcbtn='save']")
            assert await wait_until(lambda: _saved_groups("화면그룹")
                                    == [{"from_block": 1, "to_block": 2, "repeat": 3}], 10), \
                storage.load("화면그룹")
            # 저장 뒤 서버가 상태를 다시 보내면 편집기를 다시 그린다 — 그 순간에 친 값은 떨어져 나간
            # 입력 칸으로 갈 수 있어, 다시 그린 뒤에 고친다(부하가 크면 한 번 더)
            for _ in range(3):
                await page.wait_for_timeout(1000)
                await page.fill("[data-rcg='repeat']", "5")
                await _shows(page, "rcSum", "그룹 1 /")
                await page.click("[data-rcbtn='save']")
                if await wait_until(lambda: (_saved_groups("화면그룹") or [{}])[0].get("repeat") == 5, 5):
                    break
            assert (_saved_groups("화면그룹") or [{}])[0].get("repeat") == 5, storage.load("화면그룹")
            with open(storage.path_of("화면그룹"), encoding="utf-8") as f:
                assert '"from"' not in f.read()

            # PLC 표(그룹 항목)에 반영
            await page.evaluate("() => app.send('recipe_upload', {name: '화면그룹'})")
            sim = state.sim
            g = A.D_RCP_GROUP_BASE
            assert await wait_until(lambda: sim.reg[A.D_RCP_GROUP_COUNT] == 1
                                    and sim.reg[g:g + 3] == [1, 2, 5], 5), sim.reg[g:g + 3]

            # 32768 은 저장 거절(검증 오류)
            await page.wait_for_timeout(1000)                 # 올리기 뒤 다시 그리기가 끝나게
            await page.fill("[data-rcg='repeat']", "32768")
            await _shows(page, "rcCheck", "1~32767")
            await page.click("[data-rcbtn='save']")
            await asyncio.sleep(0.8)
            assert (_saved_groups("화면그룹") or [{}])[0].get("repeat") == 5
        finally:
            await b.close()


async def test_tampered_recipe_does_not_run_script_in_browser(served):
    pw_api = pytest.importorskip("playwright.async_api")
    port = served
    rec = two_blocks("조작화면")
    evil = '1"><img src=x onerror="window.__pwned=1">'
    rec["blocks"][0]["repeat"] = evil
    rec["blocks"][0]["mfc_sccm"][0] = evil
    rec["blocks"][0]["steps"][0]["time_ms"] = evil
    rec["groups"] = [{"from_block": evil, "to_block": 2, "repeat": evil}]
    with open(storage.path_of("조작화면"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    async with pw_api.async_playwright() as pw:
        b, page = await _browser_page(pw, port)
        try:
            await _open(page, "조작화면")
            await _shows(page, "rcCheck", "정수")
            assert await page.evaluate("() => window.__pwned") is None
            assert await page.evaluate("() => document.querySelectorAll('#view-recipe img').length") == 0
            val = await page.get_attribute("[data-rcb='repeat']", "value")
            assert val == evil                          # 값은 글자 그대로 들어갔다
            assert "정수" in await page.inner_text("[data-bind='rcCheck']")
        finally:
            await b.close()
