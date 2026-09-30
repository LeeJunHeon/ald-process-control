"""3단계 — 관리자 PIN · 설정 편집 · 트렌드 이력 · 데이터 로그 보기."""
import os
import csv
import json
import time
import types
import asyncio
import datetime

import pytest

from peald import addresses as A
from peald import device as DEV
from peald import paths
from peald import commands as C
from peald.admin import Admin, ITERATIONS
from peald.convert import Converters
from peald.process import ProcessRunner
from peald.state import state

from conftest import wait_until


class FakeWS:
    def __init__(self, host="127.0.0.1"):
        self.client = types.SimpleNamespace(host=host)
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    def of(self, typ):
        return [m for m in self.sent if m.get("type") == typ]

    def notices(self):
        return [m["msg"] for m in self.of("notice")]


# ===================== 1. 관리자 PIN =====================
def test_pin_is_stored_as_pbkdf2_hash_only():
    a, ws = Admin(), object()
    ok, _ = a.setup(ws, "4812", "4812", local=True)
    assert ok
    raw = open(os.path.join(paths.DATA_DIR, "admin_pin.json"), encoding="utf-8").read()
    rec = json.loads(raw)
    assert "4812" not in raw
    assert rec["algo"] == "pbkdf2_sha256" and rec["iterations"] == ITERATIONS >= 200_000
    assert len(bytes.fromhex(rec["salt"])) >= 16
    # 같은 PIN 이라도 salt 가 달라 해시가 다르다
    a2 = Admin()
    os.remove(os.path.join(paths.DATA_DIR, "admin_pin.json"))
    a2.setup(object(), "4812", "4812", local=True)
    rec2 = json.load(open(os.path.join(paths.DATA_DIR, "admin_pin.json"), encoding="utf-8"))
    assert rec2["hash"] != rec["hash"]


@pytest.mark.parametrize("pin,pin2", [("123", "123"), ("123456789", "123456789"),
                                      ("12a4", "12a4"), ("1234", "1235")])
def test_pin_setup_rules(pin, pin2):
    ok, _ = Admin().setup(object(), pin, pin2, local=True)
    assert not ok


def test_unlock_lock_and_token_bound_to_connection():
    a, ws, other = Admin(), object(), object()
    a.setup(ws, "2468", "2468", local=True)
    a.lock(ws)
    assert not a.is_admin(ws)
    ok, _ = a.unlock(ws, "2468", local=True)
    assert ok and a.is_admin(ws)
    tok = a.status(ws)["token"]
    assert a.is_admin(ws, tok)
    assert not a.is_admin(other, tok), "다른 연결이 같은 토큰으로 통하면 안 된다"
    assert not a.is_admin(ws, "x" + tok)
    assert "2468" not in json.dumps(a.status(ws)), "상태에 PIN 이 실리면 안 된다"
    a.lock(ws)
    assert not a.is_admin(ws)


def test_unlock_rejected_for_remote():
    a, ws = Admin(), object()
    a.setup(ws, "2468", "2468", local=True)
    a.lock(ws)
    ok, msg = a.unlock(ws, "2468", local=False)
    assert not ok and "이 PC" in msg
    ok, _ = Admin().setup(object(), "1111", "1111", local=False)
    assert not ok


def test_five_failures_block_for_five_minutes(monkeypatch):
    a, ws = Admin(), object()
    a.setup(ws, "2468", "2468", local=True)
    a.lock(ws)
    for _ in range(5):
        assert not a.unlock(ws, "0000", local=True)[0]
    ok, msg = a.unlock(ws, "2468", local=True)
    assert not ok and "막혀" in msg, "맞는 PIN 이어도 막힌 동안은 거절"
    assert 290 <= a.blocked_s() <= 300
    base = time.monotonic()
    monkeypatch.setattr("peald.admin.time.monotonic", lambda: base + 301)
    assert a.unlock(ws, "2468", local=True)[0]


def test_session_expires_after_idle(monkeypatch):
    a, ws = Admin(), object()
    a.setup(ws, "2468", "2468", local=True)
    assert a.is_admin(ws)
    base = time.monotonic()
    monkeypatch.setattr("peald.admin.time.monotonic", lambda: base + 599)
    assert a.is_admin(ws)
    a.touch(ws)                                    # 조작하면 연장
    monkeypatch.setattr("peald.admin.time.monotonic", lambda: base + 599 + 601)
    assert not a.is_admin(ws)


def test_lock_all_on_process_start():
    from peald import loops
    from peald.admin import admin
    ws = object()
    admin.sessions[ws] = {"token": "t", "last": time.monotonic()}
    loops._was_running = False
    loops._admin_on_process_start({"process": {"running": True}})
    assert not admin.is_admin(ws)
    assert ws in loops._admin_lock_pending
    loops._admin_lock_pending.clear()


