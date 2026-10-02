"""v0.4.11 — 실시간 트렌드 줄이기 · 좁은 폭 · 멈춤/끊김 표시 · 묶음 라벨 · hover 지시선 · 포트 · 알람 이력 ·
v0.4.10 남은 것(이어받은 공정 b13 · 시뮬레이터 시점).

★ 화면의 순수 함수(decimate.js · chart.js 의 묶음 라벨)는 node 로 바로 부른다(node 가 없으면 건너뜀).
★ 화면 단추 · 지시선 · refill 은 브라우저로 본다 — 브라우저를 띄울 수 없으면 건너뛴다.
★ 시뮬레이터는 가짜 시계 · 스캔 단위(conftest FakeSim · FakeSim.cmd).
"""
import os
import csv
import json
import time
import types
import shutil
import socket
import asyncio
import datetime
import subprocess

import pytest

from powderald import addresses as A
from powderald import device as DEV
from powderald import paths
from powderald import recipe as R
from powderald import window
from powderald.convert import Converters
from powderald.process import ProcessRunner
from powderald.state import AlarmTracker, state

from conftest import FakeSim
from test_v047 import rig                                                  # noqa: F401  (픽스처)
from test_v048 import alm0, alm1, aux, pump_down, table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "frontend", "js")
bit = A.bit


# ===================== node 로 JS 순수 함수 =====================
def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 가 없다")
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _dec(body: str):
    return _node("const D = require(%s);\n%s" % (json.dumps(os.path.join(JS, "decimate.js")), body))


def test_u1_view_keeps_first_min_max_last_per_column():
    out = _dec(r'''
      // 1000 점(0..999 ms) · 열 10 개 → 열마다 100 점, 값은 톱니(열 안에서 최소 · 최대가 가운데)
      const pts = [];
      for (let i = 0; i < 1000; i++) { const v = (i % 100 === 37) ? -5 : (i % 100 === 61) ? 9 : i % 7;
                                       pts.push([i, v - 0.5, v + 0.5, v]); }
      const r = D.view(pts, 0, 999, Infinity, 10);
      const col0 = r.line.filter(p => p[0] < 100).map(p => p[0]);
      console.log(JSON.stringify({ n: r.line.length, col0, bands: r.band.length, b0: r.band[0],
                                   lo: r.lo, hi: r.hi }));
    ''')
    assert out["col0"] == [0, 37, 61, 99]                  # 처음 · 최소 · 최대 · 끝(시간 순)
    assert out["n"] <= 4 * 10 and out["bands"] == 10
    assert out["b0"] == [0, -5.5, 9.5]                      # 열마다 띠 하나(최소 ~ 최대)


def test_u1_view_cuts_to_visible_range_with_one_point_outside():
    out = _dec(r'''
      const pts = []; for (let i = 0; i < 10000; i++) pts.push([i, i, i, i]);
      const r = D.view(pts, 5000, 5100, 0, 1000);           // 열이 점보다 많다 — 그대로(자르기만)
      console.log(JSON.stringify({ first: r.line[0][0], last: r.line[r.line.length - 1][0], n: r.line.length,
                                   bands: r.band.length }));
    ''')
    assert out["first"] == 4999 and out["last"] == 5101 and out["n"] == 103
    assert out["bands"] == 0                                 # 최소 = 최대(실시간 점)는 띠가 없다


def test_u1_view_keeps_gaps():
    out = _dec(r'''
      const pts = [];
      for (let i = 0; i < 500; i++) pts.push([i, 1, 1, 1]);
      for (let i = 900; i < 1400; i++) pts.push([i, 2, 2, 2]);   // 500..900 끊김
      const r = D.view(pts, 0, 1400, 50, 7);                 // 한 열 200 ms — 끊김이 열 안에 있다
      const breaks = []; for (let i = 1; i < r.line.length - 1; i++) if (r.line[i] === null) breaks.push([r.line[i - 1][0], r.line[i + 1][0]]);
      console.log(JSON.stringify({ breaks }));
    ''')
    assert out["breaks"] == [[499, 900]]                     # 끊김 자리에만 null(양쪽 점이 남는다) → 선이 끊긴다


