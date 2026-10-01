"""
recipe.py — 레시피 형식 · 검증 · PLC 표 변환 · 시간 계산 (순수 함수만).

★ 순수 함수만 둔다. 파일 I/O 도, 전역 상태도 없다 — 레시피 계산은 공정 안전과 직결되므로
  단위 테스트로 전부 덮을 수 있어야 한다.

★ 계산·검증·판정은 서버에서만 한다. 화면이 같은 계산을 따로 하면, 결과가 갈리는 순간
  "화면에는 통과인데 시작하면 거절"이 된다.

레시피 파일 형식 (data/recipes/<이름>.json)
  format   장비 형식 태그 — 다른 장비의 레시피는 열지 않는다
  name, memo, created, modified
  blocks[] name, repeat, mfc_sccm[장비 MFC 개수], (PEALD) pcv_pct, rf_w / (Powder) o3
           steps[] name, time_ms, valves[밸브 이름], pause_ok, (PEALD) rf
  groups[] from_block, to_block, repeat        ← 블록 번호는 1부터
           (옛 파일의 from / to 는 불러올 때 from_block / to_block 으로 바꿔 읽는다 — upgrade())

PLC 시퀀서 동작(래더 확정 사양)은 README 를 참고할 것. 이 파일의 시간 계산은 그 동작을
그대로 따라간다 — 실효 스텝 시간, 블록 준비, 블록·그룹 반복.
"""

import json
import math
import time
import zlib

from . import addresses as A
from . import device as DEV

# ---- 한계값 (PLC 래더와 맞춘 값) ----
STEP_MS_MIN = 20
STEP_MS_MAX = 3_276_700
STEP_MAX = A.RCP_STEP_MAX               # 100
BLOCK_MAX = A.RCP_BLOCK_MAX             # 10
GROUP_MAX = A.RCP_GROUP_MAX             # 5
BLOCK_REPEAT_MAX = 1_000_000
# ★ 그룹 반복은 PLC 표의 한 워드인데 래더가 부호 있는 16비트로 비교한다 — 32768 이상은 음수로
#   읽혀 그 그룹을 불러오는 순간 레시피 오류로 증착이 중간에 선다. 한계는 표 정의 옆 상수 하나다.
GROUP_REPEAT_MAX = A.RCP_GROUP_REPEAT_MAX     # 32767
LONG_STEP_MS = 60_000                   # 이 값을 넘으면 100 ms 타이머
# 글자 길이 한도 — 레시피 이름은 저장 규칙(storage.valid_name 80자)과 같게. 화면 입력 칸 maxlength 도
# 같은 값(state.recipe_limits). ★ 레시피 명령 크기의 상한(server.WS_MAX_SIZE)이 이 값들에서 나온다.
NAME_MAX = 80
MEMO_MAX = 500
LABEL_MAX = 40                          # 블록 이름 · 스텝 이름


# ===================== 기본 골격 =====================
def empty_recipe(name: str = "새 레시피") -> dict:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "format": DEV.RECIPE_FORMAT,
        "name": name,
        "memo": "",
        "created": now,
        "modified": now,
        "blocks": [empty_block("블록 1")],
        "groups": [],
    }


def empty_block(name: str = "새 블록") -> dict:
    b = {
        "name": name,
        "repeat": 1,
        "mfc_sccm": [0.0] * DEV.MFC_COUNT,
        "steps": [empty_step("스텝 1")],
    }
    if DEV.HAS_PCV:
        b["pcv_pct"] = 0.0
    if DEV.HAS_RF:
        b["rf_w"] = 0.0
    if DEV.HAS_O3:
        b["o3"] = 0.0
    return b


def empty_step(name: str = "새 스텝") -> dict:
    s = {"name": name, "time_ms": 1000, "valves": [], "pause_ok": False}
    if DEV.HAS_RF:
        s["rf"] = False
    return s


# ===================== 옛 형식 · 값 형식 =====================
def upgrade(recipe):
    """옛 키를 새 키로 바꾼 사본. 반복 그룹의 from / to → from_block / to_block.
    ★ 저장은 새 키로만 한다 — 옛 키가 남아 있으면 화면·PLC 표가 서로 다른 칸을 읽는다."""
    if not isinstance(recipe, dict):
        return recipe
    out = dict(recipe)
    gs = recipe.get("groups")
    if isinstance(gs, list):
        new = []
        for g in gs:
            if isinstance(g, dict):
                g = dict(g)
                if "from_block" not in g and "from" in g:
                    g["from_block"] = g["from"]
                if "to_block" not in g and "to" in g:
                    g["to_block"] = g["to"]
                g.pop("from", None)
                g.pop("to", None)
            new.append(g)
        out["groups"] = new
    return out


_NUM_ABS_MAX = 2 ** 53           # 이보다 큰 수는 실수로 옮길 때 값이 달라진다 — 형식 오류로 본다


