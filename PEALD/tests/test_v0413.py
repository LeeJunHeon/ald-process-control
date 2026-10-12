"""v0.4.13 — 멈춘 계열 다시 그리기 · 계열 끊김 표시 · 띠 글자 · 이어받기 다시 시도 · 포트 listen · 주소 family ·
알람 이력 줄 복구 · 긴 스텝 기준 · Powder 시험 O3 허가.

★ 화면의 순수 함수(decimate.js · chart.js)는 node 로 부른다(node 가 없으면 건너뜀). 브라우저 시험은 없다.
★ 시뮬레이터는 가짜 시계 · 한 스캔 전체(conftest FakeSim).
"""
import os
import csv
import types
import socket
import asyncio
import datetime

import pytest

from peald import addresses as A
from peald import logger
from peald import process
from peald import server
from peald import window
from peald.config import host_valid, validate
from peald.state import AlarmTracker, state

from conftest import FakeSim, free_port
from test_v0411 import _dec, _runner, _s, _blocks
from test_v0412 import _chart, _start, _step_ms, _alarm_file


# ===================== A1 열은 절대 시각으로 =====================
def test_a1_tail_key_stable_when_window_slides_without_new_points():
    """새 점이 없는 계열(멈춤 · '—')의 마지막 열은 x0 가 화소의 일부만큼씩 움직여도 같다."""
    out = _dec(r'''
      const T = 1.7e12, span = 3600 * 1000, cols = 652;
      const pts = []; for (let i = 0; i < 3600 * 5; i++) { const v = Math.sin(i * 1.7) * 5; pts.push([T - span + i * 200, v, v, v]); }   // v0.4.14: 띠가 생기게(최소 = 최대 = 값)
      const ypx = v => Math.round(v * 10);
      const keys = new Set(), last = new Set();
      for (let k = 0; k < 80; k++) {                      // 5 Hz 로 16 s — 창은 화소의 일부씩 움직인다
        const x1 = T + k * 200, x0 = x1 - span;
        keys.add(D.tailKey(pts, x0, x1, cols, ypx));
        const r = D.view(pts, x0, x1, 5000, cols);
        const b = r.band[r.band.length - 1];
        last.add(JSON.stringify([b[0], b[1], b[2], b.n, b.xs]));
      }
      console.log(JSON.stringify({ keys: keys.size, last: last.size }));
    ''')
    assert out == {"keys": 1, "last": 1}, out


def test_a1_columns_are_absolute_and_x1_point_adds_no_column():
    out = _dec(r'''
      const G = D.grid(1234, 1234 + 1000, 10);            // 열 폭 100
      const g0 = D.grid(0, 999, 10);                      // x1 이 열 경계에 딱 걸림(999 / 99.9 = 10)
      console.log(JSON.stringify({ base: G.base, c: [G.col(1234), G.col(1299), G.col(1300), G.col(2234)],
                                   start: G.start(1), last: G.last, last0: g0.last, c999: g0.col(999) }));
    ''')
    assert out["base"] == 12 and out["c"] == [0, 0, 1, 10] and out["start"] == 1300, out
    assert out["last"] == 10                                # 첫 열이 x0 앞에서 시작 — 창 안 열은 11 개까지
    assert out["last0"] == 9 and out["c999"] == 10           # view 가 9 로 묶는다(열을 하나 더 만들지 않게)


def test_a1_chart_stalled_series_does_not_redraw_while_window_slides():
    out = _chart(r'''
      const span = 3600 * 1000, T = 1.7e12;
      const pts = []; for (let i = 0; i < 3600 * 5; i++) pts.push([T - span + i * 200, 0, 0, 50 + Math.sin(i * 1.7) * 5]);
      const S = () => [{ label: 'MFC1', unit: 'sccm', color: '#888', pts: pts }];
      for (let k = 0; k < 25; k++) {                      // 5 s(1 h 보기에서 1 px 보다 짧다)
        const x1 = T + k * 200; c.set({ series: S(), x0: x1 - span, x1, gap: 5000, tol: 1e9 });
      }
      console.log(JSON.stringify({ draws }));
    ''')
    assert out["draws"] <= 2, out                          # 처음 한 번(+ 창이 1 px 넘어가면 한 번)


