"""v0.8.5 — 두 PLC 래더에 새로 들어간 렁 세 가지와 그에 맞춘 PC 동작.

원칙: 요청이 인터락 등으로 실행되지 못하면 PLC 가 그 요청도 지운다 — 인터락이 풀린 뒤 옛 요청이 뒤늦게 실행되지 않는다.

A. P35 — SEQ_RUN AND NOT Valve Permit 0.5 s → 알람1 b8 'Valve Permit Lost in Process'(중대) → 다음 스캔 공정 중단.
   0.5 s 미만은 알람 없음 · 다른 알람이 먼저 공정을 끊으면 b8 은 서지 않음 · 공정 중 대기압 입력 · 빠진 조건 로그 한 줄.
B. P45 — IV-E 동작 이상(알람1 b0) 래치 중에는 벤트 요청을 매 스캔 지운다. PC 는 벤트를 보내기 전에 거절한다.
C. P50(Powder) — O3 허가가 없는 동안 수동 보조 반영에서 O3 발생기(b9)만 지운다. PC 는 O3 라인을 두 단계로 켠다
   (바이패스 펌프 + IV-B → O3 허가를 기다림 → 발생기) — 정상 · 제한 시간 초과 · 중간 취소.
★ 두 폴더 같은 파일이다(패키지 이름만 다르다) — 장비 차이는 DEV.HAS_O3 로 가른다.
"""
import json
import time
import asyncio
import threading

import pytest

from powderald import addresses as A
from powderald import commands as C
from powderald import config as CF
from powderald import device as DEV
from powderald import paths
from powderald import process as P
from powderald import settings
from powderald import simulator as SIM
from powderald import version
from powderald.state import state

from conftest import FakeSim, free_port, wait_until
from test_process import wired, notices  # noqa: F401 (픽스처)
from test_v084 import Rig

o3_only = pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
VP = A.ALM1_VALVE_PERMIT
VP_NAME = "Valve Permit Lost in Process"
LINE = (1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB)
GEN = 1 << A.AUX_O3_GEN
O3_FOLLOW = " · 뒤따름: O3 Generator Alarm / O3 Permit Lost"       # Powder: 공정 중 안전 정지면 래더가 알람1 b3 도 세운다


def test_version_is_at_least_085():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")[:3]) >= (0, 8, 5)


def test_alarm1_b8_definition_on_both_devices():
    d = next(a for a in DEV.ALARMS1 if a["bit"] == VP)
    assert d["name"] == VP_NAME and d["crit"] is True
    # 원인 · 조치 안내 — 빠진 조건이 무엇인지 확인하라고 적는다
    for word in ("펌프 운전", "IV-E 열림 리미트", "기본 인터락", "대기압"):
        assert word in d["check"], (word, d["check"])
    assert "0.5 s" in d["cause"]
    assert A.ALM1_VALVE_PERMIT == 8
    assert len({a["bit"] for a in DEV.ALARMS1}) == len(DEV.ALARMS1)       # 비트가 겹치지 않는다


# ===================== A. 공정 중 Valve Permit 꺼짐 =====================
def _lose(rig, cause):
    """공정 중 Valve Permit 을 끄는 원인 하나 — pump_nofb = 펌프 운전 입력 꺼짐 · ive_stuck = IV-E 열림 리미트 꺼짐 ·
    atm_sw = 챔버 대기압 입력 켜짐(압력은 그대로)."""
    rig.s.set_fault(cause, True)


def _restore(rig, cause):
    rig.s.set_fault(cause, False)


def _scans_until_b8(rig, limit_s=2.0):
    """알람1 b8 이 D00006 에 보일 때까지의 스캔 수(20 ms) — 안 서면 None."""
    for n in range(1, int(limit_s / 0.02) + 1):
        rig.run(0.02)
        if rig.alarm(1, VP):
            return n
    return None


@pytest.mark.parametrize("cause", ["ive_stuck", "atm_sw"])
def test_valve_permit_lost_shorter_than_half_second_is_ignored(cfg, cause):
    """0.5 s 미만으로 꺼졌다 돌아오면 알람이 없고 공정이 이어진다 — 꺼진 시간은 이어지지 않는다(TON 은 입력이 꺼지면 0).
    (펌프 운전 입력은 아래 시험 — 그 입력이 빠지면 IV-E 출력도 닫혀 허가가 더 오래 꺼진다.)"""
    rig = Rig(cfg, True)
    s = rig.s
    for _ in range(3):                                  # 0.4 s 꺼짐 × 3 (사이에 0.1 s 정상) — 합치면 1.2 s 지만 알람 없음
        _lose(rig, cause)
        rig.run(0.4)
        assert not rig.ilk(A.ILK_VALVE_OK) and s.reg[A.D_VALVE_OUT] & DEV.MANUAL_VALVE_MASK == 0   # 그동안 밸브는 막힌다
        assert not rig.alarm(1, VP) and s.running
        _restore(rig, cause)
        rig.run(0.1)
        assert rig.ilk(A.ILK_VALVE_OK)
    rig.run(1.0)
    assert s.reg[A.D_ALARM0] == 0 and s.reg[A.D_ALARM1] == 0
    assert s.running and s.reg[A.D_STATE] == A.STATE_RUN and not rig.ilk(A.ILK_SAFE_STOP_REQ)
    assert rig.run(3.0, cond=lambda: s.reg[A.D_VALVE_OUT] & DEV.MANUAL_VALVE_MASK), "허가가 돌아왔는데 밸브가 다시 열리지 않는다"


def test_short_pump_feedback_dropout_keeps_permit_off_until_ive_reports_open(cfg):
    """펌프 운전 입력이 0.2 s 만 빠져도 그 스캔에 IV-E 출력이 닫힌다(P45: 배기 요청 AND 펌프 허가). 입력이 돌아와 IV-E 가
    다시 열려도 열림 리미트가 들어올 때까지(시뮬레이터 1 s) Valve Permit 이 없다 — 꺼진 시간이 0.5 s 를 넘으면 b8 이 선다."""
    rig = Rig(cfg, True)
    s = rig.s
    _lose(rig, "pump_nofb")
    rig.run(0.2)
    assert not A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE) and not rig.alarm(1, VP)
    _restore(rig, "pump_nofb")
    rig.run(0.1)
    assert A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE) and not rig.ilk(A.ILK_VALVE_OK)     # 출력은 돌아왔지만 리미트 전
    assert rig.run(1.0, cond=lambda: rig.alarm(1, VP)), "허가가 0.5 s 넘게 꺼졌는데 알람이 없다"
    rig.run(0.1)
    assert not s.running and not rig.alarm(0, A.ALM0_PUMP)


