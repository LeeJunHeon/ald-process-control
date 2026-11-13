"""v0.8.4 — 전체 시험 · 검증에서 찾은 것의 회귀 시험.

1. 통신 이상(Modbus 응답 형식 오류 · 예외 응답 · 트랜잭션 번호 불일치 · 짧은 응답 · 응답 지연)에서 하트비트가 살아 있고,
   같은 명령이 두 번 실행되지 않고, 보내지지 않은 명령이 '처리됨' 으로 읽히지 않는다.
2. PLC 하트비트 멈춤 동안 PRM 자동 복구도 쓰지 않는다.
3. 수동 조작 명령의 값 검사(NaN · 무한대 · 형식이 틀린 묶음) · 트렌드 이력 구간의 NaN · 깨진 데이터 로그 메타.
4. 고장 주입 표 — 조작판 · plc_ctl 의 고장 키 전부를 대기 중 · 공정 중에 넣어 알람 비트 · 장비 상태 · 출력 · 끝 결과 문구.
5. 레시피 표 경계값 — 변환 → 올리기 → 되읽기 → 되돌리기.
6. 화면 — 사람이 넣거나 파일에서 온 글(레시피 · 실행 정보 · 데이터 로그 메타 · 알람 이력 · 설정의 이름)이 HTML 로 실행되지 않는다.
★ 두 폴더 같은 파일이다(패키지 이름만 다르다) — 장비 차이는 DEV.HAS_RF · DEV.HAS_O3 로 가른다.
"""
import os
import csv
import copy
import json
import time
import struct
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from peald import addresses as A
from peald import commands as C
from peald import device as DEV
from peald import logger
from peald import logview
from peald import loops
from peald import paths
from peald import plclink
from peald import process as P
from peald import recipe as R
from peald import simulator as SIM
from peald import trendlog
from peald import version
from peald.convert import Converters, heater_raw
from peald.modbus import ModbusClient, ModbusError, ModbusTimeout
from peald.state import state

from conftest import FakeSim, free_port, wait_until
from test_process import wired, notices, heater_channels  # noqa: F401 (픽스처)


def test_version_is_at_least_084():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")[:3]) >= (0, 8, 4)


