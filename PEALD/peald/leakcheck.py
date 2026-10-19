"""
leakcheck.py — 누설률(압력 상승률) 점검 (v0.7.0 A3).

래더는 바꾸지 않고 지금 명령만 쓴다 — 명령 11(모두 닫기)은 IV-E 만 닫고 펌프는 그대로 둔다.
    명령 11 → IV-E 닫힘 입력(10 s 안) → leakcheck.settle_s 기다림 → leakcheck.measure_s 동안 1 s 마다 압력
    (CM 이 설치 · 확인 · 범위 안이면 CM, 아니면 CVG) → 최소제곱 기울기 mTorr/min → 명령 8(펌핑 시작)로 IV-E 다시
    열기 → 열림 입력 확인.
판정: leakcheck.limit_mtorr_min 이하 PASS, 넘으면 FAIL. 압력이 leakcheck.max_torr 를 넘으면 그 자리에서 멈추고 FAIL(큰 누설).
중간 멈춤: [Cancel] · PLC 끊김/멈춤 · ID 막힘 · 중대 알람 · 공정 상태로 바뀜 → CANCEL / ABORT 와 이유. 멈출 때 링크가
있으면 명령 8 로 되돌리고, 못 하면 알림 '[Pumping Start] 로 IV-E 를 다시 여세요'.
기록: data/leakcheck/leakcheck.csv(BOM UTF-8) · 이벤트 로그 한 줄. 화면(설정 탭 Maintenance)에 진행과 최근 10 건.

★ 점검 중 프로그램을 끄면 IV-E 는 닫힌 채(펌프 운전) 남는다 — 다시 켜서 [Pumping Start] 로 연다(README).
"""

import os
import csv
import time
import asyncio

from . import addresses as A
from . import device as DEV
from . import logger
from . import paths

HEAD = ["Time", "Gauge", "Start Torr", "End Torr", "Measured s", "mTorr/min", "Limit mTorr/min", "Result", "Reason"]
CLOSE_WAIT_S = 10.0
OPEN_WAIT_S = 15.0
RECENT_N = 10
RUNNING_STATES = (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)
REOPEN_TEXT = "[Pumping Start] 로 IV-E 를 다시 여세요"
RELINK_WAIT_S = 10.0            # 되돌리기 전에 짧은 링크 끊김이 다시 붙기를 기다리는 시간(v0.7.1 L5)
MEASURE_MIN_S = 10.0            # leakcheck.measure_s 최소(설정 검사와 같게 — v0.7.1 W4)


def csv_path() -> str:
    return os.path.join(paths.DATA_DIR, "leakcheck", "leakcheck.csv")


def params(cfg) -> dict:
    lc = (cfg or {}).get("leakcheck") or {}

    def f(k, d):
        try:
            v = float(lc.get(k, d))
            return v if v == v else d
        except (TypeError, ValueError):
            return d
    return {"settle_s": max(0.0, f("settle_s", 10.0)), "measure_s": max(MEASURE_MIN_S, f("measure_s", 120.0)),
            "limit_mtorr_min": f("limit_mtorr_min", 10.0), "max_torr": f("max_torr", 1.0)}


def slope_mtorr_min(pts) -> float:
    """[(t s, p Torr)] 최소제곱 기울기 → mTorr/min. 점이 2 개 미만이면 0."""
    n = len(pts)
    if n < 2:
        return 0.0
    mx = sum(t for t, _ in pts) / n
    my = sum(p for _, p in pts) / n
    sxx = sum((t - mx) ** 2 for t, _ in pts)
    if sxx <= 0:
        return 0.0
    sxy = sum((t - mx) * (p - my) for t, p in pts)
    return sxy / sxx * 1000.0 * 60.0


