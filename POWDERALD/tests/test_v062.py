"""v0.6.2 — 옛 시험 4 개 · 시험 경쟁 · '+N more' 규칙 · 숨은 항목 알림 · 갱신 순서 · Vacuum 이름 · config.json BOM ·
plc_ctl 쓰기 막기(--raw · 히터 · 프로그램 붙음 · ID 0) · e2e MFC 막힘 · 끊김 중 명령 판정 · 제어 파일 · 작은 것.

★ v0.8.0: '+N more' 규칙 · 숨은 항목 알림 · 인터락 목록 재기(B1 · B2 · B3)는 그 기능을 없애 지웠다(고정 램프판 — 모든 항목이
  늘 보인다, test_v080). B2 의 core.js keydown 글자 검사(배관도 g 에 click 없음)는 남긴다. B4 는 램프 이름 규칙.
★ plc_ctl 은 가짜 PLC(별도 프로세스 · 빈 포트)에 대고 돌린다 — 실행 중인 프로그램과 data/ 는 건드리지 않는다.
"""
import os
import sys
import json
import time
import inspect
import threading
import subprocess

from powderald import device as DEV
from powderald import addresses as A
from powderald import config as C
from powderald import settings
from powderald import storage

from test_v0411 import JS  # noqa: F401
from test_v060 import fake_plc, _ctl, ROOT, TEST, PY, ENV  # noqa: F401 — fake_plc 는 pytest 픽스처

if TEST not in sys.path:
    sys.path.insert(0, TEST)


def _src(*parts):
    return open(os.path.join(ROOT, "frontend", *parts), encoding="utf-8").read()


def _terms():
    return "const T = require(%s);" % json.dumps(os.path.join(JS, "terms.js"))