@pytest.mark.parametrize("cause", ["pump_nofb", "ive_stuck", "atm_sw"])
def test_valve_permit_lost_half_second_latches_b8_and_aborts_next_scan(cfg, cause):
    """0.5 s 이어지면 알람1 b8 → 다음 스캔 안전 정지 요구 → 공정 중단. 펌프 피드백(10 s) · IV-E(5 s) 알람보다 먼저다."""
    rig = Rig(cfg, True)
    s = rig.s
    _lose(rig, cause)
    n = _scans_until_b8(rig)
    # 첫 스캔에 허가가 꺼지고 타이머가 돈다 — 0.5 s(25 스캔) 뒤 스캔의 P35 가 래치한다
    assert n == 26, f"b8 까지 {n} 스캔"
    # 그 스캔: 알람은 섰지만 SEQ_RUN 은 아직 1(P30 이 먼저 돌았다) — 안전 정지 요구는 다음 스캔
    assert s.running and s.reg[A.D_STATE] != A.STATE_SAFE_STOP and not rig.ilk(A.ILK_SAFE_STOP_REQ)
    assert s.reg[A.D_ALARM_NEW] == 1
    rig.run(0.02)
    assert not s.running and s.reg[A.D_SEQ_STATE] == 8 and s.reg[A.D_STATE] == A.STATE_SAFE_STOP
    assert rig.ilk(A.ILK_SAFE_STOP_REQ) and s.reg[A.D_VALVE_OUT] == 0
    assert all(s.ao[SIM.ao_mfc(no)] == 0 for no in range(1, DEV.MFC_COUNT + 1))
    # 원래의 늦은 알람은 아직 없다 — 이 알람이 먼저 공정을 끊었다
    assert not rig.alarm(0, A.ALM0_PUMP) and not rig.alarm(1, A.ALM1_IVE)
    assert A.bit(s.reg[A.D_AUX_OUT], A.AUX_PUMP)                    # 펌프는 그대로(요청을 지우는 알람이 아니다)
    # 끝 결과 문구 — 러너와 같은 규칙(Powder 는 공정 중 안전 정지에 알람1 b3 이 뒤따른다)
    rig.run(0.1)
    snap = list(s.reg[0:A.STATUS_COUNT])
    runner = P.ProcessRunner(state)
    res = P.judge_end(snap, {"words": rig.table["words"]}, None, crit_text=runner._crit_text(snap))
    assert res == f"중단 (안전 정지 — {VP_NAME}{O3_FOLLOW if DEV.HAS_O3 else ''})", res
    assert P.result_level(res) == P.result_level("중단 (안전 정지 — Emergency Stop)")
    # 알람 리셋은 워드째 지운다 — 공정이 끝났으니 원인(SEQ_RUN)이 없어 다시 서지 않는다
    assert rig.fs.cmd(A.CMD_ALARM_RESET) == A.RESULT_OK
    rig.run(0.3)
    assert not rig.alarm(1, VP) and s.reg[A.D_ALARM1] == 0 and s.reg[A.D_STATE] == A.STATE_IDLE


@pytest.mark.parametrize("key,alm", [("emo", A.ALM0_EMO), ("lid", A.ALM0_LID), ("cw", A.ALM0_CW), ("air", A.ALM0_AIR),
                                     ("n2", A.ALM0_N2), ("pump_alm", A.ALM0_PUMP), ("leak", A.ALM0_LEAK)])
def test_other_alarm_that_aborts_first_leaves_b8_off(cfg, key, alm):
    """비상정지처럼 다른 알람이 먼저 공정을 끊으면 SEQ_RUN 이 0.5 s 안에 꺼져 b8 은 서지 않는다 — 원인 알람만 남는다."""
    rig = Rig(cfg, True)
    s = rig.s
    s.set_fault(key, True)
    seen_b8 = False
    for _ in range(150):                                # 3 s — 공압 · N2 는 1 s 뒤에 선다
        rig.run(0.02)
        seen_b8 = seen_b8 or rig.alarm(1, VP)
    assert rig.alarm(0, alm) and not s.running and s.reg[A.D_STATE] == A.STATE_SAFE_STOP
    assert not seen_b8 and not rig.alarm(1, VP), "원인 알람이 먼저 끊었는데 Valve Permit 알람도 섰다"
    res = rig.end_text()
    assert VP_NAME not in res and res.startswith("중단 (안전 정지 — "), res


def test_atm_input_by_pressure_in_process_also_trips(cfg):
    """대기압 입력이 실제 압력으로 켜져도 같다(챔버가 대기압으로 올라온 경우) — 고장 키가 아니라 입력 이미지로 판단한다."""
    rig = Rig(cfg, True)
    s = rig.s
    s.atm_input_torr = 0.0                              # 지금 압력에서 입력이 켜지게(이 인스턴스만)
    assert _scans_until_b8(rig) == 26
    assert A.bit(s.reg[A.D_INPUT0], A.IN0_ATM) and not s.faults["atm_sw"]


def test_valve_permit_alarm_is_only_judged_while_sequencer_runs(cfg):
    """대기 중에는 허가가 꺼져 있어도(펌핑 전 · 대기압) 알람이 없다 — SEQ_RUN 일 때만 본다."""
    fs = FakeSim(cfg)
    s = fs.sim
    fs.step(100)                                        # 2 s — 대기압 · 펌프 정지 · 허가 없음
    assert not A.bit(s.reg[A.D_INTERLOCK], A.ILK_VALVE_OK)
    assert s.reg[A.D_ALARM1] == 0 and s.reg[A.D_STATE] == A.STATE_IDLE
    # 공정 중에 섰던 타이머는 PLC 재시작(P00 첫 스캔)에서 초기화된다
    rig = Rig(cfg, True)
    _lose(rig, "pump_nofb")
    rig.run(0.3)
    assert rig.s.t_vp.since is not None
    rig.s.set_fault("plc_stop", True)
    rig.run(0.2)
    rig.s.set_fault("plc_stop", False)
    rig.run(0.1)
    assert rig.s.t_vp.since is None and not rig.alarm(1, VP)


def _feed(rig, runner, logs, secs, every=5):
    """PC 가 읽듯이(100 ms 마다 = 5 스캔) 상태 영역을 러너의 감시에 먹인다."""
    for i in range(int(round(secs / 0.02))):
        rig.run(0.02)
        if i % every == 0:
            snap = list(rig.s.reg[0:A.STATUS_COUNT])
            runner._watch_valve_permit(snap, snap[A.D_STATE] in P.RUNNING_STATES, lambda lv, m: logs.append((lv, m)))


@pytest.mark.parametrize("cause,word", [("pump_nofb", "Pump 운전 · IV-E 열림"), ("ive_stuck", "IV-E 열림"),
                                        ("atm_sw", "대기압 아님")])
def test_event_log_names_the_missing_permit_condition_once(cfg, cause, word):
    """알람1 b8 이 서는 순간 빠져 있던 허가 조건을 이벤트 로그에 한 줄 — 안전 정지 요구(결과)는 적지 않는다.
    (펌프 운전 입력이 빠지면 IV-E 출력도 닫혀 열림 리미트가 함께 빠진다.)"""
    rig = Rig(cfg, True)
    runner, logs = P.ProcessRunner(state), []
    _feed(rig, runner, logs, 0.4)                       # 정상 공정 — 로그 없음
    assert logs == []
    _lose(rig, cause)
    _feed(rig, runner, logs, 2.0)
    assert len(logs) == 1, logs
    lv, msg = logs[0]
    assert lv == "warn" and "Valve Permit 꺼짐" in msg and VP_NAME in msg
    assert msg.endswith("빠진 조건(필요): " + word), msg
    assert "안전 정지 요구 해제" not in msg
    _feed(rig, runner, logs, 1.0)                       # 알람이 서 있는 동안 되풀이하지 않는다
    assert len(logs) == 1


