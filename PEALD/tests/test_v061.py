"""v0.6.1 — 옛 시험 2 개 · 알람 많을 때 인터락 목록 · plc_ctl 쓰기 허용 목록 · e2e 프록시 · comm 경쟁 · 배속 범위 ·
설정 경로 · Windows 정리.

★ 화면의 순수 함수(terms.js 의 stiFit · stiWorst, main.js 의 stiList · snapIlks · inputChip)는 node 로 부른다
  (node 가 없으면 건너뜀). 브라우저 시험은 없다.
★ plc_ctl 은 가짜 PLC(별도 프로세스 · 빈 포트)에 대고 돌린다 — 실행 중인 프로그램과 data/ 는 건드리지 않는다.
"""
import os
import sys
import json
import time
import asyncio
import threading
import subprocess

import pytest

from peald import device as DEV
from peald import addresses as A
from peald import paths

from test_v0411 import _node, JS
from test_v054 import _fn
from test_v060 import fake_plc, _ctl, ROOT, TEST, PY, ENV  # noqa: F401 — fake_plc 는 pytest 픽스처

if TEST not in sys.path:
    sys.path.insert(0, TEST)


def _src(*parts):
    return open(os.path.join(ROOT, "frontend", *parts), encoding="utf-8").read()


def _terms():
    return "const T = require(%s);" % json.dumps(os.path.join(JS, "terms.js"))


# ===================== A2. 긴 캔버스 — 구현 글자가 아니라 동작 =====================
def test_a2_tall_canvas_behaviour():
    import test_v053
    src = open(test_v053.__file__, encoding="utf-8").read()
    body = src[src.index("def test_6_tall_canvas_gives_extra_height_to_bottom_row"):]
    body = body[:body.index("\n\n\n")]
    for old in ("maxHeight", "flexGrow", "if (hh <= CANVAS_H + 1) return;"):
        assert old not in body, old                     # 옛 구현 글자를 찾지 않는다
    test_v053.test_6_tall_canvas_gives_extra_height_to_bottom_row()


def test_a1_version_compare_is_numeric():
    from peald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 6, 1)   # v0.6.2: 버전이 오를 때 깨지지 않게
    src = open(os.path.join(ROOT, "tests", "test_v051.py"), encoding="utf-8").read()
    assert 'APP_VERSION.startswith("0.5.")' not in src


# ===================== B1. 인터락 상자 — 칸 몸통이 자르는 높이까지 18 단위로 =====================
_SNAP = r'''
  var STI_ROW = 18;
  var w = { getComputedStyle: el => ({ paddingBottom: el === body ? '4px' : '0px' }) };
  const zoom = 0.8;
  const body = { getBoundingClientRect: () => ({ top: 0, bottom: 100 + 60 }) };
  function mkBox(client) {
    return { style: {}, clientHeight: client, offsetHeight: client, closest: s => s === '.panel-body' ? body : null,
             getBoundingClientRect() { return { top: 100, height: this.offsetHeight * zoom }; } };
  }
  %s
  %s
  %s
  const a = mkBox(72);          // 상자는 72(4 줄)인데 칸 몸통 아래까지는 60 / 0.8 − 4 − 2 = 69 → 3 줄
  const ra = snapIlks(a);
  const b = mkBox(40);          // 상자가 더 작으면 상자 높이(40 → 2 줄)
  const rb = snapIlks(b);
  const c = mkBox(0);           // 못 재면(숨은 탭) 손대지 않는다
  const rc = snapIlks(c);
  console.log(JSON.stringify({ ra, ma: a.style.maxHeight, rb, mb: b.style.maxHeight, rc, mc: c.style.maxHeight }));
'''


def test_b1_snap_ilks_uses_cell_body_room():
    main = _src("js", "views", "main.js")
    out = _node(_SNAP % (_fn(main, "snapRows"), _fn(main, "ilkRoom"), _fn(main, "snapIlks")))
    assert out == {"ra": 3, "ma": "54px", "rb": 2, "mb": "36px", "rc": 0, "mc": ""}, out
    css = _src("css", "style.css")
    rule = css[css.index("#view-main .ilks.stl { grid-template-columns"):]
    rule = rule[:rule.index("}")]
    assert "min-height: 18px;" in rule and "min-height: 72px" not in rule


