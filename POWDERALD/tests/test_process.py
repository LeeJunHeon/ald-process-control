"""레시피 올리기 · 공정 시작 흐름 · 수동 조작 규칙.

여기는 "PC 가 PLC 에게 실제로 무엇을 썼는가"를 본다. 화면 문구가 아니라 레지스터를 본다 —
운전자가 누른 것과 PLC 가 받은 것이 어긋나면 현장에서 가장 위험하다.
"""
import os
import copy
import asyncio

import pytest

from powderald import addresses as A
from powderald import commands as C
from powderald import device as DEV
from powderald import recipe as R
from powderald import storage
from powderald.convert import Converters
from powderald.process import ProcessRunner, IDLE, BASE_WAIT
from powderald.state import state

from conftest import wait_until


# ===================== 준비 =====================
def short_recipe(name="짧은 레시피"):
    r = R.empty_recipe(name)
    b = R.empty_block("b1")
    b["repeat"] = 2
    b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    b["steps"] = [
        {"name": "전구체", "time_ms": 100, "valves": [DEV.PRECURSOR_TAGS[0]], "pause_ok": False},
        {"name": "퍼지", "time_ms": 200, "valves": [], "pause_ok": True},
    ]
    if DEV.HAS_RF:
        for s in b["steps"]:
            s["rf"] = False
        b["rf_w"] = 0.0
    r["blocks"] = [b]
    return r


@pytest.fixture
def wired(link):
    """state 싱글턴을 시뮬레이터에 붙인 링크로 채운다(서버 create_app 과 같은 순서)."""
    lk, sim, cfg = link
    state.cfg = cfg
    state.conv = Converters(cfg)
    state.link = lk
    state.sim = sim
    state.runner = ProcessRunner(state)
    state.datalog = None
    state.recipe_check = {}
    state.plc_recipe = {}
    state.manual_unlock_until = 0.0
    state.alarms.clear_all()
    try:
        yield lk, sim, cfg
    finally:
        state.link = None
        state.runner = None
        state.sim = None


async def at_vacuum(lk, sim):
    """대기압 입력만 끈다 — 대기압이면 PLC 가 밸브 반영을 매 스캔 지운다."""
    sim.base_pressure = 1.0
    assert await wait_until(lambda: not A.bit(lk.status[A.D_INPUT0], A.IN0_ATM), 3)


async def valve_permit(lk, sim):
    """펌핑해 공정 밸브 허가(인터락 b4)를 받는다 — 허가가 없으면 PLC 가 밸브를 내보내지 않는다."""
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(lk.status[A.D_INTERLOCK], A.ILK_VALVE_OK), 20), \
        "공정 밸브 허가가 나오지 않았다"


def heater_channels(cfg, n=2):
    """과온 한계가 있고 명령 13 으로 전원을 다루는 사용 채널."""
    return [h["ch"] for h in (cfg.get("heaters") or [])
            if h.get("enabled") and h.get("max_c") is not None
            and DEV.HEATER_POWER_MASK & (1 << (h["ch"] - 1))][:n]


@pytest.fixture
def notices(monkeypatch):
    """화면 알림·로그를 모은다."""
    got = []

    async def fake_notice(msg, level="info", ws=None):
        got.append((level, msg))

    async def fake_log(msg, level="info"):
        got.append((level, msg))

    monkeypatch.setattr(C, "push_notice", fake_notice)
    monkeypatch.setattr(C, "push_log", fake_log)
    return got


async def pumped(lk, sim):
    """베이스 압력까지 뽑고, O3 가 있는 장비면 O3 허가까지 받는다 —
    시작 흐름 시험의 공통 전제(장비마다 시작 조건이 다르다)."""
    r, _ = await lk.send_command(A.CMD_PUMP_START)
    assert r == A.RESULT_OK
    # ★ 시뮬레이터 레지스터가 아니라 '링크가 읽어 둔 값'을 기다린다 —
    #   시작 판정은 PC 가 읽은 값으로 하므로, 여기서 앞서 가면 시작이 헛돈다.
    assert await wait_until(lambda: A.bit(lk.status[A.D_INTERLOCK], A.ILK_VACUUM), 20), \
        f"베이스 압력에 도달하지 못했다 (현재 {sim.pressure:g} Torr)"
    if DEV.HAS_O3:
        # 예시 설정은 O3 상한을 미정으로 두므로(현장에서 확인해야 하는 값) PLC 가 O3 를
        # 금지한다. 여기서는 시작 흐름을 보려는 것이라 상한만 심어 준다.
        if sim.reg[A.D_PRM_O3_MAX] == 0:
            sim.write(A.D_PRM_O3_MAX, [16000])
        await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
        assert await wait_until(lambda: A.bit(lk.status[A.D_INTERLOCK], A.ILK_O3_OK), 10), \
            "O3 허가가 나오지 않았다"


# ===================== 올리기 =====================
async def test_upload_puts_table_and_plc_accepts(wired):
    lk, sim, cfg = wired
    rec = short_recipe()
    tbl = R.to_plc_words(cfg, state.conv, rec)

    ok, detail = await lk.upload_recipe(tbl["words"], tbl["checksum"])
    assert ok, detail
    # PLC 가 스스로 합계를 계산해 통과 표시를 낸다
    assert sim.reg[A.D_RECIPE_OK] == 1
    assert sim.reg[A.D_RECIPE_SUM_PLC] == tbl["checksum"]
    assert sim.reg[A.D_RCP_NO] == tbl["number"]
    assert sim.reg[A.D_RCP_STEP_COUNT] == tbl["step_count"]


async def test_upload_rejected_when_table_is_corrupted(wired):
    """표가 깨지면 PLC 가 스스로 계산한 합계가 헤더와 달라 통과 표시를 내지 않는다."""
    lk, sim, cfg = wired
    tbl = R.to_plc_words(cfg, state.conv, short_recipe())
    words = list(tbl["words"])
    words[A.D_RCP_STEP_BASE - A.RCP_SUM_BASE] ^= 0x0001      # 본문 한 워드만 틀어 놓는다
    ok, detail = await lk.upload_recipe(words, tbl["checksum"])
    assert not ok
    assert sim.reg[A.D_RECIPE_OK] == 0
    assert detail


