"""
settings.py — 설정 편집 (설정 탭, 관리자 PIN 필요).

흐름: 편집값 → 서버 검증(config.validate) → 바뀌는 항목 표(PRM 은 원시값 병기) →
      확인 → config.json 원자적 저장 + 백업 → 로그 → 환산 다시 만들기 →
      PLC 연결 중이면 PRM 다시 쓰고 되읽기 → 스냅샷 다시 보냄.

★ 화면이 보내는 것은 '경로 → 값' 쌍뿐이다(예: "params.base_press_torr": 0.03).
  여기 FIELDS 에 없는 경로는 받지 않는다 — 화면이 장비 구조·서버 포트·access 를
  바꾸지 못하게 한다(access.local_only 는 읽기 전용).
★ 파일에 원래 있던 "_" 로 시작하는 설명 키는 그대로 둔다. 편집한 값만 바꿔 쓴다.
★ 시퀀서 동작 중(공정 준비·실행·일시정지·사이클 후 정지 예약)에는 저장하지 않는다.
"""

import os
import copy
import glob
import json
import shutil
import time

from . import config as C
from . import device as DEV
from . import logger
from . import paths
from .convert import Converters
from .plclink import PRM_KEYS, param_words
from .storage import atomic_write_json

BACKUP_KEEP = 20

# (경로, 종류, null 허용, 다시 시작해야 반영, 이름)
#   종류: num(실수) / int(정수) / bool / str
_F = []


def _add(path, kind, label, nullable=False, restart=False):
    _F.append({"path": path, "kind": kind, "label": label,
               "nullable": nullable, "restart": restart})


# --- PLC 파라미터 ---
_add("params.pc_wdt_ms", "int", "PC Heartbeat Timeout ms")
_add("params.base_press_torr", "num", "Base Pressure Torr", nullable=True)
_add("params.pump_timeout_s", "int", "Pump-down Timeout s")
_add("params.vent_timeout_s", "int", "Vent Timeout s")
_add("params.mfc_stable_s", "int", "MFC Stable Time s")
_add("params.mfc_tol_sccm", "num", "MFC1 Tolerance sccm", nullable=True)
_add("params.mfc_timeout_s", "int", "MFC Stable Timeout s")
_add("params.valve_min_ms", "int", "Pulse Valve Min Open ms")
if DEV.HAS_RF:
    _add("params.rf_max_w", "num", "RF Setpoint Max W", nullable=True)
    _add("params.rf_ref_max_w", "num", "RF Reflected Limit W", nullable=True)
    _add("params.rf_ref_ms", "int", "RF Reflected Over ms")
    _add("params.rf_p_max_torr", "num", "RF Permit Max Pressure Torr", nullable=True)
if DEV.HAS_O3:
    _add("params.o3_max", "num", "O3 Setpoint Max", nullable=True)

# --- MFC (개수는 장비 고정) ---
for _no in range(1, DEV.MFC_COUNT + 1):
    _add(f"mfc.{_no}.name", "str", f"MFC{_no} Name")
    _add(f"mfc.{_no}.gas", "str", f"MFC{_no} Gas")
    _add(f"mfc.{_no}.full_scale_sccm", "num", f"MFC{_no} Full Scale sccm", nullable=True)
    _add(f"mfc.{_no}.confirmed", "bool", f"MFC{_no} Confirmed")

# --- 히터 ---
for _ch in range(1, DEV.HEATER_COUNT + 1):
    _add(f"heaters.{_ch}.name", "str", f"CH{_ch} Name")
    _add(f"heaters.{_ch}.enabled", "bool", f"CH{_ch} Enabled")
    _add(f"heaters.{_ch}.max_c", "num", f"CH{_ch} Overtemp Limit ℃", nullable=True)
    _add(f"heaters.{_ch}.default_sv", "num", f"CH{_ch} Default Setpoint ℃", nullable=True)
    _add(f"heaters.{_ch}.station", "int", f"CH{_ch} TC Station")