# ===================== A2 한 계열 끊김을 바로 =====================
def test_a2_signature_includes_per_series_staleness():
    out = _chart(r'''
      const S = (pts) => [{ label: 'CH1', unit: '℃', color: '#888', pts }];
      const pts = []; for (let i = 0; i <= 3600; i++) pts.push([i * 1000, 25, 25, 25]);
      const x1 = 3600 * 1000 + 137;                        // v0.4.14: 열 경계(3,600,000 / 652 의 배수)에 걸리지 않게
      c.set({ series: S(pts), x0: x1 - 3600 * 1000, x1, gap: 5000, tol: 1000 });
      const n0 = draws;
      c.set({ series: S(pts), x0: x1 + 600 - 3600 * 1000, x1: x1 + 600, gap: 5000, tol: 1000 });
      const n1 = draws;                                     // 0.6 s — 아직 tol 안
      c.set({ series: S(pts), x0: x1 + 1200 - 3600 * 1000, x1: x1 + 1200, gap: 5000, tol: 1000 });
      const n2 = draws;                                     // 1.2 s — tol 넘음(창은 1 px 도 안 움직였다)
      console.log(JSON.stringify({ n0, n1, n2, stale: c.debug().rows[0].stale }));
    ''')
    assert out["n0"] == 1 and out["n1"] == 1, out
    assert out["n2"] == 2 and out["stale"] is True, out


# ===================== A3 띠 글자만 다시 쓰기 =====================
def test_a3_band_text_redrawn_at_most_once_per_second():
    out = _chart(r'''
      let T = 0; const c2 = HistChart(canvas, { now: () => T });
      const pts = []; for (let i = 0; i < 3600; i++) pts.push([i * 1000, 0, 0, i % 2 ? 100 : 0]);
      pts.push([3600 * 1000, 50, 50, 50]);
      const S = () => [{ label: 'CVG', unit: '', color: '#888', pts }];
      const x1 = 3600 * 1000, x0 = 0;
      c2.set({ series: S(), x0, x1, gap: 5000, tol: 1e9 });
      const a = draws, t0 = c2.debug().rows[0].text;
      // v0.4.15: 띠만 다시 쓸 때는 clearRect 가 두 번(띠 · 넘침 안내 자리) — 전체 지우기와 띠 지우기를 따로 센다
      const G = c2.debug().geom, full = [], band = [];
      ctx.clearRect = (x, y, w, h) => { if (x === 0 && y === 0) full.push(T); else if (x === G.x1 + 1) band.push(T); };
      pts[pts.length - 1] = [x1, 50.08, 50.08, 50.08];   // 화소는 그대로(0.08 / 100 × 180 px) · 글자는 50.1
      T = 500; c2.set({ series: S(), x0, x1, gap: 5000, tol: 1e9 });
      const t1 = c2.debug().rows[0].text;                 // 1 s 안 — 아직
      T = 1200; c2.set({ series: S(), x0, x1, gap: 5000, tol: 1e9 });
      const t2 = c2.debug().rows[0].text;                 // 띠만 다시
      T = 2500; c2.set({ series: S(), x0, x1, gap: 5000, tol: 1e9 });   // 같은 글자 — 그대로
      console.log(JSON.stringify({ a, full, band, t0, t1, t2 }));
    ''')
    assert out["a"] == 1, out
    assert out["band"] == [1200] and out["full"] == [], out
    assert out["t0"].startswith("50.0") and out["t1"].startswith("50.0") and out["t2"].startswith("50.1"), out