async def test_upload_fails_when_sent_checksum_differs(wired):
    """PC 가 보낸 합계와 PLC 가 계산한 합계가 다르면 성공으로 보지 않는다 —
    ★ 표는 올라갔을 수 있으므로 '올라갔다'고 말하지 않고 실패로 보고한다."""
    lk, _sim, cfg = wired
    tbl = R.to_plc_words(cfg, state.conv, short_recipe())
    ok, detail = await lk.upload_recipe(tbl["words"], (tbl["checksum"] + 1) & 0xFFFF)
    assert not ok
    assert "합계" in detail


async def test_read_back_recipe_area_matches(wired):
    lk, _sim, cfg = wired
    rec = short_recipe()
    tbl = R.to_plc_words(cfg, state.conv, rec)
    assert (await lk.upload_recipe(tbl["words"], tbl["checksum"]))[0]

    words = await lk.read_recipe_area()
    info = R.from_plc_words(words)
    assert info["number"] == tbl["number"]
    assert info["step_count"] == tbl["step_count"]
    assert info["block_count"] == len(rec["blocks"])


async def test_find_by_number_recovers_name(wired):
    """PC 를 다시 켰을 때 PLC 의 번호로 로컬 레시피 이름을 되찾는다."""
    _lk, _sim, _cfg = wired
    rec = short_recipe("이름찾기")
    assert storage.save("이름찾기", rec)
    assert storage.find_by_number(R.recipe_number(rec)) == "이름찾기"
    assert storage.find_by_number(0xFFFF) is None


# ===================== 시작 흐름 =====================
async def test_start_flow_reaches_run(wired):
    lk, sim, cfg = wired
    await pumped(lk, sim)
    rec = short_recipe("시작시험")
    assert storage.save("시작시험", rec)
    ok, why = state.runner.select("시작시험")
    assert ok, why
    state.recipe_check = R.validate(cfg, rec)

    ok, msg = await state.runner.start(_log, _notice)
    assert ok, msg
    assert await wait_until(lambda: sim.reg[A.D_STATE] == A.STATE_RUN, 5), \
        f"공정이 시작되지 않았다 (상태 {sim.reg[A.D_STATE]})"
    assert state.runner.phase == IDLE     # 흐름은 끝났고 이제 PLC 가 돌린다
    assert state.runner.table["number"] == sim.reg[A.D_RCP_NO]
    # ★ 공정이 시작되면 수동 잠금이 다시 채워진다
    assert state.manual_unlock_until == 0.0
    await lk.send_command(A.CMD_ABORT)


async def test_start_blocked_without_recipe(wired):
    lk, sim, _cfg = wired
    await pumped(lk, sim)
    ok, msg = await state.runner.start(_log, _notice)
    assert not ok and "레시피" in msg


async def test_start_blocked_when_recipe_has_errors(wired):
    lk, sim, cfg = wired
    await pumped(lk, sim)
    bad = short_recipe("오류레시피")
    bad["blocks"][0]["steps"][0]["time_ms"] = 0      # 최소 시간 미달
    assert storage.save("오류레시피", bad)
    assert state.runner.select("오류레시피")[0]
    state.recipe_check = R.validate(cfg, bad)
    ok, msg = await state.runner.start(_log, _notice)
    assert not ok and "검증 오류" in msg
    assert sim.reg[A.D_STATE] != A.STATE_RUN


async def test_base_wait_times_out(wired):
    """압력이 안 내려가면 제한 시간 뒤 시작을 포기한다 — 명령 1 을 보내지 않는다."""
    lk, sim, cfg = wired
    # 펌프만 돌리지 않으면 대기압 그대로다. 밸브 허가가 없으므로 can_start 가 막지 않도록
    # 리미트·상태만 통과시키고 베이스만 미달로 둔다.
    await pumped(lk, sim)
    sim.base_pressure = 500.0                       # 다시 올려 미달로 만든다
    lk.prm_autofix = False                     # 일부러 바꾼 PRM 을 링크가 되돌리지 않게
    sim.write(A.D_PRM_BASE_PRESS, [1])              # 사실상 도달 불가
    cfg["process"]["base_wait_timeout_s"] = 1.0
    rec = short_recipe("대기초과")
    assert storage.save("대기초과", rec)
    assert state.runner.select("대기초과")[0]
    state.recipe_check = R.validate(cfg, rec)

    ok, msg = await state.runner.start(_log, _notice)
    assert not ok and "베이스 압력" in msg
    assert state.runner.phase == IDLE
    assert sim.reg[A.D_STATE] != A.STATE_RUN


async def test_base_wait_can_be_cancelled(wired):
    lk, sim, cfg = wired
    await pumped(lk, sim)
    sim.base_pressure = 500.0
    lk.prm_autofix = False                     # 일부러 바꾼 PRM 을 링크가 되돌리지 않게
    sim.write(A.D_PRM_BASE_PRESS, [1])
    cfg["process"]["base_wait_timeout_s"] = 60.0
    rec = short_recipe("대기취소")
    assert storage.save("대기취소", rec)
    assert state.runner.select("대기취소")[0]
    state.recipe_check = R.validate(cfg, rec)

    task = asyncio.create_task(state.runner.start(_log, _notice))
    assert await wait_until(lambda: state.runner.phase == BASE_WAIT, 5)
    assert state.runner.cancel_wait()
    ok, msg = await asyncio.wait_for(task, 5)
    assert not ok and "취소" in msg
    assert sim.reg[A.D_STATE] != A.STATE_RUN


async def test_start_rejected_by_plc_without_recipe_table(wired):
    """표를 올리지 않고 명령 1 을 보내면 공정 시작 허가(b3 — 표 통과 포함)가 없어 결과 1."""
    lk, sim, _cfg = wired
    await pumped(lk, sim)
    assert sim.reg[A.D_RECIPE_OK] == 0
    result, _text = await lk.send_command(A.CMD_PROCESS_START)
    assert result == A.RESULT_INTERLOCK


