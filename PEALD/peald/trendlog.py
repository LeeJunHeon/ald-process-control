"""
trendlog.py — 트렌드 이력 (1 Hz, 날짜별 SQLite).

data/trend/YYYYMMDD.db — 표준 sqlite3, WAL, 5 s 마다 묶어서 커밋한다.

★ 열은 장비 구조(device.py)로 고정한다. 설정(이름·사용 여부·환산)이 바뀌어도 표 구조가
  바뀌지 않아야 옛 파일을 그대로 읽을 수 있다.
★ 값은 공학 단위를 고정 배율 정수로 둔다(온도·유량·전력·개도·O3 ×10, 압력은 실수).
  환산을 나중에 바꿔도 그때 본 값이 바뀌지 않는다. 하루 10 MB 안팎.
★ 조회·내보내기는 이벤트 루프에서 하지 않는다(작업 스레드). 7일치 조회는 수 초가 걸려,
  루프에서 돌면 PC 하트비트가 멈춰 PLC 가 'PC 통신 끊김'으로 공정을 세운다.
  쓰기 연결은 루프 스레드에서만 쓰고(조회 전에 루프에서 flush), 조회는 읽기 전용 새 연결로 한다.
★ 쓰기 실패는 로그만 남기고 화면·공정을 막지 않는다. PLC 가 끊긴 동안은 줄을 남기지 않는다
  (그래프가 선을 잇지 않도록 — 0 을 채우면 거짓말이 된다).
"""

import os
import json
import csv
import glob
import time
import shutil
import sqlite3
import datetime

from . import device as DEV
from . import logger
from . import paths

COMMIT_S = 5.0
MAX_POINTS = 2000
LOW_DISK_BYTES = 1 << 30        # 1 GB
QUERY_MAX_S = 31 * 86400         # 이력 조회 최대 구간
EXPORT_MAX_S = 7 * 86400         # 내보내기 최대 구간
FUTURE_S = 3600                  # 끝 시각은 지금 + 1 h 까지만


def _cols():
    """(열 이름, SQL 종류, 배율, 묶음, 이름, 단위). 배율 10 = 저장값 ×10 정수."""
    out = [("p", "REAL", 1, "p", "CVG 압력", "Torr"),
           ("cm", "REAL", 1, "p", "CM 압력", "Torr")]
    for no in range(1, DEV.MFC_COUNT + 1):
        out.append((f"mfc{no}_pv", "INTEGER", 10, "m", f"MFC{no} 현재", "sccm"))
        out.append((f"mfc{no}_sv", "INTEGER", 10, "m", f"MFC{no} 설정", "sccm"))
    for ch in range(1, DEV.HEATER_COUNT + 1):
        out.append((f"h{ch}_pv", "INTEGER", 10, "t", f"CH{ch} 현재", "℃"))
        out.append((f"h{ch}_sv", "INTEGER", 10, "t", f"CH{ch} 설정", "℃"))
    if DEV.HAS_RF:
        out += [("rf_fwd", "INTEGER", 10, "x", "RF 순방향", "W"),
                ("rf_ref", "INTEGER", 10, "x", "RF 반사", "W"),
                ("rf_sv", "INTEGER", 10, "x", "RF 설정", "W")]
    if DEV.HAS_PCV:
        out += [("pcv_pv", "INTEGER", 10, "x", "PCV 개도", "%"),
                ("pcv_sv", "INTEGER", 10, "x", "PCV 목표", "%")]
    if DEV.HAS_O3:
        out += [("o3_pv", "INTEGER", 10, "x", "O3 현재", ""),
                ("o3_sv", "INTEGER", 10, "x", "O3 설정", "")]
    out += [("valves", "INTEGER", 1, "w", "밸브 출력 워드", ""),
            ("aux", "INTEGER", 1, "w", "보조 출력 워드", ""),
            ("state", "INTEGER", 1, "w", "장비 상태", ""),
            ("blk", "INTEGER", 1, "w", "블록", ""),
            ("step", "INTEGER", 1, "w", "스텝", "")]
    return out


COLS = _cols()
COL_NAMES = [c[0] for c in COLS]
SCALE = {c[0]: c[2] for c in COLS}
# 그래프로 그리는 열(워드·상태는 표·CSV 에만)
PLOT_COLS = [c for c in COLS if c[3] in ("p", "m", "t", "x")]


def trend_dir() -> str:
    return os.path.join(paths.DATA_DIR, "trend")


def export_dir() -> str:
    return os.path.join(paths.DATA_DIR, "export")


def columns_meta() -> list:
    return [{"key": c[0], "group": c[3], "label": c[4], "unit": c[5]} for c in PLOT_COLS]


