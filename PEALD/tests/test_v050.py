"""v0.5.0 — 화면 색 정리 · 영문 용어 · 배관도 조작(faceplate).

★ 화면의 순수 함수(terms.js · ops.js · faceplate.js)는 node 로 부른다(node 가 없으면 건너뜀). 브라우저 시험은 없다.
"""
import os
import re
import csv
import json

import pytest

from peald import addresses as A
from peald import device as DEV
from peald import logview
from peald import paths
from peald import settings
from peald import trendlog

from test_v0411 import _node, JS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANGUL = re.compile("[가-힣]")


# ===================== 1. 용어 사전에 한글 용어가 없다 =====================
def test_terms_js_has_no_korean():
    out = _node("const T = require(%s); console.log(JSON.stringify(T.dict));" % json.dumps(os.path.join(JS, "terms.js")))
    bad = {k: v for k, v in out.items() if HANGUL.search(v)}
    assert not bad, bad
    for k in ("tab.main", "tab.datalog", "panel.pid", "eq.manifold", "v.setpoint", "s.unknown", "b.stopAfterCycle"):
        assert k in out, k
    assert out["tab.main"] == "Operation" and out["b.abort"] == "Abort" and out["panel.pid"] == "P&ID"


def test_device_terms_are_english():
    names = []
    for group in (DEV.VALVES, DEV.AUX, DEV.INPUTS0, DEV.INPUTS1, DEV.ALARMS0, DEV.ALARMS1):
        names += [x["name"] for x in group]
    names += [x["tag"] for x in DEV.INTERLOCKS]
    names += list(DEV.HEATER_DEFAULT_NAMES) + list(DEV.MFC_DEFAULT_NAMES)
    names += [x["name"] for x in DEV.DISPLAY_SETPOINTS]
    names += list(A.STATE_NAMES.values()) + list(A.SEQ_NAMES.values())
    names += [c[4] for c in trendlog.COLS]
    names += [f["label"] for f in settings._F]
    bad = [n for n in names if HANGUL.search(n)]
    assert not bad, bad
    assert not HANGUL.search(DEV.TITLE)


def test_example_config_names_are_english():
    cfg = json.load(open(os.path.join(ROOT, "config", "config.example.json"), encoding="utf-8"))
    names = [m["name"] for m in cfg.get("mfc") or []] + [h["name"] for h in cfg.get("heaters") or []]
    assert names and not [n for n in names if HANGUL.search(n)], names


def test_index_html_labels_use_dictionary():
    html = open(os.path.join(ROOT, "frontend", "index.html"), encoding="utf-8").read()
    # v0.8.0: P&ID 탭(tab.pid) — 배관도 칸 머리는 panel.pid 그대로
    for k in ("tab.main", "tab.pid", "tab.recipe", "tab.trend", "tab.datalog", "tab.alarm", "tab.setup", "panel.pid",
              "b.pumpStart", "b.closeAll", "b.ack", "b.reset", "b.exit"):
        assert f'data-t="{k}"' in html, k
    for old in (">운전<", ">레시피<", ">트렌드<", ">배관도", ">공정 진행", ">펌핑 시작<", ">알람 리셋<"):
        assert old not in html, old


# ===================== 2. 조작 창 단추 = 수동 조작 창과 같은 명령 · 인자 =====================
def _fp(body: str):
    return _node(r'''
      global.window = global; global.document = null;
      global.T = require(%s);
      global.Ops = require(%s);
      global.UI = require(%s);            // v0.8.0: 이미 그 상태인 펌핑 · 벤트 규칙(UI.alreadyWhy — 명령줄과 같다)
      global.fmt = { flow: v => Number(v).toFixed(1), temp: v => Number(v).toFixed(1), torr: String,
                     pct: v => String(Math.round(v)), watt: v => Number(v).toFixed(1), num: (v, n) => Number(v).toFixed(n) };
      const F = require(%s);
      %s
    ''' % (json.dumps(os.path.join(JS, "terms.js")), json.dumps(os.path.join(JS, "ops.js")),
           json.dumps(os.path.join(JS, "uimodel.js")), json.dumps(os.path.join(JS, "views", "faceplate.js")), body))