def test_change_pin_needs_current():
    a, ws = Admin(), object()
    a.setup(ws, "2468", "2468", local=True)
    assert not a.change(ws, "1111", "13579", "13579", local=True)[0]
    assert a.change(ws, "2468", "13579", "13579", local=True)[0]
    a.lock(ws)
    assert not a.unlock(ws, "2468", local=True)[0]
    assert a.unlock(ws, "13579", local=True)[0]


def test_remote_admin_commands_rejected(monkeypatch):
    from peald.connection import manager
    ws = FakeWS("192.168.10.55")
    manager.active[ws] = {"local": False}
    try:
        for cmd in ("admin_setup", "admin_unlock", "config_save", "trend_export", "open_folder"):
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                C.handle_command({"cmd": cmd, "pin": "1234", "pin2": "1234"}, ws))
    finally:
        manager.active.pop(ws, None)
    assert all("보기 전용" in n for n in ws.notices())
    assert not os.path.exists(os.path.join(paths.DATA_DIR, "admin_pin.json"))


# ===================== 2. 설정 편집 =====================
def test_prepare_validates_and_lists_diff(cfg):
    from peald import settings as S
    res = S.prepare(cfg, {"params.base_press_torr": 0.03, "heaters.2.max_c": "150",
                          "mfc.1.name": "캐리어 A"})
    assert res["ok"], res["errors"]
    paths_ = {r["path"]: r for r in res["diff"]}
    assert paths_["heaters.2.max_c"]["new"] == 150
    row = paths_["params.base_press_torr"]
    assert row["prm"] == "D01101" and row["raw_new"] != row["raw_old"], "PRM 은 원시값을 병기한다"

    bad = S.prepare(cfg, {"heaters.1.station": 9, "params.pc_wdt_ms": "abc",
                          "server.port": 1, "access.local_only": False})
    assert not bad["ok"]
    joined = " ".join(bad["errors"])
    assert "station" in joined and "숫자가 아닙니다" in joined
    assert "server.port" in joined and "access.local_only" in joined, "화면이 바꾸면 안 되는 항목"


def test_prepare_flags_restart_and_sim_to_real(cfg):
    from peald import settings as S
    res = S.prepare(cfg, {"plc.simulate": False, "plc.host": "10.0.0.9"})
    assert res["restart"] and res["sim_to_real"]


def test_heartbeat_over_wdt_third_is_error(cfg):
    from peald import settings as S
    res = S.prepare(cfg, {"params.pc_wdt_ms": 900, "plc.heartbeat_ms": 500})
    assert not res["ok"] and any("1/3" in e for e in res["errors"])


def _admin_ws():
    from peald.admin import admin
    from peald.connection import manager
    ws = FakeWS()
    manager.active[ws] = {"local": True}
    admin.sessions[ws] = {"token": "tok", "last": time.monotonic()}
    return ws


@pytest.fixture
def wired(link):
    lk, sim, cfg = link
    state.startup_notices = []
    state.link = lk
    state.install_config(cfg, [], "example")
    state.sim = sim
    state.runner = ProcessRunner(state)
    state.datalog = None
    try:
        yield lk, sim, cfg
    finally:
        from peald.connection import manager
        manager.active.clear()
        state.link = None
        state.runner = None
        state.sim = None


async def test_save_creates_file_keeps_comments_backs_up_and_rewrites_prm(wired):
    lk, sim, cfg = wired
    from peald import settings as S
    ws = _admin_ws()
    target = paths.DEFAULT_CONFIG_PATH
    assert not os.path.exists(target)

    await C.handle_command({"cmd": "config_save", "token": "tok",
                            "edits": {"params.valve_min_ms": 150}}, ws)
    assert os.path.isfile(target), "예시로 실행 중이면 exe 옆에 config.json 을 만든다"
    raw = json.load(open(target, encoding="utf-8"))
    assert raw["params"]["valve_min_ms"] == 150
    assert "_주의" in raw and "_비고" in raw["analog"], "설명 키를 보존한다"
    assert state.cfg["params"]["valve_min_ms"] == 150 and state.config_source == "file"
    assert sim.reg[A.D_PRM_VALVE_MIN_MS] == 150, "PLC 에 PRM 을 다시 써야 한다"
    assert lk.prm_readback[A.D_PRM_VALVE_MIN_MS] == 150

    # 두 번째 저장부터는 이전 파일을 백업한다
    await C.handle_command({"cmd": "config_save", "token": "tok",
                            "edits": {"params.valve_min_ms": 120}}, ws)
    bks = os.listdir(S.backup_dir())
    assert len(bks) == 1
    assert json.load(open(os.path.join(S.backup_dir(), bks[0]), encoding="utf-8"))["params"]["valve_min_ms"] == 150
    assert sim.reg[A.D_PRM_VALVE_MIN_MS] == 120
    assert ws.of("config_saved")