def row_from_live(t: float, live: dict):
    """live 스냅샷 → 저장 줄(dict). PLC 끊김이면 None."""
    press = live.get("pressure")
    if not press:
        return None
    r = {"t": t, "p": press.get("cvg"), "cm": press.get("cm")}
    for m in live.get("mfc") or []:
        r[f"mfc{m['no']}_pv"] = m.get("pv")
        r[f"mfc{m['no']}_sv"] = m.get("sv")
    for h in live.get("heaters") or []:
        r[f"h{h['ch']}_pv"] = h.get("pv")
        r[f"h{h['ch']}_sv"] = h.get("sv")
    ex = live.get("extra") or {}
    for k in ("rf_fwd", "rf_ref", "rf_sv", "pcv_pv", "pcv_sv", "o3_pv", "o3_sv"):
        r[k] = ex.get(k)
    r["valves"] = live.get("valves")
    r["aux"] = live.get("aux")
    r["state"] = (live.get("state") or {}).get("code")
    r["blk"] = (live.get("seq") or {}).get("block")
    r["step"] = (live.get("seq") or {}).get("step")
    out = {}
    for name in COL_NAMES:
        v = r.get(name)
        if v is None:
            out[name] = None
        elif SCALE[name] == 10:
            out[name] = int(round(float(v) * 10))
        elif name in ("p", "cm"):
            out[name] = float(v)
        else:
            out[name] = int(v)
    out["t"] = float(t)
    return out


def _day(t: float) -> str:
    return datetime.datetime.fromtimestamp(t).strftime("%Y%m%d")


