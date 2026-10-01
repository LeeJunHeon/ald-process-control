"""
logview.py — 데이터 로그 보기 (목록 · 그래프 · 표 · 레시피 사본).

★ 파일 이름은 목록에 있는 것만 받는다. 정규식으로 모양을 보고, 실제 경로가
  DATALOG_DIR 바로 아래인지 한 번 더 확인한다 — '..\\' 같은 경로 탈출을 막는다.
★ 화면에서 지우기는 두지 않는다. 정리는 보존 기간(log.datalog_keep_days)으로만 한다.
★ 여기 함수는 파일 크기에 따라 오래 걸린다 — 루프에서는 *_async 로 작업 스레드에서 돌린다.
"""

import os
import re
import csv
import json
import glob
import math
import codecs
import asyncio
import datetime
from array import array

from . import paths
from .storage import read_json

_writing = None         # 지금 데이터 로그가 쓰고 있는 파일 이름(종료 뒤 꼬리 포함)


def set_writing(name):
    """DataLog 가 파일을 열 때 이름, 닫을 때 None 을 알린다."""
    global _writing
    _writing = name


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


# ★ CSV 읽기 — 작업 스레드에서 돌아도 이벤트 루프(PC 하트비트)를 멈추지 않게:
#   텍스트 모드로 줄마다 읽으면(8 KB 읽기) 읽을 때마다 GIL 을 놓았다가 바로 다시 잡아 루프가 차례를
#   못 받는다(convoy, 70~200 ms). 줄 전체를 list 로 모았다 버리는 것도 100~200 ms(+ GC).
#   그래서 1 MiB 바이너리 읽기 + 증분 디코더 + 줄 생성기를 csv.reader 에 넣고, 필요한 것만 모은다.
READ_CHUNK = 1 << 20
_QN = re.compile(rb'["\n]')


def _lines(path: str):
    """줄 생성기(줄 끝 포함). 따옴표 안 줄바꿈은 csv.reader 가 다음 줄과 잇는다."""
    dec = codecs.getincrementaldecoder("utf-8-sig")(errors="replace")
    tail = ""
    with open(path, "rb") as f:
        while True:
            b = f.read(READ_CHUNK)
            text = tail + dec.decode(b, final=not b)
            if not b:
                if text:
                    yield text
                return
            parts = text.split("\n")
            tail = parts.pop()                  # 아직 줄 끝을 못 본 조각 — 다음 덩어리와 잇는다
            for p in parts:
                yield p + "\n"


def _reader(path: str):
    return csv.reader(_lines(path))