# ===================== B2. 줄 수에 맞춘 목록 · '+N more' =====================
_MANY = ([{"name": n, "lvl": "stop"} for n in ("EMO", "Air", "N2 Pressure", "Cooling Water", "Lid", "Pump Alarm",
                                              "Gas Leak", "Heater Overtemp Trip", "Safe Stop Request", "Basic")]
         + [{"name": n, "lvl": "warn"} for n in ("Pump", "Vacuum OK", "Start Permit", "Valve Permit", "Vent Permit")]
         + [{"name": "Pump Running", "lvl": "ok"}, {"name": "Chamber ATM", "lvl": ""}])


def _used(spans, cols):
    """순서 그대로(dense 아님) 놓을 때 쓰는 줄 수 — 두 열짜리가 줄 끝에 안 들어가면 다음 줄."""
    if not spans:
        return 0
    r, col = 1, 0
    for s in spans:
        if col + s > cols:
            r, col = r + 1, 0
        col += s
    return r


def test_b2_fit_list_to_rows_with_more_cell():
    script = _terms() + r'''
      const items = %s;
      const out = {};
      for (const [rows, cols] of [[3, 3], [4, 3], [2, 2], [7, 3]]) {
        const c = T.stiCompact(items);
        const f = T.stiFit(c, rows, cols);
        out[rows + 'x' + cols] = { items: f.items.map(i => i.name), ok: f.ok.map(i => i.name),
          more: f.more.map(i => i.name), okHid: f.okHid.map(i => i.name), worst: T.stiWorst(f.more),
          compact: c.items.map(i => i.name), okAll: c.ok.map(i => i.name),
          spans: c.items.map(i => Math.min(cols, T.stiSpan(i.name))) };
      }
      const few = T.stiFit(T.stiCompact(items.slice(0, 2).concat(items.slice(-2))), 3, 3);
      out.few = { items: few.items.map(i => i.name), ok: few.ok.map(i => i.name), more: few.more.length };
      out.worstWarn = T.stiWorst([{ lvl: 'ok' }, { lvl: 'warn' }]);
      out.worstNone = T.stiWorst([{ lvl: 'ok' }, { lvl: '' }]);
      console.log(JSON.stringify(out));
    ''' % json.dumps(_MANY)
    out = _node(script)
    for key in ("3x3", "4x3", "2x2"):
        r = out[key]
        rows, cols = (int(x) for x in key.split("x"))
        k = len(r["items"])
        assert r["items"] == r["compact"][:k], r                      # 앞에서부터(알람 먼저)
        # v0.6.2(B1): '+N more' 는 숨은 문제 항목만 센다 — 정상 묶음은 okHid(툴팁 'OK n 개')
        assert r["more"] == r["compact"][k:] and r["okHid"] == r["okAll"] and r["ok"] == [], r
        assert _used(r["spans"][:k] + [1], cols) <= rows, r            # 마지막 칸 '+N more' 까지 들어간다
        assert _used(r["spans"][:k + 1] + [1], cols) > rows, r         # 하나 더 넣으면 넘친다(들어가는 만큼은 다 보인다)
        assert r["worst"] == "stop", r                                 # 숨은 것 중 가장 나쁜 수준(알람)
    # 7 줄 × 3 열이면 문제 항목 15 개 + '+N OK' 가 다 들어간다
    r = out["7x3"]
    if _used(r["spans"] + [1], 3) <= 7:
        assert r["more"] == [] and r["items"] == r["compact"] and r["ok"] == r["okAll"], r
    assert out["few"] == {"items": ["EMO", "Air"], "ok": ["Pump Running", "Chamber ATM"], "more": 0}, out
    assert out["worstWarn"] == "warn" and out["worstNone"] == ""


