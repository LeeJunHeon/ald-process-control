"""v0.4.16 — 포트 권한 오류 문구 · 응답 없음 직후 표시 · 알림 수준 · selftest 빈 포트. 브라우저 시험은 없다."""
import socket
import asyncio
import types

from powderald import addresses as A
from powderald import commands
from powderald import device as DEV
from powderald import process
from powderald import window
from powderald.state import state

from test_v0411 import _runner, _s
from test_v0414 import _recipe


# ===================== 1 포트 권한 오류 문구 =====================
def test_1_permission_error_text_comes_before_address_text():
    posix = window.port_busy_text("127.0.0.1", 80, "PermissionError: [Errno 13] Permission denied")
    win = window.port_busy_text("127.0.0.1", 80, window.BUSY_MARK + "PermissionError: [WinError 10013] 액세스 권한")
    for t in (posix, win):
        assert "권한이 없거나 예약된" in t and "server.host 를 고치세요" not in t, t
    other = window.port_busy_text("192.0.2.1", 8201, "OSError: [Errno 99] Cannot assign requested address")
    assert "server.host 를 고치세요" in other and "권한" not in other


# ===================== 3 응답 없음 직후 — 시작 태스크가 아직 안 끝나도 =====================
async def test_3_adopting_while_start_task_not_done(cfg):
    r, _tbl = _runner(cfg)
    r.recipe, r.recipe_name = _recipe(), "앞것"
    r._task = asyncio.get_running_loop().create_future()      # 시작 태스크가 아직 안 끝났다(busy)
    state.link = types.SimpleNamespace(connected=True, status=_s(4, st=A.STATE_RUN))
    try:
        assert r.busy and r.phase == process.IDLE
        out = r.progress()
        assert out["adopting"] is True and out["recipe"] == process.ADOPTING_NAME
        r.phase = process.STARTING
        assert r.progress()["adopting"] is False                 # 시작 절차 중에는 아니다
    finally:
        r._task.cancel()
        state.link = None


# ===================== 5 시작 명령을 못 보냈을 때 알림 수준 =====================
async def test_5_not_sent_notice_is_err(monkeypatch):
    got, notices = {}, []

    class Fake:
        busy = False

        def begin(self, _log, _notice, notify):
            got["notify"] = notify
            return True, "시작 절차를 시작합니다"

    async def notice(msg, lv="info", ws=None):
        notices.append((lv, msg))

    async def nothing(*_a, **_k):
        return None
    monkeypatch.setattr(state, "runner", Fake())
    monkeypatch.setattr(commands, "push_notice", notice)
    monkeypatch.setattr(commands, "push_state", nothing)
    await commands._cmd_process_start({}, None)
    await got["notify"](f"{process.START_NOT_SENT} — PLC 연결이 없습니다", False)
    await got["notify"]("시작 결과 모름 — PLC 상태로 확인합니다 (PLC 응답 없음)", False)
    await got["notify"]("공정을 시작했습니다", True)
    assert [lv for lv, _m in notices[1:]] == ["err", "warn", "ok"], notices


# ===================== 6 selftest 빈 포트 =====================
def test_6_selftest_passes_when_default_plus_50_ports_are_busy():
    import run as entry
    held = []
    for p in range(DEV.DEFAULT_PORT + 50, DEV.DEFAULT_PORT + 60):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.listen(1)
            held.append(s)
        except OSError:
            s.close()                                           # 이미 쓰이는 포트 — 그대로 둔다
    try:
        assert entry.selftest("") == 0
    finally:
        for s in held:
            s.close()