def pick_gauge(state, s) -> str:
    """측정에 쓸 게이지 — CM 이 설치 · 확인 · 범위 안(원시값 0 초과 · 최대 미만)이면 'CM', 아니면 'CVG'.
    ★ v0.7.1(L3): 측정 시작 때 한 번 정하고 끝까지 그것으로 잰다(한 회귀에 CM · CVG 가 섞이지 않게)."""
    c = state.conv
    raw = s[A.D_CM_RAW]
    if c.cm.installed and c.cm.confirmed and 0 < raw < c.raw_max and c.cm.to_torr(raw) is not None:
        return "CM"
    return "CVG"


def read_gauge(state, s, name: str):
    """(압력 Torr, 범위) — 범위 'ok' · 'high'(원시값 최대 이상 — 위로 넘음) · 'low'(0 이하 · 값 없음)."""
    c = state.conv
    sc, raw = (c.cm, s[A.D_CM_RAW]) if name == "CM" else (c.cvg, s[A.D_CVG_RAW])
    if raw >= c.raw_max:
        return None, "high"
    v = sc.to_torr(raw) if raw > 0 else None
    if v is None:
        return None, "low"
    return float(v), "ok"


def gauge(state, s):
    """(이름, 압력 Torr) — 지금 고를 게이지와 그 값(화면 · 시험용)."""
    g = pick_gauge(state, s)
    return g, read_gauge(state, s, g)[0]