def test_b2_render_more_cell_and_opens_alarm_tab():
    main = _src("js", "views", "main.js")
    script = _terms() + r'''
      const core = { esc: s => String(s) };
      %s
      %s
      const html = stiList(%s, 3, 3);
      const full = stiList(%s);
      console.log(JSON.stringify({ html, full }));
    ''' % (_fn(main, "sti"), _fn(main, "stiList"), json.dumps(_MANY), json.dumps(_MANY))
    out = _node(script)
    h = out["html"]
    assert h.count('<span class="sti') >= 3 and h.rstrip().endswith("more</span>"), h
    last = h[h.rindex('<span class="sti'):]
    # v0.6.2(B2): data-more 는 수준별 알림 글(누르면 알림 — 탭을 옮기지 않는다)
    assert 'class="sti more stop"' in last and 'data-more="Alarm: ' in last and "title=" in last, last
    assert "Basic" in last.split('title="')[1]                       # 툴팁에 숨은 이름
    assert "more" not in out["full"].replace("sti more", "")         # 줄 수를 모르면 예전처럼('+N OK')
    assert "closest('[data-more]')" in main and "core.toast(mm.getAttribute('data-more')" in main   # v0.6.2(B2)
    css = _src("css", "style.css")
    assert ".sti.more.stop > i { background: var(--stop); }" in css and ".sti.more.warn > i { background: var(--warn); }" in css
    # 인터락 목록은 잰 줄 · 열 수로 다시 그린다
    assert "stiList(out, rows, cols)" in main and "var rows = snapIlks(box), cols = stlCols(box);" in main


# ===================== B3. 줄인 목록은 dense 끔 =====================
def test_b3_problem_list_keeps_order():
    css = _src("css", "style.css")
    assert ".stl.ordered { grid-auto-flow: row; }" in css
    assert css.index(".stl.ordered { grid-auto-flow: row; }") > css.index(".stl { grid-auto-flow: row dense; }")
    main = _src("js", "views", "main.js")
    assert "box.classList.toggle('ordered', out.some(function (i) { return T.stiRank(i.lvl) < 2; }));" in main


# ===================== B4. 허가 · 정상 조건 이름은 상태 없이 =====================
def test_b4_permit_inputs_use_stateless_names():
    oks = [k for k in DEV.INPUTS0 if k.get("ok_when") is True and not k.get("momentary")]      # 순간 스위치는 목록에 없다
    assert oks
    for k in oks:
        sh = k.get("short") or ""
        assert sh and " OK" not in sh and "Closed" not in sh and not sh.endswith("OK"), k
    shorts = {k["tag"]: k["short"] for k in oks}
    assert shorts["EMO"] == "EMO" and shorts["AIR"] == "Air" and shorts["N2"] == "N2 Pressure"
    assert shorts["CW"] == "Cooling Water" and shorts["LID"] == "Lid"
    main = _src("js", "views", "main.js")
    script = r'''
      const core = { bit: (w, n) => !!((w >> n) & 1) };
      %s
      console.log(JSON.stringify([
        inputChip(0, { bit: 2, name: 'Air OK', short: 'Air', ok_when: true }, 'x'),
        inputChip(4, { bit: 2, name: 'Air OK', short: 'Air', ok_when: true }, 'x'),
        inputChip(1, { bit: 0, name: 'Pump Alarm', ok_when: false }, 'x'),
        inputChip(0, { bit: 0, name: 'Pump Alarm', ok_when: false }, 'x'),
        inputChip(1, { bit: 0, name: 'Pump Running', short: 'Pump', ok_when: null }, 'x')]));
    ''' % _fn(main, "inputChip")
    out = _node(script)
    assert [(o["name"], o["lvl"]) for o in out] == [("Air", "stop"), ("Air", "ok"), ("Pump Alarm", "stop"),
                                                    ("Pump Alarm", ""), ("Pump Running", "ok")], out


