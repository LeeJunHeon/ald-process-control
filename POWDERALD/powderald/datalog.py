"""
datalog.py — 공정 데이터 로그 (CSV).

공정 시작부터 종료 감지 뒤 여유 시간까지 일정 주기로 한 줄씩 남긴다.
파일 이름: data/datalog/YYYYMMDD_HHMMSS_<레시피 이름>.csv
같은 이름의 .recipe.json 에 그때 레시피 사본·번호·합계·예상 시간을 함께 둔다.

★ UTF-8 BOM 으로 쓴다 — 엑셀이 BOM 없는 UTF-8 CSV 의 한글을 깨뜨린다.
★ 파일 쓰기 실패가 공정·화면을 멈추지 않게 한다. 실패는 로그로만 알리고 기록을 포기한다.
"""

import os
import csv
import glob
import time

from . import addresses as A
from . import device as DEV
from . import logger
from . import logview
from . import paths
from . import storage

TAIL_S = 5.0            # 종료를 본 뒤에도 이만큼 더 남긴다(마무리 거동 확인용)


def cleanup(keep_days: int):
    """오래된 데이터 로그 정리. 기동할 때 한 번 부른다."""
    try:
        keep = max(0, int(keep_days or 0))
    except (TypeError, ValueError):
        keep = 180
    if keep <= 0:
        return
    cutoff = time.time() - keep * 86400
    for pat in ("*.csv", "*.recipe.json"):
        for f in glob.glob(os.path.join(paths.DATALOG_DIR, pat)):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except Exception:  # noqa: BLE001
                pass


