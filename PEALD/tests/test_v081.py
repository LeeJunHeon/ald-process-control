"""v0.8.1 — v0.8.0 확인 결과 고치기: 상단 띠 글자 · Heater/MFC 표 열 고정(큰 값 기준) · 밸브 띠 값 타일 고정 ·
넓은 배치 표 자리 · 시작 조건 자리 글 · 조작 창 결과 줄 · O3 ON · 공정 중 시작 조건 계산 보호.

★ test_v080 · test_v0411 · test_v047 을 import 하지 않는다(그쪽 모듈 머리의 importorskip 이 이 파일 전체를 조용히 건너뛰게
  할 수 있다) — node 도우미는 여기 따로 둔다.
★ 화면의 순수 함수(uimodel.js · faceplate.js)는 node 로 부른다(node 가 없으면 건너뜀). 브라우저 시험은 test_v080 에 보강했다.
★ 서버 시험은 가짜 링크 · 내장 시뮬레이터(test_process 의 wired 꼴) · TestClient 웹소켓 — 실행 중인 프로그램은 건드리지 않는다.
"""
import os
import re
import json
import types
import shutil
import subprocess

import pytest

from peald import addresses as A
from peald import device as DEV
from peald import logger
from peald import process as P
from peald.state import state

from conftest import wait_until
from test_process import wired, notices, pumped, short_recipe, _log, _notice  # noqa: F401 (픽스처)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "frontend", "js")


def _src(*parts):
    return open(os.path.join(ROOT, "frontend", *parts), encoding="utf-8").read()


