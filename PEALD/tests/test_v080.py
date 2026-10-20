"""v0.8.0 — 운전 화면 다시 짜기: 두 배치 · 고정 칸 · 상단 상태 띠 · P&ID 탭(포트 모델 · 검사기) · 명령 rid/done ·
진행 중 잠금 · 같은 요청 무시 · 허가 없는 밸브 열기 거절 · 알림 합치기.

★ test_v0411 · test_v047 을 import 하지 않는다(그쪽 모듈 머리의 importorskip 때문에 이 파일 전체가 조용히 건너뛰어진다).
  node 도우미 _node 는 여기 따로 둔다.
★ 화면의 순수 함수(uimodel.js · pidcheck.js · pid_model.js · faceplate.js)는 node 로 부른다(node 가 없으면 건너뜀).
★ 브라우저 시험은 이름에 browser 를 넣었다(-k "not browser" 로 뺄 수 있다 — playwright 가 없으면 건너뜀).
★ 서버 시험은 내장 시뮬레이터(test_process 의 wired 꼴)와 TestClient 웹소켓으로 — 실행 중인 프로그램은 건드리지 않는다.
"""
import os
import re
import json
import shutil
import asyncio
import threading
import subprocess

import pytest

from peald import addresses as A
from peald import commands as C
from peald import device as DEV
from peald import paths
from peald import process as P
from peald.state import state

from conftest import wait_until, free_port
from test_process import wired, notices, at_vacuum, valve_permit, heater_channels  # noqa: F401 (픽스처)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "frontend", "js")
OTHER = os.path.join(os.path.dirname(ROOT), {"PEALD": "POWDERALD", "POWDERALD": "PEALD"}.get(os.path.basename(ROOT), ""),
                     "frontend", "js")
HANGUL = re.compile("[가-힣]")


def _src(*parts):
    return open(os.path.join(ROOT, "frontend", *parts), encoding="utf-8").read()


