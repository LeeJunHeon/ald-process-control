"""v0.8.2 — Pending 근본 대책: 래더 P50 이 Valve Permit(PEALD 는 RF 허가도)이 꺼지면 수동 요청을 같은 스캔에 지운다.

가짜 PLC(시뮬레이터)는 FakeSim(가짜 시계, 한 스캔씩)으로 래더 규칙을 보고, PC 는 지운 사유 · 토스트 · Pending 1 s ·
RF 켜기 허가 확인 · 화면 글을 본다.
★ test_v080 · test_v0411 · test_v047 을 import 하지 않는다(그쪽 모듈 머리의 importorskip 때문에 이 파일 전체가 조용히
  건너뛰어질 수 있다). node · 가짜 Modbus 클라이언트 도우미는 이 파일에 따로 둔다.
"""
import os
import json
import shutil
import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from powderald import addresses as A
from powderald import commands as C
from powderald import device as DEV
from powderald import plclink
from powderald import process as P
from powderald import version
from powderald.state import state

from conftest import FakeSim, wait_until
from test_process import wired, notices, at_vacuum  # noqa: F401 (픽스처)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "frontend", "js")


def _src(*parts):
    return open(os.path.join(JS, *parts), encoding="utf-8").read()


def _j(*parts):
    return json.dumps(os.path.join(JS, *parts))


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 가 없다")
    r = subprocess.run([node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


# ===================== 가짜 PLC — 래더 P50 =====================
def _cmd12(fs, valve=0, aux=0, rf=None):
    """PC 처럼 요청 영역(D01004~ · D01008 · RF 설정)을 쓰고 명령 12 — 한 스캔 뒤 결과."""
    s = fs.sim
    lo, hi = A.split_dword(valve)
    s.write(A.D_MANUAL_VALVE, [lo, hi, 0, 0])
    s.write(A.D_MANUAL_AUX, [aux])
    if rf is not None:
        s.write(A.D_RF_SV, [rf])
    return fs.cmd(A.CMD_MANUAL_APPLY)


def _until(fs, cond, secs, dt=0.02, each=None):
    """가짜 시계로 cond 가 될 때까지 한 스캔씩(최대 secs 초). each(s) 는 스캔마다."""
    for _ in range(int(secs / dt)):
        fs.step(1, dt)
        if each:
            each(fs.sim)
        if cond(fs.sim):
            return True
    return False


def _permit(s):
    return A.bit(s.reg[A.D_INTERLOCK], A.ILK_VALVE_OK)


def _pump_permit(fs):
    assert fs.cmd(A.CMD_PUMP_START) == A.RESULT_OK
    assert _until(fs, _permit, 60, 0.05), "Valve Permit 이 나오지 않았다"


def _pv(tag):
    return 1 << DEV.valve_bit(tag)


def _applied_valve(s):
    return A.dword(s.reg[A.D_APPLIED_VALVE], s.reg[A.D_APPLIED_VALVE + 1])


def test_p50_clears_valve_request_in_the_scan_permit_drops(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    pv1 = _pv("PV-1")
    _pump_permit(fs)
    assert _cmd12(fs, pv1) == A.RESULT_OK
    assert _until(fs, lambda x: x.reg[A.D_VALVE_OUT] & pv1, 1)
    assert fs.cmd(A.CMD_PUMP_STOP) == A.RESULT_OK
    for _ in range(1000):                          # 허가가 꺼지는 바로 그 스캔을 잡는다
        if not _permit(s):
            break
        fs.step(1, 0.02)
    assert not _permit(s), "허가가 꺼지지 않았다"
    assert s.man_valve == 0 and _applied_valve(s) == 0 and s.reg[A.D_VALVE_OUT] == 0
    _pump_permit(fs)
    fs.step(100, 0.02)                             # 2 s
    assert s.reg[A.D_VALVE_OUT] == 0 and s.man_valve == 0 and _applied_valve(s) == 0


def test_p50_vent_then_pump_start_midway_does_not_reopen(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    pv1 = _pv("PV-1")
    _pump_permit(fs)
    assert _cmd12(fs, pv1) == A.RESULT_OK
    assert _until(fs, lambda x: x.reg[A.D_VALVE_OUT] & pv1, 1)
    assert fs.cmd(A.CMD_VENT) == A.RESULT_OK
    assert _until(fs, lambda x: not _permit(x), 10)
    assert s.man_valve == 0 and _applied_valve(s) == 0
    assert not A.bit(s.reg[A.D_INPUT0], A.IN0_ATM), "아직 대기압이 아닐 때 다시 펌핑하는 경우다"
    _pump_permit(fs)
    fs.step(100, 0.02)
    assert not s.reg[A.D_VALVE_OUT] & pv1 and s.man_valve == 0


def test_p50_command12_without_permit_cleared_same_scan(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.base_pressure = 1.0                          # 대기압 아님 · 펌프 꺼짐
    fs.step(5)
    assert not A.bit(s.reg[A.D_INPUT0], A.IN0_ATM) and not _permit(s)
    pv1 = _pv("PV-1")
    assert _cmd12(fs, pv1) == A.RESULT_OK          # 받았다(결과 0)
    assert s.man_valve == 0 and _applied_valve(s) == 0 and s.reg[A.D_VALVE_OUT] == 0   # 그 스캔에 지움
    assert s.reg[A.D_MANUAL_VALVE] == pv1, "PC 요청 영역은 PC 가 쓴 그대로여야 한다"


def test_old_p50_fault_keeps_request_like_old_ladder(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.set_fault("old_p50", True)
    pv1 = _pv("PV-1")
    _pump_permit(fs)
    assert _cmd12(fs, pv1) == A.RESULT_OK
    assert _until(fs, lambda x: x.reg[A.D_VALVE_OUT] & pv1, 1)
    assert fs.cmd(A.CMD_PUMP_STOP) == A.RESULT_OK
    assert _until(fs, lambda x: not _permit(x), 10)
    fs.step(10)
    assert s.man_valve == pv1 and s.reg[A.D_VALVE_OUT] == 0        # 요청은 남고 출력만 0(고치기 전)
    _pump_permit(fs)
    assert _until(fs, lambda x: x.reg[A.D_VALVE_OUT] & pv1, 1), "고치기 전 래더는 허가가 돌아오면 다시 연다"


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
def test_p50_clears_rf_request_when_rf_permit_lost(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    raw = s.conv.cvg.to_raw
    s.write(A.D_PRM_RF_MAX, [8000])
    s.write(A.D_PRM_RF_REF_MAX, [30000])
    s.write(A.D_PRM_RF_REF_MS, [2000])
    s.write(A.D_PRM_RF_MAX_PRESS, [raw(1.0)])
    s.write(A.D_PRM_BASE_PRESS, [raw(0.05)])
    rf = 1 << A.AUX_RF

    def rf_ok(x):
        return A.bit(x.reg[A.D_INTERLOCK], A.ILK_RF_OK)

    def rf_out(x):
        return A.bit(x.reg[A.D_AUX_OUT], A.AUX_RF)

    def hold(p):
        def f(x):
            x.base_pressure = p
        return f

    _pump_permit(fs)
    assert _until(fs, rf_ok, 60, 0.05, each=hold(0.5)), "RF 허가가 나오지 않았다"
    assert _cmd12(fs, 0, rf, rf=1000) == A.RESULT_OK
    assert _until(fs, rf_out, 1, each=hold(0.5))
    # 압력이 범위를 벗어나 RF 허가가 꺼진다 → 요청 지움 · RF 꺼짐
    assert _until(fs, lambda x: not rf_ok(x), 5, each=hold(1e-3))
    assert not s.man_aux & rf and not s.reg[A.D_APPLIED_AUX] & rf and not rf_out(s)
    # 허가가 돌아와도 저절로 켜지지 않는다
    assert _until(fs, rf_ok, 5, each=hold(0.5))
    _until(fs, lambda x: False, 2, each=hold(0.5))
    assert not rf_out(s) and not s.man_aux & rf
    # 다시 켜고 Vent(대기압까지) → 요청 지움 → 다시 펌핑해 RF 허가 범위를 지나도 안 켜진다
    assert _cmd12(fs, 0, rf, rf=1000) == A.RESULT_OK
    assert _until(fs, rf_out, 1, each=hold(0.5))
    assert fs.cmd(A.CMD_VENT) == A.RESULT_OK
    assert _until(fs, lambda x: A.bit(x.reg[A.D_INPUT0], A.IN0_ATM), 120, 0.05)
    assert not s.man_aux & rf and not rf_out(s)
    assert fs.cmd(A.CMD_PUMP_START) == A.RESULT_OK
    seen = {"in_range": False, "on": False}

    def watch(x):
        seen["in_range"] = seen["in_range"] or rf_ok(x)
        seen["on"] = seen["on"] or rf_out(x)

    assert _until(fs, lambda x: x.pressure <= 0.02, 300, 0.05, each=watch)
    assert seen["in_range"], "펌프다운이 RF 허가 범위를 지나지 않았다(시험 전제)"
    assert not seen["on"], "지운 RF 요청이 펌프다운 중 저절로 켜졌다"


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_powder_o3_line_untouched_and_pv_r_cleared(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    line = (1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB) | (1 << A.AUX_O3_GEN)
    assert not _permit(s) and s.o3_gen_on and s.man_aux & line == line      # 허가 없이도 O3 라인은 그대로
    pvr, pvb = _pv("PV-R"), _pv("PV-B")
    _pump_permit(fs)
    # PC 처럼 지금 반영한 보조 요청(= O3 라인)도 함께 쓴다 — 명령 12 는 요청 영역 전부를 복사한다
    assert _cmd12(fs, pvr, s.reg[A.D_APPLIED_AUX]) == A.RESULT_OK
    assert _until(fs, lambda x: x.reg[A.D_VALVE_OUT] & pvr, 1)
    assert not s.reg[A.D_VALVE_OUT] & pvb and s.o3_gen_on
    assert fs.cmd(A.CMD_PUMP_STOP) == A.RESULT_OK
    assert _until(fs, lambda x: not _permit(x), 10)
    fs.step(2)
    assert s.man_valve == 0 and not s.reg[A.D_VALVE_OUT] & pvr and s.reg[A.D_VALVE_OUT] & pvb
    assert s.o3_gen_on and s.man_aux & line == line
    _pump_permit(fs)
    fs.step(100, 0.02)
    assert not s.reg[A.D_VALVE_OUT] & pvr and s.reg[A.D_VALVE_OUT] & pvb


# ===================== PC — 지운 사유 · 토스트 =====================
class _FakeClient:
    """읽을 때마다 정해 둔 값을 돌려주는 가짜 Modbus 클라이언트."""

    def __init__(self, areas):
        self.areas = areas
        self.write_guard = None

    async def read_holding(self, addr, count):
        return list(self.areas[addr])[:count]


def _link_with(cfg, fresh, seen_valve=0, seen_aux=0):
    from powderald.convert import Converters
    events = []
    lk = plclink.PlcLink(cfg, Converters(cfg), on_event=lambda lvl, msg: events.append((lvl, msg)))
    fresh[A.D_DEVICE_ID] = DEV.DEVICE_ID
    lk.status = list(fresh)
    lk.client = _FakeClient({A.APPLIED_BASE: [0] * A.APPLIED_COUNT, A.STATUS_BASE: fresh})
    lk.id_state = "ok"
    lk.write_ok = True
    lk._seen = (seen_valve, seen_aux, lk._cmd_seq)
    return lk, events


async def test_clear_reason_valve_permit_and_toast(cfg):
    fresh = [0] * A.STATUS_COUNT
    fresh[A.D_INTERLOCK] = 1 << A.ILK_BASIC                 # 펌프 · IV-E 없음
    lk, events = _link_with(cfg, fresh, seen_valve=_pv("PV-2"))
    await lk._read_display()
    msg = next(m for _l, m in events if "수동 요청을 지웠습니다" in m)
    assert "PV-2" in msg and "Valve Permit 없음 — 필요: Pump 운전 · IV-E 열림" in msg, msg
    assert "저절로 다시 열리거나 켜지지 않습니다" in msg and "사유 확인 필요" not in msg
    assert lk.pending_toasts == [("warn", msg)]


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
async def test_clear_reason_rf_permit(cfg):
    fresh = [0] * A.STATUS_COUNT
    fresh[A.D_INTERLOCK] = (1 << A.ILK_BASIC) | (1 << A.ILK_PUMP) | (1 << A.ILK_VALVE_OK)
    fresh[A.D_INPUT0] = 1 << A.IN0_IVE_OPEN                 # RF 준비 입력 없음
    lk, events = _link_with(cfg, fresh, seen_aux=1 << A.AUX_RF)
    await lk._read_display()
    msg = next(m for _l, m in events if "수동 요청을 지웠습니다" in m)
    assert "RF 허가 없음 — 필요: RF 준비 입력" in msg, msg


def test_rf_permit_missing_rules(cfg):
    from powderald.convert import Converters
    conv = Converters(cfg)
    s = [0] * A.STATUS_COUNT
    if not DEV.HAS_RF:
        assert P.rf_permit_missing(s) == []
        return
    raw = conv.cvg.to_raw
    prm = {A.D_PRM_RF_MAX: 0, A.D_PRM_BASE_PRESS: raw(0.05), A.D_PRM_RF_MAX_PRESS: raw(1.0)}
    ok = list(s)
    ok[A.D_INTERLOCK] = 1 << A.ILK_RF_OK
    assert P.rf_permit_missing(ok, prm, conv) == []
    bad = list(s)
    bad[A.D_INPUT1] = 1 << A.IN1_RF_ALM
    bad[A.D_ALARM1] = 1 << A.ALM1_RF_REF
    bad[A.D_CVG_RAW] = raw(760.0)
    miss = P.rf_permit_missing(bad, prm, conv)
    assert miss[:4] == ["Valve Permit", "RF 준비 입력", "RF 알람 입력 꺼짐", "반사 전력 알람 리셋"], miss
    assert miss[4] == "PLC 의 RF 상한(PRM_RF_MAX) > 0", miss
    assert len(miss) == 6 and miss[5].startswith("압력 ") and "(지금 " in miss[5], miss
    good = list(s)
    good[A.D_INTERLOCK] = 1 << A.ILK_VALVE_OK
    good[A.D_INPUT1] = 1 << A.IN1_RF_READY
    good[A.D_CVG_RAW] = raw(0.5)
    assert P.rf_permit_missing(good, {**prm, A.D_PRM_RF_MAX: 8000}, conv) == ["PLC 허가 조건 확인"]


# ===================== PC — Pending 은 1 s 넘게 =====================
def test_manual_state_pending_needs_hold(monkeypatch):
    import powderald.state as SM
    clock = [100.0]
    monkeypatch.setattr(SM, "_now", lambda: clock[0])
    pv1 = _pv("PV-1")
    lk = SimpleNamespace(connected=True, applied_valve=pv1, applied_aux=0, status=[0] * A.STATUS_COUNT)
    monkeypatch.setattr(state, "link", lk)
    monkeypatch.setattr(state, "_pend_since", {})

    def at(t):
        clock[0] = t
        return state.manual_state()["pending"]

    assert at(100.0) == [] and at(100.5) == []
    assert at(101.0) == ["PV-1"]
    lk.applied_valve = 0
    assert at(101.1) == []                                   # 조건이 사라지면 바로
    lk.applied_valve = pv1
    assert at(102.0) == [] and at(102.5) == []               # 다시 생기면 처음부터 1 s
    assert at(103.0) == ["PV-1"]
    lk.connected = False
    assert at(103.1) == [] and state._pend_since == {}       # 끊기면 비움


# ===================== PC — RF 켜기도 RF 허가 확인 =====================
@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
async def test_manual_rf_refused_without_rf_permit(wired, notices):
    lk, sim, _cfg = wired
    await at_vacuum(lk, sim)
    state.manual_unlock_until = 9e9
    no = sim.reg[A.D_CMD_NO]
    await C.handle_command({"cmd": "manual_rf", "on": True, "watt": 50})
    assert await wait_until(lambda: any("RF Permit 이 없어 보내지 않았습니다 — 필요:" in m for _l, m in notices), 2), notices
    m = next(m for lv, m in notices if "RF Permit 이 없어 보내지 않았습니다" in m and lv == "warn")
    assert "Valve Permit" in m
    await asyncio.sleep(0.1)
    assert sim.reg[A.D_CMD_NO] == no, "명령을 보냈다"


# ===================== 화면 글 =====================
def test_texts_no_auto_open_promise():
    files = [("uimodel.js",), ("views", "manual.js"), ("views", "schematic.js"), ("views", "faceplate.js"),
             ("views", "panels.js")]
    for f in files:
        src = _src(*f)
        assert "허가가 생기면 바로 열립니다" not in src and "허가가 생기면 바로 열림" not in src, f
    assert "래더 P50" in _src("uimodel.js") and "래더 P50" in _src("views", "faceplate.js")
    assert "(mn.pending || []).indexOf(tag)" in _src("views", "manual.js")


def _fstate(manual):
    return {
        "access": {"local": True}, "device": {"has_rf": DEV.HAS_RF, "has_o3": DEV.HAS_O3},
        "structure": {"valves": DEV.VALVES, "aux": DEV.AUX, "interlocks": DEV.INTERLOCKS, "inputs0": DEV.INPUTS0,
                      "inputs1": DEV.INPUTS1, "mfc": [], "heaters": []},
        "recipe_limits": {"recipe_valves": [v["tag"] for v in DEV.VALVES if not v.get("auto")]},
        "live": {"plc": {"connected": True, "id_state": "ok"}, "valves": 0, "aux": 0, "interlock": 0, "inputs0": 0,
                 "inputs1": 0, "manual": manual, "process": {"running": False}, "extra": {}, "pressure": {"cvg": 1e-3}}}


def test_faceplate_transient_request_is_not_pending():
    vb = DEV.valve_bit("PV-1")
    base = {"unlocked": True, "unlock_left_s": 200, "valve_request": 1 << vb, "valve_permit": True,
            "valve_permit_missing": []}
    S = [_fstate(dict(base, pending=[])), _fstate(dict(base, pending=["PV-1"]))]
    out = _node(r'''
      global.window = global; global.document = null;
      global.T = require(''' + _j("terms.js") + r''');
      global.Ops = require(''' + _j("ops.js") + r''');
      global.UI = require(''' + _j("uimodel.js") + r''');
      global.fmt = { flow: v => Number(v).toFixed(1), temp: v => Number(v).toFixed(1), torr: String,
                     pct: v => String(Math.round(v)), watt: v => Number(v).toFixed(1), num: (v, n) => Number(v).toFixed(n) };
      const F = require(''' + _j("views", "faceplate.js") + r''');
      const S = ''' + json.dumps(S) + r''';
      console.log(JSON.stringify(S.map(s => { const m = F.model('valve', 'PV-1', s, false); return [m.why, m.whyLvl]; })));''')
    assert out[0][1] != "warn", out[0]                       # 출력 0 · 요청 있음이어도 서버 pending 이 없으면 한순간
    assert out[1][1] == "warn" and out[1][0].startswith("PLC 가 지우지 않은 요청"), out[1]


# ===================== 기타 =====================
def test_plclink_seq_codes():
    assert plclink.SEQ_CODES == (A.CMD_PROCESS_START, A.CMD_ALL_CLOSE, A.CMD_MANUAL_APPLY)


def test_version_082():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")[:3]) >= (0, 8, 2)