class DataLog:
    def __init__(self, state):
        self.state = state
        self.fp = None
        self.writer = None
        self.path = ""
        self.name = ""
        self.started = 0.0
        self.stop_at = 0.0          # 종료를 본 시각(+TAIL_S 까지 더 쓴다)
        self._next = 0.0
        self._rows = 0
        self.error = ""
        self._was_running = False
        self.meta_path = ""
        self.meta = None
        self.end_result = ""
        self._result_fn = None      # 끝을 볼 때 결과가 아직 없으면 닫을 때 다시 묻는다
        self._down_since = 0.0      # PLC 가 끊긴 시각(끊긴 동안도 같은 파일에 쓴다)

    @property
    def active(self) -> bool:
        return self.fp is not None

    # ===================== 시작 · 끝 =====================
    def start(self, recipe_name: str, recipe: dict, table: dict, total_ms: int, adopted: bool = False):
        if self.fp:
            self.close()
        self.error = ""
        self._down_since = 0.0
        if adopted and self._resume(table):
            return
        try:
            os.makedirs(paths.DATALOG_DIR, exist_ok=True)
            safe = _safe(recipe_name) or "recipe"
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self.name = f"{stamp}_{safe}"
            self.path = os.path.join(paths.DATALOG_DIR, self.name + ".csv")
            # utf-8-sig: 엑셀에서 바로 열린다
            self.fp = open(self.path, "w", encoding="utf-8-sig", newline="")
            self.writer = csv.writer(self.fp)
            self.writer.writerow(logger.csv_row(self._header()))
            self.fp.flush()
            self.started = time.monotonic()
            self.stop_at = 0.0
            self._next = 0.0
            self._rows = 0
            self.end_result = ""
            self.meta_path = os.path.join(paths.DATALOG_DIR, self.name + ".recipe.json")
            num, chk = table_id(table)
            self.meta = {"recipe": recipe, "number": num, "checksum": chk,
                         "estimated_ms": total_ms,
                         "started": time.strftime("%Y-%m-%d %H:%M:%S")}
            self.meta.update(run_meta(self.state))        # v0.7.0(A2): 실행 정보 · 장비 · 히터 설정 · PRM
            if adopted:
                self.meta["adopted"] = True                 # 이어받았는데 같은 파일 조건이 안 맞음 — 새 파일
            storage.atomic_write_json(self.meta_path, self.meta)
            logview.set_writing(self.name)
            logger.write("info", f"데이터 로그 시작: {os.path.basename(self.path)}")
        except Exception as e:  # noqa: BLE001
            # ★ 기록을 못 해도 공정은 돌아야 한다.
            self.error = f"데이터 로그를 시작하지 못했습니다: {e}"
            logger.write("err", self.error)
            self.fp = None
            self.writer = None

    def _resume(self, table) -> bool:
        """v0.7.0(P5) — 이어받기 → 같은 파일. 가장 최근 메타가 끝 판정이 없고 번호 · 합계가 되읽은 PLC 표와 같고 CSV
        머리줄이 지금 머리줄과 같으면 그 CSV 를 덧붙이기로 다시 연다 — 표시 줄 하나(State 'PC Restart', 값 칸 비움)를
        쓰고 이어 쓴다. 메타는 처음 것을 두고 ended · result 를 지운 뒤 'resumed' 에 {at, why} 를 더한다. 열었으면 True."""
        num, chk = table_id(table)
        if num is None or chk is None or not (table or {}).get("words"):
            return False
        name, meta = latest_meta()
        if not name or judged(meta) or meta.get("number") != num or meta.get("checksum") != chk:
            return False
        path = os.path.join(paths.DATALOG_DIR, name + ".csv")
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                head = next(csv.reader(f), [])
                rows = sum(1 for _ in f)
        except (OSError, csv.Error, UnicodeDecodeError):
            return False
        if head != logger.csv_row(self._header()):
            logger.write("info", f"이어받기 — 데이터 로그 {name} 의 머리줄이 지금과 달라 새 파일로 씁니다")
            return False
        try:
            self.fp = open(path, "a", encoding="utf-8", newline="")
            self.writer = csv.writer(self.fp)
        except OSError as e:
            logger.write("warn", f"이어받기 — 데이터 로그 {name} 을(를) 다시 열지 못해 새 파일로 씁니다: {e}")
            self.fp = self.writer = None
            return False
        self.name, self.path = name, path
        self.meta_path = os.path.join(paths.DATALOG_DIR, name + ".recipe.json")
        meta = {k: v for k, v in meta.items() if k not in ("ended", "result", "rows", "took_s")}
        meta["resumed"] = list(meta.get("resumed") or []) + [
            {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "why": "프로그램을 다시 켜 이어받음(PLC 공정 계속)"}]
        self.meta = meta
        # 경과 s 는 처음 시작 시각부터 이어 간다(메타의 started)
        try:
            t0 = time.mktime(time.strptime(meta.get("started") or "", "%Y-%m-%d %H:%M:%S"))
            self.started = time.monotonic() - max(0.0, time.time() - t0)
        except (TypeError, ValueError, OverflowError):
            self.started = time.monotonic()
        self.stop_at = 0.0
        self._next = 0.0
        self._rows = rows
        self.end_result = ""
        try:
            n = len(self._header())
            row = [time.strftime("%Y-%m-%d %H:%M:%S"), f"{time.monotonic() - self.started:.1f}", RESTART_STATE]
            self.writer.writerow(logger.csv_row(row + [""] * (n - len(row))))
            self.fp.flush()
            self._rows += 1
            storage.atomic_write_json(self.meta_path, self.meta)
        except Exception as e:  # noqa: BLE001
            logger.write("warn", f"이어받기 — 표시 줄 · 메타 쓰기 실패(계속 기록): {e}")
        logview.set_writing(self.name)
        logger.write("info", f"데이터 로그 이어 쓰기(이어받기): {os.path.basename(self.path)}")
        return True

    def follow(self, running: bool, start_fn, result_fn=None):
        """공정 상태를 따라 파일을 연다·닫는다.

        ★ 시작 가장자리(멈춤 → 공정 중)를 잡아 앞 파일을 바로 닫고 새 파일을 연다.
          앞 공정의 꼬리(TAIL_S)를 쓰는 중에 다음 공정이 시작되면, 새 공정 줄이
          앞 파일에 섞이고 새 파일은 늦게 생기거나(첫 몇 초 빠짐) 아예 안 생긴다."""
        if running and not self._was_running:
            start_fn()                  # start() 가 열린 앞 파일을 먼저 닫는다
        elif running and self.fp:
            self.stop_at = 0.0          # 공정 중이면 종료 표시를 지운다
        elif not running and self.fp:
            if not self.stop_at:
                self._result_fn = result_fn
            self.note_end(result_fn() if (result_fn and not self.stop_at) else None)
        self._was_running = running

    def note_end(self, result=None):
        """종료를 본 시각. 여기서 바로 닫지 않고 조금 더 남긴다."""
        if self.fp and not self.stop_at:
            self.stop_at = time.monotonic()
            if result:
                self.end_result = result

    def close(self):
        if self.fp:
            try:
                self.fp.close()
            except Exception:  # noqa: BLE001
                pass
            logger.write("info", f"데이터 로그 종료: {os.path.basename(self.path)} "
                                 f"({self._rows}줄)")
            self._write_end_meta()
            logview.set_writing(None)
        self.fp = None
        self.writer = None
        self.stop_at = 0.0
        self._result_fn = None

    def _write_end_meta(self):
        """짝 .recipe.json 에 끝난 시각·결과·줄 수·걸린 시간을 덧붙인다(원자적 저장).
        ★ 실패해도 공정·화면에는 영향이 없다 — 보기 화면이 CSV 로 짐작한다."""
        if not self.meta_path or self.meta is None:
            return
        try:
            meta = dict(self.meta)
            if not self.end_result and self._result_fn and self.stop_at:
                # 끝을 볼 때 결과가 아직 없었다(즉시 중단 결과 대기 등) — 지금 다시 묻는다
                try:
                    self.end_result = self._result_fn() or ""
                except Exception:  # noqa: BLE001
                    pass
            meta.update({
                "ended": time.strftime("%Y-%m-%d %H:%M:%S"),
                "result": self.end_result or "기록 중단(프로그램 종료 등)",
                "rows": self._rows,
                "took_s": round(time.monotonic() - self.started, 1),
            })
            storage.atomic_write_json(self.meta_path, meta)
        except Exception as e:  # noqa: BLE001
            logger.write("warn", f"데이터 로그 메타 기록 실패: {e}")
        self.meta = None

    # ===================== 한 줄 =====================
    def tick(self, interval_s: float):
        if not self.fp:
            return
        now = time.monotonic()
        if self.stop_at and now - self.stop_at > TAIL_S:
            self.close()
            return
        # ★ PLC 가 끊겨도 같은 파일에 이어 쓴다(값 칸은 비우고 상태 칸 'PLC 끊김').
        # ★ v0.7.0(P4): 공정 구간(active_run) 동안은 끊김 · 멈춤이 길어도 닫지 않는다 — PLC 는 받은 레시피를 끝까지
        #   돌린다. 30 분을 넘으면 'PLC Lost' · 'PLC Stalled' 줄을 60 s 에 한 줄로 줄이고, 다시 붙으면 같은 파일에 이어
        #   쓴다. 공정 구간이 아닌(끝난 뒤 꼬리) 파일만 30 분에 닫는다
        link = self.state.link
        thin = False
        if link and link.connected and not getattr(link, "plc_hb_stalled", False):
            if self._down_since and now - self._down_since > DOWN_CLOSE_S:
                self._next = 0.0                # 줄인 간격(60 s)을 기다리지 않고 다시 붙은 값을 바로 쓴다
            self._down_since = 0.0
        else:
            self._down_since = self._down_since or now
            if now - self._down_since > DOWN_CLOSE_S:
                runner = self.state.runner
                if not (runner and runner.active_run):
                    self.end_result = "기록 중단(PLC 끊김)" if not (link and link.connected) else "기록 중단(PLC 멈춤)"
                    logger.write("warn", f"데이터 로그를 닫습니다 — PLC 끊김 · 멈춤이 {DOWN_CLOSE_S / 60:.0f} 분을 "
                                         "넘었습니다")
                    self.close()
                    return
                thin = True
        if now < self._next:
            return
        self._next = now + (DOWN_THIN_S if thin else max(0.2, float(interval_s or 1)))
        try:
            self.writer.writerow(logger.csv_row(self._row(now)))
            self.fp.flush()
            self._rows += 1
        except Exception as e:  # noqa: BLE001
            self.error = f"데이터 로그 기록 실패: {e}"
            logger.write("err", self.error)
            self.close()

    # ===================== 열 =====================
    def _heater_channels(self):
        return [h for h in (self.state.cfg.get("heaters") or []) if h.get("enabled")]

    def _header(self):
        # ★ v0.5.0: 열 이름은 영문 용어(화면 · 트렌드 내보내기와 같게). 옛 파일의 한글 열 이름은 logview 가
        #   읽을 때 이 이름으로 바꿔 읽는다(OLD_HEAD)
        cols = ["Time", "Elapsed s", "State", "Sequencer", "Block", "Step", "Step Name",
                "Cycle", "Group Pass", "Step Elapsed ms", "CVG Torr"]
        if self.state.conv and self.state.conv.cm.installed:
            cols.append("CM Torr")
        for m in self.state.cfg.get("mfc") or []:
            cols += [f"MFC{m['no']} Actual sccm", f"MFC{m['no']} Setpoint sccm"]
        for h in self._heater_channels():
            cols += [f"CH{h['ch']} {h['name']} Actual ℃", f"CH{h['ch']} {h['name']} Setpoint ℃"]
        if DEV.HAS_RF:
            cols += ["RF Forward W", "RF Reflected W", "RF Setpoint W"]
        if DEV.HAS_PCV:
            cols += ["PCV Position %", "PCV Target %"]
        if DEV.HAS_O3:
            cols += ["O3 Actual", "O3 Setpoint"]
        cols += ["Open Valves", "Valve Word", "Aux Word", "Alarm0", "Alarm1"]
        return cols

    def _row(self, now):
        st = self.state
        link = st.link
        conn = bool(link and link.connected)
        stalled = conn and getattr(link, "plc_hb_stalled", False)
        if not conn or stalled:
            # 값을 지어내지 않는다 — 밸브 워드 0x0000 은 '닫힘'으로 읽히므로 쓰지 않는다.
            # ★ 하트비트 멈춤(STOP 등)이면 읽은 값이 굳은 옛 값이다 — 끊김과 같게 비운다
            n = len(self._header())
            row = [time.strftime("%Y-%m-%d %H:%M:%S"), f"{now - self.started:.1f}",
                   "PLC Stalled" if stalled else "PLC Lost"]
            return row + [""] * (n - len(row))
        live = st.live()
        s = link.status

        def num(v, d=1):
            return "" if v is None else f"{float(v):.{d}f}"

        seq = live.get("seq") or {}
        prog = st.runner.progress() if st.runner else {}
        step_name = ""
        steps = prog.get("steps") or []
        idx = (prog.get("step_in_block") or 0) - 1
        if 0 <= idx < len(steps):
            step_name = steps[idx]["name"]

        row = [
            time.strftime("%Y-%m-%d %H:%M:%S"),
            f"{now - self.started:.1f}",
            (live.get("state") or {}).get("name", ""),
            seq.get("name", ""),
            seq.get("block", ""), seq.get("step", ""), step_name,
            seq.get("block_pass", ""), seq.get("group_pass", ""), seq.get("step_ms", ""),
            num((live.get("pressure") or {}).get("cvg"), 5),
        ]
        if st.conv and st.conv.cm.installed:
            row.append(num((live.get("pressure") or {}).get("cm"), 5))
        mfc = {m["no"]: m for m in (live.get("mfc") or [])}
        for m in st.cfg.get("mfc") or []:
            v = mfc.get(m["no"], {})
            row += [num(v.get("pv")), num(v.get("sv"))]
        heat = {h["ch"]: h for h in (live.get("heaters") or [])}
        for h in self._heater_channels():
            v = heat.get(h["ch"], {})
            row += [num(v.get("pv")), num(v.get("sv"))]
        ex = live.get("extra") or {}
        if DEV.HAS_RF:
            row += [num(ex.get("rf_fwd")), num(ex.get("rf_ref")), num(ex.get("rf_sv"))]
        if DEV.HAS_PCV:
            row += [num(ex.get("pcv_pv"), 0), num(ex.get("pcv_sv"), 0)]
        if DEV.HAS_O3:
            row += [num(ex.get("o3_pv")), num(ex.get("o3_sv"))]

        vw = s[A.D_VALVE_OUT] if conn else 0
        aw = s[A.D_AUX_OUT] if conn else 0
        open_names = [v["tag"] for v in DEV.VALVES if (vw >> v["bit"]) & 1]
        row += [" ".join(open_names),
                f"0x{vw:04X}", f"0x{aw:04X}",
                f"0x{s[A.D_ALARM0]:04X}" if conn else "",
                f"0x{s[A.D_ALARM1]:04X}" if conn else ""]
        return row