# ===================== 수동 조작 =====================
async def test_manual_valve_needs_unlock(wired):
    lk, sim, _cfg = wired
    await at_vacuum(lk, sim)
    tag = DEV.RECIPE_VALVES[0]
    state.manual_unlock_until = 0.0
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": True})
    await asyncio.sleep(0.1)
    assert sim.man_valve == 0, "잠긴 상태에서 요청이 나갔다"

    await C.handle_command({"cmd": "manual_unlock", "on": True})
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": True})
    assert A.bit(sim.reg[A.D_MANUAL_VALVE], DEV.valve_bit(tag)), "PC 요청 영역에 쓰지 않았다"
    assert A.bit(sim.man_valve, DEV.valve_bit(tag)), "PLC 반영 영역에 들어가지 않았다"


async def test_manual_apply_writes_whole_request_set(wired):
    """★ 명령 12 는 한꺼번에 반영한다 — 매번 전체를 써야 빠진 값이 0 으로 지워지지 않는다."""
    lk, sim, _cfg = wired
    await at_vacuum(lk, sim)
    a, b = DEV.RECIPE_VALVES[0], DEV.RECIPE_VALVES[1]
    state.manual_unlock_until = 9e9
    await C.handle_command({"cmd": "manual_valve", "tag": a, "on": True})
    await C.handle_command({"cmd": "manual_valve", "tag": b, "on": True})
    want = (1 << DEV.valve_bit(a)) | (1 << DEV.valve_bit(b))
    assert sim.man_valve == want, f"먼저 연 밸브가 지워졌다 (PLC {sim.man_valve:#06x})"
    assert sim.reg[A.D_MANUAL_VALVE] == (want & 0xFFFF)


async def test_precursor_and_reactant_together_blocked_by_pc(wired):
    """PC 가 먼저 막는다 — PLC 는 둘 다 막고 중대 알람을 내므로 요청 자체를 보내지 않는다."""
    lk, sim, _cfg = wired
    if not (DEV.PRECURSOR_TAGS and DEV.REACTANT_TAGS):
        pytest.skip("이 장비에는 전구체·반응물 구분이 없다")
    await at_vacuum(lk, sim)
    state.manual_unlock_until = 9e9
    pre, rea = DEV.PRECURSOR_TAGS[0], DEV.REACTANT_TAGS[0]
    await C.handle_command({"cmd": "manual_valve", "tag": pre, "on": True})
    await C.handle_command({"cmd": "manual_valve", "tag": rea, "on": True})
    assert not (sim.man_valve & (1 << DEV.valve_bit(rea))), "동시 열기 요청이 나갔다"
    assert not (sim.reg[A.D_MANUAL_VALVE] & (1 << DEV.valve_bit(rea)))
    assert sim.man_valve & (1 << DEV.valve_bit(pre))


async def test_manual_rejected_during_process(wired):
    lk, sim, cfg = wired
    await pumped(lk, sim)
    rec = short_recipe("수동차단")
    assert storage.save("수동차단", rec)
    assert state.runner.select("수동차단")[0]
    state.recipe_check = R.validate(cfg, rec)
    assert (await state.runner.start(_log, _notice))[0]
    assert await wait_until(lambda: sim.reg[A.D_STATE] == A.STATE_RUN, 5)

    state.manual_unlock_until = 9e9
    before = sim.reg[A.D_MANUAL_VALVE]
    await C.handle_command({"cmd": "manual_valve",
                            "tag": DEV.RECIPE_VALVES[0], "on": True})
    assert sim.reg[A.D_MANUAL_VALVE] == before, "공정 중에 수동 요청이 나갔다"
    await lk.send_command(A.CMD_ABORT)


async def test_screen_request_follows_plc_when_cleared(wired):
    """PLC 가 전체 닫기로 반영을 지우면 화면의 '요청'도 0 이다 — 요청 표시는 PC 기억이
    아니라 PLC 반영 영역(D04012)이다. PC 영역(D01004)은 PLC 가 지우지 않는다."""
    lk, sim, _cfg = wired
    await at_vacuum(lk, sim)
    state.manual_unlock_until = 9e9
    tag = DEV.RECIPE_VALVES[0]
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": True})
    assert await wait_until(lambda: state.manual_state()["valve_request"] != 0, 2)
    r, _ = await lk.send_command(A.CMD_ALL_CLOSE)
    assert r == A.RESULT_OK
    assert await wait_until(lambda: state.manual_state()["valve_request"] == 0, 2)
    assert sim.reg[A.D_MANUAL_VALVE] != 0, "PLC 는 PC 영역을 지우지 않는다"


