"""v0.8.3 — 두 폴더 짝 맞춤: 가짜 PLC · plc_ctl 의 포트를 config.json 한곳에서 · 안전망 글의 '(RF 는 OFF)' 는 RF 요청이
남았을 때만 · 한쪽만 고쳤던 곳(commands.py 머리 주석 · config.py O3 끄기 지연 기본값 · tests/conftest.py) · e2e O3 라인 끄기는
바이패스까지 기다림 · e2e 레시피 저장은 내용이 바뀌면 다시. 두 폴더 비교 자체는 tests/test_pair.py.

★ 가짜 PLC 는 별도 프로세스 · 빈 포트 · 임시 설정 파일(--config)로 돌린다 — 이 폴더의 config.json · data/ 는 건드리지 않는다.
★ test_v080 · test_v0411 · test_v047 을 import 하지 않는다(그쪽 모듈 머리의 importorskip 때문에 이 파일 전체가 조용히
  건너뛰어질 수 있다). node 도우미는 이 파일에 따로 둔다.
"""
import os
import sys
import json
import time
import shutil
import socket
import subprocess
from types import SimpleNamespace

import pytest

from peald import addresses as A
from peald import config as CF
from peald import device as DEV
from peald import version
from peald.state import state

from test_v060 import _free_pair, ROOT, TEST, PY, ENV

if TEST not in sys.path:                     # import _pkg · plc_ctl (시험 도구 — 두 폴더 같은 파일)
    sys.path.insert(0, TEST)

FAKE = os.path.join(TEST, "fake_plc.py")
CTL = os.path.join(TEST, "plc_ctl.py")


