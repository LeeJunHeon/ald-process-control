"""
config.py — 설정 파일 로드·기본값 병합·검증.

설정에는 "현장마다 다른 값"만 둔다: 서버·PLC 주소, 아날로그 환산, MFC 풀스케일,
히터 한계, PLC 파라미터. 장비 구조(밸브·보조 출력·입력·알람 문구·배관도)는
device.py 에 있다 — 설정 파일을 잘못 복사해도 장비가 바뀌어 보이면 안 되기 때문이다.

★ 모르는 값은 지어내지 않는다. 예시값 + confirmed=false 로 두고, 화면에서 그 값 옆에
  "환산 미확정"을 붙인다. 현장에서 확인하면 설정만 고치면 된다.

★ 검증은 오류를 모아서 돌려줄 뿐 기동을 막지 않는다. 설정 한 줄이 틀렸다고 화면이
  아예 안 뜨면 현장에서 원인조차 볼 수 없다.
"""

import os
import copy
import json

from . import paths
from . import addresses as A
from . import device as DEV
from .convert import Converters

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": DEV.DEFAULT_PORT},
    "window": {"side": DEV.DEFAULT_SIDE},
    "plc": {
        "host": "", "port": 502, "unit_id": 1,     # ★ 주소 기본값은 없다(다른 장비에 붙지 않게)
        "timeout_ms": 1000, "poll_ms": 100, "heartbeat_ms": 500,
        "simulate": True, "sim_port": DEV.DEFAULT_SIM_PORT, "sim_speed": 5,
        # 장비 ID 필수 — 켜면 ID 0 인 PLC 도 막는다(두 PLC 에 ID 렁을 넣은 뒤 켠다)
        "require_device_id": False,
    },
    "analog": {"raw_max": 16000, "confirmed": False},
    "pressure": {},
    "mfc": [],
    "heaters": [],
    "params": {},
    "process": {
        # 공정 시작 흐름
        "base_wait_timeout_s": 1800,     # 베이스 압력 대기 제한
        "base_stable_s": 3,              # 이만큼 계속 도달해 있어야 시작한다
        "heater_ready": {"enabled": False, "band_c": 2.0, "stable_s": 60},
    },
    "log": {"level": "info", "keep_days": 90,
            "datalog_interval_s": 1, "datalog_keep_days": 180, "trend_keep_days": 90},
    "access": {"local_only": True},
}