async def test_valve_not_revived_after_emo(wired):
    """PV-2 열기 → 비상정지 → 해제·리셋·펌핑 → PV-3 만 열기 → PV-2 는 되살아나지 않는다."""
    lk, sim, _cfg = wired
    a, b = DEV.RECIPE_VALVES[1], DEV.RECIPE_VALVES[2]
    await valve_permit(lk, sim)
    state.manual_unlock_until = 9e9
    await C.handle_command({"cmd": "manual_valve", "tag": a, "on": True})
    assert await wait_until(lambda: A.bit(sim.reg[A.D_VALVE_OUT], DEV.valve_bit(a)), 2)
    sim.set_fault("emo", True)
    assert await wait_until(lambda: sim.man_valve == 0, 2)
    sim.set_fault("emo", False)
    await lk.send_command(A.CMD_ALARM_RESET)
    assert await wait_until(lambda: not A.bit(lk.status[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ), 3)
    await valve_permit(lk, sim)
    await C.handle_command({"cmd": "manual_valve", "tag": b, "on": True})
    want = 1 << DEV.valve_bit(b)
    assert await wait_until(lambda: sim.reg[A.D_VALVE_OUT] == want, 2), \
        f"이전 밸브가 되살아났다 (D00010 = {sim.reg[A.D_VALVE_OUT]:#06x})"


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 라인이 있는 장비")
async def test_o3_line_not_revived_after_safe_stop(wired):
    """안전 정지로 꺼진 O3 라인이 밸브 하나 누른다고 다시 켜지지 않는다."""
    lk, sim, _cfg = wired
    sim.write(A.D_PRM_O3_MAX, [16000])
    await at_vacuum(lk, sim)
    state.manual_unlock_until = 9e9
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert await wait_until(lambda: sim.man_aux != 0, 2)
    sim.set_fault("emo", True)
    assert await wait_until(lambda: sim.man_aux == 0, 2)
    sim.set_fault("emo", False)
    await lk.send_command(A.CMD_ALARM_RESET)
    assert await wait_until(lambda: not A.bit(lk.status[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ), 3)
    await C.handle_command({"cmd": "manual_valve", "tag": DEV.RECIPE_VALVES[0], "on": True})
    await asyncio.sleep(0.2)
    assert sim.man_aux == 0, "O3 라인이 되살아났다"
    assert not (sim.reg[A.D_AUX_OUT] & DEV.AUX_CMD_MASK)


def _run_process_directly(sim, cfg, rec):
    """표를 올리고 PLC 쪽에서 공정을 시작했다가 끝낸다(블록 적재 → 공정 종료 정리)."""
    tbl = R.to_plc_words(cfg, state.conv, rec)
    sim.write(A.RCP_SUM_BASE, tbl["words"])
    assert sim._process_start() == A.RESULT_OK
    sim._process_end("시험 종료", aborted=True)


async def test_mfc_after_process_keeps_other_channels_zero(wired):
    """공정 뒤 MFC 한 채널만 바꿔도 다른 채널은 0 그대로(공정 전 수동값으로 돌아가지 않는다)."""
    lk, sim, cfg = wired
    for m in cfg["mfc"]:
        m["full_scale_sccm"] = m.get("full_scale_sccm") or 1000
    state.conv = Converters(cfg)
    all_on = {str(m["no"]): 50.0 for m in cfg["mfc"]}
    await C.handle_command({"cmd": "manual_mfc", "sccm": all_on})
    assert all(sim.ao[m["no"]] for m in cfg["mfc"])
    _run_process_directly(sim, cfg, short_recipe("공정뒤MFC"))
    assert all(sim.ao[m["no"]] == 0 for m in cfg["mfc"]), "공정 종료 때 MFC AO 는 0"
    await asyncio.sleep(0.05)
    await C.handle_command({"cmd": "manual_mfc", "sccm": {"1": 20.0}})
    assert sim.ao[1] == state.conv.mfc[1].to_raw(20.0)
    for m in cfg["mfc"][1:]:
        assert sim.ao[m["no"]] == 0, f"MFC{m['no']} 가 공정 전 수동값으로 돌아갔다"


async def test_valve_after_process_keeps_pcv_o3(wired):
    """공정 뒤 밸브 조작이 PCV 목표(PEALD)·O3 설정(Powder)을 바꾸지 않는다."""
    from powderald.simulator import AO_PCV, AO_O3
    lk, sim, cfg = wired
    await at_vacuum(lk, sim)
    state.manual_unlock_until = 9e9
    key = AO_PCV if DEV.HAS_PCV else AO_O3
    if DEV.HAS_PCV:
        await C.handle_command({"cmd": "manual_pcv", "pct": 30})
    else:
        sim.write(A.D_PRM_O3_MAX, [16000])
        await C.handle_command({"cmd": "manual_o3", "action": "set", "value": 30})
    manual_val = sim.ao[key]
    assert manual_val
    rec = short_recipe("공정뒤PCV")
    if DEV.HAS_PCV:
        rec["blocks"][0]["pcv_pct"] = 55.0
    else:
        rec["blocks"][0]["o3"] = 55.0
    _run_process_directly(sim, cfg, rec)
    after_process = sim.ao[key]
    assert after_process != manual_val, "블록 값으로 바뀌어 있어야 한다"
    await asyncio.sleep(0.05)
    await C.handle_command({"cmd": "manual_valve", "tag": DEV.RECIPE_VALVES[0], "on": True})
    assert sim.man_valve, "밸브가 반영되지 않았다"
    assert sim.ao[key] == after_process, \
        f"밸브 하나 눌렀는데 설정이 바뀌었다 ({after_process} → {sim.ao[key]})"


async def test_valve_at_atmosphere_reports_not_applied(wired, notices):
    """대기압에서 밸브 열기 → 결과 0 이어도 '반영 안 됨'과 이유를 알린다."""
    lk, sim, _cfg = wired
    assert await wait_until(lambda: A.bit(lk.status[A.D_INPUT0], A.IN0_ATM), 2)
    state.manual_unlock_until = 9e9
    tag = DEV.RECIPE_VALVES[0]
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": True})
    msgs = [m for _l, m in notices if "반영되지 않았습니다" in m]
    assert msgs, notices
    assert tag in msgs[0] and "대기압" in msgs[0]


async def test_manual_mfc_writes_raw_setpoint(wired):
    """PC 영역에 원시값을 쓰고 명령 14 로 AO 사본(D04121~)에 반영된다."""
    lk, sim, cfg = wired
    m = (cfg.get("mfc") or [])[0]
    if m.get("full_scale_sccm") is None:
        pytest.skip("풀스케일이 정해지지 않은 장비 설정이다")
    await C.handle_command({"cmd": "manual_mfc", "sccm": {str(m["no"]): 100.0}})
    want = state.conv.mfc[m["no"]].to_raw(100.0)
    assert sim.reg[A.D_MFC_SV + m["no"] - 1] == want
    assert sim.ao[m["no"]] == want, f"MFC AO 에 반영되지 않았다 ({sim.ao[m['no']]} ≠ {want})"


async def test_manual_mfc_out_of_range_rejected(wired):
    lk, sim, cfg = wired
    m = (cfg.get("mfc") or [])[0]
    fs = m.get("full_scale_sccm")
    if fs is None:
        pytest.skip("풀스케일이 정해지지 않은 장비 설정이다")
    before = sim.reg[A.D_MFC_SV + m["no"] - 1]
    await C.handle_command({"cmd": "manual_mfc", "sccm": {str(m["no"]): float(fs) + 1}})
    await asyncio.sleep(0.2)
    assert sim.reg[A.D_MFC_SV + m["no"] - 1] == before
    assert sim.ao[m["no"]] == 0


async def test_manual_heater_keeps_other_channels(wired):
    """전원 비트는 한꺼번에 반영된다 — 한 채널을 켤 때 다른 채널이 꺼지면 안 된다."""
    lk, sim, cfg = wired
    chs = heater_channels(cfg)
    if len(chs) < 2:
        pytest.skip("사용 채널이 둘 미만이다")
    await C.handle_command({"cmd": "manual_heater", "power": {str(chs[0]): True}})
    await C.handle_command({"cmd": "manual_heater", "power": {str(chs[1]): True}})
    want = (1 << (chs[0] - 1)) | (1 << (chs[1] - 1))
    assert sim.reg[A.D_HEATER_POWER] == want, \
        f"먼저 켠 채널이 꺼졌다 (PLC {sim.reg[A.D_HEATER_POWER]:#06x})"
    assert sim.heater_power == want


async def test_heater_display_follows_command_area(wired):
    """히터 목표·전원 표시는 연결 때 값에 고정되지 않는다 — 명령 13 성공 직후 바로 바뀐다."""
    lk, sim, cfg = wired
    chs = heater_channels(cfg, 1)
    if not chs:
        pytest.skip("한계가 정해진 사용 채널이 없다")
    ch = chs[0]
    await C.handle_command({"cmd": "manual_heater", "sv": {str(ch): 80}, "power": {str(ch): True}})
    h = state.live()["heaters"][ch - 1]
    assert h["sv"] == 80.0 and h["power"] is True
    await C.handle_command({"cmd": "manual_heater", "power": {str(ch): False}})
    assert state.live()["heaters"][ch - 1]["power"] is False, "켠 히터를 끌 수 없다"


async def test_heater_without_limit_power_rejected(wired, notices):
    """과온 한계가 없는 채널은 목표 온도뿐 아니라 전원 켜기도 거절한다."""
    lk, sim, cfg = wired
    h = next((x for x in cfg["heaters"] if DEV.HEATER_POWER_MASK & (1 << (x["ch"] - 1))), None)
    h["max_c"] = None
    await C.handle_command({"cmd": "manual_heater", "power": {str(h["ch"]): True}})
    assert sim.reg[A.D_HEATER_POWER] == 0 and sim.heater_power == 0
    assert any("한계" in m for _l, m in notices)


async def test_over_temp_trip_not_revived_by_other_channel(wired, notices):
    """과온 차단 뒤 다른 채널 히터 적용이 꺼진 채널을 되살리지 않는다."""
    lk, sim, cfg = wired
    chs = heater_channels(cfg)
    if len(chs) < 2:
        pytest.skip("사용 채널이 둘 미만이다")
    await C.handle_command({"cmd": "manual_heater", "power": {str(chs[0]): True}})
    assert sim.heater_power
    sim.set_fault("ot", True)
    assert await wait_until(lambda: sim.reg[A.D_HEATER_POWER] == 0, 3), "PC 가 D01010 을 0 으로 쓰지 않았다"
    # 래치 중 켜기는 PC 가 거절한다
    await C.handle_command({"cmd": "manual_heater", "power": {str(chs[1]): True}})
    assert sim.reg[A.D_HEATER_POWER] == 0
    assert any("과온" in m for _l, m in notices)
    sim.set_fault("ot", False)
    await lk.send_command(A.CMD_ALARM_RESET)
    assert await wait_until(lambda: not A.bit(lk.status[A.D_ALARM0], A.ALM0_OT), 2)
    await C.handle_command({"cmd": "manual_heater", "power": {str(chs[1]): True}})
    assert sim.heater_power == 1 << (chs[1] - 1), \
        f"꺼진 채널이 되살아났다 ({sim.heater_power:#06x})"


async def test_manual_heater_over_max_rejected(wired):
    lk, sim, cfg = wired
    h = next((x for x in (cfg.get("heaters") or [])
              if x.get("enabled") and x.get("max_c") is not None), None)
    if not h:
        pytest.skip("과온 한계가 정해진 사용 채널이 없다")
    before = sim.reg[A.D_HEATER_SV + h["ch"] - 1]
    await C.handle_command({"cmd": "manual_heater",
                            "sv": {str(h["ch"]): float(h["max_c"]) + 10}})
    await asyncio.sleep(0.2)
    assert sim.reg[A.D_HEATER_SV + h["ch"] - 1] == before


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 없는 장비")
async def test_rf_zero_watt_blocked(wired):
    """전력 0 으로 RF 를 켜면 PLC 가 요청을 지운다 — PC 가 먼저 막는다."""
    lk, sim, _cfg = wired
    await C.handle_command({"cmd": "manual_rf", "on": True, "watt": 0})
    assert not (sim.reg[A.D_MANUAL_AUX] & (1 << A.AUX_RF)), "0 W 로 RF 요청이 나갔다"
    await asyncio.sleep(0.2)
    assert not A.bit(sim.reg[A.D_AUX_OUT], A.AUX_RF)


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 없는 장비")
async def test_rf_over_limit_blocked(wired):
    lk, sim, cfg = wired
    lim = (cfg.get("params") or {}).get("rf_max_w")
    if lim is None:
        pytest.skip("RF 상한이 정해지지 않았다")
    await C.handle_command({"cmd": "manual_rf", "on": True, "watt": float(lim) + 1})
    assert not (sim.reg[A.D_MANUAL_AUX] & (1 << A.AUX_RF))


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 라인이 없는 장비")
async def test_o3_off_turns_generator_first(wired):
    """끄기는 발생기 먼저, 바이패스 라인은 지연 뒤에 — 서버 타이머가 마무리한다
    (화면을 닫거나 새로 고쳐도 진행)."""
    lk, sim, cfg = wired
    cfg["process"]["o3_off_delay_s"] = 0.5
    sim.write(A.D_PRM_O3_MAX, [16000])
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert await wait_until(lambda: sim.man_aux & (1 << A.AUX_O3_GEN), 2)
    await C.handle_command({"cmd": "manual_o3", "action": "off"})
    assert not (sim.man_aux & (1 << A.AUX_O3_GEN)), "발생기가 꺼지지 않았다"
    assert sim.man_aux & (1 << A.AUX_BYPASS_PUMP), "바이패스 펌프를 함께 꺼 버렸다"
    assert state.o3_off_at > 0 and state.manual_state()["o3_off_left_s"] >= 0
    assert await wait_until(lambda: sim.man_aux == 0, 3), "지연 뒤 바이패스 라인이 닫히지 않았다"
    assert state.o3_off_at == 0.0


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 라인이 있는 장비")
async def test_o3_finish_cancelled_when_plc_cleared_line(wired, notices):
    """지연 사이 PLC 가 라인을 지웠으면(안전 정지 등) 닫기를 취소하고 로그만 남긴다."""
    lk, sim, cfg = wired
    cfg["process"]["o3_off_delay_s"] = 0.5
    sim.write(A.D_PRM_O3_MAX, [16000])
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    await C.handle_command({"cmd": "manual_o3", "action": "off"})
    await lk.send_command(A.CMD_ALL_CLOSE)          # PLC 가 라인을 지운다
    assert await wait_until(lambda: any("닫기 취소" in m for _l, m in notices), 3), notices


@pytest.mark.skipif(DEV.HAS_O3, reason="O3 라인이 있는 장비")
async def test_o3_command_rejected_without_o3_line(wired):
    lk, sim, _cfg = wired
    before = sim.reg[A.D_MANUAL_AUX]
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert sim.reg[A.D_MANUAL_AUX] == before


# ===================== 종료 감지 · 데이터 로그 =====================
async def test_end_logged_once(wired):
    """★ 시작 직후에는 PLC 상태가 아직 '대기'로 읽힌다 — 그 한 번을 종료로 보면
    '정상 종료'가 먼저 찍히고 진짜 종료가 또 찍힌다(현장에서 두 번 돈 줄 안다)."""
    lk, sim, cfg = wired
    await pumped(lk, sim)
    rec = short_recipe("한번만")
    assert storage.save("한번만", rec)
    assert state.runner.select("한번만")[0]
    state.recipe_check = R.validate(cfg, rec)
    assert (await state.runner.start(_log, _notice))[0]

    seen = []
    for _ in range(300):                       # 공정이 끝날 때까지 tick 을 돌린다
        state.refresh()
        state.runner.tick(lambda lvl, msg: seen.append(msg))
        if seen:
            await asyncio.sleep(0.5)           # 끝난 뒤에도 조금 더 돌려 본다
            state.runner.tick(lambda lvl, msg: seen.append(msg))
            break
        await asyncio.sleep(0.05)
    ends = [m for m in seen if "공정" in m and "종료" in m]
    assert len(ends) == 1, f"종료 기록이 {len(ends)}번 남았다: {ends}"
    assert "정상 종료" in ends[0]


async def test_datalog_writes_csv(wired, tmp_path):
    lk, sim, cfg = wired
    from powderald.datalog import DataLog
    from powderald import paths
    rec = short_recipe("로그시험")
    tbl = R.to_plc_words(cfg, state.conv, rec)
    dl = DataLog(state)
    dl.start("로그시험", rec, tbl, 4000)
    assert dl.active, dl.error
    for _ in range(3):
        dl._next = 0.0                          # 간격을 기다리지 않고 바로 한 줄
        dl.tick(1)
    path = dl.path
    dl.close()

    import csv
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 4, "머리글 + 3줄이어야 한다"
    assert len(rows[0]) == len(rows[1]), "머리글과 값의 열 수가 다르다"
    assert rows[0][0] == "시각" and "CVG Torr" in rows[0]
    assert os.path.exists(os.path.join(paths.DATALOG_DIR, dl.name + ".recipe.json"))


async def test_datalog_survives_write_failure(wired):
    """★ 기록을 못 해도 공정은 계속 돌아야 한다 — 로그로만 알리고 조용히 그만둔다."""
    lk, _sim, cfg = wired
    from powderald.datalog import DataLog
    dl = DataLog(state)
    dl.start("실패시험", short_recipe(), {}, 1000)
    assert dl.active
    dl.fp.close()                               # 파일을 밖에서 닫아 쓰기를 실패시킨다
    dl._next = 0.0
    dl.tick(1)                                  # 예외가 밖으로 나오면 안 된다
    assert not dl.active
    assert dl.error


async def test_datalog_new_file_on_back_to_back_start(wired, monkeypatch):
    """앞 파일이 종료 꼬리(TAIL_S)를 쓰는 중에 다음 공정이 시작되면
    앞 파일을 바로 닫고 새 파일을 연다 — 새 공정 줄이 앞 파일에 섞이지 않는다."""
    lk, _sim, _cfg = wired
    from powderald import datalog as DL
    from powderald.datalog import DataLog
    dl = DataLog(state)
    stamps = iter(["20260101_000001", "20260101_000002", "20260101_000003"])
    monkeypatch.setattr(DL.time, "strftime",
                        lambda fmt, *a: next(stamps) if fmt == "%Y%m%d_%H%M%S" else "x")
    starts = []

    def start():
        dl.start("연속", short_recipe(), {}, 1000)
        starts.append(dl.name)

    dl.follow(True, start)
    first = dl.name
    dl.follow(False, start)                     # 끝 — 꼬리를 쓰는 중
    assert dl.active and dl.stop_at
    dl.follow(True, start)                      # 4 s 뒤 다시 시작(꼬리 5 s 안)
    assert len(starts) == 2 and dl.name != first, "새 파일이 생기지 않았다"
    assert dl.active and dl.stop_at == 0.0, "공정 중인데 종료 표시가 남았다"
    dl.follow(True, start)
    assert len(starts) == 2, "공정 중에 파일을 또 열었다"
    dl.close()


# ===================== 사전 판정 =====================
async def test_precheck_pause_when_paused(wired, monkeypatch):
    lk, _sim, _cfg = wired
    st = list(lk.status)
    st[A.D_STATE] = A.STATE_PAUSE
    monkeypatch.setattr(lk, "status", st)
    ok, why = C.precheck(A.CMD_PAUSE)
    assert not ok and "이미 일시정지" in why


async def test_precheck_vent_allowed_while_pumping(wired):
    """벤트는 IV-E 가 열려 있어도(펌핑 중) PC 가 막지 않는다 — PLC 가 IV-E 를 닫고 VV 를 연다."""
    lk, sim, _cfg = wired
    await lk.send_command(A.CMD_PUMP_START)
    assert await wait_until(lambda: A.bit(lk.status[A.D_INPUT0], A.IN0_IVE_OPEN), 5)
    ok, why = C.precheck(A.CMD_VENT)
    assert ok, why


async def test_precheck_pump_uses_emo_input_not_latch(wired):
    """비상정지를 푼 뒤 알람 리셋 전에도 펌핑 시작을 PC 가 막지 않는다(PLC 는 입력을 본다)."""
    lk, sim, _cfg = wired
    sim.set_fault("emo", True)
    assert await wait_until(lambda: A.bit(lk.status[A.D_ALARM0], A.ALM0_EMO), 2)
    assert not C.precheck(A.CMD_PUMP_START)[0]
    assert not C.precheck(A.CMD_VENT)[0]
    sim.set_fault("emo", False)
    assert await wait_until(lambda: A.bit(lk.status[A.D_INPUT0], A.IN0_EMO), 2)
    assert A.bit(lk.status[A.D_ALARM0], A.ALM0_EMO)
    assert C.precheck(A.CMD_PUMP_START)[0]


# ===================== 도우미 =====================
async def _log(msg, level="info"):
    pass


async def _notice(msg, level="info", ws=None):
    pass



# ===================== v0.4.1 출력 단계 · 대기 표시 =====================
async def test_manual_valve_waits_without_permit(wired):
    """공정 밸브 허가가 없으면(펌프 정지) 요청은 반영되지만 출력은 0 — 화면에는 '대기'."""
    lk, sim, _cfg = wired
    await at_vacuum(lk, sim)
    state.manual_unlock_until = 9e9
    tag = DEV.RECIPE_VALVES[2]
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": True})
    assert await wait_until(lambda: state.manual_state()["valve_request"] != 0, 2)
    await asyncio.sleep(0.2)
    assert sim.reg[A.D_VALVE_OUT] == 0, "허가 없이 밸브가 나갔다"
    assert tag in state.manual_state()["pending"], "대기 표시가 없다"
    await valve_permit(lk, sim)
    assert await wait_until(lambda: A.bit(sim.reg[A.D_VALVE_OUT], DEV.valve_bit(tag)), 2)
    assert await wait_until(lambda: tag not in state.manual_state()["pending"], 2)



# ===================== v0.4.2 공정 끝 결과 =====================
def long_recipe(name):
    r = short_recipe(name)
    r["blocks"][0]["repeat"] = 500              # 몇 분짜리 — 시험 중에는 끝나지 않는다
    return r


async def _start(lk, sim, cfg, name, rec=None):
    from powderald.datalog import DataLog
    await pumped(lk, sim)
    rec = rec or long_recipe(name)
    assert storage.save(name, rec)
    assert state.runner.select(name)[0]
    state.recipe_check = R.validate(cfg, rec)
    state.datalog = DataLog(state)
    assert (await state.runner.start(_log, _notice))[0]
    assert await wait_until(lambda: lk.status[A.D_STATE] in (A.STATE_READY, A.STATE_RUN), 5)


async def _seen_running(timeout=5.0):
    """★ 샘플링 루프가 '공정 중'을 한 번은 보게 한다. 중단이 첫 tick 보다 먼저 끝나면
    종료 감지가 공정 중을 본 적이 없어 끝을 기록하지 않는다(시험 경쟁). 데이터 로그도 같이."""
    from powderald import loops
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        state.refresh()
        state.runner.tick(lambda lvl, msg: None)
        loops._datalog_tick()
        if state.runner._was_running:
            return True
        await asyncio.sleep(0.05)
    return False


async def _run_to_end(timeout=30.0):
    """샘플링 루프가 하는 것(알람 갱신 → 종료 감지 → 데이터 로그)을 끝날 때까지 돌린다."""
    from powderald import loops
    seen = []
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        state.refresh()
        state.runner.tick(lambda lvl, msg: seen.append(msg))
        loops._datalog_tick()
        if seen:
            break
        await asyncio.sleep(0.05)
    dl = state.datalog
    name = dl.name
    dl.close()
    return seen, name


def _meta_and_list(name):
    import json
    from powderald import logview, paths
    meta = json.load(open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), encoding="utf-8"))
    item = next(i for i in logview.list_logs() if i["name"] == name)
    return meta["result"], item["result"]


