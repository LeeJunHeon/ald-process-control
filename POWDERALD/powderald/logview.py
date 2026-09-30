"""
logview.py — 데이터 로그 보기 (목록 · 그래프 · 표 · 레시피 사본).

★ 파일 이름은 목록에 있는 것만 받는다. 정규식으로 모양을 보고, 실제 경로가
  DATALOG_DIR 바로 아래인지 한 번 더 확인한다 — '..\\' 같은 경로 탈출을 막는다.
★ 화면에서 지우기는 두지 않는다. 정리는 보존 기간(log.datalog_keep_days)으로만 한다.
"""

import os
import re
import csv
import glob
import datetime

from . import paths
from .storage import read_json

NAME_RE = re.compile(r"^\d{8}_\d{6}_[^\\/:*?\"<>|\x00-\x1f]{1,60}$")
MAX_POINTS = 2000
PAGE = 200


def _dir() -> str:
    return paths.DATALOG_DIR


def names() -> list:
    out = []
    for p in glob.glob(os.path.join(_dir(), "*.csv")):
        n = os.path.basename(p)[:-4]
        if NAME_RE.match(n):
            out.append(n)
    return sorted(out, reverse=True)


def safe_path(name: str, ext: str = ".csv"):
    """목록에 있는 이름만 경로로 바꾼다. 아니면 None."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    base = os.path.abspath(_dir())
    p = os.path.abspath(os.path.join(base, name + ext))
    if os.path.dirname(p) != base:
        return None
    if ext == ".csv" and not os.path.isfile(p):
        return None
    if name not in names():
        return None
    return p


def _read_rows(path: str):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _guess_meta(path: str) -> dict:
    """예전 파일(메타가 없는 것) — CSV 마지막 줄로 짐작한다."""
    try:
        head, rows = _read_rows(path)
    except Exception:  # noqa: BLE001
        return {}
    if not rows:
        return {"rows": 0, "result": "알 수 없음(줄 없음)", "guessed": True}
    last = rows[-1]
    idx = {h: i for i, h in enumerate(head)}

    def get(col):
        i = idx.get(col)
        return last[i] if i is not None and i < len(last) else ""

    try:
        took = float(get("경과 s") or 0)
    except ValueError:
        took = 0
    state = get("장비 상태")
    guess = "정상 종료(추정)" if state in ("대기",) else f"알 수 없음(마지막 상태 {state or '—'})"
    return {"rows": len(rows), "took_s": took, "ended": get("시각"),
            "result": guess, "guessed": True}


def meta_of(name: str) -> dict:
    p = os.path.join(_dir(), name + ".recipe.json")
    rec = read_json(p) if os.path.isfile(p) else None
    rec = rec if isinstance(rec, dict) else {}
    meta = {
        "name": name,
        "started": rec.get("started") or _started_from_name(name),
        "recipe": (rec.get("recipe") or {}).get("name") or name[16:],
        "number": rec.get("number"),
        "estimated_ms": rec.get("estimated_ms"),
    }
    if rec.get("ended"):
        meta.update({"ended": rec.get("ended"), "result": rec.get("result", ""),
                     "rows": rec.get("rows"), "took_s": rec.get("took_s"), "guessed": False})
    else:
        meta.update(_guess_meta(os.path.join(_dir(), name + ".csv")))
    return meta


def _started_from_name(name: str) -> str:
    try:
        return datetime.datetime.strptime(name[:15], "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


def list_logs() -> list:
    return [meta_of(n) for n in names()]


# ===================== 한 파일 =====================
def _group_of(col: str):
    if col in ("CVG Torr", "CM Torr"):
        return "p"
    if col.startswith("MFC"):
        return "m"
    if col.startswith("CH") and col.endswith("℃"):
        return "t"
    if any(col.startswith(k) for k in ("RF ", "PCV ", "O3 ")):
        return "x"
    return None


def chart(name: str, max_points: int = MAX_POINTS):
    """그래프 자료 — 시간축(경과 s) · 열 묶음 · 블록/스텝 구간 · 레시피 사본."""
    path = safe_path(name)
    if path is None:
        return None
    head, rows = _read_rows(path)
    idx = {h: i for i, h in enumerate(head)}
    ti = idx.get("경과 s")
    cols = [(i, h, _group_of(h)) for i, h in enumerate(head) if _group_of(h)]
    pts = []
    for r in rows:
        try:
            t = float(r[ti])
        except (TypeError, ValueError, IndexError):
            continue
        vals = []
        for i, _h, _g in cols:
            try:
                vals.append(float(r[i]) if i < len(r) and r[i] != "" else None)
            except ValueError:
                vals.append(None)
        pts.append((t, vals))

    # 블록·스텝 구간(줄이기 전 전체 줄로 만든다)
    segs = []
    bi, si, ni = idx.get("블록"), idx.get("스텝"), idx.get("스텝 이름")
    for r in rows:
        try:
            t = float(r[ti])
        except (TypeError, ValueError, IndexError):
            continue
        key = (r[bi] if bi is not None else "", r[si] if si is not None else "")
        nm = r[ni] if ni is not None and ni < len(r) else ""
        if segs and segs[-1]["key"] == key:
            segs[-1]["t1"] = t
        else:
            if segs:
                segs[-1]["t1"] = t
            segs.append({"key": key, "block": key[0], "step": key[1], "name": nm, "t0": t, "t1": t})
    segs = [{k: v for k, v in s.items() if k != "key"} for s in segs
            if s["block"] not in ("", "0")]

    # 최대 max_points 묶음(최소·최대·평균)
    out = []
    if pts:
        t0, t1 = pts[0][0], pts[-1][0]
        width = max((t1 - t0) / max(1, max_points), 1e-9)
        buckets = {}
        for t, vals in pts:
            b = int((t - t0) / width) if t1 > t0 else 0
            buckets.setdefault(b, []).append((t, vals))
        for b in sorted(buckets):
            grp = buckets[b]
            row = [sum(x[0] for x in grp) / len(grp)]
            for k in range(len(cols)):
                vs = [x[1][k] for x in grp if x[1][k] is not None]
                row.append([min(vs), max(vs), sum(vs) / len(vs)] if vs else None)
            out.append(row)
    rec = read_json(os.path.join(_dir(), name + ".recipe.json"))
    return {"name": name, "meta": meta_of(name),
            "cols": [{"label": h, "group": g} for _i, h, g in cols],
            "rows": out, "segments": segs,
            "recipe": (rec or {}).get("recipe") if isinstance(rec, dict) else None}


def table(name: str, offset: int = 0, limit: int = PAGE):
    path = safe_path(name)
    if path is None:
        return None
    head, rows = _read_rows(path)
    offset = max(0, int(offset or 0))
    limit = max(1, min(PAGE, int(limit or PAGE)))
    return {"name": name, "head": head, "offset": offset, "total": len(rows),
            "rows": rows[offset:offset + limit]}
