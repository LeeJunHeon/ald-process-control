"""
config.py — 설정 파일 로드·기본값 병합·검증.

★ 이 모듈이 "장비 구성의 유일한 출처"다. 라인 개수, 밸브 태그, 공급 방식(mode),
  히터 채널, 인터락 목록은 전부 여기서 나온다. 다른 모듈은 장비 이름이나 밸브 태그를
  절대 하드코딩하지 않는다 — 현장마다 구성이 다르고, 코드를 고치지 않고 납품해야 한다.

검증은 오류를 모아서 돌려줄 뿐 기동을 막지 않는다. 설정 한 줄이 틀렸다고 화면이
아예 안 뜨면 현장에서 원인조차 볼 수 없기 때문이다(문제는 화면 로그·파일 로그로 알린다).
"""

import os
import re
import json
import copy

# ===================== 상수 =====================
# 밸브 역할. 배관도 행 템플릿과 mode 정의가 공유한다.
ROLE_IN = "IN"
ROLE_OUT = "OUT"
ROLE_BYP = "BYP"
ROLE_ALD = "ALD"
VALID_ROLES = (ROLE_IN, ROLE_OUT, ROLE_BYP, ROLE_ALD)

# 라인 종류
KIND_N2 = "n2"
KIND_CANISTER = "canister"
KIND_GAS = "gas"
VALID_KINDS = (KIND_N2, KIND_CANISTER, KIND_GAS)

SIDE_PRECURSOR = "precursor"
SIDE_REACTANT = "reactant"
VALID_SIDES = (SIDE_PRECURSOR, SIDE_REACTANT)

# 밸브 태그 규칙: <라인id>-<역할>
_TAG_RE = re.compile(r"^[A-Za-z0-9_]+-(IN|OUT|BYP|ALD)$")

DEFAULTS = {
    "chamber": {"id": "chamber", "name": "ALD", "subtitle": ""},
    "server": {"host": "127.0.0.1", "port": 8001},
    "ui": {"theme": "light", "accent": "#1f3b73", "window": {"side": "left"}},
    "plc": {"ip": "", "port": 502, "unit_id": 1, "poll_ms": 200,
            "heartbeat_s": 1.0, "timeout_s": 3.0},
    "demo": {"enabled": True, "scenario": "idle"},
    "lines": [],
    "modes": {},
    "process": {"always_open": []},
    "chamber_io": {},
    "gauges": {},
    "heaters": [],
    "interlocks": [],
    "alarms": {"mfc_dev_pct_fs": 2.0, "mfc_delay_s": 5},
    "vacuum": {"start_base_torr": 5.0e-2, "base_timeout_s": 600,
               "vent_timeout_s": 300, "process_dev_pct": 20},
    "log": {"enabled": True, "level": "info", "keep_days": 90,
            "datalog_interval_s": 1, "datalog_keep_days": 180},
    "access": {"local_only": True},
}