def test_u1_live_store_raw_two_minutes_then_one_second_buckets():
    out = _dec(r'''
      const L = new D.Live(120000, 3600000);
      const t0 = 1000000;
      for (let k = 0; k < 5 * 600; k++) L.push(t0 + k * 200, (k % 10));    // 5 Hz · 10 분
      const now = t0 + (5 * 600 - 1) * 200;
      const raw = L.pts.filter(p => p[0] > now - 120000);
      const old = L.pts.filter(p => p[0] < now - 121000);
      const span = old.length ? old[1][0] - old[0][0] : 0;
      console.log(JSON.stringify({ n: L.pts.length, raw: raw.length, old: old.length, span,
                                   o0: old[0], sorted: L.pts.every((p, i) => !i || p[0] >= L.pts[i - 1][0]) }));
    ''')
    assert out["raw"] == 600                                  # 최근 2 분 = 받은 그대로(5 Hz)
    assert 470 <= out["old"] <= 481 and abs(out["span"] - 1000) < 1e-6   # 그보다 오래된 것 = 1 s 묶음
    assert out["o0"][1] == 0 and out["o0"][2] == 4 and abs(out["o0"][3] - 2) < 1e-9
    assert out["sorted"] and out["n"] < 1200


def test_u1_live_store_trims_one_hour_in_one_cut():
    out = _dec(r'''
      const L = new D.Live(120000, 3600000);
      for (let k = 0; k < 4000; k++) L.push(k * 1000, 1);         // 1 Hz · 4000 s
      console.log(JSON.stringify({ first: L.pts[0][0], last: L.pts[L.pts.length - 1][0], n: L.pts.length }));
    ''')
    assert out["first"] >= (3999 - 3600) * 1000 and out["last"] == 3999000 and out["n"] <= 3601


def _chart_api(body: str):
    return _node(r'''
      global.window = { addEventListener() {}, devicePixelRatio: 1 };
      global.document = { documentElement: {} };
      require(%s);
      global.window.fmt = {};
      %s
    ''' % (json.dumps(os.path.join(JS, "chart.js")), body))


@pytest.mark.parametrize("labels,want", [
    (["CH1 현재", "CH1 설정"], "CH1 현재 · 설정"),
    (["CH1 이름 현재", "CH1 이름 설정", "CH2 이름 현재", "CH2 이름 설정", "CH3 이름 현재", "CH3 이름 설정"],
     "CH1–CH3 현재 · 설정"),
    (["CH1 Stage·챔버", "CH2 전구체 라인", "CH3 반응물 라인"], "CH1–CH3"),
    (["CH4 현재", "CH4 설정", "CH5 현재"], "CH4 현재 · 설정 · CH5 현재"),
    (["CH6 설정", "CH7 설정"], "CH6 · CH7 설정"),
])
def test_u5_merged_label_keeps_what_was_merged(labels, want):
    out = _chart_api(r'''
      const H = global.window.HistChart;
      console.log(JSON.stringify(H.segText(H.mergeSegs(%s))));
    ''' % json.dumps(labels))
    assert out == want


# ===================== U6 포트 =====================
def test_u6_port_probe_busy_and_free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert window.port_probe("127.0.0.1", port)          # 듣고 있으면 이유
        t0 = time.monotonic()
        why = window.wait_port("127.0.0.1", port, total_s=0.6, step_s=0.2)
        assert why and time.monotonic() - t0 >= 0.55          # 잠시 다시 시도한 뒤 이유
        assert "설정 포트" in window.port_busy_text("127.0.0.1", port, why)
    assert window.port_probe("127.0.0.1", port) == ""         # 닫으면 열 수 있다


def test_u6_time_wait_port_is_usable():
    """빠른 다시 시작 — 서버 쪽이 먼저 닫아 TIME_WAIT 만 남은 포트도 서버와 같은 소켓 옵션이면 열린다."""
    ls = socket.socket()
    if os.name == "posix":
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    port = ls.getsockname()[1]
    c = socket.create_connection(("127.0.0.1", port))
    a, _ = ls.accept()
    a.close()                                                 # 서버 쪽이 먼저 닫는다 → 서버 쪽 TIME_WAIT
    time.sleep(0.1)
    c.close()
    ls.close()
    time.sleep(0.1)
    assert window.port_probe("127.0.0.1", port) == "", "TIME_WAIT 포트를 '사용 중'으로 봤다"