def test_event_log_uses_last_seen_condition_when_it_came_back(cfg):
    """알람이 선 스캔에 조건이 돌아왔으면(PC 가 읽을 때는 안전 정지 요구만 남는다) 공정 중 마지막으로 본 조건을 적는다."""
    rig = Rig(cfg, True)
    runner, logs = P.ProcessRunner(state), []
    _feed(rig, runner, logs, 0.2)
    _lose(rig, "atm_sw")
    for i in range(26):                                 # b8 이 서는 스캔까지 — PC 는 그 전에 꺼진 허가를 여러 번 본다
        rig.run(0.02)
        if i % 5 == 0 and i < 25:
            snap = list(rig.s.reg[0:A.STATUS_COUNT])
            runner._watch_valve_permit(snap, True, lambda lv, m: logs.append((lv, m)))
    assert rig.alarm(1, VP) and logs == []
    _restore(rig, "atm_sw")                             # 접점이 돌아왔다
    rig.run(0.04)
    snap = list(rig.s.reg[0:A.STATUS_COUNT])
    assert P.valve_permit_missing(snap) == ["안전 정지 요구 해제"]     # 지금 상태에는 원인이 없다(결과만 남았다)
    _feed(rig, runner, logs, 0.5)
    assert len(logs) == 1 and logs[0][1].endswith("빠진 조건(필요): 대기압 아님"), logs


def test_event_log_falls_back_to_current_state_when_pc_never_saw_the_permit_off(cfg):
    """PC 가 꺼진 허가를 한 번도 못 읽었으면(읽기 지연) 알람을 본 때의 상태에서 안전 정지 요구 항목을 빼고 적는다."""
    rig = Rig(cfg, True)
    runner, logs = P.ProcessRunner(state), []
    _feed(rig, runner, logs, 0.2)
    _lose(rig, "ive_stuck")
    rig.run(1.0)                                        # PC 는 이 1 s 를 못 봤다
    assert rig.alarm(1, VP)
    _feed(rig, runner, logs, 0.3)
    assert len(logs) == 1 and logs[0][1].endswith("빠진 조건(필요): IV-E 열림"), logs


def test_event_log_does_not_announce_an_alarm_already_latched_at_connect(cfg):
    """연결했을 때 이미 서 있던 알람1 b8 은 '새로 선 것' 이 아니다 — 알람 목록에는 뜨지만 이 줄은 남기지 않는다."""
    rig = Rig(cfg, True)
    _lose(rig, "pump_nofb")
    rig.run(1.0)
    assert rig.alarm(1, VP)
    runner, logs = P.ProcessRunner(state), []
    _feed(rig, runner, logs, 0.5)
    assert logs == [] and runner._vp_seen is True


# ===================== B. IV-E 동작 이상 중의 벤트 =====================
def _vv(s):
    return A.bit(s.reg[A.D_AUX_OUT], A.AUX_VV)


def test_vent_request_is_cleared_while_ive_alarm_is_latched(cfg):
    """IV-E 닫힘 리미트가 안 들어온 채 벤트 → VV 는 못 열리고(벤트 허가 없음) 5 s 뒤 IV-E 알람 → 벤트 요청이 지워진다.
    리미트가 돌아와 벤트 허가가 서도 VV 가 뒤늦게 열리지 않는다. 래치 중에 받은 벤트(결과 0)도 같은 스캔에 지워진다."""
    rig = Rig(cfg, False)
    s = rig.s
    s.set_fault("ive_stuck", True)                      # 리미트 입력이 따라오지 않는다
    assert rig.fs.cmd(A.CMD_VENT) == A.RESULT_OK
    rig.run(1.0)
    assert s.vent_req and not _vv(s) and not rig.ilk(A.ILK_VENT_OK)      # 요청은 남아 기다린다(닫힘 리미트 없음)
    assert not A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVE)
    rig.run(5.0)
    assert rig.alarm(1, A.ALM1_IVE)
    assert not s.vent_req and not _vv(s), "IV-E 알람 뒤에도 벤트 요청이 남았다"
    p0 = s.base_pressure
    s.set_fault("ive_stuck", False)                     # 닫힘 리미트가 돌아온다
    opened = False
    for _ in range(250):                                # 5 s
        rig.run(0.02)
        opened = opened or _vv(s) or s.vv_on
    assert rig.ilk(A.ILK_VENT_OK), "벤트 허가는 돌아와야 한다(리미트 정상)"
    assert not opened and not s.vent_req, "리미트가 돌아오자 VV 가 뒤늦게 열렸다"
    assert s.base_pressure < max(1.0, p0 * 50), f"챔버가 벤트됐다: {s.base_pressure:g} Torr"
    # 알람이 래치된 동안 벤트 명령 — PLC 는 받지만(결과 0) 같은 스캔에 지운다
    assert rig.alarm(1, A.ALM1_IVE)
    assert rig.fs.cmd(A.CMD_VENT) == A.RESULT_OK
    assert not s.vent_req and not s.vv_on and not _vv(s)
    rig.run(1.0)
    assert not _vv(s)
    # 원인을 없애고 알람 리셋 → 벤트가 정상으로 된다
    assert rig.fs.cmd(A.CMD_ALARM_RESET) == A.RESULT_OK
    rig.run(0.5)
    assert s.reg[A.D_ALARM1] == 0 and s.reg[A.D_STATE] == A.STATE_IDLE
    assert rig.fs.cmd(A.CMD_VENT) == A.RESULT_OK
    assert rig.run(3.0, cond=lambda: _vv(s)), "리셋 뒤 벤트가 되지 않는다"


def test_vent_timeout_and_atm_clear_still_work(cfg):
    """렁 B 는 앞뒤 렁을 바꾸지 않는다 — 대기압 입력 · 벤트 시간 초과로 지우는 것은 그대로."""
    rig = Rig(cfg, False)
    s = rig.s
    assert rig.fs.cmd(A.CMD_VENT) == A.RESULT_OK
    assert rig.run(5.0, cond=lambda: _vv(s))
    assert rig.run(120.0, 0.05, cond=lambda: not s.vent_req), "대기압에서 벤트 요청이 지워지지 않았다"
    assert A.bit(s.reg[A.D_INPUT0], A.IN0_ATM) and not rig.alarm(0, A.ALM0_VENT_TIMEOUT)


