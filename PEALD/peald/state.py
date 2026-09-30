"""
state.py — PLC 레지스터를 화면이 쓸 모양으로 푼다 + 서버가 주인인 상태.

화면은 비트 번호나 D 주소를 모른다. 여기서 전부 이름과 값으로 바꿔 내보낸다 —
화면 코드가 주소표를 알게 되면 래더를 고칠 때 고쳐야 할 곳이 두 군데가 된다.

★ PLC 연결이 끊기면 값을 지어내지 않는다. 전부 None(화면에서 '—')으로 두고
  "PLC 끊김"을 크게 보여 준다. 마지막 값을 계속 보여 주면 운전자가 현재 상태로 오해한다.
"""

import time

from . import addresses as A
from . import device as DEV
from . import logger
from . import version
from . import recipe as R
from . import storage
from .convert import heater_temp

# PC 자체 알림(PLC 알람이 아니라 프로그램이 판단한 것). 화면에서 구분해 보여 준다.
PC_NOTICE_KEYS = ("plc_disconnected", "plc_hb_stall", "prm_mismatch",
                  "unconfirmed", "example_config")


class AlarmTracker:
    """알람 워드 비트 → 알람 목록. 발생·해제 시각을 PC 가 처음 본 시각으로 기록한다."""

    def __init__(self):
        self.active = {}        # code -> {code, name, crit, since}
        self.history = []       # 최근이 앞

    def update(self, w0: int, w1: int):
        seen = set()
        for word, defs, tag in ((w0, DEV.ALARMS0, "A0"), (w1, DEV.ALARMS1, "A1")):
            for d in defs:
                if not (word >> d["bit"]) & 1:
                    continue
                code = f"{tag}-{d['bit']:02d}"
                seen.add(code)
                if code not in self.active:
                    rec = {"code": code, "name": d["name"], "crit": d["crit"],
                           "since": time.strftime("%H:%M:%S"),
                           "date": time.strftime("%m-%d")}
                    self.active[code] = rec
                    self.history.insert(0, {**rec, "cleared": ""})
                    del self.history[200:]
                    sev = "중대" if d["crit"] else "경고"
                    logger.write("err" if d["crit"] else "warn", f"알람 발생 [{code}] {d['name']} ({sev})")
                    logger.alarm_event("발생", code, d["name"], sev)
        for code in list(self.active):
            if code not in seen:
                rec = self.active.pop(code)
                for h in self.history:
                    if h["code"] == code and not h["cleared"]:
                        h["cleared"] = time.strftime("%H:%M:%S")
                        break
                logger.write("ok", f"알람 해제 [{code}] {rec['name']}")
                logger.alarm_event("해제", code, rec["name"], "중대" if rec["crit"] else "경고")

    def clear_all(self):
        """PLC 연결이 끊기면 알람 목록을 비운다 — 옛 값을 현재 알람으로 보여 주면 안 된다."""
        for code in list(self.active):
            self.active.pop(code)

    def list(self):
        return sorted(self.active.values(), key=lambda a: (not a["crit"], a["code"]))

    def has_critical(self) -> bool:
        return any(a["crit"] for a in self.active.values())