def test_faceplate_commands_match_manual_window():
    out = _fp(r'''
      const c = (k, key, a, v) => { const r = F.command(k, key, a, v); return r ? [r.pair, r.confirm || ''] : null; };
      console.log(JSON.stringify({
        vo: c('valve', 'PV-1', 'open'), vc: c('valve', 'PV-1', 'close'), un: c('valve', 'PV-1', 'unlock'),
        mfc: c('mfc', 2, 'apply', '50'), mfcNo: c('mfc', 2, 'apply', ''),
        hsv: c('heater', 4, 'apply', '60'), hon: c('heater', 4, 'on'), hoff: c('heater', 4, 'off'),
        pcv: c('pcv', 'PCV', 'apply', '40'), rfon: c('rf', 'RF', 'on', '10'), rfoff: c('rf', 'RF', 'off'),
        rf0: c('rf', 'RF', 'on', '0'),
        o3set: c('o3', 'O3', 'apply', '12.5'), o3on: c('o3', 'O3', 'on', '12.5'), o3off: c('o3', 'O3', 'off'),
        ps: c('pump', 'PMP', 'pump_start'), pt: c('pump', 'PMP', 'pump_stop'), vent: c('vent', 'VV', 'vent'),
        // 수동 조작 창(manual.js)이 보내는 꼴 — 같은 Ops 로 만든다
        manMfc: Ops.mfcMany({ '2': 50 }), manHsv: Ops.heaterSvMany({ '4': 60 }), manPow: Ops.heaterPower(4, true)
      }));
    ''')
    # v0.4.16 의 수동 조작 창이 보내던 것과 같은 명령 · 같은 인자
    assert out["vo"] == [["manual_valve", {"tag": "PV-1", "on": True}], ""]
    assert out["vc"] == [["manual_valve", {"tag": "PV-1", "on": False}], ""]
    assert out["un"] == [["manual_unlock", {"on": True}], ""]
    assert out["mfc"] == [["manual_mfc", {"sccm": {"2": 50}}], ""] and out["mfcNo"] is None
    assert out["mfc"][0] == out["manMfc"]
    assert out["hsv"] == [["manual_heater", {"sv": {"4": 60}}], "heater"] and out["hsv"][0] == out["manHsv"]
    assert out["hon"] == [["manual_heater", {"power": {"4": True}}], ""] and out["hon"][0] == out["manPow"]
    assert out["hoff"] == [["manual_heater", {"power": {"4": False}}], ""]
    assert out["pcv"] == [["manual_pcv", {"pct": 40}], ""]
    assert out["rfon"] == [["manual_rf", {"on": True, "watt": 10}], "rf"] and out["rf0"] is None
    assert out["rfoff"] == [["manual_rf", {"on": False}], ""]
    assert out["o3set"] == [["manual_o3", {"action": "set", "value": 12.5}], ""]
    assert out["o3on"] == [["manual_o3", {"action": "on", "value": 12.5}], "o3"]
    assert out["o3off"] == [["manual_o3", {"action": "off"}], ""]
    assert out["ps"] == [["pump_start"], ""] and out["pt"] == [["pump_stop"], "pump_stop"]
    assert out["vent"] == [["vent"], "vent"]


def test_manual_window_sends_through_ops():
    src = open(os.path.join(JS, "views", "manual.js"), encoding="utf-8").read()
    # v0.8.0: 보내기는 진행 중 잠금을 거는 sendBusy 한 곳(그 안이 Ops.send) — 명령마다 sendBusy(…, Ops.…)
    assert "w.app.send(" not in src and "var rid = Ops.send(pair);" in src and src.count("sendBusy(") >= 10