class LeakCheck:
    def __init__(self):
        self.task = None
        self.phase = ""                 # closing · settle · measure · restore
        self.started_mono = 0.0
        self.progress = {}
        self._cancel_why = ""
        self._cancel_restore = True
        self.recent = None              # 최근 기록(새 것이 앞) — 처음 볼 때 CSV 끝에서 읽는다
        self.last = None

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    # ===================== 시작 조건 =====================
    def refuse_reasons(self, state) -> list:
        """시작 조건 — 하나라도 아니면 이유 목록(비어 있으면 시작해도 된다)."""
        link = state.link
        why = []
        if self.running:
            return ["누설률 점검이 이미 진행 중입니다"]
        if not (link and link.connected):
            return ["PLC 에 연결되어 있지 않습니다"]
        blocked = link.id_block_text() if hasattr(link, "id_block_text") else ""
        if blocked:
            return [blocked]
        if getattr(link, "plc_hb_stalled", False):
            return ["PLC 하트비트 멈춤"]
        s = link.status
        if s[A.D_STATE] in RUNNING_STATES:
            why.append("공정 중입니다")
        if state.runner and state.runner.busy:
            why.append("공정 시작 절차가 진행 중입니다")
        if state.alarms.has_critical():
            why.append("중대 알람: " + " · ".join(a["name"] for a in state.alarms.list() if a["crit"]))
        ilk, i0 = s[A.D_INTERLOCK], s[A.D_INPUT0]
        if not A.bit(ilk, A.ILK_PUMP):
            why.append("인터락 Pump 아님(펌프 운전 · 알람 없음)")
        if not A.bit(i0, A.IN0_IVE_OPEN):
            why.append("IV-E 열림 입력 꺼짐 — [Pumping Start] 로 배기 중이어야 합니다")
        if not A.bit(ilk, A.ILK_VACUUM):
            why.append("인터락 Vacuum OK 아님(베이스 압력 도달 전)")
        rmask = 0
        for v in DEV.VALVES:
            if v["tag"] in DEV.RECIPE_VALVES:
                rmask |= 1 << v["bit"]
        if s[A.D_VALVE_OUT] & rmask:
            why.append("레시피 밸브 출력이 켜져 있습니다 — 모두 닫은 뒤에")
        flow = []
        for m in state.cfg.get("mfc") or []:
            sc = state.conv.mfc.get(m["no"])
            if not (sc and sc.full):
                continue
            pv = sc.to_eng(s[A.D_MFC_PV + m["no"] - 1]) or 0.0
            if pv >= 0.01 * float(sc.full):
                flow.append(f"MFC{m['no']} {pv:.1f} sccm")
        if flow:
            why.append("MFC 유량이 풀스케일 1 % 이상 — " + " · ".join(flow))
        aux = s[A.D_AUX_OUT]
        if DEV.HAS_RF and A.bit(aux, A.AUX_RF):
            why.append("RF 출력이 켜져 있습니다")
        if DEV.HAS_O3:
            o3 = A.bit(aux, A.AUX_O3_GEN) or A.bit(aux, A.AUX_IVB) or A.bit(aux, A.AUX_BYPASS_PUMP)
            if o3 or getattr(link, "applied_aux", 0):
                why.append("O3 라인이 켜져 있습니다 — 명령 11 이 수동 보조 출력을 끄므로 수동 조작의 O3 라인 끄기를 먼저 하세요")
        return why

    # ===================== 시작 · 취소 =====================
    def start(self, state, push_log, push_notice) -> tuple:
        why = self.refuse_reasons(state)
        if why:
            return False, "누설률 점검을 시작할 수 없습니다 — " + " · ".join(why)
        self._cancel_why = ""
        self._cancel_restore = True
        self.phase = "closing"
        self.started_mono = time.monotonic()
        self.progress = {}
        self.task = asyncio.create_task(self._run(state, push_log, push_notice))
        return True, "누설률 점검을 시작합니다 — IV-E 닫기 → 안정 → 측정 → IV-E 다시 열기"

    def cancel(self, why: str = "운전자 취소", restore: bool = True) -> bool:
        if not self.running:
            return False
        self._cancel_why = why
        self._cancel_restore = restore
        return True

    async def cancel_and_wait(self, why: str, restore: bool = False, timeout: float = 3.0) -> bool:
        """운전자가 펌핑 · 벤트 · 전체 닫기를 누름 — 점검을 먼저 취소하고(되돌리기 명령은 보내지 않는다) 끝날 때까지."""
        if not self.cancel(why, restore):
            return False
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        return True

    async def stop(self):
        """프로그램을 끌 때 — 태스크만 정리한다(IV-E 는 닫힌 채 남는다)."""
        t = self.task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ===================== 진행 =====================
    def _abort_reason(self, state) -> tuple:
        """(결과, 이유) — 멈춰야 하면. 없으면 ("", "")."""
        if self._cancel_why:
            return ("CANCEL" if self._cancel_why.startswith("운전자") else "ABORT"), self._cancel_why
        link = state.link
        if not (link and link.connected):
            return "ABORT", "PLC 연결 끊김"
        if getattr(link, "plc_hb_stalled", False):
            return "ABORT", "PLC 하트비트 멈춤"
        blocked = link.id_block_text() if hasattr(link, "id_block_text") else ""
        if blocked:
            return "ABORT", "장비 ID 막힘"
        if state.alarms.has_critical():
            return "ABORT", "중대 알람: " + " · ".join(a["name"] for a in state.alarms.list() if a["crit"])
        if link.status[A.D_STATE] in RUNNING_STATES:
            return "ABORT", "공정 상태로 바뀜"
        return "", ""

    async def _wait(self, state, sec: float, cond=None, isolate=False) -> tuple:
        """sec 동안(cond 가 참이 되면 바로) 기다리며 멈출 이유를 본다. (멈춤 결과, 이유, cond 결과).
        isolate: 격리(IV-E 닫힘 · VV 꺼짐)가 이어지는지도 본다(v0.7.1 L1)."""
        end = time.monotonic() + sec
        while True:
            res, why = self._abort_reason(state)
            if not res and isolate:
                why = self._isolation_broken(state.link)
                res = "ABORT" if why else ""
            if res:
                return res, why, False
            if cond is not None and cond():
                return "", "", True
            if time.monotonic() >= end:
                return "", "", False
            await asyncio.sleep(0.1)

    async def _run(self, state, push_log, push_notice):
        p = params(state.cfg)
        rec = {"gauge": "", "p0": None, "p1": None, "secs": 0.0, "slope": None, "limit": p["limit_mtorr_min"],
               "result": "", "why": ""}
        self._closed = False
        try:
            try:
                await self._steps(state, p, rec, push_log, push_notice)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                rec.update(result="ABORT", why=f"오류 — {type(e).__name__}: {logger.clean(e, 120)}")
            # --- 되돌리기(명령 8) — ★ v0.7.1(L4): try 안 — 종료 · 예외에도 CSV 한 줄 · 이벤트 로그가 남게 ---
            self.phase = "restore"
            if self._closed:
                await self._restore(state, rec, push_log, push_notice)
        except asyncio.CancelledError:
            # 프로그램 종료 — 보낼 수 없다. 명령 11 을 보낸 뒤면 IV-E 는 닫힌 채 남는다
            rec.update(result=rec["result"] or "ABORT",
                       why=_join(rec["why"], "프로그램 종료" + (" — IV-E 는 닫힌 채 남습니다" if self._closed else "")))
            self._finish(rec)
            logger.write("warn", self.line(rec))
            self.phase = ""
            raise
        self._finish(rec)
        lv = "ok" if rec["result"] == "PASS" else "warn"
        msg = self.line(rec)
        await push_log(msg, lv)
        await push_notice(msg, lv)
        self.phase = ""

    def _isolation_broken(self, link) -> str:
        """★ v0.7.1(L1): 격리 확인 — IV-E 닫힘 입력 켜짐 · IV-E 출력 꺼짐 · VV 출력 꺼짐이 이어지는가. 깨졌으면 이유."""
        s = link.status
        if not A.bit(s[A.D_INPUT0], A.IN0_IVE_CLOSE):
            return "격리 깨짐 — IV-E 닫힘 입력이 꺼졌습니다"
        if A.bit(s[A.D_AUX_OUT], A.AUX_IVE):
            return "격리 깨짐 — IV-E 출력이 켜졌습니다"
        if A.bit(s[A.D_AUX_OUT], A.AUX_VV):
            return "격리 깨짐 — VV(벤트) 출력이 켜졌습니다"
        return ""

    async def _wait_closed(self, state, link):
        return await self._wait(state, CLOSE_WAIT_S, lambda: A.bit(link.status[A.D_INPUT0], A.IN0_IVE_CLOSE))

    async def _steps(self, state, p, rec, push_log, push_notice):
        """닫기 → 안정 → 측정. 멈추면 rec 에 결과 · 이유를 적고 돌아온다(되돌리기는 _run 이)."""
        link = state.link
        await push_log("누설률 점검 시작 — 명령 11(모두 닫기)로 IV-E 를 닫습니다", "info")
        result, text = await link.send_command(A.CMD_ALL_CLOSE)
        logger.command("모두 닫기(누설률 점검)", link._cmd_no, text, "local")
        if result is None:
            # ★ v0.7.1(L2): 결과 모름(응답 없음 · 받았을 수 있음) — 닫혔을 수 있다. 닫힘 입력을 10 s 본다
            self._closed = True             # 되돌리기(명령 8)는 한다
            res, why, ok = await self._wait_closed(state, link)
            if res:
                rec.update(result=res, why=_join(f"모두 닫기 결과 모름({text})", why))
                return
            if not ok:
                rec.update(result="ABORT", why=f"모두 닫기 결과 모름({text}) · IV-E 닫힘 입력이 {CLOSE_WAIT_S:g} s 안에 "
                                               "켜지지 않음")
                await push_notice(f"누설률 점검 — 모두 닫기 결과를 몰라 멈춥니다({text}). IV-E 를 다시 엽니다", "warn")
                return
            await push_log(f"누설률 점검 — 모두 닫기 결과는 몰랐지만({text}) IV-E 닫힘 입력이 켜져 계속합니다", "warn")
        elif result != A.RESULT_OK:
            rec.update(result="ABORT", why=f"모두 닫기 거절 — {text}")
            return
        else:
            self._closed = True
            res, why, ok = await self._wait_closed(state, link)
            if res:
                rec.update(result=res, why=why)
                return
            if not ok:
                rec.update(result="ABORT", why=f"IV-E 닫힘 입력이 {CLOSE_WAIT_S:g} s 안에 켜지지 않음")
                return
        self.phase = "settle"
        res, why, _ = await self._wait(state, p["settle_s"], isolate=True)
        if res:
            rec.update(result=res, why=why)
            return
        self.phase = "measure"
        # ★ v0.7.1(L3): 게이지는 측정 시작 때 하나로 정하고 끝까지 그 게이지로 잰다
        g = pick_gauge(state, link.status)
        rec["gauge"] = g
        pts = []
        t0 = time.monotonic()
        nxt = t0
        while True:
            res, why = self._abort_reason(state)
            if not res:
                why = self._isolation_broken(link)
                res = "ABORT" if why else ""
            if res:
                rec.update(result=res, why=why)
                break
            now = time.monotonic()
            if now >= nxt:
                pv, where = read_gauge(state, link.status, g)
                if where == "high":
                    rec.update(result="FAIL", why=f"{g} 범위 위로 넘음(큰 누설) — 측정을 멈춤")
                    break
                if where == "low":
                    rec.update(result="ABORT", why=f"{g} 범위 밖(아래) — 측정을 멈춤")
                    break
                pts.append((now - t0, pv))
                rec["p0"] = rec["p0"] if rec["p0"] is not None else pv
                rec["p1"] = pv
                rec["secs"] = round(now - t0, 1)
                rec["slope"] = slope_mtorr_min(pts) if len(pts) >= 2 else None
                self.progress = {"elapsed_s": rec["secs"], "pressure": pv, "gauge": g, "slope": rec["slope"]}
                nxt += 1.0
                if pv > p["max_torr"]:
                    rec.update(result="FAIL", why=f"압력이 {p['max_torr']:g} Torr 를 넘음(큰 누설) — 측정을 멈춤")
                    break
                if now - t0 >= p["measure_s"]:
                    break
            await asyncio.sleep(0.1)
        if not rec["result"]:
            if rec["slope"] is None:
                rec.update(result="ABORT", why="압력 값을 읽지 못함")
            else:
                ok = rec["slope"] <= p["limit_mtorr_min"]
                rec.update(result="PASS" if ok else "FAIL",
                           why="" if ok else f"상승률 {rec['slope']:.2f} > 한계 {p['limit_mtorr_min']:g} mTorr/min")

    def _link_ok(self, link) -> bool:
        return bool(link and link.connected and not getattr(link, "plc_hb_stalled", False)
                    and not (hasattr(link, "id_block_text") and link.id_block_text()))

    async def _restore(self, state, rec, push_log, push_notice):
        link = state.link
        if not self._cancel_restore:
            return                      # 운전자 명령(펌핑 · 벤트 · 전체 닫기)이 뒤따른다
        # ★ v0.7.1(L5): 짧은 링크 끊김 — 다시 붙기를 10 s 까지 기다린다
        end = time.monotonic() + RELINK_WAIT_S
        while not self._link_ok(state.link) and time.monotonic() < end and self._cancel_restore:
            await asyncio.sleep(0.2)
        link = state.link
        if not self._cancel_restore:
            return
        if not self._link_ok(link):
            await push_notice(f"누설률 점검을 멈췄지만 IV-E 를 다시 열지 못했습니다 — {REOPEN_TEXT}", "warn")
            rec["why"] = _join(rec["why"], "IV-E 를 다시 열지 못함")
            return
        try:
            result, text = await link.send_command(A.CMD_PUMP_START)
            logger.command("펌핑 시작(누설률 점검 끝)", link._cmd_no, text, "local")
        except Exception as e:  # noqa: BLE001
            result, text = None, str(e)
        if result != A.RESULT_OK:
            await push_notice(f"누설률 점검 뒤 펌핑 시작이 처리되지 않았습니다({text}) — {REOPEN_TEXT}", "warn")
            rec["why"] = _join(rec["why"], "IV-E 다시 열기 실패")
            return
        end = time.monotonic() + OPEN_WAIT_S
        while time.monotonic() < end:
            if not self._cancel_restore:
                return                  # ★ v0.7.1(L4): 되돌리는 중 운전자가 벤트 등을 눌렀다 — 그 명령이 바로 간다
            if link.connected and A.bit(link.status[A.D_INPUT0], A.IN0_IVE_OPEN):
                return
            await asyncio.sleep(0.2)
        await push_notice(f"누설률 점검 뒤 IV-E 열림 입력이 {OPEN_WAIT_S:g} s 안에 켜지지 않았습니다 — 확인하세요", "warn")

    @staticmethod
    def line(rec) -> str:
        sl = "—" if rec.get("slope") is None else f"{rec['slope']:.2f} mTorr/min"
        return (f"누설률 점검 {rec['result']} — {sl} (한계 {rec['limit']:g}, {rec.get('gauge') or '—'} "
                f"{_t(rec.get('p0'))} → {_t(rec.get('p1'))} Torr, {rec.get('secs', 0):g} s)"
                + (f" · {rec['why']}" if rec.get("why") else ""))

    def _finish(self, rec):
        row = [time.strftime("%Y-%m-%d %H:%M:%S"), rec.get("gauge") or "", _t(rec.get("p0")), _t(rec.get("p1")),
               f"{rec.get('secs', 0):g}", "" if rec.get("slope") is None else f"{rec['slope']:.3f}",
               f"{rec['limit']:g}", rec["result"], rec.get("why") or ""]
        self.last = dict(zip(HEAD, row))
        try:
            p = csv_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            new = not os.path.isfile(p)
            with open(p, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(HEAD)
                w.writerow([logger.clean(x, 300) for x in row])
        except Exception as e:  # noqa: BLE001
            logger.write("warn", f"누설률 점검 기록 실패: {e}")
        self.recent_rows()
        self.recent.insert(0, self.last)
        del self.recent[RECENT_N:]

    def recent_rows(self) -> list:
        if self.recent is None:
            self.recent = []
            try:
                with open(csv_path(), encoding="utf-8-sig", errors="replace", newline="") as f:
                    rows = list(csv.reader(f))
                head = rows[0] if rows else HEAD
                self.recent = [dict(zip(head, r)) for r in reversed(rows[1:]) if r][:RECENT_N]
            except OSError:
                pass
        return self.recent

    def blocked(self, state) -> str:
        """★ v0.7.1(L7): [Leak Check] 단추를 흐리게 둘 이유(연결 없음 · 멈춤 · ID 막힘 · 공정 중 · 시작 절차 중), 없으면 ''."""
        link = getattr(state, "link", None) if state is not None else None
        if not (link and link.connected):
            return "PLC 에 연결되어 있지 않습니다"
        if getattr(link, "plc_hb_stalled", False):
            return "PLC 하트비트 멈춤"
        if hasattr(link, "id_block_text") and link.id_block_text():
            return link.id_block_text()
        if link.status[A.D_STATE] in RUNNING_STATES:
            return "공정 중입니다"
        runner = getattr(state, "runner", None)
        if runner is not None and getattr(runner, "busy", False):
            return "공정 시작 절차가 진행 중입니다"
        return ""

    def status(self, cfg, state=None) -> dict:
        p = params(cfg)
        return {"running": self.running, "phase": self.phase if self.running else "",
                "blocked": "" if self.running else self.blocked(state),
                "elapsed_s": round(time.monotonic() - self.started_mono, 1) if self.running else 0,
                "progress": dict(self.progress) if self.running else {},
                "settle_s": p["settle_s"], "measure_s": p["measure_s"], "limit": p["limit_mtorr_min"],
                "recent": self.recent_rows()}


def _join(a: str, b: str) -> str:
    return f"{a} · {b}" if a and b else (a or b)


def _t(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{v:.3e}" if v < 0.1 else f"{v:.4g}"


leak = LeakCheck()
