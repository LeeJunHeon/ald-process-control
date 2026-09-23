"""원격 접속 명령 거절 테스트.

★ 이 검사가 이 단계 보안의 핵심이다. host 를 0.0.0.0 으로 열어 원격에서 화면을 보더라도
  조작 명령은 루프백 접속만 받아야 한다. 화면 쪽 잠금은 개발자 도구로 풀 수 있으므로
  서버가 막는지를 직접 확인한다.
"""
import asyncio
import types

import pytest

import commands
import connection
from connection import manager, is_local


class FakeWS:
    """WebSocket 대역. send_text 로 나간 메시지를 모아 둔다."""

    def __init__(self, host):
        self.client = types.SimpleNamespace(host=host)
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)

    def notices(self):
        return [t for t in self.sent if '"notice"' in t]


@pytest.fixture
def wired(cfg1, monkeypatch):
    """provider 대신 '불리면 기록만 하는' 대역을 꽂는다."""
    from state import state
    called = []

    class Spy:
        is_demo = True

        def __getattr__(self, name):
            def fn(*a, **k):
                called.append(name)
                return True, ""
            return fn

        def alarm_history(self):
            return []

    state.cfg = cfg1
    state.provider = Spy()
    state.snap = {"process": {"mode": "idle"}}
    manager.active.clear()
    yield called
    manager.active.clear()


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize("host,local", [
    ("127.0.0.1", True), ("::1", True), ("localhost", True),
    ("192.168.10.55", False), ("10.0.0.2", False), ("", False), (None, False),
])
def test_is_local_classification(host, local):
    """판정 불가(host 없음)는 원격으로 본다 — 안전한 쪽으로 틀린다."""
    assert is_local(FakeWS(host)) is local


CONTROL_CMDS = ["valve_toggle", "pump", "vent", "all_close", "heater_apply",
                "process_start", "process_pause", "process_stop_after_cycle",
                "process_abort", "alarm_ack", "alarm_reset",
                "recipe_save", "recipe_delete", "settings_save", "exit"]


@pytest.mark.parametrize("cmd", CONTROL_CMDS)
def test_remote_control_commands_rejected(wired, cmd):
    ws = FakeWS("192.168.10.55")
    manager.active[ws] = {"local": False}
    run(commands.handle_command({"cmd": cmd}, ws))
    assert wired == [], f"원격에서 {cmd} 가 provider 까지 도달했다"
    assert any("보기 전용" in n for n in ws.notices())


@pytest.mark.parametrize("cmd", ["recipe_list", "recipe_preview"])
def test_remote_read_only_commands_allowed(wired, cmd):
    ws = FakeWS("192.168.10.55")
    manager.active[ws] = {"local": False}
    run(commands.handle_command({"cmd": cmd, "recipe": {}}, ws))
    assert not any("보기 전용" in n for n in ws.notices())


def test_local_control_command_reaches_provider(wired):
    ws = FakeWS("127.0.0.1")
    manager.active[ws] = {"local": True}
    run(commands.handle_command({"cmd": "pump"}, ws))
    assert "pump" in wired


def test_manual_commands_blocked_while_running(wired):
    """공정 중 수동 조작은 로컬이라도 서버가 거절한다 —
    공정이 쥐고 있는 밸브를 사람이 동시에 건드리면 안 된다."""
    from state import state
    state.snap = {"process": {"mode": "running"}}
    ws = FakeWS("127.0.0.1")
    manager.active[ws] = {"local": True}
    for cmd in ("valve_toggle", "pump", "vent", "all_close"):
        run(commands.handle_command({"cmd": cmd, "tag": "P1-ALD"}, ws))
    assert wired == []
    assert len([n for n in ws.notices() if "공정 중" in n]) == 4


def test_unknown_command_is_reported(wired):
    ws = FakeWS("127.0.0.1")
    manager.active[ws] = {"local": True}
    run(commands.handle_command({"cmd": "drop_tables"}, ws))
    assert any("알 수 없는 명령" in n for n in ws.notices())