# ===================== 3. 막힌 상태 — 단추는 막히고 이유가 보인다 =====================
def _state(**kw):
    s = {
        "access": {"local": True},
        "device": {"has_rf": DEV.HAS_RF, "has_o3": DEV.HAS_O3},
        "structure": {"valves": DEV.VALVES, "aux": DEV.AUX, "interlocks": DEV.INTERLOCKS,
                      "mfc": [{"no": 1, "name": "Precursor Carrier", "gas": "N2", "full_scale_sccm": 1000}],
                      "heaters": [{"ch": 4, "name": "Canister 2", "enabled": True, "max_c": 120}]},
        "recipe_limits": {"recipe_valves": [v["tag"] for v in DEV.VALVES if not v.get("auto")]},
        "live": {"plc": {"connected": True, "id_state": "ok"}, "valves": 0, "aux": 0, "interlock": 0,
                 "manual": {"unlocked": True, "unlock_left_s": 200, "valve_request": 0},
                 "process": {"running": False}, "mfc": [{"pv": 0, "sv": 0}], "heaters": [{}, {}, {}, {"pv": 25, "sv": 60, "power": False, "comm_ok": True}],
                 "extra": {}, "pressure": {"cvg": 1e-3}}
    }
    for k, v in kw.items():
        cur = s
        parts = k.split("__")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = v
    return s


@pytest.mark.parametrize("change,offline,why", [
    ({"live__process": {"running": True}}, False, "공정 중"),
    ({"access": {"local": False}}, False, "원격 접속 — 보기 전용"),
    ({"live__plc": {"connected": False}}, False, "PLC 끊김"),
    ({"live__plc": {"connected": True, "stalled": True}}, False, "PLC 하트비트 멈춤"),
    ({"live__plc": {"connected": True, "id_state": "wrong"}}, False, "다른 장비의 PLC"),
    ({}, True, "서버 연결 끊김"),
])
def test_faceplate_blocked_states_show_reason(change, offline, why):
    st = _state(**change)
    out = _fp(r'''
      const s = %s, off = %s;
      const r = {};
      [['valve', 'PV-1'], ['mfc', '1'], ['heater', '4'], ['pump', 'PMP'], ['vent', 'VV']].forEach(([k, key]) => {
        const m = F.model(k, key, s, off);
        r[k] = { why: m.why, on: m.buttons.filter(b => b.on).map(b => b.a), n: m.buttons.length,
                 inputsOff: m.inputs.every(x => x.off) };
      });
      console.log(JSON.stringify(r));
    ''' % (json.dumps(st), "true" if offline else "false"))
    for k, r in out.items():
        assert r["n"] >= 1 and r["on"] == [], (k, r)              # 창은 열리되 단추는 막힌다
        assert r["why"].startswith(why) and "조작할 수 없습니다" in r["why"], (k, r)
        assert r["inputsOff"], (k, r)


def test_faceplate_normal_state_and_valve_lock():
    # ★ v0.8.0: 밸브 Open 은 Valve Permit 이 있어야 · Close 는 요청이나 출력이 있을 때만 · 이미 멈춘 펌프의 Pumping Stop 은
    #   막힘(조작 창 판단을 고쳤다 — 7장). '정상 상태면 단추가 살아 있다' 는 허가 있음 · 닫힘 상태로 본다
    bit = next(k["bit"] for k in DEV.INTERLOCKS if k["tag"] == "Valve Permit")
    st = _state(live__interlock=1 << bit)
    opened = _state(live__interlock=1 << bit, live__valves=1, live__manual={"unlocked": True, "valve_request": 1})
    locked = _state(live__manual={"unlocked": False, "valve_request": 0})
    out = _fp(r'''
      const a = F.model('valve', 'PV-1', %s, false), b = F.model('valve', 'PV-1', %s, false);
      const c = F.model('valve', 'PV-1', %s, false);
      const h = F.model('heater', '4', %s, false), p = F.model('pump', 'PMP', %s, false);
      console.log(JSON.stringify({ a: [a.why, a.buttons.map(x => [x.a, x.on])], b: [b.why, b.buttons.map(x => [x.a, x.on])],
                                   c: c.buttons.map(x => [x.a, x.on]),
                                   h: h.buttons.map(x => [x.a, x.on]), p: p.buttons.map(x => [x.a, x.on]),
                                   rows: a.rows }));
    ''' % (json.dumps(st), json.dumps(locked), json.dumps(opened), json.dumps(st), json.dumps(st)))
    assert out["a"] == ["", [["open", True], ["close", False]]]
    assert out["c"] == [["open", False], ["close", True]]
    assert "Unlock" in out["b"][0] and out["b"][1] == [["open", False], ["close", False], ["unlock", True]]
    assert out["h"] == [["apply", True], ["on", True], ["off", True]]
    assert out["p"] == [["pump_start", True], ["pump_stop", False]]          # 이미 멈춰 있다(PMP · IV-E 꺼짐)
    assert ["Output", "Closed"] in out["rows"] and ["Valve Permit", "Yes"] in out["rows"]