# ===================== 1. Modbus 응답 형식 =====================
async def _raw_server(mode):
    """요청마다 형식이 틀린 응답을 돌려주는 서버 — (서버, 포트)."""
    async def client(r, w):
        try:
            while True:
                tid, _proto, length, unit = struct.unpack(">HHHB", await r.readexactly(7))
                body = await r.readexactly(length - 1)
                fc = body[0]
                pdu = {"empty": b"",                                # 길이 1 — PDU 없음
                       "fc_only": bytes([fc]),                      # 기능 코드만
                       "short_write": bytes([fc, 0, 0]),            # 쓰기 응답 3 바이트
                       "wrong_fc": bytes([6, 0, 4, 0, 1]),          # 읽기를 보냈는데 쓰기 응답 모양
                       "exception": bytes([fc | 0x80, 6])}[mode]    # 예외 응답(장치 사용 중)
                w.write(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)
                await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
    srv = await asyncio.start_server(client, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


@pytest.mark.parametrize("mode,op", [("empty", "read"), ("empty", "write"), ("fc_only", "read"),
                                     ("short_write", "write"), ("short_write", "write_many"), ("wrong_fc", "read")])
async def test_modbus_malformed_response_drops_socket(mode, op):
    """형식이 틀린 응답은 IndexError 로 새거나 성공으로 읽히지 않는다 — 소켓을 버리고 재연결 대상 오류."""
    srv, port = await _raw_server(mode)
    c = ModbusClient("127.0.0.1", port, 1, 300)
    await c.connect()
    try:
        with pytest.raises(ModbusTimeout) as e:
            if op == "read":
                await c.read_holding(0, 2)
            elif op == "write":
                await c.write_single(A.D_CMD_NO, 7)
            else:
                await c.write_multiple(A.D_MFC_SV, [1, 2])
        assert "응답 형식 오류" in str(e.value)
        assert not c.connected, "형식이 틀린 응답 뒤의 소켓을 계속 쓴다"
        with pytest.raises(ModbusError) as e2:                      # 줄 서 있던 다음 요청의 오류에 이유가 붙는다
            await c.read_holding(0, 1)
        assert "응답 형식 오류" in str(e2.value)
    finally:
        await c.close()
        srv.close()


async def test_modbus_exception_response_keeps_socket():
    """예외 응답은 프로토콜 오류다 — 프레임은 맞으므로 소켓은 그대로(ModbusError)."""
    srv, port = await _raw_server("exception")
    c = ModbusClient("127.0.0.1", port, 1, 300)
    await c.connect()
    try:
        with pytest.raises(ModbusError) as e:
            await c.write_single(A.D_CMD_NO, 7)
        assert not isinstance(e.value, ModbusTimeout) and "장치 사용 중" in str(e.value)
        assert c.connected
    finally:
        await c.close()
        srv.close()


def _plc_ctl():
    """test/plc_ctl.py 를 모듈로(시험 도구 — 패키지가 아니라 경로로 읽는다)."""
    import sys
    import importlib
    tool_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test")
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    return importlib.import_module("plc_ctl")


@pytest.mark.parametrize("mode", ["empty", "fc_only", "short_write", "wrong_fc"])
async def test_plc_ctl_modbus_malformed_response_is_io_error(mode):
    """시운전 도구(plc_ctl)의 작은 Modbus 클라이언트도 형식이 틀린 응답을 통신 오류(IOError)로 — 예전에는 IndexError ·
    struct.error traceback 이거나 짧은 쓰기 응답을 성공으로 읽었다."""
    ctl = _plc_ctl()
    srv, port = await _raw_server(mode)

    def run():
        mb = ctl.Modbus("127.0.0.1", port, 1, 1.0)
        try:
            with pytest.raises(IOError):
                if mode == "short_write":
                    mb.write(A.D_MFC_SV, [1, 2])
                else:
                    mb.read(0, 2)
        finally:
            mb.close()
    try:
        await asyncio.to_thread(run)
    finally:
        srv.close()


# ===================== 1. PLC 링크 — 응답 이상을 한 번씩 넣는 시뮬레이터 서버 =====================
class FlakyServer(SIM.SimServer):
    """SimServer + 응답 이상. arm(mode, addr, …) 으로 '그 주소를 다루는 다음 요청' 에 한 번(times 번) 넣는다.
    mode: echo(쓰기 응답 값이 다름) · exception(예외 응답) · short(짧은 응답) · empty(PDU 없음) · tid(번호 불일치) ·
          delay(delay s 뒤에 답)
    apply=False 면 PLC 가 그 요청을 받지 못한 것으로(레지스터에 쓰지 않는다)."""

    def __init__(self, sim, host, port):
        super().__init__(sim, host, port)
        self.rules = []
        self.cmds = []              # PLC 가 받은 명령 (번호, 코드) — 번호 쓰기가 레지스터에 닿은 것만
        self.hb_writes = 0

    def arm(self, mode, addr, fc=None, times=1, apply=True, delay=0.0, code=6):
        self.rules.append({"mode": mode, "addr": addr, "fc": fc, "left": times, "apply": apply,
                           "delay": delay, "code": code})

    @staticmethod
    def _span(pdu):
        """(기능 코드, 첫 주소, 끝 주소) — 모르는 요청이면 None."""
        if len(pdu) < 5 or pdu[0] not in (3, 6, 16):
            return None
        addr, n = struct.unpack(">HH", pdu[1:5])
        return pdu[0], addr, addr + (0 if pdu[0] == 6 else n - 1)

    def _rule(self, pdu):
        sp = self._span(pdu)
        if sp is None:
            return None
        for r in self.rules:
            if r["left"] > 0 and (r["fc"] is None or r["fc"] == sp[0]) and sp[1] <= r["addr"] <= sp[2]:
                r["left"] -= 1
                return r
        return None

    def _note(self, pdu):
        sp = self._span(pdu)
        if sp and sp[0] in (6, 16):
            if sp[1] <= A.D_CMD_NO <= sp[2]:
                self.cmds.append((self.sim.reg[A.D_CMD_NO], self.sim.reg[A.D_CMD_CODE]))
            if sp[1] <= A.D_PC_HB <= sp[2]:
                self.hb_writes += 1

    async def _client(self, reader, writer):
        self._clients.add(writer)
        try:
            while True:
                tid, _proto, length, unit = struct.unpack(">HHHB", await reader.readexactly(7))
                body = await reader.readexactly(max(0, length - 1))
                rule = self._rule(body)
                if rule is None or rule["apply"]:
                    resp = self._handle(body)
                    self._note(body)
                else:
                    resp = body[:5]
                if rule is not None:
                    mode = rule["mode"]
                    if mode == "delay":
                        await asyncio.sleep(rule["delay"])
                    elif mode == "exception":
                        resp = bytes([body[0] | 0x80, rule["code"]])
                    elif mode == "short":
                        resp = resp[:3]
                    elif mode == "empty":
                        resp = b""
                    elif mode == "tid":
                        tid = (tid + 7) & 0xFFFF
                    elif mode == "echo":
                        resp = resp[:4] + bytes([resp[4] ^ 0xFF])
                writer.write(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture
async def flaky(cfg):
    """응답 이상을 넣을 수 있는 시뮬레이터 서버에 붙은 링크 — (링크, 시뮬레이터, 서버, 링크 이벤트 목록)."""
    port = free_port()
    cfg["plc"]["sim_port"] = port
    cfg["plc"]["sim_speed"] = 50
    sim = SIM.PlcSim(cfg, 50)
    srv = FlakyServer(sim, "127.0.0.1", port)
    assert await srv.start()
    events = []
    lk = plclink.PlcLink(cfg, Converters(cfg), on_event=lambda lv, m: events.append((lv, m)))
    lk.start()
    try:
        assert await wait_until(lambda: lk.connected, 5), "시뮬레이터에 연결하지 못했다"
        assert await wait_until(lambda: lk.cmd_regs and srv.hb_writes >= 2, 5)
        yield lk, sim, srv, events
    finally:
        await lk.stop()
        await srv.stop()


async def _hb_alive(srv, secs=1.5):
    """PC 하트비트 쓰기가 secs 안에 다시 PLC 에 닿는가."""
    n = srv.hb_writes
    return await wait_until(lambda: srv.hb_writes >= n + 2, secs)


@pytest.mark.parametrize("mode,apply", [("echo", True), ("exception", True), ("exception", False)])
async def test_cmd_number_is_not_reused_after_write_error(flaky, mode, apply):
    """명령 번호 쓰기가 오류(응답 불일치 · 예외 응답)로 끝나도 번호는 쓴 것으로 친다.
    ★ 예전에는 다음 명령이 같은 번호를 다시 써서 PLC 는 무시하는데(번호가 안 바뀜) PC 는 D00002 == 번호를 보고 앞 명령의
      결과를 읽었다 — 보내지지 않은 펌핑 정지가 '처리됨'."""
    lk, sim, srv, _ev = flaky
    srv.arm(mode, A.D_CMD_NO, fc=6, apply=apply)
    r1, t1 = await lk.send_command(A.CMD_PUMP_START)
    assert r1 is None and "받았을 수" in t1, (r1, t1)
    assert lk.connected, "예외 응답 · 응답 불일치는 연결을 버리지 않는다"
    await asyncio.sleep(0.2)
    assert sim.pump_req is bool(apply)                  # PLC 가 받은 경우에만 실행됐다
    if not apply:
        r, _t = await lk.send_command(A.CMD_PUMP_START)
        assert r == A.RESULT_OK
        assert await wait_until(lambda: sim.pump_req, 2)
    n = len(srv.cmds)
    r2, t2 = await lk.send_command(A.CMD_PUMP_STOP)
    assert r2 == A.RESULT_OK, (r2, t2)
    assert await wait_until(lambda: not sim.pump_req, 2), "펌핑 정지가 PLC 에서 실행되지 않았는데 '처리됨' 으로 읽었다"
    assert len(srv.cmds) == n + 1 and srv.cmds[-1][1] == A.CMD_PUMP_STOP
    nos = [no for no, _c in srv.cmds]
    assert len(set(nos)) == len(nos), f"같은 명령 번호를 두 번 썼다: {nos}"
    assert await _hb_alive(srv)


async def test_short_ack_on_cmd_number_is_result_unknown(flaky):
    """명령 번호 쓰기에 짧은 응답 — 성공으로 읽지 않는다(결과 모름 · 다시 보내지 않음). 링크는 다시 붙고 하트비트가 이어지며
    다음 명령은 새 번호로 한 번만 실행된다."""
    lk, sim, srv, events = flaky
    srv.arm("short", A.D_CMD_NO, fc=6)
    r1, t1 = await lk.send_command(A.CMD_PUMP_START)
    assert r1 is None and "받았을 수" in t1 and "응답 형식 오류" in t1, (r1, t1)
    assert await wait_until(lambda: sim.pump_req, 2)                # PLC 는 받았다 — 한 번
    assert [c for _n, c in srv.cmds].count(A.CMD_PUMP_START) == 1
    assert await wait_until(lambda: any("PLC 통신 끊김" in m for _lv, m in events), 3)
    assert await wait_until(lambda: lk.connected and not lk.blocked_reason(), 5), "다시 붙지 않았다"
    assert await _hb_alive(srv)
    assert not any("IndexError" in m for _lv, m in events), events
    r2, _t2 = await lk.send_command(A.CMD_PUMP_STOP)
    assert r2 == A.RESULT_OK
    assert await wait_until(lambda: not sim.pump_req, 2)
    assert [c for _n, c in srv.cmds].count(A.CMD_PUMP_START) == 1, "같은 명령이 두 번 실행됐다"
    nos = [no for no, _c in srv.cmds]
    assert len(set(nos)) == len(nos), nos


@pytest.mark.parametrize("mode", ["empty", "tid", "short"])
async def test_bad_status_response_reconnects_and_heartbeat_survives(flaky, mode):
    """상태 읽기에 PDU 없는 응답 · 트랜잭션 번호 불일치 · 짧은 응답 — 링크 태스크가 죽지 않고 다시 붙는다. 끊긴 동안은 옛 상태
    값을 남기지 않는다(전부 0 · connected 거짓 — 화면은 '—', 명령은 거절)."""
    lk, sim, srv, events = flaky
    task = lk._task
    seen = []
    orig = lk._drop

    def spy(msg):
        orig(msg)
        seen.append((lk.connected, sum(lk.status)))
    lk._drop = spy
    srv.arm(mode, A.D_STATE, fc=3)
    assert await wait_until(lambda: seen, 3), "끊김을 알아채지 못했다"
    assert seen[0] == (False, 0), "끊긴 뒤에도 옛 상태 값이 남았다"
    assert await wait_until(lambda: lk.connected and lk.status[A.D_DEVICE_ID] == DEV.DEVICE_ID, 5), "다시 붙지 않았다"
    assert not task.done(), "PLC 링크 태스크가 끝났다"
    assert await _hb_alive(srv)
    msgs = " / ".join(m for _lv, m in events)
    assert "IndexError" not in msgs and "링크 오류" not in msgs, msgs
    assert ("번호 불일치" in msgs) if mode == "tid" else ("응답 형식 오류" in msgs), msgs
    r, _t = await lk.send_command(A.CMD_ALARM_ACK)
    assert r == A.RESULT_OK


async def test_slow_responses_below_timeout_keep_link_and_heartbeat(flaky):
    """응답 지연(시간 초과 미만) — 끊지 않고 하트비트가 이어진다. 시간 초과를 넘는 지연은 소켓을 버리고 다시 붙는다(늦게 온
    응답을 다음 요청의 답으로 읽지 않는다)."""
    lk, sim, srv, events = flaky
    srv.arm("delay", A.D_STATE, fc=3, times=3, delay=0.3)
    await asyncio.sleep(1.5)
    assert lk.connected and not any(lv == "err" for lv, _m in events), events
    assert await _hb_alive(srv)
    srv.arm("delay", A.D_STATE, fc=3, times=1, delay=lk.client.timeout + 0.3)
    assert await wait_until(lambda: any("응답 시간 초과" in m for _lv, m in events), 4), events
    assert await wait_until(lambda: lk.connected, 5)
    assert await _hb_alive(srv)
    pv = sim.reg[A.D_CVG_RAW]
    assert await wait_until(lambda: lk.status[A.D_CVG_RAW] == pv, 2)   # 다시 붙은 뒤의 값은 지금 PLC 값


# ===================== 2. PLC 하트비트 멈춤 동안 PRM 자동 복구 =====================
async def test_prm_autofix_waits_while_plc_heartbeat_is_stalled(flaky):
    """PLC STOP(하트비트 멈춤) 동안은 설정과 다른 PRM 을 보아도 쓰지 않는다 — 풀리면 다음 되읽기가 맞춘다."""
    lk, sim, srv, events = flaky
    assert await wait_until(lambda: lk.prm_written and not lk.prm_mismatch, 5)
    addr = A.D_PRM_VENT_TIMEOUT
    want = lk.prm_written[addr]
    sim.set_fault("plc_stop", True)
    assert await wait_until(lambda: lk.plc_hb_stalled, 4)
    sim.write(addr, [want + 7])                                     # 멈춘 동안 다른 곳에서 바뀜
    await asyncio.sleep(plclink.CMD_READ_S * 2 + 0.3)
    assert sim.reg[addr] == want + 7, "PLC 하트비트 멈춤 동안 PRM 을 썼다"
    sim.set_fault("plc_stop", False)
    assert await wait_until(lambda: sim.reg[addr] == want, 6), "멈춤이 풀린 뒤 PRM 을 맞추지 않았다"
    assert any("파라미터" in m and "다시 썼습니다" in m for _lv, m in events)


# ===================== 3. 수동 조작 명령의 값 검사 =====================
def test_finite_number_helper():
    f = C._finite
    assert f(1) == 1.0 and f("2.5") == 2.5 and f(0) == 0.0
    for bad in (float("nan"), float("inf"), float("-inf"), "nan", "inf", None, "", "abc", [1], {"a": 1}, True, False):
        assert f(bad) is None, bad
    assert C._dict({"1": 2}) == {"1": 2} and C._dict([1]) == {} and C._dict(5) == {} and C._dict(None) == {}


async def test_manual_commands_reject_nan_and_wrong_shapes(wired, notices):
    """NaN 은 범위 비교를 모두 지나가 원시값 변환에서 예외가 났다 — 이유를 알리고 거절하고, PLC 에는 아무것도 보내지 않는다."""
    lk, sim, cfg = wired
    nan, inf = float("nan"), float("inf")
    no0 = lk._cmd_no
    cases = [("manual_mfc", {"sccm": {"1": nan}}, "올바르지 않습니다"),
             ("manual_mfc", {"sccm": {"1": inf}}, "올바르지 않습니다"),
             ("manual_mfc", {"sccm": [1, 2]}, "바꿀 MFC 값이 없습니다"),
             ("manual_mfc", {"sccm": 5}, "바꿀 MFC 값이 없습니다"),
             ("manual_heater", {"sv": [1], "power": 3}, "바꿀 히터 값이 없습니다"),
             ("trend_export", {"t0": nan, "t1": 5}, "구간이 올바르지 않습니다"),
             ("trend_export", {"t0": 1, "t1": inf}, "구간이 올바르지 않습니다"),
             ("open_folder", {"which": ["export"]}, "열 수 없는 폴더"),
             ("sim_fault", {"key": ["emo"], "on": True}, "알 수 없는 시험 입력")]
    for ch in heater_channels(cfg, 1):              # 과온 한계가 있는 채널(없는 채널은 그 이유로 먼저 거절된다)
        cases.append(("manual_heater", {"sv": {str(ch): nan}}, "올바르지 않습니다"))
    if DEV.HAS_PCV:
        cases.append(("manual_pcv", {"pct": nan}, "올바르지 않습니다"))
    if DEV.HAS_RF:
        cases += [("manual_rf", {"on": True, "watt": nan}, "올바르지 않습니다"),
                  ("manual_rf", {"on": True, "watt": inf}, "올바르지 않습니다")]
    if DEV.HAS_O3:
        cases += [("manual_o3", {"action": "set", "value": nan}, "올바르지 않습니다"),
                  ("manual_o3", {"action": "on", "value": nan}, "O3 설정을 먼저 넣으세요")]
    for cmd, body, want in cases:
        del notices[:]
        await C.handle_command(dict(body, cmd=cmd))                 # 예외가 새지 않는다
        assert any(want in m for _lv, m in notices), (cmd, body, notices)
    assert lk._cmd_no == no0, "거절한 값으로 PLC 명령을 보냈다"
    assert not any(sim.faults.values())


def test_trend_range_rejects_nan():
    """NaN 구간은 max · min · 비교를 모두 지나가 오류 없이 조회로 넘어갔다(응답 JSON 에 NaN) — 거절한다."""
    nan = float("nan")
    now = time.time()
    for t0, t1 in ((nan, nan), (nan, now), (now - 60, nan)):
        _a, _b, err = trendlog.clamp_range(t0, t1, 90, trendlog.QUERY_MAX_S, "이력 조회")
        assert "올바르지 않습니다" in err, (t0, t1, err)
    a, b, err = trendlog.clamp_range(now - 60, now, 90, trendlog.QUERY_MAX_S, "이력 조회")
    assert not err and b - a == pytest.approx(60, abs=1)
    _a, _b, err = trendlog.clamp_range(float("-inf"), float("inf"), 90, trendlog.QUERY_MAX_S, "이력 조회")
    assert "최대" in err                               # 무한대는 보존 기간으로 잘린 뒤 최대 구간에 걸린다


def test_trend_history_api_nan_is_400_json():
    from peald.server import create_app
    c = TestClient(create_app("", single_instance=False, start_io=False), client=("192.0.2.10", 50000),
                   base_url="http://127.0.0.1:8201")              # 원격(보기 전용)에서도 닿는 GET
    r = c.get("/api/trend/history?t0=nan&t1=nan")
    assert r.status_code == 400 and "NaN" not in r.text
    assert "올바르지 않습니다" in r.json()["error"]


def test_http_responses_forbid_framing():
    """화면 · 정적 파일 · 조회 응답 모두 프레임에 넣을 수 없다(클릭재킹 — 다른 페이지가 이 화면을 프레임에 넣고 누르게 하는 것)."""
    from peald.server import create_app
    c = TestClient(create_app("", single_instance=False, start_io=False), client=("127.0.0.1", 50000),
                   base_url="http://127.0.0.1:8201")
    for path in ("/", "/health", "/js/core.js", "/css/style.css", "/api/datalog/list", "/nope"):
        r = c.get(path)
        assert r.headers.get("x-frame-options") == "DENY", path
        assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", ""), path
    assert c.get("/", headers={"host": "evil.example"}).status_code == 403          # Host 확인은 그대로


def _write_log(name, meta, rows=3):
    os.makedirs(paths.DATALOG_DIR, exist_ok=True)
    with open(os.path.join(paths.DATALOG_DIR, name + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Time", "Elapsed s", "State", "Sequencer", "Block", "Step", "Step Name", "CVG Torr"])
        for i in range(rows):
            w.writerow([f"2026-11-10 10:10:{i:02d}", f"{i}.0", "Running", "Step Run", 1, 1, "S", "0.0123"])
    if meta is not None:
        with open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)


def test_datalog_list_survives_a_broken_meta_file():
    """메타 하나의 형식이 틀리면(recipe 가 사전이 아님 등) 예외로 목록 전체가 500 이 됐다 — 그 파일만 형식을 보고 채운다."""
    good, broken = "20261110_101010_Good", "20261109_090909_Broken"
    _write_log(good, {"recipe": {"name": "R1"}, "number": 7, "started": "2026-11-10 10:10:10", "operator": "Kim",
                      "ended": "2026-11-10 10:11:00", "result": "정상 종료", "rows": 3, "took_s": 2.0})
    _write_log(broken, {"recipe": "not a dict", "number": "x", "started": 5, "operator": ["x"], "sample": {"a": 1},
                        "memo": 3, "ended": "2026-11-09 09:10:00", "result": {"a": 1}, "rows": "many",
                        "took_s": float("nan"), "estimated_ms": [1]})
    items = {it["name"]: it for it in logview.list_logs()}
    assert set(items) == {good, broken}
    assert items[good]["recipe"] == "R1" and items[good]["result"] == "정상 종료" and items[good]["level"] == "ok"
    b = items[broken]
    assert b["recipe"] == "Broken" and b["started"] == "2026-11-09 09:09:09"        # 파일 이름에서
    assert (b["operator"], b["sample"], b["memo"], b["result"]) == ("", "", "", "")
    assert b["number"] is None and b["rows"] is None and b["took_s"] is None and b["estimated_ms"] is None
    json.loads(logview.list_json().decode("utf-8"))                                 # 화면이 읽을 수 있는 JSON
    assert logview.chart(broken)["meta"]["recipe"] == "Broken"


def test_atomic_write_failure_keeps_original_and_leaves_no_tmp(tmp_path):
    """원자적 저장이 도중에 실패해도(직렬화 오류 · 디스크 가득 참) 원래 파일은 그대로이고 반쪽 임시 파일이 남지 않는다."""
    from peald import storage
    folder = tmp_path / "save"
    folder.mkdir()
    path = str(folder / "x.json")
    storage.atomic_write_json(path, {"a": 1})
    with pytest.raises(TypeError):
        storage.atomic_write_json(path, {"a": object()})
    assert storage.read_json(path) == {"a": 1}
    assert os.listdir(str(folder)) == ["x.json"]

    def full(*_a, **_k):
        raise OSError(28, "No space left on device")
    real = os.fsync
    os.fsync = full
    try:
        with pytest.raises(OSError):
            storage.atomic_write_json(path, {"a": 2})
    finally:
        os.fsync = real
    assert storage.read_json(path) == {"a": 1}
    assert os.listdir(str(folder)) == ["x.json"]


def test_config_edits_must_be_a_mapping(cfg):
    from peald import settings
    assert C._dict([("plc.host", "x")]) == {}
    assert settings.prepare(cfg, C._dict(["plc.host"]))["diff"] == settings.prepare(cfg, {})["diff"]


# ===================== 3. 주기 루프 — 한 단계의 예외 · 이어지는 예외 =====================
def test_sampling_stage_failure_does_not_skip_later_stages(cfg, monkeypatch):
    """샘플링의 한 단계(알람 갱신)가 예외를 내도 끝 판정 · 데이터 로그 · 트렌드는 그 주기에 돈다 — 예전에는 뒤 단계를
    주기마다 통째로 건너뛰었다(공정 끝 판정 · 데이터 로그가 멈춘다)."""
    from types import SimpleNamespace
    state.install_config(cfg, [], "example")
    ran = []
    monkeypatch.setattr(state, "refresh", lambda: 1 / 0)
    monkeypatch.setattr(loops, "_datalog_tick", lambda: ran.append("datalog"))
    monkeypatch.setattr(loops.trend, "record", lambda *a, **k: ran.append("trend"))
    monkeypatch.setattr(loops.trendlog, "record", lambda *a, **k: ran.append("trendlog"))
    logs = []
    monkeypatch.setattr(logger, "write", lambda lv, m: logs.append((lv, m)))
    monkeypatch.setattr(loops, "_err_log", {})
    state.link = None
    state.runner = SimpleNamespace(tick=lambda log: ran.append("tick"), progress=lambda: {}, active_run=False)
    try:
        loops.sample_once()
        assert ran == ["tick", "datalog", "trend", "trendlog"], ran
        assert len(logs) == 1 and "샘플링(알람 갱신)" in logs[0][1] and "ZeroDivisionError" in logs[0][1]
    finally:
        state.runner = None


def test_loop_error_log_is_rate_limited(monkeypatch):
    """같은 자리의 같은 예외가 이어지면 30 s 에 한 줄(묶인 횟수와 함께) — 10 Hz 로 로그를 채우지 않는다."""
    logs = []
    now = [100.0]
    monkeypatch.setattr(logger, "write", lambda lv, m: logs.append(m))
    monkeypatch.setattr(loops, "_err_log", {})
    monkeypatch.setattr(loops.time, "monotonic", lambda: now[0])
    for _ in range(50):
        loops.loop_error("샘플링 루프", ValueError("x"))
        now[0] += 0.1
    assert len(logs) == 1 and "ValueError: x" in logs[0]
    now[0] += loops.ERR_LOG_EVERY_S
    loops.loop_error("샘플링 루프", ValueError("x"))
    assert len(logs) == 2 and "앞 오류 49번 더" in logs[1]
    loops.loop_error("샘플링 루프", KeyError("y"))                  # 다른 오류는 바로
    loops.loop_error("화면 전송 루프", ValueError("x"))             # 다른 자리도 바로
    assert len(logs) == 4


# ===================== 4. 고장 주입 표 (가짜 시계 · 한 스캔씩) =====================
def _matrix_cfg(cfg):
    cfg = copy.deepcopy(cfg)
    for h in cfg.get("heaters") or []:
        if h.get("max_c") is None:
            h["max_c"] = 300.0
    cfg["params"]["mfc_tol_sccm"] = 20
    if DEV.HAS_RF:
        cfg.setdefault("rf", {})["max_w"] = 300.0
        cfg["params"].update(rf_max_w=300.0, rf_ref_max_w=20.0, rf_p_max_torr=5.0, rf_ref_ms=500)
    if DEV.HAS_O3:
        cfg.setdefault("o3", {})["full"] = 200.0
        cfg["params"]["o3_max"] = 200.0
    return cfg


def _matrix_recipe():
    rec = R.empty_recipe("Fault Matrix")
    b = R.empty_block("B1")
    b["repeat"] = 5000
    b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    b["steps"] = [{"name": "Dose", "time_ms": 200, "valves": [DEV.PRECURSOR_TAGS[0]], "pause_ok": True},
                  {"name": "Purge", "time_ms": 300, "valves": [], "pause_ok": True},
                  {"name": "React", "time_ms": 400, "valves": [DEV.REACTANT_TAGS[0]], "pause_ok": True},
                  {"name": "Purge 2", "time_ms": 300, "valves": [], "pause_ok": True}]
    if DEV.HAS_RF:
        for st in b["steps"]:
            st["rf"] = False
    if DEV.HAS_O3:
        b["o3"] = 100.0
    rec["blocks"] = [b]
    return rec


class Rig:
    """가짜 시계 시뮬레이터를 '펌핑 끝 · 히터 켜짐 · (Powder) O3 라인 켜짐 · 표 올림' 까지 올린다. running 이면 공정 중
    (스텝 실행), 아니면 대기 중이고 수동으로 전구체 밸브 하나를 열어 둔다."""

    def __init__(self, cfg, running):
        self.cfg = _matrix_cfg(cfg)
        self.conv = Converters(self.cfg)
        self.fs = FakeSim(self.cfg, speed=50)
        s = self.s = self.fs.sim
        for addr, (_n, v) in plclink.param_words(self.cfg, self.conv).items():
            s.write(addr, [v])                                      # PC 가 연결 때 쓰는 PRM
        assert self.fs.cmd(A.CMD_PUMP_START) == A.RESULT_OK
        assert self.run(120, 0.05, lambda: self.ilk(A.ILK_VACUUM) and self.ilk(A.ILK_VALVE_OK)), "진공이 안 됐다"
        if DEV.HAS_O3:
            # ★ v0.8.5: O3 라인은 두 단계 — 래더 P50 은 O3 허가가 없는 동안 발생기 요청을 지운다. 바이패스 펌프 + IV-B 를
            #   보내고 허가가 선 뒤에 발생기를 보낸다(PC 의 O3 라인 켜기와 같은 순서)
            line = (1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB)
            self.cmd12(aux=line, o3=self.conv.o3.to_raw(100.0))
            assert self.run(20, 0.05, lambda: self.ilk(A.ILK_O3_OK)), "O3 허가가 안 났다"
            self.cmd12(aux=line | (1 << A.AUX_O3_GEN), o3=self.conv.o3.to_raw(100.0))
            assert self.run(2, 0.05, lambda: s.o3_gen_on), "O3 발생기가 켜지지 않았다"
        s.write(A.D_HEATER_POWER, [1])
        s.write(A.D_HEATER_SV, [heater_raw(100.0)] + [0] * 11)
        assert self.fs.cmd(A.CMD_HEATER_APPLY) == A.RESULT_OK
        self.table = R.to_plc_words(self.cfg, self.conv, _matrix_recipe())
        s.write(A.RCP_SUM_BASE, self.table["words"])
        self.run(1.5)
        assert s.reg[A.D_RECIPE_OK] == 1
        self.pv = 1 << DEV.valve_bit(DEV.PRECURSOR_TAGS[0])
        if running:
            assert self.fs.cmd(A.CMD_PROCESS_START) == A.RESULT_OK
            assert self.run(30, 0.02, lambda: s.reg[A.D_STATE] == A.STATE_RUN and s.reg[A.D_SEQ_STATE] == 4)
            self.run(1.0)
        else:
            self.cmd12(valve=self.pv, aux=s.man_aux, o3=s.ao[SIM.AO_O3] if DEV.HAS_O3 else None)
            self.run(0.5)
            assert s.reg[A.D_VALVE_OUT] & self.pv, "수동 밸브가 열리지 않았다"

    def ilk(self, bit):
        return A.bit(self.s.reg[A.D_INTERLOCK], bit)

    def cmd12(self, valve=0, aux=0, o3=None, rf=None):
        s = self.s
        lo, hi = A.split_dword(valve)
        s.write(A.D_MANUAL_VALVE, [lo, hi, 0, 0])
        s.write(A.D_MANUAL_AUX, [aux])
        if o3 is not None:
            s.write(A.D_O3_SV, [o3])
        if rf is not None:
            s.write(A.D_RF_SV, [rf])
        assert self.fs.cmd(A.CMD_MANUAL_APPLY) == A.RESULT_OK

    def run(self, secs, dt=0.02, cond=None):
        """PC 하트비트를 쓰면서(200 ms 마다) secs 초 돈다. cond 가 참이 되면 그 스캔에서 True."""
        s = self.s
        for i in range(int(round(secs / dt))):
            if i % max(1, int(round(0.2 / dt))) == 0:
                s.write(A.D_PC_HB, [(s.reg[A.D_PC_HB] + 1) & 0xFFFF])
            self.fs.step(1, dt)
            if cond is not None and cond():
                return True
        return False

    def alarm(self, word, bit):
        return A.bit(self.s.reg[A.D_ALARM0 if word == 0 else A.D_ALARM1], bit)

    def end_text(self):
        s = self.s
        return P.judge_end(list(s.reg[0:A.STATUS_COUNT]), {"words": self.table["words"]}, None)


def _alarm_name(word, bit):
    return next(d["name"] for d in (DEV.ALARMS0 if word == 0 else DEV.ALARMS1) if d["bit"] == bit)


# 고장 키 → (대기 중 알람, 공정 중 알람, 고장을 넣은 뒤 도는 시간 s — 묶음이면 (대기 중, 공정 중)).
# 알람 = (워드, 비트) 또는 None(알람 없음 · 상태 그대로).
# ★ README '비트 정의' · '래더와 같게 맞춘 것' 의 조건 그대로 — 리드는 공정 중에만(b4), 공압 · N2 는 1 s 뒤, IV-E 5 s · 펌프
#   피드백 10 s, MFC 편차 10 s, PC 하트비트 멈춤은 와치독(3 s) 뒤 경고 b6(대기 중이면 바로 안전 정지 · 공정 중이면 계속).
_PUMP_BLOCK = ("emo", "air", "cw", "pump_alm", "pump_nofb")         # 이 고장은 펌프 · 배기 요청도 지운다(명령 8 결과 1 조건)
_MATRIX = {
    "emo": ((0, A.ALM0_EMO), (0, A.ALM0_EMO), 1.0),
    "air": ((0, A.ALM0_AIR), (0, A.ALM0_AIR), 2.5),
    "n2": ((0, A.ALM0_N2), (0, A.ALM0_N2), 2.5),
    "cw": ((0, A.ALM0_CW), (0, A.ALM0_CW), 1.0),
    "lid": (None, (0, A.ALM0_LID), 1.0),
    "pump_alm": ((0, A.ALM0_PUMP), (0, A.ALM0_PUMP), 1.0),
    "leak": ((0, A.ALM0_LEAK), (0, A.ALM0_LEAK), 1.0),
    "ot": ((0, A.ALM0_OT), (0, A.ALM0_OT), 1.0),
    "mfc1_stuck": (None, (0, A.ALM0_MFC), 20.0),
    "tc1_comm": (None, None, 2.0), "tc2_comm": (None, None, 2.0), "tc3_comm": (None, None, 2.0),
    "chamber_leak": (None, None, 2.0),
    "old_p50": (None, None, 2.0),
    # ★ v0.8.5: 공정 중에는 IV-E 열림 리미트 · 펌프 운전 입력이 빠지는 순간 Valve Permit 이 꺼져, 5 s · 10 s 알람보다 먼저
    #   0.5 s 뒤 알람1 b8 'Valve Permit Lost in Process' 로 끝난다(그때는 b0 · b5 가 아직 없고 펌프 요청도 남아 있다)
    "ive_stuck": ((1, A.ALM1_IVE), (1, A.ALM1_VALVE_PERMIT), (7.0, 1.5)),
    "pump_nofb": ((0, A.ALM0_PUMP), (1, A.ALM1_VALVE_PERMIT), (12.0, 1.5)),
    # v0.8.5: 챔버 대기압 입력(압력은 그대로) — 대기 중에는 알람 없이 Valve Permit 만 꺼지고, 공정 중에는 0.5 s 뒤 b8
    "atm_sw": (None, (1, A.ALM1_VALVE_PERMIT), (2.0, 1.5)),
}
_VP = (1, A.ALM1_VALVE_PERMIT)
if DEV.HAS_RF:
    _MATRIX.update({"rf_notready": (None, None, 2.0), "rf_alm": ((1, A.ALM1_RF), (1, A.ALM1_RF), 1.0)})
if DEV.HAS_O3:
    _MATRIX.update({"o3_alm": ((1, A.ALM1_O3_GEN), (1, A.ALM1_O3_GEN), 1.0),
                    "o3_room": ((1, A.ALM1_O3_ROOM), (1, A.ALM1_O3_ROOM), 1.0),
                    "bp_alm": ((1, A.ALM1_BYPASS_PUMP), (1, A.ALM1_BYPASS_PUMP), 1.0),
                    # 공정 중에는 운전 입력이 꺼지는 스캔에 O3 허가가 빠져 b3 로 먼저 선다(10 s 피드백 타이머 전에 요청이 지워진다)
                    "bp_nofb": ((1, A.ALM1_BYPASS_PUMP), (1, A.ALM1_O3_GEN), 12.0)})
# 따로 보는 것: pc_hb_stop(공정 계속 → 끝난 뒤 안전 정지) · plc_stop(스캔 멈춤) · rf_ref(RF 가 켜져 있어야 한다)
_SEPARATE = {"pc_hb_stop", "plc_stop"} | ({"rf_ref"} if DEV.HAS_RF else set())
# 알람이 서면 그 원인이 스스로 없어지는 것 — 공정이 끝나거나(리드 b4 · MFC b12 · O3 허가 b3 는 공정 중에만 본다) 펌프 출력이
# 꺼진다(운전 피드백 타이머는 출력이 켜져 있을 때만 잰다). 고장을 둔 채 리셋해도 다시 서지 않는다
_SELF_CLEARING = {"lid", "mfc1_stuck", "pump_nofb", "bp_nofb"}


def test_fault_matrix_covers_every_fault_key():
    keys = {f["key"] for f in SIM.visible_faults()}
    assert keys == set(_MATRIX) | _SEPARATE, (keys - set(_MATRIX) - _SEPARATE, (set(_MATRIX) | _SEPARATE) - keys)


@pytest.mark.parametrize("running", [False, True], ids=["idle", "process"])
@pytest.mark.parametrize("key", sorted(_MATRIX))
def test_fault_matrix(cfg, key, running):
    """고장 하나를 대기 중 · 공정 중에 넣는다 — 알람 비트(중대면 안전 정지) · 밸브/RF/O3 출력 · 수동 요청 · 끝 결과 문구."""
    idle_alm, run_alm, secs = _MATRIX[key]
    alm = run_alm if running else idle_alm
    secs = secs[running] if isinstance(secs, tuple) else secs
    rig = Rig(cfg, running)
    s = rig.s
    s.set_fault(key, True)
    rig.run(secs)
    a0, a1 = s.reg[A.D_ALARM0], s.reg[A.D_ALARM1]
    crit = bool((a0 & DEV.CRITICAL_MASK0) or a1)
    if alm is None:
        assert not crit and not a0 and not a1, f"알람이 없어야 한다: {a0:#06x} {a1:#06x}"
        assert s.reg[A.D_STATE] == (A.STATE_RUN if running else A.STATE_IDLE)
        assert not rig.ilk(A.ILK_SAFE_STOP_REQ)
        if running:
            assert s.reg[A.D_SEQ_STATE] in (4, 5) and s.running
        elif key in ("lid", "atm_sw"):
            # 대기 중 리드 — 알람은 없지만 기본 인터락 · Valve Permit 이 빠지고 P50 이 수동 요청을 지운다
            # (대기압 입력도 — 기본 인터락은 그대로, Valve Permit 만 빠진다)
            assert rig.ilk(A.ILK_BASIC) == (key != "lid") and not rig.ilk(A.ILK_VALVE_OK)
            assert s.reg[A.D_VALVE_OUT] & DEV.MANUAL_VALVE_MASK == 0 and s.man_valve == 0
        else:
            assert s.reg[A.D_VALVE_OUT] & rig.pv, "알람 없는 고장인데 수동 밸브가 닫혔다"
        if key.startswith("tc"):
            assert not A.bit(s.reg[A.D_TC_COMM], int(key[2]) - 1) and s.heater_power == 1
        if key == "rf_notready":
            assert not rig.ilk(A.ILK_RF_OK)
        return
    word, bit = alm
    assert rig.alarm(word, bit), f"{_alarm_name(word, bit)} 가 서지 않았다 ({a0:#06x} {a1:#06x})"
    assert crit and rig.ilk(A.ILK_SAFE_STOP_REQ) and s.reg[A.D_STATE] == A.STATE_SAFE_STOP
    # 안전 정지 — 공정 밸브 · RF · O3 발생기 출력 0, 수동 요청 지움
    assert s.reg[A.D_VALVE_OUT] == 0 and s.valve_out == 0
    assert s.man_valve == 0 and s.man_aux == 0
    aux = s.reg[A.D_AUX_OUT]
    assert not A.bit(aux, A.AUX_RF) and not A.bit(aux, A.AUX_O3_GEN) and not A.bit(aux, A.AUX_VV)
    # 펌프 · 배기: 비상정지 입력 · 공압 · 냉각수 · 펌프 알람이면 요청을 지워 꺼지고, 그 밖의 안전 정지는 그대로 둔다
    if alm == _VP:
        # v0.8.5: 원래의 5 s · 10 s 알람은 아직 서지 않았다 — Valve Permit 꺼짐이 먼저 공정을 끊었다
        assert (idle_alm is None or not rig.alarm(*idle_alm)) and A.bit(aux, A.AUX_PUMP)
    elif key in _PUMP_BLOCK:
        assert not A.bit(aux, A.AUX_PUMP) and not A.bit(aux, A.AUX_IVE)
    else:
        assert A.bit(aux, A.AUX_PUMP)
    assert s.heater_power == (0 if key == "ot" else 1)              # 히터 전원은 과온일 때만 PLC 가 끈다
    if running:
        assert not s.running and s.reg[A.D_SEQ_STATE] == 8
        assert all(s.ao[SIM.ao_mfc(n)] == 0 for n in range(1, DEV.MFC_COUNT + 1))    # 끝에서 MFC AO 0
        res = rig.end_text()
        assert res.startswith("중단 (안전 정지 — ") and _alarm_name(word, bit) in res, res
    # 원인이 남은 채 리셋 → 다시 서고 안전 정지 유지(원인이 스스로 없어지는 알람은 풀린다), 원인을 없애고 리셋 → 대기
    assert rig.fs.cmd(A.CMD_ALARM_RESET) == A.RESULT_OK
    rig.run(0.3)
    if key in _SELF_CLEARING or alm == _VP:         # b8 은 공정 중에만 본다 — 공정이 끝났으니 다시 서지 않는다
        assert not rig.alarm(word, bit) and s.reg[A.D_STATE] == A.STATE_IDLE
    else:
        assert rig.alarm(word, bit) and s.reg[A.D_STATE] == A.STATE_SAFE_STOP
    assert s.reg[A.D_VALVE_OUT] == 0 and s.man_valve == 0
    s.set_fault(key, False)
    rig.run(1.5)
    assert rig.fs.cmd(A.CMD_ALARM_RESET) == A.RESULT_OK
    rig.run(0.5)
    assert s.reg[A.D_ALARM0] == 0 and s.reg[A.D_ALARM1] == 0 and s.reg[A.D_STATE] == A.STATE_IDLE
    assert s.reg[A.D_VALVE_OUT] == 0 and s.man_valve == 0, "리셋 뒤 밸브가 저절로 다시 열렸다"


def test_fault_pc_hb_stop_idle_trips_now_process_runs_to_end(cfg):
    """PC 하트비트 멈춤 — 대기 중이면 와치독 뒤 바로 안전 정지(경고 b6 만), 공정 중이면 끝까지 돌고 끝난 뒤 트립이 남아
    있으면 안전 정지. 트립은 PC 링크가 돌아온 뒤 알람 리셋으로만 풀린다."""
    wdt = A.PRM_DEFAULTS["pc_wdt_ms"] / 1000.0
    rig = Rig(cfg, False)
    s = rig.s
    s.set_fault("pc_hb_stop", True)
    rig.run(wdt - 0.5)
    assert s.reg[A.D_STATE] == A.STATE_IDLE and s.reg[A.D_VALVE_OUT] & rig.pv
    rig.run(1.0)
    assert rig.alarm(0, A.ALM0_PC_LINK) and not (s.reg[A.D_ALARM0] & DEV.CRITICAL_MASK0)
    assert s.reg[A.D_STATE] == A.STATE_SAFE_STOP and s.reg[A.D_VALVE_OUT] == 0 and s.man_valve == 0
    assert A.bit(s.reg[A.D_AUX_OUT], A.AUX_PUMP) and s.heater_power == 1        # 펌프 · 히터는 그대로
    assert rig.fs.cmd(A.CMD_ALARM_RESET) == A.RESULT_OK                         # PC 링크가 없으면 안 풀린다
    rig.run(0.3)
    assert rig.alarm(0, A.ALM0_PC_LINK) and s.reg[A.D_STATE] == A.STATE_SAFE_STOP
    s.set_fault("pc_hb_stop", False)
    rig.run(0.5)
    assert rig.fs.cmd(A.CMD_ALARM_RESET) == A.RESULT_OK
    rig.run(0.3)
    assert s.reg[A.D_ALARM0] == 0 and s.reg[A.D_STATE] == A.STATE_IDLE

    rig = Rig(cfg, True)
    s = rig.s
    s.set_fault("pc_hb_stop", True)
    rig.run(wdt + 2.0)
    assert rig.alarm(0, A.ALM0_PC_LINK) and s.running and s.reg[A.D_STATE] == A.STATE_RUN
    assert not rig.ilk(A.ILK_SAFE_STOP_REQ)
    assert rig.fs.cmd(A.CMD_STOP_AFTER_CYCLE) == A.RESULT_OK                    # 명령은 PLC 가 받는다(시험은 직접 쓴다)
    assert rig.run(5, 0.02, lambda: not s.running)
    assert s.reg[A.D_SEQ_STATE] == 6
    rig.run(0.1)
    assert s.reg[A.D_STATE] == A.STATE_SAFE_STOP and s.reg[A.D_VALVE_OUT] == 0
    res = rig.end_text()
    assert "끝난 뒤 안전 정지" in res and P.TRIP_LEFT_TEXT in res, res


def test_fault_plc_stop_freezes_scan_and_restart_clears_process(cfg):
    """PLC STOP — 스캔이 멈춰 상태 영역 · PLC 하트비트가 그대로이고 출력은 꺼진다. RUN 이면 첫 스캔이 공정을 지운다."""
    rig = Rig(cfg, True)
    s = rig.s
    hb, st = s.reg[A.D_PLC_HB], s.reg[A.D_STATE]
    s.set_fault("plc_stop", True)
    rig.run(3.0)
    assert s.reg[A.D_PLC_HB] == hb and s.reg[A.D_STATE] == st, "STOP 인데 상태 영역이 바뀌었다"
    assert s.valve_out == 0 and s.heater_power == 0 and not s.pump_on and not s.rf_on and not s.o3_gen_on
    s.set_fault("plc_stop", False)
    rig.run(0.1)
    assert s.reg[A.D_STATE] == A.STATE_IDLE and s.reg[A.D_SEQ_STATE] == 0 and s.reg[A.D_SEQ_BLOCK] == 0
    assert not s.running and s.reg[A.D_VALVE_OUT] == 0
    assert P.judge_end(list(s.reg[0:A.STATUS_COUNT]), {"words": rig.table["words"]}, None).startswith("중단 (PLC 재시작")


@pytest.mark.skipif(not DEV.HAS_RF, reason="RF 가 있는 장비만")
def test_fault_rf_reflected_trips_after_delay_and_rf_goes_off(cfg):
    """RF 반사 — RF 출력 중 반사가 한계를 PRM_RF_REF_MS 넘게 넘으면 알람1 b2 · 안전 정지 · RF 꺼짐(요청도 지움)."""
    rig = Rig(cfg, False)
    s = rig.s

    def hold(secs, cond=None):
        """압력을 RF 허가 창(베이스 < P ≤ RF 최대 압력) 안에 붙잡고 돈다 — 펌핑 중이라 그냥 두면 베이스 아래로 내려간다."""
        for _ in range(int(round(secs / 0.02))):
            s.base_pressure = 0.5
            if rig.run(0.02, 0.02, cond):
                return True
        return False
    assert hold(3, lambda: rig.ilk(A.ILK_RF_OK)), "RF 허가가 안 났다"
    rig.cmd12(aux=1 << A.AUX_RF, rf=rig.conv.rf.to_raw(100.0))
    assert hold(1, lambda: A.bit(s.reg[A.D_AUX_OUT], A.AUX_RF)), "RF 가 켜지지 않았다"
    hold(1.0)
    assert not s.reg[A.D_ALARM1] and A.bit(s.reg[A.D_AUX_OUT], A.AUX_RF)     # 정상 반사(3 %)는 한계 아래
    s.set_fault("rf_ref", True)
    hold(0.3)
    assert not rig.alarm(1, A.ALM1_RF_REF), "반사 초과 허용 시간(500 ms) 전에 알람이 섰다"
    hold(0.6)
    assert rig.alarm(1, A.ALM1_RF_REF) and s.reg[A.D_STATE] == A.STATE_SAFE_STOP
    assert not A.bit(s.reg[A.D_AUX_OUT], A.AUX_RF) and s.man_aux == 0
    # 반사 알람이 래치된 동안은 RF 허가가 나지 않는다 — 리셋 뒤에도 RF 는 저절로 다시 켜지지 않는다
    s.set_fault("rf_ref", False)
    hold(0.5)
    assert not rig.ilk(A.ILK_RF_OK)
    assert rig.fs.cmd(A.CMD_ALARM_RESET) == A.RESULT_OK
    hold(1.0)
    assert s.reg[A.D_STATE] == A.STATE_IDLE and not A.bit(s.reg[A.D_AUX_OUT], A.AUX_RF) and s.man_aux == 0


# ===================== 5. 레시피 표 경계값 — 변환 → 올리기 → 되읽기 → 되돌리기 =====================
def _limit_recipe():
    """한계까지 채운 레시피 — 스텝 100(블록 10 × 10) · 그룹 5 · 스텝 20 ms 와 3,276,700 ms · 반복 1,000,000 · 32,767."""
    rec = R.empty_recipe("Limits")
    blocks = []
    for bi in range(R.BLOCK_MAX):
        b = R.empty_block(f"B{bi + 1}")
        b["repeat"] = R.BLOCK_REPEAT_MAX if bi == 0 else bi + 1
        b["mfc_sccm"] = [100.0 + bi] + [0.0] * (DEV.MFC_COUNT - 1)
        steps = []
        for si in range(R.STEP_MAX // R.BLOCK_MAX):
            st = {"name": f"S{si + 1}", "time_ms": 1000 + si, "valves": [], "pause_ok": bool(si % 2)}
            if DEV.HAS_RF:
                st["rf"] = False
            steps.append(st)
        steps[0]["time_ms"] = R.STEP_MS_MIN
        steps[1]["time_ms"] = R.STEP_MS_MAX
        steps[2]["time_ms"] = R.LONG_STEP_MS + 150              # 60 s 넘는 스텝 — 100 ms 단위로 내린다
        steps[3]["valves"] = [DEV.PRECURSOR_TAGS[0]]
        steps[5]["valves"] = [DEV.REACTANT_TAGS[0]]
        b["steps"] = steps
        blocks.append(b)
    rec["blocks"] = blocks
    rec["groups"] = [{"from_block": 2 * i + 1, "to_block": 2 * i + 2,
                      "repeat": R.GROUP_REPEAT_MAX if i == 0 else i + 1} for i in range(R.GROUP_MAX)]
    return rec


def test_recipe_limits_validate_and_reject_one_past(cfg):
    rec = _limit_recipe()
    res = R.validate(cfg, rec)
    assert not res["errors"], res["errors"][:3]
    assert any("100 ms 단위" in w["msg"] for w in res["warnings"])

    def errs(mut):
        r = copy.deepcopy(rec)
        mut(r)
        return " / ".join(e["msg"] for e in R.validate(cfg, r)["errors"])
    step = rec["blocks"][0]["steps"][0]
    assert "스텝 시간" in errs(lambda r: r["blocks"][0]["steps"][0].update(time_ms=R.STEP_MS_MIN - 1))
    assert "스텝 시간" in errs(lambda r: r["blocks"][0]["steps"][1].update(time_ms=R.STEP_MS_MAX + 1))
    assert "전체 스텝 개수" in errs(lambda r: r["blocks"][0]["steps"].append(dict(step)))
    assert "블록 개수" in errs(lambda r: r["blocks"].append(copy.deepcopy(r["blocks"][0])))
    assert "최대 5 개" in errs(lambda r: r["groups"].append({"from_block": 10, "to_block": 10, "repeat": 1}))
    assert "블록 반복" in errs(lambda r: r["blocks"][0].update(repeat=R.BLOCK_REPEAT_MAX + 1))
    assert "블록 반복" in errs(lambda r: r["blocks"][0].update(repeat=0))
    assert "반복은 1~32767" in errs(lambda r: r["groups"][0].update(repeat=R.GROUP_REPEAT_MAX + 1))
    assert "정수" in errs(lambda r: r["blocks"][0].update(repeat=2.5))


async def test_recipe_limits_upload_readback_and_back_convert(link):
    """한계 레시피를 실제 통신 경로로 올리고 되읽어 — 표가 워드까지 같고, 되돌린 값이 원래 레시피와 같다."""
    lk, sim, cfg = link
    rec = _limit_recipe()
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    assert len(tbl["words"]) == A.RCP_AREA_COUNT == 1120
    assert tbl["checksum"] == R.checksum_of(tbl["words"]) and tbl["number"] == R.recipe_number(rec) != 0
    ok, why = await lk.upload_recipe(tbl["words"], tbl["checksum"])
    assert ok, why
    back = await lk.read_recipe_area()
    assert list(back) == tbl["words"]
    assert sim.reg[A.D_RECIPE_OK] == 1 and sim.reg[A.D_RECIPE_SUM_PLC] == tbl["checksum"]
    info = R.from_plc_words(back)
    assert (info["step_count"], info["block_count"], info["group_count"]) == (R.STEP_MAX, R.BLOCK_MAX, R.GROUP_MAX)
    assert info["number"] == tbl["number"] and info["checksum"] == tbl["checksum"]
    assert [b["repeat"] for b in info["blocks"]] == [b["repeat"] for b in rec["blocks"]]
    assert info["blocks"][0]["repeat"] == R.BLOCK_REPEAT_MAX          # DWORD(하위 · 상위 워드)
    assert [(b["first_step"], b["last_step"]) for b in info["blocks"]] == [(10 * i + 1, 10 * i + 10) for i in range(10)]
    assert info["groups"] == rec["groups"] and info["groups"][0]["repeat"] == R.GROUP_REPEAT_MAX
    got = R.recipe_from_words(back)
    for b0, b1 in zip(rec["blocks"], got["blocks"]):
        assert [s["time_ms"] for s in b1["steps"]] == [s["time_ms"] for s in b0["steps"]]
        assert [s["valves"] for s in b1["steps"]] == [s["valves"] for s in b0["steps"]]
    # 플래그 · 블록 설정값 워드
    sb = A.D_RCP_STEP_BASE - A.RCP_SUM_BASE
    assert [back[sb + i * A.RCP_STEP_STRIDE + A.RCP_STEP_FLAGS] & A.RCP_FLAG_PAUSE_OK for i in range(4)] == [0, 1, 0, 1]
    bb = A.D_RCP_BLOCK_BASE - A.RCP_SUM_BASE
    conv = Converters(cfg)
    assert back[bb + A.RCP_BLOCK_MFC] == conv.mfc[1].to_raw(100.0)
    # 한 워드가 바뀌면 PLC 검사가 불합격이다(합계)
    sim.write(A.D_RCP_STEP_BASE + A.RCP_STEP_TIME_LO, [R.STEP_MS_MIN + 1])
    assert await wait_until(lambda: sim.reg[A.D_RECIPE_OK] == 0, 3)


def test_plc_runs_boundary_step_times_as_pc_computes(cfg):
    """PC 가 계산한 실효 스텝 시간(최소 열림 · 60 s 넘는 스텝의 100 ms 단위 · 20 ms)과 래더 대역의 스텝 시간이 같다."""
    cfg = copy.deepcopy(cfg)
    cfg["params"]["valve_min_ms"] = 200
    cfg["params"]["mfc_stable_s"] = 0
    conv = Converters(cfg)
    rec = R.empty_recipe("Edges")
    b = R.empty_block("B1")
    b["repeat"] = 2
    times = [R.STEP_MS_MIN, 50, R.LONG_STEP_MS + 150, R.LONG_STEP_MS, 30]
    valves = [[], [DEV.PRECURSOR_TAGS[0]], [], [], [DEV.REACTANT_TAGS[0]]]
    b["steps"] = [dict({"name": f"S{i}", "time_ms": t, "valves": v, "pause_ok": False}, **({"rf": False} if DEV.HAS_RF else {}))
                  for i, (t, v) in enumerate(zip(times, valves))]
    rec["blocks"] = [b]
    assert not R.validate(cfg, rec)["errors"]
    first, rep = R.step_times(b, R.prm_value(cfg, "valve_min_ms"))
    assert first == [20, 200, 60100, 60000, 200] and rep == [20, 200, 60100, 60000, 200]
    fs = FakeSim(cfg, speed=50, o3=True)
    s = fs.sim
    for addr, (_n, v) in plclink.param_words(cfg, conv).items():
        s.write(addr, [v])
    s.write(A.RCP_SUM_BASE, R.to_plc_words(cfg, conv, rec)["words"])
    s.write(A.D_PC_HB, [1])
    assert fs.cmd(A.CMD_PUMP_START) == A.RESULT_OK
    for _ in range(4000):
        fs.step(1, 0.05)
        s.write(A.D_PC_HB, [(s.reg[A.D_PC_HB] + 1) & 0xFFFF])
        if A.bit(s.reg[A.D_INTERLOCK], A.ILK_START_OK):
            break
    assert fs.cmd(A.CMD_PROCESS_START) == A.RESULT_OK
    seen = {}

    def note():
        if s.reg[A.D_SEQ_STATE] == 4 and A.dword(s.reg[A.D_SEQ_BLOCK_PASS], s.reg[A.D_SEQ_BLOCK_PASS + 1]) == 1:
            seen[s.reg[A.D_SEQ_STEP]] = (s.step_dur, s.step_long)
    note()                                          # 안정 시간 0 — 시작 스캔에 스텝 1 이 적재된다(20 ms = 한 스캔)
    for _ in range(int(125 / 0.02)):                # 한 사이클 약 120.5 s — 20 ms 스캔으로
        fs.step(1, 0.02)
        note()
        if len(seen) == 5 and s.reg[A.D_SEQ_STEP] == 1 and s.cycle == 2:
            break
    assert [seen[i][0] for i in range(1, 6)] == first, seen
    assert [seen[i][1] for i in range(1, 6)] == [False, False, True, False, False]
    assert R.total_ms(cfg, rec) == 2 * sum(first)


# ===================== 6. 화면 — 넣은 글이 실행되지 않는다 (playwright · 내장 시뮬레이터) =====================
_P1 = '<img src=x onerror=window.__xss=1>'
_P2 = '"><svg onload=window.__xss=2>'
_P3 = "'><script>window.__xss=3</script>"
_P4 = "</td><img src=x onerror=window.__xss=4>"


def _seed_untrusted_text():
    """레시피 · 데이터 로그(메타 · CSV) · 알람 이력 · 누설률 기록 · 마지막 작업자에 HTML 조각을 심는다."""
    import datetime
    from peald import leakcheck
    rec = R.empty_recipe("XSS'&amp;test")
    rec["memo"] = " ".join((_P1, _P2, _P3))
    b = R.empty_block(_P1)
    b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    b["steps"] = [dict({"name": _P1, "time_ms": 300, "valves": [DEV.PRECURSOR_TAGS[0]], "pause_ok": True},
                       **({"rf": False} if DEV.HAS_RF else {})),
                  dict({"name": _P3, "time_ms": 300, "valves": [], "pause_ok": True},
                       **({"rf": False} if DEV.HAS_RF else {}))]
    rec["blocks"] = [b]
    os.makedirs(paths.RECIPES_DIR, exist_ok=True)
    with open(os.path.join(paths.RECIPES_DIR, "XSS'&amp;test.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    with open(os.path.join(paths.RECIPES_DIR, "Bad&'file.json"), "w", encoding="utf-8") as f:
        json.dump(dict(rec, name=_P2, memo={"x": _P1}, modified=_P4), f, ensure_ascii=False)    # 형식이 틀린 값도
    name = "20261110_101010_XSS'&log"
    head = ["Time", "Elapsed s", "State", "Sequencer", "Block", "Step", "Step Name", "Cycle", "Group Pass",
            "Step Elapsed ms", "CVG Torr", "MFC1 Actual sccm", "MFC1 Setpoint sccm", "CH1 " + _P1 + " Actual ℃",
            "CH1 " + _P1 + " Setpoint ℃", "Open Valves", "Valve Word", "Aux Word", "Alarm0", "Alarm1"]
    os.makedirs(paths.DATALOG_DIR, exist_ok=True)
    with open(os.path.join(paths.DATALOG_DIR, name + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for i in range(40):
            w.writerow([f"2026-11-10 10:10:{i:02d}", f"{i}.0", _P1 if i % 2 else "Running", _P2, 1, 1 + i // 20,
                        _P1 if i < 20 else _P4, 1, 1, i * 10, "0.01234", "100.0", "100.0", "150.0", "150.0", _P3,
                        "0x0001", "0x000A", "0x0000", "0x0000"])
    meta = {"recipe": dict(rec, name=_P1), "number": 123, "checksum": 456, "estimated_ms": 1000,
            "started": "2026-11-10 10:10:10", "operator": _P1, "sample": _P2, "memo": _P3, "ended": _P4,
            "result": "중단 (" + _P1 + ")", "rows": 40, "took_s": 39.0, "app_version": _P1,
            "prm": [{"name": _P1, "value": _P2, "unit": _P3}], "resumed": [{"at": _P1, "why": _P2}]}
    with open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    today = datetime.datetime.now()
    os.makedirs(paths.ALARMS_DIR, exist_ok=True)
    with open(os.path.join(paths.ALARMS_DIR, f"alarms-{today:%Y%m%d}.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시각", "구분", "코드", "내용", "등급"])
        w.writerow([f"{today:%Y-%m-%d} 00:00:01", "발생", "A0-00", _P1, "중대"])
        w.writerow([f"{today:%Y-%m-%d} 00:00:02", "해제", "A0-00", _P1, "중대"])
        w.writerow([f"{today:%Y-%m-%d} 00:00:03", "발생", "A1-07", _P4, "경고"])
    os.makedirs(os.path.dirname(leakcheck.csv_path()), exist_ok=True)
    with open(leakcheck.csv_path(), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(leakcheck.HEAD)
        w.writerow(["2026-11-10 10:00:00", _P1, "0.01", "0.02", "60", "10", "50", _P2, _P3])
    with open(os.path.join(paths.DATA_DIR, "last_operator.json"), "w", encoding="utf-8") as f:
        json.dump({"operator": _P1}, f, ensure_ascii=False)
    return rec, name


@pytest.fixture
def served84(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    from peald.server import create_app, uvicorn_config
    rec, log_name = _seed_untrusted_text()
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": True, "sim_port": free_port(), "sim_speed": 50})
    for m, fs in zip(raw.get("mfc") or [], (1000.0, 500.0, 500.0, 500.0)):
        if m.get("full_scale_sccm") is None:
            m["full_scale_sccm"] = fs
        m["name"], m["gas"] = _P1, _P2
    for h in raw.get("heaters") or []:
        h["name"] = _P1 if h["ch"] % 2 else _P3
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    app = create_app(str(path), single_instance=False)
    state.add_log("warn", "LOG " + _P1 + _P2 + _P3)
    state.startup_notices.append({"level": "warn", "msg": "NOTICE " + _P1 + _P4, "kind": "config"})
    port = free_port()
    server = uvicorn.Server(uvicorn_config(app, "127.0.0.1", port))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        yield port, rec, log_name
    finally:
        server.should_exit = True
        th.join(10)
        state.link = None
        state.runner = None
        state.sim = None


async def test_browser_untrusted_text_is_shown_not_executed(served84):
    """레시피 이름 · 메모 · 블록/스텝 이름, 작업자 · 시료 · 메모, 데이터 로그 메타 · 열 이름 · 칸, 알람 이력, 이벤트 로그 · 알림 ·
    토스트, 설정의 히터 · MFC 이름에 든 HTML 조각이 모든 탭 · 편집기 · 데이터 로그 보기에서 글자로만 보인다(콘솔 오류도 없다)."""
    port, rec, log_name = served84
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

        async def clean(where):
            got = await pg.evaluate("""() => [window.__xss === undefined ? null : window.__xss,
                document.querySelectorAll('img[src="x"], svg[onload], script:not([src])').length]""")
            assert got == [None, 0], f"{where}: 넣은 글이 HTML 로 들어갔다 {got}"

        async def tab(name, ms=600):
            await pg.click(f'button.tab[data-tab="{name}"]')
            await pg.wait_for_timeout(ms)

        for name in ("main", "pid", "recipe", "trend", "datalog", "alarm", "setup"):
            await tab(name)
            await clean("탭 " + name)
        # 알림 · 토스트 — 넣은 글이 든 명령(거절 글에 그 글이 들어간다)
        bad_valves = dict(rec, blocks=[dict(rec["blocks"][0],
                                            steps=[dict(rec["blocks"][0]["steps"][0], valves=[_P1, _P2])])])
        for cmd, body in (("recipe_load", {"name": _P1}), ("recipe_delete", {"name": _P2}),
                          ("recipe_rename", {"name": _P1, "new_name": _P3}), ("recipe_save", {"name": _P1, "recipe": rec}),
                          ("sim_fault", {"key": _P1, "on": True}), ("manual_unlock", {"on": True}),
                          ("manual_valve", {"tag": _P4, "on": True}), ("nope" + _P1, {}),
                          ("recipe_validate", {"recipe": bad_valves, "req": 5})):
            await pg.evaluate("(a) => app.send(a[0], a[1])", [cmd, body])
            await pg.wait_for_timeout(150)
        await clean("토스트")
        # 레시피 탭 — 목록의 레시피를 편집기로
        await tab("recipe")
        for i in range(2):
            items = await pg.query_selector_all("[data-rcopen]")
            assert len(items) == 2, "심은 레시피 두 개가 목록에 없다"
            await items[i].click()
            await pg.wait_for_timeout(500)
            await clean(f"레시피 편집기 {i}")
        assert await pg.evaluate("() => document.querySelector('[data-rcf=\"memo\"]').value") == rec["memo"]
        # 데이터 로그 — 그래프 · 표 · 레시피 사본
        await tab("datalog", 900)
        item = await pg.query_selector(f'[data-dlname="{log_name}"]')
        assert item is not None, "심은 데이터 로그가 목록에 없다"
        await item.click()
        await pg.wait_for_timeout(1200)
        await clean("데이터 로그 그래프")
        for view in ("table", "recipe", "chart"):
            await pg.click(f'[data-dlview="{view}"]')
            await pg.wait_for_timeout(600)
            await clean("데이터 로그 " + view)
        for name in ("alarm", "setup", "main", "pid"):
            await tab(name)
            await clean("다시 " + name)
        assert await pg.evaluate("() => document.body.innerText.indexOf('onerror=window.__xss') >= 0"), \
            "넣은 글이 화면에 글자로 보여야 한다(이스케이프)"
        assert not errors, errors[:5]
    finally:
        await br.close()
        await pw.stop()


_WS_SPY = """(() => { const O = window.WebSocket; window.__ws = [];
  window.WebSocket = function (u, p) { const s = p === undefined ? new O(u) : new O(u, p); window.__ws.push(s); return s; };
  window.WebSocket.prototype = O.prototype;
  ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(k => { window.WebSocket[k] = O[k]; });
})();"""


async def test_browser_ws_reconnect_restores_screen_and_clears_busy(served84):
    """화면의 WebSocket 이 끊겼다 다시 붙는다 — 끊긴 동안은 '서버 끊김' · 진행 중 잠금을 지우고, 다시 붙으면 값과 조작이
    돌아온다. 소켓은 하나만 새로 만들고(겹쳐 다시 붙지 않는다) 콘솔 오류가 없다."""
    port, _rec, _log = served84
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
        await pg.add_init_script(_WS_SPY)
        await pg.goto(f"http://127.0.0.1:{port}/")
        await pg.add_style_tag(content="#alarmModal { display: none !important; }")
        await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
        assert await pg.evaluate("() => window.__ws.length") == 1
        await pg.evaluate("() => core.busy.start('cmd:pump_start', 123456)")       # 답을 기다리는 명령이 있는 채로
        assert await pg.evaluate("() => core.busy.has('cmd:pump_start')")
        await pg.evaluate("() => window.__ws[0].close()")
        await pg.wait_for_function("() => document.documentElement.classList.contains('offline')", timeout=5000)
        assert not await pg.evaluate("() => core.busy.has('cmd:pump_start')"), "끊겼는데 진행 중 잠금이 남았다"
        assert not await pg.evaluate("() => core.plcOk()")
        assert await pg.evaluate("() => app.send('alarm_ack')") == 0               # 끊긴 동안은 보내지 않는다
        await pg.wait_for_function(
            "() => !document.documentElement.classList.contains('offline') && core.plcOk()", timeout=10000)
        await pg.wait_for_timeout(2500)                                            # 다시 붙은 뒤 더 만들지 않는다
        assert await pg.evaluate("() => window.__ws.length") == 2, "다시 붙으면서 소켓을 여러 개 만들었다"
        assert await pg.evaluate("() => core.sendBusy('cmd:alarm_ack', 'alarm_ack') > 0")
        await pg.wait_for_function("() => !core.busy.has('cmd:alarm_ack')", timeout=5000)   # done 이 와서 잠금이 풀린다
        for name in ("pid", "alarm", "main"):
            await pg.click(f'button.tab[data-tab="{name}"]')
            await pg.wait_for_timeout(300)
        assert not errors, errors[:5]
    finally:
        await br.close()
        await pw.stop()
