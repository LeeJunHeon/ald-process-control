"""
process.py — 공정 시작 흐름 · 진행 · 종료 감지.

시작은 한 번의 명령이 아니라 흐름이다:
    레시피 검증 → PLC 표 올리기 → 베이스 압력 대기 → 명령 1
중간에 PLC 가 끊기거나 중대 알람·안전 정지 요구가 생기면 대기를 취소한다.

★ 시작 조건 판정은 서버에서만 한다. 화면은 항목과 현재 값을 받아 그리기만 한다 —
  화면이 따로 판단하면 "화면에는 되는데 PLC 가 거절"이 된다.

★ 최종 판단은 PLC 결과다. 여기서 통과시켰다고 "됐다"고 말하지 않는다.
"""

import copy
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
NO_ADDR_TEXT = "PLC 주소가 없어 연결하지 않았습니다 — 설정 탭에서 이 장비 PLC 주소를 넣으세요"


class ProcessRunner:
    """공정 시작 흐름과 진행·종료 감지를 맡는다. 서버가 하나만 가진다."""

    def __init__(self, state):
        self.state = state
        self.phase = IDLE
        self.message = ""
        self.recipe_name = ""
        self.recipe = None              # 실행 중(또는 시작하려는) 레시피 사본
        self.table = None               # to_plc_words 결과
        # ★ 시작을 누른 순간의 스냅샷(이름·레시피·표). 시작 흐름·진행 표시·로그·데이터 로그·
        #   끝 기록은 이것만 쓴다 — 도중에 다른 레시피를 골라도 돌고 있는 공정 기록이 바뀌지 않게.
        self.run = None
        self._task = None               # 백그라운드 시작 흐름
        self.base_wait_started = 0.0
        self.base_ok_since = 0.0
        self.started_at = 0.0
        self.ended_at = 0.0
        self.last_result = ""
        self._was_running = False
        self._abort_sent = False        # 즉시 중단이 처리됨(결과 0)이고 보낼 때 공정 중이었다
        self._abort_pending = False     # 즉시 중단을 보내는 중(결과를 아직 모른다)
        self._end_deferred = None       # 그동안 본 종료 — 결과가 오면 기록한다
        self._stop_reserved = False     # 사이클 후 정지 예약(명령 4 처리됨)
        self._last_pos = None           # 공정 중 마지막으로 본 (블록, 스텝, 사이클)
        self._cancel = False
        self._cancel_why = "운전자"
        self._b13_wait = None           # (기한, 끝 인자) — 시퀀서 8 인데 알람0 b13 이 아직 안 보일 때 1 s 기다림
        self._abort_unknown = False     # 즉시 중단을 보냈지만 결과를 못 받았다
        self._abort_unknown_at = 0.0
        self._end_seen_mono = 0.0       # 끝을 본 시각(b13 1 s 창의 출발점)
        self._gen = 0                   # 시작마다 +1 — 늦게 온 앞 공정의 중단 결과가 새 공정에 붙지 않게
        self._abort_gen = -1
        self._b13_pre = False           # 시작 때 이미 서 있던 b13(중대 아님 — 리셋 전까지 남는다)
        self._b13_cleared = False       # 이번 공정 중 b13 = 0 을 봤다
        # 데이터 로그 · 끝 판정의 공정 구간 — 명령 1 처리됨(또는 이어받기)부터 끝 판정까지.
        # ★ '공정 중' 읽기에 묶으면 짧은 PLC 끊김에 데이터 로그가 두 파일로 갈라진다.
        self.active_run = False
        self._run_started_mono = 0.0
        # RF 스텝 감시 — {(블록, 스텝): 이번 스텝에서 RF 출력을 봤는가}, 경고한 것
        self._rf_watch = None
        self._rf_warned = set()
        # 히터 안정 — 채널마다 ±band 안에 들어온 시각(monotonic)
        self._hr_since = {}

    # ===================== 시작 조건 =====================
    def start_checks(self) -> list:
        """[{key, label, ok, detail}] — 항목마다 현재 값과 이유를 함께 준다."""
        st = self.state
        link = st.link
        cfg = st.cfg
        out = []

        conn = bool(link and link.connected)
        cfg_err = getattr(link, "config_error", "") if link else ""
        out.append({"key": "plc", "label": "PLC 연결 · 하트비트", "ok": conn and link.plc_hb_ok,
                    "detail": NO_ADDR_TEXT if cfg_err else
                    (link.addr_text if link else "") + ("" if conn else " — 연결 안 됨")})
        if not conn:
            return out
        # 장비 ID — 막혔으면(다른 장비·ID 없음) 연결 안 됨 때처럼 여기서 끝낸다
        from .plclink import id_text
        blocked = link.id_block_text() if hasattr(link, "id_block_text") else ""
        out.append({"key": "device_id", "label": "장비 ID", "ok": not blocked,
                    "detail": f"읽은 ID {id_text(getattr(link, 'device_id', None))} / "
                              f"이 장비 {id_text(DEV.DEVICE_ID)}" + (f" — {blocked}" if blocked else "")})
        if blocked:
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
        # 화면은 cur · target 숫자로 게이지와 같은 규칙(fmt.torr)으로 다시 쓴다(detail 은 로그용)
        out.append({"key": "base", "label": "베이스 압력", "ok": A.bit(ilk, A.ILK_VACUUM),
                    "detail": f"현재 {_torr(cur)} / 목표 {_torr(target)} Torr",
                    "cur": cur, "target": target})

        if DEV.HAS_O3:
            ok = A.bit(ilk, A.ILK_O3_OK)
            out.append({"key": "o3", "label": "O3 허가", "ok": ok,
                        "detail": "준비됨" if ok else "O3 라인을 먼저 켜세요",
                        "action": None if ok else "o3_on"})
            # ★ 래더의 공정 시작 조건은 O3 허가(바이패스 펌프·IV-B·5 s·알람 없음·O3 한계 > 0)만 보고
            #   발생기 요청은 보지 않는다 — 발생기만 꺼진 채 시작하면 O3 없이 돈다. O3 를 쓰는
            #   레시피면 발생기 출력(보조 출력 b9)을 시작 조건에 넣는다.
            if _uses_o3(self.recipe):
                gen = A.bit(s[A.D_AUX_OUT], A.AUX_O3_GEN)
                out.append({"key": "o3_gen", "label": "O3 발생기 켜짐", "ok": gen,
                            "detail": "켜짐" if gen else "레시피가 O3 를 씁니다 — [O3 라인 켜기]로 발생기를 켜세요",
                            "action": None if gen else "o3_on"})

        res = st.recipe_check or {}
        out.append({"key": "recipe", "label": "레시피 검증",
                    "ok": bool(self.recipe) and not res.get("errors"),
                    "detail": (self.recipe_name or "레시피를 고르세요") +
                              (f" — 오류 {len(res.get('errors') or [])}건"
                               if res.get("errors") else "")})

        hr = (cfg.get("process") or {}).get("heater_ready") or {}
        if hr.get("enabled"):
            band = float(hr.get("band_c") or 2.0)
            need = float(hr.get("stable_s") or 0)
            bad = []
            now = time.monotonic()
            for h in cfg.get("heaters") or []:
                if not h.get("enabled"):
                    continue
                ch = h["ch"]
                if not _tc_ok(s, h):
                    bad.append(f"CH{ch} {h['name']} 통신 없음")
                    continue
                since = self._hr_since.get(ch)
                if since is None:
                    bad.append(f"CH{ch} {h['name']}")
                elif now - since < need:
                    bad.append(f"CH{ch} {h['name']} 안정 {now - since:.0f}/{need:g} s")
            out.append({"key": "heater", "label": f"히터 안정 (±{band:g} ℃ · {need:g} s)", "ok": not bad,
                        "detail": " · ".join(bad) if bad else "모든 사용 채널이 설정 안"})
        if DEV.HAS_RF and _uses_rf(self.recipe):
            # ★ 래더의 공정 허가에는 RF 조건이 없다 — RF 가 안 나오면 RF 스텝이 RF 없이 끝까지 돈다.
            #   레시피가 RF 를 쓰면 RF 준비 · RF 알람 없음 · PLC 의 RF 상한(되읽기) > 0 을 시작 조건에 넣는다
            i1 = s[A.D_INPUT1]
            rf_max = (getattr(link, "prm_readback", {}) or {}).get(A.D_PRM_RF_MAX) or 0
            miss = []
            if not A.bit(i1, A.IN1_RF_READY):
                miss.append("RF 준비 입력 꺼짐")
            if A.bit(i1, A.IN1_RF_ALM):
                miss.append("RF 알람 입력")
            if rf_max <= 0:
                miss.append("PLC 의 RF 상한(PRM_RF_MAX) 0")
            out.append({"key": "rf", "label": "RF 준비", "ok": not miss,
                        "detail": " · ".join(miss) if miss else "준비됨"})
        return out

    def _track_heaters(self, s):
        """샘플링 루프에서 — 채널마다 ±band 안에 들어온 시각을 기록한다(heater_ready.stable_s 용)."""
        cfg = self.state.cfg
        hr = (cfg.get("process") or {}).get("heater_ready") or {}
        if not hr.get("enabled"):
            self._hr_since = {}
            return
        band = float(hr.get("band_c") or 2.0)
        link = self.state.link
        now = time.monotonic()
        for h in cfg.get("heaters") or []:
            if not h.get("enabled"):
                continue
            ch = h["ch"]
            i = ch - 1
            sv_raw = link.cmd_reg(A.D_HEATER_SV + i)
            inband = (_tc_ok(s, h) and sv_raw is not None
                      and abs(_temp(s[A.D_HEATER_PV + i]) - _temp(sv_raw)) <= band)
            if inband:
                self._hr_since.setdefault(ch, now)
            else:
                self._hr_since.pop(ch, None)

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
        # 형식이 틀린 레시피는 표를 만들지 않는다(검증 오류로 시작·올리기가 막힌다)
        self.table = (None if R.shape_errors(data)
                      else R.to_plc_words(self.state.cfg, self.state.conv, data))
        return True, ""

    def estimate(self) -> dict:
        if not self.recipe:
            return {}
        s = R.summarize(self.state.cfg, self.recipe)
        s["name"] = self.recipe_name
        return s

    # ===================== 스냅샷 =====================
    @property
    def active_name(self) -> str:
        return self.run.get("name") if self.run is not None else self.recipe_name

    @property
    def active_recipe(self):
        return self.run.get("recipe") if self.run is not None else self.recipe

    @property
    def active_table(self):
        return self.run.get("table") if self.run is not None else self.table

    @property
    def busy(self) -> bool:
        """시작 흐름(올리기·베이스 압력 대기·명령 1)이 도는 중인가."""
        return self.phase != IDLE or bool(self._task and not self._task.done())

    # ===================== 시작 흐름 =====================
    def begin(self, push_log, push_notice, notify=None):
        """시작 흐름을 러너가 쥐는 백그라운드 태스크로 띄우고 바로 돌아온다.
        ★ 명령 통로를 붙잡지 않는다 — 베이스 압력 대기(최대 30 분) 동안에도 대기 취소·알람 확인·
          벤트·펌핑 정지·전체 닫기가 바로 처리돼야 한다. (성공, 메시지)"""
        if self.busy:
            return False, "이미 시작 절차가 진행 중입니다"
        if not self.recipe:
            return False, "레시피를 고르세요"

        async def run():
            ok, msg = await self.start(push_log, push_notice)
            if notify:
                try:
                    await notify(msg, ok)
                except Exception:  # noqa: BLE001
                    pass

        self._task = asyncio.create_task(run())
        return True, "시작 절차를 시작합니다 — 레시피 올리기 → 베이스 압력 대기 → 공정 시작"

    async def stop_task(self):
        """프로그램을 끌 때 시작 흐름 태스크를 정리한다."""
        t = self._task
        if t and not t.done():
            self._cancel = True
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def start(self, push_log, push_notice):
        """올리기 → 베이스 압력 대기 → 명령 1. 화면은 phase 로 진행을 본다.
        ★ 어떤 예외가 나도 phase 를 idle 로 돌린다 — 'uploading' 에 멈추면 다음 시작이
          프로그램을 다시 켜기 전까지 '이미 진행 중'으로 거절된다."""
        if self.phase != IDLE:
            return False, "이미 시작 절차가 진행 중입니다"
        try:
            return await self._start_flow(push_log, push_notice)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            why = f"시작 절차 오류 — {type(e).__name__}: {logger.clean(e, 200)}"
            logger.write("err", why)
            try:
                await push_log(why, "err")
            except Exception:  # noqa: BLE001
                pass
            return False, why
        finally:
            self.phase = IDLE
            self.message = ""

    async def _start_flow(self, push_log, push_notice):
        st = self.state
        if not self.recipe:
            return False, "레시피를 고르세요"
        check = R.validate(st.cfg, self.recipe)
        if check["errors"]:
            return False, f"레시피 검증 오류 {len(check['errors'])}건 — 먼저 고치세요"
        blocked = st.link.id_block_text() if (st.link and hasattr(st.link, "id_block_text")) else ""
        if blocked:
            return False, blocked
        ok, blocking, _ = self.can_start()
        if not ok:
            return False, "시작 조건 미달 — " + " · ".join(c["label"] for c in blocking)

        # ★ 공정이 시작되면 수동 밸브 잠금을 다시 채운다 — 화면을 열어 둔 채로
        #   공정 중에 잘못 누르는 일을 막는다.
        st.manual_unlock_until = 0.0
        # ★ Powder: O3 끄기 뒤 바이패스 라인 닫기 예약이 시작 흐름 중에 터지면 O3 허가가 빠진다 — 취소
        if getattr(st, "o3_off_task", None) is not None:
            from .commands import _cancel_o3_timer
            _cancel_o3_timer()
            await push_log("O3 바이패스 라인 닫기 예약을 취소했습니다 — 공정 시작 절차", "warn")
        # ★ 미뤄 둔 앞 공정의 끝(b13 대기 · 중단 결과 대기)을 지금 본 값으로 먼저 적고 앞 데이터 로그를 닫는다 —
        #   아래에서 상태를 지우면 앞 끝이 사라지거나 틀리게 적힌다
        self._flush_pending_end()
        self._gen += 1
        self._cancel_why = "운전자"
        self._cancel = False
        self._abort_sent = False
        self._abort_pending = False
        self._end_deferred = None
        self._stop_reserved = False
        self._last_pos = None
        self.last_result = ""
        self._b13_wait = None
        self._abort_unknown = False

        # --- 스냅샷 ---
        rec = copy.deepcopy(self.recipe)
        tbl = R.to_plc_words(st.cfg, st.conv, rec)
        snap = {"name": self.recipe_name, "recipe": rec, "table": tbl}

        # --- 올리기 ---
        self.phase = UPLOADING
        self.message = "PLC 에 레시피를 올리는 중"
        good, why = await st.link.upload_recipe(tbl["words"], tbl["checksum"])
        await push_log(f"레시피 올리기 [{snap['name']}] 번호 {tbl['number']} — {why}",
                       "ok" if good else "err")
        if not good:
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
                await push_log(f"베이스 압력 대기 취소 — {self._cancel_why}", "warn")
                return False, f"베이스 압력 대기를 취소했습니다 ({self._cancel_why})"
            stop_why = self._wait_broken()
            if stop_why:
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
                await push_log(f"베이스 압력 대기 시간 초과 ({timeout:g} s)", "err")
                return False, f"베이스 압력에 도달하지 못했습니다 ({timeout:g} s 초과)"

        # --- 시작 명령 ---
        self.phase = STARTING
        self.message = "공정 시작 명령"
        # ★ 명령 1 직전에 PLC 표가 올린 그대로인지 다시 본다(합계·검증 통과·레시피 번호)
        mism = await self._plc_table_differs(tbl)
        if mism:
            await push_log(f"공정 시작 취소 — {mism}", "err")
            return False, mism
        # ★ 스냅샷은 명령 1 을 보내기 직전에 세운다 — 샘플링 루프가 결과보다 먼저 '공정 중'을 보면
        #   데이터 로그가 앞 공정의 이름·레시피로 열린다. 거절되면 되돌린다.
        prev_run = self.run
        self.run = snap
        self._b13_pre = A.bit(st.link.status[A.D_ALARM0], A.ALM0_RECIPE)
        self._b13_cleared = False
        no = st.link._cmd_no
        try:
            result, text = await st.link.send_command(A.CMD_PROCESS_START)
        except BaseException:
            self.run = prev_run
            raise
        logger.command("공정 시작", no, text, "local")
        if result != A.RESULT_OK:
            self.run = prev_run
        if result == A.RESULT_OK:
            self.started_at = time.time()
            self.active_run = True              # 데이터 로그 · 끝 판정 구간 시작
            self._run_started_mono = time.monotonic()
            self._rf_watch = None
            self._rf_warned = set()
            # ★ '공정 중'으로 본 적이 있다는 표시(_was_running)는 여기서 세우지 않는다.
            #   PLC 상태는 100 ms 주기로 읽어 오므로, 명령이 처리된 직후에도 아직 '대기'로
            #   읽힌다. 그 한 번을 종료로 오해해 '정상 종료' 로그가 먼저 찍힌다.
            await push_log(f"공정 시작 — {snap['name']} (번호 {tbl['number']}, "
                           f"예상 {_hms(R.total_ms(st.cfg, rec))})", "ok")
            return True, "공정을 시작했습니다"
        detail = text
        if result == A.RESULT_INTERLOCK:
            miss = [c["label"] for c in self.start_checks() if not c["ok"]]
            detail = f"{text} — 빠진 조건: {' · '.join(miss) if miss else '인터락 확인'}"
        elif result == A.RESULT_RECIPE:
            detail = f"{text} — {await self._recipe_refusal(tbl)}"
        await push_log(f"공정 시작 거절 — {detail}", "err")
        return False, detail

    async def _plc_table_differs(self, tbl) -> str:
        """PLC 의 합계(D00028)·검증 통과(D00029)·표 머리 레시피 번호가 스냅샷과 같은지.
        다르면 이유 문구, 같으면 ''."""
        link = self.state.link
        try:
            head = await link.client.read_holding(A.D_RECIPE_SUM_PLC, 2)
            no = (await link.client.read_holding(A.D_RCP_NO, 1))[0]
        except Exception as e:  # noqa: BLE001
            return f"PLC 표를 확인하지 못했습니다 ({logger.clean(e, 120)})"
        if head[0] != (tbl["checksum"] & 0xFFFF) or head[1] != 1 or no != (tbl["number"] & 0xFFFF):
            return (f"PLC 레시피 표가 올린 것과 다릅니다 (합계 {head[0]:#06x}/{tbl['checksum']:#06x}, "
                    f"통과 {head[1]}, 번호 {no}/{tbl['number']}) — 시작하지 않았습니다")
        return ""

    async def _recipe_refusal(self, tbl) -> str:
        """시작 결과 3(표 검사 실패)의 원인 — 알람0 b13 과 D00028/D00029 로 나눈다.
        표 머리(개수·합계)가 틀리면 PLC 의 1 s 검사가 불합격(D00029 = 0)이거나 합계가 다르고,
        머리는 맞는데 첫 블록·그룹·스텝 항목이 틀리면 시작 때 적재에서 거절된다."""
        link = self.state.link
        try:
            head = await link.client.read_holding(A.D_RECIPE_SUM_PLC, 2)
            a0 = (await link.client.read_holding(A.D_ALARM0, 1))[0]
            # ★ 래더는 결과 3 을 그 스캔에 쓰고 b13 은 다음 스캔 P35 에서 공개한다 — 몇 스캔 더 본다
            for _ in range(3):
                if A.bit(a0, A.ALM0_RECIPE):
                    break
                await asyncio.sleep(0.1)
                a0 = (await link.client.read_holding(A.D_ALARM0, 1))[0]
        except Exception as e:  # noqa: BLE001
            return f"PLC 표 검사 불합격 (원인을 읽지 못했습니다: {logger.clean(e, 80)})"
        alarm = " · 알람 '레시피 표 검증 실패'" if A.bit(a0, A.ALM0_RECIPE) else ""
        if head[1] != 1 or head[0] != (tbl["checksum"] & 0xFFFF):
            return (f"PLC 표 머리(개수 · 합계) 검사 불합격 — 합계 PLC {head[0]:#06x} / "
                    f"올린 것 {tbl['checksum']:#06x}, 통과 {head[1]}{alarm}")
        return (f"PLC 가 첫 블록 · 그룹 · 스텝 항목을 받지 않았습니다(표 머리는 통과){alarm} — "
                f"블록 첫/끝 스텝 · 그룹 범위 · 반복 값을 확인하세요")

    def _wait_broken(self):
        """대기 중 그만둬야 하는 사유. 없으면 빈 문자열.
        ★ 입력으로도 본다 — 원격 · 판넬에서 벤트 · 펌핑 정지를 해도 대기가 살아 남지 않게."""
        link = self.state.link
        if not (link and link.connected):
            return "PLC 연결이 끊겼습니다"
        s = link.status
        if A.bit(s[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ):
            return "안전 정지 요구가 생겼습니다"
        i0 = s[A.D_INPUT0]
        if not A.bit(i0, A.IN0_PUMP_RUN):
            return "펌프 운전 입력이 꺼졌습니다"
        if not A.bit(i0, A.IN0_IVE_OPEN):
            return "IV-E 열림 입력이 꺼졌습니다(배기 격리 닫힘 · 벤트)"
        if A.bit(i0, A.IN0_ATM):
            return "챔버 대기압 입력이 켜졌습니다"
        if s[A.D_STATE] != A.STATE_IDLE:
            return f"장비 상태가 대기가 아닙니다({A.STATE_NAMES.get(s[A.D_STATE], s[A.D_STATE])})"
        crit = [a["name"] for a in self.state.alarms.list() if a["crit"]]
        if crit:
            return "중대 알람: " + " · ".join(crit)
        return ""

    def cancel_wait(self, why: str = "운전자"):
        if self.phase == BASE_WAIT:
            self._cancel_why = why
            self._cancel = True
            return True
        return False

    # ===================== 진행 · 종료 감지 =====================
    def tick(self, push_log_sync):
        """샘플링 루프가 부른다. 공정 중 → 아님 으로 바뀌면 종료를 기록한다."""
        st = self.state
        link = st.link
        if not (link and link.connected):
            # ★ 끊겨 있어도 b13 기한이 지나면 저장해 둔 끝 값으로 적는다(이벤트 로그 · 데이터 로그 메타가 같게)
            if self._b13_wait is not None and time.monotonic() >= self._b13_wait[0]:
                _d, (s0, at, pl) = self._b13_wait
                self._b13_wait = None
                self._finish(s0, at, pl)
            return
        s = link.status
        self._track_heaters(s)
        running = s[A.D_STATE] in RUNNING_STATES
        if (self._was_running or self.active_run) and not A.bit(s[A.D_ALARM0], A.ALM0_RECIPE):
            self._b13_cleared = True
        if self._b13_wait is not None:
            # 시퀀서 8 로 끝났는데 b13 이 아직 안 보였다 — 래더는 b13 을 한 스캔 늦게 공개한다(P35).
            # 1 s 안에 서면 레시피 표 오류, 아니면 그때의 끝 값으로 적는다(다음 공정이 시작돼도 바로 적는다)
            deadline, (s0, at, pl) = self._b13_wait
            b13 = self._b13_rose(s)
            if b13 or running or time.monotonic() >= deadline:
                self._b13_wait = None
                self._finish(self._with_b13(s0, s) if b13 else s0, at, pl)
            if not running:
                return
        if running:
            if self._abort_unknown and time.monotonic() - self._abort_unknown_at > ABORT_GRACE_S:
                # 결과를 못 받은 즉시 중단 뒤에도 공정이 계속 돈다 — 중단은 PLC 에 닿지 않았다
                self._abort_unknown = False
                push_log_sync("warn", "중단 명령이 PLC 에 닿지 않았습니다 — 공정 계속")
            self._watch_rf(s, push_log_sync)
            self._was_running = True
            if not self.started_at:
                self.started_at = time.time()
            # ★ 마지막 위치는 공정 중에 본 값으로 — 끝난 뒤의 상태 영역은 래더에서 정상 완료면
            #   '블록 N+1' 이 되고, 다른 끝에서도 다음 시작 전까지 남은 값일 뿐이다.
            if s[A.D_SEQ_BLOCK]:
                self._last_pos = (s[A.D_SEQ_BLOCK], s[A.D_SEQ_STEP],
                                  A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1]))
            return
        # ★ 시작 직후 '공정 중'을 한 번도 못 보고 끝난 경우(아주 짧은 공정 · 바로 중단)도 끝을 기록한다
        #   — 명령 1 직후의 '대기' 읽기(상태 영역은 최대 한 주기 늦다)는 1.5 s 동안 끝으로 보지 않는다.
        if (not self._was_running and self.active_run
                and time.monotonic() - self._run_started_mono > 1.5):
            self._was_running = True
        if self._was_running:
            self._was_running = False
            self.ended_at = time.time()
            self._watch_rf(None, push_log_sync)
            end = (list(s), self.ended_at, push_log_sync)
            self._end_seen_mono = time.monotonic()
            if self._abort_pending:
                # 즉시 중단 결과를 아직 모른다 — 결과가 오면(abort_result) 기록한다
                self._end_deferred = end
                return
            if self._needs_b13_wait(s):
                self._b13_wait = (self._end_seen_mono + B13_WAIT_S, end)
                return
            self._finish(*end)

    def _needs_b13_wait(self, s) -> bool:
        """시퀀서 8 · 안전 정지 아님 · 이번 공정의 b13 이 아직 안 보임 — 1 s 기다린다."""
        return (s[A.D_SEQ_STATE] == 8 and s[A.D_STATE] != A.STATE_SAFE_STOP
                and not self._b13_rose(s))

    def _b13_rose(self, s) -> bool:
        """이번 공정 중 0→1 로 바뀐 b13(시작 때 서 있었으면 그 뒤 0 을 본 다음에 선 것)."""
        return A.bit(s[A.D_ALARM0], A.ALM0_RECIPE) and (not self._b13_pre or self._b13_cleared)

    @staticmethod
    def _with_b13(s0, s):
        s0 = list(s0)
        s0[A.D_ALARM0] |= s[A.D_ALARM0] & (1 << A.ALM0_RECIPE)
        return s0

    def _flush_pending_end(self):
        """시작 흐름 첫머리 — 미뤄 둔 앞 공정의 끝을 지금 본 값으로 적고 앞 데이터 로그를 닫는다."""
        link = self.state.link
        cur = link.status if (link and link.connected) else None
        if self._b13_wait is not None:
            _d, (s0, at, pl) = self._b13_wait
            self._b13_wait = None
        elif self._end_deferred is not None:
            s0, at, pl = self._end_deferred
            self._end_deferred = None
            self._abort_pending = False
            self._abort_unknown = True              # 중단 결과를 못 받은 채 다음 시작
        else:
            return
        if cur is not None and self._b13_rose(cur):
            s0 = self._with_b13(s0, cur)
        self._finish(s0, at, pl)
        dl = self.state.datalog
        if dl is not None and dl.fp:
            dl.note_end(self.last_result)
            dl.close()
            dl._was_running = False                 # 새 공정이 시작 가장자리로 새 파일을 연다

    def _finish(self, s, ended_at, push_log_sync):
        took = ended_at - (self.started_at or ended_at)
        self.last_result = self.end_result(s)
        push_log_sync("ok" if result_level(self.last_result) == "ok" else "warn",
                      f"공정 {self.last_result} — {self.active_name or ''} · "
                      f"걸린 시간 {_hms(int(took * 1000))} · 마지막 위치 {self.where_text()}")
        self.started_at = 0.0
        self._abort_sent = False
        self._stop_reserved = False
        self._end_deferred = None
        self._abort_unknown = False
        self.active_run = False                 # 데이터 로그 구간 끝(끝 판정과 같은 순간)

    def _watch_rf(self, s, push_log_sync):
        """공정 중 RF 스텝(500 ms 이상)이 끝날 때까지 RF 출력(보조 b8)이 한 번도 안 켜지면 경고 한 줄.
        같은 블록 · 스텝은 공정마다 한 번만. s=None 이면 공정이 끝났다(마지막 스텝을 마감)."""
        if not DEV.HAS_RF:
            return
        cur = None
        if s is not None:
            rec = self.active_recipe or {}
            blocks = rec.get("blocks") or []
            bno, gstep = s[A.D_SEQ_BLOCK], s[A.D_SEQ_STEP]
            if 1 <= bno <= len(blocks) and s[A.D_SEQ_STATE] == 4:
                base = sum(len(b.get("steps") or []) for b in blocks[:bno - 1])
                steps = blocks[bno - 1].get("steps") or []
                k = gstep - base - 1
                if 0 <= k < len(steps):
                    st = steps[k]
                    if st.get("rf") and int(st.get("time_ms") or 0) >= 500 \
                            and float(blocks[bno - 1].get("rf_w") or 0) > 0:
                        cur = (bno, gstep)
        w = self._rf_watch
        if w is not None and (cur is None or cur[:2] != w["key"]
                              or A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1]) != w["cycle"]):
            if not w["on"] and w["key"] not in self._rf_warned:
                self._rf_warned.add(w["key"])
                push_log_sync("warn", f"RF 스텝에서 RF 가 한 번도 켜지지 않았습니다 — 블록 {w['key'][0]} · "
                                      f"스텝 {w['key'][1]} · RF 허가(인터락 b8) {w['ilk']} · CVG {w['cvg']} Torr")
            w = self._rf_watch = None
        if cur is not None:
            cyc = A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1])
            if w is None:
                w = self._rf_watch = {"key": cur, "cycle": cyc, "on": False, "ilk": 0, "cvg": "—"}
            w["on"] = w["on"] or A.bit(s[A.D_AUX_OUT], A.AUX_RF)
            w["ilk"] = int(A.bit(s[A.D_INTERLOCK], A.ILK_RF_OK))
            w["cvg"] = _torr(self.state.conv.cvg.to_torr(s[A.D_CVG_RAW])) if self.state.conv else "—"

    def where_text(self) -> str:
        if not self._last_pos:
            return "(공정 중 위치를 보지 못함)"
        b, st, c = self._last_pos
        return f"블록 {b} · 스텝 {st} · 사이클 {c}"

    def _block_count(self) -> int:
        tbl = self.active_table or {}
        if tbl.get("block_count"):
            return int(tbl["block_count"])
        rec = self.active_recipe or {}
        return len(rec.get("blocks") or [])

    def end_result(self, s) -> str:
        """끝났을 때의 결과(이벤트 로그·데이터 로그 메타·목록이 이 값을 쓴다). 래더 동작 기준, 앞이 우선:

        1. 정상 완료 — 시퀀서 6 이고 D00021 > 블록 수(래더는 SEQ_BLOCK 을 블록 수 + 1 로 올린 뒤 끝낸다).
           장비 상태 6 이어도 완료를 먼저 본다(끝을 보기 전 끊김으로 PC 트립 등) → '정상 종료 (끝난 뒤 안전 정지: …)'
        2. 안전 정지(장비 상태 6) — 운전자 중단보다 앞선다(중단을 누른 순간 비상정지가 났다면 원인은 비상정지)
        3. 사이클 후 정지 — 시퀀서 6 이고 D00021 ≤ 블록 수. 마지막 블록의 마지막 사이클(그 블록이 그룹 안이면
           마지막 그룹 회차)이면 다 끝난 것 → '정상 종료 (사이클 후 정지와 겹침)'
        4. 레시피 표 오류 — 시퀀서 8 AND 이번 공정 중 선 알람0 b13(중대 아님 — 장비 상태 1). 시작 때부터 서 있던
           b13 이면 스냅샷 표로 그 블록 · 스텝 · 그룹이 정말 적재에서 걸리는지 보고 정한다. 운전자 중단보다 앞선다
        5. 운전자 중단 — 명령 5 결과 0. 결과를 못 받았으면 '결과 확인 안 됨'
        6. PLC 재시작 — 끝 스냅샷이 시퀀서 0 · 블록 0(P00 첫 스캔이 지웠다). 원인이 남은 알람(장비 상태 6)은 뒤따름.
           (공정 중 하트비트 멈춤은 보조 근거 — 그것만으로는 재시작이라 하지 않는다)
        7. 시퀀서 8 — PLC 중단 / 그 밖 — 끝 확인 안 됨"""
        seq, st = s[A.D_SEQ_STATE], s[A.D_STATE]
        nb = self._block_count()
        blk = s[A.D_SEQ_BLOCK]
        if seq == 6 and (not nb or blk > nb):
            if st == A.STATE_SAFE_STOP:
                return f"정상 종료 (끝난 뒤 안전 정지: {self._crit_text(s)})"
            return "정상 종료"
        if seq == 0 and blk == 0:
            follow = f" · 뒤따름: {self._crit_text(s)}" if st == A.STATE_SAFE_STOP else ""
            return f"중단 (PLC 재시작 — 시퀀서 · 출력 초기화{follow})"
        if st == A.STATE_SAFE_STOP:
            return f"중단 (안전 정지 — {self._crit_text(s)})"
        if seq == 6:
            # ★ 끝 스냅샷의 D00024 — 래더는 끝에서 사이클을 올리지 않는다(P40 행 55~56 · 66)
            cyc = A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1])
            blocks = (self.active_recipe or {}).get("blocks") or []
            reps = int(blocks[blk - 1].get("repeat") or 1) if 1 <= blk <= len(blocks) else 0
            if self._last_cycle_of_all(blk, cyc, reps, s):
                return "정상 종료 (사이클 후 정지와 겹침)"
            rep = f"/{reps}" if reps else ""
            return f"사이클 후 정지 (블록 {blk} · 사이클 {cyc}{rep})"
        if seq == 8 and A.bit(s[A.D_ALARM0], A.ALM0_RECIPE):
            why = self._table_fault(blk, s[A.D_SEQ_STEP])
            if self._b13_rose(s) or why:
                return f"중단 (레시피 표 오류 — {why or f'블록 {blk} 적재 거절'})"
        if self._abort_sent:
            return "중단 (운전자 중단)"
        if self._abort_unknown:
            return "중단 (운전자 중단 — 결과 확인 안 됨)"
        if seq == 8:
            return "중단 (PLC 중단)"
        return "중단 (끝 확인 안 됨)"

    def _crit_text(self, s) -> str:
        """켜진 중대 알람 이름. ★ Powder: 래더는 공정 중 안전 정지면 O3 허가 알람(알람1 b3)도 함께 세운다
        (P30 행 13 + P35 행 26) — O3 발생기 알람 입력이 꺼져 있고 다른 중대 알람이 있으면 b3 는 원인이
        아니라 '뒤따름'으로 적는다."""
        crit = [a for a in self.state.alarms.list() if a["crit"]]
        if not crit:
            return "중대 알람 이름 없음"
        if DEV.HAS_O3 and len(crit) > 1 and not A.bit(s[A.D_INPUT1], A.IN1_O3_ALM):
            b3 = f"A1-{A.ALM1_O3_GEN:02d}"
            follow = [a["name"] for a in crit if a["code"] == b3]
            cause = [a["name"] for a in crit if a["code"] != b3]
            if follow:
                return " · ".join(cause) + " · 뒤따름: " + follow[0]
        return " · ".join(a["name"] for a in crit)

    def _last_cycle_of_all(self, blk, cyc, reps, s) -> bool:
        """사이클 후 정지가 마지막 블록의 마지막 사이클(그 블록이 그룹 안이면 마지막 그룹 회차)에 겹쳤나."""
        nb = self._block_count()
        if not (nb and blk == nb and reps and cyc >= reps):
            return False
        for g in (self.active_recipe or {}).get("groups") or []:
            try:
                a, z, r = int(g.get("from_block")), int(g.get("to_block")), int(g.get("repeat") or 1)
            except (TypeError, ValueError, AttributeError):
                continue
            if a <= blk <= z and s[A.D_SEQ_GROUP_PASS] < r:
                return False
        return True

    def _table_fault(self, blk: int, step: int = 0) -> str:
        """레시피 표 오류의 위치 · 이유 — 시작 스냅샷 표를 래더 적재 검사처럼 본다(부호 있는 16비트).
        블록 항목 → 그 스텝 시간(P40 행 116~117, 20 ~ 3,276,700 ms) → 그룹 순. 걸리는 것이 없으면 "".
        래더는 그룹 진입이 거절돼도 다음 블록을 적재한 뒤 끝내므로 D00021 은 그 다음 블록이다."""
        words = (self.active_table or {}).get("words")
        if not words:
            return ""

        def w(addr):
            i = addr - A.RCP_SUM_BASE
            return words[i] & 0xFFFF if 0 <= i < len(words) else 0

        def ws(addr):
            return A.to_signed16(w(addr))
        ns, nb, ng = w(A.D_RCP_STEP_COUNT), w(A.D_RCP_BLOCK_COUNT), w(A.D_RCP_GROUP_COUNT)
        if 1 <= blk <= nb:
            base = A.D_RCP_BLOCK_BASE + (blk - 1) * A.RCP_BLOCK_STRIDE
            first, last = ws(base + A.RCP_BLOCK_FIRST), ws(base + A.RCP_BLOCK_LAST)
            rep = A.dword(w(base + A.RCP_BLOCK_REPEAT_LO), w(base + A.RCP_BLOCK_REPEAT_LO + 1))
            why = ("첫 스텝이 1 보다 작음" if first < 1 else "끝 스텝 < 첫 스텝" if last < first
                   else f"끝 스텝 {last} > 스텝 수 {ns}" if last > ns else "반복 < 1" if rep < 1 else "")
            if why:
                return f"블록 {blk} 적재 거절: {why}"
            if first <= step <= last and 1 <= step <= ns:
                sb = A.D_RCP_STEP_BASE + (step - 1) * A.RCP_STEP_STRIDE
                t = A.dword(w(sb + A.RCP_STEP_TIME_LO), w(sb + A.RCP_STEP_TIME_LO + 1))
                if t >= 1 << 31:
                    t -= 1 << 32                # 부호 있는 32 비트(래더 비교와 같게)
                if t < 20 or t > 3_276_700:
                    return f"블록 {blk} · 스텝 {step} 적재 거절: 시간 {t} ms (20 ~ 3,276,700 ms 밖)"
        for g in range(1, min(ng, A.RCP_GROUP_MAX) + 1):
            b = A.D_RCP_GROUP_BASE + (g - 1) * A.RCP_GROUP_STRIDE
            a, z, r = ws(b), ws(b + 1), ws(b + 2)
            why = ("반복 < 1(부호 있는 16비트)" if r < 1 else "범위가 올바르지 않음" if a < 1 or z < a or z > nb
                   else "")
            if why:
                return f"그룹 {g} 진입 거절: {why} — 블록 {blk} 적재 뒤 중단"
        return ""

    # ---- 즉시 중단 · 사이클 후 정지 (명령 결과를 보고 표시한다) ----
    def abort_begin(self):
        """즉시 중단을 보내기 직전(보낼 때 공정 중인 것을 확인한 뒤)."""
        self._abort_pending = True
        self._abort_gen = self._gen

    def abort_result(self, done: bool, unknown: bool = False):
        """즉시 중단 결과. 처리됨(0)일 때만 '운전자 중단'으로 적는다 — 거절이면 적지 않는다.
        unknown: 보냈지만 결과를 못 받았다(응답 없음 · 보낸 뒤 통신 오류) → '운전자 중단 — 결과 확인 안 됨'."""
        if self._abort_gen != self._gen:
            return                              # 그사이 다음 공정이 시작됐다 — 앞 끝은 이미 적었다
        self._abort_pending = False
        if done:
            self._abort_sent = True
        elif unknown:
            self._abort_unknown = True
            self._abort_unknown_at = time.monotonic()
        if self._end_deferred:
            end = self._end_deferred
            self._end_deferred = None
            # ★ 같은 b13 규칙 — 끝을 본 뒤 1 s 창 안이면 b13 을 기다린다(래더에서 중단 명령은 늘 결과 0 이라
            #   b13 이 운전자 중단보다 앞선다)
            if self._needs_b13_wait(end[0]) and time.monotonic() < self._end_seen_mono + B13_WAIT_S:
                self._b13_wait = (self._end_seen_mono + B13_WAIT_S, end)
            else:
                # 창이 지났어도 그사이 선 b13 은 합친다(_flush_pending_end 와 같게)
                link = self.state.link
                s0 = end[0]
                if link is not None and link.connected and self._b13_rose(link.status):
                    s0 = self._with_b13(s0, link.status)
                self._finish(s0, end[1], end[2])

    def note_stop_after_cycle(self):
        """사이클 후 정지가 처리됨 — 남은 시간은 이번 사이클만(일시정지 중 예약 포함)."""
        self._stop_reserved = True

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
            # 공정 중이면 시작 때 스냅샷, 아니면 지금 고른 레시피
            "recipe": self.active_name if running else self.recipe_name,
            "number": ((self.active_table if running else self.table) or {}).get("number"),
            "block": s[A.D_SEQ_BLOCK],
            "step": s[A.D_SEQ_STEP],
            "cycle": cycle,
            "group_pass": s[A.D_SEQ_GROUP_PASS],
            "step_ms": step_ms,
            "paused": code == A.STATE_PAUSE,
            "prep": s[A.D_SEQ_STATE] == 3,
            "last_result": self.last_result,
            "stop_reserved": running and (code == A.STATE_STOPPING or self._stop_reserved),
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
        rec = self.active_recipe
        if rec and running:
            pos = {"block": s[A.D_SEQ_BLOCK], "step": s[A.D_SEQ_STEP], "cycle": cycle,
                   "group_pass": s[A.D_SEQ_GROUP_PASS], "step_elapsed_ms": step_ms,
                   "paused": out["paused"],
                   "stop_after_cycle": code == A.STATE_STOPPING or self._stop_reserved}
            out["remaining_ms"] = R.remaining_ms(st.cfg, rec, pos)
            out["total_ms"] = R.total_ms(st.cfg, rec)
            out["eta"] = time.strftime("%H:%M:%S",
                                       time.localtime(time.time() + out["remaining_ms"] / 1000))
            blocks = rec.get("blocks") or []
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
            g, gi = R._group_of(rec, bno)
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
        # ★ v0.4.12: b13 은 되읽기 전 상태로 — 되읽는 동안 선 b13 은 '이번 공정 중 선 것'이다
        b13_pre = A.bit(link.status[A.D_ALARM0], A.ALM0_RECIPE)
        words = None
        for i in range(ADOPT_READ_TRIES):             # 되읽기가 실패하면 다시(한 번 놓쳤다고 이어받기를 버리지 않는다)
            if i:
                await asyncio.sleep(ADOPT_READ_GAP_S)
            if not link.connected:
                return
            words = await link.read_recipe_area()
            if words:
                break
        if not words:
            await push_log(f"PLC 가 공정 중인데 레시피 표를 {ADOPT_READ_TRIES}번 읽지 못했습니다 — 이어받지 않습니다", "warn")
            return
        # ★ 되읽는 동안 공정이 끝났거나 다른 레시피가 열렸으면 이어받지 않는다
        if not (link.connected and link.status[A.D_STATE] in RUNNING_STATES) or self.recipe:
            return
        info = R.from_plc_words(words)
        name = storage.find_by_number(info["number"])
        # 끝 판정의 표 근거는 PLC 에 실제로 올라가 있는 표(되읽은 것)
        if name:
            self.select(name)
            self.run = {"name": name, "recipe": self.recipe, "table": dict(self.table or {}, words=list(words))}
            await push_log(f"PLC 가 이미 공정 중입니다 — 레시피 [{name}] "
                           f"(번호 {info['number']})로 이어 갑니다", "warn")
        else:
            self.recipe_name = f"(PLC 번호 {info['number']})"
            self.table = {"number": info["number"], "checksum": info["checksum"]}
            self.run = {"name": self.recipe_name, "recipe": None, "table": dict(self.table, words=list(words))}
            await push_log(f"PLC 가 이미 공정 중입니다 — 번호 {info['number']} 에 맞는 "
                           f"로컬 레시피가 없어 이름 없이 표시합니다", "warn")
        self._was_running = True
        self.started_at = time.time()
        self.active_run = True                  # 이어받은 공정도 데이터 로그 구간
        self._run_started_mono = time.monotonic()
        # ★ b13 은 리셋 전까지 남는다 — 이어받는 순간 이미 서 있었으면 '이번 공정 중 선 것'이 아니다
        self._gen += 1
        self._b13_pre = b13_pre
        self._b13_cleared = False
        self._abort_sent = self._abort_pending = self._abort_unknown = False
        self._end_deferred = self._b13_wait = None