DOWN_CLOSE_S = 1800.0           # PLC 끊김이 이만큼 이어지면 — 공정 구간이 아니면 닫고, 공정 구간이면 줄을 줄인다
DOWN_THIN_S = 60.0              # 그때 'PLC Lost' · 'PLC Stalled' 줄 간격(v0.7.0 P4)
RESTART_STATE = "PC Restart"    # 이어받아 같은 파일을 다시 열 때 쓰는 표시 줄의 State 칸(v0.7.0 P5)


def table_id(table):
    """(레시피 번호, 합계) — 되읽은 PLC 표(words)가 있으면 그 표에서, 없으면 표 머리 값."""
    t = table or {}
    if t.get("words"):
        try:
            from . import recipe as R
            info = R.from_plc_words(t["words"])
            return info["number"], info["checksum"]
        except Exception:  # noqa: BLE001
            pass
    return t.get("number"), t.get("checksum")


def judged(meta) -> bool:
    """메타에 끝 판정이 있나 — 'ended' 가 있고 result 가 '기록 중단(' 으로 시작하지 않으면."""
    if not isinstance(meta, dict) or not meta.get("ended"):
        return False
    return not str(meta.get("result") or "").startswith("기록 중단(")


def latest_meta():
    """가장 최근(이름 순 마지막) 데이터 로그의 (이름, 메타). 없으면 (None, None)."""
    try:
        names = sorted(os.path.basename(p)[:-len(".recipe.json")]
                       for p in glob.glob(os.path.join(paths.DATALOG_DIR, "*.recipe.json")))
    except OSError:
        return None, None
    for name in reversed(names):
        meta = storage.read_json(os.path.join(paths.DATALOG_DIR, name + ".recipe.json"))
        if isinstance(meta, dict):
            return name, meta
        return None, None
    return None, None


