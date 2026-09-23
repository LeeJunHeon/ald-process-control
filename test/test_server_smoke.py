"""서버 스모크 — 챔버1·2 각각 헤드리스 기동 → /health → /ws 첫 state 메시지 필수 키.

두 챔버가 각자의 데이터 폴더를 쓰는지도 함께 확인한다(레시피가 섞이면 사고가 난다).
"""
import json
import os

import pytest

pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient

import paths
from conftest import CFG1, CFG2

STATE_KEYS = ["chamber", "conn", "demo", "process", "lines", "chamber_io",
              "gauges", "heaters", "interlocks", "alarms", "recipes", "access"]


def make(config_path):
    # single_instance=False: 개발 PC 에서 실제 프로그램이 떠 있어도 테스트가 돌아야 한다.
    # 뮤텍스 동작 자체는 test_single_instance.py 가 따로 검증한다.
    import server
    return server.create_app(config_path, single_instance=False)


def client(app):
    """★ TestClient 의 기본 client 는 ("testclient", 50000) 이라 루프백으로 판정되지 않는다.
    현장에서는 창·브라우저가 127.0.0.1 로 붙으므로 그 조건을 명시해 만든다."""
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.mark.parametrize("cfg_path,chamber,theme,port", [
    (CFG1, "ald1", "light", 8001),
    (CFG2, "ald2", "dark", 8002),
])
def test_boot_health_and_first_state(cfg_path, chamber, theme, port):
    app = make(cfg_path)
    with client(app) as c:
        h = c.get("/health").json()
        assert h["ok"] is True and h["chamber"] == chamber

        with c.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state"
            for k in STATE_KEYS:
                assert k in msg, f"state 에 {k} 가 없다"
            assert msg["chamber"]["id"] == chamber
            assert msg["chamber"]["theme"] == theme
            assert msg["demo"]["enabled"] is True
            assert msg["access"]["local"] is True          # TestClient 는 루프백이다
            assert len(msg["lines"]) == 7
            assert msg["recipes"], "데모 모드면 샘플 레시피가 있어야 한다"

        # 데이터 폴더가 챔버별로 갈렸는지
        assert paths.CHAMBER_ID == chamber
        assert os.path.isdir(paths.RECIPES_DIR)
        assert chamber in paths.RECIPES_DIR.replace("\\", "/")


def test_trend_endpoint_clamps_range():
    app = make(CFG1)
    with client(app) as c:
        js = c.get("/api/trend?sec=999999").json()
        assert js["sec"] == 3600
        for k in ("slow", "fast", "pulses"):
            assert isinstance(js[k], list)


def test_running_scenario_actually_runs():
    """chamber1 은 running 시나리오 — 첫 state 에 진행 중인 공정이 실려야 한다."""
    app = make(CFG1)
    with client(app) as c:
        with c.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            p = msg["process"]
            assert p["mode"] == "running"
            assert p["cycle"] >= 1 and p["cycles"] == 300
            assert p["recipe"]
            # 항상 열림 밸브는 공정 중 열려 있어야 한다.
            live = msg["live"]["valves"]
            assert live["PN-ALD"] and live["RN-ALD"]


def test_idle_scenario_is_idle():
    """chamber2 는 idle 시나리오 — ALD 밸브가 닫혀 있고 펌프가 돈다."""
    app = make(CFG2)
    with client(app) as c:
        with c.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["process"]["mode"] == "idle"
            live = msg["live"]
            assert live["io"]["dry_pump"] is True
            assert live["io"]["rv"] is True
            assert live["gauges"]["baratron"] < 5e-2
            # ★ 기동 직후 대기는 '아무것도 흘리지 않는' 상태다 — ALD 밸브 전부 닫힘, 유량 0.
            #   (공정을 마친 뒤의 대기는 N2 퍼지를 유지한다 — 경위가 달라 상태도 다르다)
            for lid in ("PN", "P1", "P2", "RN", "R1", "R2"):
                assert live["valves"][lid + "-ALD"] is False, lid
                assert live["mfc"][lid]["sv"] == 0.0, lid


def test_two_chambers_use_separate_dirs():
    make(CFG1)
    d1 = paths.RECIPES_DIR
    make(CFG2)
    d2 = paths.RECIPES_DIR
    assert d1 != d2


def test_command_over_websocket_reaches_provider():
    app = make(CFG2)
    with client(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()                       # 첫 state
            ws.send_json({"cmd": "vent"})
            # notice 가 올 때까지 읽는다(중간에 telemetry/log 가 섞인다).
            for _ in range(40):
                m = ws.receive_json()
                if m["type"] == "notice" and "벤트" in m["msg"]:
                    return
            pytest.fail("벤트 명령의 응답이 오지 않았다")


def test_testclient_default_is_treated_as_remote():
    """★ 판정 불가한 주소는 원격으로 본다 — 안전한 쪽으로 틀린다."""
    app = make(CFG2)
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["access"]["local"] is False