def _is_num(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return abs(v) <= _NUM_ABS_MAX
    return isinstance(v, float) and math.isfinite(v) and abs(v) <= _NUM_ABS_MAX


def _is_int(v) -> bool:
    """정수만. 2.0 처럼 소수부가 없는 실수는 받고, 2.5 · 150.7 은 받지 않는다(조용히 자르지 않는다)."""
    return _is_num(v) and (isinstance(v, int) or v.is_integer())


def shape_errors(recipe) -> list:
    """모든 필드의 형식(정수 · 실수 · 불 · 문자열 · 목록 · 사전). 틀린 것마다 오류 항목 하나.
    ★ 형식이 틀린 레시피로 계산하면 예외가 나거나(문자열 숫자) 값이 조용히 잘린다(소수 반복) —
      계산 전에 여기서 걸러 검증 오류로 돌려준다."""
    errs = []

    def bad(msg, block=None, step=None, field=None):
        errs.append({"msg": msg, "block": block, "step": step, "field": field})

    if not isinstance(recipe, dict):
        bad("레시피 형식이 올바르지 않습니다 (사전이 아님)")
        return errs

    def num(obj, key, label, block=None, step=None, field=None, integer=False, required=False):
        if key not in obj:
            if required:
                bad(f"{label} 값이 없습니다", block, step, field)
            return
        v = obj[key]
        if v is None:
            bad(f"{label} 값이 비어 있습니다", block, step, field)
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and not _is_num(v):
            bad(f"{label} 값이 너무 크거나 올바르지 않습니다 (현재 {_show(v)})", block, step, field)
        elif integer and not _is_int(v):
            bad(f"{label}은(는) 정수여야 합니다 (현재 {_show(v)})", block, step, field)
        elif not integer and not _is_num(v):
            bad(f"{label}은(는) 숫자여야 합니다 (현재 {_show(v)})", block, step, field)

    def flag(obj, key, label, block=None, step=None, field=None):
        if key in obj and not isinstance(obj[key], bool):
            bad(f"{label}은(는) 참/거짓이어야 합니다 (현재 {_show(obj[key])})", block, step, field)

    def text(obj, key, label, block=None, step=None, field=None, limit=None):
        if key in obj and not isinstance(obj[key], str):
            bad(f"{label}은(는) 문자열이어야 합니다 (현재 {_show(obj[key])})", block, step, field)
        elif limit is not None and key in obj and len(obj[key]) > limit:
            bad(f"{label}이(가) 너무 깁니다 (최대 {limit}자, 현재 {len(obj[key])}자)", block, step, field)

    for key, lim in (("format", 40), ("name", NAME_MAX), ("memo", MEMO_MAX), ("created", 40), ("modified", 40)):
        text(recipe, key, {"name": "레시피 이름", "memo": "메모"}.get(key, f"'{key}'"), field=key, limit=lim)

    blocks = recipe.get("blocks")
    if not isinstance(blocks, list):
        bad("'blocks'는 목록이어야 합니다", field="blocks")
        blocks = []
    for bi, b in enumerate(blocks, start=1):
        if not isinstance(b, dict):
            bad("블록 형식이 올바르지 않습니다 (사전이 아님)", block=bi)
            continue
        text(b, "name", "블록 이름", bi, field="name", limit=LABEL_MAX)
        num(b, "repeat", "블록 반복", bi, field="repeat", integer=True, required=True)
        mfc = b.get("mfc_sccm", [])
        if not isinstance(mfc, list):
            bad("'mfc_sccm'은 목록이어야 합니다", bi, field="mfc1")
        else:
            if len(mfc) > DEV.MFC_COUNT:
                bad(f"MFC 설정이 이 장비의 MFC 개수({DEV.MFC_COUNT})보다 많습니다 ({len(mfc)}개)",
                    bi, field="mfc1")
            for mi, v in enumerate(mfc[:DEV.MFC_COUNT]):
                num({"v": v}, "v", f"MFC{mi + 1} 설정", bi, field=f"mfc{mi + 1}")
        if DEV.HAS_PCV:
            num(b, "pcv_pct", "PCV 목표", bi, field="pcv")
        if DEV.HAS_RF:
            num(b, "rf_w", "RF 전력", bi, field="rf")
        if DEV.HAS_O3:
            num(b, "o3", "O3 설정", bi, field="o3")
        steps = b.get("steps", [])
        if not isinstance(steps, list):
            bad("'steps'는 목록이어야 합니다", bi, field="steps")
            continue
        for si, st in enumerate(steps, start=1):
            if not isinstance(st, dict):
                bad("스텝 형식이 올바르지 않습니다 (사전이 아님)", bi, si)
                continue
            text(st, "name", "스텝 이름", bi, si, "name", limit=LABEL_MAX)
            num(st, "time_ms", "스텝 시간", bi, si, "time", integer=True, required=True)
            vs = st.get("valves", [])
            if not isinstance(vs, list) or not all(isinstance(x, str) for x in vs):
                bad("밸브는 이름(문자열) 목록이어야 합니다", bi, si, "valves")
            flag(st, "pause_ok", "정지 허용", bi, si, "pause_ok")
            if DEV.HAS_RF:
                flag(st, "rf", "RF", bi, si, "rf")

    groups = recipe.get("groups", [])
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        bad("'groups'는 목록이어야 합니다", field="groups")
        groups = []
    for gi, g in enumerate(groups, start=1):
        where = f"group{gi}"
        if not isinstance(g, dict):
            bad(f"반복 그룹 {gi}: 형식이 올바르지 않습니다 (사전이 아님)", field=where)
            continue
        num(g, "from_block", f"반복 그룹 {gi} 시작 블록", field=where, integer=True, required=True)
        num(g, "to_block", f"반복 그룹 {gi} 끝 블록", field=where, integer=True, required=True)
        num(g, "repeat", f"반복 그룹 {gi} 반복", field=where, integer=True, required=True)
    return errs


def _show(v) -> str:
    """오류 문장에 넣을 값 — 짧게, 형식이 드러나게."""
    try:
        t = json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        t = repr(v)
    return t if len(t) <= 30 else t[:29] + "…"


# ===================== 레시피 번호 =====================
def normalize(recipe: dict) -> str:
    """번호 계산용 정규형. ★ 메모·시각은 뺀다 — 실행에 영향이 없는데 번호가 바뀌면
    '같은 레시피인데 PLC 와 번호가 다르다'가 되어 대조가 불가능해진다."""
    r = recipe or {}
    out = {
        "format": r.get("format", ""),
        "name": r.get("name", ""),
        "blocks": [{
            "name": b.get("name", ""),
            "repeat": int(b.get("repeat") or 1),
            "mfc": [round(float(x or 0), 3) for x in (b.get("mfc_sccm") or [])],
            "pcv": round(float(b.get("pcv_pct") or 0), 3) if DEV.HAS_PCV else None,
            "rf": round(float(b.get("rf_w") or 0), 3) if DEV.HAS_RF else None,
            "o3": round(float(b.get("o3") or 0), 3) if DEV.HAS_O3 else None,
            "steps": [{
                "name": s.get("name", ""),
                "t": int(s.get("time_ms") or 0),
                "v": sorted(s.get("valves") or []),
                "p": bool(s.get("pause_ok")),
                "rf": bool(s.get("rf")) if DEV.HAS_RF else None,
            } for s in (b.get("steps") or [])],
        } for b in (r.get("blocks") or [])],
        "groups": [{
            "f": int(g.get("from_block") or 0),
            "t": int(g.get("to_block") or 0),
            "r": int(g.get("repeat") or 1),
        } for g in (r.get("groups") or [])],
    }
    return json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def recipe_number(recipe: dict) -> int:
    """CRC32 하위 16비트. 0 은 '레시피 없음'과 헷갈리므로 1 로 올린다.
    형식이 틀린 레시피(조작된 파일 등)도 목록에 번호를 보일 수 있게 예외를 내지 않는다."""
    recipe = upgrade(recipe)
    text = None
    if not shape_errors(recipe):
        try:
            text = normalize(recipe)
        except (TypeError, ValueError, AttributeError, OverflowError):
            text = None
    if text is None:
        text = json.dumps(recipe, ensure_ascii=False, sort_keys=True, default=str)
    n = zlib.crc32(text.encode("utf-8")) & 0xFFFF
    return n or 1


# ===================== 시간 계산 =====================
def effective_step_ms(time_ms: int, has_new_valve: bool, valve_min_ms: int) -> int:
    """PLC 가 실제로 세는 시간.

    ① 새로 열리는 밸브가 있고 시간이 최소 열림보다 짧으면 최소 열림으로 늘린다.
    ② 60,000 ms 를 넘으면 100 ms 타이머라 100 ms 미만 나머지는 버린다.
    ★ 화면의 남은 시간이 실제와 맞으려면 이 두 가지를 그대로 따라가야 한다."""
    t = int(time_ms or 0)
    # ★ 래더처럼 부호 있는 16비트로 비교한다 — 32768 이상이면 음수라 늘리지 않는다
    vmin = A.to_signed16(int(valve_min_ms or 0))
    if has_new_valve and t < vmin:
        t = vmin
    if t > LONG_STEP_MS:
        t = (t // 100) * 100
    return t


def _valve_set(step: dict) -> frozenset:
    return frozenset(step.get("valves") or [])


def step_times(block: dict, valve_min_ms: int):
    """(첫 사이클 스텝 시간들, 반복 사이클 스텝 시간들).

    첫 스텝의 '직전 출력'이 다르다 — 블록 준비·일시정지 뒤에는 전부 닫힘이고,
    사이클 반복 때는 그 블록의 마지막 스텝이다."""
    steps = block.get("steps") or []
    if not steps:
        return [], []
    sets = [_valve_set(s) for s in steps]
    first_cycle, repeat_cycle = [], []
    for i, s in enumerate(steps):
        prev_first = frozenset() if i == 0 else sets[i - 1]
        prev_rep = sets[-1] if i == 0 else sets[i - 1]
        first_cycle.append(effective_step_ms(s.get("time_ms"), bool(sets[i] - prev_first), valve_min_ms))
        repeat_cycle.append(effective_step_ms(s.get("time_ms"), bool(sets[i] - prev_rep), valve_min_ms))
    return first_cycle, repeat_cycle


def block_ms(block: dict, valve_min_ms: int, prep_ms: int) -> int:
    """블록 한 번 실행에 걸리는 시간(블록 준비 포함)."""
    first, rep = step_times(block, valve_min_ms)
    repeat = max(1, int(block.get("repeat") or 1))
    return int(prep_ms + sum(first) + (repeat - 1) * sum(rep))


def prm_value(cfg: dict, key: str) -> int:
    """PLC 파라미터 하나(공학 단위 정수). 설정에 없으면 PLC P00 기본값과 같은 표
    (addresses.PRM_DEFAULTS)에서 — 시간 계산·검증 경고·PLC 쓰기가 같은 값을 쓴다.
    ★ 0 은 뜻이 있는 값이다(최소 열림 없음·대기 없음) — 기본값으로 바꾸지 않는다."""
    p = (cfg or {}).get("params") or {}
    default = A.PRM_DEFAULTS[key]
    v = p.get(key)
    if v is None or v == "":
        v = default
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _prm(cfg: dict):
    """시간 계산에 쓰는 PLC 파라미터 (최소 열림 ms, 블록 준비 ms)."""
    return prm_value(cfg, "valve_min_ms"), prm_value(cfg, "mfc_stable_s") * 1000


def _group_of(recipe: dict, block_no: int):
    """block_no(1부터)를 포함하는 그룹과 그 번호(1부터). 없으면 (None, 0)."""
    for i, g in enumerate(recipe.get("groups") or [], start=1):
        if int(g.get("from_block") or 0) <= block_no <= int(g.get("to_block") or 0):
            return g, i
    return None, 0


def total_ms(cfg: dict, recipe: dict) -> int:
    """전체 예상 시간. 그룹은 번호 순서로 하나씩 불러오고 서로 겹치지 않는다는 전제."""
    vmin, prep = _prm(cfg)
    blocks = recipe.get("blocks") or []
    per = [block_ms(b, vmin, prep) for b in blocks]
    total = 0
    n = 1
    while n <= len(blocks):
        g, _ = _group_of(recipe, n)
        if g and int(g.get("from_block") or 0) == n:
            a, b = int(g["from_block"]), min(int(g["to_block"]), len(blocks))
            span = sum(per[a - 1:b])
            total += span * max(1, int(g.get("repeat") or 1))
            n = b + 1
        else:
            total += per[n - 1]
            n += 1
    return int(total)


def remaining_ms(cfg: dict, recipe: dict, pos: dict) -> int:
    """PLC 가 알려 준 현재 위치에서 남은 시간.

    pos: block(1부터) · step(표 전체 기준 번호) · cycle · group_pass · step_elapsed_ms · paused ·
         stop_after_cycle(사이클 후 정지 예약 — 이번 사이클이 끝나면 공정이 끝난다)
    ★ PLC 는 스텝 '끝'에서 멈춘다 — 일시정지 중이면 멈춘 스텝은 이미 끝났으므로 빼고,
      재개 뒤 첫 스텝은 직전 출력이 전부 닫힌 것으로 센다(최소 열림 적용)."""
    vmin, prep = _prm(cfg)
    blocks = recipe.get("blocks") or []
    if not blocks:
        return 0
    bno = max(1, min(int(pos.get("block") or 1), len(blocks)))
    block = blocks[bno - 1]
    steps = block.get("steps") or []
    if not steps:
        return 0

    first, rep = step_times(block, vmin)
    # 표 전체 기준 스텝 번호 → 블록 안 인덱스
    base = sum(len(b.get("steps") or []) for b in blocks[:bno - 1])
    idx = int(pos.get("step") or (base + 1)) - base - 1
    idx = max(0, min(idx, len(steps) - 1))
    cycle = max(1, int(pos.get("cycle") or 1))
    cur = first if cycle == 1 else rep

    repeat = max(1, int(block.get("repeat") or 1))
    if pos.get("stop_after_cycle"):
        # 이번 사이클의 남은 스텝만 — 다음 사이클·블록·그룹은 돌지 않는다
        if pos.get("paused"):
            if idx + 1 >= len(steps):
                return 0
            sets = [_valve_set(s) for s in steps]
            nxt = effective_step_ms(steps[idx + 1].get("time_ms"), bool(sets[idx + 1]), vmin)
            return int(nxt + sum(cur[idx + 2:]))
        elapsed = int(pos.get("step_elapsed_ms") or 0)
        return int(max(0, cur[idx] - elapsed) + sum(cur[idx + 1:]))
    if pos.get("paused"):
        sets = [_valve_set(s) for s in steps]
        if idx + 1 < len(steps):
            nxt = effective_step_ms(steps[idx + 1].get("time_ms"), bool(sets[idx + 1]), vmin)
            left = nxt + sum(cur[idx + 2:])
            left += max(0, repeat - cycle) * sum(rep)
        elif cycle < repeat:
            # 다음 사이클 첫 스텝 — 직전 출력이 닫힘이라 첫 사이클 시간과 같다
            left = first[0] + sum(rep[1:])
            left += max(0, repeat - cycle - 1) * sum(rep)
        else:
            left = 0
    else:
        elapsed = int(pos.get("step_elapsed_ms") or 0)
        left = max(0, cur[idx] - elapsed) + sum(cur[idx + 1:])
        left += max(0, repeat - cycle) * sum(rep)

    # 남은 블록들
    left += _blocks_after(cfg, recipe, bno, int(pos.get("group_pass") or 1))
    return int(left)


def _blocks_after(cfg: dict, recipe: dict, block_no: int, group_pass: int) -> int:
    """block_no 를 마친 뒤 남은 블록들의 시간.
    ★ 반복 횟수가 크면(그룹 32767회) 하나씩 세는 것은 불가능하다 — 구간 합으로 계산한다."""
    vmin, prep = _prm(cfg)
    blocks = recipe.get("blocks") or []
    per = [block_ms(b, vmin, prep) for b in blocks]
    total = 0

    g, _ = _group_of(recipe, block_no)
    n = block_no + 1
    if g:
        a, b = int(g["from_block"]), min(int(g["to_block"]), len(blocks))
        rep = max(1, int(g.get("repeat") or 1))
        # 이번 회차에서 남은 블록들
        total += sum(per[block_no:b])
        # 남은 회차 전체
        total += max(0, rep - max(1, group_pass)) * sum(per[a - 1:b])
        n = b + 1

    while n <= len(blocks):
        g2, _ = _group_of(recipe, n)
        if g2 and int(g2.get("from_block") or 0) == n:
            a2, b2 = int(g2["from_block"]), min(int(g2["to_block"]), len(blocks))
            total += sum(per[a2 - 1:b2]) * max(1, int(g2.get("repeat") or 1))
            n = b2 + 1
        else:
            total += per[n - 1]
            n += 1
    return int(total)


def summarize(cfg: dict, recipe: dict) -> dict:
    """화면이 그대로 쓰는 요약. 형식이 틀린 레시피는 계산하지 않고 빈 요약."""
    vmin, prep = _prm(cfg)
    recipe = upgrade(recipe)
    if shape_errors(recipe):
        return {"number": None, "step_count": 0, "block_count": 0, "group_count": 0,
                "total_ms": 0, "prep_ms": prep, "blocks": []}
    blocks = recipe.get("blocks") or []
    out_blocks = []
    for i, b in enumerate(blocks, start=1):
        first, rep = step_times(b, vmin)
        out_blocks.append({
            "no": i,
            "name": b.get("name", ""),
            "repeat": max(1, int(b.get("repeat") or 1)),
            "step_count": len(b.get("steps") or []),
            "cycle_ms": int(sum(rep)),
            "total_ms": block_ms(b, vmin, prep),
        })
    return {
        "number": recipe_number(recipe),
        "step_count": sum(len(b.get("steps") or []) for b in blocks),
        "block_count": len(blocks),
        "group_count": len(recipe.get("groups") or []),
        "total_ms": total_ms(cfg, recipe),
        "prep_ms": prep,
        "blocks": out_blocks,
    }


# ===================== 검증 =====================
def _g(v, none_text="미정") -> str:
    """숫자는 간결하게, 정해지지 않은 값은 글자로. 오류 문장에 그대로 들어간다."""
    return none_text if v is None else f"{float(v):g}"


def _err(out, msg, block=None, step=None, field=None):
    out["errors"].append({"msg": msg, "block": block, "step": step, "field": field})


def _warn(out, msg, block=None, step=None, field=None):
    out["warnings"].append({"msg": msg, "block": block, "step": step, "field": field})


def validate(cfg: dict, recipe: dict) -> dict:
    """{errors, warnings} — 오류가 있으면 저장·올리기·시작 불가, 경고는 표시만.
    위치(블록·스텝)를 담아 화면이 그 칸으로 이동할 수 있게 한다."""
    out = {"errors": [], "warnings": []}
    if not isinstance(recipe, dict):
        _err(out, "레시피 형식이 올바르지 않습니다")
        return out
    recipe = upgrade(recipe)
    # ★ 형식(정수·실수·불·문자열·목록)부터 — 틀리면 계산하지 않고 그 목록을 오류로 돌려준다
    shape = shape_errors(recipe)
    if shape:
        out["errors"].extend(shape)
        return out

    fmt = recipe.get("format")
    if fmt != DEV.RECIPE_FORMAT:
        _err(out, f"이 장비의 레시피가 아닙니다 (형식 {fmt!r}, 필요 {DEV.RECIPE_FORMAT!r})")
        return out
    if not (recipe.get("name") or "").strip():
        _err(out, "레시피 이름이 비어 있습니다", field="name")

    conv_limits = _limits(cfg)
    blocks = recipe.get("blocks") or []

    if not (1 <= len(blocks) <= BLOCK_MAX):
        _err(out, f"블록 개수는 1~{BLOCK_MAX} 이어야 합니다 (현재 {len(blocks)})")

    total_steps = sum(len(b.get("steps") or []) for b in blocks)
    if not (1 <= total_steps <= STEP_MAX):
        _err(out, f"전체 스텝 개수는 1~{STEP_MAX} 이어야 합니다 (현재 {total_steps})")

    vmin = prm_value(cfg, "valve_min_ms")

    for bi, b in enumerate(blocks, start=1):
        steps = b.get("steps") or []
        if not steps:
            _err(out, "스텝이 하나도 없습니다", block=bi)
        rep = int(b.get("repeat") or 0)
        if not (1 <= rep <= BLOCK_REPEAT_MAX):
            _err(out, f"블록 반복은 1~{BLOCK_REPEAT_MAX} 이어야 합니다 (현재 {rep})",
                 block=bi, field="repeat")

        # MFC
        mfc = b.get("mfc_sccm") or []
        for mi in range(DEV.MFC_COUNT):
            v = float(mfc[mi]) if mi < len(mfc) and mfc[mi] is not None else 0.0
            fs = conv_limits["mfc_full"][mi]
            if fs is None and v > 0:
                # ★ 풀스케일이 없으면 PLC 표에 0 으로 들어간다 — 그 가스 없이 돈다(블록 준비는
                #   MFC1 만 본다). 조용히 0 으로 바꾸지 않고 막는다.
                _err(out, f"MFC{mi + 1} 풀스케일이 설정되지 않아 {v:g} sccm 을 보낼 수 없습니다 — "
                          f"설정에서 풀스케일을 넣거나 0 으로 두세요",
                     block=bi, field=f"mfc{mi + 1}")
            elif v < 0 or (fs is not None and v > fs):
                _err(out, f"MFC{mi + 1} 설정이 범위를 벗어납니다 "
                          f"(0~{_g(fs)} sccm, 현재 {v:g})",
                     block=bi, field=f"mfc{mi + 1}")
        if DEV.MFC_COUNT and float((mfc or [0])[0] or 0) == 0:
            _warn(out, "전구체 캐리어(MFC1) 설정이 0 입니다 — 전구체가 챔버로 밀려가지 않습니다",
                  block=bi, field="mfc1")

        # 장비 전용 아날로그
        if DEV.HAS_PCV:
            pcv = float(b.get("pcv_pct") or 0)
            if not (0 <= pcv <= 100):
                _err(out, f"PCV 목표는 0~100 % 이어야 합니다 (현재 {pcv:g})", block=bi, field="pcv")
        if DEV.HAS_RF:
            rf = float(b.get("rf_w") or 0)
            lim = conv_limits["rf_max"]
            if rf < 0 or (lim is not None and rf > lim):
                _err(out, f"RF 전력이 상한을 넘습니다 (0~{_g(lim, '미설정')} W, "
                          f"현재 {rf:g})", block=bi, field="rf")
            if lim in (None, 0) and rf > 0:
                _err(out, "RF 상한(params.rf_max_w)이 설정되지 않아 RF 를 쓸 수 없습니다",
                     block=bi, field="rf")
            # ★ RF 풀스케일(rf.max_w)이 비면 PLC 의 RF 상한이 0 으로 써져 RF 허가가 나지 않는다 —
            #   래더의 공정 허가에는 RF 조건이 없어 RF 스텝이 RF 없이 끝까지 돈다(알람 없음)
            if rf > 0 and not (cfg.get("rf") or {}).get("max_w"):
                _err(out, "RF 풀스케일(rf.max_w)이 설정되지 않아 RF 를 보낼 수 없습니다 — "
                          "PLC 의 RF 상한이 0 이 되어 RF 없이 돕니다", block=bi, field="rf")
        if DEV.HAS_O3:
            o3 = float(b.get("o3") or 0)
            lim = conv_limits["o3_max"]
            if o3 < 0 or (lim is not None and o3 > lim):
                _err(out, f"O3 설정이 상한을 넘습니다 (0~{_g(lim, '미설정')}, "
                          f"현재 {o3:g})", block=bi, field="o3")
            if lim in (None, 0) and o3 > 0:
                _err(out, "O3 상한(params.o3_max)이 설정되지 않아 O3 를 쓸 수 없습니다",
                     block=bi, field="o3")
            if o3 > 0 and not (cfg.get("o3") or {}).get("full"):
                _err(out, "O3 풀스케일(o3.full)이 설정되지 않아 O3 를 보낼 수 없습니다 — "
                          "PLC 의 O3 상한이 0 이 되어 O3 허가가 나지 않습니다", block=bi, field="o3")

        sets = [_valve_set(s) for s in steps]
        block_has_reactant = any(set(v) & set(DEV.REACTANT_TAGS) for v in sets)
        block_has_rf = DEV.HAS_RF and any(s.get("rf") for s in steps)

        for si, s in enumerate(steps, start=1):
            t = int(s.get("time_ms") or 0)
            if not (STEP_MS_MIN <= t <= STEP_MS_MAX):
                _err(out, f"스텝 시간은 {STEP_MS_MIN}~{STEP_MS_MAX:,} ms 이어야 합니다 (현재 {t:,})",
                     block=bi, step=si, field="time")

            vs = sets[si - 1]
            unknown = [v for v in vs if v not in DEV.RECIPE_VALVES]
            if unknown:
                _err(out, f"이 장비에 없는 밸브입니다: {', '.join(unknown)}",
                     block=bi, step=si, field="valves")

            pre = vs & set(DEV.PRECURSOR_TAGS)
            rea = vs & set(DEV.REACTANT_TAGS)
            if pre and rea:
                # ★ ALD 의 전제가 무너진다. PLC 가 둘 다 막고 중대 알람을 낸다.
                _err(out, f"전구체({', '.join(sorted(pre))})와 {', '.join(sorted(rea))} 를 "
                          f"같이 열 수 없습니다 — PLC 가 둘 다 막고 중대 알람을 냅니다",
                     block=bi, step=si, field="valves")

            for assist, need in DEV.ASSIST_PAIR.items():
                if assist in vs and need not in vs:
                    _err(out, f"{assist} 는 {need} 가 열리는 스텝에서만 쓸 수 있습니다 "
                              f"(캐니스터만 가압됩니다)", block=bi, step=si, field="valves")

            # --- 경고 ---
            prev = frozenset() if si == 1 else sets[si - 2]
            if (vs - prev) and t < vmin:
                _warn(out, f"새로 열리는 밸브가 있어 PLC 가 {vmin} ms 로 늘립니다 (적은 값 {t} ms)",
                      block=bi, step=si, field="time")
            if t > LONG_STEP_MS and t % 100:
                _warn(out, f"60 s 를 넘는 스텝은 100 ms 단위입니다 — 나머지를 버려 실제 "
                           f"{(t // 100) * 100:,} ms 로 동작합니다", block=bi, step=si, field="time")

            if DEV.HAS_RF and s.get("rf"):
                if not ({"PV-R3", "PV-R"} <= vs):
                    _warn(out, "RF 스텝에 O2 공급(PV-R3 + PV-R)이 없습니다",
                          block=bi, step=si, field="rf")
                if float(b.get("rf_w") or 0) == 0:
                    _warn(out, "RF 스텝이 있는데 블록 RF 전력이 0 입니다", block=bi, step=si, field="rf")
            if DEV.HAS_RF and ({"PV-R2", "PV-R3"} & vs) and "PV-R" not in vs:
                _warn(out, "PV-R 없이 매니폴드로 보내면 매니폴드만 가압됩니다",
                      block=bi, step=si, field="valves")

        if DEV.HAS_O3 and block_has_reactant and float(b.get("o3") or 0) == 0:
            _warn(out, "PV-R 스텝이 있는데 블록 O3 설정이 0 입니다", block=bi, field="o3")
        if DEV.HAS_RF and block_has_rf and float(b.get("rf_w") or 0) == 0:
            pass    # 위 스텝 경고로 충분

    # --- 그룹 ---
    groups = recipe.get("groups") or []
    if len(groups) > GROUP_MAX:
        _err(out, f"반복 그룹은 최대 {GROUP_MAX} 개입니다 (현재 {len(groups)})")
    last_to = 0
    for gi, g in enumerate(groups, start=1):
        a, b = int(g.get("from_block") or 0), int(g.get("to_block") or 0)
        r = int(g.get("repeat") or 0)
        where = {"field": f"group{gi}"}
        if not (1 <= a <= len(blocks)) or not (1 <= b <= len(blocks)):
            _err(out, f"반복 그룹 {gi}: 블록 범위를 벗어납니다 ({a}~{b}, 블록 {len(blocks)}개)", **where)
        elif a > b:
            _err(out, f"반복 그룹 {gi}: 시작 블록이 끝 블록보다 뒤입니다 ({a} > {b})", **where)
        elif a <= last_to:
            # ★ PLC 는 그룹을 번호 순서로 하나씩만 불러온다 — 겹치거나 순서가 어긋나면
            #   래더가 되돌아갈 곳을 잘못 잡는다.
            _err(out, f"반복 그룹 {gi}: 앞 그룹과 겹치거나 순서가 어긋났습니다 "
                      f"(블록 순서대로 정렬되고 서로 겹치지 않아야 합니다)", **where)
        else:
            last_to = b
        if not (1 <= r <= GROUP_REPEAT_MAX):
            _err(out, f"반복 그룹 {gi}: 반복은 1~{GROUP_REPEAT_MAX} 이어야 합니다 (현재 {r})", **where)

    return out


def _limits(cfg: dict) -> dict:
    mfc_full = []
    for m in (cfg.get("mfc") or [])[:DEV.MFC_COUNT]:
        mfc_full.append(m.get("full_scale_sccm"))
    while len(mfc_full) < DEV.MFC_COUNT:
        mfc_full.append(None)
    prm = cfg.get("params") or {}
    return {
        "mfc_full": mfc_full,
        "rf_max": prm.get("rf_max_w") if DEV.HAS_RF else None,
        "o3_max": prm.get("o3_max") if DEV.HAS_O3 else None,
    }


def ok(result: dict) -> bool:
    return not (result or {}).get("errors")


# ===================== PLC 표 변환 =====================
def to_plc_words(cfg: dict, conv, recipe: dict) -> dict:
    """레시피 → 1120 워드 (D02000~D03119). {words, number, checksum, step_count, ...}

    블록 순서대로 스텝을 이어 붙여 전체 스텝 번호(1부터)를 매기고,
    블록 항목의 첫/끝 스텝에 그 번호를 쓴다."""
    words = [0] * A.RCP_AREA_COUNT
    off = A.RCP_SUM_BASE

    def put(addr, value):
        words[addr - off] = int(value) & 0xFFFF

    def put_dword(addr, value):
        lo, hi = A.split_dword(int(value))
        put(addr, lo)
        put(addr + 1, hi)

    blocks = recipe.get("blocks") or []
    groups = recipe.get("groups") or []

    step_no = 0
    for bi, b in enumerate(blocks):
        steps = b.get("steps") or []
        first_no = step_no + 1
        for s in steps:
            step_no += 1
            base = A.D_RCP_STEP_BASE + (step_no - 1) * A.RCP_STEP_STRIDE
            bits = 0
            for tag in (s.get("valves") or []):
                bit = DEV.valve_bit(tag)
                if bit is not None and bit < 16:
                    bits |= 1 << bit
            put(base + A.RCP_STEP_VALVE_LO, bits)
            put_dword(base + A.RCP_STEP_TIME_LO, int(s.get("time_ms") or 0))
            flags = A.RCP_FLAG_PAUSE_OK if s.get("pause_ok") else 0
            if DEV.HAS_RF and s.get("rf"):
                flags |= A.RCP_FLAG_RF_ON
            put(base + A.RCP_STEP_FLAGS, flags)

        bbase = A.D_RCP_BLOCK_BASE + bi * A.RCP_BLOCK_STRIDE
        put(bbase + A.RCP_BLOCK_FIRST, first_no)
        put(bbase + A.RCP_BLOCK_LAST, step_no)
        put_dword(bbase + A.RCP_BLOCK_REPEAT_LO, max(1, int(b.get("repeat") or 1)))
        mfc = b.get("mfc_sccm") or []
        for mi in range(8):
            # 장비에 없는 MFC·예비는 0
            raw = 0
            if mi < DEV.MFC_COUNT and mi < len(mfc):
                sc = conv.mfc.get(mi + 1)
                if sc is not None:
                    raw = sc.to_raw(mfc[mi])
            put(bbase + A.RCP_BLOCK_MFC + mi, raw)
        if DEV.HAS_PCV:
            put(bbase + A.RCP_BLOCK_PCV, conv.pcv.to_raw(b.get("pcv_pct")))
        if DEV.HAS_RF:
            put(bbase + A.RCP_BLOCK_RF, conv.rf.to_raw(b.get("rf_w")))
        if DEV.HAS_O3:
            put(bbase + A.RCP_BLOCK_O3, conv.o3.to_raw(b.get("o3")))

    for gi, g in enumerate(groups):
        gbase = A.D_RCP_GROUP_BASE + gi * A.RCP_GROUP_STRIDE
        put(gbase + 0, int(g.get("from_block") or 0))
        put(gbase + 1, int(g.get("to_block") or 0))
        put(gbase + 2, max(1, int(g.get("repeat") or 1)))

    number = recipe_number(recipe)
    put(A.D_RCP_STEP_COUNT, step_no)
    put(A.D_RCP_BLOCK_COUNT, len(blocks))
    put(A.D_RCP_GROUP_COUNT, len(groups))
    put(A.D_RCP_NO, number)
    put(A.D_RCP_SUM, 0)
    checksum = checksum_of(words)
    put(A.D_RCP_SUM, checksum)

    return {"words": words, "number": number, "checksum": checksum,
            "step_count": step_no, "block_count": len(blocks), "group_count": len(groups)}


def checksum_of(words) -> int:
    """D02003(합계 자신)을 뺀 모든 워드 합의 하위 16비트."""
    idx = A.D_RCP_SUM - A.RCP_SUM_BASE
    total = 0
    for i, v in enumerate(words):
        if i == idx:
            continue
        total = (total + (int(v) & 0xFFFF)) & 0xFFFF
    return total


def from_plc_words(words) -> dict:
    """표 → 요약(역변환). 지금 PLC 에 올라가 있는 레시피를 화면에 보여 줄 때 쓴다."""
    off = A.RCP_SUM_BASE

    def get(addr):
        i = addr - off
        return int(words[i]) & 0xFFFF if 0 <= i < len(words) else 0

    nb = get(A.D_RCP_BLOCK_COUNT)
    blocks = []
    for bi in range(min(nb, A.RCP_BLOCK_MAX)):
        b = A.D_RCP_BLOCK_BASE + bi * A.RCP_BLOCK_STRIDE
        blocks.append({
            "no": bi + 1,
            "first_step": get(b + A.RCP_BLOCK_FIRST),
            "last_step": get(b + A.RCP_BLOCK_LAST),
            "repeat": A.dword(get(b + A.RCP_BLOCK_REPEAT_LO), get(b + A.RCP_BLOCK_REPEAT_LO + 1)),
        })
    ng = get(A.D_RCP_GROUP_COUNT)
    groups = []
    for gi in range(min(ng, A.RCP_GROUP_MAX)):
        g = A.D_RCP_GROUP_BASE + gi * A.RCP_GROUP_STRIDE
        groups.append({"from_block": get(g), "to_block": get(g + 1), "repeat": get(g + 2)})
    return {
        "number": get(A.D_RCP_NO),
        "checksum": get(A.D_RCP_SUM),
        "step_count": get(A.D_RCP_STEP_COUNT),
        "block_count": nb,
        "group_count": ng,
        "blocks": blocks,
        "groups": groups,
    }


def step_index_map(recipe: dict) -> list:
    """표 전체 기준 스텝 번호 → (블록 번호, 블록 안 인덱스). 진행 표시가 쓴다."""
    out = []
    for bi, b in enumerate(recipe.get("blocks") or [], start=1):
        for si in range(len(b.get("steps") or [])):
            out.append((bi, si))
    return out