def _read_rows(path: str):
    """(머리, 모든 줄) — 작은 파일 · 시험용. 큰 파일은 _reader 로 흘려 읽는다."""
    rows = list(_reader(path))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _head_and_last(path: str):
    """(머리, 줄 수, 마지막 줄) — 줄 수는 바이너리 줄바꿈 세기, 마지막 줄은 파일 끝에서 거꾸로 읽기."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        n = 0
        last_byte = b""
        inq = False                             # 따옴표 안(그 안의 줄바꿈은 줄이 아니다)
        while True:
            b = f.read(READ_CHUNK)
            if not b:
                break
            if not inq and b'"' not in b:
                n += b.count(b"\n")             # 따옴표 없는 덩어리 — C 로 한 번에 센다
            else:
                for m in _QN.finditer(b):
                    if m.group() == b'"':
                        inq = not inq
                    elif not inq:
                        n += 1
            last_byte = b[-1:]
        lines = n + (1 if size and last_byte != b"\n" else 0)
        f.seek(0)
        first = f.readline()
        # 끝에서 거꾸로 — 마지막 줄 끝의 줄바꿈은 빼고 그 앞 줄바꿈까지
        end = size
        while end > 0:
            f.seek(end - 1)
            c = f.read(1)
            if c not in (b"\n", b"\r"):
                break
            end -= 1
        # buf = 파일의 [pos, end). 마지막 줄의 시작 = 그 뒤 따옴표 수가 짝수인 가장 가까운 줄바꿈 뒤
        pos, buf, cut = end, b"", -1
        while True:
            cut = buf.rfind(b"\n", 0, cut if cut >= 0 else len(buf)) if buf else -1
            while cut >= 0 and buf.count(b'"', cut) % 2:
                cut = buf.rfind(b"\n", 0, cut)  # 따옴표 안의 줄바꿈 — 더 앞으로
            if cut >= 0 or pos == 0:
                break
            step = min(65536, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            cut = -1
        last = buf[cut + 1:]
    head = next(csv.reader([first.decode("utf-8-sig", "replace")]), [])
    tail = next(csv.reader([last.decode("utf-8-sig", "replace")]), []) if lines > 1 else []
    return head, max(0, lines - 1), tail


def _guess_meta(path: str) -> dict:
    """예전 파일(메타가 없는 것) — CSV 마지막 줄로 짐작한다."""
    try:
        head, nrows, last = _head_and_last(path)
    except Exception:  # noqa: BLE001
        return {}
    if not nrows:
        return {"rows": 0, "result": "알 수 없음(줄 없음)", "guessed": True}
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
    return {"rows": nrows, "took_s": took, "ended": get("시각"),
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
    if name == _writing:
        # ★ 아직 쓰는 중(종료 뒤 꼬리 포함)인 파일은 결과를 짐작하지 않는다 — 운전자 중단 직후
        #   마지막 줄이 '대기'라서 '정상 종료(추정)'로 보이는 일을 막는다.
        meta.update(_guess_meta(os.path.join(_dir(), name + ".csv")))
        meta.update({"result": "기록 중", "writing": True, "guessed": False, "ended": ""})
    elif rec.get("ended"):
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
    rd = _reader(path)
    head = next(rd, [])
    idx = {h: i for i, h in enumerate(head)}
    ti = idx.get("경과 s")
    cols = [(i, h, _group_of(h)) for i, h in enumerate(head) if _group_of(h)]
    bi, si, ni = idx.get("블록"), idx.get("스텝"), idx.get("스텝 이름")
    nan = math.nan
    ts = array("d")
    vals = [array("d") for _ in cols]           # 값 없음 = NaN (0 과 구분)
    segs = []                                   # 블록·스텝 구간(줄이기 전 모든 줄로)
    seg = None
    for r in rd:
        try:
            t = float(r[ti])
        except (TypeError, ValueError, IndexError):
            continue
        ts.append(t)
        n = len(r)
        for k, (i, _h, _g) in enumerate(cols):
            v = r[i] if i < n else ""
            try:
                vals[k].append(float(v) if v != "" else nan)
            except ValueError:
                vals[k].append(nan)
        key = (r[bi] if bi is not None and bi < n else "", r[si] if si is not None and si < n else "")
        if seg is not None and seg["key"] == key:
            seg["t1"] = t
        else:
            if seg is not None:
                seg["t1"] = t
            seg = {"key": key, "block": key[0], "step": key[1],
                   "name": r[ni] if ni is not None and ni < n else "", "t0": t, "t1": t}
            segs.append(seg)
    segs = [{k: v for k, v in s.items() if k != "key"} for s in segs
            if s["block"] not in ("", "0")]

    # 최대 max_points 묶음(최소·최대·평균) — 줄을 모으지 않고 칸마다 한 번씩 훑는다
    out = []
    if ts:
        t0, t1 = ts[0], ts[-1]
        width = max((t1 - t0) / max(1, max_points), 1e-9)
        # 마지막 점(t1)이 한 칸을 더 만들지 않게 max_points-1 로 자른다
        bk = array("l", (min(int((t - t0) / width), max_points - 1) for t in ts)) if t1 > t0 \
            else array("l", [0]) * len(ts)
        nb = max_points if t1 > t0 else 1
        cnt = [0] * nb
        tsum = [0.0] * nb
        for b, t in zip(bk, ts):
            cnt[b] += 1
            tsum[b] += t
        used = [b for b in range(nb) if cnt[b]]
        percol = []
        for k in range(len(cols)):
            mn, mx, sm, c = [math.inf] * nb, [-math.inf] * nb, [0.0] * nb, [0] * nb
            for b, v in zip(bk, vals[k]):
                if v == v:                      # NaN 이 아님
                    if v < mn[b]:
                        mn[b] = v
                    if v > mx[b]:
                        mx[b] = v
                    sm[b] += v
                    c[b] += 1
            percol.append((mn, mx, sm, c))
            vals[k] = None
        for b in used:
            row = [tsum[b] / cnt[b]]
            for mn, mx, sm, c in percol:
                row.append([mn[b], mx[b], sm[b] / c[b]] if c[b] else None)
            out.append(row)
    rec = read_json(os.path.join(_dir(), name + ".recipe.json"))
    return {"name": name, "meta": meta_of(name),
            "cols": [{"label": h, "group": g} for _i, h, g in cols],
            "rows": out, "segments": segs,
            "recipe": (rec or {}).get("recipe") if isinstance(rec, dict) else None}


# 그래프 결과 캐시 — (경로, 크기, 수정 시각) 이 같으면 다시 풀지 않는다(쓰는 중인 파일은 빼고)
from .heavy import ResultCache, rows_json            # noqa: E402
chart_cache = ResultCache(4)


def _dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=lambda o: None).encode("utf-8")


def chart_json(name: str):
    """그래프 응답 JSON 바이트(작업 스레드에서 부른다). 목록에 없으면 None."""
    path = safe_path(name)
    if path is None:
        return None
    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if key and name != _writing:
        hit = chart_cache.get(key)
        if hit is not None:
            return hit
    res = chart(name)
    if res is None:
        return None
    out = rows_json(res)
    if key and name != _writing:
        chart_cache.put(key, out)
    return out


def chart_json_cached(name: str):
    """캐시에 있으면 그 바이트(파일 상태만 본다 — 루프에서 불러도 가볍다), 없으면 None."""
    path = safe_path(name)
    if path is None or name == _writing:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return chart_cache.get((path, st.st_size, st.st_mtime_ns))


def table_json(name: str, offset: int = 0):
    res = table(name, offset)
    return None if res is None else _dumps(res)


def list_json():
    return _dumps({"items": list_logs()})


async def list_async():
    return await asyncio.to_thread(list_logs)


async def chart_async(name: str):
    return await asyncio.to_thread(chart, name)


async def table_async(name: str, offset: int = 0):
    return await asyncio.to_thread(table, name, offset)


def table(name: str, offset: int = 0, limit: int = PAGE):
    path = safe_path(name)
    if path is None:
        return None
    offset = max(0, int(offset or 0))
    limit = max(1, min(PAGE, int(limit or PAGE)))
    rd = _reader(path)
    head = next(rd, [])
    page = []
    total = 0
    for r in rd:                                # offset 까지 건너뛰고 그 쪽 줄만 모은다 — 총 줄 수는 세기만
        if offset <= total < offset + limit:
            page.append(r)
        total += 1
    return {"name": name, "head": head, "offset": offset, "total": total, "rows": page}
