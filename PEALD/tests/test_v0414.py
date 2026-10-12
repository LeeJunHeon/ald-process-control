"""v0.4.14 — 띠 넘침 안내 · 띠 위 포인터 · 띠 화소 맞춤 · 표 없이 이어받은 사이클 후 정지 · 다른 장비 이어받기 막기 ·
이어받는 중 표시 · 이어받기 상태 순서 · 시작 직전 앞 공정 끝 · 서버 시작 실패 · 포트 오류 이유 · 빈 알람 CSV.

★ 화면의 순수 함수(chart.js)는 node 로 부른다(node 가 없으면 건너뜀). 브라우저 시험은 없다.
"""
import os
import csv
import json
import time
import types
import socket
import asyncio
import datetime

import pytest

from peald import addresses as A
from peald import device as DEV
from peald import logger
from peald import paths
from peald import process
from peald import recipe as R
from peald import server
from peald import window
from peald.state import state

from conftest import free_port
from test_v0411 import _runner, _s
from test_v0412 import _chart


def _link(s, read=None, **kw):
    return types.SimpleNamespace(connected=True, status=s, read_recipe_area=read, **kw)


async def _nolog(*_a):
    return None


# ===================== B1 띠만 다시 쓸 때 넘침 안내 자리도 지운다 =====================
def test_b1_band_redraw_clears_overflow_note():
    out = _chart(r'''
      let T = 0, mark = ''; const c2 = HistChart(canvas, { now: () => T });
      global.fmt.num = v => mark + Number(v).toFixed(1);     // 같은 점 · 다른 글자(화소에 기대지 않게)
      const S = () => Array.from({ length: 24 }, (_, i) => ({ label: 'CH' + i, unit: '', color: '#888',
                       pts: [[0, i, i, i], [1000, i, i, i]] }));
      c2.set({ series: S(), x0: 0, x1: 1000, gap: 5000, tol: 1e9 });     // 24 줄 — 띠가 넘친다
      const G = c2.debug().geom, rects = [];
      ctx.clearRect = (x, y, w, h) => rects.push([x, y, w, h]);
      T = 1500; mark = '~'; c2.set({ series: S(), x0: 0, x1: 1000, gap: 5000, tol: 1e9 });   // 글자만 바뀜
      console.log(JSON.stringify({ rects, G }));
    ''')
    G = out["G"]
    assert len(out["rects"]) == 2, out["rects"]
    note = out["rects"][1]
    assert note[0] > G["x1"] + 20 and abs(note[1] - (G["y1"] + 2)) < 1e-9 and note[0] + note[2] <= G["W"] + 1e-9


# ===================== B2 포인터가 띠 · 축 위에 있으면 =====================
def test_b2_pointer_over_band_keeps_staleness_in_signature():
    out = _chart(r'''
      const pts = []; for (let i = 0; i <= 3600; i++) pts.push([i * 1000, 25, 25, 25]);
      const S = () => [{ label: 'CH1', unit: '℃', color: '#888', pts }];
      const x1 = 3600 * 1000 + 137;
      c.set({ series: S(), x0: x1 - 3600 * 1000, x1, gap: 5000, tol: 1000 });
      const G = c.debug().geom;
      c.hoverAt(G.x1 + 60, (G.y0 + G.y1) / 2);              // 띠 위 — 커서는 안 그림
      const n0 = draws;
      c.set({ series: S(), x0: x1 + 1200 - 3600 * 1000, x1: x1 + 1200, gap: 5000, tol: 1000 });
      console.log(JSON.stringify({ n0, n1: draws, stale: c.debug().rows[0].stale }));
    ''')
    assert out["n1"] == out["n0"] + 1 and out["stale"] is True, out


# ===================== B3 띠 사각형은 기기 화소에 =====================
def test_b3_band_rects_snap_to_device_pixels():
    out = _chart(r'''
      global.devicePixelRatio = 1.25;
      const xs = [];
      ctx.fillRect = (x, y, w, h) => { if (ctx.globalAlpha < 1 && ctx.globalAlpha > 0.15) xs.push([x, w]); };
      const pts = []; for (let i = 0; i < 3600 * 5; i++) { const v = i / 1800 + (i % 7) * 0.03; pts.push([i * 200 + 37, v, v, v]); }
      c.set({ series: [{ label: 'x', unit: '', color: '#888', pts }], x0: 0, x1: 3600 * 1000, gap: 5000 });
      console.log(JSON.stringify({ n: xs.length, bad: xs.filter(([x, w]) => Math.abs(x * 1.25 - Math.round(x * 1.25)) > 1e-6
                                                             || Math.abs(w - 0.8) > 1e-9).length }));
    ''')
    assert out["n"] > 100 and out["bad"] == 0, out