@pytest.mark.parametrize("how,want", [
    ("pc_hb", "PC 통신 끊김"),
    ("emo", "비상정지"),
    ("mfc", "MFC 이상"),
])
async def test_aborted_process_is_never_normal(wired, how, want):
    """PC 하트비트 끊김·비상정지·MFC 편차 중단 → 모두 '중단 (안전 정지 — 알람)'.
    이벤트 로그·데이터 로그 메타·목록이 같은 결과를 쓴다."""
    lk, sim, cfg = wired
    await _start(lk, sim, cfg, f"중단{how}")
    await asyncio.sleep(0.3)
    assert await _seen_running()
    if how == "pc_hb":
        lk.prm_autofix = False                     # 일부러 바꾼 PRM 을 링크가 되돌리지 않게
        sim.write(A.D_PRM_PC_WDT_MS, [300])
        sim.set_fault("pc_hb_stop", True)
    elif how == "emo":
        sim.set_fault("emo", True)
    else:
        sim._latch0(A.ALM0_MFC)
        sim._process_end("공정 중 MFC 편차", aborted=True)
    seen, name = await _run_to_end()
    msg = next(m for m in seen if m.startswith("공정 "))
    assert "정상 종료" not in msg and "중단" in msg and want in msg, msg
    assert state.runner.last_result.startswith("중단 (안전 정지")
    meta_res, list_res = _meta_and_list(name)
    assert meta_res == list_res == state.runner.last_result
    for k in ("pc_hb_stop", "emo"):
        sim.set_fault(k, False)