def _deep_merge(base: dict, over: dict) -> dict:
    """기본값 위에 사용자 설정을 덮는다. dict만 재귀하고 list는 통째로 교체한다
    (라인 목록을 부분 병합하면 삭제한 라인이 되살아난다)."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(path: str):
    """설정 파일을 읽어 (config, problems) 를 돌려준다.
    파일이 없거나 깨져도 기본값으로 계속 간다 — 화면은 떠야 원인을 볼 수 있다."""
    problems = []
    raw = {}
    if not path:
        problems.append(("warn", "설정 파일 경로가 지정되지 않았습니다 — 기본값으로 기동합니다"))
    elif not os.path.isfile(path):
        problems.append(("warn", f"설정 파일이 없습니다: {os.path.basename(path)} — 기본값으로 기동합니다"))
    else:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                problems.append(("err", "설정 파일의 최상위가 객체가 아닙니다 — 기본값으로 기동합니다"))
                raw = {}
        except Exception as e:  # noqa: BLE001
            problems.append(("err", f"설정 파일을 읽지 못했습니다 ({type(e).__name__}) — 기본값으로 기동합니다"))
            raw = {}

    cfg = _deep_merge(DEFAULTS, raw)
    cfg["_source"] = os.path.basename(path) if path else ""
    problems.extend(validate(cfg))
    return cfg, problems


def validate(cfg: dict) -> list:
    """누락 키, 중복 id, 존재하지 않는 mode·역할, 태그 규칙 위반을 찾는다.
    반환: [(level, message)] — 비어 있으면 정상."""
    p = []

    # --- chamber / server / ui ---
    ch = cfg.get("chamber") or {}
    if not ch.get("id"):
        p.append(("err", "chamber.id 가 없습니다"))
    if not ch.get("name"):
        p.append(("warn", "chamber.name 이 없습니다"))
    srv = cfg.get("server") or {}
    try:
        port = int(srv.get("port", 0))
        if not (1 <= port <= 65535):
            raise ValueError
    except (TypeError, ValueError):
        p.append(("err", f"server.port 값이 올바르지 않습니다: {srv.get('port')!r}"))
    ui = cfg.get("ui") or {}
    if ui.get("theme") not in ("light", "dark"):
        p.append(("warn", f"ui.theme 은 light 또는 dark 여야 합니다: {ui.get('theme')!r}"))
    if (ui.get("window") or {}).get("side") not in ("left", "right"):
        p.append(("warn", "ui.window.side 는 left 또는 right 여야 합니다"))

    # --- modes ---
    modes = cfg.get("modes") or {}
    if not modes:
        p.append(("err", "modes 가 비어 있습니다 — 공급 방식을 정의해야 레시피를 만들 수 있습니다"))
    for mid, m in modes.items():
        roles = (m or {}).get("open")
        if not isinstance(roles, list) or not roles:
            p.append(("err", f"mode '{mid}' 의 open 역할 목록이 비어 있습니다"))
            continue
        for r in roles:
            if r not in VALID_ROLES:
                p.append(("err", f"mode '{mid}' 에 알 수 없는 밸브 역할: {r!r}"))
        for k in (m or {}).get("kinds") or []:
            if k not in VALID_KINDS:
                p.append(("err", f"mode '{mid}' 에 알 수 없는 라인 종류: {k!r}"))

    # --- lines ---
    lines = cfg.get("lines") or []
    if not lines:
        p.append(("err", "lines 가 비어 있습니다"))
    seen_ids = set()
    seen_tags = set()
    for i, ln in enumerate(lines):
        ln = ln or {}
        lid = ln.get("id")
        where = f"lines[{i}]"
        if not lid:
            p.append(("err", f"{where}: id 가 없습니다"))
            continue
        where = f"라인 {lid}"
        if lid in seen_ids:
            p.append(("err", f"{where}: id 가 중복됩니다"))
        seen_ids.add(lid)
        if ln.get("kind") not in VALID_KINDS:
            p.append(("err", f"{where}: kind 가 올바르지 않습니다 ({ln.get('kind')!r})"))
        if ln.get("side") not in VALID_SIDES:
            p.append(("err", f"{where}: side 는 precursor 또는 reactant 여야 합니다"))
        mfc = ln.get("mfc") or {}
        try:
            if float(mfc.get("full_scale", 0)) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            p.append(("warn", f"{where}: mfc.full_scale 값이 올바르지 않습니다"))
        valves = ln.get("valves") or {}
        if not isinstance(valves, dict) or not valves:
            p.append(("err", f"{where}: valves 가 없습니다"))
            continue
        for role, tag in valves.items():
            if role not in VALID_ROLES:
                p.append(("err", f"{where}: 알 수 없는 밸브 역할 {role!r}"))
                continue
            if not isinstance(tag, str) or not _TAG_RE.match(tag):
                p.append(("err", f"{where}: 밸브 태그 규칙 위반 {tag!r} (<라인id>-<역할> 이어야 합니다)"))
                continue
            if tag != f"{lid}-{role}":
                p.append(("err", f"{where}: 밸브 태그가 라인·역할과 맞지 않습니다 ({tag} ≠ {lid}-{role})"))
            if tag in seen_tags:
                p.append(("err", f"밸브 태그가 중복됩니다: {tag}"))
            seen_tags.add(tag)
        if ROLE_ALD not in valves:
            p.append(("err", f"{where}: ALD 밸브가 없습니다"))

    # --- process.always_open ---
    for tag in (cfg.get("process") or {}).get("always_open") or []:
        if tag not in seen_tags:
            p.append(("err", f"process.always_open 에 존재하지 않는 밸브 태그: {tag}"))

    # --- heaters ---
    seen_h = set()
    for h in cfg.get("heaters") or []:
        hid = (h or {}).get("id")
        if not hid:
            p.append(("err", "heaters 항목에 id 가 없습니다"))
            continue
        if hid in seen_h:
            p.append(("err", f"히터 id 가 중복됩니다: {hid}"))
        seen_h.add(hid)
        link = (h or {}).get("line")
        if link and link not in seen_ids:
            p.append(("warn", f"히터 {hid}: 존재하지 않는 라인을 참조합니다 ({link})"))

    # --- interlocks ---
    seen_k = set()
    for k in cfg.get("interlocks") or []:
        kid = (k or {}).get("id")
        if not kid:
            p.append(("err", "interlocks 항목에 id 가 없습니다"))
        elif kid in seen_k:
            p.append(("err", f"인터락 id 가 중복됩니다: {kid}"))
        else:
            seen_k.add(kid)

    return p


# ===================== 조회 도우미 =====================
def enabled_lines(cfg: dict) -> list:
    """enabled=false(미장착)를 뺀 라인 목록."""
    return [ln for ln in (cfg.get("lines") or []) if ln.get("enabled", True)]


def line_by_id(cfg: dict, line_id: str):
    for ln in cfg.get("lines") or []:
        if ln.get("id") == line_id:
            return ln
    return None


def line_modes(cfg: dict, line: dict) -> list:
    """이 라인이 지원하는 mode id 목록.

    ★ 밸브 조합을 코드에 하드코딩하지 않기 위한 핵심 규칙 — mode 가 요구하는 역할을
      라인이 전부 가지고 있으면 지원한다.
    ★ 역할만으로는 부족한 경우가 있다. 예를 들어 ALD 만 여는 방식은 캐니스터 라인에서
      기술적으로 '가능'하지만(모든 라인이 ALD 밸브를 가진다) 실제로는 아무것도 흐르지
      않는다. 그래서 mode 가 kinds 로 적용 대상 라인 종류를 좁힐 수 있게 둔다 —
      이 판단 역시 코드가 아니라 설정에 있어야 현장마다 바꿀 수 있다.
    """
    have = set((line or {}).get("valves") or {})
    kind = (line or {}).get("kind")
    out = []
    for mid, m in (cfg.get("modes") or {}).items():
        m = m or {}
        roles = set(m.get("open") or [])
        kinds = m.get("kinds")
        if kinds and kind not in kinds:
            continue
        if roles and roles <= have:
            out.append(mid)
    return out


def all_valve_tags(cfg: dict) -> list:
    """설정에 정의된 모든 밸브 태그(라인 + 챔버 IO). 상태 초기화에 쓴다."""
    tags = []
    for ln in cfg.get("lines") or []:
        for tag in (ln.get("valves") or {}).values():
            if isinstance(tag, str):
                tags.append(tag)
    for key in ("vent", "rv"):
        io = (cfg.get("chamber_io") or {}).get(key) or {}
        if io.get("tag"):
            tags.append(io["tag"])
    return tags