def run_meta(state) -> dict:
    """v0.7.0(A2) — 메타에 더하는 실행 정보 · 프로그램 버전 · 장비 · 히터 설정(사용 채널 ℃) · PRM(되읽은 값)."""
    from . import version
    from .convert import heater_temp
    runner, link = state.runner, state.link
    info = dict(getattr(runner, "run_info", {}) or {}) if runner else {}
    out = {"operator": info.get("operator", ""), "sample": info.get("sample", ""), "memo": info.get("memo", ""),
           "app_version": version.APP_VERSION,
           "device": {"key": DEV.KEY, "name": DEV.NAME,
                      "id": getattr(link, "device_id", None) if (link and link.connected) else None}}
    hs = {}
    for h in state.cfg.get("heaters") or []:
        if not h.get("enabled"):
            continue
        raw = link.cmd_reg(A.D_HEATER_SV + h["ch"] - 1) if (link and hasattr(link, "cmd_reg")) else None
        hs[f"CH{h['ch']} {h.get('name', '')}"] = heater_temp(raw) if raw is not None else None
    out["heater_sv"] = hs
    prm = []
    try:
        for r in state.prm_table() if (link and link.connected) else []:
            prm.append({"name": r.get("name") or r.get("addr"), "value": r.get("eng"), "unit": r.get("unit", "")})
    except Exception:  # noqa: BLE001
        pass
    out["prm"] = prm
    return out


def _safe(name: str) -> str:
    """파일 이름에 쓸 수 있게 다듬는다."""
    bad = set('<>:"|?*/\\') | {chr(i) for i in range(32)}
    out = "".join("_" if ch in bad else ch for ch in (name or ""))
    return out.strip(" .")[:60]