async def test_save_rejected_without_admin_or_with_errors(wired):
    ws = _admin_ws()
    from peald.admin import admin
    admin.lock(ws)
    await C.handle_command({"cmd": "config_save", "token": "tok",
                            "edits": {"params.valve_min_ms": 150}}, ws)
    assert not os.path.exists(paths.DEFAULT_CONFIG_PATH)
    assert any("잠금" in n for n in ws.notices())

    ws2 = _admin_ws()
    await C.handle_command({"cmd": "config_save", "token": "wrong",
                            "edits": {"params.valve_min_ms": 150}}, ws2)
    assert not os.path.exists(paths.DEFAULT_CONFIG_PATH), "토큰이 다르면 거절"
    await C.handle_command({"cmd": "config_save", "token": "tok",
                            "edits": {"heaters.1.station": 7}}, ws2)
    assert not os.path.exists(paths.DEFAULT_CONFIG_PATH)
    assert ws2.of("config_preview")[-1]["errors"]


async def test_save_rejected_while_sequencer_running(wired, monkeypatch):
    lk, _sim, _cfg = wired
    ws = _admin_ws()
    st = list(lk.status)
    st[A.D_STATE] = A.STATE_PAUSE
    monkeypatch.setattr(lk, "status", st)
    await C.handle_command({"cmd": "config_preview", "token": "tok",
                            "edits": {"params.valve_min_ms": 150}}, ws)
    assert ws.of("config_preview")[-1]["blocked"]
    await C.handle_command({"cmd": "config_save", "token": "tok",
                            "edits": {"params.valve_min_ms": 150}}, ws)
    assert not os.path.exists(paths.DEFAULT_CONFIG_PATH)


def test_backup_keeps_latest_20(tmp_path):
    from peald import settings as S
    src = os.path.join(paths.DATA_ROOT, "config.json")
    with open(src, "w", encoding="utf-8") as f:
        f.write("{}")
    for i in range(25):
        S._backup(src)
    assert len(os.listdir(S.backup_dir())) == 20


def test_prm_table_has_setting_written_readback(wired):
    lk, _sim, cfg = wired
    rows = {r["addr"]: r for r in state.prm_table()}
    r = rows["D01107"]
    assert r["setting"] == cfg["params"]["valve_min_ms"]
    assert r["written"] == r["readback"] and r["match"]


# ===================== 3. 트렌드 이력 =====================
def _live(p=0.05, t_pv=100.0, mfc=10.0):
    return {"pressure": {"cvg": p, "cm": None},
            "mfc": [{"no": n, "pv": mfc, "sv": mfc} for n in range(1, DEV.MFC_COUNT + 1)],
            "heaters": [{"ch": c, "pv": t_pv, "sv": 80.0} for c in range(1, 13)],
            "extra": {}, "valves": 3, "aux": 8, "state": {"code": 1}, "seq": {"block": 0, "step": 0}}


def test_trend_store_query_and_reduce():
    from peald.trendlog import TrendLog
    tl = TrendLog()
    t0 = time.time() - 5000
    for i in range(5000):
        tl.record(_live(p=0.01 + i * 1e-5, t_pv=100 + (i % 10)), now=t0 + i)
    tl.flush()
    assert os.path.isfile(os.path.join(paths.DATA_DIR, "trend",
                                       datetime.date.fromtimestamp(t0).strftime("%Y%m%d") + ".db"))
    res = tl.query(t0, t0 + 5000, ["p", "h1_pv"], max_points=500)
    assert 450 <= len(res["rows"]) <= 500
    mn, mx, av = res["rows"][0][2]
    assert mn == 100.0 and mx == 109.0 and 100 < av < 109, "묶음은 최소·최대·평균"
    assert abs(res["rows"][0][1][2] - 0.01) < 1e-3
    tl.close()


def test_trend_values_are_fixed_scale():
    from peald.trendlog import row_from_live
    r = row_from_live(1.0, _live(t_pv=123.46, mfc=7.26))
    assert r["h1_pv"] == 1235 and r["mfc1_pv"] == 73, "온도·유량은 ×10 정수로"
    assert isinstance(r["p"], float)
    assert row_from_live(1.0, {"pressure": None}) is None, "PLC 끊김이면 줄을 남기지 않는다"


def test_trend_gap_is_not_filled():
    from peald.trendlog import TrendLog
    tl = TrendLog()
    t0 = time.time() - 1000
    for i in list(range(0, 100)) + list(range(600, 700)):
        tl.record(_live(), now=t0 + i)
    tl.flush()
    res = tl.query(t0, t0 + 700, ["p"], max_points=700)
    ts = [r[0] for r in res["rows"]]
    assert max(b - a for a, b in zip(ts, ts[1:])) > 400, "꺼져 있던 구간은 비어 있어야 한다"
    tl.close()