def _cfg_file(tmp_path, **plc):
    """이 폴더의 예시 설정에서 plc 만 바꾼 임시 설정 파일."""
    with open(os.path.join(ROOT, "config", "config.example.json"), encoding="utf-8") as f:
        ex = json.load(f)
    ex["plc"].update(plc)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(ex, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _run(*args, timeout=60):
    r = subprocess.run([PY, *args], cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def _start(args):
    return subprocess.Popen([PY, FAKE, *args], cwd=ROOT, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")


def _ping(ctl_port) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", ctl_port), timeout=1) as s:
            s.sendall(b'{"op": "ping"}\n')
            return b'"ok"' in s.recv(200)
    except OSError:
        return False


def _stop(p, ctl_port) -> str:
    try:
        with socket.create_connection(("127.0.0.1", ctl_port), timeout=2) as s:
            s.sendall(b'{"op": "quit"}\n')
            s.recv(100)
    except OSError:
        pass
    try:
        out, _ = p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate(timeout=5)
    return out or ""


# ===================== 가짜 PLC · plc_ctl — 포트는 config.json 한곳에서 =====================
def test_fake_target_rules():
    import _pkg
    ok = {"plc": {"host": "127.0.0.1", "port": 15301, "simulate": False}}
    assert _pkg.fake_target(ok, "설정 파일") == ("127.0.0.1", 15301)
    assert _pkg.fake_target({"plc": {**ok["plc"], "host": "localhost"}}, "설정 파일") == ("localhost", 15301)
    assert _pkg.fake_target(ok, "예시 설정") is None                       # config.json 이 없어 예시 설정을 읽었다
    assert "config.json 이 없습니다" in _pkg.why_not_fake(ok, "예시 설정", "x/config.example.json")
    for bad, why in (({"simulate": True}, "simulate 가 true"), ({"host": "192.168.10.12"}, "실제 PLC 자리"),
                     ({"port": 0}, "plc.port"), ({"port": 65535}, "plc.port"), ({"port": "x"}, "plc.port")):
        c = {"plc": {**ok["plc"], **bad}}
        assert _pkg.fake_target(c, "설정 파일") is None, bad
        assert why in _pkg.why_not_fake(c, "설정 파일", "x/config.json"), bad


def test_fake_plc_and_plc_ctl_take_port_from_config(tmp_path):
    port = _free_pair()
    cfg = _cfg_file(tmp_path, host="127.0.0.1", port=port, simulate=False)
    log = tmp_path / "plc.log"
    p = _start(["--config", cfg, "--speed", "20", "--log", str(log)])            # --port 없음
    try:
        assert any(_ping(port + 1) or time.sleep(0.1) for _ in range(100)), "가짜 PLC 가 뜨지 않았다"
        code, out = _run(CTL, "--config", cfg, "status", "--count", "1")           # --host · --port 없음
        assert code == 0, out
        assert f"대상: 127.0.0.1:{port} (config.json 이 가리키는 가짜 PLC)" in out and f"@ 127.0.0.1:{port}" in out, out
        assert "설정: 설정 파일" in out, out                                     # 설정은 한 번만 읽는다(두 번째가 예시로 가지 않게)
        code, out = _run(CTL, "--config", cfg, "faults")
        assert code == 0 and "emo" in out, out
    finally:
        out = _stop(p, port + 1)
    text = log.read_text(encoding="utf-8")
    assert f"127.0.0.1:{port} · 제어 {port + 1}" in text and "경고" not in text, text
    assert "[warn]" not in out, out


@pytest.mark.parametrize("plc, why", [
    ({"host": "127.0.0.1", "port": 15999, "simulate": True}, "simulate 가 true"),
    ({"host": "192.168.10.12", "port": 502, "simulate": False}, "실제 PLC 자리"),
])
def test_fake_plc_refuses_when_config_is_not_fake(tmp_path, plc, why):
    cfg = _cfg_file(tmp_path, **plc)
    code, out = _run(FAKE, "--config", cfg, "--speed", "20", timeout=30)
    assert code == 2, out
    assert "[error] --port 가 없고" in out and why in out and "config.example.json 을 config.json 으로 복사해" in out, out


def test_fake_plc_warns_when_port_differs_from_config(tmp_path):
    port = _free_pair()
    cfg = _cfg_file(tmp_path, host="127.0.0.1", port=port + 10, simulate=False)
    log = tmp_path / "plc.log"
    p = _start(["--port", str(port), "--config", cfg, "--speed", "20", "--log", str(log)])
    try:
        assert any(_ping(port + 1) or time.sleep(0.1) for _ in range(100)), "가짜 PLC 가 뜨지 않았다"
    finally:
        out = _stop(p, port + 1)
    want = f"프로그램은 이 가짜 PLC(포트 {port})에 붙지 않습니다 — config.json 의 plc.port 는 {port + 10} 입니다"
    assert "[warn] " + want in out, out
    assert "경고 — " + want in log.read_text(encoding="utf-8")


def test_plc_ctl_target_rules(monkeypatch):
    import _pkg
    import plc_ctl
    fake = ({"plc": {"host": "127.0.0.1", "port": 15301, "simulate": False}}, "x/config.json", "설정 파일")
    example = ({"plc": {"host": "192.168.10.12", "port": 502, "simulate": True}}, "x/config.example.json", "예시 설정")
    real = ({"plc": {"host": "192.168.10.12", "port": 502, "simulate": False}}, "x/config.json", "설정 파일")

    def target(argv, loaded):
        monkeypatch.setattr(_pkg, "load_config", lambda _p="": loaded)
        a = plc_ctl.parse_args(argv + ["ping"])
        plc_ctl.resolve_target(a)
        return a.host, a.port

    assert target([], fake) == ("127.0.0.1", 15301)
    assert target([], example) == ("127.0.0.1", 502)
    assert target([], real) == ("127.0.0.1", 502)                    # 실제 PLC 에는 저절로 붙지 않는다(--host 를 직접)
    assert target(["--host", "192.168.10.12"], fake) == ("192.168.10.12", 502)
    assert target(["--port", "15311"], fake) == ("127.0.0.1", 15311)


# ===================== 안전망 글 — '(RF 는 OFF)' 는 RF 요청이 남았을 때만 =====================
def test_safety_notice_rf_hint_only_with_rf(monkeypatch):
    lk = SimpleNamespace(connected=True, status=[0] * A.STATUS_COUNT)
    lk.status[A.D_STATE] = A.STATE_IDLE
    monkeypatch.setattr(state, "link", lk)
    monkeypatch.setattr(state, "runner", None)

    def texts(pending, aux):
        monkeypatch.setattr(state, "manual_state", lambda: {"pending": pending, "aux_pending": aux})
        return [n[1] for n in state.pc_notices(True) if "PLC 가 지우지 않은 수동 요청" in n[1]]

    t = texts(["PV-1"], [])
    assert t and "그 밸브 [Close] 또는 [Close All Valves]" in t[0] and "RF" not in t[0], t
    t = texts([], ["RF"])
    if DEV.HAS_RF:
        assert t and t[0].startswith("PLC 가 지우지 않은 수동 요청: RF") and "[Close](RF 는 OFF) 또는" in t[0], t
    else:
        assert not t, t                                              # Powder 는 RF 가 없다(O3 라인은 넣지 않는다)


def _fn(src, name):
    """소스에서 함수 하나를 떼어 낸다(본문 끝 = 같은 들여쓰기의 '}') — test_v080 과 같은 도우미."""
    i = src.index("  function " + name + "(")
    line = src[i:src.index("\n", i)]
    if line.rstrip().endswith("}"):
        return line
    j = src.index("\n  }\n", i)
    return src[i:j + 4]


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 가 없다")
    r = subprocess.run([node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_pump_start_confirm_rf_hint_only_with_rf():
    with open(os.path.join(ROOT, "frontend", "js", "views", "main.js"), encoding="utf-8") as f:
        main = f.read()
    out = _node(r'''
      const T = k => k;
      var CONFIRM = {};
      const core = { state: null, bit: (w, n) => !!((w >> n) & 1), esc: s => String(s) };
      ''' + _fn(main, "auxMap") + "\n" + _fn(main, "confirmFor") + r'''
      const base = { structure: { valves: [], aux: [] }, device: {} };
      const mk = mn => Object.assign({}, base, { live: { plc: { connected: true }, manual: mn } });
      const r = [];
      core.state = mk({ pending: ['PV-1'] }); r.push(confirmFor('pump_start').b);
      core.state = mk({ pending: ['PV-1'], aux_pending: ['RF'] }); r.push(confirmFor('pump_start').b);
      console.log(JSON.stringify(r));''')
    assert "먼저 그 밸브를 Close 하거나 [Close All Valves]" in out[0] and "RF" not in out[0], out[0]
    assert "<b>PV-1 · RF</b>" in out[1] and "먼저 그 밸브를 Close(RF 는 OFF) 하거나" in out[1], out[1]


# ===================== e2e — O3 라인 끄기는 바이패스까지 =====================
def test_e2e_o3_line_off_waits_until_bypass_closed():
    """e2e 의 O3 라인 끄기는 발생기만이 아니라 바이패스 라인(IV-B · BPMP)이 닫힐 때까지 기다린다 — 서버 타이머(10 s)의
    명령 12 가 다음 시험(manual '같은 열기 두 번')의 명령 12 횟수 창에 들어가 가끔 2 번으로 실패하던 것."""
    import inspect
    import e2e_process as E
    off = inspect.getsource(E.Run.o3_line).split('action="off"')[1]
    assert '("O3GEN", "IV-B", "BPMP")' in off and "o3_on" not in off
    assert '("O3GEN", "IV-B", "BPMP")' not in inspect.getsource(E.Run.maint)       # 따로 기다리던 것을 o3_line 한곳으로


async def test_e2e_save_resaves_same_name_with_new_content():
    """e2e 레시피 저장은 이름이 같아도 내용이 다르면 다시 저장한다 — 이름만 보고 건너뛰면 전체 시나리오를 한 번에 돌릴 때
    recipe 의 'E2E TMA-H2O'(3 사이클) 뒤 maint 의 같은 이름(2 사이클)이 저장되지 않아 PLC 가 옛 레시피를 돌렸다(정비 카운터 3 대 2)."""
    import e2e_process as E
    calls = []

    class Op:
        async def reply(self, _typ, _cmd, **kw):
            calls.append(kw["recipe"]["n"])
            return {"ok": True}

    run = E.Run.__new__(E.Run)                       # 시나리오 틀만 — 프로그램 · 가짜 PLC 없이
    run.op, run.saved = Op(), {}
    run.check = lambda *a, **k: None
    assert await run.save("E2E X", {"n": 3}) and await run.save("E2E X", {"n": 3})
    assert await run.save("E2E X", {"n": 2})
    assert calls == [3, 2]                            # 같은 내용은 한 번, 바뀐 내용은 다시


# ===================== 한쪽만 고쳤던 곳 =====================
def test_o3_off_delay_default_only_on_o3_device():
    assert ("o3_off_delay_s" in CF.DEFAULTS["process"]) == DEV.HAS_O3
    if DEV.HAS_O3:
        assert CF.DEFAULTS["process"]["o3_off_delay_s"] == 10


def test_version():
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 8, 3)
