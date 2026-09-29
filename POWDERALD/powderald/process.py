"""
process.py — 공정 시작 흐름 · 진행 · 종료 감지.

시작은 한 번의 명령이 아니라 흐름이다:
    레시피 검증 → PLC 표 올리기 → 베이스 압력 대기 → 명령 1
중간에 PLC 가 끊기거나 중대 알람·안전 정지 요구가 생기면 대기를 취소한다.

★ 시작 조건 판정은 서버에서만 한다. 화면은 항목과 현재 값을 받아 그리기만 한다 —
  화면이 따로 판단하면 "화면에는 되는데 PLC 가 거절"이 된다.

★ 최종 판단은 PLC 결과다. 여기서 통과시켰다고 "됐다"고 말하지 않는다.
"""

import time
import asyncio

from . import addresses as A
from . import device as DEV
from . import logger
from . import recipe as R
from . import storage

# 시작 흐름 상태
IDLE = "idle"
UPLOADING = "uploading"
BASE_WAIT = "base_wait"
STARTING = "starting"

RUNNING_STATES = (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)


class ProcessRunner:
    """공정 시작 흐름과 진행·종료 감지를 맡는다. 서버가 하나만 가진다."""

    def __init__(self, state):
        self.state = state
        self.phase = IDLE
        self.message = ""
        self.recipe_name = ""
        self.recipe = None              # 실행 중(또는 시작하려는) 레시피 사본
        self.table = None               # to_plc_words 결과
        self.base_wait_started = 0.0
        self.base_ok_since = 0.0
        self.started_at = 0.0
        self.ended_at = 0.0
        self.last_result = ""
        self._was_running = False
        self._abort_sent = False
        self._saw_alarm = False
        self._cancel = False

    # ===================== 시작 조건 =====================
    def start_checks(self) -> list:
        """[{key, label, ok, detail}] — 항목마다 현재 값과 이유를 함께 준다."""
        st = self.state
        link = st.link
        cfg = st.cfg
        out = []

        conn = bool(link and link.connected)
        out.append({"key": "plc", "label": "PLC 연결 · 하트비트", "ok": conn and link.plc_hb_ok,
                    "detail": (link.addr_text if link else "") +
                              ("" if conn else " — 연결 안 됨")})
        if not conn:
            return out

        s = link.status
        code = s[A.D_STATE]
        out.append({"key": "state", "label": "장비 상태", "ok": code not in RUNNING_STATES
                    and code != A.STATE_SAFE_STOP,
                    "detail": A.STATE_NAMES.get(code, str(code))})

        crit = [a["name"] for a in st.alarms.list() if a["crit"]]
        out.append({"key": "alarm", "label": "중대 알람 없음", "ok": not crit,
                    "detail": " · ".join(crit) if crit else "없음"})

        ilk = s[A.D_INTERLOCK]
        out.append({"key": "pump", "label": "펌프", "ok": A.bit(ilk, A.ILK_PUMP),
                    "detail": "운전 중" if A.bit(ilk, A.ILK_PUMP) else "펌프 운전·알람을 확인하세요"})
        out.append({"key": "valve_ok", "label": "공정 밸브 허가",
                    "ok": A.bit(ilk, A.ILK_VALVE_OK),
                    "detail": self._valve_ok_detail(s)})

        conv = st.conv
        cur = conv.cvg.to_torr(s[A.D_CVG_RAW])
        target = (cfg.get("params") or {}).get("base_press_torr")
        out.append({"key": "base", "label": "베이스 압력", "ok": A.bit(ilk, A.ILK_VACUUM),
                    "detail": f"현재 {_torr(cur)} / 목표 {_torr(target)} Torr"})

        if DEV.HAS_O3:
            ok = A.bit(ilk, A.ILK_O3_OK)
            out.append({"key": "o3", "label": "O3 허가", "ok": ok,
                        "detail": "준비됨" if ok else "O3 라인을 먼저 켜세요",
                        "action": None if ok else "o3_on"})

        res = st.recipe_check or {}
        out.append({"key": "recipe", "label": "레시피 검증",
                    "ok": bool(self.recipe) and not res.get("errors"),
                    "detail": (self.recipe_name or "레시피를 고르세요") +
                              (f" — 오류 {len(res.get('errors') or [])}건"
                               if res.get("errors") else "")})

        hr = (cfg.get("process") or {}).get("heater_ready") or {}
        if hr.get("enabled"):
            band = float(hr.get("band_c") or 2.0)
            bad = []
            for h in cfg.get("heaters") or []:
                if not h.get("enabled"):
                    continue
                i = h["ch"] - 1
                pv = _temp(s[A.D_HEATER_PV + i])
                sv = _temp(link.sync_regs[A.D_HEATER_SV + i - A.SYNC_BASE]) \
                    if getattr(link, "sync_regs", None) else None
                if sv is None or abs(pv - sv) > band:
                    bad.append(f"CH{h['ch']} {h['name']}")
            out.append({"key": "heater", "label": f"히터 안정 (±{band:g} °C)", "ok": not bad,
                        "detail": " · ".join(bad) if bad else "모든 사용 채널이 설정 안"})
        return out

    def _valve_ok_detail(self, s) -> str:
        miss = []
        i0 = s[A.D_INPUT0]
        if not A.bit(i0, A.IN0_IVE_OPEN):
            miss.append("IV-E 열림")
        if A.bit(i0, A.IN0_ATM):
            miss.append("대기압 아님")
        if A.bit(s[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ):
            miss.append("안전 정지 요구 해제")
        if not A.bit(s[A.D_INTERLOCK], A.ILK_BASIC):
            miss.append("기본 인터락")
        return "필요: " + " · ".join(miss) if miss else "허가됨"

    def can_start(self):
        checks = self.start_checks()
        # 베이스 압력은 대기로 채울 수 있으므로 시작 버튼을 막지 않는다.
        blocking = [c for c in checks if not c["ok"] and c["key"] != "base"]
        return (not blocking), blocking, checks

    # ===================== 레시피 고르기 =====================
    def select(self, name: str):
        """레시피를 고르고 검증한다. (성공, 메시지)"""
        data = storage.load(name)
        if data is None:
            self.recipe = None
            self.recipe_name = ""
            self.state.recipe_check = {}
            return False, f"레시피를 열 수 없습니다: {name}"
        self.recipe = data
        self.recipe_name = name
        self.state.recipe_check = R.validate(self.state.cfg, data)
        self.table = R.to_plc_words(self.state.cfg, self.state.conv, data)
        return True, ""

    def estimate(self) -> dict:
        if not self.recipe:
            return {}
        s = R.summarize(self.state.cfg, self.recipe)
        s["name"] = self.recipe_name
        return s

    # ===================== 시작 흐름 =====================
    async def start(self, push_log, push_notice):
        """올리기 → 베이스 압력 대기 → 명령 1. 화면은 phase 로 진행을 본다."""
        st = self.state
        if self.phase != IDLE:
            return False, "이미 시작 절차가 진행 중입니다"
        if not self.recipe:
            return False, "레시피를 고르세요"
        check = R.validate(st.cfg, self.recipe)
        if check["errors"]:
            return False, f"레시피 검증 오류 {len(check['errors'])}건 — 먼저 고치세요"
        ok, blocking, _ = self.can_start()
        if not ok:
            return False, "시작 조건 미달 — " + " · ".join(c["label"] for c in blocking)

        # ★ 공정이 시작되면 수동 밸브 잠금을 다시 채운다 — 화면을 열어 둔 채로
        #   공정 중에 잘못 누르는 일을 막는다.
        st.manual_unlock_until = 0.0
        self._cancel = False
        self._abort_sent = False
        self._saw_alarm = False
        self.last_result = ""

        # --- 올리기 ---
        self.phase = UPLOADING
        self.message = "PLC 에 레시피를 올리는 중"
        self.table = R.to_plc_words(st.cfg, st.conv, self.recipe)
        good, why = await st.link.upload_recipe(self.table["words"], self.table["checksum"])
        await push_log(f"레시피 올리기 [{self.recipe_name}] 번호 {self.table['number']} — {why}",
                       "ok" if good else "err")
        if not good:
            self.phase = IDLE
            self.message = ""
            return False, f"레시피 올리기 실패 — {why}"

        # --- 베이스 압력 대기 ---
        pr = st.cfg.get("process") or {}
        timeout = float(pr.get("base_wait_timeout_s") or 1800)
        need = float(pr.get("base_stable_s") or 0)
        self.phase = BASE_WAIT
        self.base_wait_started = time.monotonic()
        self.base_ok_since = 0.0
        await push_log(f"베이스 압력 대기 시작 (제한 {timeout:g} s)", "info")

        while True:
            await asyncio.sleep(0.2)
            if self._cancel:
                self.phase = IDLE
                self.message = ""
                await push_log("베이스 압력 대기 취소 (운전자)", "warn")
                return False, "베이스 압력 대기를 취소했습니다"
            stop_why = self._wait_broken()
            if stop_why:
                self.phase = IDLE
                self.message = ""
                await push_log(f"베이스 압력 대기 중단 — {stop_why}", "err")
                return False, stop_why
            now = time.monotonic()
            if A.bit(st.link.status[A.D_INTERLOCK], A.ILK_VACUUM):
                if not self.base_ok_since:
                    self.base_ok_since = now
                if now - self.base_ok_since >= need:
                    break
            else:
                self.base_ok_since = 0.0
            self.message = f"베이스 압력 대기 {now - self.base_wait_started:.0f} s"
            if now - self.base_wait_started > timeout:
                self.phase = IDLE
                self.message = ""
                await push_log(f"베이스 압력 대기 시간 초과 ({timeout:g} s)", "err")
                return False, f"베이스 압력에 도달하지 못했습니다 ({timeout:g} s 초과)"

        # --- 시작 명령 ---
        self.phase = STARTING
        self.message = "공정 시작 명령"
        no = st.link._cmd_no
        result, text = await st.link.send_command(A.CMD_PROCESS_START)
        logger.command("공정 시작", no, text, "local")
        self.phase = IDLE
        self.message = ""
        if result == A.RESULT_OK:
            self.started_at = time.time()
            # ★ '공정 중'으로 본 적이 있다는 표시(_was_running)는 여기서 세우지 않는다.
            #   PLC 상태는 100 ms 주기로 읽어 오므로, 명령이 처리된 직후에도 아직 '대기'로
            #   읽힌다. 그 한 번을 종료로 오해해 '정상 종료' 로그가 먼저 찍힌다.
            await push_log(f"공정 시작 — {self.recipe_name} (번호 {self.table['number']}, "
                           f"예상 {_hms(R.total_ms(st.cfg, self.recipe))})", "ok")
            return True, "공정을 시작했습니다"
        detail = text
        if result == A.RESULT_INTERLOCK:
            miss = [c["label"] for c in self.start_checks() if not c["ok"]]
            detail = f"{text} — 빠진 조건: {' · '.join(miss) if miss else '인터락 확인'}"
        elif result == A.RESULT_RECIPE:
            detail = f"{text} — PLC 표 검사 불합격 (합계·개수 확인)"
        await push_log(f"공정 시작 거절 — {detail}", "err")
        return False, detail

    def _wait_broken(self):
        """대기 중 그만둬야 하는 사유. 없으면 빈 문자열."""
        link = self.state.link
        if not (link and link.connected):
            return "PLC 연결이 끊겼습니다"
        s = link.status
        if A.bit(s[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ):
            return "안전 정지 요구가 생겼습니다"
        crit = [a["name"] for a in self.state.alarms.list() if a["crit"]]
        if crit:
            return "중대 알람: " + " · ".join(crit)
        return ""

    def cancel_wait(self):
        if self.phase == BASE_WAIT:
            self._cancel = True
            return True
        return False

    # ===================== 진행 · 종료 감지 =====================
    def tick(self, push_log_sync):
        """샘플링 루프가 부른다. 공정 중 → 아님 으로 바뀌면 종료를 기록한다."""
        st = self.state
        link = st.link
        if not (link and link.connected):
            return
        s = link.status
        running = s[A.D_STATE] in RUNNING_STATES
        if running:
            self._was_running = True
            if not self.started_at:
                self.started_at = time.time()
            if st.alarms.has_critical():
                self._saw_alarm = True
            return
        if self._was_running:
            self._was_running = False
            self.ended_at = time.time()
            took = self.ended_at - (self.started_at or self.ended_at)
            where = (f"블록 {s[A.D_SEQ_BLOCK]} · 스텝 {s[A.D_SEQ_STEP]} · "
                     f"사이클 {A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1])}")
            if self._abort_sent or self._saw_alarm or s[A.D_SEQ_STATE] == 8:
                why = "운전자 중단" if self._abort_sent else (
                    "알람" if self._saw_alarm else "PLC 중단")
                self.last_result = f"중단 ({why})"
            else:
                self.last_result = "정상 종료"
            push_log_sync("ok" if self.last_result == "정상 종료" else "warn",
                          f"공정 {self.last_result} — {self.recipe_name or ''} · "
                          f"걸린 시간 {_hms(int(took * 1000))} · 마지막 위치 {where}")
            self.started_at = 0.0
            self._abort_sent = False
            self._saw_alarm = False

    def note_abort(self):
        """즉시 중단 명령을 보냈다는 표시(종료 사유 구분용)."""
        self._abort_sent = True

    def progress(self) -> dict:
        """화면이 그대로 쓰는 진행 정보."""
        st = self.state
        link = st.link
        if not (link and link.connected):
            return {}
        s = link.status
        code = s[A.D_STATE]
        running = code in RUNNING_STATES
        cycle = A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1])
        step_ms = A.dword(s[A.D_SEQ_STEP_MS], s[A.D_SEQ_STEP_MS + 1])
        out = {
            "running": running,
            "phase": self.phase,
            "message": self.message,
            "recipe": self.recipe_name,
            "number": (self.table or {}).get("number"),
            "block": s[A.D_SEQ_BLOCK],
            "step": s[A.D_SEQ_STEP],
            "cycle": cycle,
            "group_pass": s[A.D_SEQ_GROUP_PASS],
            "step_ms": step_ms,
            "paused": code == A.STATE_PAUSE,
            "prep": s[A.D_SEQ_STATE] == 3,
            "last_result": self.last_result,
            "elapsed_s": int(time.time() - self.started_at) if self.started_at else 0,
        }
        if not running:
            # 시작 조건은 화면이 아니라 서버가 판정한다 — 화면은 항목과 이유만 그린다.
            ok, blocking, checks = self.can_start()
            out["checks"] = checks
            out["can_start"] = ok
            out["blocking"] = [c["label"] for c in blocking]
            if self.recipe:
                out["estimate"] = self.estimate()
        if self.recipe and running:
            pos = {"block": s[A.D_SEQ_BLOCK], "step": s[A.D_SEQ_STEP], "cycle": cycle,
                   "group_pass": s[A.D_SEQ_GROUP_PASS], "step_elapsed_ms": step_ms,
                   "paused": out["paused"]}
            out["remaining_ms"] = R.remaining_ms(st.cfg, self.recipe, pos)
            out["total_ms"] = R.total_ms(st.cfg, self.recipe)
            out["eta"] = time.strftime("%H:%M:%S",
                                       time.localtime(time.time() + out["remaining_ms"] / 1000))
            blocks = self.recipe.get("blocks") or []
            bno = s[A.D_SEQ_BLOCK]
            if 1 <= bno <= len(blocks):
                b = blocks[bno - 1]
                base = sum(len(x.get("steps") or []) for x in blocks[:bno - 1])
                out["block_name"] = b.get("name", "")
                out["block_count"] = len(blocks)
                out["block_repeat"] = int(b.get("repeat") or 1)
                out["step_in_block"] = s[A.D_SEQ_STEP] - base
                out["steps"] = [{"name": x.get("name", ""), "ms": int(x.get("time_ms") or 0)}
                                for x in (b.get("steps") or [])]
            g, gi = R._group_of(self.recipe, bno)
            if g:
                out["group"] = {"no": gi, "repeat": int(g.get("repeat") or 1)}
        return out

    # ===================== PLC 가 이미 돌고 있을 때 =====================
    async def adopt_running(self, push_log):
        """PC 를 다시 켰는데 PLC 가 이미 공정 중이면 레시피를 되찾아 이어 간다."""
        link = self.state.link
        if not (link and link.connected):
            return
        if link.status[A.D_STATE] not in RUNNING_STATES:
            return
        if self.recipe:
            return
        words = await link.read_recipe_area()
        if not words:
            return
        info = R.from_plc_words(words)
        name = storage.find_by_number(info["number"])
        if name:
            self.select(name)
            await push_log(f"PLC 가 이미 공정 중입니다 — 레시피 [{name}] "
                           f"(번호 {info['number']})로 이어 갑니다", "warn")
        else:
            self.recipe_name = f"(PLC 번호 {info['number']})"
            self.table = {"number": info["number"], "checksum": info["checksum"]}
            await push_log(f"PLC 가 이미 공정 중입니다 — 번호 {info['number']} 에 맞는 "
                           f"로컬 레시피가 없어 이름 없이 표시합니다", "warn")
        self._was_running = True
        self.started_at = time.time()


# ===================== 작은 도우미 =====================
def _torr(v) -> str:
    if v is None:
        return "—"
    return f"{v:.3f}" if v >= 0.1 else f"{v:.1E}"


def _temp(raw) -> float:
    v = int(raw) & 0xFFFF
    return (v - 0x10000 if v & 0x8000 else v) / 10.0


def _hms(ms) -> str:
    s = max(0, int((ms or 0) / 1000))
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