async def test_operator_abort_result(wired):
    lk, sim, cfg = wired
    await _start(lk, sim, cfg, "운전자중단")
    await asyncio.sleep(0.3)
    assert await _seen_running()
    await C.handle_command({"cmd": "process_abort"})
    seen, name = await _run_to_end()
    assert state.runner.last_result == "중단 (운전자 중단)"
    assert _meta_and_list(name) == ("중단 (운전자 중단)", "중단 (운전자 중단)")


@pytest.mark.parametrize("how", ["stop_after_cycle", "normal"])
async def test_normal_end_results(wired, how):
    """둘 다 시퀀서 6(완료)이지만 래더에서 정상 완료는 D00021 > 블록 수, 사이클 후 정지는
    D00021 ≤ 블록 수 — '정상 종료' 와 '사이클 후 정지 (블록 b · 사이클 c/R)' 로 나눈다."""
    lk, sim, cfg = wired
    rec = long_recipe("정상" + how) if how == "stop_after_cycle" else short_recipe("정상" + how)
    await _start(lk, sim, cfg, "정상" + how, rec)
    assert await _seen_running()
    if how == "stop_after_cycle":
        await asyncio.sleep(0.3)
        await C.handle_command({"cmd": "process_stop_after_cycle"})
    seen, name = await _run_to_end()
    res = state.runner.last_result
    if how == "normal":
        assert res == "정상 종료", seen
    else:
        assert res.startswith("사이클 후 정지 (블록 1 · 사이클 ") and res.endswith("/500)"), res
    assert _meta_and_list(name) == (res, res)
    # 마지막 위치는 공정 중에 본 값 — 끝난 뒤의 '블록 N+1' 이나 0 이 아니다
    assert "마지막 위치 블록 1 · 스텝 " in seen[0], seen