async def test_vent_is_refused_before_sending_while_ive_alarm(wired, notices):
    """PC — IV-E 동작 이상 알람이 래치된 동안 벤트는 보내기 전에 거절하고 이유를 알린다(보내면 IV-E 만 닫히고 벤트는 안 된다)."""
    lk, sim, _cfg = wired
    sim.alm1 |= 1 << A.ALM1_IVE                         # 래치(원인은 없다 — 리셋 전까지 남는다)
    assert await wait_until(lambda: A.bit(lk.status[A.D_ALARM1], A.ALM1_IVE), 3)
    ok, why = C.precheck(A.CMD_VENT)
    assert not ok and why == "IV-E Fault 알람 중에는 Vent 를 할 수 없습니다 — 원인을 없애고 Alarm Reset 뒤 다시"
    no = sim.reg[A.D_CMD_NO]
    await C.handle_command({"cmd": "vent"})
    assert sim.reg[A.D_CMD_NO] == no, "거절해야 할 벤트를 PLC 에 보냈다"
    assert any(lv == "warn" and why in m for lv, m in notices), notices
    # 화면의 벤트 표시는 PLC 출력(VV)에서만 온다 — 요청이 없으니 남는 것이 없다
    await asyncio.sleep(0.3)
    assert not A.bit(lk.status[A.D_AUX_OUT], A.AUX_VV) and not sim.vent_req
    live = state.live()
    assert not (live["aux"] >> A.AUX_VV) & 1 and not (live["manual"]["aux_out"] >> A.AUX_VV) & 1
    # 펌핑 시작 · 정지는 이 알람으로 막지 않는다(래더가 지우는 것은 벤트 요청뿐)
    assert C.precheck(A.CMD_PUMP_STOP)[0]
    # 원인이 없으면 Alarm Reset 뒤 벤트가 된다
    r, _ = await lk.send_command(A.CMD_ALARM_RESET)
    assert r == A.RESULT_OK
    assert await wait_until(lambda: lk.status[A.D_ALARM1] == 0 and lk.status[A.D_STATE] == A.STATE_IDLE, 3)
    await C.handle_command({"cmd": "vent"})
    assert sim.reg[A.D_CMD_NO] != no and sim.reg[A.D_ACK_RESULT] == A.RESULT_OK     # (대기압이라 요청은 바로 지워진다)


# ===================== C. Powder — O3 발생기 요청 (래더) =====================
def _o3_sim(cfg):
    fs = FakeSim(cfg)
    fs.sim.write(A.D_PRM_O3_MAX, [16000])
    return fs, fs.sim


def _cmd12(fs, aux, o3=1000):
    s = fs.sim
    s.write(A.D_MANUAL_VALVE, [0, 0, 0, 0])
    s.write(A.D_MANUAL_AUX, [aux])
    s.write(A.D_O3_SV, [o3])
    return fs.cmd(A.CMD_MANUAL_APPLY)


def _o3_permit(s):
    return A.bit(s.reg[A.D_INTERLOCK], A.ILK_O3_OK)


def _steps_until(fs, cond, secs):
    for _ in range(int(secs / 0.02)):
        fs.step()
        if cond():
            return True
    return False


@o3_only
def test_generator_request_without_o3_permit_is_erased_in_the_same_scan(cfg):
    """허가 없이 받은 발생기 요청(명령 12 결과 0)은 같은 스캔에 지워진다 — IV-B · 바이패스 펌프는 남아 허가를 만든다.
    허가가 선 뒤에도 발생기는 저절로 켜지지 않는다. 허가가 선 뒤에 보낸 발생기 요청만 남는다."""
    fs, s = _o3_sim(cfg)
    assert _cmd12(fs, LINE | GEN) == A.RESULT_OK
    assert s.man_aux == LINE and s.reg[A.D_APPLIED_AUX] == LINE, "허가 없는 발생기 요청이 남았다"
    assert s.reg[A.D_MANUAL_AUX] == LINE | GEN          # PC 요청 영역은 PLC 가 지우지 않는다(다음 명령 12 는 반영 영역 기준)
    gen = False
    for _ in range(400):                                # 8 s — IV-B 뒤 5 s 에 허가가 선다
        fs.step()
        gen = gen or s.o3_gen_on or A.bit(s.reg[A.D_AUX_OUT], A.AUX_O3_GEN) or bool(s.man_aux & GEN)
    assert _o3_permit(s) and not gen, "허가가 서자 옛 발생기 요청이 실행됐다"
    assert s.man_aux == LINE
    assert A.bit(s.reg[A.D_AUX_OUT], A.AUX_IVB) and A.bit(s.reg[A.D_AUX_OUT], A.AUX_BYPASS_PUMP)
    assert s.reg[A.D_ALARM0] == 0 and s.reg[A.D_ALARM1] == 0
    assert _cmd12(fs, LINE | GEN) == A.RESULT_OK        # 허가가 선 뒤
    fs.step(3)
    assert s.man_aux == LINE | GEN and s.o3_gen_on and A.bit(s.reg[A.D_AUX_OUT], A.AUX_O3_GEN)


@o3_only
@pytest.mark.parametrize("key", ["lid", "bp_nofb"])
def test_generator_does_not_restart_when_o3_permit_comes_back(cfg, key):
    """대기 중 리드를 열었다 닫아도(허가가 꺼졌다 돌아와도) 발생기가 저절로 켜지지 않는다 — 꺼지는 스캔에 요청도 지워진다."""
    fs, s = _o3_sim(cfg)
    assert _cmd12(fs, LINE) == A.RESULT_OK
    assert _steps_until(fs, lambda: _o3_permit(s), 8.0)
    assert _cmd12(fs, LINE | GEN) == A.RESULT_OK
    fs.step(3)
    assert s.o3_gen_on
    s.set_fault(key, True)                              # 리드 열림(기본 인터락) · 바이패스 펌프 운전 입력 꺼짐
    fs.step(1)
    assert not _o3_permit(s) and not s.o3_gen_on, "허가가 꺼진 스캔에 발생기가 꺼지지 않았다"
    assert s.man_aux == LINE and s.reg[A.D_APPLIED_AUX] == LINE          # 발생기(b9)만 지워진다
    fs.step(5)
    assert A.bit(s.reg[A.D_AUX_OUT], A.AUX_BYPASS_PUMP)
    assert s.reg[A.D_ALARM0] == 0 and s.reg[A.D_ALARM1] == 0 and s.reg[A.D_STATE] == A.STATE_IDLE
    s.set_fault(key, False)
    gen = False
    for _ in range(500):                                # 10 s — IV-B 가 다시 열리고 5 s 가 지나도
        fs.step()
        gen = gen or s.o3_gen_on or bool(s.man_aux & GEN)
    assert _o3_permit(s), "허가는 돌아와야 한다"
    assert not gen and s.man_aux == LINE, "허가가 돌아오자 발생기가 저절로 켜졌다"