def test_a3_band_only_redraw_keeps_the_plot():
    """띠만 다시 쓸 때는 띠 영역만 지운다(플롯 · 축은 그대로)."""
    out = _chart(r'''
      let T = 0; const c2 = HistChart(canvas, { now: () => T });
      const rects = [];
      const pts = []; for (let i = 0; i < 3600; i++) pts.push([i * 1000, 0, 0, i % 2 ? 100 : 0]);
      pts.push([3600 * 1000, 50, 50, 50]);
      const S = () => [{ label: 'CVG', unit: '', color: '#888', pts }];
      c2.set({ series: S(), x0: 0, x1: 3600 * 1000, gap: 5000, tol: 1e9 });
      const G = c2.debug().geom;
      ctx.clearRect = (x, y, w, h) => rects.push([x, y, w, h]);
      pts[pts.length - 1] = [3600 * 1000, 50.08, 50.08, 50.08];
      T = 1500; c2.set({ series: S(), x0: 0, x1: 3600 * 1000, gap: 5000, tol: 1e9 });
      console.log(JSON.stringify({ rects, x1: G.x1 }));
    ''')
    # v0.4.15: 띠 · 넘침 안내 자리 두 번 지울 수 있다 — 첫 번째는 띠(x1 + 1), 어느 것도 플롯(x ≤ x1)은 건드리지 않는다
    assert out["rects"] and out["rects"][0][0] == out["x1"] + 1, out
    assert all(r[0] > out["x1"] for r in out["rects"]), out


# ===================== B1 이어받기 다시 시도 =====================
def _link(s, read):
    return types.SimpleNamespace(connected=True, status=s, read_recipe_area=read)


async def test_b1_adopt_wanted_rules(cfg):
    r, _tbl = _runner(cfg)
    state.link = _link(_s(4, st=A.STATE_RUN), None)
    try:
        assert r.adopt_wanted()
        r.active_run = True
        assert not r.adopt_wanted()                         # 이 PC 가 맡은 공정이 있다
        r.active_run = False
        r._b13_wait = (0, None)
        assert not r.adopt_wanted()                         # 끝 처리 중
        r._b13_wait = None
        r._abort_pending = True
        assert not r.adopt_wanted()                         # v0.4.14(D1): 중단 결과를 기다리는 중
        r._abort_pending = False
        r._end_deferred = (None, 0, None)
        assert not r.adopt_wanted()                         # v0.4.14(D1): 미뤄 둔 끝이 있다
        r._end_deferred = None
        r.phase = "uploading"
        assert not r.adopt_wanted()                         # 시작 절차 중
        r.phase = process.IDLE
        state.link.status = _s(6, st=A.STATE_IDLE)
        assert not r.adopt_wanted()                         # PLC 가 공정 중이 아니다
    finally:
        state.link = None


async def test_b1_selected_recipe_does_not_block_adopt(cfg):
    r, tbl = _runner(cfg)
    r.recipe, r.recipe_name = {"blocks": []}, "고른 것"

    async def read_area():
        return list(tbl["words"])
    state.link = _link(_s(4, st=A.STATE_RUN), read_area)
    try:
        async def log(*_a):
            return None
        assert await r.adopt_running(log) is True
        assert r.active_run and r.active_table.get("words")
        assert r.recipe_name != "고른 것"                   # PLC 에 올라간 레시피로 바뀐다
    finally:
        state.link = None


async def test_b1_disconnect_during_read_is_logged(cfg, monkeypatch):
    monkeypatch.setattr(process, "ADOPT_READ_GAP_S", 0.0)
    r, _tbl = _runner(cfg)
    logs = []

    async def read_area():
        state.link.connected = False                         # 되읽는 중 끊김
        return None
    state.link = _link(_s(4, st=A.STATE_RUN), read_area)
    try:
        async def log(m, *_a):
            logs.append(m)
        assert await r.adopt_running(log) is False
        assert not r.active_run and any("끊겼습니다" in m for m in logs), logs
    finally:
        state.link = None


async def test_b1_no_table_end_result_uses_b13_rules(cfg, monkeypatch):
    """표 없이 이어받은 공정: 남은 b13 + 운전자 중단 = '운전자 중단', 이번 공정 중 선 b13 = '표 모름'."""
    monkeypatch.setattr(process, "ADOPT_READ_GAP_S", 0.0)
    r, _tbl = _runner(cfg)

    async def read_area():
        return None
    state.link = _link(_s(4, st=A.STATE_RUN, a0=1 << A.ALM0_RECIPE), read_area)
    try:
        async def log(*_a):
            return None
        assert await r.adopt_running(log) is True
        assert r._b13_pre is True and r.active_name
        r.abort_begin()
        r.abort_result(True)
        assert r.end_result(_s(8, blk=2, a0=1 << A.ALM0_RECIPE)) == "중단 (운전자 중단)"
        r._b13_cleared = True                               # 그 뒤 0 을 봤다 → 다시 선 b13 은 이번 공정 것
        assert r.end_result(_s(8, blk=2, a0=1 << A.ALM0_RECIPE)) == "중단 (레시피 표 오류 — 블록 2 · 표 모름)"
    finally:
        state.link = None


