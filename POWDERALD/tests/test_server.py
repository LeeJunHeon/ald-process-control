"""서버 스모크 · 원격 보기 전용 · 단일 실행 · 설정 검증.

기존 프로그램에서 검증된 원칙을 새 구조에서도 그대로 지키는지 확인한다.
"""
import os
import sys
import asyncio
import types

import pytest

pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient    # noqa: E402

from powderald import addresses as A             # noqa: E402
from powderald import device as DEV              # noqa: E402
from powderald import paths                      # noqa: E402


def make_app():
    # single_instance=False: 실제 프로그램이 떠 있어도 테스트가 돌아야 한다.
    # 뮤텍스 동작 자체는 test_single_instance 가 따로 검증한다.
    from powderald.server import create_app
    return create_app("", single_instance=False)


def client(app):
    """★ TestClient 의 기본 client 는 ("testclient", 50000) 이라 루프백으로 판정되지 않는다.
    현장에서는 창·브라우저가 127.0.0.1 로 붙으므로 그 조건을 명시해 만든다."""
    return TestClient(app, client=("127.0.0.1", 50000))


STATE_KEYS = ["device", "structure", "config", "unconfirmed", "notices",
              "alarm_history", "logs", "access", "live"]


def test_health_and_first_state():
    app = make_app()
    with client(app) as c:
        h = c.get("/health").json()
        assert h["ok"] is True and h["device"] == DEV.KEY

        with c.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state"
            for k in STATE_KEYS:
                assert k in msg, f"state 에 {k} 가 없다"
            dev = msg["device"]
            assert dev["name"] == DEV.NAME
            assert dev["theme"] == DEV.THEME
            assert dev["accent"] == DEV.ACCENT
            assert msg["access"]["local"] is True
            st = msg["structure"]
            assert len(st["valves"]) == len(DEV.VALVES)
            assert len(st["heaters"]) == DEV.HEATER_COUNT
            assert len(st["mfc"]) == DEV.MFC_COUNT


def test_live_has_no_values_when_plc_down():
    """★ PLC 가 없으면 값을 지어내지 않는다 — 전부 None(화면에서 '—')."""
    app = make_app()
    with client(app) as c:
        with c.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            live = msg["live"]
            if not live["plc"]["connected"]:
                for k in ("state", "valves", "pressure", "heaters", "mfc"):
                    assert live[k] is None, k


def test_trend_endpoint_clamps_range():
    app = make_app()
    with client(app) as c:
        js = c.get("/api/trend?sec=999999").json()
        assert js["sec"] == 3600
        for k in ("slow", "fast"):
            assert isinstance(js[k], list)


def test_index_and_assets_served():
    app = make_app()
    with client(app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/css/tokens.css").status_code == 200
        assert c.get("/js/core.js").status_code == 200


def test_example_config_is_flagged():
    """config.json 이 없으면 예시 설정으로 기동하고 화면에 경고를 띄운다."""
    app = make_app()
    with client(app) as c:
        with c.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["config"]["source"] == "example"
            assert any("예시 설정" in n["msg"] for n in msg["notices"])


def test_unconfirmed_conversions_are_reported():
    app = make_app()
    with client(app) as c:
        with c.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert "CVG 압력" in msg["unconfirmed"]


# ===================== 원격 = 보기 전용 =====================
class FakeWS:
    def __init__(self, host):
        self.client = types.SimpleNamespace(host=host)
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)

    def notices(self):
        return [t for t in self.sent if '"notice"' in t]


@pytest.mark.parametrize("host,local", [
    ("127.0.0.1", True), ("::1", True), ("localhost", True),
    ("192.168.10.55", False), ("10.0.0.2", False), ("", False), (None, False),
])
def test_is_local_classification(host, local):
    """판정 불가(host 없음)는 원격으로 본다 — 안전한 쪽으로 틀린다."""
    from powderald.connection import is_local
    assert is_local(FakeWS(host)) is local


CONTROL_CMDS = ["pump_start", "pump_stop", "vent", "all_close",
                "alarm_ack", "alarm_reset", "sim_fault", "exit"]


@pytest.mark.parametrize("cmd", CONTROL_CMDS)
def test_remote_control_commands_rejected(cmd, monkeypatch):
    from powderald import commands
    from powderald.connection import manager
    from powderald.state import state

    sent = []
    monkeypatch.setattr(state, "link", types.SimpleNamespace(
        connected=True, status=[0] * A.STATUS_COUNT, addr_text="x",
        send_command=lambda *a, **k: sent.append(a)))
    ws = FakeWS("192.168.10.55")
    manager.active[ws] = {"local": False}
    try:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            commands.handle_command({"cmd": cmd}, ws))
    finally:
        manager.active.pop(ws, None)
    assert not sent, f"원격에서 {cmd} 가 PLC 까지 도달했다"
    assert any("보기 전용" in n for n in ws.notices())