# --- 환산 ---
_add("analog.raw_max", "int", "Analog Raw Max")
_add("analog.confirmed", "bool", "Analog Confirmed")
for _g in ("cvg", "cm"):
    _n = "CVG" if _g == "cvg" else "CM"
    if _g == "cm":
        _add("pressure.cm.installed", "bool", "CM Installed")
    _add(f"pressure.{_g}.mode", "str", f"{_n} Mode")
    _add(f"pressure.{_g}.volt_max", "num", f"{_n} Max Voltage V")
    _add(f"pressure.{_g}.torr_at_0v", "num", f"{_n} Pressure at 0 V Torr")
    _add(f"pressure.{_g}.decades_per_volt", "num", f"{_n} decade/V")
    _add(f"pressure.{_g}.torr_per_volt", "num", f"{_n} Torr/V")
    _add(f"pressure.{_g}.confirmed", "bool", f"{_n} Confirmed")
if DEV.HAS_RF:
    _add("rf.max_w", "num", "RF Full Scale W", nullable=True)
    _add("rf.confirmed", "bool", "RF Confirmed")
if DEV.HAS_PCV:
    _add("pcv.full_pct", "num", "PCV Full Scale %")
    _add("pcv.confirmed", "bool", "PCV Confirmed")
if DEV.HAS_O3:
    _add("o3.unit", "str", "O3 Unit")
    _add("o3.full", "num", "O3 Full Scale", nullable=True)
    _add("o3.confirmed", "bool", "O3 Confirmed")

# --- 공정 ---
_add("process.base_wait_timeout_s", "num", "Base Wait Timeout s")
_add("process.base_stable_s", "num", "Base Stable Time s")
_add("process.heater_ready.enabled", "bool", "Heater Ready in Start Check")
_add("process.heater_ready.band_c", "num", "Heater Ready Band ±℃")
_add("process.heater_ready.stable_s", "num", "Heater Ready Hold s")
if DEV.HAS_O3:
    _add("process.o3_off_delay_s", "num", "O3 Off Bypass Delay s")
# v0.7.0(A5): PC 감시 — 프로그램이 꺼져 있으면 동작하지 않는다(이름에 'PC Watch')
_add("process.press_high_torr", "num", "PC Watch: Process Pressure High Torr (0 = off)")
_add("process.press_high_s", "num", "PC Watch: Pressure High Hold s")
_add("process.press_high_abort", "bool", "PC Watch: Abort on Pressure High")
_add("process.heater_dev_c", "num", "PC Watch: Heater Deviation ±℃ (0 = off)")
_add("process.heater_dev_s", "num", "PC Watch: Heater Deviation Hold s")

# --- 정비 (v0.7.0 A3 · A4) ---
_add("leakcheck.settle_s", "num", "Leak Check Settle s")
_add("leakcheck.measure_s", "num", "Leak Check Measure s")
_add("leakcheck.limit_mtorr_min", "num", "Leak Check Limit mTorr/min")
_add("leakcheck.max_torr", "num", "Leak Check Stop Above Torr")
_add("maintenance.valve_opens_warn", "num", "Valve Opens Warn (0 = off)")
_add("maintenance.pump_hours_warn", "num", "Pump Hours Warn h (0 = off)")
if DEV.HAS_RF:
    _add("maintenance.rf_hours_warn", "num", "RF Hours Warn h (0 = off)")
if DEV.HAS_O3:
    _add("maintenance.o3_hours_warn", "num", "O3 Generator Hours Warn h (0 = off)")

# --- 로그 ---
_add("window.theme", "str", "Theme (light / dark)")
_add("log.keep_days", "int", "Program Log Keep days")
_add("log.datalog_interval_s", "num", "Data Log Interval s")
_add("log.datalog_keep_days", "int", "Data Log Keep days")
_add("log.trend_keep_days", "int", "Trend Keep days")

# --- PLC 연결 (다시 시작해야 반영) ---
_add("plc.host", "str", "PLC Address", restart=True)
_add("plc.port", "int", "PLC Port", restart=True)
_add("plc.unit_id", "int", "Unit ID", restart=True)
_add("plc.timeout_ms", "int", "Response Timeout ms", restart=True)
_add("plc.poll_ms", "int", "Poll Interval ms", restart=True)
_add("plc.heartbeat_ms", "int", "Heartbeat Interval ms", restart=True)
_add("plc.simulate", "bool", "Use Built-in Simulator", restart=True)
_add("plc.require_device_id", "bool", "Require Device ID", restart=True)

FIELDS = {f["path"]: f for f in _F}


# ===================== 경로 도우미 =====================
def _split(path):
    """'mfc.2.name' → ('mfc', 2, 'name') / 'params.x' → ('params', None, 'x' 이하 경로)."""
    parts = path.split(".")
    if parts[0] in ("mfc", "heaters"):
        return parts[0], int(parts[1]), parts[2:]
    return parts[0], None, parts[1:]


