"""v0.4.2 — 무거운 작업이 PC 하트비트를 멈추지 않는다 · 구간 제한 · 루프 지연 감시 · 트렌드 간격."""
import os
import csv
import time
import sqlite3
import asyncio
import datetime

import pytest

from powderald import addresses as A
from powderald import device as DEV
from powderald import paths
from powderald import recipe as R
from powderald.convert import Converters

from conftest import wait_until


# ===================== 큰 자료 만들기 =====================
def _make_trend_days(days=7):
    """1 Hz · days 일치 트렌드 이력(날짜별 파일)을 빠르게 만든다."""
    from powderald import trendlog as T
    os.makedirs(T.trend_dir(), exist_ok=True)
    now = time.time()
    t = now - days * 86400
    names = ["t"] + T.COL_NAMES
    batch = {}
    while t < now:
        day = datetime.datetime.fromtimestamp(t).strftime("%Y%m%d")
        row = {n: None for n in names}
        row.update(t=t, p=0.05 + (t % 100) * 1e-4, valves=3, aux=8, state=1, blk=0, step=0)
        for no in range(1, DEV.MFC_COUNT + 1):
            row[f"mfc{no}_pv"] = 1000
            row[f"mfc{no}_sv"] = 1000
        for ch in range(1, 13):
            row[f"h{ch}_pv"] = 1200 + int(t) % 7
            row[f"h{ch}_sv"] = 1200
        batch.setdefault(day, []).append([row[n] for n in names])
        t += 1.0
    for day, rows in batch.items():
        con = T._open(os.path.join(T.trend_dir(), f"{day}.db"))
        con.executemany(f"INSERT OR REPLACE INTO trend ({', '.join(names)}) "
                        f"VALUES ({', '.join('?' * len(names))})", rows)
        con.commit()
        con.close()
    return now - days * 86400, now