def _deep_merge(base: dict, over: dict) -> dict:
    """dict 만 재귀하고 list 는 통째로 교체한다
    (MFC·히터 목록을 부분 병합하면 지운 항목이 되살아난다)."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(path: str = ""):
    """(config, problems, source) 를 돌려준다.
    source: "file" 실제 설정 / "example" 예시 설정 / "default" 기본값."""
    problems = []
    target = os.path.abspath(path) if path else paths.DEFAULT_CONFIG_PATH
    raw, source = None, "default"

    raw, problems_read = _read(target)
    problems.extend(problems_read)
    if raw is not None:
        source = "file"
    else:
        # config.json 이 없으면 예시 설정으로 기동한다 — 처음 켠 사람이
        # 아무것도 못 보는 것보다 낫다. 대신 화면에 경고를 크게 띄운다.
        ex, _ = _read(paths.EXAMPLE_CONFIG)
        if ex is not None:
            raw, source = ex, "example"
            problems.append(("warn", "config.json 이 없어 예시 설정으로 실행 중입니다 — "
                                     "현장 값을 넣은 config.json 을 exe 옆에 두세요"))
        else:
            problems.append(("err", "설정 파일과 예시 설정을 모두 읽지 못했습니다 — 기본값으로 기동합니다"))
            raw = {}

    cfg = _deep_merge(DEFAULTS, raw)
    cfg["_source"] = source
    cfg["_path"] = target if source == "file" else paths.EXAMPLE_CONFIG
    _fill_devices(cfg)
    problems.extend(validate(cfg))
    return cfg, problems, source


def _read(p: str):
    if not p or not os.path.isfile(p):
        return None, []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, [("err", f"{os.path.basename(p)}: 최상위가 객체가 아닙니다")]
        return data, []
    except Exception as e:  # noqa: BLE001
        return None, [("err", f"{os.path.basename(p)} 을(를) 읽지 못했습니다 ({type(e).__name__})")]


def _fill_devices(cfg: dict):
    """MFC·히터 목록에 빠진 항목을 장비 정의의 기본 이름으로 채운다.
    설정이 짧아도 화면의 표가 12채널·N개 MFC 를 그릴 수 있어야 한다."""
    mfc = {int(m.get("no") or 0): m for m in (cfg.get("mfc") or []) if m.get("no")}
    out = []
    for i in range(1, DEV.MFC_COUNT + 1):
        m = mfc.get(i) or {}
        out.append({
            "no": i,
            "name": m.get("name") or DEV.MFC_DEFAULT_NAMES[i - 1],
            "gas": m.get("gas", ""),
            "full_scale_sccm": m.get("full_scale_sccm"),
            "confirmed": bool(m.get("confirmed", False)),
        })
    cfg["mfc"] = out

    heat = {int(h.get("ch") or 0): h for h in (cfg.get("heaters") or []) if h.get("ch")}
    out = []
    for ch in range(1, DEV.HEATER_COUNT + 1):
        h = heat.get(ch) or {}
        out.append({
            "ch": ch,
            "name": h.get("name") or DEV.HEATER_DEFAULT_NAMES[ch - 1],
            "enabled": bool(h.get("enabled", False)),
            "max_c": h.get("max_c"),
            "default_sv": h.get("default_sv"),
            # 온도조절기 국번(1~3). 없으면 4채널씩 묶은 기본 배선으로 본다.
            "station": h.get("station") if h.get("station") is not None else (ch - 1) // 4 + 1,
        })
    cfg["heaters"] = out


# PRM 규칙 — 래더 P00 은 첫 스캔에만 0 인 PRM 을 기본값으로 바꾸고, 그 뒤에는 PC 가 쓴 값을
# 타이머 설정값·비교값으로 그대로 쓴다(TON 설정값 0 = 조건이 서는 즉시 완료).
#   (키, 최소, 최대, 설명) — 초 단위는 ×10 해서 100 ms 타이머로 쓰므로 워드(65535) 안이 되게 6553 s 까지.
PRM_INT_RULES = [
    ("pc_wdt_ms", 1, 65535, "0 이면 첫 하트비트 뒤 곧바로 PC 통신 끊김"),
    ("pump_timeout_s", 1, 6553, "0 이면 IV-E 가 열리자마자 펌핑 시간 초과"),
    ("vent_timeout_s", 1, 6553, "0 이면 VV 가 열리기 전에 벤트 시간 초과"),
    ("mfc_timeout_s", 1, 6553, "0 이면 모든 블록 준비에서 MFC 시간 초과"),
    ("mfc_stable_s", 0, 6553, "0 = 안정 대기 없음"),
    ("valve_min_ms", 0, 65535, "0 = 최소 열림 없음"),
]
if DEV.HAS_RF:
    PRM_INT_RULES.append(("rf_ref_ms", 0, 65535, "0 = 반사 초과 즉시 알람"))
# 반드시 0 보다 커야 하는 값(비어 있으면 0 으로 써진다)
PRM_REQUIRED_POS = []
if DEV.HAS_RF:
    PRM_REQUIRED_POS += [
        ("rf_max_w", "0 이면 RF 금지"),
        ("rf_ref_max_w", "비거나 0 이면 첫 RF 스텝에서 반사 알람 → 안전 정지(래더에 0 보호 없음)"),
        ("rf_p_max_torr", "0 이면 RF 허가(인터락 b8)가 나지 않음"),
    ]
if DEV.HAS_O3:
    PRM_REQUIRED_POS.append(("o3_max", "0 이면 O3 허가(인터락 b9)가 나지 않음"))


def prm_problems(cfg: dict) -> list:
    """PLC 파라미터 오류 목록(문구). 비어 있으면 PLC 에 써도 된다.
    ★ 설정 불러오기·설정 편집 저장·PLC 링크가 같은 규칙을 쓴다."""
    out = []
    prm = cfg.get("params") or {}
    for key, lo, hi, why in PRM_INT_RULES:
        v = prm.get(key)
        if v is None or v == "":
            continue                        # 비어 있으면 PLC 링크가 기본값을 쓴다
        try:
            f = float(v)
            if f != int(f):
                raise ValueError
            iv = int(f)
        except (TypeError, ValueError, OverflowError):
            out.append(f"params.{key} 는 정수여야 합니다: {v!r}")
            continue
        if not (lo <= iv <= hi):
            out.append(f"params.{key} 는 {lo}~{hi} 여야 합니다 (지금 {iv}) — {why}")
    for key, why in PRM_REQUIRED_POS:
        v = prm.get(key)
        try:
            ok = v is not None and float(v) > 0
        except (TypeError, ValueError, OverflowError):
            ok = False
        if not ok:
            out.append(f"params.{key} 는 0 보다 커야 합니다 (지금 {v!r}) — {why}")
    tol = prm.get("mfc_tol_sccm")
    if tol is not None:
        try:
            if float(tol) < 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            out.append(f"params.mfc_tol_sccm 는 0 이상이어야 합니다: {tol!r}")
    return out


def validate(cfg: dict) -> list:
    """[(level, message)] — 비어 있으면 정상."""
    p = []

    srv = cfg.get("server") or {}
    try:
        port = int(srv.get("port", 0))
        if not (1 <= port <= 65535):
            raise ValueError
    except (TypeError, ValueError):
        p.append(("err", f"server.port 값이 올바르지 않습니다: {srv.get('port')!r}"))
    if (cfg.get("window") or {}).get("side") not in ("left", "right"):
        p.append(("warn", "window.side 는 left 또는 right 여야 합니다"))

    plc = cfg.get("plc") or {}
    if not plc.get("simulate") and not str(plc.get("host") or "").strip():
        p.append(("err", "plc.host 가 비어 있습니다 — 시뮬레이터가 아니면 이 장비 PLC 주소가 필요합니다 "
                         "(비어 있으면 연결하지 않습니다)"))
    for key, lo, hi in (("poll_ms", 20, 5000), ("heartbeat_ms", 100, 5000),
                        ("timeout_ms", 100, 10000)):
        try:
            v = int(plc.get(key))
            if not (lo <= v <= hi):
                p.append(("warn", f"plc.{key} 가 권장 범위({lo}~{hi})를 벗어납니다: {v}"))
        except (TypeError, ValueError):
            p.append(("warn", f"plc.{key} 값이 올바르지 않습니다"))
    try:
        wdt = int((cfg.get("params") or {}).get("pc_wdt_ms") or A.PRM_DEFAULTS["pc_wdt_ms"])
        # ★ 응답 하나를 잃었을 때의 최악 공백 = 응답 제한 + 하트비트 주기 + 재연결 여유(500 ms).
        #   이것이 PC 하트비트 판정보다 짧지 않으면 PLC 가 PC 끊김으로 공정을 세울 수 있다.
        worst = int(plc.get("timeout_ms")) + int(plc.get("heartbeat_ms")) + 500
        if worst >= wdt:
            p.append(("err", f"응답 하나를 잃었을 때의 최악 하트비트 공백({worst} ms = plc.timeout_ms "
                             f"{plc.get('timeout_ms')} + plc.heartbeat_ms {plc.get('heartbeat_ms')} + 500)이 "
                             f"params.pc_wdt_ms({wdt}) 이상입니다 — PLC 가 PC 끊김으로 공정을 세울 수 있습니다"))
        if int(plc.get("heartbeat_ms")) > wdt / 3:
            p.append(("err", f"plc.heartbeat_ms({plc.get('heartbeat_ms')})가 "
                             f"PC 하트비트 판정(params.pc_wdt_ms={wdt})의 1/3 보다 깁니다 — "
                             f"PLC 가 PC 끊김으로 보고 안전 정지할 수 있습니다"))
    except (TypeError, ValueError):
        pass
    if int(plc.get("sim_port") or 0) == int(srv.get("port") or 0):
        p.append(("err", "plc.sim_port 와 server.port 가 같습니다 — 포트를 나눠야 합니다"))

    analog = cfg.get("analog") or {}
    try:
        if int(analog.get("raw_max")) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        p.append(("err", "analog.raw_max 값이 올바르지 않습니다"))

    # 환산이 단조 증가인지 — 역함수를 쓰기 때문에 반드시 확인한다.
    conv = Converters(cfg)
    if not conv.cvg.is_monotonic():
        p.append(("err", "pressure.cvg 환산이 단조 증가가 아닙니다 — "
                         "베이스 압력을 원시값으로 바꿀 수 없습니다"))
    if conv.cm.installed and not conv.cm.is_monotonic():
        p.append(("err", "pressure.cm 환산이 단조 증가가 아닙니다"))

    for m in cfg.get("mfc") or []:
        fs = m.get("full_scale_sccm")
        if fs is None:
            p.append(("warn", f"MFC{m['no']} ({m['name']}) 풀스케일이 정해지지 않았습니다 — "
                              f"유량 표시가 '—' 로 나옵니다"))
        else:
            try:
                if float(fs) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                p.append(("err", f"MFC{m['no']} 풀스케일 값이 올바르지 않습니다: {fs!r}"))

    for h in cfg.get("heaters") or []:
        try:
            stn = int(h.get("station"))
            if not (1 <= stn <= 3):
                raise ValueError
        except (TypeError, ValueError):
            p.append(("err", f"히터 CH{h['ch']}: station(온도조절기 국번)은 1~3 이어야 합니다: "
                             f"{h.get('station')!r}"))
        if not h.get("enabled"):
            continue
        if h.get("max_c") is None:
            # ★ PLC 는 한계 0 인 채널의 소프트 과온 감시만 안 할 뿐 전원을 막지 않는다.
            #   감시 없는 히터가 켜지지 않도록 PC 가 목표 온도·전원 켜기를 거절한다.
            p.append(("warn", f"히터 CH{h['ch']} ({h['name']}) 과온 한계가 정해지지 않았습니다 — "
                              f"PLC 소프트 과온 감시가 꺼지므로 PC 가 이 채널의 설정·전원 켜기를 막습니다"))
        sv = h.get("default_sv")
        mx = h.get("max_c")
        if sv is not None and mx is not None and float(sv) > float(mx):
            p.append(("err", f"히터 CH{h['ch']}: 기본 설정 온도({sv})가 과온 한계({mx})보다 높습니다"))

    pr = cfg.get("process") or {}
    for key, lo, hi in (("base_wait_timeout_s", 10, 86400), ("base_stable_s", 0, 3600)):
        try:
            v = float(pr.get(key))
            if not (lo <= v <= hi):
                p.append(("warn", f"process.{key} 가 권장 범위({lo}~{hi})를 벗어납니다: {v:g}"))
        except (TypeError, ValueError):
            p.append(("warn", f"process.{key} 값이 올바르지 않습니다"))
    hr = pr.get("heater_ready") or {}
    if hr.get("enabled"):
        try:
            if float(hr.get("band_c")) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            p.append(("err", "process.heater_ready.band_c 값이 올바르지 않습니다"))

    lg = cfg.get("log") or {}
    try:
        if not (0.2 <= float(lg.get("datalog_interval_s", 1)) <= 60):
            p.append(("warn", "log.datalog_interval_s 는 0.2~60 s 가 알맞습니다"))
    except (TypeError, ValueError):
        p.append(("warn", "log.datalog_interval_s 값이 올바르지 않습니다"))
    for key in ("keep_days", "datalog_keep_days", "trend_keep_days"):
        try:
            if int(lg.get(key)) < 0:
                raise ValueError
        except (TypeError, ValueError):
            p.append(("err", f"log.{key} 는 0 이상의 정수여야 합니다 (0 = 지우지 않음)"))
    try:
        if not (1 <= int(plc.get("port")) <= 65535) or not (0 <= int(plc.get("unit_id")) <= 255):
            raise ValueError
    except (TypeError, ValueError):
        p.append(("err", "plc.port(1~65535) 또는 plc.unit_id(0~255) 값이 올바르지 않습니다"))

    for msg in prm_problems(cfg):
        p.append(("err", msg))
    prm = cfg.get("params") or {}
    if prm.get("base_press_torr") is None:
        p.append(("warn", "params.base_press_torr 가 없습니다 — PLC 가 공정 시작을 막습니다"))
    elif conv.cvg.to_raw(prm.get("base_press_torr")) <= 0:
        p.append(("warn", "params.base_press_torr 가 환산에서 원시값 0 이 됩니다 — "
                          "PLC 가 공정 시작을 막습니다"))
    if DEV.HAS_RF and not (cfg.get("rf") or {}).get("max_w"):
        p.append(("warn", "rf.max_w 가 없습니다 — PLC 가 RF 를 막습니다"))
    if DEV.HAS_O3 and not (cfg.get("o3") or {}).get("full"):
        p.append(("warn", "o3.full 이 없습니다 — PLC 가 O3 를 막습니다"))

    return p