def _list_item(lst, key, idx, create=False):
    for it in lst:
        if isinstance(it, dict) and int(it.get(key) or 0) == idx:
            return it
    if create:
        it = {key: idx}
        lst.append(it)
        lst.sort(key=lambda x: int(x.get(key) or 0) if isinstance(x, dict) else 0)
        return it
    return None


def get_value(cfg: dict, path: str):
    top, idx, rest = _split(path)
    if idx is not None:
        it = _list_item(cfg.get(top) or [], "no" if top == "mfc" else "ch", idx)
        return None if it is None else it.get(rest[0])
    cur = cfg.get(top)
    for k in rest:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _set_value(raw: dict, path: str, value):
    top, idx, rest = _split(path)
    if idx is not None:
        lst = raw.setdefault(top, [])
        it = _list_item(lst, "no" if top == "mfc" else "ch", idx, create=True)
        it[rest[0]] = value
        return
    cur = raw.setdefault(top, {})
    for k in rest[:-1]:
        cur = cur.setdefault(k, {})
    cur[rest[-1]] = value


def _coerce(field, v):
    """화면 값 → 설정 값. (값, 오류)"""
    if v is None or v == "":
        if field["nullable"]:
            return None, ""
        if field["kind"] == "str":
            return "", ""
        return None, f"{field['label']}: 값이 필요합니다"
    k = field["kind"]
    try:
        if k == "bool":
            if isinstance(v, str):
                return v.lower() in ("1", "true", "on", "yes"), ""
            return bool(v), ""
        if k == "int":
            f = float(v)
            if f != int(f):
                raise ValueError
            return int(f), ""
        if k == "num":
            f = float(v)
            if f != f or f in (float("inf"), float("-inf")):
                raise ValueError
            return (int(f) if f == int(f) and abs(f) < 1e15 else f), ""
        return str(v).strip(), ""
    except (TypeError, ValueError, OverflowError):
        return None, f"{field['label']}: 숫자가 아닙니다 ({v!r})"


# ===================== 대상 파일 =====================
def target_path(cfg: dict) -> str:
    """저장할 config.json. 예시로 실행 중이면 exe 옆에 새로 만든다."""
    if cfg.get("_source") == "file" and cfg.get("_path"):
        return cfg["_path"]
    return paths.DEFAULT_CONFIG_PATH


def save_block(cfg: dict) -> str:
    """★ v0.6.2(C1): 저장을 막는 이유('' = 저장 가능). config.json 이 있는데 읽지 못하면(기동 때든 지금이든) 막는다 —
    읽지 못한 현장 설정을 예시 바탕 + 고친 항목으로 덮어쓰지 않게."""
    p = cfg.get("_unreadable") or target_path(cfg)
    why = ""
    if cfg.get("_unreadable"):
        why = "읽지 못해 예시 설정으로 실행 중"
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                if not isinstance(json.load(f), dict):
                    why = "최상위가 객체가 아님"
        except Exception as e:  # noqa: BLE001
            why = C.read_reason(e)
    if not why:
        return ""
    name = os.path.basename(p)
    return f"{name} 을(를) 읽지 못했습니다({why}) — 저장하지 않습니다. {name} 을 고친 뒤 프로그램을 다시 시작하세요"


def _raw_base(cfg: dict) -> dict:
    """편집을 얹을 원본 — 파일에 있던 그대로(설명 키 포함). 파일이 있는데 못 읽으면 save_block 이 저장을 막는다."""
    for p in (target_path(cfg), paths.EXAMPLE_CONFIG):
        try:
            with open(p, encoding="utf-8-sig") as f:        # ★ v0.6.2(C1): BOM 이 있어도
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            continue
    return {}


def _effective(raw: dict, source_path: str) -> dict:
    cfg = C._deep_merge(C.DEFAULTS, raw)
    cfg["_source"] = "file"
    cfg["_path"] = source_path
    C._fill_devices(cfg)
    return cfg