def test_end_result_rules():
    """시퀀서 6 + D00021 > 블록 수 → 정상 종료, ≤ 블록 수 → 사이클 후 정지.
    안전 정지(장비 상태 6)가 운전자 중단보다 앞서고, 끝 상태를 모르면 '끝 확인 안 됨'."""
    runner = ProcessRunner(state)
    state.alarms.clear_all()
    rec = short_recipe("규칙")
    rec["blocks"].append(copy.deepcopy(rec["blocks"][0]))
    rec["blocks"][1]["repeat"] = 7
    runner.run = {"name": "규칙", "recipe": rec, "table": {"block_count": 2}}
    s = [0] * A.STATUS_COUNT
    s[A.D_STATE], s[A.D_SEQ_STATE] = A.STATE_IDLE, 6
    s[A.D_SEQ_BLOCK] = 3
    assert runner.end_result(s) == "정상 종료"
    s[A.D_SEQ_BLOCK], s[A.D_SEQ_BLOCK_PASS] = 2, 4
    assert runner.end_result(s) == "사이클 후 정지 (블록 2 · 사이클 4/7)"
    s[A.D_SEQ_STATE] = 8
    assert runner.end_result(s) == "중단 (PLC 중단)"
    s[A.D_SEQ_STATE] = 4                         # 예전 시뮬레이터처럼 '6 + 4' 가 읽혀도
    assert runner.end_result(s) == "중단 (끝 확인 안 됨)"
    runner.abort_begin()
    runner.abort_result(True)                    # 처리됨(0) — 운전자 중단
    s[A.D_SEQ_STATE] = 8
    assert runner.end_result(s) == "중단 (운전자 중단)"
    s[A.D_STATE] = A.STATE_SAFE_STOP             # 안전 정지 사유가 앞선다
    assert runner.end_result(s).startswith("중단 (안전 정지")
    runner2 = ProcessRunner(state)
    runner2.abort_begin()
    runner2.abort_result(False)                  # 거절·통신 오류면 적지 않는다
    s[A.D_STATE] = A.STATE_IDLE
    assert runner2.end_result(s) == "중단 (PLC 중단)"