ADOPT_READ_TRIES = 3     # 이어받을 때 PLC 레시피 표 되읽기 시도 횟수
ADOPT_READ_GAP_S = 0.5   # 그 사이 간격
B13_WAIT_S = 1.0        # 시퀀서 8 로 끝났을 때 레시피 표 오류(b13)가 공개되기를 기다리는 시간
ABORT_GRACE_S = 1.0     # 결과를 못 받은 즉시 중단 뒤 이만큼 지나도 공정 중이면 중단이 닿지 않은 것


def result_level(result: str) -> str:
    """끝 결과의 수준 — 이벤트 로그와 데이터 로그 목록 색이 같은 규칙을 쓴다(ok · warn · off)."""
    from .logview import result_level as _lv
    return _lv(result)


# ===================== 작은 도우미 =====================
def _tc_ok(s, h) -> bool:
    """그 채널 온도조절기 국번의 통신 정상 비트(D00054)."""
    station = int(h.get("station") or ((int(h["ch"]) - 1) // 4 + 1))
    return A.bit(s[A.D_TC_COMM], station - 1)


def _uses_rf(recipe) -> bool:
    """RF 스텝(rf 켬)이 있고 그 블록 RF 전력이 0 보다 큰가(PEALD)."""
    if not (DEV.HAS_RF and isinstance(recipe, dict)):
        return False
    for b in recipe.get("blocks") or []:
        try:
            if isinstance(b, dict) and float(b.get("rf_w") or 0) > 0 and \
                    any(isinstance(st, dict) and st.get("rf") for st in b.get("steps") or []):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _uses_o3(recipe) -> bool:
    """O3 설정이 0 보다 큰 블록이 있는가(Powder)."""
    if not (DEV.HAS_O3 and isinstance(recipe, dict)):
        return False
    for b in recipe.get("blocks") or []:
        try:
            if isinstance(b, dict) and float(b.get("o3") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _torr(v) -> str:
    """화면 fmt.torr 와 같은 규칙 — 1 이상 소수 2자리, 0.1 이상 3자리, 그 미만 지수, 0 은 '0'."""
    if v is None:
        return "—"
    v = float(v)
    if v == 0:
        return "0"
    if v >= 1:
        return f"{v:.2f}"
    if v >= 0.1:
        return f"{v:.3f}"
    m, e = f"{v:.1e}".split("e")
    return f"{m}E{'+' if int(e) >= 0 else '-'}{abs(int(e))}"


def _temp(raw) -> float:
    v = int(raw) & 0xFFFF
    return (v - 0x10000 if v & 0x8000 else v) / 10.0


def _hms(ms) -> str:
    s = max(0, int((ms or 0) / 1000))
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