# ===================== 미리 보기 =====================
def prepare(cfg: dict, edits: dict) -> dict:
    """편집값을 검증하고 바뀌는 항목을 만든다. 파일은 건드리지 않는다.
    반환 {ok, errors, warnings, diff, restart, sim_to_real, raw, new_cfg}"""
    errors, diff = [], []
    blk = save_block(cfg)
    if blk:
        errors.append(blk)
    raw = copy.deepcopy(_raw_base(cfg))
    for path, v in (edits or {}).items():
        f = FIELDS.get(path)
        if f is None:
            errors.append(f"바꿀 수 없는 항목입니다: {path}")
            continue
        val, err = _coerce(f, v)
        if err:
            errors.append(err)
            continue
        _set_value(raw, path, val)

    path_out = target_path(cfg)
    new_cfg = _effective(raw, path_out)
    for lv, msg in C.validate(new_cfg):
        (errors if lv == "err" else []).append(msg)
    warnings = [msg for lv, msg in C.validate(new_cfg) if lv != "err"]

    for path, f in FIELDS.items():
        old, new = get_value(cfg, path), get_value(new_cfg, path)
        if old != new:
            row = {"path": path, "label": f["label"], "old": old, "new": new,
                   "restart": f["restart"]}
            key = path.split(".", 1)[1] if path.startswith("params.") else None
            if key in PRM_KEYS:
                row["prm"] = f"D{PRM_KEYS[key]:05d}"
            diff.append(row)

    # PRM 원시값 — 환산이 바뀌어도 원시값이 바뀐다(예: CVG 환산 → 베이스 압력 원시값)
    old_w = param_words(cfg, Converters(cfg))
    new_w = param_words(new_cfg, Converters(new_cfg))
    prm = []
    for addr in sorted(set(old_w) | set(new_w)):
        o = old_w.get(addr, ("", None))
        n = new_w.get(addr, ("", None))
        if o[1] != n[1]:
            prm.append({"addr": f"D{addr:05d}", "name": n[0] or o[0],
                        "old": o[1], "new": n[1]})
    for row in diff:
        hit = next((p for p in prm if p["addr"] == row.get("prm")), None)
        if hit:
            row["raw_old"], row["raw_new"] = hit["old"], hit["new"]

    sim_to_real = bool((cfg.get("plc") or {}).get("simulate")) and \
        not bool((new_cfg.get("plc") or {}).get("simulate"))
    host_changed = get_value(cfg, "plc.host") != get_value(new_cfg, "plc.host")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "diff": diff,
            "host_changed": host_changed,
            "device_id": f"0x{DEV.DEVICE_ID:04X}",
            "prm": prm, "restart": any(r["restart"] for r in diff),
            "sim_to_real": sim_to_real, "raw": raw, "new_cfg": new_cfg,
            "path_name": os.path.basename(path_out),
            "first_file": not os.path.isfile(path_out)}


def public(res: dict) -> dict:
    """화면에 보낼 부분(원본·새 설정 전체는 빼고)."""
    return {k: v for k, v in res.items() if k not in ("raw", "new_cfg")}


# ===================== 저장 =====================
def backup_dir() -> str:
    return os.path.join(paths.DATA_DIR, "config_backup")


def _backup(path: str) -> str:
    """바꾸기 전 파일을 백업하고 최근 BACKUP_KEEP 개만 남긴다. 백업한 파일 이름."""
    if not os.path.isfile(path):
        return ""
    d = backup_dir()
    os.makedirs(d, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(d, f"config-{stamp}.json")
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(d, f"config-{stamp}-{n}.json")
    shutil.copy2(path, dst)
    files = sorted(glob.glob(os.path.join(d, "config-*.json")), key=os.path.getmtime)
    for old in files[:-BACKUP_KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass
    return os.path.basename(dst)


def write(res: dict) -> str:
    """검증을 통과한 결과를 저장한다. 백업 파일 이름(없으면 '')을 돌려준다."""
    path = res["new_cfg"]["_path"]
    bk = _backup(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    atomic_write_json(path, res["raw"])
    for row in res["diff"]:
        extra = (f" (원시값 {row['raw_old']} → {row['raw_new']})"
                 if "raw_new" in row else "")
        logger.write("warn", f"설정 변경 {row['path']}: {_fmt(row['old'])} → {_fmt(row['new'])}{extra}")
    logger.write("ok", f"설정 저장: {os.path.basename(path)}" + (f" (백업 {bk})" if bk else " (새 파일)"))
    return bk


def _fmt(v):
    return "없음" if v is None else json.dumps(v, ensure_ascii=False)