# ===================== C1 이어받은 공정의 사이클 후 정지 =====================
async def test_c1_cycle_stop_with_plc_number_table(cfg):
    r, tbl = _runner(cfg)                                   # 블록 2 개 · 각 반복 3

    async def read():
        return list(tbl["words"])
    state.link = _link(_s(4, st=A.STATE_RUN), read)
    try:
        assert await r.adopt_running(_nolog)
        assert r.active_recipe is None and r.active_table.get("words")      # '(PLC 번호 N)' — 로컬 레시피 없음
        assert r.end_result(_s(6, blk=2)) == "사이클 후 정지 (블록 2 · 사이클 1/3)"
        s = _s(6, blk=2)
        s[A.D_SEQ_BLOCK_PASS] = 3
        assert r.end_result(s) == "정상 종료 (사이클 후 정지와 겹침)"
        assert r.end_result(_s(6, blk=3)) == "정상 종료"
    finally:
        state.link = None


async def test_c1_cycle_stop_without_table_uses_stopping_state(cfg, monkeypatch):
    monkeypatch.setattr(process, "ADOPT_READ_GAP_S", 0.0)
    r, _tbl = _runner(cfg)

    async def read():
        return None
    state.link = _link(_s(4, st=A.STATE_RUN), read)
    try:
        assert await r.adopt_running(_nolog) and r.active_table is None
        assert r.end_result(_s(6, blk=1)) == "정상 종료"       # 예약을 못 봤다
        state.link.status = _s(4, st=A.STATE_STOPPING)
        r.tick(lambda *_a: None)                             # 공정 중 '사이클 후 정지 예약'을 봤다
        assert r.end_result(_s(6, blk=1)) == "사이클 후 정지 (블록 1 · 사이클 1 · 표 모름)"
    finally:
        state.link = None


# ===================== C2 장비 ID 막힘 =====================
async def test_c2_no_adopt_when_device_id_blocked(cfg):
    r, _tbl = _runner(cfg)
    state.link = _link(_s(4, st=A.STATE_RUN), None, id_block_text=lambda: "다른 장비의 PLC")
    try:
        assert not r.adopt_wanted()
        assert await r.adopt_running(_nolog) is False and not r.active_run
        state.link.id_block_text = lambda: ""
        assert r.adopt_wanted()
    finally:
        state.link = None


# ===================== C3 이어받는 중 표시 =====================
def test_c3_progress_shows_adopting_without_remaining(cfg):
    r, _tbl = _runner(cfg)
    rec = R.empty_recipe("고른 것")
    rec["blocks"] = [R.empty_block("b")]
    r.recipe, r.recipe_name = rec, "고른 것"
    state.link = _link(_s(4, st=A.STATE_RUN))
    try:
        out = r.progress()
        assert out["recipe"] == process.ADOPTING_NAME and out["adopting"] is True
        assert "remaining_ms" not in out and out["number"] is None
    finally:
        state.link = None


# ===================== C4 이어받기 — 상태 먼저 · 로그 뒤 =====================
async def test_c4_state_set_before_log_and_cleared(cfg):
    r, tbl = _runner(cfg)
    r.last_result, r._rf_warned, r._rf_watch = "앞 결과", {(1, 1)}, {(1, 1): True}
    seen = []

    async def read():
        return list(tbl["words"])

    async def log(_m, *_a):
        seen.append((r.active_run, r._abort_sent))
        r.abort_begin()                                     # 로그를 보내는 사이 운전자 중단이 와도
        r.abort_result(True)
        await asyncio.sleep(0)
    state.link = _link(_s(4, st=A.STATE_RUN), read)
    try:
        assert await r.adopt_running(log)
        assert seen == [(True, False)]                      # 로그 때는 이미 이어받은 상태
        assert r._abort_sent is True, "로그 사이의 중단을 잃지 않는다"
        assert r.last_result == "" and r._rf_warned == set() and r._rf_watch is None
        msgs = []
        r._finish(_s(6, blk=3), time.time() + 61, lambda lv, m: msgs.append(m))
        assert "이어받은 뒤 " in msgs[0] and "걸린 시간" not in msgs[0], msgs
    finally:
        state.link = None


# ===================== C6 중단 직후 새 시작 =====================
def _recipe():
    rec = R.empty_recipe("새것")
    b = R.empty_block("b1")
    b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    b["steps"] = [{"name": "s", "time_ms": 200, "valves": [], "pause_ok": True,
                   **({"rf": False} if DEV.HAS_RF else {})}]
    if DEV.HAS_RF:
        b["rf_w"] = 0.0
    rec["blocks"] = [b]
    return rec