def test_trend_cleanup_and_export():
    from peald import trendlog as T
    tl = T.TrendLog()
    t0 = time.time() - 30
    for i in range(20):
        tl.record(_live(), now=t0 + i)
    tl.flush()
    name = tl.export_csv(t0, t0 + 30)
    path = os.path.join(T.export_dir(), name)
    raw = open(path, "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM"
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    assert len(rows) == 21 and rows[0][0] == "시각"
    tl.close()
    old = os.path.join(T.trend_dir(), "20000101.db")
    open(old, "w").close()
    assert T.cleanup(90) == 1 and not os.path.exists(old)


def test_trend_write_failure_does_not_raise(monkeypatch):
    from peald import trendlog as T
    tl = T.TrendLog()
    monkeypatch.setattr(T, "_open", lambda p: (_ for _ in ()).throw(OSError("disk")))
    tl.record(_live(), now=time.time())
    tl.flush()                                  # 예외가 밖으로 나오면 안 된다
    assert "disk" in tl.error


# ===================== 4. 데이터 로그 보기 =====================
def _make_log(name, rows, meta=None):
    os.makedirs(paths.DATALOG_DIR, exist_ok=True)
    head = ["시각", "경과 s", "장비 상태", "시퀀서 상태", "블록", "스텝", "스텝 이름", "사이클",
            "그룹 회차", "스텝 경과 ms", "CVG Torr", "MFC1 현재 sccm", "CH1 Stage 현재 ℃"]
    with open(os.path.join(paths.DATALOG_DIR, name + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for r in rows:
            w.writerow(r)
    if meta is not None:
        with open(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)


def _rows(n=30):
    out = []
    for i in range(n):
        blk, step = (1, 1 + (i // 5) % 2)
        out.append(["2026-01-01 10:00:%02d" % i, f"{i:.1f}", "공정 중" if i < n - 1 else "대기", "스텝 실행",
                    blk, step, "전구체" if step == 1 else "퍼지", 1, 1, 0, "0.05", "100.0", "120.5"])
    return out


def test_datalog_list_meta_and_guess():
    from peald import logview as V
    _make_log("20260101_100000_새것", _rows(), {"recipe": {"name": "새것"}, "number": 77,
                                                  "started": "2026-01-01 10:00:00", "ended": "x",
                                                  "result": "정상 종료", "rows": 30, "took_s": 29.0})
    _make_log("20251231_090000_옛것", _rows(12))
    items = {i["name"]: i for i in V.list_logs()}
    assert items["20260101_100000_새것"]["result"] == "정상 종료"
    assert items["20260101_100000_새것"]["number"] == 77
    old = items["20251231_090000_옛것"]
    assert old["guessed"] and old["rows"] == 12 and "추정" in old["result"]


@pytest.mark.parametrize("bad", ["..\\..\\config", "../x", "20260101_100000_a/../../b",
                                 "C:\\Windows\\win", "20260101_100000_없는파일", "", None])
def test_datalog_path_escape_rejected(bad):
    from peald import logview as V
    _make_log("20260101_100000_있는것", _rows())
    assert V.safe_path(bad) is None
    assert V.chart(bad) is None and V.table(bad) is None


def test_datalog_chart_segments_and_table_paging():
    from peald import logview as V
    _make_log("20260101_100000_구간", _rows(30))
    ch = V.chart("20260101_100000_구간")
    labels = [c["label"] for c in ch["cols"]]
    assert "CVG Torr" in labels and "MFC1 현재 sccm" in labels
    assert [s["step"] for s in ch["segments"]][:3] == ["1", "2", "1"]
    assert ch["segments"][0]["name"] == "전구체"
    tb = V.table("20260101_100000_구간", 20)
    assert tb["total"] == 30 and len(tb["rows"]) == 10


def test_datalog_close_writes_end_meta(wired):
    from peald.datalog import DataLog
    dl = DataLog(state)
    dl.start("메타시험", {"name": "메타시험"}, {"number": 5}, 1000)
    dl._next = 0.0
    dl.tick(1)
    dl.note_end("정상 종료")
    dl.close()
    meta = json.load(open(os.path.join(paths.DATALOG_DIR, dl.name + ".recipe.json"), encoding="utf-8"))
    assert meta["result"] == "정상 종료" and meta["rows"] == 1 and meta["ended"] and "took_s" in meta


# ===================== 5. 자체 점검 =====================
def test_selftest_returns_zero():
    import run
    assert run.selftest("") == 0