def _j(*parts):
    return json.dumps(os.path.join(JS, *parts))


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 가 없다")
    # 스크립트는 표준 입력으로(명령줄 길이 한도 32K 를 넘지 않게)
    r = subprocess.run([node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _ui(body: str, data=None):
    """terms · fmt · uimodel 을 올리고 body 를 돌린다(DATA = data)."""
    return _node("global.window = global; require(" + _j("fmt.js") + "); global.T = require(" + _j("terms.js") + ");\n"
                 "const UI = require(" + _j("uimodel.js") + "); global.UI = UI;\n"
                 "const DATA = " + json.dumps(data, ensure_ascii=False) + ";\n" + body)


def _fn(src, name):
    """소스에서 함수 하나를 떼어 낸다(본문 끝 = 같은 들여쓰기의 '}')."""
    i = src.index("  function " + name + "(")
    line = src[i:src.index("\n", i)]
    if line.rstrip().endswith("}"):
        return line
    j = src.index("\n  }\n", i)
    return src[i:j + 4]


@pytest.fixture
def struct(cfg):
    state.install_config(cfg, [], "example")
    return {"structure": state.structure(), "device": state.device_info()}


# ===================== 0. 공통 파일 · 자산 목록 =====================
@pytest.mark.parametrize("name", ["uimodel.js", "pidcheck.js"])
def test_common_files_identical_in_both_programs(name):
    other = os.path.join(OTHER, name)
    if not os.path.isfile(other):
        pytest.skip("다른 프로그램 폴더가 없다")
    with open(os.path.join(JS, name), "rb") as a, open(other, "rb") as b:
        assert a.read() == b.read(), name


def test_index_scripts_order_and_asset_list():
    html = _src("index.html")
    refs = re.findall(r'(?:src|href)="((?:js|css)/[^"?]+)\?v=__ASSET_V__"', html)
    from peald.server import _ASSET_FILES
    missing = [r for r in refs if r not in _ASSET_FILES]
    assert refs and not missing, missing                       # 빠지면 창이 옛 파일을 캐시에서 읽을 수 있다
    order = [r for r in refs if r.endswith(".js")]
    want = ["js/terms.js", "js/fmt.js", "js/uimodel.js", "js/core.js", "js/decimate.js", "js/chart.js", "js/app.js",
            "js/pidcheck.js", "js/views/pid_model.js", "js/views/schematic.js", "js/views/panels.js", "js/views/strip.js",
            "js/views/main.js", "js/views/pid.js", "js/views/trend.js", "js/views/alarm.js", "js/views/setup.js",
            "js/views/recipe.js", "js/ops.js", "js/views/manual.js", "js/views/faceplate.js", "js/views/datalog.js"]
    assert order == want, order
    assert 'data-tab="pid" data-t="tab.pid"' in html and '<svg id="schemSvg" viewBox="0 0 920 680"' in html
    assert html.count('id="schemSvg"') == 1 and html.index('id="faceplate"') > html.index('class="statusbar"')


# ===================== 3. 창 크기 · 두 배치 =====================
LAYOUT_CASES = [((960, 1009), "narrow", 1.0, 960, 1009), ((1920, 1009), "wide", 1.2, 1600, 841),
                ((1920, 1032), "wide", 1.2, 1600, 860), ((1366, 697), "wide", 0.83, 1646, 840),
                ((1280, 1024), "narrow", 1.024, 1200, 1000), ((1000, 700), "narrow", 0.7, 1200, 1000),
                ((2560, 1080), "wide", 1.286, 1991, 840), ((640, 1024), "narrow", 0.667, 960, 1536)]


def test_pick_layout_examples_and_canvas():
    out = _ui(r'''
      console.log(JSON.stringify(DATA.map(c => { const n = UI.pickLayout(c[0], c[1]); const b = UI.layoutBox(n, c[0], c[1]);
        return [n, b.s, b.w, b.h]; })));''', [c[0] for c in LAYOUT_CASES])
    for (wh, name, s, cw, ch), got in zip(LAYOUT_CASES, out):
        assert got[0] == name and abs(got[1] - s) < 0.0015 and got[2:] == [cw, ch], (wh, got)


def test_layout_hysteresis_3_percent():
    out = _ui(r'''
      // 높이 1000 에서 폭만 바꾼다 — 1600 이면 두 배율이 같다(처음은 narrow)
      let cur = UI.pickLayout(1600, 1000); const seq = [cur];
      for (const w of [1620, 1640, 1590, 1645, 1600, 1560]) { cur = UI.pickLayout(w, 1000, cur); seq.push(cur); }
      let c2 = UI.pickLayout(1700, 1000); const back = [c2];
      for (const w of [1640, 1590, 1560, 1600, 1500]) { c2 = UI.pickLayout(w, 1000, c2); back.push(c2); }
      console.log(JSON.stringify({ seq, back, far: UI.pickLayout(1700, 1000, 'narrow') }));''')
    assert out["seq"] == ["narrow"] * 7, out                    # 경계 근처 왕복에서 바뀌지 않음(3 % 안)
    assert out["back"] == ["wide", "wide", "wide", "wide", "wide", "narrow"], out   # 1500 → narrow 가 3 % 넘게 큼
    assert out["far"] == "wide"


# ===================== 4. 상단 상태 띠 =====================
_LIVE = {"plc": {"connected": True, "hb_ok": True, "id_state": "ok"}, "state": {"code": 1, "name": "Idle"},
         "seq": {"name": "Idle"}, "valves": 0, "aux": 0, "inputs0": 0, "inputs1": 0, "interlock": 0, "alarms": [],
         "pressure": {"cvg": 759.6}, "manual": {}, "extra": {}, "process": {}}


def test_strip_model_cases(struct):
    st = struct["structure"]
    i0 = {k["tag"]: k["bit"] for k in st["inputs0"]}
    ax = {a["tag"]: a["bit"] for a in st["aux"]}
    cases = {
        "idle": {}, "ready": {"state": {"code": 2, "name": "Ready"}}, "run": {"state": {"code": 3, "name": "Running"}},
        "paused": {"state": {"code": 4, "name": "Paused"}},
        "stopping": {"state": {"code": 5, "name": "Stopping after Cycle"}, "seq": {"name": "Step Run"}},
        "safe": {"state": {"code": 6, "name": "Safe Stop"}},
        "trip": {"plc": {"connected": True, "hb_ok": True, "id_state": "ok", "pc_trip": True}},
        "id": {"plc": {"connected": True, "hb_ok": True, "id_state": "wrong"}},
        "lost": {"plc": {"connected": False}}, "stalled": {"plc": {"connected": False, "stalled": True}},
        "noaddr": {"plc": {"connected": False, "config_error": "x"}},
        "proc_run": {"state": {"code": 3, "name": "Running"},
                     "process": {"running": True, "recipe": "R", "number": 2, "cycle": 3, "block_repeat": 10,
                                 "total_ms": 100000, "remaining_ms": 25000, "checks": []}},
        "proc_paused": {"state": {"code": 4, "name": "Paused"},
                        "process": {"running": True, "paused": True, "recipe": "R", "cycle": 1, "block_repeat": 2,
                                    "total_ms": 1000, "remaining_ms": 500}},
        "proc_flow": {"process": {"recipe": "R", "phase": "base_wait", "message": "베이스 압력 대기 중"}},
        "proc_last": {"process": {"recipe": "R", "last_result": "중단 (운전자 중단)"}},
        "proc_none": {"process": {}},
        "base_bad": {"process": {"recipe": "R", "checks": [{"key": "base", "ok": False, "cur": 759.6, "target": 0.05}]}},
        "cm_run": {"state": {"code": 3, "name": "Running"}, "pressure": {"cvg": 0.5, "cm_installed": True, "cm": 0.45},
                   "process": {"running": True, "recipe": "R"}, "extra": {"pcv_pv": 40}},
        "pump_nofb": {"aux": 1 << ax["PMP"]},
        "pump_alm": {"aux": 1 << ax["PMP"], "inputs0": 1 << i0["PMP-ALM"]},
        "ive_open": {"aux": 1 << ax["IV-E"], "inputs0": 1 << i0["IVE-O"]},
        "ive_opening": {"aux": 1 << ax["IV-E"]},
        "ive_closed": {"inputs0": 1 << i0["IVE-C"]},
        "vv": {"aux": 1 << ax["VV"]},
        "alm_crit": {"alarms": [{"crit": True, "name": "EMO", "since": "10:00:00"}], "alarm_new": 1},
        "alm_warn": {"alarms": [{"crit": False, "name": "TC Alarm", "since": "09:00:00"}]},
        "alm_both": {"alarms": [{"crit": False, "name": "TC Alarm", "since": "09:00:00"},
                                {"crit": True, "name": "Gas Leak", "since": "10:00:01"},
                                {"crit": True, "name": "EMO", "since": "10:00:00"}]},
    }
    data = {"s": dict(struct, unconfirmed=["CVG Pressure"]), "base": _LIVE, "cases": cases}
    out = _ui(r'''
      const res = {};
      for (const k in DATA.cases) res[k] = UI.stripModel(DATA.s, Object.assign({}, DATA.base, DATA.cases[k]), false);
      res.offline = UI.stripModel(DATA.s, DATA.base, true);
      console.log(JSON.stringify(res));''', data)
    d = lambda k: out[k]["dev"]   # noqa: E731
    assert [d(k)["text"] for k in ("idle", "ready", "run", "paused", "stopping", "safe")] == \
        ["Idle", "Ready", "Running", "Paused", "Stopping", "Safe Stop"]
    assert [d(k)["lvl"] for k in ("idle", "ready", "run", "paused", "stopping", "safe")] == \
        ["off", "ok", "ok", "warn", "warn", "alarm"]
    assert d("stopping")["sub"].startswith("after Cycle · Sequencer · Step Run")
    assert d("trip")["subLvl"] == "warn" and d("trip")["sub"].startswith("PC Trip") and d("trip")["trip"] is True
    assert d("id")["lvl"] == "alarm" and d("id")["sub"].startswith("Device ID mismatch")
    for k, word in (("lost", "PLC Lost"), ("stalled", "PLC Stalled"), ("noaddr", "No Address"), ("offline", "Server Lost")):
        assert d(k)["text"] == word and d(k)["lvl"] == "unknown", (k, d(k))
    p = lambda k: out[k]["proc"]  # noqa: E731
    assert p("proc_none")["text"] == "No recipe selected" and p("proc_none")["dim"] is True
    assert p("proc_run")["text"] == "R · No. 2" and abs(p("proc_run")["bar"] - 0.75) < 1e-9 and p("proc_run")["barLvl"] == "ok"
    assert p("proc_run")["sub"] == "Cycle 3 / 10 · Remaining 00:00:25"
    assert p("proc_paused")["sub"].startswith("Paused · ") and p("proc_paused")["barLvl"] == "warn"
    assert p("proc_flow")["sub"] == "베이스 압력 대기 중" and p("proc_flow")["subLvl"] == "warn"
    assert p("proc_last")["last"] is True and p("proc_last")["subLvl"] == "warn"
    pr = lambda k: out[k]["pr"]  # noqa: E731
    assert pr("idle")["text"] == "759.60" and pr("idle")["unconf"] is True
    assert pr("base_bad")["sub"] == "Base 5.0E-2 Torr · not reached" and pr("base_bad")["subLvl"] == "warn"
    assert pr("cm_run")["sub"].startswith("In process") and "CM 0.450" in pr("cm_run")["sub"]
    assert ("PCV 40 %" in pr("cm_run")["sub"]) is bool(struct["device"]["has_pcv"])
    ex = lambda k: [(x["text"], x["lvl"]) for x in out[k]["exh"]]  # noqa: E731
    assert ex("idle") == [("OFF", "off"), ("Closing", "warn"), ("Closed", "off")]
    assert ex("pump_nofb")[0] == ("ON · No FB", "warn") and ex("pump_alm")[0][1] == "alarm"
    assert ex("ive_open")[1] == ("Open", "ok") and ex("ive_opening")[1] == ("Opening", "warn")
    assert ex("ive_closed")[1] == ("Closed", "off") and ex("vv")[2] == ("Open", "ok")
    assert all(x[1] == "unknown" for x in ex("lost"))
    al = lambda k: out[k]["alm"]  # noqa: E731
    assert (al("idle")["text"], al("idle")["lvl"]) == ("No Alarm", "ok")
    assert (al("alm_crit")["text"], al("alm_crit")["lvl"], al("alm_crit")["attn"]) == ("1 Critical", "alarm", True)
    assert (al("alm_warn")["text"], al("alm_warn")["lvl"]) == ("1 Warning", "warn")
    assert al("alm_both")["text"] == "2 Critical" and al("alm_both")["sub"] == "Gas Leak · 10:00:01 · Warning 1"
    assert (al("lost")["text"], al("lost")["lvl"]) == ("Alarm —", "unknown")
    # 칸 다섯 · 펌프 줄 셋은 어떤 상태든 같다
    assert all({"alm", "dev", "exh", "pr", "proc"} <= set(v) and len(v["exh"]) == 3 for v in out.values())


# ===================== 5-2. 시작 조건 자리 =====================
def test_start_slots_fixed_ten(struct):
    slots = struct["structure"]["start_slots"]
    rf = DEV.HAS_RF

    def ck(key, ok, **kw):
        return dict({"key": key, "label": key.upper(), "ok": ok, "detail": key + " 글"}, **kw)
    base_ok = [ck("plc", True), ck("device_id", True), ck("state", True), ck("alarm", True), ck("pump", True),
               ck("valve_ok", True), ck("base", True, cur=0.01, target=0.05), ck("recipe", True)]
    cases = {
        "ok": {"process": {"recipe": "R", "checks": base_ok}, "ctx": {"conn": True}},
        "lost": {"process": {}, "ctx": {"conn": False}},
        "id": {"process": {"checks": [ck("plc", True), ck("device_id", False)]}, "ctx": {"conn": True}},
        "base": {"process": {"recipe": "R", "checks": base_ok[:6] + [ck("base", False, cur=759.6, target=0.05)] + base_ok[7:]},
                 "ctx": {"conn": True}},
        "leak": {"process": {"recipe": "R", "checks": base_ok + [ck("leakcheck", False)]}, "ctx": {"conn": True}},
        "norecipe": {"process": {"checks": base_ok[:7] + [ck("recipe", False)]}, "ctx": {"conn": True}},
        "heater": {"process": {"recipe": "R", "checks": base_ok + [ck("heater", False)]}, "ctx": {"conn": True}},
        "o3": {"process": {"recipe": "R", "checks": base_ok + [ck("o3", True), ck("o3_gen", False, action="o3_on")]},
               "ctx": {"conn": True}},
        "run": {"process": {"running": True, "recipe": "R", "checks": base_ok + [ck("state", False)]}, "ctx": {"conn": True, "running": True}},
    }
    out = _ui(r'''
      const r = {}; for (const k in DATA.cases) r[k] = UI.startSlots(DATA.slots, DATA.cases[k].process, DATA.cases[k].ctx);
      console.log(JSON.stringify(r));''', {"slots": slots, "cases": cases})
    labels = [s["label"] for s in slots]
    for k, m in out.items():
        assert [i["label"] for i in m["items"]] == labels and len(labels) == 10, k        # 자리 수 · 순서 고정
    icons = lambda k: [i["icon"] for i in out[k]["items"]]  # noqa: E731
    assert icons("ok")[:8] == ["ok"] * 8 and icons("ok")[8] == "na" and out["ok"]["summary"] == {"text": "Ready", "lvl": "ok"}
    assert out["ok"]["items"][8]["text"] == "시작 조건에서 끔 (Settings)"
    if rf:
        assert out["ok"]["items"][9]["text"] == "레시피에 RF 스텝 없음"
        assert out["norecipe"]["items"][9]["text"] == "레시피를 고르면 확인"
    assert icons("lost")[0] == "no" and icons("lost")[1:] == ["unknown"] * 9
    assert out["lost"]["items"][1]["text"] == "연결되면 확인"
    assert icons("id")[:2] == ["ok", "no"] and icons("id")[2:] == ["unknown"] * 8
    assert out["id"]["items"][2]["text"] == "Device ID 를 먼저 맞추세요"
    assert icons("base")[6] == "wait" and out["base"]["summary"]["text"] == "Ready"          # base 는 막지 않는다
    assert out["base"]["items"][6]["text"] == "759.60 → 5.0E-2 Torr · 시작 뒤 대기"
    assert icons("leak")[2] == "no" and out["leak"]["summary"] == {"text": "Not ready · 1", "lvl": "alarm"}
    assert icons("heater")[8] == "no"
    if not rf:
        assert icons("o3")[9] == "no" and out["o3"]["items"][9]["o3btn"] is True
    assert out["run"]["dim"] is True and out["run"]["summary"]["text"] == "공정 중 — 다음 시작 때 확인"


# ===================== 5-3. 램프판 =====================
def test_annunciator_same_lamps_in_every_state(struct):
    st = struct["structure"]
    allbits = (1 << 16) - 1
    out = _ui(r'''
      const S = DATA.s, f = r => r.map(x => [x.group, x.items.map(i => i.key + '|' + i.name)]);
      const lv = r => r.map(x => x.items.map(i => i.lvl));
      const ok = UI.annunciator(S, { inputs0: DATA.okin, inputs1: 0, interlock: DATA.okilk }, true);
      const bad = UI.annunciator(S, { inputs0: DATA.all, inputs1: DATA.all, interlock: DATA.all }, true);
      const lost = UI.annunciator(S, {}, false);
      console.log(JSON.stringify({ a: f(ok), b: f(bad), c: f(lost), la: lv(ok), lb: lv(bad), lc: lv(lost),
        r8: UI.annRows(S, 8), r6: UI.annRows(S, 6) }));''',
              {"s": st, "all": allbits,
               "okin": sum(1 << k["bit"] for k in st["inputs0"] if k.get("ok_when") is True),
               "okilk": sum(1 << k["bit"] for k in st["interlocks"] if not k.get("bad"))})
    assert out["a"] == out["b"] == out["c"]                       # 수 · 순서 · 묶음 · 이름이 같다(상태만 다름)
    groups = [g for g, _ in out["a"]]
    assert groups == ["safety", "permit", "state", "rf" if DEV.HAS_RF else "o3"]
    assert all(v == "unknown" for row in out["lc"] for v in row)
    flat_ok = [v for row in out["la"] for v in row]
    assert "alarm" not in flat_ok and "warn" not in flat_ok[:8]
    assert out["r8"] == 4 and out["r6"] == 6
    # 레벨 규칙 표
    rules = _ui(r'''
      const ilk = { bit: 0 }, bad = { bit: 0, bad: true };
      const t = { bit: 0, ok_when: true }, f = { bit: 0, ok_when: false }, n = { bit: 0, ok_when: null };
      console.log(JSON.stringify([UI.lamp(1, ilk, 'ilk'), UI.lamp(0, ilk, 'ilk'), UI.lamp(1, bad, 'ilk'), UI.lamp(0, bad, 'ilk'),
        UI.lamp(1, t, 'in'), UI.lamp(0, t, 'in'), UI.lamp(1, f, 'in'), UI.lamp(0, f, 'in'), UI.lamp(1, n, 'in'),
        UI.lamp(0, n, 'in'), UI.lamp(null, t, 'in'), UI.lamp(undefined, ilk, 'ilk')]));''')
    assert rules == ["ok", "warn", "alarm", "off", "ok", "alarm", "alarm", "off", "ok", "off", "unknown", "unknown"]


def test_annunciator_render_one_cell_per_item(struct):
    """panels.js 의 램프판을 가짜 DOM 에 그린다 — 항목마다 칸 하나 · 레벨 클래스."""
    out = _node("global.window = global; require(" + _j("fmt.js") + "); global.T = require(" + _j("terms.js") + ");\n"
                "global.UI = require(" + _j("uimodel.js") + ");\n" + r'''
      const S = ''' + json.dumps({"structure": struct["structure"]}) + r''';
      global.core = { esc: s => String(s), bind: () => null, html: () => {}, setTab() {} };
      global.document = { addEventListener() {} };
      require(''' + _j("views", "panels.js") + r''');
      const lamps = [];
      const root = { set innerHTML(v) { this.h = v; }, get innerHTML() { return this.h; },
        querySelectorAll: sel => { const n = (root.h.match(/data-lamp="/g) || []).length;
          for (let i = 0; i < n; i++) lamps.push({ className: 'lamp unknown' }); return lamps; }, classList: { toggle() {} } };
      Panels.ann.build(root, S, { cols: 8 });
      const html = root.h;
      const t = { plc: { connected: true }, inputs0: 0, inputs1: 0, interlock: 0 };
      Panels.ann.update(root, S, t);
      console.log(JSON.stringify({ keys: (html.match(/data-lamp="([^"]+)"/g) || []).map(x => x.slice(11, -1)),
        cls: lamps.map(l => l.className), groups: (html.match(/class="ann-g"/g) || []).length }));''')
    items = [k for r in struct["structure"]["annunciator"] for k in r["items"]]
    assert out["keys"] == items and len(set(items)) == len(items)
    assert out["groups"] == len(struct["structure"]["annunciator"])
    assert set(out["cls"]) <= {"lamp ok", "lamp warn", "lamp alarm", "lamp off"} and len(out["cls"]) == len(items)


# ===================== 5-4. 밸브 띠 =====================
def test_valve_strip_states_and_targets(struct):
    st = struct["structure"]
    vb = {v["tag"]: v["bit"] for v in st["valves"]}
    ab = {a["tag"]: a["bit"] for a in st["aux"]}
    rv = [v["tag"] for v in st["valves"] if not v.get("auto")]
    live = dict(_LIVE, valves=1 << vb["PV-1"], aux=1 << ab["VV"], manual={"pending": ["PV-2"]},
                extra={"pcv_pv": 40, "rf_on": False, "o3_on": True, "o3_pv": 120.0, "o3_unit": "g/Nm3"})
    out = _ui(r'''
      const a = UI.valveStrip(DATA.s, DATA.live, true, { recipeValves: DATA.rv });
      const b = UI.valveStrip(DATA.s, {}, false, { recipeValves: DATA.rv });
      const m = g => g.groups.map(x => x.map(i => [i.key, i.state, i.value, i.fp, i.auto]));
      console.log(JSON.stringify({ a: m(a), b: m(b), pa: a.pending, pb: b.pending }));''',
              {"s": st, "live": live, "rv": rv})
    flat = {k: (s, v, fp, au) for g in out["a"] for k, s, v, fp, au in g}
    assert [[x[0] for x in g] for g in out["a"]] == st["valve_strip"]      # 묶음 · 순서 그대로
    assert flat["valve:PV-1"][0] == "open" and flat["valve:PV-2"][0] == "pending" and flat["valve:PV-3"][0] == "closed"
    assert flat["aux:VV"][:3:2] == ("open", "vent:VV") and flat["aux:IV-E"][2] == "pump:PMP"
    assert out["pa"] == ["PV-2"] and out["pb"] == []
    assert all(s == "unknown" for g in out["b"] for _k, s, _v, _f, _a in g)
    if DEV.HAS_RF:
        assert flat["value:PCV"][1:3] == ("40 %", "pcv:PCV") and flat["value:RF"][1:3] == ("OFF", "rf:RF")
    else:
        assert flat["valve:PV-B"][3] is True and flat["valve:PV-B"][2] == ""          # Auto — 누를 수 없음
        assert flat["value:O3"][1:3] == ("120.0 g/Nm3", "o3:O3") and flat["aux:IV-B"][2] == "o3:O3"
        assert flat["value:BPMP"][2] == "o3:O3"


# ===================== 6. 배관도 모델 · 검사기 =====================
def _pid(body: str):
    return _node("global.window = global; global.T = require(" + _j("terms.js") + ");\n"
                 "const C = require(" + _j("pidcheck.js") + "); const M0 = require(" + _j("views", "pid_model.js") + ");\n"
                 "const cp = () => JSON.parse(JSON.stringify(M0));\n" + body)


def test_pid_model_passes_checker():
    out = _pid("console.log(JSON.stringify({ bad: C.validate(M0), key: M0.key, W: M0.W, H: M0.H, G: M0.G }));")
    assert out["bad"] == [] and out["key"] == DEV.KEY and (out["W"], out["H"], out["G"]) == (920, 680, 8)


def test_pid_checker_catches_each_rule():
    out = _pid(r'''
      const r = {}; let m;
      // 배관 끝 4 비킴(꺾는 점이 격자 밖 · 토막이 가로세로 아님)
      m = cp(); m.pipes.find(p => p.key === 'vv').path[1] = [676, 40]; r.endoff = C.validate(m);
      // 포트가 모서리에서 8
      m = cp(); m.boxes.find(b => b.id === 'mfc1').ports.out = ['r', 8]; r.corner = C.validate(m);
      // 가로세로 아님(꺾는 점 없이 대각선)
      m = cp(); m.pipes.find(p => p.key === 'vv').path = ['ch.vv', 'n2']; r.hv = C.validate(m);
      // 상자 관통 — 열린 끝 둘 사이를 MFC1 상자 위로
      m = cp(); m.terminals.ta = { x: 8, y: 104 }; m.terminals.tb = { x: 8, y: 200 };
      m.terminals.tc = { x: 48, y: 96 }; m.terminals.td = { x: 48, y: 200 }; m.pipes.push({ key: 'thru', path: ['tc', 'td'] }); r.box = C.validate(m);
      // 4 떨어진 나란한 배관
      m = cp(); m.terminals.n1 = { x: 432, y: 148 }; m.terminals.n2b = { x: 496, y: 148 };
      m.pipes.push({ key: 'near', path: ['n1', 'n2b'] }); r.near = C.validate(m);
      // tee 없는 T — 배관 끝이 다른 배관 가운데에
      m = cp(); m.junctions.jx = { x: 296, y: 152, kind: 'tee' }; m.terminals.tx = { x: 296, y: 96 };
      m.pipes.push({ key: 'tx', path: ['tx', 'jx'] }); r.tee = C.validate(m);
      // X 교차
      m = cp(); m.terminals.x1 = { x: 456, y: 120 }; m.terminals.x2 = { x: 456, y: 176 };
      m.pipes.push({ key: 'xx', path: ['x1', 'x2'] }); r.cross = C.validate(m);
      // 밸브가 꺾인 자리
      m = cp(); const vp = m.pipes.find(p => p.key === 'vv'); m.valves.find(v => v.tag === 'VV').x = 672; r.valve = C.validate(m);
      // 글자 겹침 · 상자 밖 글자
      m = cp(); m.labels.push({ text: 'Assist N2', x: 160, y: 330, w: 64 }); r.text = C.validate(m);
      m = cp(); m.labels.push({ text: 'Far', x: 900, y: 670, w: 64 }); r.outside = C.validate(m);
      console.log(JSON.stringify(r));''')
    want = {"endoff": "grid", "corner": "corner", "hv": "hv", "box": "box", "near": "near", "tee": "tee-missing",
            "cross": "cross", "valve": "valve", "text": "text", "outside": "text"}
    for k, rule in want.items():
        assert any(b.startswith(rule + " ·") for b in out[k]), (k, out[k])
    assert any("outside drawing" in b for b in out["outside"])


def test_pid_render_uses_model_and_keeps_data_attrs():
    """schematic.js 를 가짜 DOM 에 그린다 — viewBox · data 속성 · 누르기 영역 = 검사기와 같은 수 · Tab 순서."""
    out = _node(_SCHEM_HEAD + r'''
      const C = global.PidCheck;
      const fps = all.filter(e => e.tag === 'g' && e.a['data-fp']).map(e => e.a['data-fp']);
      console.log(JSON.stringify({ vb: svg.a.viewBox, fps, hits: C.hitAreas(PidModel).length,
        pipes: all.filter(e => e.a && e.a['data-pipe'] !== undefined).map(e => e.a['data-pipe']),
        jn: all.filter(e => e.a && e.a['data-jn'] !== undefined).length,
        valves: all.filter(e => e.a && e.a['data-valve']).map(e => e.a['data-valve']),
        sv: all.filter(e => e.a && e.a['data-sv']).map(e => e.a['data-sv']),
        cm: all.some(e => e.a && e.a['data-cm']), sizes: all.filter(e => e.tag === 'text').map(e => +e.a['font-size']),
        weights: [...new Set(all.filter(e => e.tag === 'text').map(e => e.a['font-weight']))] }));''')
    assert out["vb"] == "0 0 920 680" and len(out["fps"]) == out["hits"] and len(set(out["fps"])) == len(out["fps"])
    assert "manifold" not in out["pipes"] and "pline" in out["pipes"] and "exh" in out["pipes"]
    assert out["cm"] and min(out["sizes"]) >= 13 and set(out["weights"]) <= {"400", "700"}
    for k in ("cvg", "cm", "lid", "h1", "h2", "mfc1pv", "mfc1sv"):
        assert k in out["sv"], k
    assert ("rf" in out["sv"] and "pcv" in out["sv"]) is DEV.HAS_RF and ("o3" in out["sv"]) is DEV.HAS_O3


_SCHEM_HEAD = r'''
  function El(tag) { return { tag, a: {}, kids: [], textContent: '', setAttribute(k, v) { this.a[k] = String(v); },
    getAttribute(k) { return this.a[k]; }, appendChild(c) { this.kids.push(c); return c; },
    classList: { toggle() {} }, style: {} }; }
  const svg = El('svg');
  Object.defineProperty(svg, 'innerHTML', { set() { svg.kids = []; } });
  svg.querySelector = () => null; svg.querySelectorAll = () => [];
  global.window = global;
  global.document = { createElementNS: (ns, t) => El(t), getElementById: () => svg };
  global.core = { register() {}, bind() { return null; }, state: {}, bit() { return false; }, esc: s => String(s) };
  global.fmt = { DASH: '—', flow: String, temp: String, torr: String, pct: String, watt: String, num: String };
  global.T = require(''' + _j("terms.js") + r''');
  global.UI = require(''' + _j("uimodel.js") + r''');
  global.PidCheck = require(''' + _j("pidcheck.js") + r''');
  global.PidModel = require(''' + _j("views", "pid_model.js") + r''');
  require(''' + _j("views", "schematic.js") + r''');
  viewSchematic.render({ live: {} });
  const all = []; (function walk(e) { all.push(e); e.kids.forEach(walk); })(svg);
'''


# ===================== 7. 조작 창 =====================
def _fstate(**kw):
    s = {
        "access": {"local": True}, "device": {"has_rf": DEV.HAS_RF, "has_o3": DEV.HAS_O3},
        "structure": {"valves": DEV.VALVES, "aux": DEV.AUX, "interlocks": DEV.INTERLOCKS, "inputs0": DEV.INPUTS0,
                      "inputs1": DEV.INPUTS1, "mfc": [], "heaters": []},
        "recipe_limits": {"recipe_valves": [v["tag"] for v in DEV.VALVES if not v.get("auto")]},
        "live": {"plc": {"connected": True, "id_state": "ok"}, "valves": 0, "aux": 0, "interlock": 0, "inputs0": 0,
                 "inputs1": 0, "manual": {"unlocked": True, "unlock_left_s": 200, "valve_request": 0, "valve_permit": False,
                                          "valve_permit_missing": ["Pump 운전", "IV-E 열림"]},
                 "process": {"running": False}, "extra": {}, "pressure": {"cvg": 1e-3}}}
    for k, v in kw.items():
        cur = s
        parts = k.split("__")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = v
    return s


def _fp(body: str):
    return _node(r'''
      global.window = global; global.document = null;
      global.T = require(''' + _j("terms.js") + r''');
      global.Ops = require(''' + _j("ops.js") + r''');
      global.UI = require(''' + _j("uimodel.js") + r''');
      global.fmt = { flow: v => Number(v).toFixed(1), temp: v => Number(v).toFixed(1), torr: String,
                     pct: v => String(Math.round(v)), watt: v => Number(v).toFixed(1), num: (v, n) => Number(v).toFixed(n) };
      const F = require(''' + _j("views", "faceplate.js") + r''');
      const B = m => m.buttons.map(x => [x.a, x.on]);
      ''' + body)


def test_faceplate_valve_permit_pending_and_busy():
    vb = DEV.valve_bit("PV-1")
    vp = next(k["bit"] for k in DEV.INTERLOCKS if k["tag"] == "Valve Permit")
    nop = _fstate()
    ok = _fstate(live__interlock=1 << vp, live__manual={"unlocked": True, "valve_request": 0, "valve_permit": True,
                                                         "valve_permit_missing": []})
    req = _fstate(live__manual={"unlocked": True, "valve_request": 1 << vb, "valve_permit": False,
                                "valve_permit_missing": ["Pump 운전"]})
    opened = _fstate(live__valves=1 << vb, live__manual={"unlocked": True, "valve_request": 1 << vb, "valve_permit": True})
    out = _fp(r'''
      const S = ''' + json.dumps([nop, ok, req, opened]) + r''';
      const m = S.map(s => F.model('valve', 'PV-1', s, false)), busy = F.model('valve', 'PV-1', S[1], false, true);
      console.log(JSON.stringify({ b: m.map(B), why: m.map(x => [x.why, x.whyLvl]), busy: [B(busy), busy.why],
                                   rows: m[2].rows }));''')
    assert out["b"][0] == [["open", False], ["close", False]]                        # 허가 없음 → Open 막힘
    assert out["why"][0][0] == "Valve Permit 이 없어 열 수 없습니다 — 필요: Pump 운전 · IV-E 열림"
    assert out["b"][1] == [["open", True], ["close", False]] and out["why"][1] == ["", ""]
    assert out["b"][2] == [["open", False], ["close", True]]                         # pending → Close 살아 있음 + 노랑
    assert out["why"][2][1] == "warn" and out["why"][2][0].startswith("요청은 남아 있지만 Valve Permit 이 없어")
    assert out["b"][3] == [["open", False], ["close", True]]                         # 이미 요청 · 열림 → Open 막힘
    assert out["busy"] == [[["open", False], ["close", False]], "응답을 기다리는 중"]
    assert ["Valve Permit", "No — Pump 운전"] in out["rows"] and ["Request", "Open"] in out["rows"]


def test_faceplate_o3_rf_pump_vent_rules():
    ax = {a["tag"]: a["bit"] for a in DEV.AUX}
    pmp_on = _fstate(live__aux=(1 << ax["PMP"]) | (1 << ax["IV-E"]))
    stopped = _fstate()
    vent = _fstate(live__aux=1 << ax["VV"])
    out = _fp(r'''
      const S = ''' + json.dumps([pmp_on, stopped, vent]) + r''';
      console.log(JSON.stringify({ run: B(F.model('pump', 'PMP', S[0], false)), stop: B(F.model('pump', 'PMP', S[1], false)),
        runWhy: F.model('pump', 'PMP', S[0], false).why, vent: B(F.model('vent', 'VV', S[2], false)),
        ventWhy: F.model('vent', 'VV', S[2], false).why, vent0: B(F.model('vent', 'VV', S[1], false)) }));''')
    assert out["run"] == [["pump_start", False], ["pump_stop", True]] and out["runWhy"] == "이미 펌핑 중입니다"
    assert out["stop"] == [["pump_start", True], ["pump_stop", False]]
    assert out["vent"] == [["vent", False]] and out["ventWhy"] == "이미 벤트 중입니다" and out["vent0"] == [["vent", True]]
    if DEV.HAS_O3:
        gen = ax["O3GEN"]
        off = _fstate(live__extra={"o3_sv": 50.0, "o3_unit": "g/Nm3"})                 # O3 Permit 없음 · 라인 꺼짐 · 설정값 있음
        on = _fstate(live__extra={"o3_sv": 50.0}, live__manual={"aux_request": 1 << gen, "unlocked": True})
        nosv = _fstate(live__extra={"o3_sv": 0})
        o = _fp(r'''
          const S = ''' + json.dumps([off, on, nosv]) + r''';
          const on = m => m.buttons.filter(x => x.a === 'on')[0].on;
          console.log(JSON.stringify(S.map(s => { const m = F.model('o3', 'O3', s, false); return [on(m), m.why]; })));''')
        assert o[0] == [True, ""], o                                                # ★ O3 Permit 으로 막지 않는다
        assert o[1][0] is False and o[1][1] == "O3 라인이 이미 켜져 있습니다"
        assert o[2][0] is False
    else:
        bit = next(k["bit"] for k in DEV.INTERLOCKS if k["tag"] == "RF Permit")
        r = _fp(r'''
          const S = ''' + json.dumps([_fstate(), _fstate(live__interlock=1 << bit)]) + r''';
          const on = m => m.buttons.filter(x => x.a === 'on')[0].on;
          console.log(JSON.stringify(S.map(s => on(F.model('rf', 'RF', s, false)))));''')
        assert r == [False, True]                                                   # RF 는 허가가 있어야 ON(그대로)


def test_pump_start_confirm_when_request_left():
    main = _src("js", "views", "main.js")
    vb = DEV.valve_bit("PV-1")
    out = _node(r'''
      const T = k => k;
      var CONFIRM = {};
      const core = { state: null, bit: (w, n) => !!((w >> n) & 1), esc: s => String(s) };
      ''' + _fn(main, "auxMap") + "\n" + _fn(main, "confirmFor") + r'''
      const base = { structure: { valves: ''' + json.dumps(DEV.VALVES) + r''', aux: [] }, device: {} };
      const mk = mn => Object.assign({}, base, { live: { plc: { connected: true }, manual: mn } });
      const r = [];
      core.state = mk({ valve_request: 0, valve_permit: false }); r.push(confirmFor('pump_start'));
      core.state = mk({ valve_request: ''' + str(1 << vb) + r''', valve_permit: true }); r.push(confirmFor('pump_start'));
      core.state = mk({ valve_request: ''' + str(1 << vb) + r''', valve_permit: false }); r.push(confirmFor('pump_start'));
      console.log(JSON.stringify(r));''')
    assert out[0] is None and out[1] is None
    assert "수동 밸브 요청이 남아 있습니다: <b>PV-1</b>" in out[2]["b"] and out[2]["ok"] == "b.pumpStart"
    assert "그대로 펌핑할까요?" in out[2]["b"]


# ===================== 8-1. 알림 합치기 · 진행 중 잠금 =====================
def test_toast_merge_and_busy_store():
    out = _ui(r'''
      let L = [];
      const add = (m, l) => { const r = UI.toastMerge(L, m, l, 4); L = r.list; return r; };
      add('A', 'ok'); add('B', 'ok'); const r3 = add('A', 'ok'); const r4 = add('A', 'warn');
      const merged = { list: L.map(x => [x.msg, x.level, x.n]), r3: [r3.merged, UI.toastText(r3.item)], r4: r4.merged };
      add('C', 'ok'); const r6 = add('D', 'ok');
      merged.after = L.map(x => x.msg + x.level); merged.dropped = r6.dropped.map(x => x.msg);
      let now = 1000; const b = UI.busyStore({ ttl: 8000, now: () => now });
      b.start('valve:PV-1', 7); b.start('heater:2', 7); b.start('mfc', 8);
      const s1 = [b.has('valve:PV-1'), b.has('heater:2'), b.has('mfc'), b.has('pcv')];
      const unknown = b.done(99);
      const d7 = b.done(7); const s2 = [b.has('valve:PV-1'), b.has('heater:2'), b.has('mfc')];
      now += 8001; const s3 = b.has('mfc');
      b.start('o3', 3); b.clear(); const s4 = b.has('o3');
      merged.busy = { s1, unknown, d7, s2, s3, s4 };
      console.log(JSON.stringify(merged));''')
    assert out["list"] == [["B", "ok", 1], ["A", "ok", 2], ["A", "warn", 1]]        # 같은 글 · 수준만 합친다(맨 아래로)
    assert out["r3"] == [True, "A  ×2"] and out["r4"] is False
    assert out["after"] == ["Aok", "Awarn", "Cok", "Dok"] and out["dropped"] == ["B"]   # 4 개 넘으면 오래된 것부터
    b = out["busy"]
    assert b["s1"] == [True, True, True, False] and b["unknown"] is False and b["d7"] is True
    assert b["s2"] == [False, False, True] and b["s3"] is False and b["s4"] is False   # 8 s 만료 · clear


def test_app_send_rid_and_dispatch_done():
    app = _src("js", "app.js")
    assert "obj.rid = lastRid;" in app and "return lastRid;" in app and "return 0;" in app
    assert "case 'done':" in app and "core.busy.done(msg.rid);" in app and "viewFaceplate.onDone(msg)" in app
    core = _src("js", "core.js")
    assert "busy.clear();" in core and "var busy = w.UI.busyStore({ ttl: 8000 });" in core
    assert "w.UI.toastMerge(toastList" in core
    man = _src("js", "views", "manual.js")
    assert "heaterBusy" not in man and "w.app.send(" not in man and "core.busy.start(k, rid)" in man
    fp = _src("js", "views", "faceplate.js")
    assert "msg.rid !== cur.rid" in fp and "sentAt" not in fp


# ===================== 8-2. 서버 — rid · done =====================
def _client(host="127.0.0.1"):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from peald.server import create_app
    return TestClient(create_app("", single_instance=False), client=(host, 50000), base_url="http://127.0.0.1:8201")


def _until_done(ws, rid, n=400):
    got = []
    for _ in range(n):
        m = ws.receive_json()
        if m.get("type") in ("notice", "done"):
            got.append(m)
        if m.get("type") == "done" and m.get("rid") == rid:
            return got
    raise AssertionError(f"done {rid} 이 오지 않았다: {got}")


def test_ws_rid_done_and_notice_rid(monkeypatch):
    def boom(d, ws):
        raise RuntimeError("시험")

    async def aboom(d, ws):
        boom(d, ws)
    monkeypatch.setitem(C._HANDLERS, "v080_boom", aboom)
    with _client() as c:
        with c.websocket_connect("ws://127.0.0.1:8201/ws") as ws:
            ws.receive_json()
            ws.send_json({"cmd": "ping", "rid": 7})
            got = _until_done(ws, 7)
            assert got[-1] == {"type": "done", "rid": 7, "cmd": "ping"}
            ws.send_json({"cmd": "nope_v080", "rid": 8})
            got = _until_done(ws, 8)
            n = [m for m in got if m["type"] == "notice"]
            assert n and n[-1]["rid"] == 8 and "알 수 없는 명령" in n[-1]["msg"] and got[-1]["type"] == "done"
            ws.send_json({"cmd": "v080_boom", "rid": 9})                 # 예외가 나도 done
            got = _until_done(ws, 9)
            assert any(m["type"] == "notice" and m.get("rid") == 9 and "처리하지 못했습니다" in m["msg"] for m in got)
            # rid 가 없거나 이상하면 done 없음 — 뒤에 보낸 정상 rid 의 done 만 온다
            for bad in (None, True, -1, "3", 0, 2 ** 31):
                ws.send_json({"cmd": "ping"} if bad is None else {"cmd": "ping", "rid": bad})
            ws.send_json({"cmd": "ping", "rid": 10})
            got = _until_done(ws, 10)
            assert [m for m in got if m["type"] == "done"] == [{"type": "done", "rid": 10, "cmd": "ping"}], got


def test_ws_remote_refusal_also_done():
    with _client("192.0.2.55") as c:
        with c.websocket_connect("ws://127.0.0.1:8201/ws") as ws:
            ws.receive_json()
            ws.send_json({"cmd": "pump_start", "rid": 21})
            got = _until_done(ws, 21)
            assert any(m["type"] == "notice" and m.get("rid") == 21 and "보기 전용" in m["msg"] for m in got), got


def test_broadcast_notice_has_no_rid():
    from peald import connection as CN
    sent = []

    class M:
        async def broadcast(self, obj):
            sent.append(obj)

        async def send_to(self, ws, obj):
            sent.append(obj)
    old = CN.manager
    CN.manager = M()
    tok = CN.CURRENT_RID.set(5)
    try:
        asyncio.run(CN.push_notice("x", "info"))
        asyncio.run(CN.push_notice("y", "info", ws=object()))
    finally:
        CN.CURRENT_RID.reset(tok)
        CN.manager = old
    assert "rid" not in sent[0] and sent[1]["rid"] == 5


# ===================== 8-2. 서버 — 수동 밸브(내장 시뮬레이터) =====================
def _plc_words(sim):
    return (sim.reg[A.D_CMD_NO], sim.reg[A.D_MANUAL_VALVE], sim.reg[A.D_APPLIED_VALVE])


def _maint(cfg):
    from peald.maintenance import maint
    return {r["key"]: r["value"] for r in maint.rows(cfg)}


async def test_m_manual_valve_permit_same_request_and_readback(wired, notices):
    lk, sim, cfg = wired
    await at_vacuum(lk, sim)
    state.manual_unlock_until = 9e9
    tag = "PV-1"
    # ① 대기압 아님 · 펌프 꺼짐 — 허가 없음 → 보내지 않음(명령 번호 · D01004 · D04012 그대로), 정비 카운터 그대로
    w0, m0 = _plc_words(sim), _maint(cfg)
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": True})
    warn = [m for lv, m in notices if lv == "warn"]
    assert any("Valve Permit 이 없어 보내지 않았습니다 — 필요: Pump 운전 · IV-E 열림" in m for m in warn), notices
    await asyncio.sleep(0.2)
    assert _plc_words(sim) == w0 and _maint(cfg) == m0
    assert tag not in state.manual_state()["pending"]
    # ② 허가 뒤 열기 → 되읽은 출력으로 '열림 확인'
    await valve_permit(lk, sim)
    notices.clear()
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": True})
    assert ("ok", f"{tag} 열림 확인") in notices, notices
    # ③ 같은 열기 다시 → info · 명령 번호 그대로
    no = sim.reg[A.D_CMD_NO]
    notices.clear()
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": True})
    assert any(lv == "info" and "이미 열기 요청 상태입니다 — 다시 보내지 않았습니다" in m for lv, m in notices), notices
    assert sim.reg[A.D_CMD_NO] == no and not [m for lv, m in notices if lv == "warn"]
    # ④ 닫기 → '닫힘 확인' ⑤ 닫기 다시 → info
    notices.clear()
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": False})
    assert ("ok", f"{tag} 닫힘 확인") in notices, notices
    no = sim.reg[A.D_CMD_NO]
    notices.clear()
    await C.handle_command({"cmd": "manual_valve", "tag": tag, "on": False})
    assert any(lv == "info" and "이미 닫힘(요청 없음)" in m for lv, m in notices) and sim.reg[A.D_CMD_NO] == no


async def test_m_pending_notice_after_pump_stop(wired, notices):
    lk, sim, _cfg = wired
    await valve_permit(lk, sim)
    state.manual_unlock_until = 9e9
    await C.handle_command({"cmd": "manual_valve", "tag": "PV-1", "on": True})
    assert await wait_until(lambda: A.bit(sim.reg[A.D_VALVE_OUT], DEV.valve_bit("PV-1")), 2)
    r, _ = await lk.send_command(A.CMD_PUMP_STOP)
    assert r == A.RESULT_OK
    assert await wait_until(lambda: "PV-1" in state.manual_state()["pending"], 5)
    pn = [n for n in state.pc_notices(True) if n[1].startswith("수동 밸브 요청이 허가를 기다립니다: PV-1")]
    assert pn and pn[0][0] == "warn" and "[Close All Valves]" in pn[0][1]
    ms = state.manual_state()
    assert ms["valve_permit"] is False and "Pump 운전" in ms["valve_permit_missing"]
    r, _ = await lk.send_command(A.CMD_ALL_CLOSE)
    assert r == A.RESULT_OK
    assert await wait_until(lambda: not state.manual_state()["pending"], 3)
    assert not [n for n in state.pc_notices(True) if "허가를 기다립니다" in n[1]]


def _st(ilk=0, i0=0, a0=0, a1=0):
    s = [0] * A.STATUS_COUNT
    s[A.D_INTERLOCK], s[A.D_INPUT0], s[A.D_ALARM0], s[A.D_ALARM1] = ilk, i0, a0, a1
    return s


def test_valve_permit_missing_rules():
    basic, pump, vok, ssr = (1 << A.ILK_BASIC), (1 << A.ILK_PUMP), (1 << A.ILK_VALVE_OK), (1 << A.ILK_SAFE_STOP_REQ)
    ive, atm, emo = (1 << A.IN0_IVE_OPEN), (1 << A.IN0_ATM), (1 << A.IN0_EMO)
    assert P.valve_permit_missing(_st(ilk=vok)) == []
    assert P.valve_permit_missing(_st(ilk=basic, i0=atm)) == ["Pump 운전", "IV-E 열림", "대기압 아님"]
    assert P.valve_permit_missing(_st(ilk=0, i0=ive)) == ["기본 인터락", "Pump 운전"]
    assert P.valve_permit_missing(_st(ilk=basic | pump | ssr, i0=ive | emo, a0=1 << A.ALM0_PC_LINK)) == \
        ["PC 끊김 트립 — Alarm Reset"]
    assert P.valve_permit_missing(_st(ilk=basic | pump | ssr, i0=ive | emo, a0=(1 << A.ALM0_PC_LINK) | 1)) == \
        ["안전 정지 요구 해제"]
    assert P.valve_permit_missing(_st(ilk=basic | pump, i0=ive)) == ["PLC 허가 조건 확인"]
    # 시작 조건 글은 예전 순서 · 문구(Pump 없음 · 기본 인터락 맨 뒤)
    from peald.process import ProcessRunner
    r = ProcessRunner(state)
    assert r._valve_ok_detail(_st(ilk=0, i0=atm)) == "필요: IV-E 열림 · 대기압 아님 · 기본 인터락"
    assert r._valve_ok_detail(_st(ilk=vok)) == "허가됨"


async def test_m_checks_sent_while_running(wired):
    from test_process import pumped, short_recipe, _log, _notice
    from peald import storage, recipe as R
    lk, sim, cfg = wired
    await pumped(lk, sim)
    rec = short_recipe("v080 시작 조건")
    rec["blocks"][0]["repeat"] = 200
    assert storage.save("v080 시작 조건", rec)
    assert state.runner.select("v080 시작 조건")[0]
    state.recipe_check = R.validate(cfg, rec)
    assert (await state.runner.start(_log, _notice))[0]
    assert await wait_until(lambda: lk.status[A.D_STATE] in (A.STATE_READY, A.STATE_RUN), 5)
    p = state.runner.progress()
    assert p["running"] and p["checks"] and "can_start" not in p and "blocking" not in p
    await lk.send_command(A.CMD_ABORT)


async def test_m_same_value_noop_except_heater(wired, notices):
    lk, sim, cfg = wired
    if DEV.HAS_PCV:
        cmd = {"cmd": "manual_pcv", "pct": 30}
    else:
        cmd = {"cmd": "manual_o3", "action": "set", "value": 30}
    await C.handle_command(dict(cmd))
    no = sim.reg[A.D_CMD_NO]
    notices.clear()
    await C.handle_command(dict(cmd))
    assert sim.reg[A.D_CMD_NO] == no
    assert any(lv == "info" and "바뀌는 것이 없어 다시 보내지 않았습니다" in m for lv, m in notices), notices
    ch = heater_channels(cfg, 1)
    if not ch:
        pytest.skip("한계가 있는 히터 채널이 없다")
    hc = {"cmd": "manual_heater", "sv": {str(ch[0]): 40}}
    await C.handle_command(dict(hc))
    no = sim.reg[A.D_CMD_NO]
    await C.handle_command(dict(hc))                      # ★ 명령 13 은 noop 없음(기준이 PC 요청 워드)
    assert sim.reg[A.D_CMD_NO] != no


# ===================== 9. 장비 정의 =====================
def test_device_layout_tables_cover_structure(struct):
    items = [k for r in DEV.ANNUNCIATOR for k in r["items"]]
    want = ({"in0:" + k["tag"] for k in DEV.INPUTS0 if not k.get("unused") and not k.get("momentary")} |
            {"in1:" + k["tag"] for k in DEV.INPUTS1} | {"ilk:" + k["tag"] for k in DEV.INTERLOCKS})
    assert sorted(items) == sorted(want) and len(items) == len(set(items))           # 정확히 한 번씩
    assert "in0:RST-SW" not in items and "in0:SCRUB" not in items
    for k in DEV.INPUTS0 + DEV.INPUTS1:
        assert k.get("desc") and HANGUL.search(k["desc"]), k
        if not (k.get("unused") or k.get("momentary")):
            assert k.get("lamp") and not HANGUL.search(k["lamp"]) and not k["lamp"].endswith(" OK"), k
    for k in DEV.INTERLOCKS:
        assert k.get("lamp") and not k["lamp"].endswith(" OK"), k
    strip = [k for g in DEV.VALVE_STRIP for k in g]
    assert {"valve:" + v["tag"] for v in DEV.VALVES} <= set(strip)
    assert "aux:VV" in strip and "aux:IV-E" in strip
    for a in DEV.AUX:
        if DEV.AUX_CMD_MASK & (1 << a["bit"]) and a["tag"] not in ("O3GEN", "BPMP", "RF"):
            assert "aux:" + a["tag"] in strip, a
    keys = {k for s in DEV.START_SLOTS for k in s["keys"]}
    can = {"plc", "device_id", "state", "alarm", "pump", "valve_ok", "base", "recipe", "leakcheck", "heater"}
    can |= {"o3", "o3_gen"} if DEV.HAS_O3 else {"rf"}
    assert can <= keys and len(DEV.START_SLOTS) == 10
    st = struct["structure"]
    for k in ("annunciator", "valve_strip", "start_slots", "aux_cmd_mask", "heater_power_mask"):
        assert k in st, k


# ===================== 5. 모든 항목이 늘 보인다 =====================
def test_no_grouping_and_no_measuring_in_operation_parts():
    """옛 '+N OK' · '+N more' 묶음(v0.5.4 ~ v0.6.3)과 칸 재기를 없앴다 — 칸 크기는 배치 · 장비 구조로만."""
    terms = _src("js", "terms.js")
    for old in ("stiRank", "stiOrder", "stiCompact", "stiFit", "stiMoreText", "stiWorst", "STI_COL", "stiSpan"):
        assert old not in terms, old
    assert "T.textPx = function" in terms
    for f in (("js", "views", "main.js"), ("js", "views", "panels.js"), ("js", "views", "strip.js"), ("js", "views", "pid.js")):
        src = _src(*f)
        for bad in ("clientHeight", "getBoundingClientRect", "scrollHeight", "+N", " more</span>", "data-okall", "data-more",
                    "keepProblems", "snapIlks", "stiList", "maxHeight"):
            assert bad not in src, (f, bad)
    core = _src("js", "core.js")
    assert "fitMainRows" not in core and "rowFlex" not in core and "CANVAS_W" not in core
    css = _src("css", "style.css")
    assert not re.search(r"\.(sti|stl|ilks)[\s.{>:,]", css)


def test_fit_two_layouts_in_fake_dom():
    """core.js 의 fit 을 떼어 가짜 DOM 에서 — 배치 · 캔버스 · zoom · data-layout, 배치가 바뀌면 모든 뷰 다시 그리기."""
    core = _src("js", "core.js")
    i = core.index("  var curLayout = '';")
    head = core[i:core.index("  function fit()", i)]
    out = _ui(r'''
      var VIEWS = {}, lastState = { live: {} }; const renders = [];
      ['main', 'pid', 'trend', 'recipe'].forEach(k => { VIEWS[k] = { render: () => renders.push(k) }; });
      function requestAnimationFrame() {}
      const app = { style: { setProperty(k, v) { this[k] = v; } } };
      const w = { innerWidth: 960, innerHeight: 1000, UI: UI };
      const d = { getElementById: () => app, documentElement: { a: {}, setAttribute(k, v) { this.a[k] = v; } } };
      ''' + head + _fn(core, "fit") + r'''
      const snap = () => [d.documentElement.a['data-layout'], app.style.width, app.style.height, +app.style.zoom.toFixed(3),
                          [...new Set(renders.splice(0))].sort().join(',')];
      const out = [];
      fit(); out.push(snap());
      w.innerWidth = 1920; w.innerHeight = 1032; fit(); out.push(snap());
      w.innerHeight = 1080; fit(); out.push(snap());                 // 같은 배치 · 높이만 — 운전 · P&ID 만
      w.innerWidth = 960; w.innerHeight = 1000; fit(); out.push(snap());
      console.log(JSON.stringify(out));''')
    assert out[0][:4] == ["narrow", "960px", "1000px", 1.0]
    assert out[1] == ["wide", "1600px", "860px", 1.2, "main,pid,recipe,trend"]
    assert out[2][0] == "wide" and out[2][4] == "main,pid"
    assert out[3] == ["narrow", "960px", "1000px", 1.0, "main,pid,recipe,trend"]
    assert "if (!fitAfterState) { fitAfterState = true; fit(); }" in core          # 첫 state 뒤 다시 잰다(그대로)


# ===================== 브라우저 (playwright · 내장 시뮬레이터) =====================
@pytest.fixture
def served80(cfg, tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    from peald.server import create_app, uvicorn_config
    with open(paths.EXAMPLE_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    raw["plc"].update({"simulate": True, "sim_port": free_port(), "sim_speed": 50})
    for m, fs in zip(raw.get("mfc") or [], (1000.0, 500.0, 500.0, 500.0)):
        if m.get("full_scale_sccm") is None:
            m["full_scale_sccm"] = fs
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    app = create_app(str(path), single_instance=False)
    port = free_port()
    server = uvicorn.Server(uvicorn_config(app, "127.0.0.1", port))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        yield port
    finally:
        server.should_exit = True
        th.join(10)
        state.link = None
        state.runner = None
        state.sim = None


async def _page80(port, width, height, theme=None):
    pw_mod = pytest.importorskip("playwright.async_api")
    pw = await pw_mod.async_playwright().start()
    try:
        b = await pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        await pw.stop()
        pytest.skip(f"브라우저를 띄울 수 없음: {type(e).__name__}")
    pg = await b.new_page(viewport={"width": width, "height": height})
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    await pg.goto(f"http://127.0.0.1:{port}/")
    await pg.add_style_tag(content="#alarmModal { display: none !important; }")
    await pg.wait_for_function("() => window.core && core.state && core.plcOk()", timeout=30000)
    if theme:
        await pg.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
    return pw, b, pg, errors


_BOXES = """() => [...document.querySelectorAll('.view.on .panel, #strip .cell, .cmdbar')].map(e => {
  const r = e.getBoundingClientRect(); return [e.className, Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })"""
_FITS = """() => [...document.querySelectorAll('.view.on .panel-body, .view.on .panel, #strip .cell')].filter(e =>
  e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1).map(e => e.className)
  .concat([...document.querySelectorAll('#strip .kpi:not(.name), .lamp, .vt .vtag, .sct b')].filter(e =>
  e.scrollWidth > e.clientWidth + 1).map(e => 'clip ' + e.textContent))"""


@pytest.mark.parametrize("width,height,layout", [(960, 1000, "narrow"), (1920, 1032, "wide")])
async def test_browser_fixed_cells_fit_and_do_not_move(served80, width, height, layout):
    pw, b, pg, errors = await _page80(served80, width, height)
    try:
        assert await pg.evaluate("() => document.documentElement.getAttribute('data-layout')") == layout
        for tab in ("main", "pid"):
            await pg.evaluate("(t) => core.setTab(t)", tab)
            await asyncio.sleep(0.4)
            assert await pg.evaluate(_FITS) == [], tab                         # ① 넘침 없음 · 상태 낱말 말줄임 없음
            before = await pg.evaluate(_BOXES)
            for fault in ("emo", "leak", "pump_alm", "plc_stop"):
                await pg.evaluate("(k) => app.send('sim_fault', {key: k, on: true})", fault)
                await asyncio.sleep(0.8)
                assert await pg.evaluate(_BOXES) == before, (tab, fault)      # ② 칸 상자가 1 px 도 안 바뀜
            for fault in ("plc_stop", "pump_alm", "leak", "emo"):
                await pg.evaluate("(k) => app.send('sim_fault', {key: k, on: false})", fault)
            await asyncio.sleep(1.0)
        assert not errors, errors                                                # ⑧ 콘솔 오류 0
    finally:
        await b.close()
        await pw.stop()


async def test_browser_valve_tile_faceplate_and_one_command(served80):
    pw, b, pg, errors = await _page80(served80, 960, 1000)
    try:
        await pg.evaluate("() => core.setTab('main')")
        await pg.click('[data-vt="valve:PV-1"]')
        fp = await pg.evaluate("""() => { const f = document.getElementById('faceplate'), t = document.querySelector('[data-vt="valve:PV-1"]');
          const a = f.getBoundingClientRect(), r = t.getBoundingClientRect();
          const open = f.querySelector('[data-fpa="open"]');
          return { shown: !f.hidden, near: Math.abs(a.left - r.right) < 40 || Math.abs(r.left - a.right) < 40 || a.top >= r.bottom - 2,
                   openDisabled: open ? open.disabled : null }; }""")
        assert fp["shown"] and fp["near"] and fp["openDisabled"] is True, fp          # ③ 허가 없음 → Open 막힘
        await pg.keyboard.press("Escape")
        await pg.evaluate("() => app.send('manual_unlock', {on: true})")
        await pg.evaluate("() => app.send('pump_start')")
        await pg.wait_for_function("() => ((core.state.live || {}).manual || {}).valve_permit === true", timeout=60000)
        n0 = await pg.evaluate("() => document.querySelectorAll('#toasts .toast').length")
        await pg.click('[data-vt="valve:PV-1"]')
        await pg.wait_for_function("() => !document.querySelector('#faceplate [data-fpa=\"open\"]').disabled", timeout=5000)
        for _ in range(5):                                                           # ④ 빠르게 5 번
            await pg.evaluate("() => document.querySelector('#faceplate [data-fpa=\"open\"]').click()")
        await asyncio.sleep(1.5)
        toasts = await pg.evaluate("() => [...document.querySelectorAll('#toasts .toast')].map(e => e.textContent)")
        assert len([t for t in toasts if "PV-1" in t]) == 1 and len(toasts) <= n0 + 2, toasts
        assert not errors, errors
    finally:
        await b.close()
        await pw.stop()


async def test_browser_other_tabs_and_dark_and_resize(served80):
    pw, b, pg, errors = await _page80(served80, 1920, 1032, theme="dark")
    try:
        for tab in ("recipe", "trend", "datalog", "alarm", "setup"):
            await pg.evaluate("(t) => core.setTab(t)", tab)
            await asyncio.sleep(0.3)
            over = await pg.evaluate("() => { const v = document.querySelector('.view.on'); return v.scrollWidth > v.clientWidth + 1; }")
            assert not over, tab                                                     # ⑤ 가로 넘침 없음
        h = await pg.evaluate("() => document.querySelector('.panel.log-min').getBoundingClientRect().height / "
                              "(document.getElementById('app').getBoundingClientRect().width / document.getElementById('app').offsetWidth)")
        assert h >= 150 - 1, h
        await pg.evaluate("() => core.setTab('main')")
        assert await pg.evaluate(_FITS) == []                                        # ⑥ 어두운 테마에서도
        before = await pg.evaluate(_BOXES)
        await pg.click('[data-vt="aux:VV"]')
        await pg.set_viewport_size({"width": 960, "height": 1000})                   # ⑦ 배치가 바뀌면 조작 창 닫힘
        await asyncio.sleep(0.6)
        assert await pg.evaluate("() => [document.documentElement.getAttribute('data-layout'), document.getElementById('faceplate').hidden]") == ["narrow", True]
        await pg.set_viewport_size({"width": 1920, "height": 1032})
        await asyncio.sleep(0.6)
        assert await pg.evaluate(_BOXES) == before
        assert not errors, errors
    finally:
        await b.close()
        await pw.stop()


def test_version_080():
    from peald import version
    assert tuple(int(x) for x in version.APP_VERSION.split(".")) >= (0, 8, 0)