async def test_c6_new_start_right_after_abort_records_previous_end(cfg, monkeypatch):
    r, tbl = _runner(cfg)
    r.run = {"name": "앞것", "recipe": None, "table": tbl}
    r.active_run, r._was_running, r._abort_sent = True, True, True
    r._last_pos = (1, 1, 2)
    r.started_at = time.time() - 30
    r.recipe, r.recipe_name = _recipe(), "새것"
    monkeypatch.setattr(r, "can_start", lambda: (True, [], []))
    monkeypatch.setattr(process.R, "validate", lambda *_a: {"errors": []})

    async def upload(*_a):
        return False, "시험 — 여기서 멈춤"
    state.link = _link(_s(8, st=A.STATE_IDLE, blk=1), None, upload_recipe=upload)   # PLC 는 이미 끝났다
    logs = []

    async def log(m, *_a):
        logs.append(m)
    try:
        ok, _why = await r._start_flow(log, _nolog)
        assert not ok
        assert any(m.startswith("공정 중단 (운전자 중단)") and "앞것" in m for m in logs), logs
        assert not any("공정 중 위치를 보지 못함" in m for m in logs), logs
    finally:
        state.link = None


# ===================== C7 서버 시작 실패 =====================
async def test_c7_lifespan_failure_is_logged(monkeypatch):
    wrote = []
    monkeypatch.setattr(server.logger, "write", lambda lv, m: wrote.append((lv, m)))
    app = server.create_app("", single_instance=False, start_io=False)
    monkeypatch.setattr(paths, "check_writable", lambda: (_ for _ in ()).throw(RuntimeError("일부러")))
    with pytest.raises(RuntimeError):
        async with app.router.lifespan_context(app):
            pass
    assert any(lv == "err" and "서버 시작 실패" in m and "일부러" in m for lv, m in wrote), wrote


def test_c7_headless_exit_code_1_when_lifespan_fails(monkeypatch):
    import sys
    import run as entry
    from peald import commands

    async def asgi(scope, receive, send):
        # Starlette 의 lifespan 처럼 — 시작이 실패하면 'startup.failed' 를 보낸다
        if scope["type"] == "lifespan":
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.failed", "message": "일부러"})
    port = free_port()

    def fake_app(_cfg=""):
        state.cfg = {"server": {"host": "127.0.0.1", "port": port}}
        return asgi
    monkeypatch.setattr(commands, "set_shutdown_handler", lambda _f: None)
    monkeypatch.setattr(state, "cfg", state.cfg)
    monkeypatch.setattr(entry, "create_app", fake_app)
    monkeypatch.setattr(sys, "argv", ["run.py", "--headless"])
    with pytest.raises(SystemExit) as e:
        entry.main()
    assert e.value.code == 1


# ===================== C9 포트 오류 이유 =====================
def test_c9_not_busy_error_returns_at_once_with_real_reason():
    t0 = time.monotonic()
    socks, why = window.bind_port("192.0.2.1", free_port(), total_s=3, step_s=0.2)   # 이 PC 에 없는 주소(TEST-NET-1)
    took = time.monotonic() - t0
    assert not socks and why and not why.startswith(window.BUSY_MARK)
    assert took < 1.0, took                                  # 다시 시도하지 않는다
    text = window.port_busy_text("192.0.2.1", 8201, why)
    assert "다른 프로그램" not in text and "192.0.2.1" in text


def test_c9_busy_error_still_retries():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        t0 = time.monotonic()
        socks, why = window.bind_port("127.0.0.1", port, total_s=0.6, step_s=0.2)
        assert not socks and why.startswith(window.BUSY_MARK) and time.monotonic() - t0 >= 0.55
        assert "다른 프로그램" in window.port_busy_text("127.0.0.1", port, why)


# ===================== C10 크기 0 인 알람 CSV =====================
def test_c10_zero_size_alarm_csv_gets_header():
    now = datetime.datetime.now()
    os.makedirs(paths.ALARMS_DIR, exist_ok=True)
    p = os.path.join(paths.ALARMS_DIR, f"alarms-{now:%Y%m%d}.csv")
    open(p, "wb").close()
    logger.alarm_event("발생", "A0-14", "가스 누출", "중대")
    with open(p, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["시각", "구분", "코드", "내용", "등급"] and rows[1][1:3] == ["발생", "A0-14"], rows


def test_c10_state_comment_is_korean():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            os.path.dirname(process.__file__).split(os.sep)[-1], "state.py"), encoding="utf-8").read()
    comments = [ln for ln in src.splitlines() if ln.lstrip().startswith("#")]
    assert not any("\\u" in ln for ln in comments)


# 가져온 이름 정리
_ = (json,)