def _run(script, *args, inp="", timeout=60):
    r = subprocess.run([PY, os.path.join(TEST, script), *args], cwd=ROOT, env=ENV, input=inp, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def _write(port, *args, inp=""):
    return _run("plc_ctl.py", "--port", str(port), *args, inp=inp)


def _read(port, addr):
    code, out = _ctl(port, "read", f"D{addr:05d}")
    assert code == 0, out
    return int(out.split("=")[1].split()[0])


def _fake(port, req):
    import plc_ctl
    return plc_ctl.fake_ctl(port + 1, req)


# ===================== A3. PLC 하트비트 멈춤 시험 — 첫 변화를 기다린다 =====================
def test_a3_hb_stall_test_waits_first_heartbeat():
    src = open(os.path.join(ROOT, "tests", "test_plclink.py"), encoding="utf-8").read()
    body = src[src.index("async def test_plc_hb_stall_is_detected"):]
    body = body[:body.index("\n\n\n")]
    assert "assert await wait_until(lambda: lk.plc_hb_ok, 3)" in body
    assert "\n    assert lk.plc_hb_ok\n" not in body


# ===================== B2. 배관도 요소 Enter · Space — core.js 는 data-fp 를 click 으로 바꾸지 않는다 =====================
def test_b2_more_click_shows_levels_and_keeps_tab():
    core = _src("js", "core.js")
    assert "ev.target.matches('[tabindex=\"0\"]:not(input):not(button):not(select):not([data-fp])')" in core
    assert "typeof ev.target.click === 'function'" in core
    fp = _src("js", "views", "faceplate.js")
    assert "g.tagName !== 'BUTTON'" in fp                      # 조작 창이 g · 표 줄의 Enter/Space 를 받는다(단추는 click 만)


# ===================== B4. 'Vacuum OK' → 'Vacuum' =====================
def test_b4_vacuum_short_name():
    k = next(k for k in DEV.INTERLOCKS if k["bit"] == A.ILK_VACUUM)
    assert k["tag"] == "Vacuum OK" and k["short"] == "Vacuum"            # 기록 · 툴팁 이름은 그대로
    for k in DEV.INTERLOCKS:
        nm = k.get("short") or k["tag"]
        assert not nm.endswith(" OK"), k
    for k in DEV.INTERLOCKS:
        assert not (k.get("lamp") or "").endswith(" OK"), k                # v0.8.0: 램프 이름도 상태 없이
    assert next(k for k in DEV.INTERLOCKS if k["bit"] == A.ILK_VACUUM)["lamp"] == "Vacuum"


# ===================== C1. config.json BOM · 못 읽으면 저장 거절 =====================
def _example():
    with open(os.path.join(ROOT, "config", "config.example.json"), encoding="utf-8") as f:
        return json.load(f)


def test_c1_bom_config_and_recipe(tmp_path):
    cfg = _example()
    cfg.setdefault("plc", {})["host"] = "192.0.2.10"
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8"))
    got, problems, source = C.load(str(p))
    assert source == "file" and got["plc"]["host"] == "192.0.2.10", problems
    assert not any("읽지 못" in m for _lv, m in problems), problems
    raw = settings._raw_base(got)
    assert raw["plc"]["host"] == "192.0.2.10"
    assert settings.save_block(got) == ""
    r = tmp_path / "r.json"
    r.write_bytes(b"\xef\xbb\xbf" + json.dumps({"name": "BOM", "blocks": []}).encode("utf-8"))
    assert storage.read_json(str(r)) == {"name": "BOM", "blocks": []}
    for mod in (C, settings, storage):
        assert "utf-8-sig" in inspect.getsource(mod)


def test_c1_unreadable_config_blocks_save(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{\n  "plc": {"host": "192.0.2.10",}\n}\n', encoding="utf-8")
    got, problems, source = C.load(str(p))
    msgs = " / ".join(m for _lv, m in problems)
    assert source == "example" and got.get("_unreadable") == str(p), msgs
    assert "읽지 못해 예시 설정으로" in msgs and "없어 예시" not in msgs, msgs
    assert "2 줄" in msgs and "칸" in msgs, msgs                              # 줄 · 칸 · 이유
    blk = settings.save_block(got)
    assert "읽지 못했습니다" in blk and "다시 시작하세요" in blk, blk
    res = settings.prepare(got, {"plc.host": "192.0.2.11"})
    assert not res["ok"] and any("다시 시작하세요" in e for e in res["errors"]), res["errors"]
    src = inspect.getsource(__import__("powderald.commands", fromlist=["x"])._save_blocked)
    assert "settings.save_block(state.cfg)" in src
    # 없을 때는 예전 문구 그대로
    got, problems, source = C.load(str(tmp_path / "none.json"))
    assert source == "example" and any("없어 예시" in m for _lv, m in problems) and "_unreadable" not in got


# ===================== D1 · D2 · D5. plc_ctl 확인은 누적 · 히터 워드 · ID 0 =====================
def test_d1_raw_span_still_needs_write(fake_plc):
    port, _log = fake_plc
    old = _read(port, A.D_PRM_PC_WDT_MS)
    # 주소 다시 치기만으로는 PRM(D01100)을 쓰지 않는다 — WRITE 를 따로
    code, out = _write(port, "--yes", "--raw", "write", "D01099", "0", "2", inp="D01099\n")
    assert code == 1 and "주소를 한 번 더" in out and f"{old} → 2" in out and "WRITE" in out, out
    assert _read(port, A.D_PRM_PC_WDT_MS) == old
    code, out = _write(port, "--yes", "--raw", "write", "D00999", "0", "0", "0", "0", "0", inp="D00999\nno\n")
    assert code == 1 and "취소" in out and "D01000 PC 하트비트" in out, out
    code, out = _write(port, "--yes", "--raw", "write", "D01099", "0", str(old), inp="D01099\nWRITE\n")
    assert code == 0 and f"되읽은 값 [0, {old}]" in out, out


def test_d2_heater_words_need_write_and_print_revert(fake_plc):
    port, _log = fake_plc
    old = _read(port, A.D_HEATER_POWER)
    code, out = _write(port, "--yes", "write", "D01010", "4095", inp="y\n")
    assert code == 1 and "취소" in out and _read(port, A.D_HEATER_POWER) == old, out
    code, out = _write(port, "--yes", "write", "D01010", "4095", inp="WRITE\n")
    assert code == 0 and "다음 명령 13" in out and f"{old} → 4095" in out, out
    assert f"되돌리기: python test/plc_ctl.py --port {port} write D01010 {old}" in out, out
    code, out = _write(port, "--yes", "write", "D01010", str(old), inp="WRITE\n")
    assert code == 0
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    sec = readme[readme.index("### 4. 실기 시운전 때 plc_ctl"):readme.index("## 내장 PLC 시뮬레이터")]
    assert "D01010~D01023" in sec and "명령 13" in sec


def test_d5_device_id_zero_asks_device_name(fake_plc):
    port, _log = fake_plc
    word = DEV.KEY.upper()
    try:
        assert _ctl(port, "id", "0")[0] == 0
        code, out = _write(port, "--yes", "write", "D01040", "0")
        assert code == 1 and "장비 ID 없음(0 — ID 렁 전)" in out and "--force" not in out, out
        code, out = _write(port, "--yes", "write", "D01040", "0", inp="ALD\n")
        assert code == 1 and "취소" in out, out
        code, out = _write(port, "--yes", "write", "D01040", "0", inp=word + "\n")
        assert code == 0 and "되읽은 값 [0]" in out, out
        # 0 이 아닌 다른 ID 는 예전처럼 거절
        assert _ctl(port, "id", "1234")[0] == 0
        code, out = _write(port, "--yes", "write", "D01040", "0", inp=word + "\n")
        assert code == 3 and "--force" in out, out
    finally:
        _ctl(port, "id", f"{DEV.DEVICE_ID:04X}")


# ===================== D3. 하트비트 1000 ms 주기의 프로그램도 잡는다 =====================
def test_d3_slow_heartbeat_program_is_detected(fake_plc):
    import plc_ctl
    assert plc_ctl.hb_watch_s(3000) == 1.3 and plc_ctl.hb_watch_s(6000) == 2.3
    assert plc_ctl.hb_watch_s(300) == 0.7 and plc_ctl.hb_watch_s(0) == 1.3 and plc_ctl.hb_watch_s(65535) == 1.3
    port, _log = fake_plc
    stop = threading.Event()

    def heartbeat():
        mb = plc_ctl.Modbus("127.0.0.1", port)
        n = 0
        try:
            while not stop.is_set():
                n += 1
                mb.write(A.D_PC_HB, [n])
                stop.wait(1.0)
        finally:
            mb.close()
    t = threading.Thread(target=heartbeat, daemon=True)
    t.start()
    try:
        time.sleep(0.2)
        for _ in range(3):
            code, out = _write(port, "--yes", "write", "D01040", "0")
            assert code == 3 and "제어 프로그램" in out, out
    finally:
        stop.set()
        t.join(5)
    src = inspect.getsource(plc_ctl.cmd_write)
    assert "hb2 != hb" in src                         # 쓰기 바로 직전에 하트비트를 한 번 더


# ===================== D7. 제어 파일 =====================
def test_d7_ctl_file_lines_and_position(tmp_path):
    import fake_plc as F
    ping = '{"op": "ping"}'
    assert F._lines((ping + "\r\n").encode("utf-16-le")) == [ping]
    assert F._lines(b"\xff\xfe" + (ping + "\n").encode("utf-16-le")) == [ping]
    assert F._lines(b"\xfe\xff" + (ping + "\n").encode("utf-16-be")) == [ping]
    assert F._lines(b"\xef\xbb\xbf" + (ping + "\r\n").encode("utf-8")) == [ping]
    mixed = (ping + "\r\n").encode("ascii") + b"\xff\xfe" + ('{"op": "faults"}\r\n').encode("utf-16-le")
    assert F._lines(mixed) == [ping, '{"op": "faults"}']
    assert F._lines(b"\n\r\n  \n") == []
    p = tmp_path / "ctl.txt"
    p.write_bytes(b'{"op": "quit"}\n')                        # 지난번에 남은 quit
    pos = F.ctl_start(str(p))
    assert pos == p.stat().st_size
    assert F.ctl_read(str(p), pos) == ([], pos)               # 켤 때 있던 줄은 실행하지 않는다
    with open(p, "ab") as f:
        f.write(b'{"op": "ping"}\n{"op": "sta')               # 덧붙이는 중인 줄은 다음 번에
    lines, pos = F.ctl_read(str(p), pos)
    assert lines == [ping]
    with open(p, "ab") as f:
        f.write(b'tus"}\n')
    lines, pos = F.ctl_read(str(p), pos)
    assert lines == ['{"op": "status"}']
    # '>' 로 덮어써 작아지면 처음부터
    p.write_bytes(b'{"op": "faults"}\n')
    lines, pos = F.ctl_read(str(p), pos)
    assert lines == ['{"op": "faults"}'] and pos == p.stat().st_size
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "덧붙이기(>>, Add-Content)만" in readme


# ===================== D8. 작은 것 =====================
def test_d8_speed_range_and_atm_instance(fake_plc):
    import _pkg
    import fake_plc as F
    assert _pkg.clamp_speed("5000") == 1000.0 and _pkg.clamp_speed(0) == 0.1 and _pkg.clamp_speed("2.5") == 2.5
    for bad in ("inf", "-inf", "nan"):
        try:
            _pkg.clamp_speed(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    port, _log = fake_plc
    assert _fake(port, {"op": "speed", "value": "inf"})["ok"] is False
    assert _fake(port, {"op": "speed", "value": 5000}) == {"ok": True, "speed": 1000.0}
    assert _fake(port, {"op": "speed", "value": 20})["ok"]
    code, out = _ctl(port, "speed", "inf")
    assert code == 2 and "유한한 수" in out, out
    code, out = _run("fake_plc.py", "--port", "1", "--speed", "nan")
    assert code == 2 and "유한한 수" in out, out
    # 대기압 입력 임계는 인스턴스 값 — 모듈 값(SIM.ATM_INPUT_TORR)은 그대로
    from powderald import simulator as SIM
    before = SIM.ATM_INPUT_TORR
    plant = F.load_plant()
    plant["pressure"]["atm_input_torr"] = 123.0
    sim = F.PlantSim(C.load()[0], plant, 1.0, 1)
    assert sim.atm_input_torr == 123.0 and SIM.ATM_INPUT_TORR == before
    assert "self.pressure >= self.atm_input_torr" in inspect.getsource(SIM)


def test_d8_ctl_value_address_ranges_and_config_typo(fake_plc, tmp_path):
    port, _log = fake_plc
    code, out = _write(port, "--yes", "write", "D01040", "70000")
    assert code == 2 and "-32768~65535" in out and "Traceback" not in out, out
    code, out = _write(port, "--yes", "write", "D01040", "-40000")
    assert code == 2 and "-32768~65535" in out, out
    code, out = _ctl(port, "read", "70000")
    assert code == 2 and "0~65535" in out and "Traceback" not in out, out
    code, out = _ctl(port, "read", "65535", "2")
    assert code == 2 and "0~65535" in out, out
    missing = str(tmp_path / "confg.json")
    code, out = _ctl(port, "--config", missing, "status", "--count", "1")
    assert code == 2 and "--config 파일이 없습니다" in out, out
    code, out = _run("fake_plc.py", "--port", "1", "--config", missing)
    assert code == 2 and "--config 파일이 없습니다" in out, out
    broken = tmp_path / "broken.json"
    broken.write_text("{ nope", encoding="utf-8")
    code, out = _ctl(port, "--config", str(broken), "status", "--count", "1")
    assert code == 2 and "--config 를 읽지 못했습니다" in out and "줄" in out, out


def test_d8_e2e_dual_finally_and_serve_app_flag():
    import e2e_process as E
    dual = inspect.getsource(E.Run.dual)
    assert "finally:" in dual and "except Exception" not in dual
    # v0.6.3(A1): serve_app 의 EOF 감시는 글자(독스트링이 'isatty' 를 이유로 적는다) 대신 동작으로 본다 —
    #   test_v063 test_a1_*(stdin=DEVNULL 이면 계속 뜸 · --exit-on-eof 와 stdin 닫힘이면 끝남)
    assert '"--exit-on-eof"]' in inspect.getsource(E.Harness.start_app)


# ===================== D4 · D6. e2e 고장 · 끊김 중 명령 =====================
def test_d4_fault_alarm_covers_cases_and_timing():
    import e2e_process as E
    keys = [k for k, _label in E.Run.FAULT_CASES]
    assert set(keys) <= set(E.Run.FAULT_ALARM), set(keys) - set(E.Run.FAULT_ALARM)
    assert "mfc1_stuck_prep" in keys and E.Run.FAULT_KEY["mfc1_stuck_prep"] == "mfc1_stuck"
    import fake_plc as F
    vis = {f["key"] for f in F.SIM.visible_faults()}
    assert {E.Run.FAULT_KEY.get(k, k) for k in keys} <= vis
    src = inspect.getsource(E.Run.faults)
    assert "self.state_code() == A.STATE_RUN and self.seq_code() == SEQ_STEP_RUN" in src
    assert "MFC_TIMEOUT_S + 25" in src
    assert E.MFC_TIMEOUT_S < 60 and E.A.SEQ_NAMES[E.SEQ_BLOCK_PREP] == "Block Prep" and \
        E.A.SEQ_NAMES[E.SEQ_STEP_RUN] == "Step Run"
    assert '"mfc_timeout_s"] = MFC_TIMEOUT_S' in inspect.getsource(E.Harness._write_config)


def test_d6_cut_on_next_command_number(fake_plc):
    import plc_ctl
    port, log = fake_plc
    assert _fake(port, {"op": "cut", "mode": "refuse", "s": 1, "on": "cmd"})["ok"] is False
    assert _fake(port, {"op": "cut", "mode": "blackhole", "s": 1.0, "on": "cmd"})["ok"]
    assert _fake(port, {"op": "status"})["cut"]["armed"] == ["blackhole", 1.0]
    mb = plc_ctl.Modbus("127.0.0.1", port, timeout=0.5)
    try:
        mb.write(A.D_PC_HB, [7])                                   # 명령 번호가 아니면 끊지 않는다
        no = mb.read(A.D_ACK_NO, 1)[0]
        mb.write(A.D_CMD_CODE, [A.CMD_ALARM_ACK])
        try:
            mb.write(A.D_CMD_NO, [(no + 1) & 0xFFFF])              # 블랙홀 — PLC 는 받고 답이 없다
            raise AssertionError("답이 왔다")
        except OSError:
            pass
    finally:
        mb.close()
    st = _fake(port, {"op": "status"})["cut"]
    assert st["armed"] is None and st["blackhole"] > 0, st
    time.sleep(1.3)
    text = log.read_text(encoding="utf-8")
    assert "통신 끊기 대기" in text and "명령 받음: 번호 %d · 코드 %d" % ((no + 1) & 0xFFFF, A.CMD_ALARM_ACK) in text
    import e2e_process as E
    src = inspect.getsource(E.Run.comm)
    assert '"on": "cmd"' in src and "got == 1" in src and "got == 0" in src and "받았을 수 있습니다" in src