@o3_only
def test_safe_stop_and_all_close_still_clear_the_whole_o3_line(cfg):
    """렁 C 는 앞의 지우기 렁을 바꾸지 않는다 — 안전 정지 요구 · 전체 닫기는 O3 라인 전부를 지운다."""
    fs, s = _o3_sim(cfg)
    fs.o3_ready()
    assert s.man_aux == LINE | GEN and s.o3_gen_on
    assert fs.cmd(A.CMD_ALL_CLOSE) == A.RESULT_OK
    fs.step(2)
    assert s.man_aux == 0 and not s.o3_gen_on
    fs.o3_ready()
    s.set_fault("emo", True)
    fs.step(2)
    assert s.man_aux == 0 and not s.o3_gen_on and s.reg[A.D_STATE] == A.STATE_SAFE_STOP


@o3_only
def test_old_p50_fault_also_keeps_the_generator_request(cfg):
    """시험 결함 old_p50(고치기 전 래더) — 그 래더에는 렁 C 가 없었다. 허가 없는 발생기 요청이 남아 허가가 서는 순간
    저절로 켜진다(PC 의 Pending 안전망 시험에 쓴다)."""
    fs, s = _o3_sim(cfg)
    s.set_fault("old_p50", True)
    assert _cmd12(fs, LINE | GEN) == A.RESULT_OK
    assert s.man_aux == LINE | GEN
    assert _steps_until(fs, lambda: s.o3_gen_on, 8.0)


@o3_only
def test_process_keeps_o3_line_and_permit_loss_still_alarms_b3(cfg):
    """공정 중 O3 라인 유지는 그대로 — 공정 시작이 발생기 요청을 지우지 않고, 공정 중 허가가 빠지면 알람1 b3 로 끝난다."""
    rig = Rig(cfg, True)
    s = rig.s
    assert s.man_aux == LINE | GEN and s.o3_gen_on
    rig.run(1.0)
    assert s.running and s.man_aux == LINE | GEN
    s.set_fault("bp_nofb", True)
    rig.run(0.5)
    assert rig.alarm(1, A.ALM1_O3_GEN) and not s.running and not rig.alarm(1, VP)


# ===================== C. O3 허가에 빠진 조건 · 알림 =====================
def _status(**kw):
    s = [0] * A.STATUS_COUNT
    for k, v in kw.items():
        s[getattr(A, k)] = v
    return s


def test_o3_permit_missing_rules():
    if not DEV.HAS_O3:
        assert P.o3_permit_missing(_status()) == []
        return
    assert P.o3_permit_missing(_status(D_INTERLOCK=1 << A.ILK_O3_OK)) == []
    bad = _status(D_INPUT1=(1 << A.IN1_BP_ALM) | (1 << A.IN1_O3_ALM) | (1 << A.IN1_O3_ROOM),
                  D_INTERLOCK=1 << A.ILK_SAFE_STOP_REQ)
    assert P.o3_permit_missing(bad, {A.D_PRM_O3_MAX: 0}) == [
        "기본 인터락", "Bypass Pump 운전", "Bypass Pump 알람 입력 꺼짐", "IV-B 열림", "O3 발생기 알람 입력 꺼짐",
        "실내 O3 감지 꺼짐", "PLC 의 O3 상한(PRM_O3_MAX) > 0", "안전 정지 요구 해제"]
    # 다 맞는데 허가가 없다 — IV-B 뒤 5 s 를 기다리는 중
    hold = _status(D_INTERLOCK=1 << A.ILK_BASIC, D_INPUT1=1 << A.IN1_BP_RUN, D_AUX_OUT=1 << A.AUX_IVB)
    assert P.o3_permit_missing(hold, {A.D_PRM_O3_MAX: 16000}) == [P.O3_HOLD_TEXT]
    assert "5 s" in P.O3_HOLD_TEXT


@o3_only
async def test_cleared_generator_notice_names_o3gen_and_missing_permit(cfg):
    """PLC 가 발생기 요청을 지우면 'PLC 가 수동 요청을 지웠습니다 — O3GEN (O3 허가 없음 — 필요: …)' — IV-B · 바이패스
    펌프가 함께 지워진 것(안전 정지 · 전체 닫기)이 아니면 O3 허가 사유를 붙인다."""
    from test_v082 import _link_with
    fresh = [0] * A.STATUS_COUNT
    fresh[A.D_INPUT1] = 1 << A.IN1_BP_RUN                    # 기본 인터락 없음(리드) · IV-B 출력 꺼짐
    lk, events = _link_with(cfg, fresh, seen_aux=LINE | GEN)
    lk.client.areas[A.APPLIED_BASE] = [0] * A.APPLIED_COUNT
    lk.client.areas[A.APPLIED_BASE][A.applied_off(A.D_APPLIED_AUX)] = LINE      # 발생기만 지워졌다
    await lk._read_display()
    msg = next(m for _l, m in events if "수동 요청을 지웠습니다" in m)
    assert "O3GEN" in msg and "IV-B" not in msg.split("(")[0], msg
    assert "O3 허가 없음 — 필요: 기본 인터락 · IV-B 열림" in msg, msg
    assert "저절로 다시 열리거나 켜지지 않습니다" in msg and lk.pending_toasts == [("warn", msg)]
    # 바이패스 라인만 지워졌으면(발생기 비트 아님) O3 허가 사유를 붙이지 않는다
    assert "O3 허가 없음" not in lk.clear_reason(False, 1 << A.AUX_IVB)
    assert "O3 허가 없음" in lk.clear_reason(False, GEN) and "O3 허가 없음" in lk.clear_reason(False, True)


# ===================== C. PC — O3 라인 켜기 두 단계 =====================
async def _o3_wired(wired, wait_s=None, off_delay=None):
    lk, sim, cfg = wired
    sim.write(A.D_PRM_O3_MAX, [16000])
    assert await wait_until(lambda: lk.prm_readback.get(A.D_PRM_O3_MAX), 5), "PRM_O3_MAX 되읽기가 없다"
    if wait_s is not None:
        cfg["process"]["o3_permit_wait_s"] = wait_s
    if off_delay is not None:
        cfg["process"]["o3_off_delay_s"] = off_delay
    return lk, sim, cfg


def _watch_gen(sim):
    """발생기 요청 · 출력이 한 번이라도 섰는지 지켜보는 태스크 — (태스크, 결과 묶음)."""
    seen = {"gen": False, "no_permit": False}

    async def loop():
        while True:
            if sim.man_aux & GEN or sim.o3_gen_on or sim.reg[A.D_MANUAL_AUX] & GEN:
                seen["gen"] = True
                if not _o3_permit(sim):
                    seen["no_permit"] = True
            await asyncio.sleep(0.01)
    return asyncio.create_task(loop()), seen


def _has(notices, *words):
    return any(all(w in m for w in words) for _l, m in notices)


