"""
recipe_model.py — 레시피 계산·검증 (순수 함수만).

★ 순수 함수만 둔다. 파일 I/O도, 전역 상태도 없다 — 레시피 계산은 공정 안전과 직결되므로
  단위 테스트로 전부 덮을 수 있어야 한다(test/test_recipe_model.py).

레시피 구조
  name, memo
  conditions: stage_sv, soak_min, base_pressure_torr, throttle_pct, end_action
  blocks[]  : name, repeat, mfc {라인id: sccm}, steps[]
     steps[]: name, time_s, lines[] ({line, mode})
  groups[]  : {from_block, to_block, repeat}   ← 라미네이트용 반복 그룹

MFC 값을 블록 단위로 두는 이유: 유량은 설정 후 안정화에 수 초가 걸린다. 0.1 s 스텝마다
바꾸면 실제로는 따라오지 못하고 편차 알람만 난다 — 블록 시작에서 한 번 잡고 유지한다.
"""

from config import ROLE_ALD, SIDE_PRECURSOR, SIDE_REACTANT, line_by_id, line_modes

# PLC 타이머 분해능 한계. 이보다 짧은 스텝은 실제로 재현되지 않는다.
MIN_STEP_S = 0.02


# ===================== 기본 골격 =====================
def empty_recipe(name: str = "새 레시피") -> dict:
    return {
        "name": name,
        "memo": "",
        "conditions": {
            "stage_sv": 200.0,
            "soak_min": 10,
            "base_pressure_torr": 5.0e-2,
            "throttle_pct": 35,
            "end_action": "N2 퍼지 유지",
        },
        "blocks": [],
        "groups": [],
    }


# ===================== 계산 =====================
def step_open_tags(cfg: dict, step: dict) -> list:
    """이 스텝에서 열리는 밸브 태그 목록.
       = 선택한 라인 × mode 가 요구하는 역할 + process.always_open
    ★ 밸브 조합을 코드에 두지 않는다 — 전부 config 의 modes 에서 온다."""
    tags = []
    modes = cfg.get("modes") or {}
    for sel in (step or {}).get("lines") or []:
        ln = line_by_id(cfg, (sel or {}).get("line"))
        if not ln:
            continue
        roles = (modes.get((sel or {}).get("mode")) or {}).get("open") or []
        valves = ln.get("valves") or {}
        for r in roles:
            tag = valves.get(r)
            if tag and tag not in tags:
                tags.append(tag)
    for tag in (cfg.get("process") or {}).get("always_open") or []:
        if tag not in tags:
            tags.append(tag)
    return tags


def block_cycle_seconds(block: dict) -> float:
    """블록 1회(1 사이클)에 걸리는 시간."""
    return round(sum(_f(s.get("time_s")) for s in (block or {}).get("steps") or []), 3)


def block_seconds(block: dict) -> float:
    """블록 전체 시간 = 1 사이클 × repeat."""
    return round(block_cycle_seconds(block) * max(1, _i((block or {}).get("repeat"), 1)), 3)


def total_seconds(recipe: dict) -> float:
    """반복 그룹을 반영한 총 시간.
    그룹은 블록 구간을 통째로 되풀이한다 — 그룹에 속한 블록은 (repeat-1)회만큼 더 돈다."""
    blocks = (recipe or {}).get("blocks") or []
    base = [block_seconds(b) for b in blocks]
    total = sum(base)
    for g in (recipe or {}).get("groups") or []:
        a, b = _i(g.get("from_block"), 0), _i(g.get("to_block"), -1)
        rep = max(1, _i(g.get("repeat"), 1))
        if 0 <= a <= b < len(base):
            total += sum(base[a:b + 1]) * (rep - 1)
    return round(total, 3)


