"""v0.6.3 — serve_app 시험을 동작으로 · e2e FAIL 진단(가짜 PLC 상태 · live 경과) · FAIL 이면 임시 폴더 남김 · '+N more' 문구.

★ 프로그램(headless) · 가짜 PLC 는 빈 포트 · 임시 폴더로 띄운다 — 실행 중인 프로그램과 data/ 는 건드리지 않는다.
★ 화면의 순수 함수(terms.js 의 stiMoreText, main.js 의 stiList)는 node 로 부른다(node 가 없으면 건너뜀).
"""
import os
import re
import sys
import json
import time
import shutil
import asyncio
import inspect
import subprocess
import urllib.request

from peald import addresses as A

from test_v0411 import _node, JS
from test_v054 import _fn
from test_v060 import ROOT, TEST, PY, ENV

if TEST not in sys.path:
    sys.path.insert(0, TEST)


def _health(port) -> bool:
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _wait(pred, timeout, step=0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


def _serve(h, stdin, *extra):
    """e2e 의 --serve-app 을 시험 틀의 임시 설정 · 빈 포트로 띄운다(출력은 임시 폴더)."""
    out = open(os.path.join(h.tmp, "serve.out"), "a", encoding="utf-8")
    p = subprocess.Popen([PY, os.path.join(TEST, "e2e_process.py"), "--serve-app", h.cfg_path,
                          os.path.join(h.tmp, "app"), *extra], cwd=ROOT, env=ENV, stdin=stdin, stdout=out,
                         stderr=subprocess.STDOUT)
    return p, out


def _stop(p, out):
    if p.poll() is None:
        p.kill()
        p.wait(10)
    if p.stdin:
        p.stdin.close()
    out.close()


def _tail(h):
    try:
        with open(os.path.join(h.tmp, "serve.out"), encoding="utf-8", errors="replace") as f:
            return f.read()[-1500:]
    except OSError:
        return "(출력 없음)"


# ===================== A1. serve_app 의 stdin EOF 감시 — 동작으로 =====================
def test_a1_serve_app_keeps_running_with_devnull_stdin():
    import e2e_process as E
    h = E.Harness(10, False, False)
    p, out = _serve(h, subprocess.DEVNULL)
    try:
        assert _wait(lambda: _health(h.web_port) or p.poll() is not None, 60), _tail(h)
        assert p.poll() is None and _health(h.web_port), _tail(h)
        time.sleep(3.0)                     # stdin 이 /dev/null(EOF) — --exit-on-eof 가 없으면 끝나지 않는다
        assert p.poll() is None and _health(h.web_port), _tail(h)
    finally:
        _stop(p, out)
        h.close()


def test_a1_serve_app_exits_on_stdin_eof_with_flag():
    import e2e_process as E
    h = E.Harness(10, False, False)
    p, out = _serve(h, subprocess.PIPE, "--exit-on-eof")
    try:
        assert _wait(lambda: _health(h.web_port) or p.poll() is not None, 60), _tail(h)
        assert p.poll() is None, _tail(h)
        p.stdin.close()                     # 시험 틀이 강제로 꺼진 것과 같다(파이프 EOF)
        p.wait(30)                          # 스스로 끝남(늦어도 15 s 뒤 강제 종료 타이머)
        assert not _health(h.web_port)
    finally:
        _stop(p, out)
        h.close()


# ===================== B1. e2e FAIL 진단 · FAIL 이면 임시 폴더 남김 =====================
def test_b1_fake_plc_status_has_diag_fields():
    import e2e_process as E
    h = E.Harness(10, False, False)
    try:
        h.start_plc("atm")
        st = h.ctl({"op": "status"})
        for k in ("vent_req", "in0", "ive_open_in", "ive_close_in", "atm_in", "interlock", "seq_name", "vv", "iv_e",
                  "pressure", "state_name"):
            assert k in st, (k, st)
        assert st["atm_in"] is True and st["vent_req"] is False
        assert st["atm_in"] == bool((st["in0"] >> A.IN0_ATM) & 1)
    finally:
        h.close()


def test_b1_fail_check_appends_plc_status_and_live_age_and_keeps_tmp():
    import e2e_process as E
    h = E.Harness(10, False, False)
    try:
        h.start_plc("atm")
        op = E.Operator(h)                      # 프로그램 없이 — live 는 2 s 전에 받은 것으로
        op.live = {"inputs0": 0}
        op.live_t = time.monotonic() - 2.0
        rep = E.Report()
        rep.diag = lambda: E.fail_diag(h, op)
        rep.check("manual", "준비", True, "x", "x")
        assert "‖" not in rep.rows[-1][3]                   # PASS 에는 붙이지 않는다
        # 짧은 기다림으로 시간 초과 FAIL — manual '벤트 → 대기압' 과 같은 판정
        ok = asyncio.run(op.wait(lambda: ((op.live.get("inputs0") or 0) >> A.IN0_ATM) & 1, 0.3))
        rep.check("manual", "벤트 → 대기압", ok, "대기압 입력(700 Torr↑)", "657.8 Torr")
        row = rep.rows[-1]
        assert row[4] is False
        act = row[3]
        assert act.startswith("657.8 Torr ‖ 가짜 PLC: 압력 "), act
        for part in (" Torr · VV 0 · IV-E 출력 0(리미트 열림 ", " · 닫힘 ", " · 대기압 입력 1 · 벤트 요청 0 · 인터락 0x",
                     " · 장비 ", " · 시퀀서 ", " · 프로그램 live 마지막 수신 "):
            assert part in act, (part, act)
        age = float(re.search(r"마지막 수신 (\d+\.\d) s 전", act).group(1))
        assert 2.0 <= age < 30, act
        assert "FAIL" in rep.summary() and "벤트 → 대기압" in rep.summary()
        op.live_t = None
        assert "프로그램 live: 받은 적 없음" in E.fail_diag(h, op)
        # FAIL 이 있으면 --keep 없이도 임시 폴더(가짜 PLC 기록)를 남긴다
        assert h.keep is False and E.keep_on_fail(h, rep) is True and h.keep is True
        h.close()
        assert os.path.isdir(h.tmp) and os.path.isfile(h.plc_log), h.tmp
    finally:
        h.close()
        shutil.rmtree(h.tmp, ignore_errors=True)


def test_b1_pass_only_removes_tmp_and_amain_wiring():
    import e2e_process as E
    h = E.Harness(10, False, False)
    rep = E.Report()
    rep.check("startup", "a", True, "x", "x")
    assert E.keep_on_fail(h, rep) is False
    h.close()
    assert not os.path.exists(h.tmp)
    src = inspect.getsource(E.amain)
    assert "rep.diag = lambda: fail_diag(h, op)" in src
    assert src.index("keep_on_fail(h, rep)") < src.index("h.close()")
    st = E.fail_diag(h, None)                       # 가짜 PLC 가 없어도 진단은 예외 없이
    assert "가짜 PLC 상태: 받지 못함" in st and "받은 적 없음" in st


# ===================== B2. '+N more' 문구 =====================
_PROBS = [{"name": n, "lvl": "stop"} for n in ("EMO", "Air", "Pump Alarm")] + \
         [{"name": n, "lvl": "warn"} for n in ("RF Permit", "Pump")]
_OKS = [{"name": n, "lvl": "ok"} for n in ("Lid", "Door", "N2", "CW", "Scrubber", "Exhaust")]


def test_b2_more_text_and_tooltip():
    main = open(os.path.join(ROOT, "frontend", "js", "views", "main.js"), encoding="utf-8").read()
    script = "const T = require(%s);" % json.dumps(os.path.join(JS, "terms.js")) + r'''
      const core = { esc: s => String(s).replace(/"/g, '&quot;') };
      %s
      %s
      const probs = %s, oks = %s;
      const quiet = T.stiFit(T.stiCompact(oks), 1, 3);
      const mixed = T.stiFit(T.stiCompact(probs.concat(oks)), 1, 3);
      console.log(JSON.stringify({
        quiet: T.stiMoreText(quiet.more, quiet.okHid), qn: quiet.more.length, qok: quiet.okHid.length,
        qnames: quiet.more.map(i => i.name).join(' · '),
        mixed: T.stiMoreText(mixed.more, mixed.okHid),
        okOnly: T.stiMoreText([], [{ name: 'Lid', lvl: 'ok' }]),
        warnOnly: T.stiMoreText([{ name: 'A', lvl: 'warn' }], []),
        htmlQuiet: stiList(oks, 1, 3), htmlMixed: stiList(probs.concat(oks), 1, 3),
      }));
    ''' % (_fn(main, "sti"), _fn(main, "stiList"), json.dumps(_PROBS), json.dumps(_OKS))
    out = _node(script)
    # 모두 정상인데 넘침 — 이름도 보인다
    assert out["qn"] >= 1 and out["qok"] == 0, out
    assert out["quiet"] == "OK %d — %s" % (out["qn"], out["qnames"]), out
    assert out["quiet"].count(" · ") == out["qn"] - 1, out
    # 문제 항목이 있으면 개수만(이름은 알람 · 경고)
    assert out["mixed"] == "Alarm: Pump Alarm / Warning: RF Permit · Pump / OK 6", out
    assert out["okOnly"] == "OK 1 — Lid" and out["warnOnly"] == "Warning: A", out
    # 툴팁도 'OK n'(알림과 같게) — 'OK n 개' 없음
    last = out["htmlMixed"][out["htmlMixed"].rindex('<span class="sti'):]
    assert 'title="Pump Alarm · RF Permit · Pump · OK 6"' in last and " 개" not in last, last
    lastq = out["htmlQuiet"][out["htmlQuiet"].rindex('<span class="sti'):]
    assert ('data-more="OK %d — ' % out["qn"]) in lastq and 'data-lvl="info"' in lastq, lastq