@o3_only
async def test_o3_on_sends_line_first_then_generator_after_permit(wired, notices):
    """① 바이패스 펌프 + IV-B 와 설정값(발생기 비트 없이) ② 서버가 O3 허가를 기다림 ③ 허가가 서면 발생기."""
    lk, sim, _cfg = await _o3_wired(wired)
    task, seen = _watch_gen(sim)
    try:
        await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
        # ① 직후 — PC 요청 영역(D01008)에도 발생기 비트가 없다
        assert sim.reg[A.D_MANUAL_AUX] == LINE and sim.man_aux == LINE
        assert sim.reg[A.D_O3_SV] == state.conv.o3.to_raw(50)
        assert C.o3_on_waiting() and state.o3_on_until > 0
        ms = state.manual_state()
        assert 1 <= ms["o3_on_wait_s"] <= 15 and ms["o3_off_left_s"] == 0
        assert _has(notices, "O3 라인 켜기", "O3 Permit 을 기다립니다(최대 15 s)")
        # 기다리는 동안 다시 켜기 — 보내지 않는다
        no = sim.reg[A.D_CMD_NO]
        await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
        assert sim.reg[A.D_CMD_NO] == no and _has(notices, "이미 O3 Permit 을 기다리는 중")
        # ② → ③ IV-B 뒤 5 s 에 허가 → 발생기
        assert await wait_until(lambda: sim.o3_gen_on, 10), "허가 뒤 발생기가 켜지지 않았다"
        assert seen["gen"] and not seen["no_permit"], "허가가 서기 전에 발생기 요청을 보냈다"
        assert sim.reg[A.D_MANUAL_AUX] == LINE | GEN and sim.man_aux == LINE | GEN
        assert await wait_until(lambda: not C.o3_on_waiting(), 2)
        assert state.o3_on_until == 0 and state.manual_state()["o3_on_wait_s"] == 0
        assert _has(notices, "O3 Permit 확인 뒤 발생기를 켰습니다")
        # 이미 켜진 라인에 ON — 같은 요청은 보내지 않고 기다리지도 않는다
        no = sim.reg[A.D_CMD_NO]
        await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
        assert sim.reg[A.D_CMD_NO] == no and not C.o3_on_waiting() and sim.o3_gen_on
    finally:
        task.cancel()


@o3_only
async def test_o3_on_goes_straight_to_generator_when_permit_already_stands(wired, notices):
    """끄기 지연 중(바이패스 라인이 켜진 채 허가가 서 있음)에 다시 켜면 기다리지 않고 바로 발생기를 보낸다."""
    lk, sim, _cfg = await _o3_wired(wired, off_delay=30)
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert await wait_until(lambda: sim.o3_gen_on, 10)
    await C.handle_command({"cmd": "manual_o3", "action": "off"})
    assert await wait_until(lambda: not sim.o3_gen_on, 2) and state.o3_off_at > 0
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert state.o3_off_at == 0, "다시 켰는데 바이패스 라인 닫기 예약이 남았다"
    assert not C.o3_on_waiting()
    assert await wait_until(lambda: sim.o3_gen_on, 2), "허가가 서 있는데 발생기를 바로 보내지 않았다"


@o3_only
@pytest.mark.parametrize("fault,words", [(None, ["IV-B 열린 뒤 5 s"]), ("bp_nofb", ["Bypass Pump 운전", "IV-B 열림"]),
                                         ("o3_room", ["실내 O3 감지 꺼짐"])])
async def test_o3_on_timeout_sends_no_generator_and_closes_the_line(wired, notices, fault, words):
    """제한 시간 안에 허가가 안 서면 발생기는 보내지 않고, 빠진 조건을 알린 뒤 바이패스 라인을 지연 없이 닫는다."""
    lk, sim, _cfg = await _o3_wired(wired, wait_s=1.0 if fault is None else 1.5, off_delay=30)
    if fault == "o3_room":
        pytest.skip("실내 O3 는 중대 알람(안전 정지)이라 취소 경로로 끝난다 — 아래 취소 시험에서 본다")
    if fault:
        sim.set_fault(fault, True)
    task, seen = _watch_gen(sim)
    try:
        await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
        assert sim.man_aux == LINE and C.o3_on_waiting()
        assert await wait_until(lambda: _has(notices, "O3 라인 켜기 실패"), 4), notices
        msg = next(m for _l, m in notices if "O3 라인 켜기 실패" in m)
        for w in words:
            assert w in msg, (w, msg)
        assert "안에 서지 않았습니다" in msg and "발생기를 보내지 않았고 바이패스 라인을 닫습니다" in msg
        assert await wait_until(lambda: sim.man_aux == 0, 2), "바이패스 라인이 지연 없이 닫히지 않았다"
        assert state.o3_off_at == 0 and state.manual_state()["o3_on_wait_s"] == 0
        assert await wait_until(lambda: not C.o3_on_waiting(), 2)
        await asyncio.sleep(0.5)
        assert not seen["gen"], "허가가 안 섰는데 발생기 요청을 보냈다"
        assert not sim.reg[A.D_MANUAL_AUX] & GEN
    finally:
        task.cancel()


@o3_only
async def test_o3_on_timeout_waits_out_the_purge_when_generator_ran_recently(wired, notices):
    """발생기 출력을 본 지 끄기 지연이 안 지났으면(끄기 지연 중에 다시 켠 뒤 시간 초과) 남은 시간만큼 기다렸다가 닫는다."""
    lk, sim, _cfg = await _o3_wired(wired, wait_s=1.0, off_delay=3.0)
    state.o3_gen_seen = time.monotonic()                # 방금까지 발생기가 돌았다
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert await wait_until(lambda: _has(notices, "O3 라인 켜기 실패"), 4), notices
    assert state.o3_off_at > 0 and sim.man_aux == LINE, "남은 O3 를 뺄 시간 없이 바로 닫았다"
    assert 1 <= state.manual_state()["o3_off_left_s"] <= 3
    assert await wait_until(lambda: sim.man_aux == 0, 4), "남은 지연 뒤 바이패스 라인이 닫히지 않았다"