def summarize(cfg: dict, recipe: dict) -> dict:
    """화면이 그대로 쓰는 요약. 블록별 시간·시퀀스 요약과 총 시간."""
    blocks = (recipe or {}).get("blocks") or []
    out_blocks = []
    for b in blocks:
        steps = b.get("steps") or []
        out_blocks.append({
            "name": b.get("name", ""),
            "repeat": max(1, _i(b.get("repeat"), 1)),
            "cycle_s": block_cycle_seconds(b),
            "total_s": block_seconds(b),
            "seq": " → ".join(s.get("name", "") for s in steps),
            "step_count": len(steps),
        })
    cycles = max((bb["repeat"] for bb in out_blocks), default=0)
    return {
        "blocks": out_blocks,
        "total_s": total_seconds(recipe),
        "max_cycles": cycles,
        "soak_min": _i((recipe.get("conditions") or {}).get("soak_min"), 0),
    }


# ===================== 검증 =====================
def validate(cfg: dict, recipe: dict) -> list:
    """오류 목록을 돌려준다. [{"level","msg","where"}] — 비어 있으면 통과."""
    errs = []

    def err(msg, where="", level="err"):
        errs.append({"level": level, "msg": msg, "where": where})

    if not isinstance(recipe, dict):
        err("레시피 형식이 올바르지 않습니다")
        return errs
    if not (recipe.get("name") or "").strip():
        err("레시피 이름이 비어 있습니다")

    enabled = {ln["id"] for ln in (cfg.get("lines") or [])
               if ln.get("id") and ln.get("enabled", True)}
    known = {ln["id"] for ln in (cfg.get("lines") or []) if ln.get("id")}

    blocks = recipe.get("blocks") or []
    if not blocks:
        err("블록이 하나도 없습니다")

    for bi, b in enumerate(blocks):
        bname = (b or {}).get("name") or f"블록 {bi + 1}"
        if _i((b or {}).get("repeat"), 1) < 1:
            err("반복 횟수는 1 이상이어야 합니다", bname)
        for lid in ((b or {}).get("mfc") or {}):
            if lid not in known:
                err(f"없는 라인의 MFC 설정: {lid}", bname)
            elif lid not in enabled:
                err(f"미장착 라인의 MFC 설정: {lid}", bname)

        steps = (b or {}).get("steps") or []
        if not steps:
            err("스텝이 하나도 없습니다", bname)
        for si, s in enumerate(steps):
            sname = f"{bname} · 스텝 {si + 1}"
            t = _f((s or {}).get("time_s"))
            if t < MIN_STEP_S:
                err(f"스텝 시간이 최소값보다 짧습니다 ({t:g} s < {MIN_STEP_S:g} s)", sname)

            sides = set()
            for sel in (s or {}).get("lines") or []:
                lid = (sel or {}).get("line")
                mode = (sel or {}).get("mode")
                ln = line_by_id(cfg, lid)
                if ln is None:
                    err(f"없는 라인을 사용합니다: {lid}", sname)
                    continue
                if not ln.get("enabled", True):
                    err(f"미장착 라인을 사용합니다: {lid}", sname)
                    continue
                if mode not in line_modes(cfg, ln):
                    err(f"라인 {lid} 이(가) 지원하지 않는 공급 방식입니다: {mode}", sname)
                    continue
                # 이 mode 가 ALD 밸브를 여는 경우에만 챔버로 들어간다 → 동시 개방 판정 대상.
                roles = ((cfg.get("modes") or {}).get(mode) or {}).get("open") or []
                if ROLE_ALD in roles:
                    sides.add(ln.get("side"))
            if SIDE_PRECURSOR in sides and SIDE_REACTANT in sides:
                # ALD의 전제가 무너진다 — 기상 반응으로 챔버 안에 파티클이 생기고
                # 심하면 배관이 막힌다. 어떤 경우에도 허용하지 않는다.
                err("전구체와 반응물의 ALD 밸브가 동시에 열립니다", sname)

    for gi, g in enumerate(recipe.get("groups") or []):
        gname = f"반복 그룹 {gi + 1}"
        a, b = _i((g or {}).get("from_block"), -1), _i((g or {}).get("to_block"), -1)
        if not (0 <= a < len(blocks)) or not (0 <= b < len(blocks)):
            err("블록 범위가 레시피를 벗어납니다", gname)
        elif a > b:
            err("시작 블록이 끝 블록보다 뒤에 있습니다", gname)
        if _i((g or {}).get("repeat"), 1) < 1:
            err("반복 횟수는 1 이상이어야 합니다", gname)

    return errs