# ===================== C1. plc_ctl 쓰기 허용 목록 =====================
def _write(port, *args, inp="", timeout=60):
    r = subprocess.run([PY, os.path.join(TEST, "plc_ctl.py"), "--port", str(port), *args], cwd=ROOT, env=ENV,
                       input=inp, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def _read(port, addr):
    code, out = _ctl(port, "read", f"D{addr:05d}")
    assert code == 0, out
    return int(out.split("=")[1].split()[0])


def test_c1_write_allow_list(fake_plc):
    port, _log = fake_plc
    # 작업 이미지(D04012 수동 밸브 이미지 · D04050 보조)는 거절 — y 확인으로는 못 쓴다
    for addr in (A.D_APPLIED_VALVE, A.D_APPLIED_AUX):
        code, out = _write(port, "--yes", "write", f"D{addr:05d}", "256", inp="y\n")
        assert code == 3 and "허용 목록 밖" in out and "--raw" in out, out
    # --raw 여도 주소를 한 번 더 맞게 쳐야
    code, out = _write(port, "--yes", "--raw", "write", "D04012", "0", inp="D04013\n")
    assert code == 1 and "취소" in out, out
    code, out = _write(port, "--yes", "--raw", "write", "D04012", "0", inp="04012\n")
    assert code == 0 and "되읽은 값" in out, out
    # 명령 데이터 — 뜻을 보이고 '명령 13 을 보내야 반영'
    # v0.6.2(D2): 히터 워드(D01010~D01023)는 프로그램의 다음 명령 13 이 그대로 반영 — PRM 처럼 WRITE
    code, out = _write(port, "--yes", "write", "D01012", "600", inp="WRITE\n")
    assert code == 0 and "CH01 설정 온도" in out and "명령 13" in out and "되읽은 값 [600]" in out, out
    code, out = _write(port, "--yes", "write", "D01040", "0")
    assert code == 0 and "명령 12/13/14" in out and "되읽은 값 [0]" in out, out
    # PRM — 옛 값 → 새 값, WRITE 를 쳐야(--yes 로 건너뛰지 않음)
    old = _read(port, A.D_PRM_PC_WDT_MS)
    code, out = _write(port, "--yes", "write", "D01100", "5000", inp="y\n")
    assert code == 1 and "취소" in out and f"{old} → 5000" in out and "WRITE" in out, out
    assert _read(port, A.D_PRM_PC_WDT_MS) == old
    # 레시피 영역
    code, out = _write(port, "--yes", "write", "D02004", "7")
    assert code == 0 and "레시피 표" in out, out
    # 질문에서 입력이 끝나면(EOF) traceback 없이 '쓰지 않음'
    code, out = _write(port, "write", "D01040", "0", inp="")
    assert code == 1 and "쓰지 않음" in out and "Traceback" not in out, out
    # 앞자리 0 주소
    code, out = _ctl(port, "read", "01004", "2")
    assert code == 0 and "D01004 =" in out and "D01005 =" in out, out


def test_c1_refuse_other_device_and_attached_program(fake_plc):
    port, _log = fake_plc
    code, out = _ctl(port, "id", "1234")                 # 16 진
    assert code == 0 and '"device_id": 4660' in out, out
    code, out = _write(port, "--yes", "write", "D01040", "0")
    assert code == 3 and "장비 ID" in out and "--force" in out, out
    code, out = _write(port, "--yes", "--force", "write", "D01040", "0")
    assert code == 0, out
    code, out = _ctl(port, "id", f"h{DEV.DEVICE_ID:04X}")
    assert code == 0 and f'"device_id": {DEV.DEVICE_ID}' in out, out
    # 제어 프로그램이 붙어 있으면(PC 하트비트가 바뀜) 거절
    import plc_ctl
    stop = threading.Event()

    def heartbeat():
        mb = plc_ctl.Modbus("127.0.0.1", port)
        n = 0
        try:
            while not stop.is_set():
                n += 1
                mb.write(A.D_PC_HB, [n])
                time.sleep(0.15)
        finally:
            mb.close()
    t = threading.Thread(target=heartbeat, daemon=True)
    t.start()
    try:
        time.sleep(0.3)
        code, out = _write(port, "--yes", "write", "D01040", "0")
        assert code == 3 and "제어 프로그램" in out, out
    finally:
        stop.set()
        t.join(5)
    code, out = _write(port, "--yes", "write", "D01040", "0")
    assert code == 0, out


def test_c1_readme_section4():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    sec = readme[readme.index("### 4. 실기 시운전 때 plc_ctl"):readme.index("## 내장 PLC 시뮬레이터")]
    assert "조작 창(faceplate" in sec and "읽기 · 진단 위주" in sec
    assert "(밸브 요청 하나" not in sec and "요청만 놓음" in sec and "명령 12" in sec
    assert "동시 연결 수" in sec and "--raw" in sec and "--force" in sec
    assert "PC 통신 끊김 트립" in readme and "펌핑 · 벤트 · 승온 · O3 만" in readme


# ===================== C2. e2e 의 WebSocket 은 프록시를 타지 않는다 =====================
def test_c2_e2e_websocket_ignores_proxy(monkeypatch):
    websockets = pytest.importorskip("websockets")
    import inspect
    import conftest
    if not hasattr(conftest, "_ws_connect"):
        pytest.skip("이 websockets 에는 proxy 인자가 없다")
    import e2e_process
    src = inspect.getsource(e2e_process.Operator.connect)
    assert "proxy=None" in src and "except TypeError" in src
    # 죽은 프록시를 설정해 두고(재현 조건), conftest 의 기본값 바꾸기를 빼고 e2e 코드 그대로 붙는다
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(websockets, "connect", conftest._ws_connect)

    async def run():
        async def handler(ws, *_):
            await ws.send(json.dumps({"type": "live", "plc": {"connected": True}}))
            try:
                async for _m in ws:
                    pass
            except Exception:  # noqa: BLE001
                pass
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = next(iter(server.sockets)).getsockname()[1]

            class H:
                web_port = port
                verbose = False
            op = e2e_process.Operator(H())
            await op.connect(timeout=8)
            got = op.live.get("plc")
            await op.close()
            return got
    assert asyncio.run(run()) == {"connected": True}
    # pytest 의 다른 WebSocket 시험도 같게 — conftest 가 proxy=None 을 기본으로
    assert "kw.setdefault(\"proxy\", None)" in open(conftest.__file__, encoding="utf-8").read()


# ===================== C4. 빠른 기기 반응은 배속과 무관 =====================
def test_c4_fast_responses_are_real_time(cfg):
    import fake_plc as FP
    SIM = FP.SIM
    for m in cfg.get("mfc") or []:
        if not m.get("full_scale_sccm"):
            m["full_scale_sccm"] = 1000
    plant = FP.load_plant()

    def run(speed):
        sim = FP.PlantSim(cfg, plant, speed, seed=1)
        sim.set_state("vac")
        s1 = sim.conv.mfc.get(1)
        sim.ao[SIM.ao_mfc(1)] = s1.to_raw(500)
        sim.pulse = 0.05
        if DEV.HAS_PCV:
            sim.ao[SIM.AO_PCV] = sim.conv.pcv.to_raw(40)
        if DEV.HAS_RF:
            sim.rf_on = True
            sim.ao[SIM.AO_RF] = sim.conv.rf.to_raw(100)
        sim.heater_power = 1
        sim.heater_sv[0] = SIM.heater_raw(200.0)
        t0 = sim.heater_pv[0]
        for i in range(4):                       # 실제 0.4 s
            sim._physics(0.1 * sim.speed, float(i))
        return {"mfc": sim.mfc_pv[0], "pulse": sim.pulse, "pcv": sim.pcv_pos, "rf": sim.rf_fwd_w,
                "heat": sim.heater_pv[0] - t0}
    slow, fast = run(1.0), run(10.0)
    for k in ("mfc", "pulse", "pcv", "rf"):
        assert abs(slow[k] - fast[k]) < 1e-9, (k, slow, fast)
    assert 0 < slow["mfc"] < 500 * 0.6, slow                     # 0.4 s 에 tau 0.8 s — 아직 정착 전
    assert fast["heat"] > slow["heat"] * 3, (slow, fast)         # 승온(느린 물리)은 배속을 탄다
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "**펌핑 · 벤트 · 승온 · O3 만** `--speed` 배속" in readme


# ===================== C5. 설정 경로 =====================
def test_c5_tools_read_program_config_path(tmp_path, fake_plc):
    import _pkg
    assert not os.path.isfile(paths.DEFAULT_CONFIG_PATH)            # conftest 가 데이터 자리를 임시 폴더로
    assert _pkg.config_path("") == paths.EXAMPLE_CONFIG
    with open(paths.DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"plc": {"host": "127.0.0.1"}}, f)
    assert _pkg.config_path("") == paths.DEFAULT_CONFIG_PATH        # 프로그램과 같은 기본 경로
    other = tmp_path / "x.json"
    assert _pkg.config_path(str(other)) == os.path.abspath(str(other))
    port, _log = fake_plc
    code, out = _ctl(port, "status", "--count", "1")
    assert code == 0 and "설정: " in out, out                       # 어느 설정을 쓰는지 찍는다
    src = open(os.path.join(TEST, "fake_plc.py"), encoding="utf-8").read()
    assert "_pkg.load_config(args.config)" in src and "set_data_root" not in src


# ===================== C10. 입력 처리 =====================
def test_c10_input_parsing(monkeypatch):
    import _pkg
    assert _pkg.parse_int("01004") == 1004 and _pkg.parse_int("D01004") == 1004 and _pkg.parse_int("1004") == 1004
    assert _pkg.parse_int("0x10") == 16 and _pkg.parse_int("10h") == 16
    for t in ("5045", "0x5045", "h5045", "5045h", "H5045"):
        assert _pkg.parse_id(t) == 0x5045, t

    def eof(_p=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert _pkg.ask("> ") is None