@o3_only
@pytest.mark.parametrize("how", ["off", "all_close", "safe_stop", "process_start", "disconnect"])
async def test_o3_on_wait_is_cancelled_and_no_generator_is_sent(wired, notices, how):
    """기다리는 동안 끄기 · 전체 닫기 · 안전 정지 · 공정 시작 · 연결 끊김이 오면 취소 — 그 뒤 허가가 서도 발생기를 보내지 않는다."""
    lk, sim, _cfg = await _o3_wired(wired, off_delay=30)
    task, seen = _watch_gen(sim)
    try:
        await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
        assert C.o3_on_waiting() and sim.man_aux == LINE
        await asyncio.sleep(0.5)
        if how == "off":
            await C.handle_command({"cmd": "manual_o3", "action": "off"})
            assert _has(notices, "O3 라인 켜기 취소", "운전자 OFF")
            assert state.o3_off_at > 0 and sim.man_aux == LINE           # 끄기 순서 그대로 — 지연 뒤 바이패스 닫기
        elif how == "all_close":
            await C.handle_command({"cmd": "all_close"})
            assert _has(notices, "O3 라인 켜기 취소", "전체 닫기")
            assert sim.man_aux == 0
        elif how == "safe_stop":
            sim.set_fault("emo", True)
            assert await wait_until(lambda: _has(notices, "O3 라인 켜기 취소", "발생기를 보내지 않았습니다"), 3), notices
        elif how == "process_start":
            state.runner.phase = P.UPLOADING            # 시작 흐름이 돈다(올리기 · 베이스 압력 대기 · 명령 1)
            assert await wait_until(lambda: _has(notices, "O3 라인 켜기 취소", "공정 시작"), 3), notices
            state.runner.phase = P.IDLE
        else:
            await lk.stop()
            lk._drop("시험: PLC 연결 끊김")             # 링크가 떨어졌다(상태 영역도 비운다)
            assert await wait_until(lambda: _has(notices, "O3 라인 켜기 취소", "PLC 연결이 끊겼습니다"), 3), notices
        assert await wait_until(lambda: not C.o3_on_waiting(), 2)
        assert state.o3_on_until == 0 and state.manual_state()["o3_on_wait_s"] == 0
        if how == "safe_stop":
            sim.set_fault("emo", False)
        # 허가가 설 시간(IV-B 뒤 5 s)이 지나도 발생기 요청은 한 번도 없다
        await asyncio.sleep(6.0)
        assert not seen["gen"], f"취소({how}) 뒤에 발생기 요청을 보냈다"
        if how in ("off", "process_start"):
            assert _o3_permit(sim), "이 경우는 허가가 서야 시험이 뜻이 있다"
    finally:
        task.cancel()
        state.runner.phase = P.IDLE


@o3_only
async def test_start_flow_cancels_the_o3_permit_wait(wired, notices):
    """공정 시작 절차가 시작되면 O3 허가 대기를 취소한다(시작 절차 중에 발생기 명령이 끼어들지 않게)."""
    lk, sim, _cfg = await _o3_wired(wired)
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert C.o3_on_waiting()
    seq = state.o3_on_seq
    assert C.cancel_o3_on() is True
    assert state.o3_on_seq == seq + 1 and not C.o3_on_waiting() and state.o3_on_until == 0
    assert C.cancel_o3_on() is False                    # 기다리던 것이 없으면 False
    import inspect
    src = inspect.getsource(P.ProcessRunner._start_flow)
    assert "cancel_o3_on" in src and src.index("cancel_o3_on") < src.index("upload_recipe")


@o3_only
async def test_generator_send_is_dropped_when_cancelled_while_waiting_for_the_command_lock(wired, notices):
    """끄기가 발생기 보내기와 겹쳐도(발생기 보내기가 명령 잠금을 기다리는 사이 끄기가 왔다) 발생기를 보내지 않는다."""
    lk, sim, _cfg = await _o3_wired(wired)
    sim.man_aux = LINE
    assert await wait_until(lambda: _o3_permit(sim) and A.bit(lk.status[A.D_INTERLOCK], A.ILK_O3_OK)
                            and lk.applied_aux == LINE, 10)
    token = C._o3_on_begin()
    C.cancel_o3_on()                                    # 끄기 · 전체 닫기 · 공정 시작이 번호를 바꾼다
    no = sim.reg[A.D_CMD_NO]
    assert await C._o3_gen_on(None, token) is None
    assert sim.reg[A.D_CMD_NO] == no and not sim.man_aux & GEN
    assert _has(notices, "O3 라인 켜기가 취소되었습니다")
    assert await C._o3_gen_on(None, C._o3_on_begin()) == A.RESULT_OK       # 취소되지 않은 켜기는 보낸다
    assert await wait_until(lambda: sim.o3_gen_on, 2)


@o3_only
async def test_plc_cleared_generator_is_reported_and_not_revived_by_next_command(wired, notices):
    """켜진 뒤 허가가 꺼져 PLC 가 발생기 요청을 지우면 알리고, PC 요청 영역(D01008)에 남은 발생기 비트를 다음 명령 12 가
    되살리지 않는다(명령 12 는 PLC 반영 영역 기준으로 다시 만든다)."""
    lk, sim, _cfg = await _o3_wired(wired)
    await C.handle_command({"cmd": "manual_o3", "action": "on", "value": 50})
    assert await wait_until(lambda: sim.o3_gen_on, 10)
    assert await wait_until(lambda: lk.applied_aux == LINE | GEN, 2)
    await asyncio.sleep(0.3)
    sim.set_fault("lid", True)                          # 대기 중 리드 — 기본 인터락 → O3 허가 꺼짐(알람 없음)
    assert await wait_until(lambda: any("수동 요청을 지웠습니다" in m for _l, m in lk.pending_toasts), 3), lk.pending_toasts
    msg = next(m for _l, m in lk.pending_toasts if "수동 요청을 지웠습니다" in m)
    assert "O3GEN" in msg and "O3 허가 없음 — 필요: 기본 인터락" in msg, msg
    assert sim.man_aux == LINE and sim.reg[A.D_MANUAL_AUX] == LINE | GEN     # PC 영역에는 옛 요청이 남아 있다
    sim.set_fault("lid", False)
    assert await wait_until(lambda: A.bit(lk.status[A.D_INTERLOCK], A.ILK_O3_OK), 3)
    no = sim.reg[A.D_CMD_NO]
    await C.handle_command({"cmd": "manual_o3", "action": "set", "value": 60})      # 다음 명령 12
    assert sim.reg[A.D_CMD_NO] != no
    assert sim.reg[A.D_MANUAL_AUX] == LINE, "다음 명령 12 가 지워진 발생기 요청을 되살렸다"
    await asyncio.sleep(0.5)
    assert sim.man_aux == LINE and not sim.o3_gen_on


@o3_only
async def test_generator_pending_safety_net_when_plc_does_not_erase(wired):
    """반영되지 않는 요청의 1 s 안전망(Pending)이 발생기에도 — PLC 가 지우지 않은 발생기 요청(고치기 전 래더)은 Program
    Notices 에 뜬다. 래더가 지우면(렁 C) Pending 도 알림도 없다."""
    lk, sim, _cfg = await _o3_wired(wired)
    sim.set_fault("old_p50", True)
    sim.man_aux = GEN                                   # 바이패스 라인 없이 발생기 요청만 — 허가가 설 수 없다
    assert await wait_until(lambda: lk.applied_aux & GEN, 2)
    assert await wait_until(lambda: "O3GEN" in state.manual_state()["aux_pending"], 3), state.manual_state()
    text = " / ".join(n[1] for n in state.pc_notices(True))
    assert "PLC 가 지우지 않은 수동 요청: O3GEN" in text and "(O3 는 Line OFF)" in text, text
    sim.set_fault("old_p50", False)                     # 고친 래더 — 다음 스캔에 지운다
    assert await wait_until(lambda: not lk.applied_aux & GEN, 2)
    assert await wait_until(lambda: "O3GEN" not in state.manual_state()["aux_pending"], 2)
    assert "지우지 않은 수동 요청" not in " / ".join(n[1] for n in state.pc_notices(True))
    # 바이패스 펌프 운전 입력을 기다리는 IV-B 는 알림에 넣지 않는다(허가를 만드는 쪽 — 기다리는 것이 정상)
    sim.set_fault("bp_nofb", True)
    sim.man_aux = LINE
    assert await wait_until(lambda: "IV-B" in state.manual_state()["aux_pending"], 4)
    assert "지우지 않은 수동 요청" not in " / ".join(n[1] for n in state.pc_notices(True))