def preview(cfg: dict, recipe: dict) -> dict:
    """화면의 '검증 결과 띠'가 쓰는 한 덩어리 — 요약 + 오류 + 스텝별 열리는 밸브."""
    opens = []
    for bi, b in enumerate((recipe or {}).get("blocks") or []):
        for si, s in enumerate((b or {}).get("steps") or []):
            opens.append({"block": bi, "step": si, "tags": step_open_tags(cfg, s)})
    errors = validate(cfg, recipe)
    return {
        "summary": summarize(cfg, recipe),
        "errors": errors,
        "ok": not any(e["level"] == "err" for e in errors),
        "opens": opens,
    }


# ===================== 샘플 레시피 =====================
def sample_recipe(cfg: dict):
    """데모용 샘플 레시피. 내용은 config.demo.sample 에서 읽는다.

    ★ 막질·전구체 조합을 코드에 두지 않는다 — 챔버마다 다르고, 코드를 고치지 않고
      설정만 바꿔 납품할 수 있어야 한다. sample 설정이 없으면 만들지 않는다."""
    sp = (cfg.get("demo") or {}).get("sample") or {}
    name = sp.get("name")
    if not name:
        return None
    pl = line_by_id(cfg, sp.get("precursor_line") or "")
    rl = line_by_id(cfg, sp.get("reactant_line") or "")
    if pl is None or rl is None:
        return None
    pn = [ln for ln in (cfg.get("lines") or [])
          if ln.get("kind") == "n2" and ln.get("side") == SIDE_PRECURSOR]
    rn = [ln for ln in (cfg.get("lines") or [])
          if ln.get("kind") == "n2" and ln.get("side") == SIDE_REACTANT]
    purge_mfc = {}
    for ln in pn + rn:
        purge_mfc[ln["id"]] = float((ln.get("mfc") or {}).get("purge_sccm") or 100)

    pmode, rmode = sp.get("precursor_mode") or "vapor", sp.get("reactant_mode") or "vapor"
    carrier = float(sp.get("carrier_sccm") or 0)
    cycle_mfc = dict(purge_mfc)
    if pmode == "carrier" and carrier > 0:
        cycle_mfc[pl["id"]] = carrier

    def purge_block(bname, sec):
        return {"name": bname, "repeat": 1, "mfc": dict(purge_mfc),
                "steps": [{"name": "N2 퍼지", "time_s": float(sec), "lines": []}]}

    rec = empty_recipe(name)
    rec["memo"] = sp.get("memo", "")
    rec["conditions"].update({
        "stage_sv": float(sp.get("stage_sv") or 200),
        "soak_min": int(sp.get("soak_min") or 10),
        "base_pressure_torr": float((cfg.get("vacuum") or {}).get("start_base_torr") or 5e-2),
        "throttle_pct": int(sp.get("throttle_pct") or 35),
        "end_action": "N2 퍼지 유지",
    })
    rec["blocks"] = [
        purge_block("Pre-purge", sp.get("pre_purge_s") or 60),
        {
            "name": sp.get("film") or "ALD",
            "repeat": int(sp.get("cycles") or 100),
            "mfc": cycle_mfc,
            "steps": [
                {"name": f"{pl.get('material', '')} 펄스", "time_s": float(sp.get("pulse_p_s") or 0.1),
                 "lines": [{"line": pl["id"], "mode": pmode}]},
                {"name": "N2 퍼지", "time_s": float(sp.get("purge1_s") or 10), "lines": []},
                {"name": f"{rl.get('material', '')} 펄스", "time_s": float(sp.get("pulse_r_s") or 0.1),
                 "lines": [{"line": rl["id"], "mode": rmode}]},
                {"name": "N2 퍼지", "time_s": float(sp.get("purge2_s") or 15), "lines": []},
            ],
        },
        purge_block("Post-purge", sp.get("post_purge_s") or 120),
    ]
    return rec


# ===================== 작은 도우미 =====================
def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
