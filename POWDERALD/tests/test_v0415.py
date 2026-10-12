"""v0.4.15 — 다른 장비 공정 표시 · 포트 권한 오류 · 시작 명령 못 보냄 문구 · 이어받은 뒤 경과 · 서버 시작 실패 이유 ·
selftest 프록시. 브라우저 시험은 없다.
"""
import sys
import time
import types
import errno

import pytest

from powderald import addresses as A
from powderald import paths
from powderald import process
from powderald import server
from powderald import window
from powderald.state import state

from test_v0411 import _runner, _s
from test_v0414 import _recipe


def _link(s, **kw):
    return types.SimpleNamespace(connected=True, status=s, **kw)


async def _nolog(*_a):
    return None


# ===================== C1 · B3 시작 명령 결과 None =====================
async def _start_until_command(cfg, monkeypatch, send_text):
    r, tbl = _runner(cfg)
    r.recipe, r.recipe_name = _recipe(), "새것"
    state.cfg.setdefault("process", {})["base_stable_s"] = 0
    monkeypatch.setattr(r, "can_start", lambda: (True, [], []))
    monkeypatch.setattr(r, "_wait_broken", lambda: "")
    monkeypatch.setattr(process.R, "validate", lambda *_a: {"errors": []})

    async def same(*_a):
        return ""
    monkeypatch.setattr(r, "_plc_table_differs", same)
    s = _s(0, st=A.STATE_IDLE)
    s[A.D_INTERLOCK] = 1 << A.ILK_VACUUM

    async def upload(*_a):
        return True, "올림"

    async def send(_code):
        return None, send_text
    state.link = _link(s, upload_recipe=upload, send_command=send, _cmd_no=7)
    logs = []

    async def log(m, lv="info"):
        logs.append((lv, m))
    try:
        ok, why = await r._start_flow(log, _nolog)
    finally:
        state.link = None
    return r, ok, why, logs


async def test_c1_no_reply_is_unknown_result(cfg, monkeypatch):
    r, ok, why, logs = await _start_until_command(cfg, monkeypatch, "PLC 응답 없음 (명령이 처리됐는지 확인하세요)")
    assert not ok and why.startswith("시작 결과 모름 — PLC 상태로 확인합니다")
    assert ("warn", why) in logs and not any("거절" in m for _lv, m in logs)


async def test_c1_not_sent_says_so(cfg, monkeypatch):
    r, ok, why, logs = await _start_until_command(cfg, monkeypatch, "PLC 연결이 없습니다")
    assert not ok and why == "공정 시작 명령을 보내지 못했습니다 — PLC 연결이 없습니다"
    assert ("err", why) in logs and not any("결과 모름" in m for _lv, m in logs)
    assert not r.active_run and r.run is None                # 스냅샷은 되돌린다


# ===================== C2 · B5 창 — lifespan 실패 이유 =====================
def test_c2_window_shows_lifespan_failure_reason_quickly(monkeypatch):
    shown = []
    monkeypatch.setattr(window, "acquire_single_instance", lambda: True)
    monkeypatch.setattr(window, "set_app_user_model_id", lambda: None)
    monkeypatch.setattr(window, "_msgbox", shown.append)
    monkeypatch.setattr(window, "_SERVER_ERROR", "")
    monkeypatch.setattr(server, "START_ERROR", "")
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))     # 가짜 — 창까지 가지 않는다
    app = server.create_app("", single_instance=False, start_io=False)
    calls = []

    def writable():
        calls.append(1)
        if len(calls) == 1:
            return True, ""                                 # window.run 의 확인은 통과
        raise RuntimeError("일부러 — 데이터 폴더")            # lifespan 시작에서 실패
    monkeypatch.setattr(paths, "check_writable", writable)
    t0 = time.monotonic()
    window.run(app, "127.0.0.1", _free())
    took = time.monotonic() - t0
    assert took < 10, took                                  # 20 s 를 기다리지 않는다
    assert shown and "시작 중 실패" in shown[0] and "일부러" in shown[0], shown


def _free():
    from conftest import free_port
    return free_port()


# ===================== C3 selftest 와 죽은 HTTP_PROXY =====================
def test_c3_selftest_ignores_dead_proxy(monkeypatch):
    import run as entry
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    t0 = time.monotonic()
    assert entry.selftest("") == 0
    assert time.monotonic() - t0 < 10


# ===================== C4 · B1 다른 장비 공정 =====================
def test_c4_foreign_process_shown_and_not_judged(cfg):
    r, _tbl = _runner(cfg)
    r.recipe, r.recipe_name = _recipe(), "고른 것"
    logs = []
    state.link = _link(_s(4, st=A.STATE_RUN), id_block_text=lambda: "다른 장비의 PLC")
    try:
        out = r.progress()
        assert out["recipe"] == process.FOREIGN_NAME and out["foreign"] is True and out["adopting"] is False
        assert out["number"] is None and "remaining_ms" not in out
        r.tick(lambda lv, m: logs.append(m))
        assert r._was_running is False
        state.link.status = _s(6, st=A.STATE_IDLE, blk=3)    # 그 장비의 공정이 끝났다
        r.tick(lambda lv, m: logs.append(m))
        assert logs == [] and r.last_result == ""            # 이 장비 이벤트 로그에 적지 않는다
    finally:
        state.link = None


# ===================== C4 · B2 POSIX EACCES =====================
def test_c4_posix_eacces_returns_at_once_with_permission_text(monkeypatch):
    def denied(*_a):
        raise PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(window, "bind_sockets", denied)
    t0 = time.monotonic()
    socks, why = window.bind_port("127.0.0.1", 80, total_s=3, step_s=0.2)
    assert not socks and not why.startswith(window.BUSY_MARK) and time.monotonic() - t0 < 1.0
    text = window.port_busy_text("127.0.0.1", 80, why)
    assert "권한이 없거나 예약된" in text and "excludedportrange" in text


# ===================== C4 · B4 이어받은 뒤 경과 =====================
async def test_c4_progress_marks_adopted(cfg):
    r, tbl = _runner(cfg)

    async def read():
        return list(tbl["words"])
    state.link = _link(_s(4, st=A.STATE_RUN), read_recipe_area=read)
    try:
        assert r.progress()["adopted"] is False
        assert await r.adopt_running(_nolog)
        assert r.progress()["adopted"] is True
    finally:
        state.link = None


@pytest.mark.parametrize("code", [errno.EADDRINUSE])
def test_c4_posix_eaddrinuse_still_busy(code):
    e = OSError(code, "in use")
    assert window._busy(e)