def _open(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    cols = ", ".join(f"{c[0]} {c[1]}" for c in COLS)
    con.execute(f"CREATE TABLE IF NOT EXISTS trend (t REAL PRIMARY KEY, {cols})")
    return con


class TrendLog:
    def __init__(self):
        self.pending = []
        self.day = ""
        self.con = None
        self.last_commit = time.monotonic()
        self.error = ""
        self._next = 0.0

    # ===================== 쓰기 =====================
    def record(self, live: dict, now: float = None):
        """샘플링 루프가 매 tick 부른다. 1 Hz 로 줄여 쌓는다.
        ★ 간격은 time.monotonic 으로 센다 — PC 시계를 뒤로 돌려도 기록이 비지 않는다.
          저장하는 시각 값만 time.time 이다. (now 를 주면 시험용으로 둘 다 now 로 센다)"""
        mono = time.monotonic() if now is None else now
        if mono < self._next:
            return
        self._next = mono + 1.0
        now = time.time() if now is None else now
        row = row_from_live(now, live)
        if row is not None:
            self.pending.append(row)
        if time.monotonic() - self.last_commit >= COMMIT_S:
            self.flush()

    def flush(self):
        self.last_commit = time.monotonic()
        if not self.pending:
            return
        rows, self.pending = self.pending, []
        try:
            by_day = {}
            for r in rows:
                by_day.setdefault(_day(r["t"]), []).append(r)
            for day, rs in by_day.items():
                con = self._con(day)
                names = ["t"] + COL_NAMES
                q = f"INSERT OR REPLACE INTO trend ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
                con.executemany(q, [[r.get(n) for n in names] for r in rs])
                con.commit()
            if self.error:
                logger.write("ok", "트렌드 이력 기록을 다시 시작했습니다")
            self.error = ""
        except Exception as e:  # noqa: BLE001
            # ★ 기록 실패가 화면·공정을 막지 않는다 — 로그로만(같은 오류는 한 번만).
            msg = f"트렌드 이력 기록 실패: {type(e).__name__}: {e}"
            if msg != self.error:
                logger.write("err", msg)
            self.error = msg
            self.close()

    def _con(self, day: str) -> sqlite3.Connection:
        if self.con is not None and self.day == day:
            return self.con
        self.close()
        os.makedirs(trend_dir(), exist_ok=True)
        self.con = _open(os.path.join(trend_dir(), f"{day}.db"))
        self.day = day
        return self.con

    def close(self):
        if self.con is not None:
            try:
                self.con.close()
            except Exception:  # noqa: BLE001
                pass
        self.con = None
        self.day = ""

    # ===================== 읽기 =====================
    def query(self, t0: float, t1: float, cols=None, max_points: int = MAX_POINTS) -> dict:
        """구간을 최대 max_points 묶음(최소·최대·평균)으로 줄여 돌려준다.
        반환 {t0, t1, bucket_s, cols, rows:[[t, [min,max,avg], ...]]} — 값은 공학 단위.
        ★ 동기 함수다 — 루프에서는 query_async 를 쓴다(작업 스레드에서 돈다)."""
        cols = [c for c in (cols or [c[0] for c in PLOT_COLS]) if c in SCALE]
        t0, t1 = float(t0), float(t1)
        if t1 <= t0:
            t1 = t0 + 1
        width = max(1.0, (t1 - t0) / max(1, int(max_points)))
        # 묶음마다 최소·최대·합·개수를 모은다 — 자정에 걸친 묶음은 두 날짜 파일에서 나오므로
        # 같은 묶음 번호끼리 합쳐야 최대 max_points 묶음이 지켜진다.
        agg = ", ".join(f"MIN({c}), MAX({c}), SUM({c}), COUNT({c})" for c in cols)
        q = (f"SELECT CAST((t - ?) / ? AS INTEGER) AS b, SUM(t), COUNT(*) {', ' + agg if agg else ''} "
             f"FROM trend WHERE t >= ? AND t < ? GROUP BY b ORDER BY b")
        merged = {}
        for path in _files_between(t0, t1):
            try:
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
                try:
                    for rec in con.execute(q, (t0, width, t0, t1)):
                        bk = min(int(rec[0]), int(max_points) - 1)
                        cur = merged.get(bk)
                        if cur is None:
                            merged[bk] = cur = [0.0, 0] + [[None, None, 0.0, 0] for _ in cols]
                        cur[0] += rec[1]
                        cur[1] += rec[2]
                        for i in range(len(cols)):
                            mn, mx, sm, n = rec[3 + i * 4: 7 + i * 4]
                            if not n:
                                continue
                            acc = cur[2 + i]
                            acc[0] = mn if acc[0] is None else min(acc[0], mn)
                            acc[1] = mx if acc[1] is None else max(acc[1], mx)
                            acc[2] += sm
                            acc[3] += n
                finally:
                    con.close()
            except sqlite3.Error as e:
                logger.write("warn", f"트렌드 이력 읽기 실패: {os.path.basename(path)} ({e})")
        rows = []
        for bk in sorted(merged):
            cur = merged[bk]
            row = [cur[0] / cur[1]]
            for i, c in enumerate(cols):
                mn, mx, sm, n = cur[2 + i]
                s = SCALE[c]
                row.append(None if not n else [mn / s, mx / s, sm / n / s])
            rows.append(row)
        return {"t0": t0, "t1": t1, "bucket_s": width, "cols": cols, "rows": rows}

    def raw_rows(self, t0: float, t1: float):
        """묶지 않은 줄(내보내기용). 공학 단위로 바꿔 돌려준다."""
        for path in _files_between(t0, t1):
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            try:
                for rec in con.execute(
                        f"SELECT t, {', '.join(COL_NAMES)} FROM trend WHERE t >= ? AND t < ? ORDER BY t",
                        (t0, t1)):
                    out = [rec[0]]
                    for i, c in enumerate(COL_NAMES):
                        v = rec[1 + i]
                        out.append(None if v is None else (v / 10 if SCALE[c] == 10 else v))
                    yield out
            finally:
                con.close()

    def export_csv(self, t0: float, t1: float) -> str:
        """구간을 CSV(UTF-8 BOM)로 data/export/ 에 저장한다. 파일 이름을 돌려준다.
        ★ 동기 함수다 — 루프에서는 export_async 를 쓴다."""
        t1 = min(float(t1), float(t0) + EXPORT_MAX_S)
        os.makedirs(export_dir(), exist_ok=True)
        name = (f"trend_{DEV.LOG_PREFIX}_"
                f"{datetime.datetime.fromtimestamp(t0).strftime('%Y%m%d_%H%M%S')}-"
                f"{datetime.datetime.fromtimestamp(t1).strftime('%Y%m%d_%H%M%S')}.csv")
        path = os.path.join(export_dir(), name)
        labels = {c[0]: f"{c[4]}{(' ' + c[5]) if c[5] else ''}" for c in COLS}
        # 1 MiB 버퍼 — 8 KB 마다 쓰기(GIL 놓기 · 다시 잡기)로 루프가 차례를 놓치지 않게
        with open(path, "w", encoding="utf-8-sig", newline="", buffering=1 << 20) as f:
            w = csv.writer(f)
            w.writerow(logger.csv_row(["시각"] + [labels[c] for c in COL_NAMES]))
            n = 0
            for r in self.raw_rows(t0, t1):
                ts = datetime.datetime.fromtimestamp(r[0]).strftime("%Y-%m-%d %H:%M:%S")
                w.writerow(logger.csv_row([ts] + ["" if v is None else v for v in r[1:]]))
                n += 1
        logger.write("info", f"트렌드 내보내기: {name} ({n}줄)")
        return name


    # ===================== 루프에서 부르는 것 =====================
    async def query_async(self, t0, t1, cols=None, max_points: int = MAX_POINTS,
                          keep_days=90, remote: bool = False, as_json: bool = False):
        """구간을 자르고 검사한 뒤, 루프에서 flush 하고 조회는 '무거운 조회' 문(heavy.gate)을 지나
        작업 스레드에서 한다. as_json 이면 JSON 바이트까지 스레드에서 만든다. 원격 칸이 차 있으면 heavy.Busy."""
        from .heavy import gate, rows_json
        t0, t1, err = clamp_range(t0, t1, keep_days, QUERY_MAX_S, "이력 조회")
        if err:
            res = {"error": err, "t0": t0, "t1": t1, "rows": [], "cols": [], "bucket_s": 1}
            return _json_bytes(res) if as_json else res
        self.flush()
        if as_json:
            return await gate.run(lambda: rows_json(self.query(t0, t1, cols, max_points)), remote=remote)
        return await gate.run(self.query, t0, t1, cols, max_points, remote=remote)

    async def export_async(self, t0, t1, keep_days=90):
        """(파일 이름, 오류). 오류가 있으면 내보내지 않는다. 무거운 조회 문을 지난다(로컬 전용 명령)."""
        from .heavy import gate
        t0, t1, err = clamp_range(t0, t1, keep_days, EXPORT_MAX_S, "내보내기")
        if err:
            return "", err
        self.flush()
        return await gate.run(self.export_csv, t0, t1), ""


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=lambda o: None).encode("utf-8")