# ===================== 4. 옛 한글 열 이름 데이터 로그가 읽힌다 =====================
def test_old_korean_datalog_still_reads():
    os.makedirs(paths.DATALOG_DIR, exist_ok=True)
    name = "20250101_100000_old"
    head = ["시각", "경과 s", "장비 상태", "시퀀서 상태", "블록", "스텝", "스텝 이름", "사이클", "그룹 회차",
            "스텝 경과 ms", "CVG Torr", "MFC1 현재 sccm", "MFC1 설정 sccm", "CH1 Stage 현재 ℃", "CH1 Stage 설정 ℃"]
    with open(os.path.join(paths.DATALOG_DIR, name + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for i in range(10):
            w.writerow([f"2025-01-01 10:00:{i:02d}", f"{i}.0", "공정 중" if i < 9 else "대기", "스텝 실행", 1, 1, "s", 1, 1, 0,
                        "0.05", "100", "100", "120", "120"])
    ch = logview.chart(name)
    labels = [c["label"] for c in ch["cols"]]
    assert "MFC1 Actual sccm" in labels and "MFC1 Setpoint sccm" in labels and "CH1 Stage Actual ℃" in labels
    assert len(ch["rows"]) == 10 and ch["segments"]
    t = logview.table(name)
    assert t["head"][:3] == ["Time", "Elapsed s", "State"] and t["total"] == 10
    g = logview._guess_meta(os.path.join(paths.DATALOG_DIR, name + ".csv"))
    assert g["result"] == "정상 종료(추정)" and g["took_s"] == 9.0 and g["ended"] == "2025-01-01 10:00:09"


def test_new_datalog_header_is_english(cfg):
    from peald.datalog import DataLog
    from peald.state import state
    state.install_config(cfg, [], "example")
    head = DataLog(state)._header()
    assert head[:4] == ["Time", "Elapsed s", "State", "Sequencer"]
    assert not [h for h in head if HANGUL.search(h)], head


def test_trend_export_and_columns_english():
    labels = [c["label"] for c in trendlog.columns_meta()]
    assert "CVG Pressure" in labels and "MFC1 Actual" in labels and "CH1 Setpoint" in labels


# ===================== 5. 색 토큰 밖의 직접 색 지정이 없다 =====================
COLOR = re.compile(r"(?<![\w&-])#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{1,5})?\b|\brgba?\(|\bhsla?\(")


def test_no_direct_colors_outside_tokens():
    files = [os.path.join(ROOT, "frontend", "css", "style.css"), os.path.join(ROOT, "frontend", "index.html")]
    for base in (os.path.join(ROOT, "frontend", "js"), os.path.join(ROOT, "frontend", "js", "views")):
        files += [os.path.join(base, n) for n in os.listdir(base) if n.endswith(".js")]
    bad = []
    for p in files:
        for i, line in enumerate(open(p, encoding="utf-8"), 1):
            code = line.split("//")[0] if p.endswith(".js") else line
            if COLOR.search(code):
                bad.append(f"{os.path.basename(p)}:{i}: {line.strip()[:100]}")
    assert not bad, bad


def test_tokens_status_colors_only():
    tok = open(os.path.join(ROOT, "frontend", "css", "tokens.css"), encoding="utf-8").read()
    # 강조 · 정보색은 파랑이 아니라 무채색 — 파랑은 흐름(--flow)에만
    light = tok.split(":root[data-theme")[0]
    val = dict(re.findall(r"--([\w-]+):\s*([^;]+);", light))
    assert val["pv"] == val["ink"] and val["sv"] == val["ink-faint"]
    assert val["info"] == val["ink-dim"] and val["chamber-line"] != val["flow"]
    assert val["vessel"] == val["vessel-cold"]                  # 가열 여부로 칠하지 않는다