def test_local_command_blocked_when_plc_down(monkeypatch):
    """PLC 가 끊긴 동안의 명령은 보내지 않고 이유를 알린다."""
    from powderald import commands
    from powderald.connection import manager
    from powderald.state import state

    monkeypatch.setattr(state, "link", types.SimpleNamespace(
        connected=False, status=[0] * A.STATUS_COUNT, addr_text="x"))
    ws = FakeWS("127.0.0.1")
    manager.active[ws] = {"local": True}
    try:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            commands.handle_command({"cmd": "pump_start"}, ws))
    finally:
        manager.active.pop(ws, None)
    assert any("연결되어 있지 않습니다" in n for n in ws.notices())


# ===================== 단일 실행 =====================
@pytest.mark.skipif(sys.platform != "win32", reason="뮤텍스는 Windows 전용")
def test_single_instance_blocks_same_device():
    import ctypes
    from powderald import window
    window._MUTEX_HANDLE = None
    assert window.acquire_single_instance() is True
    first = window._MUTEX_HANDLE
    window._MUTEX_HANDLE = None          # 다른 프로세스인 척
    assert window.acquire_single_instance() is False
    ctypes.windll.kernel32.CloseHandle(first)
    window._MUTEX_HANDLE = None


@pytest.mark.skipif(sys.platform != "win32", reason="뮤텍스는 Windows 전용")
def test_reentrant_acquire_is_safe():
    """창 경로와 lifespan 이 둘 다 부른다 — 두 번째 호출이 자기 자신에 걸리면 안 된다."""
    import ctypes
    from powderald import window
    window._MUTEX_HANDLE = None
    assert window.acquire_single_instance() is True
    assert window.acquire_single_instance() is True
    ctypes.windll.kernel32.CloseHandle(window._MUTEX_HANDLE)
    window._MUTEX_HANDLE = None


def test_mutex_name_is_device_specific():
    """두 프로그램의 뮤텍스 이름이 같으면 한쪽이 아예 뜨지 않는다."""
    from powderald import window
    assert DEV.MUTEX_NAME.endswith(".Control")
    assert DEV.KEY.upper() in DEV.MUTEX_NAME.upper()


def test_half_screen_split_has_no_gap_or_overlap(monkeypatch):
    """홀수 폭에서도 왼쪽+오른쪽이 정확히 작업 영역을 채워야 한다."""
    from powderald import window
    for w in (1920, 1921, 2559, 3440):
        monkeypatch.setattr(window, "work_area", lambda w=w: (0, 0, w, 1032))
        lx, ly, lw, lh = window.half_rect("left")
        rx, ry, rw, rh = window.half_rect("right")
        assert lx == 0 and lx + lw == rx, f"틈/겹침 (폭 {w})"
        assert rx + rw == w, f"오른쪽 끝이 화면 끝과 다르다 (폭 {w})"
        assert lh == rh == 1032


# ===================== 설정 검증 =====================
def test_config_validation_catches_problems(cfg):
    import copy
    from powderald import config as C

    bad = copy.deepcopy(cfg)
    bad["server"]["port"] = 0
    assert any("server.port" in m for _, m in C.validate(bad))

    bad = copy.deepcopy(cfg)
    bad["plc"]["sim_port"] = bad["server"]["port"]
    assert any("포트를 나눠야" in m for _, m in C.validate(bad))

    bad = copy.deepcopy(cfg)
    bad["pressure"]["cvg"]["decades_per_volt"] = -1      # 단조 감소
    assert any("단조 증가" in m for _, m in C.validate(bad))

    # 과온 한계가 정해진 채널을 골라 시험한다(한계가 null 인 채널은 비교 자체가 안 된다).
    bad = copy.deepcopy(cfg)
    idx = next(i for i, h in enumerate(bad["heaters"]) if h.get("max_c") is not None)
    bad["heaters"][idx]["default_sv"] = 9999
    assert any("과온 한계" in m and "높습니다" in m for _, m in C.validate(bad))

    # 한계가 null 이면 경고를 낸다(PLC 한계에 0 → 그 채널을 막는다)
    assert any("과온 한계가 정해지지 않았" in m
               for _, m in C.validate(cfg)) or all(
        h.get("max_c") is not None for h in cfg["heaters"] if h["enabled"])

    bad = copy.deepcopy(cfg)
    bad["window"]["side"] = "middle"
    assert any("left 또는 right" in m for _, m in C.validate(bad))


def test_data_dirs_are_under_this_program(cfg):
    """★ 두 프로그램이 데이터 폴더를 공유하면 로그·알람 이력이 섞인다."""
    assert paths.LOGS_DIR.startswith(paths.DATA_ROOT)
    assert os.path.basename(paths.DATA_DIR) == "data"
    assert DEV.LOG_PREFIX in ("PEALD", "POWDERALD")