# ===================== 설정 =====================
def test_o3_permit_wait_setting():
    assert ("o3_permit_wait_s" in CF.DEFAULTS["process"]) == DEV.HAS_O3
    assert ("process.o3_permit_wait_s" in settings.FIELDS) == DEV.HAS_O3
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    assert ("o3_permit_wait_s" in raw["process"]) == DEV.HAS_O3
    if not DEV.HAS_O3:
        return
    assert CF.DEFAULTS["process"]["o3_permit_wait_s"] == 15 and raw["process"]["o3_permit_wait_s"] == 15
    cfg, problems, _src = CF.load("")
    assert not [m for lv, m in problems if "o3_permit_wait_s" in m]
    for bad in (5, 0, -1, 601, "x", None, True):
        c = json.loads(json.dumps(cfg))
        c["process"]["o3_permit_wait_s"] = bad
        assert any(lv == "err" and "o3_permit_wait_s" in m for lv, m in CF.validate(c)), bad
    for good in (6, 15, 600):
        c = json.loads(json.dumps(cfg))
        c["process"]["o3_permit_wait_s"] = good
        assert not any("o3_permit_wait_s" in m for _lv, m in CF.validate(c)), good
    state.cfg = {"process": {"o3_permit_wait_s": 20}}
    assert C.o3_permit_wait_s() == 20
    for junk in (None, "x", 0, -3, float("nan")):
        state.cfg = {"process": {"o3_permit_wait_s": junk}}
        assert C.o3_permit_wait_s() == 15


# ===================== 화면 =====================
@pytest.fixture
def served85(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    from powderald.server import create_app, uvicorn_config
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": True, "sim_port": free_port(), "sim_speed": 50})
    if DEV.HAS_O3:
        raw.setdefault("o3", {})["full"] = 200.0
        raw["params"]["o3_max"] = 200.0
        raw["process"]["o3_permit_wait_s"] = 60         # 화면을 보는 동안 기다림이 끝나지 않게
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


async def test_browser_vent_refusal_and_o3_permit_wait_display(served85):
    """화면 — IV-E 알람 중 벤트는 거절 알림만 뜨고 벤트 표시(VV)가 남지 않는다. Powder: O3 라인 켜기가 허가를 기다리는
    동안 'Permit Wait N s' 를 보이고(끄기 지연 표시와 같은 자리들), 끄면 사라진다."""
    port = served85
    pw_mod = pytest.importorskip("playwright.async_api")
    pw = await pw_mod.async_playwright().start()
    try:
        br = await pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        await pw.stop()
        pytest.skip(f"브라우저를 띄울 수 없음: {type(e).__name__}")
    try:
        pg = await br.new_page(viewport={"width": 1600, "height": 900})
        errors = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(f"http://127.0.0.1:{port}/")
        await pg.add_style_tag(content="#alarmModal { display: none !important; }")
        await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
        sim = state.sim
        # ---- 벤트: IV-E 알람 래치 중 ----
        sim.alm1 |= 1 << A.ALM1_IVE
        await pg.wait_for_function(
            "() => ((core.state.live || {}).alarms || []).some(a => a.name === 'IV-E Feedback Fault')", timeout=5000)
        no = sim.reg[A.D_CMD_NO]
        assert await pg.evaluate("() => core.sendBusy('cmd:vent', 'vent') > 0")
        await pg.wait_for_function(
            "() => document.getElementById('toasts').textContent.includes('IV-E Fault 알람 중에는 Vent 를 할 수 없습니다')",
            timeout=5000)
        await pg.wait_for_function("() => !core.busy.has('cmd:vent')", timeout=5000)
        assert sim.reg[A.D_CMD_NO] == no
        vent = await pg.evaluate("""() => {
          const s = core.state, t = s.live, m = UI.stripModel(s, t);
          return { already: UI.alreadyWhy('vent', s.structure, t), vv: (m.exh[2] || {}).text,
                   btn: (document.querySelector('[data-cmd="vent"]') || {}).disabled };
        }""")
        assert vent == {"already": "", "vv": "Closed", "btn": False}, vent
        assert await pg.evaluate("() => core.sendBusy('cmd:alarm_reset', 'alarm_reset') > 0")
        await pg.wait_for_function("() => ((core.state.live || {}).alarms || []).length === 0", timeout=5000)
        # ---- Powder: O3 허가 대기 표시 ----
        if DEV.HAS_O3:
            sim.set_fault("bp_nofb", True)              # 바이패스 펌프 운전 입력이 없다 — 허가가 서지 않아 기다림이 이어진다
            assert await pg.evaluate("() => Ops.send(Ops.o3('on', 50)) > 0")
            await pg.wait_for_function(
                "() => /^Permit Wait \\d+ s · /.test(document.querySelector('[data-mx=\"o3line\"]').textContent)",
                timeout=5000)
            assert await pg.evaluate("() => core.state.live.manual.o3_on_wait_s") >= 1
            await pg.evaluate("""() => {            // '수동 조작…' 창(탭마다 단추가 있다 — 보이는 것을 누른다)
              const all = [...document.querySelectorAll('[data-bind="mnOpen"]')];
              (all.find(b => b.offsetParent !== null) || all[0]).click();
            }""")
            await pg.wait_for_function(
                "() => (document.querySelector('[data-bind=\"mnO3Note\"]') || {textContent: ''}).textContent"
                ".includes('O3 Permit 을 기다리는 중')", timeout=5000)
            fp = await pg.evaluate("""() => {
              const m = Faceplate.model('o3', 'O3', core.state);
              const row = m.rows.find(r => /^Permit Wait \\d+ s$/.test(r[1]));
              const on = m.buttons.find(b => b.a === 'on'), off = m.buttons.find(b => b.a === 'off');
              return { row: !!row, on: on.on, off: off.on, why: m.why, note: m.note };
            }""")
            assert fp["row"] and fp["on"] is False and fp["off"] is True, fp
            assert "O3 Permit 을 기다리는 중" in fp["why"] and "O3 Permit" in fp["note"], fp
            assert not (sim.man_aux & GEN) and sim.man_aux == LINE
            assert await pg.evaluate("() => Ops.send(Ops.o3('off')) > 0")
            await pg.wait_for_function(
                "() => !document.querySelector('[data-mx=\"o3line\"]').textContent.includes('Permit Wait')", timeout=5000)
            await pg.wait_for_function(
                "() => document.querySelector('[data-bind=\"mnO3Note\"]').textContent.includes('발생기를 껐습니다')",
                timeout=5000)
            assert await pg.evaluate("() => core.state.live.manual.o3_on_wait_s") == 0
            sim.set_fault("bp_nofb", False)
        assert not errors, errors[:5]
    finally:
        await br.close()
        await pw.stop()
