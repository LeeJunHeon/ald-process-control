"""v0.6.1 — 옛 시험 2 개 · 알람 많을 때 인터락 목록 · plc_ctl 쓰기 허용 목록 · e2e 프록시 · comm 경쟁 · 배속 범위 ·
설정 경로 · Windows 정리.

★ v0.8.0: 인터락 목록의 줄 재기 · '+N more'(B1 · B2 · B3)는 그 기능을 없애 지웠다(고정 램프판 — 모든 항목이 늘 보인다,
  test_v080). A2 는 새 fit 시험(test_v054 1 번)을, B4 는 램프판 규칙(uimodel.js UI.lamp)을 본다.
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
from test_v060 import fake_plc, _ctl, ROOT, TEST, PY, ENV  # noqa: F401 — fake_plc 는 pytest 픽스처

if TEST not in sys.path:
    sys.path.insert(0, TEST)


def _src(*parts):
    return open(os.path.join(ROOT, "frontend", *parts), encoding="utf-8").read()


def _terms():
    return "const T = require(%s);" % json.dumps(os.path.join(JS, "terms.js"))


# ===================== A2. 긴 캔버스 — 구현 글자가 아니라 동작 =====================
def test_a2_tall_canvas_behaviour():
    import test_v054
    src = open(test_v054.__file__, encoding="utf-8").read()
    body = src[src.index("def test_1_row_flex_fixes_top_row_and_grows_bottom"):]
    body = body[:body.index("\n\n\n")]
    for old in ("maxHeight", "flexGrow", "if (hh <= CANVAS_H + 1) return;", "rowFlex"):
        assert old not in body.split('"""')[-1], old                 # 옛 구현 글자를 찾지 않는다(동작을 본다)
    test_v054.test_1_row_flex_fixes_top_row_and_grows_bottom()


def test_a1_version_compare_is_numeric():
    from peald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 6, 1)   # v0.6.2: 버전이 오를 때 깨지지 않게
    src = open(os.path.join(ROOT, "tests", "test_v051.py"), encoding="utf-8").read()
    assert 'APP_VERSION.startswith("0.5.")' not in src


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
    # v0.8.0: 램프 이름(lamp)도 상태 없이 · 판정은 램프판 규칙 하나(UI.lamp)
    for k in oks:
        if not k.get("unused"):                     # SCRUB(쓰지 않음)은 램프판에 없다
            assert k.get("lamp") == k["short"], k
    out = _node(r'''
      const UI = require(%s);
      console.log(JSON.stringify([
        UI.lamp(0, { bit: 2, ok_when: true }, 'in'), UI.lamp(4, { bit: 2, ok_when: true }, 'in'),
        UI.lamp(1, { bit: 0, ok_when: false }, 'in'), UI.lamp(0, { bit: 0, ok_when: false }, 'in'),
        UI.lamp(1, { bit: 0, ok_when: null }, 'in')]));
    ''' % json.dumps(os.path.join(JS, "uimodel.js")))
    assert out == ["alarm", "ok", "alarm", "off", "ok"], out


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
