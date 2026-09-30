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

    @property
    def active(self) -> bool:
        return self.fp is not None

    # ===================== 시작 · 끝 =====================
    def start(self, recipe_name: str, recipe: dict, table: dict, total_ms: int):
        if self.fp:
            self.close()
        self.error = ""
        try:
            os.makedirs(paths.DATALOG_DIR, exist_ok=True)
            safe = _safe(recipe_name) or "recipe"
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self.name = f"{stamp}_{safe}"
            self.path = os.path.join(paths.DATALOG_DIR, self.name + ".csv")
            # utf-8-sig: 엑셀에서 바로 열린다
            self.fp = open(self.path, "w", encoding="utf-8-sig", newline="")
            self.writer = csv.writer(self.fp)
            self.writer.writerow(self._header())
            self.fp.flush()
            self.started = time.monotonic()
            self.stop_at = 0.0
            self._next = 0.0
            self._rows = 0
            self.end_result = ""
            self.meta_path = os.path.join(paths.DATALOG_DIR, self.name + ".recipe.json")
            self.meta = {"recipe": recipe, "number": (table or {}).get("number"),
                         "checksum": (table or {}).get("checksum"),
                         "estimated_ms": total_ms,
                         "started": time.strftime("%Y-%m-%d %H:%M:%S")}
            storage.atomic_write_json(self.meta_path, self.meta)
            logview.set_writing(self.name)
            logger.write("info", f"데이터 로그 시작: {os.path.basename(self.path)}")
        except Exception as e:  # noqa: BLE001
            # ★ 기록을 못 해도 공정은 돌아야 한다.
            self.error = f"데이터 로그를 시작하지 못했습니다: {e}"
            logger.write("err", self.error)
            self.fp = None
            self.writer = None

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
        if now < self._next:
            return
        self._next = now + max(0.2, float(interval_s or 1))
        try:
            self.writer.writerow(self._row(now))
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
        cols = ["시각", "경과 s", "장비 상태", "시퀀서 상태", "블록", "스텝", "스텝 이름",
                "사이클", "그룹 회차", "스텝 경과 ms", "CVG Torr"]
        if self.state.conv and self.state.conv.cm.installed:
            cols.append("CM Torr")
        for m in self.state.cfg.get("mfc") or []:
            cols += [f"MFC{m['no']} 현재 sccm", f"MFC{m['no']} 설정 sccm"]
        for h in self._heater_channels():
            cols += [f"CH{h['ch']} {h['name']} 현재 ℃", f"CH{h['ch']} {h['name']} 설정 ℃"]
        if DEV.HAS_RF:
            cols += ["RF 순방향 W", "RF 반사 W", "RF 설정 W"]
        if DEV.HAS_PCV:
            cols += ["PCV 개도 %", "PCV 목표 %"]
        if DEV.HAS_O3:
            cols += ["O3 현재", "O3 설정"]
        cols += ["열린 밸브", "밸브 워드", "보조 출력 워드", "알람0", "알람1"]
        return cols

    def _row(self, now):
        st = self.state
        link = st.link
        live = st.live()
        conn = bool(link and link.connected)
        s = link.status if conn else [0] * A.STATUS_COUNT

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


def _safe(name: str) -> str:
    """파일 이름에 쓸 수 있게 다듬는다."""
    bad = set('<>:"|?*/\\') | {chr(i) for i in range(32)}
    out = "".join("_" if ch in bad else ch for ch in (name or ""))
    return out.strip(" .")[:60]