def clamp_range(t0, t1, keep_days, max_s, what):
    """[지금 − 보존 일수, 지금 + 1 h] 로 자르고 최대 구간을 넘으면 거절한다. (t0, t1, 오류)"""
    try:
        t0, t1 = float(t0), float(t1)
    except (TypeError, ValueError):
        return 0.0, 0.0, "구간이 올바르지 않습니다"
    now = time.time()
    try:
        keep = max(1, int(keep_days))
    except (TypeError, ValueError):
        keep = 90
    t0 = max(t0, now - keep * 86400)
    t1 = min(t1, now + FUTURE_S)
    if t1 <= t0:
        return t0, t1, "구간이 비어 있습니다 (보존 기간 밖이거나 끝이 시작보다 앞)"
    if t1 - t0 > max_s + 1:
        return t0, t1, (f"{what} 구간은 최대 {int(max_s // 86400)}일입니다 — 구간을 줄이세요 "
                        f"(요청 {(t1 - t0) / 86400:.1f}일)")
    return t0, t1, ""


def _files_between(t0: float, t1: float) -> list:
    """있는 날짜 파일 중 구간에 걸치는 것만(날짜마다 파일을 확인하지 않는다)."""
    a = datetime.date.fromtimestamp(max(0.0, t0)).strftime("%Y%m%d")
    z = datetime.date.fromtimestamp(max(0.0, t1)).strftime("%Y%m%d")
    out = []
    for p in sorted(glob.glob(os.path.join(trend_dir(), "*.db"))):
        day = os.path.basename(p)[:8]
        if day.isdigit() and a <= day <= z:
            out.append(p)
    return out


def cleanup_exports(keep_days):
    """오래된 내보내기 CSV 정리(트렌드 보존 기간과 같게)."""
    try:
        keep = int(keep_days)
    except (TypeError, ValueError):
        return
    if keep <= 0:
        return
    cutoff = time.time() - keep * 86400
    for f in glob.glob(os.path.join(export_dir(), "*.csv")):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except OSError:
            pass


def cleanup(keep_days):
    """보존 기간이 지난 날짜 파일을 지운다(기동할 때 한 번)."""
    try:
        keep = int(keep_days)
    except (TypeError, ValueError):
        keep = 90
    if keep <= 0:
        return 0
    cutoff = (datetime.date.today() - datetime.timedelta(days=keep)).strftime("%Y%m%d")
    n = 0
    for p in glob.glob(os.path.join(trend_dir(), "*.db")):
        day = os.path.basename(p)[:8]
        if day.isdigit() and day < cutoff:
            for extra in ("", "-wal", "-shm"):
                try:
                    os.remove(p + extra)
                except OSError:
                    pass
            n += 1
    if n:
        logger.write("info", f"트렌드 이력 정리: {n}일치 삭제 (보존 {keep}일)")
    return n


def disk_warning() -> str:
    try:
        os.makedirs(paths.DATA_DIR, exist_ok=True)
        free = shutil.disk_usage(paths.DATA_DIR).free
    except Exception:  # noqa: BLE001
        return ""
    if free < LOW_DISK_BYTES:
        return f"데이터 폴더 디스크 여유가 {free / (1 << 20):.0f} MB 입니다 (1 GB 미만) — 오래된 파일을 정리하세요"
    return ""


trendlog = TrendLog()