def test_simulator_aborts_sequencer_in_same_scan(cfg):
    """래더와 같게 — 안전 정지 요구가 서는 스캔에 시퀀서도 8 이 된다(6 + 4 가 읽히지 않는다)."""
    import time as _t
    from powderald.simulator import PlcSim
    sim = PlcSim(cfg, 1)
    tbl = R.to_plc_words(cfg, Converters(cfg), long_recipe("같은스캔"))
    sim.write(A.RCP_SUM_BASE, tbl["words"])
    assert sim._process_start() == A.RESULT_OK
    sim.tick()
    sim.set_fault("emo", True)
    _t.sleep(0.02)
    sim.tick()
    assert sim.reg[A.D_STATE] == A.STATE_SAFE_STOP
    assert sim.reg[A.D_SEQ_STATE] == 8, "안전 정지 스캔에 시퀀서가 멈추지 않았다"



# ===================== v0.4.3 공정 시작 조건 — 장비 ID =====================
@pytest.mark.parametrize("st", ["wrong", "missing"])
async def test_start_refused_with_device_id_message(wired, monkeypatch, st):
    from powderald.plclink import ID_BLOCK_TEXT
    lk, sim, cfg = wired
    rec = short_recipe("아이디시험")
    assert storage.save("아이디시험", rec)
    assert state.runner.select("아이디시험")[0]
    state.recipe_check = R.validate(cfg, rec)
    monkeypatch.setattr(lk, "id_state", st)
    monkeypatch.setattr(lk, "write_ok", False)
    checks = state.runner.start_checks()
    assert [c["key"] for c in checks] == ["plc", "device_id"], "막혔으면 장비 ID 항목에서 끝난다"
    assert not checks[1]["ok"] and "이 장비" in checks[1]["detail"]
    ok, msg = await state.runner.start(_log, _notice)
    assert not ok and msg == ID_BLOCK_TEXT[st]
    assert C.precheck(A.CMD_PUMP_START) == (False, ID_BLOCK_TEXT[st])


def test_start_checks_no_address_text(monkeypatch):
    import types
    from powderald.process import NO_ADDR_TEXT
    runner = ProcessRunner(state)
    monkeypatch.setattr(state, "link", types.SimpleNamespace(
        connected=False, config_error="plc.host 가 비어 있습니다", addr_text="주소 없음", plc_hb_ok=False))
    checks = runner.start_checks()
    assert checks[0]["detail"] == NO_ADDR_TEXT