class State:
    def __init__(self):
        self.cfg = {}
        self.conv = None
        self.link = None
        self.sim = None
        self.config_source = "default"
        self.startup_notices = []
        self.logs = []
        self.alarms = AlarmTracker()
        self._last_new_alarm = 0
        self.alarm_popup = False        # D00007 이 0→1 이 되면 화면에 알람 창을 띄운다
        self.runner = None              # ProcessRunner (공정 시작 흐름·진행)
        self.datalog = None             # DataLog
        self.recipe_check = {}          # 고른 레시피의 검증 결과
        self.plc_recipe = {}            # 지금 PLC 에 올라가 있는 레시피 요약
        self.manual_unlock_until = 0.0  # 수동 밸브 잠금 해제 만료 시각
        self.o3_off_at = 0.0            # O3 바이패스 라인을 닫을 시각 (서버 타이머)
        self.o3_off_task = None

    # ===================== 설정 =====================
    def install_config(self, cfg: dict, problems: list, source: str):
        """설정을 적용한다(기동·설정 저장 공통). 환산을 다시 만들고 링크에도 넘긴다.
        ★ PLC 연결 값(주소·포트·주기·시뮬레이터)은 다시 시작해야 반영된다."""
        from .convert import Converters
        self.cfg = cfg
        self.config_source = source
        self.conv = Converters(cfg)
        logger.configure(cfg.get("log") or {})
        keep = [n for n in self.startup_notices if n.get("kind") not in ("check", "unconfirmed")]
        for lv, msg in problems:
            keep.append({"level": lv, "msg": msg, "kind": "check"})
        for name in self.conv.unconfirmed():
            keep.append({"level": "warn", "msg": f"환산 미확정: {name}", "kind": "unconfirmed"})
        self.startup_notices = keep
        if self.link:
            self.link.cfg = cfg
            self.link.conv = self.conv

    # ===================== 로그 =====================
    def add_log(self, level: str, msg: str):
        self.logs.append({"ts": time.strftime("%H:%M:%S"), "level": level, "msg": msg})
        del self.logs[:-500]

    # ===================== 주기 갱신 =====================
    def refresh(self):
        """PLC 링크가 읽어 둔 값으로 알람 추적을 갱신한다(샘플링 루프가 부른다)."""
        if not (self.link and self.link.connected):
            self.alarms.clear_all()
            self._last_new_alarm = 0
            return
        s = self.link.status
        self.alarms.update(s[A.D_ALARM0], s[A.D_ALARM1])
        new = s[A.D_ALARM_NEW]
        if new and not self._last_new_alarm:
            self.alarm_popup = True     # 0 → 1 인 순간에만 창을 띄운다
        self._last_new_alarm = new

    # ===================== 화면용 스냅샷 =====================
    def device_info(self) -> dict:
        return {
            "key": DEV.KEY, "name": DEV.NAME, "title": DEV.TITLE,
            "theme": DEV.THEME, "accent": DEV.ACCENT,
            "app_version": version.APP_VERSION,
            "plc_addr": self.link.addr_text if self.link else "",
            "simulate": bool(self.link and self.link.simulate),
            "config_source": self.config_source,
            "has_rf": DEV.HAS_RF, "has_o3": DEV.HAS_O3, "has_pcv": DEV.HAS_PCV,
        }

    def structure(self) -> dict:
        """배관도·표가 쓰는 장비 구조. 접속할 때 한 번만 보낸다."""
        return {
            "valves": DEV.VALVES,
            "aux": DEV.AUX,
            "inputs0": DEV.INPUTS0,
            "inputs1": DEV.INPUTS1,
            "interlocks": DEV.INTERLOCKS,
            "mfc": self.cfg.get("mfc") or [],
            "heaters": self.cfg.get("heaters") or [],
            "manual_valve_mask": DEV.MANUAL_VALVE_MASK,
            "state_names": A.STATE_NAMES,
            "seq_names": A.SEQ_NAMES,
        }

    def live(self) -> dict:
        """5 Hz 로 나가는 값. PLC 가 끊기면 전부 None 이다."""
        link = self.link
        conn = bool(link and link.connected)
        out = {
            "type": "live",
            "ts": time.time(),
            "clock": time.strftime("%H:%M:%S"),
            "date": time.strftime("%Y-%m-%d"),
            "plc": {
                "connected": conn,
                "hb_ok": bool(link and link.plc_hb_ok),
                "rtt_ms": link.rtt_ms if conn else None,
                "addr": link.addr_text if link else "",
                "prm_mismatch": list(link.prm_mismatch) if link else [],
                "prm": self.prm_table() if conn else [],
                # 장비 ID — wrong 이면 화면 머리에 빨간 띠 + 모든 조작 잠금
                "id_state": getattr(link, "id_state", "") if conn else "",
                "device_id": getattr(link, "device_id", None) if conn else None,
                "expected_id": DEV.DEVICE_ID,
                "require_device_id": bool(getattr(link, "require_id", False)) if link else False,
                "hb_gap_ms": getattr(link, "hb_gap_ms", 0) if conn else None,
                "hb_gap_max_ms": getattr(link, "hb_gap_max_ms", 0) if conn else None,
                "config_error": getattr(link, "config_error", "") if link else "",
            },
            "loop": _loop_lag(),
            "alarms": self.alarms.list(),
            "alarm_new": bool(conn and link.status[A.D_ALARM_NEW]),
            "alarm_popup": self.alarm_popup,
            "process": self.runner.progress() if self.runner else {},
            "manual": self.manual_state(),
            "datalog": {
                "active": bool(self.datalog and self.datalog.active),
                "file": (self.datalog.name if (self.datalog and self.datalog.active) else ""),
                "error": (self.datalog.error if self.datalog else ""),
            },
        }
        if not conn:
            out.update({"state": None, "seq": None, "valves": None, "aux": None,
                        "inputs0": None, "inputs1": None, "interlock": None,
                        "pressure": None, "heaters": None, "mfc": None, "extra": None})
            return out

        s = link.status
        d = link.display
        c = self.conv
        out["state"] = {
            "code": s[A.D_STATE],
            "name": A.STATE_NAMES.get(s[A.D_STATE], f"알 수 없음({s[A.D_STATE]})"),
        }
        out["seq"] = {
            "code": s[A.D_SEQ_STATE],
            "name": A.SEQ_NAMES.get(s[A.D_SEQ_STATE], "—"),
            "block": s[A.D_SEQ_BLOCK],
            "step": s[A.D_SEQ_STEP],
            "group_pass": s[A.D_SEQ_GROUP_PASS],
            "block_pass": A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1]),
            "step_ms": A.dword(s[A.D_SEQ_STEP_MS], s[A.D_SEQ_STEP_MS + 1]),
            "recipe_ok": bool(s[A.D_RECIPE_OK]),
            "recipe_sum": s[A.D_RECIPE_SUM_PLC],
        }
        out["valves"] = s[A.D_VALVE_OUT]
        out["aux"] = s[A.D_AUX_OUT]
        out["inputs0"] = s[A.D_INPUT0]
        out["inputs1"] = s[A.D_INPUT1]
        out["interlock"] = s[A.D_INTERLOCK]
        out["scan_max_ms"] = s[A.D_SCAN_MAX]

        out["pressure"] = {
            "cvg_raw": s[A.D_CVG_RAW],
            "cvg": c.cvg.to_torr(s[A.D_CVG_RAW]),
            "cvg_unit": c.cvg.unit,
            "cm_installed": c.cm.installed,
            "cm": c.cm.to_torr(s[A.D_CM_RAW]) if c.cm.installed else None,
        }

        tc = s[A.D_TC_COMM]
        heaters = []
        # ★ 히터 목표·전원은 명령 영역을 1 s 마다 되읽은 값이다(명령 13 성공 직후에는 쓴 값).
        power_req = link.cmd_reg(A.D_HEATER_POWER)
        for h in self.cfg.get("heaters") or []:
            ch = h["ch"]
            i = ch - 1
            # ★ 온도조절기 통신이 끊긴 국번의 채널은 현재값을 '—' 로 둔다.
            #   마지막으로 읽은 값을 계속 보여 주면 식고 있는 히터를 정상으로 오해한다.
            station = int(h.get("station") or (i // 4 + 1))
            station_ok = bool((tc >> (station - 1)) & 1)
            sv_raw = link.cmd_reg(A.D_HEATER_SV + i)
            heaters.append({
                "ch": ch, "name": h["name"], "enabled": h["enabled"],
                "max_c": h.get("max_c"), "default_sv": h.get("default_sv"),
                "station": station,
                "pv": heater_temp(s[A.D_HEATER_PV + i]) if station_ok else None,
                "out_pct": s[A.D_HEATER_OUT + i] if station_ok else None,
                "sv": heater_temp(sv_raw) if sv_raw is not None else None,
                "power": None if power_req is None else bool((power_req >> i) & 1),
                "alarm": bool((s[A.D_HEATER_ALARM] >> i) & 1),
                "comm_ok": station_ok,
                # 전원 켜기를 막는 이유(화면 히터 표·수동 창이 그대로 보인다). 끄기는 언제나 된다.
                "power_block": "" if station_ok else
                "온도조절기 통신이 없어 PLC 과온 감시가 동작하지 않습니다 — 전원을 켤 수 없습니다",
            })
        out["heaters"] = heaters
        out["tc_comm"] = tc

        mfc = []
        for m in self.cfg.get("mfc") or []:
            no = m["no"]
            sc = c.mfc.get(no)
            raw_pv = s[A.D_MFC_PV + no - 1]
            sv_raw = self._display(d, f"mfc{no}")
            mfc.append({
                "no": no, "name": m["name"], "gas": m.get("gas", ""),
                "full_scale": m.get("full_scale_sccm"),
                "pv": sc.to_eng(raw_pv) if sc else None,
                "sv": sc.to_eng(sv_raw) if (sc and sv_raw is not None) else None,
                "confirmed": bool(m.get("confirmed")),
            })
        out["mfc"] = mfc

        extra = {}
        if DEV.HAS_PCV:
            extra["pcv_pv"] = c.pcv.to_eng(s[A.D_PCV_RAW])
            extra["pcv_sv"] = c.pcv.to_eng(self._display(d, "pcv"))
        if DEV.HAS_RF:
            extra["rf_fwd"] = c.rf.to_eng(s[A.D_RF_FWD_RAW])
            extra["rf_ref"] = c.rf.to_eng(s[A.D_RF_REF_RAW])
            extra["rf_sv"] = c.rf.to_eng(self._display(d, "rf"))
            extra["rf_on"] = bool((s[A.D_AUX_OUT] >> A.AUX_RF) & 1)
        if DEV.HAS_O3:
            extra["o3_pv"] = c.o3.to_eng(s[A.D_O3_RAW])
            extra["o3_sv"] = c.o3.to_eng(self._display(d, "o3"))
            extra["o3_unit"] = c.o3.unit
            extra["o3_on"] = bool((s[A.D_AUX_OUT] >> A.AUX_O3_GEN) & 1)
        out["extra"] = extra
        return out

    def _display(self, d, key):
        for item in DEV.DISPLAY_SETPOINTS:
            if item["key"] == key:
                off = item["offset"]
                return d[off] if off < len(d) else None
        return None

    def manual_state(self) -> dict:
        """수동 조작 화면이 쓰는 값. 요청(PLC 반영 영역 D04012·D04050)과
        출력(D00010·D00014)을 나란히 보여 준다 — 요청했는데 안 나간 것(허가 대기)을
        운전자가 알아야 한다."""
        import time as _t
        link = self.link
        conn = bool(link and link.connected)
        req = link.applied_valve if conn else 0
        out = link.status[A.D_VALVE_OUT] if conn else 0
        aux_req = link.applied_aux if conn else 0
        aux_out = (link.status[A.D_AUX_OUT] & DEV.AUX_CMD_MASK) if conn else 0
        pending = [v["tag"] for v in DEV.VALVES
                   if (req >> v["bit"]) & 1 and not (out >> v["bit"]) & 1]
        aux_pending = [a["tag"] for a in DEV.AUX
                       if (aux_req >> a["bit"]) & 1 and not (aux_out >> a["bit"]) & 1]
        o3_left = max(0, int(round(self.o3_off_at - _t.monotonic()))) if self.o3_off_at else 0
        return {
            "unlocked": _t.monotonic() < self.manual_unlock_until,
            "unlock_left_s": max(0, int(self.manual_unlock_until - _t.monotonic())),
            "valve_request": req,
            "valve_out": out,
            "aux_request": aux_req,
            "aux_out": aux_out,
            "pending": pending,
            "aux_pending": aux_pending,
            "o3_off_left_s": o3_left,
        }

    def prm_table(self) -> list:
        """설정 탭 PRM 표 — PC 가 실제로 쓴 원시값과 되읽은 값(공학 단위 병기)."""
        link = self.link
        if not link:
            return []
        from .convert import heater_temp as _ht
        c = self.conv
        m1 = c.mfc.get(1)
        o3_unit = c.o3.unit if c else ""

        def eng(addr, raw):
            if raw is None:
                return None, ""
            if addr in (A.D_PRM_BASE_PRESS, A.D_PRM_RF_MAX_PRESS):
                return c.cvg.to_torr(raw), "Torr"
            if addr == A.D_PRM_MFC_TOL:
                if raw == 0:
                    return 0, "감시 안 함"
                return (m1.to_eng(raw) if m1 else None), "sccm"
            if addr in (A.D_PRM_RF_MAX, A.D_PRM_RF_REF_MAX):
                return c.rf.to_eng(raw), "W"
            if addr == A.D_PRM_O3_MAX:
                return c.o3.to_eng(raw), o3_unit
            if A.D_PRM_HEATER_MAX <= addr < A.D_PRM_HEATER_MAX + 12:
                return (_ht(raw), "℃") if raw else (0, "감시 안 함")
            if addr in (A.D_PRM_PC_WDT_MS, A.D_PRM_VALVE_MIN_MS, A.D_PRM_RF_REF_MS):
                return raw, "ms"
            return raw, "s"

        from .plclink import prm_setting
        names = {addr: name for addr, (name, _v) in link._param_words().items()}
        rows = []
        for addr in sorted(link.prm_written):
            w = link.prm_written[addr]
            r = link.prm_readback.get(addr)
            ev, unit = eng(addr, r if r is not None else w)
            rows.append({"addr": f"D{addr:05d}", "name": names.get(addr, ""),
                         "setting": prm_setting(self.cfg, addr),
                         "written": w, "readback": r, "eng": ev, "unit": unit,
                         "match": r == w})
        return rows

    def snapshot(self, access_local: bool = True) -> dict:
        """접속할 때와 구조가 바뀔 때 보내는 전체 스냅샷."""
        return {
            "type": "state",
            "device": self.device_info(),
            "structure": self.structure(),
            "config": self.public_config(),
            "unconfirmed": self.conv.unconfirmed() if self.conv else [],
            "notices": list(self.startup_notices),
            "alarm_history": list(self.alarms.history),
            "logs": list(self.logs),
            "sim_faults": self.sim_faults(),
            "recipes": storage.list_recipes(),
            "recipe_limits": {
                "step_max": R.STEP_MAX, "block_max": R.BLOCK_MAX, "group_max": R.GROUP_MAX,
                "step_ms_min": R.STEP_MS_MIN, "step_ms_max": R.STEP_MS_MAX,
                "block_repeat_max": R.BLOCK_REPEAT_MAX, "group_repeat_max": R.GROUP_REPEAT_MAX,
                "recipe_valves": DEV.RECIPE_VALVES, "assist_pair": DEV.ASSIST_PAIR,
                "mfc_count": DEV.MFC_COUNT, "format": DEV.RECIPE_FORMAT,
            },
            "plc_recipe": self.plc_recipe,
            "config_fields": _config_fields(),
            "trend_cols": _trend_cols(),
            "access": {"local": bool(access_local)},
            "live": self.live(),
        }

    def public_config(self) -> dict:
        """설정 탭이 읽기 전용으로 보여 주는 값. 비밀 정보는 담지 않는다."""
        cfg = self.cfg
        return {
            "source": self.config_source,
            "path_name": "config.json" if self.config_source == "file" else "config.example.json",
            "server": cfg.get("server") or {},
            "window": cfg.get("window") or {},
            "plc": {k: v for k, v in (cfg.get("plc") or {}).items()},
            "analog": cfg.get("analog") or {},
            "pressure": cfg.get("pressure") or {},
            "params": cfg.get("params") or {},
            "rf": cfg.get("rf") or {},
            "pcv": cfg.get("pcv") or {},
            "o3": cfg.get("o3") or {},
            "log": cfg.get("log") or {},
            "process": cfg.get("process") or {},
            "mfc": cfg.get("mfc") or [],
            "heaters": cfg.get("heaters") or [],
            "access": cfg.get("access") or {},
        }

    def sim_faults(self):
        """시뮬레이터 조작판 상태. 시뮬레이터가 아니면 None."""
        if not self.sim:
            return None
        from .simulator import visible_faults
        return [{"key": f["key"], "name": f["name"], "on": bool(self.sim.faults.get(f["key"]))}
                for f in visible_faults()]


def _config_fields():
    from .settings import _F
    return _F


def _trend_cols():
    from .trendlog import columns_meta
    return columns_meta()


def _loop_lag():
    from .loops import lag_status
    return lag_status()


state = State()