def _j(*parts):
    return json.dumps(os.path.join(JS, *parts))


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 가 없다")
    r = subprocess.run([node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _ui(body: str, data=None):
    return _node("global.window = global; require(" + _j("fmt.js") + "); global.T = require(" + _j("terms.js") + ");\n"
                 "const UI = require(" + _j("uimodel.js") + "); global.UI = UI;\n"
                 "const DATA = " + json.dumps(data, ensure_ascii=False) + ";\n" + body)


@pytest.fixture
def struct(cfg):
    state.install_config(cfg, [], "example")
    return {"structure": state.structure(), "device": state.device_info()}


def _st(ilk=0, i0=0, state_code=A.STATE_IDLE):
    s = [0] * A.STATUS_COUNT
    s[A.D_INTERLOCK], s[A.D_INPUT0], s[A.D_STATE] = ilk, i0, state_code
    return s


# ===================== 2-1. 공정 중 시작 조건은 화면용 — 예외가 live · 데이터 로그를 깨지 않는다 =====================
def test_progress_running_survives_check_error(cfg, monkeypatch):
    state.install_config(cfg, [], "example")
    logs = []
    monkeypatch.setattr(logger, "write", lambda lv, m: logs.append((lv, m)))
    r = P.ProcessRunner(state)
    old = state.link
    # plc_hb_ok 가 없는 가짜 링크 — start_checks 가 AttributeError (v0.8.0 클라우드에서 test_v0414 등이 깨진 길)
    state.link = types.SimpleNamespace(connected=True, status=_st(state_code=A.STATE_RUN))
    try:
        out = r.progress()
        assert out["running"] is True and out["checks"] == [] and "can_start" not in out
        r.progress()
        warns = [m for lv, m in logs if lv == "warn" and "시작 조건 계산 실패" in m]
        assert len(warns) == 1 and "AttributeError" in warns[0], logs           # 같은 예외 종류는 한 실행에 한 번
        # 공정이 아니면 시작 판단이라 삼키지 않는다 — 그리고 센 것을 비워 다음 실행에서 다시 센다
        state.link.status = _st(state_code=A.STATE_IDLE)
        with pytest.raises(AttributeError):
            r.progress()
        state.link.status = _st(state_code=A.STATE_RUN)
        r.progress()
        warns = [m for lv, m in logs if lv == "warn" and "시작 조건 계산 실패" in m]
        assert len(warns) == 2
    finally:
        state.link = old


async def test_m_progress_running_checks_with_simulator(wired):
    from peald import storage, recipe as R
    lk, sim, cfg = wired
    await pumped(lk, sim)
    rec = short_recipe("v081 시작 조건")
    rec["blocks"][0]["repeat"] = 200
    assert storage.save("v081 시작 조건", rec)
    assert state.runner.select("v081 시작 조건")[0]
    state.recipe_check = R.validate(cfg, rec)
    assert (await state.runner.start(_log, _notice))[0]
    assert await wait_until(lambda: lk.status[A.D_STATE] in (A.STATE_READY, A.STATE_RUN), 5)
    p = state.runner.progress()
    assert p["running"] and p["checks"] and "can_start" not in p and "blocking" not in p
    await lk.send_command(A.CMD_ABORT)


# ===================== 2-2. manual_unlock — 그 연결에 알림(rid 붙음) =====================
def test_manual_unlock_notice_has_rid():
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from peald.server import create_app
    with TestClient(create_app("", single_instance=False), client=("127.0.0.1", 50000),
                    base_url="http://127.0.0.1:8201") as c:
        with c.websocket_connect("ws://127.0.0.1:8201/ws") as ws:
            ws.receive_json()
            for rid, on, want in ((5, True, "수동 조작 잠금 해제 — 5분 뒤 · 공정이 시작되면 다시 잠깁니다"),
                                  (6, False, "수동 조작 잠금")):
                ws.send_json({"cmd": "manual_unlock", "on": on, "rid": rid})
                got = []
                for _ in range(400):
                    m = ws.receive_json()
                    if m.get("type") in ("notice", "done"):
                        got.append(m)
                    if m.get("type") == "done" and m.get("rid") == rid:
                        break
                n = [m for m in got if m["type"] == "notice" and m.get("rid") == rid]
                assert n and n[-1]["msg"] == want and n[-1]["level"] == "info", got
    state.manual_unlock_until = 0.0


# ===================== 2-3. 장비 ID 짧은 글 =====================
async def test_m_device_id_short(wired):
    from peald.plclink import id_text
    lk, _sim, _cfg = wired
    c = [x for x in state.runner.start_checks() if x["key"] == "device_id"][0]
    assert c["ok"] and c["short"] == id_text(lk.device_id) and c["short"] in c["detail"]


# ===================== 2-4. Valve Permit 글 =====================
def test_valve_ok_detail_pump_only_and_unknown(cfg):
    state.install_config(cfg, [], "example")
    basic, pump, vok = (1 << A.ILK_BASIC), (1 << A.ILK_PUMP), (1 << A.ILK_VALVE_OK)
    ive, atm = (1 << A.IN0_IVE_OPEN), (1 << A.IN0_ATM)
    r = P.ProcessRunner(state)
    assert r._valve_ok_detail(_st(ilk=basic, i0=ive)) == "필요: Pump 운전"                # 예전: '허가됨'
    assert r._valve_ok_detail(_st(ilk=basic | pump, i0=ive)) == "필요: PLC 허가 조건 확인"
    assert r._valve_ok_detail(_st(ilk=vok)) == "허가됨"
    assert r._valve_ok_detail(_st(ilk=0, i0=atm)) == "필요: IV-E 열림 · 대기압 아님 · 기본 인터락"   # 나머지는 그대로


# ===================== 3. 상단 상태 띠 — 모델 =====================
_LIVE = {"plc": {"connected": True, "hb_ok": True, "id_state": "ok"}, "state": {"code": 3, "name": "Running"},
         "seq": {"name": "Step Run"}, "valves": 0, "aux": 0, "inputs0": 0, "inputs1": 0, "interlock": 0, "alarms": [],
         "pressure": {"cvg": 759.6}, "manual": {}, "extra": {}, "process": {}}


def test_strip_model_short_process_and_pressure(struct):
    run = {"running": True, "recipe": "R", "cycle": 12, "block_repeat": 40, "total_ms": 1000000, "remaining_ms": 477000}
    cases = {
        "run": {"process": run},
        "adopted": {"process": dict(run, adopted=True, elapsed_s=30)},
        "paused": {"state": {"code": 4, "name": "Paused"}, "process": dict(run, paused=True)},
        "base": {"state": {"code": 1, "name": "Idle"},
                 "process": {"recipe": "R", "checks": [{"key": "base", "ok": False, "cur": 759.6, "target": 0.05}]}},
    }
    out = _ui(r'''
      const r = {}; for (const k in DATA.cases) r[k] = UI.stripModel(DATA.s, Object.assign({}, DATA.base, DATA.cases[k]), false);
      console.log(JSON.stringify(r));''', {"s": dict(struct, unconfirmed=["CVG Pressure"]), "base": _LIVE, "cases": cases})
    assert out["run"]["proc"]["sub"] == "Cycle 12/40 · 00:07:57 left" and out["run"]["proc"]["subLvl"] == ""
    assert out["adopted"]["proc"]["sub"] == "Adopted · 00:07:57 left"
    assert out["paused"]["proc"]["sub"] == "Cycle 12/40 · 00:07:57 left" and out["paused"]["proc"]["subLvl"] == "warn"
    assert out["base"]["pr"]["sub"] == "Base 5.0E-2 · not reached" and out["base"]["pr"]["unconf"] is True
    assert out["base"]["pr"]["unit"] == "Torr · CVG"


# ===================== 6-2 · 6-3. 시작 조건 자리 글 =====================
def test_start_slots_short_texts(struct):
    slots = struct["structure"]["start_slots"]

    def ck(key, ok, **kw):
        return dict({"key": key, "label": key, "ok": ok, "detail": key + " 긴 글"}, **kw)
    base = [ck("plc", True), ck("device_id", True, short="0x5045 'PE'", detail="읽은 ID 0x5045 'PE' / 이 장비 …"),
            ck("state", True), ck("alarm", True), ck("pump", True), ck("valve_ok", True),
            ck("base", False, cur=759.67, target=0.05), ck("recipe", True)]
    bad_id = [ck("plc", True), ck("device_id", False, short="0x0000", detail="읽은 ID 0x0000 / 이 장비 … — ID 없음")]
    o3 = base + [ck("o3", False, action="o3_on", detail="O3 라인을 먼저 켜세요")]
    out = _ui(r'''
      const r = [DATA.a, DATA.b, DATA.c].map(ch => UI.startSlots(DATA.slots, { recipe: 'R', checks: ch }, { conn: true }));
      console.log(JSON.stringify(r));''', {"slots": slots, "a": base, "b": bad_id, "c": o3})
    lab = [s["label"] for s in slots]
    it = lambda m, name: m["items"][lab.index(name)]  # noqa: E731
    b = it(out[0], "Base Pressure")
    assert b["text"] == "759.67 → 5.0E-2" and b["icon"] == "wait"                      # 단위 · '시작 뒤 대기' 없음
    assert "759.67 → 5.0E-2 Torr · 시작 뒤 대기" in b["title"]                         # 긴 글은 title
    assert it(out[0], "Device ID")["text"] == "0x5045 'PE'"                             # 맞으면 읽은 ID
    assert it(out[1], "Device ID")["text"].startswith("읽은 ID 0x0000")                 # 아니면 detail
    if DEV.HAS_O3:
        o = it(out[2], "O3 Line")
        assert o["o3btn"] is True and o["text"] == "O3 라인을 먼저 켜세요"              # 단추가 보이면 글은 title 로(panels)
    panels = _src("js", "views", "panels.js")
    assert "if (dd) dd.hidden = !!it.o3btn;" in panels
    i = panels.index('<span class="scd"')
    assert panels.index("sc-o3", i) < panels.index("'</span></div>'", i)                # 단추는 .sct(설명 줄 자리) 안


# ===================== 4 · 5 · 6-1. 표 열 고정 · 값 타일 · 넓은 배치 · Remaining =====================
def test_tables_fixed_columns_and_texts():
    main = _src("js", "views", "main.js")
    assert "var HEATER_COLS = [null, 72, 58, 108, 108];" in main
    assert "var MFC_COLS = [null, 50, 82, 72, 62];" in main
    assert "colgroup(HEATER_COLS)" in main and "colgroup(MFC_COLS)" in main
    assert 'class="tbl fixed" data-bind="heaterTbl"' in main and 'class="tbl fixed" data-bind="mfcTbl"' in main
    # 히터 머리 5 열 — 단위 ℃ 없음(칸 이름에), Output 열 없음(Power 칩에 합침)
    head = main[main.index("colgroup(HEATER_COLS)"):main.index("</tr></thead><tbody>", main.index("colgroup(HEATER_COLS)"))]
    assert head.count("<th>") + head.count("<th title=") == 5 and "℃" not in head and "v.output" not in head
    assert "data-hout" not in main and "out_pct" in main
    assert "T('s.on') + (h.comm_ok && h.out_pct != null ? ' ' + fmt.int(h.out_pct) + ' %' : '')" in main
    assert "(!conn || h.power == null) ? fmt.DASH" in main and "T('s.off')" in main
    # MFC — Gas 말줄임 + title, 장비 줄 2/3, Powder 발생기 줄 이름
    assert '<td class="nm" title="\' + core.esc(m.gas || \'\') + \'">' in main
    assert '<td colspan="2" class="nm">' in main and '<td colspan="3" class="' in main
    assert "'Gen · Bypass Pump · IV-B'" in main and "'Generator · Bypass Pump · IV-B'" not in main
    assert "dense(tbl, room, mfc.length + xn)" in main and "dense(tbl, room, n)" in main
    # 넓은 배치 — 왼쪽 열 마지막이 Heater, 오른쪽 열 마지막이 MFC, Operation 에 Recent Events 없음
    wide = main[main.index("grid.className = 'opgrid wide';"):main.index("grid.className = 'opgrid narrow';")]
    assert wide.index("heaterP + '</div><div class=\"opcol cr\">'") > 0 and "mRoom = vh - WIDE.start - ah - 2 * GAP;" in wide
    assert "mfcP + '</div>';" in wide and "p-events" not in main and "Panels.events" not in main
    # Remaining — 공정 전 예상은 앞말 없이 흐린 색(est)
    assert "rv.classList.toggle('est', !!est);" in main and "T('v.estimate') + ' ' + fmt.hms" not in main
    css = _src("css", "style.css")
    assert ".tbl.fixed { table-layout: fixed; }" in css and ".tbl.fixed td.nm { overflow: hidden; text-overflow: ellipsis; }" in css
    assert "padding-left: 6px; padding-right: 6px;" in css
    for k, wd in (("PCV", 56), ("RF", 72), ("O3", 104), ("BPMP", 48)):
        assert '.vt.val[data-vt="value:%s"] { width: %dpx; }' % (k, wd) in css
    assert ".opgrid.wide { grid-template-columns: 1.15fr 1fr;" in css and ".opgrid.narrow .r4 { grid-template-columns: 1.2fr 1fr; }" in css
    assert "1.09fr" not in css and ".opgrid.wide .r4" not in css
    assert ".kvc .v.est { color: var(--ink-faint);" in css
    assert ("grid-template-columns: minmax(181px, 1fr) minmax(0, 1.45fr) minmax(196px, 1.2fr) minmax(150px, 1fr) "
            "minmax(175px, 1fr);") in css
    assert ('[data-layout="wide"] .strip { grid-template-columns: minmax(181px, 1fr) minmax(0, 1.6fr) minmax(196px, 1.25fr) '
            'minmax(150px, 1.3fr) minmax(175px, 1.1fr); }') in css
    assert ".strip .cell { min-width: 0; height: 78px; padding: 4px 8px;" in css
    assert re.search(r"\.strip \.kpi\.name \{[^}]*font-size: var\(--fs-xl\)", css)
    assert ".strip .u2 { display: flex; flex-direction: column;" in css
    strip = _src("js", "views", "strip.js")
    assert '<span class="u2"><span class="u">Torr · CVG</span>' in strip and 'class="unconf"' not in strip
    pid = _src("js", "views", "pid.js")
    assert "p-events" in pid                                                     # Recent Events 는 P&ID 넓은 배치에 그대로


def test_terms_v081():
    out = _node("const T = require(" + _j("terms.js") + "); console.log(JSON.stringify("
                "[T('s.ctrlAlarm'), T('strip.exh'), T('panel.heater')]));")
    assert out == ["Ctrl Alarm", "Exhaust · Vent", "Heater (℃)"]


# ===================== 7. 조작 창 =====================
def _fstate(live=None):
    s = {"access": {"local": True}, "device": {"has_rf": DEV.HAS_RF, "has_o3": DEV.HAS_O3},
         "structure": {"valves": DEV.VALVES, "aux": DEV.AUX, "interlocks": DEV.INTERLOCKS, "inputs0": DEV.INPUTS0,
                       "inputs1": DEV.INPUTS1, "mfc": [], "heaters": []},
         "recipe_limits": {"recipe_valves": []},
         "live": {"plc": {"connected": True, "id_state": "ok"}, "valves": 0, "aux": 0, "interlock": 0, "inputs0": 0,
                  "inputs1": 0, "manual": {"unlocked": True, "aux_request": 0}, "process": {"running": False},
                  "extra": {"o3_sv": 0, "o3_unit": "g/Nm3"}, "pressure": {"cvg": 1e-3}}}
    s["live"].update(live or {})
    return s


def _fp(states, body):
    return _node(r'''
      global.window = global; global.document = null;
      global.T = require(''' + _j("terms.js") + r''');
      global.Ops = require(''' + _j("ops.js") + r''');
      global.UI = require(''' + _j("uimodel.js") + r''');
      global.fmt = { flow: String, temp: String, torr: String, pct: String, watt: String, num: (v, n) => Number(v).toFixed(n) };
      const F = require(''' + _j("views", "faceplate.js") + r''');
      const S = ''' + json.dumps(states) + r''';
      const row = (m, k) => (m.rows.filter(r => r[0] === k)[0] || [])[1];
      const on = m => m.buttons.filter(x => x.a === 'on')[0].on;
      ''' + body)


def test_faceplate_pump_feedback_row():
    ax = {a["tag"]: a["bit"] for a in DEV.AUX}
    fb = next(k["bit"] for k in DEV.INPUTS0 if k["tag"] == "PMP-RUN")
    assert fb == 7                                                     # 조작 창은 입력0 b7 을 본다
    S = [_fstate(), _fstate({"aux": 1 << ax["PMP"], "inputs0": 1 << fb}), _fstate({"aux": 1 << ax["PMP"]})]
    out = _fp(S, "console.log(JSON.stringify(S.map(s => row(F.model('pump', 'PMP', s, false), 'Feedback'))));")
    assert out == ["—", "Feedback OK", "No Feedback"]                  # 꺼진 펌프는 '—'(이상처럼 보이지 않게)


def test_faceplate_o3_on_without_plc_setpoint_and_bypass_row():
    if not DEV.HAS_O3:
        pytest.skip("O3 라인이 없는 장비")
    ax = {a["tag"]: a["bit"] for a in DEV.AUX}
    bp = next(k["bit"] for k in DEV.INPUTS1 if k["tag"] == "BP-RUN")
    S = [_fstate(),                                                                    # o3_sv 0 · 라인 꺼짐
         _fstate({"manual": {"unlocked": True, "aux_request": 1 << ax["O3GEN"]}}),     # 라인 켜짐
         _fstate({"aux": 1 << ax["BPMP"], "inputs1": 1 << bp}),
         _fstate({"aux": 1 << ax["BPMP"]})]
    out = _fp(S, r'''
      const m = S.map(s => F.model('o3', 'O3', s, false));
      console.log(JSON.stringify({ on: m.map(on), why: m.map(x => x.why), bp: m.map(x => row(x, 'Bypass Pump')), note: m[0].note,
        cmd0: F.command('o3', 'O3', 'on', ''), cmd1: F.command('o3', 'O3', 'on', '0'), cmd2: F.command('o3', 'O3', 'on', '100') }));''')
    assert out["on"][0] is True and out["why"][0] == ""                 # PLC 설정값 0 이어도 ON 살아 있음
    assert out["on"][1] is False and out["why"][1] == "O3 라인이 이미 켜져 있습니다"
    assert out["bp"][0] == "OFF" and out["bp"][2] == "ON" and out["bp"][3] == "ON · No Feedback"
    assert "ON 은 입력한 Setpoint 로 켭니다" in out["note"]
    assert out["cmd0"] is None and out["cmd1"] is None                  # 비었거나 0 → '값을 넣으세요'
    assert out["cmd2"]["pair"][0] == "manual_o3" and out["cmd2"]["confirm"] == "o3"
    assert "Apply 한 뒤 ON" not in _src("js", "views", "faceplate.js")


def test_faceplate_done_clears_waiting_and_fit_closes_on_resize():
    fp = _src("js", "views", "faceplate.js")
    i = fp.index("  function onDone(msg) {")
    body = fp[i:fp.index("\n  }\n", i)]
    assert "if (!cur.noticed) result('', '');" in body
    assert "cur.noticed = true;" in fp and "cur.noticed = false;" in fp
    core = _src("js", "core.js")
    i = core.index("} else if (resized && lastState) {")
    assert "w.viewFaceplate.close(false)" in core[i:core.index("});", i)]


def test_version_081():
    from peald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 8, 1)