async def test_b1_adopt_loop_keeps_trying_without_time_limit(monkeypatch):
    monkeypatch.setattr(process, "ADOPT_RETRY_S", 0.01)
    calls = []

    class Fake:
        want = False

        def adopt_wanted(self):
            return self.want

        async def adopt_running(self, _log):
            calls.append(1)
            return False
    f = Fake()
    monkeypatch.setattr(state, "runner", f)
    t = asyncio.create_task(server._adopt_loop())
    try:
        await asyncio.sleep(0.1)
        assert not calls                                    # 붙지 않았거나 할 일이 없으면 부르지 않는다
        f.want = True
        await asyncio.sleep(0.1)
        assert len(calls) >= 3                              # 실패해도 계속(시간 제한 없음)
    finally:
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t


# ===================== B2 포트 =====================
def test_b2_bound_sockets_are_listening():
    port = free_port()
    socks = window.bind_sockets("127.0.0.1", port)
    try:
        if hasattr(socket, "SO_ACCEPTCONN"):
            assert all(s.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) for s in socks)
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass                                            # listen 중이라 연결이 받아진다
        assert window.port_probe("127.0.0.1", port), "두 번째 실행은 같은 포트를 묶지 못한다"
    finally:
        window.close_sockets(socks)


def test_b2_unsupported_family_is_skipped(monkeypatch):
    port = free_port()
    real = socket.getaddrinfo
    bogus = (-12345, socket.SOCK_STREAM, 0, "", ("::1", port, 0, 0))

    def fake(host, p, *a, **k):
        return [bogus] + real("127.0.0.1", p, socket.AF_INET, socket.SOCK_STREAM)
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    socks = window.bind_sockets("localhost", port)
    try:
        assert len(socks) == 1 and socks[0].family == socket.AF_INET
    finally:
        window.close_sockets(socks)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [bogus])
    with pytest.raises(OSError) as e:
        window.bind_sockets("localhost", port)
    assert "찾지 못했습니다" not in str(e.value)               # 실제 오류(만들 수 없는 family)


def test_b2_host_case_and_empty_text(cfg):
    assert host_valid("LOCALHOST") and host_valid("LocalHost")
    cfg["server"]["host"] = "LOCALHOST"
    assert not any("server.host" in m for _lv, m in validate(cfg))
    cfg["server"]["host"] = "my-pc"
    msg = next(m for lv, m in validate(cfg) if "server.host" in m)
    assert "모든 주소)" not in msg and "127.0.0.1" in msg and "0.0.0.0" in msg


@pytest.mark.skipif(os.name != "nt", reason="Windows 소켓 옵션")
def test_b2_windows_socket_is_exclusive():
    """v0.4.14(D2): 평소에는 127.0.0.1 로 묶은 소켓의 SO_EXCLUSIVEADDRUSE 값만 본다(방화벽 허용 창이 뜨지 않게)."""
    assert window.EXCLUSIVE_BIND
    socks = window.bind_sockets("127.0.0.1", free_port())
    try:
        assert all(s.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) for s in socks)
    finally:
        window.close_sockets(socks)


@pytest.mark.skipif(os.name != "nt" or os.environ.get("ALD_NET_TESTS") != "1",
                    reason="Windows · ALD_NET_TESTS=1 일 때만(0.0.0.0 으로 들어 방화벽 허용 창이 뜰 수 있다)")