def _make_datalog_12h(name="20260101_000000_큰로그"):
    """0.5 s 주기 · 12 시간 데이터 로그(86,400 줄)."""
    os.makedirs(paths.DATALOG_DIR, exist_ok=True)
    head = ["시각", "경과 s", "장비 상태", "시퀀서 상태", "블록", "스텝", "스텝 이름", "사이클",
            "그룹 회차", "스텝 경과 ms", "CVG Torr"]
    head += [f"MFC{n} 현재 sccm" for n in range(1, DEV.MFC_COUNT + 1)]
    head += [f"CH{c} 히터 현재 ℃" for c in range(1, 7)]
    with open(os.path.join(paths.DATALOG_DIR, name + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for i in range(86400):
            w.writerow(["2026-01-01 00:00:00", f"{i * 0.5:.1f}", "공정 중", "스텝 실행", 1, 1 + (i // 7) % 3,
                        "s", 1, 1, 0, "0.05"] + ["100.0"] * DEV.MFC_COUNT + ["120.0"] * 6)
    return name


def _long_recipe_words(cfg):
    r = R.empty_recipe("긴공정")
    b = R.empty_block("b")
    b["repeat"] = 1000
    b["steps"] = [{"name": "s", "time_ms": 60000, "valves": [], "pause_ok": False}]
    if DEV.HAS_RF:
        b["steps"][0]["rf"] = False
    r["blocks"] = [b]
    return R.to_plc_words(cfg, Converters(cfg), r)["words"]


# ===================== 1. 하트비트 =====================
async def test_heavy_reads_do_not_stall_heartbeat(link):
    """7일 이력 조회 · 7일 내보내기 · 12시간 데이터 로그 그래프가 도는 동안
    PC 하트비트 쓰기 간격이 700 ms 를 넘지 않고, 시뮬레이터 공정이 중단되지 않는다."""
    from powderald.trendlog import trendlog
    from powderald import logview
    lk, sim, cfg = link
    # ★ 자료 만들기도 작업 스레드에서 — 루프를 막으면 그 자체로 PC 끊김 알람이 걸린다
    t0, t1 = await asyncio.to_thread(_make_trend_days, 7)
    name = await asyncio.to_thread(_make_datalog_12h)
    assert not (sim.reg[A.D_ALARM0] >> A.ALM0_PC_LINK) & 1, "준비 중에 PC 끊김이 걸렸다"

    sim.write(A.RCP_SUM_BASE, _long_recipe_words(cfg))
    assert sim._process_start() == A.RESULT_OK
    beats = []
    orig = lk._write_heartbeat

    async def spy():
        beats.append(time.monotonic())
        await orig()
    lk._write_heartbeat = spy
    await asyncio.sleep(0.6)

    started = time.monotonic()
    res, (exp, err), ch = await asyncio.gather(
        trendlog.query_async(t0 + 60, t1, None, 2000, 90),
        trendlog.export_async(t1 - 7 * 86400 + 60, t1, 90),
        logview.chart_async(name))
    took = time.monotonic() - started
    await asyncio.sleep(0.6)

    assert not res.get("error") and 1500 < len(res["rows"]) <= 2000, (res.get("error"), len(res["rows"]))
    assert exp and not err
    assert ch and len(ch["rows"]) <= 2000
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    print(f"\n무거운 작업 {took:.1f} s 동안 하트비트 {len(beats)}번, 최대 간격 {max(gaps) * 1000:.0f} ms")
    assert max(gaps) <= 0.7, f"하트비트가 {max(gaps):.2f} s 멈췄다"
    assert sim.running, f"공정이 중단됐다: {sim.end_reason} a0={sim.reg[A.D_ALARM0]:#06x} a1={sim.reg[A.D_ALARM1]:#06x}"
    assert not (sim.reg[A.D_ALARM0] >> A.ALM0_PC_LINK) & 1


# ===================== 구간 제한 =====================
def test_range_limits():
    from powderald.trendlog import clamp_range, QUERY_MAX_S, EXPORT_MAX_S
    now = time.time()
    t0, t1, err = clamp_range(now - 7 * 86400, now, 90, QUERY_MAX_S, "이력 조회")
    assert not err
    _t0, _t1, err = clamp_range(now - 40 * 86400, now, 90, QUERY_MAX_S, "이력 조회")
    assert err and "31일" in err
    _t0, _t1, err = clamp_range(now - 8 * 86400, now, 90, EXPORT_MAX_S, "내보내기")
    assert err and "7일" in err
    # t0=0 은 보존 기간으로 잘린다(그래도 31일을 넘으면 거절)
    a, b, err = clamp_range(0, now, 20, QUERY_MAX_S, "이력 조회")
    assert not err and a >= now - 20 * 86400 - 1
    a, b, err = clamp_range(now - 3600, now + 86400 * 30, 90, QUERY_MAX_S, "이력 조회")
    assert b <= now + 3601, "끝은 지금 + 1 h 까지"


async def test_export_over_7_days_rejected():
    from powderald.trendlog import TrendLog
    now = time.time()
    name, err = await TrendLog().export_async(now - 8 * 86400, now, 90)
    assert not name and "7일" in err


async def test_history_route_rejects_long_range():
    from powderald.trendlog import TrendLog
    now = time.time()
    res = await TrendLog().query_async(now - 40 * 86400, now, None, 2000, 90)
    assert res["error"] and res["rows"] == []


# ===================== 루프 지연 감시 =====================
async def test_loop_lag_is_measured_and_logged(monkeypatch):
    from powderald import loops
    logged = []
    monkeypatch.setattr(loops.logger, "write", lambda lv, msg: logged.append((lv, msg)))
    loops.lag.update(last_ms=0, max_ms=0, max_at="", max_work="", hist=[])
    task = asyncio.create_task(loops.lag_loop())
    try:
        await asyncio.sleep(0.25)
        loops.note_work("시험용 막힘")
        time.sleep(0.65)                         # 루프를 일부러 막는다
        await asyncio.sleep(0.3)
    finally:
        task.cancel()
    st = loops.lag_status()
    assert st["max_ms"] >= 500 and st["recent_max_ms"] >= 500
    assert st["max_work"] == "시험용 막힘"
    assert any("이벤트 루프 지연" in m and "시험용 막힘" in m for _lv, m in logged)


# ===================== 3. 트렌드 간격은 monotonic =====================
def test_trend_interval_uses_monotonic(monkeypatch):
    """PC 시계를 뒤로 돌려도 1 Hz 기록이 비지 않는다."""
    from powderald import trendlog as T
    tl = T.TrendLog()
    mono = [1000.0]
    wall = [2_000_000_000.0]
    monkeypatch.setattr(T.time, "monotonic", lambda: mono[0])
    monkeypatch.setattr(T.time, "time", lambda: wall[0])
    live = {"pressure": {"cvg": 0.1, "cm": None}, "mfc": [], "heaters": [], "extra": {},
            "valves": 0, "aux": 0, "state": {"code": 1}, "seq": {"block": 0, "step": 0}}
    for i in range(10):
        tl.record(live)
        mono[0] += 1.0
        wall[0] -= 3600.0 if i == 3 else -1.0    # 중간에 시계를 1시간 뒤로
    assert len(tl.pending) == 10, f"{len(tl.pending)}줄만 남았다"
    tl.pending.clear()