def test_u6_find_free_port_skips_busy():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        free = window.find_free_port("127.0.0.1", port, tries=5)
        assert free is not None and free != port


# ===================== U6 알람 이력 불러오기 =====================
def test_u6_alarm_history_loaded_from_today_and_yesterday():
    now = datetime.datetime(2026, 10, 2, 9, 0, 0)
    os.makedirs(paths.ALARMS_DIR, exist_ok=True)

    def write(day, rows):
        with open(os.path.join(paths.ALARMS_DIR, f"alarms-{day:%Y%m%d}.csv"), "w", encoding="utf-8-sig",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(["시각", "구분", "코드", "내용", "등급"])
            w.writerows(rows)
    y = now - datetime.timedelta(days=1)
    write(y, [["2026-10-01 23:50:00", "발생", "A0-00", "비상정지", "중대"],
              ["2026-10-01 23:55:10", "해제", "A0-00", "비상정지", "중대"]])
    write(now, [["2026-10-02 08:00:00", "발생", "A0-14", "가스 누출", "중대"],
                ["2026-10-02 08:01:00", "해제(연결 끊김)", "A0-14", "가스 누출", "중대"],
                ["2026-10-02 08:30:00", "발생", "A0-07", "베이스 도달 시간 초과", "경고"]])
    t = AlarmTracker()
    n = t.load_recent(now)
    assert n == 3 and t.ver == 1
    h = t.history                                              # 최근이 앞
    assert [x["code"] for x in h] == ["A0-07", "A0-14", "A0-00"]
    assert h[0]["cleared"] == "모름(재시작 전)" and h[0]["crit"] is False and h[0]["date"] == "10-02"
    assert h[1]["cleared"] == "해제(연결 끊김) 08:01:00"
    assert h[2]["cleared"] == "23:55:10" and h[2]["since"] == "23:50:00"


def test_u6_alarm_history_missing_files_is_empty():
    t = AlarmTracker()
    assert t.load_recent(datetime.datetime(2000, 1, 2)) == 0 and t.history == []


# ===================== P1 ~ P3 끝 판정 =====================
def _runner(cfg):
    state.install_config(cfg, [], "example")
    state.alarms.clear_all()
    rec = R.empty_recipe("이어")
    b = R.empty_block("b1")
    b["repeat"] = 3
    b["mfc_sccm"] = [100.0] + [0.0] * (DEV.MFC_COUNT - 1)
    b["steps"] = [{"name": "s", "time_ms": 200, "valves": [], "pause_ok": True,
                   **({"rf": False} if DEV.HAS_RF else {})}]
    if DEV.HAS_RF:
        b["rf_w"] = 0.0
    rec["blocks"] = [b, json.loads(json.dumps(b))]
    tbl = R.to_plc_words(cfg, Converters(cfg), rec)
    return ProcessRunner(state), tbl


def _s(seq, st=A.STATE_IDLE, blk=1, a0=0, step=1):
    s = [0] * A.STATUS_COUNT
    s[A.D_SEQ_STATE], s[A.D_STATE], s[A.D_SEQ_BLOCK], s[A.D_ALARM0], s[A.D_SEQ_STEP] = seq, st, blk, a0, step
    s[A.D_SEQ_BLOCK_PASS] = 1
    return s


async def test_p1_adopt_running_sets_b13_start_state(cfg):
    r, tbl = _runner(cfg)
    s = _s(4, st=A.STATE_RUN, a0=1 << A.ALM0_RECIPE)          # 남은 b13(앞 시작 결과 3 뒤 리셋 안 함)

    async def read_area():
        return list(tbl["words"])
    state.link = types.SimpleNamespace(connected=True, status=s, read_recipe_area=read_area)
    try:
        async def log(*_a):
            return None
        await r.adopt_running(log)
        assert r.active_run and r._b13_pre is True and r._b13_cleared is False
        assert r.active_table.get("words")                      # 끝 판정 근거 = PLC 에서 되읽은 표
        r.abort_begin()
        r.abort_result(True)
        assert r.end_result(_s(8, blk=1, a0=1 << A.ALM0_RECIPE)) == "중단 (운전자 중단)"
    finally:
        state.link = None


def test_p2_abort_result_after_window_merges_current_b13(cfg):
    r, _t = _runner(cfg)
    state.link = types.SimpleNamespace(connected=True, status=_s(8, blk=2, a0=1 << A.ALM0_RECIPE))
    try:
        logs = []
        r.active_run = True
        r._abort_gen = r._gen
        r._abort_pending = True
        r._end_seen_mono = time.monotonic() - 2.0               # 1 s 창은 이미 지났다
        r._end_deferred = (_s(8, blk=2), time.time(), lambda lv, m: logs.append(m))
        r.abort_result(True)
        assert r.last_result.startswith("중단 (레시피 표 오류 — 블록 2"), r.last_result
    finally:
        state.link = None


def test_p3_negative_step_time_is_signed(cfg):
    r, tbl = _runner(cfg)
    r.run = {"name": "이어", "recipe": None, "table": tbl}
    st = A.D_RCP_STEP_BASE + A.RCP_STEP_TIME_LO - A.RCP_SUM_BASE
    tbl["words"][st] = 0xFFFF
    tbl["words"][st + 1] = 0xFFFF                               # 부호 있는 32 비트 = −1
    out = r.end_result(_s(8, blk=1, a0=1 << A.ALM0_RECIPE, step=1))
    assert "시간 -1 ms" in out and "4294967295" not in out, out


# ===================== M1 ~ M4 시뮬레이터 =====================
def _load(fs, rec):
    s = fs.sim
    s.write(A.D_PRM_MFC_STABLE, [0])
    s.write(A.D_PRM_MFC_TOL, [0])
    s.write(A.RCP_SUM_BASE, R.to_plc_words(fs.sim.cfg, Converters(fs.sim.cfg), rec)["words"])


def _blocks(n, repeat_last=1, steps=1, step_ms=100):
    rec = R.empty_recipe("시점")
    rec["blocks"] = []
    for i in range(n):
        b = R.empty_block(f"b{i + 1}")
        b["repeat"] = repeat_last if i == n - 1 else 1
        b["steps"] = [{"name": f"s{k}", "time_ms": step_ms, "valves": [], "pause_ok": True,
                       **({"rf": False} if DEV.HAS_RF else {})} for k in range(steps)]
        rec["blocks"].append(b)
    return rec


def _until_end(fs, n=2000):
    for _ in range(n):
        fs.step(1)
        if not fs.sim.running:
            return
    raise AssertionError("끝나지 않았다")


def test_m1_group_failure_after_last_block_keeps_cycle(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    rec = _blocks(2, repeat_last=3)
    rec["groups"] = [{"from_block": 1, "to_block": 2, "repeat": 1}, {"from_block": 1, "to_block": 1, "repeat": 1}]
    _load(fs, rec)
    assert s._process_start() == A.RESULT_OK
    g2 = A.D_RCP_GROUP_BASE + A.RCP_GROUP_STRIDE - A.RCP_SUM_BASE
    s.work[g2 + 2] = 40000                                     # 그룹 2 반복 40000 = −25536 < 1
    _until_end(fs)
    step2 = s._w(A.D_RCP_BLOCK_BASE + A.RCP_BLOCK_STRIDE + A.RCP_BLOCK_LAST)
    assert (s.reg[A.D_SEQ_BLOCK], s.reg[A.D_SEQ_STATE], s.reg[A.D_SEQ_STEP],
            s.reg[A.D_SEQ_GROUP_PASS], s.reg[A.D_SEQ_BLOCK_PASS]) == (3, 8, step2, 1, 3)


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_m2_o3_generator_follows_p45_copy(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    fs.step(5)
    assert s.o3_gen_on
    s.write(A.D_MANUAL_AUX, [(1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB)])     # 발생기만 끈다
    fs.cmd(A.CMD_MANUAL_APPLY)
    assert not (s.man_aux >> A.AUX_O3_GEN) & 1 and s.o3_gen_on and aux(s, A.AUX_O3_GEN)   # 그 스캔은 그대로
    fs.step(1)
    assert not s.o3_gen_on and not aux(s, A.AUX_O3_GEN)


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_m2_all_close_turns_o3_line_off_one_scan_later(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    fs.step(5)
    on = lambda: (aux(s, A.AUX_O3_GEN), aux(s, A.AUX_IVB), aux(s, A.AUX_BYPASS_PUMP))   # noqa: E731
    assert on() == (True, True, True)
    fs.cmd(A.CMD_ALL_CLOSE)
    assert s.man_aux == 0 and on() == (True, True, True)       # 복사가 모두 닫기보다 앞
    fs.step(1)
    assert on() == (False, False, False)


@pytest.mark.skipif(not DEV.HAS_O3, reason="O3 가 있는 장비만")
def test_m2_bypass_feedback_timer_uses_same_scan_output(cfg):
    fs = FakeSim(cfg)
    s = fs.sim
    s.set_fault("bp_nofb", True)
    fs.step(2)
    s.man_aux = 1 << A.AUX_BYPASS_PUMP
    c = None
    for k in range(700):
        fs.step(1)
        if c is None and aux(s, A.AUX_BYPASS_PUMP):
            c = k
        if alm1(s, A.ALM1_BYPASS_PUMP):
            break
    assert c is not None and k - c == 501, (c, k)               # 래더 c + 501


def test_m3_step_timer_not_run_on_start_and_resume_scan(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    pump_down(fs)
    _load(fs, _blocks(1, repeat_last=50, steps=2, step_ms=300))
    for i in range(80):
        s.write(A.D_PC_HB, [i + 1])
        fs.step(1)
        if bit(s.reg[A.D_INTERLOCK], A.ILK_START_OK):
            break
    assert fs.cmd(A.CMD_PROCESS_START) == A.RESULT_OK
    assert s.reg[A.D_SEQ_STATE] == 4 and A.dword(s.reg[A.D_SEQ_STEP_MS], s.reg[A.D_SEQ_STEP_MS + 1]) == 0
    fs.step(1)
    assert A.dword(s.reg[A.D_SEQ_STEP_MS], s.reg[A.D_SEQ_STEP_MS + 1]) == 20
    assert fs.cmd(A.CMD_PAUSE) == A.RESULT_OK
    for _ in range(100):
        fs.step(1)
        if s.seq_state == 7:
            break
    assert s.seq_state == 7
    assert fs.cmd(A.CMD_RESUME) == A.RESULT_OK
    assert s.reg[A.D_SEQ_STATE] == 4 and A.dword(s.reg[A.D_SEQ_STEP_MS], s.reg[A.D_SEQ_STEP_MS + 1]) == 0


def test_m4_safe_stop_applied_after_step_processing(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    _load(fs, _blocks(1, repeat_last=5, steps=2, step_ms=100))
    assert s._process_start() == A.RESULT_OK
    for _ in range(400):
        if s.running and s.seq_state == 4 and s.cycle == 2 and s.step_no == s.block_last \
                and s.step_ms + 20 >= s.step_dur:
            break
        fs.step(1)
    else:
        raise AssertionError("사이클 2 마지막 스텝 끝 스캔을 못 찾았다")
    first = s.block_first
    s.set_fault("emo", True)                                    # 그 스텝 시간이 끝나는 스캔에 비상정지
    fs.step(1)
    assert not s.running and s.reg[A.D_SEQ_STATE] == 8
    assert (s.reg[A.D_SEQ_STEP], s.reg[A.D_SEQ_BLOCK_PASS]) == (first, 3), "래더: 스텝 1 · 사이클 3 에서 멈춘다"


def test_m4_abort_applied_in_p40_row_130_same_scan(cfg):
    fs = FakeSim(cfg, o3=True)
    s = fs.sim
    pump_down(fs)
    _load(fs, _blocks(1, repeat_last=50, steps=1, step_ms=200))
    for i in range(80):
        s.write(A.D_PC_HB, [i + 1])
        fs.step(1)
        if bit(s.reg[A.D_INTERLOCK], A.ILK_START_OK):
            break
    assert fs.cmd(A.CMD_PROCESS_START) == A.RESULT_OK
    fs.step(5)
    assert fs.cmd(A.CMD_ABORT) == A.RESULT_OK
    assert not s.running and s.reg[A.D_SEQ_STATE] == 8 and "즉시 중단" in s.end_reason


# ===================== 브라우저 — 단추 상태 · 지시선 높이 · refill 압력 =====================
async def _page(port):
    pw_mod = pytest.importorskip("playwright.async_api")
    pw = await pw_mod.async_playwright().start()
    try:
        b = await pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        await pw.stop()
        pytest.skip(f"브라우저를 띄울 수 없음: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
    pg = await b.new_page(viewport={"width": 960, "height": 1040})
    await pg.goto(f"http://127.0.0.1:{port}/")
    await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
    return pw, b, pg


async def test_u4_buttons_and_reasons_when_stalled(rig):
    pw, b, pg = await _page(rig.port)
    try:
        # 멈춤을 흉내(live 의 hb_stalled) — 서버에서 오는 다음 live 전까지
        r = await pg.evaluate("""() => {
            const t = JSON.parse(JSON.stringify(core.state.live)); t.plc.hb_stalled = true; core.applyLive(t);
            return { reason: core.lockReason(), plc: core.plcReason(), mn: core.bind('mnOpen').disabled,
                     mnTitle: core.bind('mnOpen').title,
                     exp: document.querySelector('[data-hact="export"]').disabled,
                     dl: document.querySelector('[data-dlact="folder"]').disabled,
                     note: (core.state.live.plc.prm || []).every(x => x.match === null) };
        }""")
        assert r["reason"] == "PLC 하트비트 멈춤" and r["plc"] == "PLC 하트비트 멈춤"
        assert r["mn"] is True and "멈춤" in r["mnTitle"] and r["exp"] is True and r["dl"] is True
        assert r["note"] is True
        txt = await pg.evaluate("() => { core.setTab('alarm'); return core.bind('pcNotices').textContent; }")
        assert "PLC 하트비트 멈춤" in txt and "접속하지 못했습니다" not in txt
    finally:
        await b.close()
        await pw.stop()


async def test_u5_leader_starts_at_right_end_value_on_hover(rig):
    pw, b, pg = await _page(rig.port)
    try:
        r = await pg.evaluate("""() => {
            core.setTab('trend');
            const c = viewTrend.charts.m.chart, now = Date.now();
            c.set({ series: [{ label: 'MFC3 시험', unit: 'sccm', color: '#888',
                               pts: [[now - 60000, 300, 300, 300], [now - 30000, 300, 300, 300], [now, 0, 0, 0]] }],
                    x0: now - 60000, x1: now, gap: 60000 });
            const g0 = c.debug(); const idle = g0.rows[0].want;
            const G = g0.geom; c.hoverAt(G.x0 + (G.x1 - G.x0) * 0.5, (G.y0 + G.y1) / 2);
            const g1 = c.debug();
            return { idle: idle, hover: g1.rows[0].want, text: g1.rows[0].text };
        }""")
        assert abs(r["hover"] - r["idle"]) < 0.5, r                # 지시선은 오른쪽 끝(0) 높이 그대로
        assert r["text"].startswith("300"), r                       # 커서 값은 띠의 글자로만
    finally:
        await b.close()
        await pw.stop()


async def test_u2_refill_hour_has_pressure_older_than_fast(rig):
    pw, b, pg = await _page(rig.port)
    try:
        r = await pg.evaluate("""async () => {
            core.setTab('trend');
            const orig = window.fetch;
            const now = 5000;                                   // 서버 monotonic 초
            window.fetch = async () => ({ json: async () => ({
              now: now, sec: 3600,
              slow: Array.from({ length: 3000 }, (_, i) => ({ t: now - 3000 + i, p: 1e-2, h: {}, m: {} })),
              fast: Array.from({ length: 600 }, (_, i) => ({ t: now - 60 + i * 0.1, p: 2e-2 })) }) });
            document.querySelector('[data-range="3600"]').click();
            await new Promise(r => setTimeout(r, 300));
            window.fetch = orig;
            const pts = viewTrend.charts.p.series[0].pts;
            return { first: (Date.now() - pts[0][0]) / 1000, n: pts.length };
        }""")
        assert r["first"] > 2900, r                                  # 10 분보다 오래된 압력도 있다(slow)
    finally:
        await b.close()
        await pw.stop()


# 가져온 이름 정리
_ = (alm0, table, asyncio)