def test_b2_windows_other_socket_cannot_steal_our_port():
    """Windows: 우리가 모든 주소(0.0.0.0:P)로 들을 때 다른 소켓이 127.0.0.1:P 를 묶지 못한다(SO_EXCLUSIVEADDRUSE).
    옵션 없이 열면 같은 사용자의 다른 프로그램이 127.0.0.1:P 를 묶어 그 접속을 가져갔다(v0.4.13 이 PC 확인)."""
    assert window.EXCLUSIVE_BIND
    port = free_port()
    socks = window.bind_sockets("0.0.0.0", port)
    try:
        for reuse in (False, True):
            t = socket.socket()
            if reuse:
                t.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                with pytest.raises(OSError):
                    t.bind(("127.0.0.1", port))
            finally:
                t.close()
    finally:
        window.close_sockets(socks)


def test_b2_quick_restart_rebinds_with_our_options():
    """빠른 다시 시작 — 우리 쪽이 먼저 닫아 TIME_WAIT 만 남은 포트를 우리 방식(Windows SO_EXCLUSIVEADDRUSE ·
    POSIX SO_REUSEADDR)으로 바로 다시 묶는다."""
    port = free_port()
    socks = window.bind_sockets("127.0.0.1", port)
    c = socket.create_connection(("127.0.0.1", port))
    a, _ = socks[0].accept()
    a.close()                                               # 우리 쪽이 먼저 닫는다 → 우리 쪽 TIME_WAIT
    c.close()
    window.close_sockets(socks)
    again = window.bind_sockets("127.0.0.1", port)
    window.close_sockets(again)


# ===================== B3 알람 이력 파일 =====================
def test_b3_unterminated_last_line_does_not_swallow_next():
    now = datetime.datetime.now()
    p = _alarm_file(now, [[f"{now:%Y-%m-%d} 08:00:00", "발생", "A0-07", "베이스 도달 시간 초과", "경고"]],
                    raw_tail=f"{now:%Y-%m-%d} 08:01:00,해제,A0-07,베이스 도달 시간 초과,경".encode("utf-8"))
    logger.alarm_event("발생", "A0-14", "가스 누출", "중대")           # 끊긴 줄 뒤에 덧붙임
    with open(p, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[-1][1:3] == ["발생", "A0-14"], rows[-2:]
    t = AlarmTracker()
    t.load_recent(now)
    assert [h["code"] for h in t.history] == ["A0-14", "A0-07"]


def test_b3_nul_run_does_not_drop_whole_file():
    now = datetime.datetime(2026, 10, 2, 9, 0, 0)
    _alarm_file(now, [["2026-10-02 08:00:00", "발생", "A0-07", "베이스 도달 시간 초과", "경고"]],
                raw_tail=b"\x00" * 200_000 + "2026-10-02 08:30:00,발생,A0-14,가스 누출,중대\r\n".encode("utf-8"))
    t = AlarmTracker()
    assert t.load_recent(now) == 2
    assert [h["code"] for h in t.history] == ["A0-14", "A0-07"]


def test_b3_one_bad_quoted_line_keeps_others():
    now = datetime.datetime(2026, 10, 2, 9, 0, 0)
    _alarm_file(now, [["2026-10-02 08:00:00", "발생", "A0-07", "베이스 도달 시간 초과", "경고"]],
                raw_tail=b'2026-10-02 08:10:00,"' + b"x" * 200_000 + b"\r\n" +
                         "2026-10-02 08:30:00,발생,A0-14,가스 누출,중대\r\n".encode("utf-8"))
    t = AlarmTracker()
    assert t.load_recent(now) == 2
    assert [h["code"] for h in t.history] == ["A0-14", "A0-07"]


# ===================== C1 긴 스텝은 원래 시간으로 =====================
@pytest.mark.parametrize("step_ms,want_long,want_d26", [(60_050, True, 1200), (60_000, False, 1230)])
def test_c1_long_step_decided_by_original_time(cfg, step_ms, want_long, want_d26):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    _start(fs, _blocks(1, steps=1, step_ms=step_ms))
    for _ in range(123):
        fs.step(1, 0.01)
    assert s.step_long is want_long and s.step_dur == 60_000
    assert _step_ms(s) == want_d26, _step_ms(s)            # 래더: 원래 60,050 ms → 100 ms 단위(1200)
