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
        self._stopping_seen = False     # 이번 공정 중 장비 상태 '사이클 후 정지 예약'(STATE_STOPPING)을 봤다(v0.4.14)
        self._adopted = False           # 이번 공정은 이어받은 것(끝 줄의 걸린 시간 = 이어받은 뒤)
        self._last_pos = None           # 공정 중 마지막으로 본 (블록, 스텝, 사이클, 그룹 회차)
        self._last_seq = None           # 공정 중 마지막으로 본 시퀀서 상태(3 · 4 · 7) — 정비 카운터(v0.7.1 M3)
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
        # ★ v0.7.0(P3 · P7): PC ↔ PLC 링크가 공정 구간 중 끊긴 시각(time.time · monotonic). 다시 붙은 첫 상태가 공정
        #   아님이면 '끊긴 동안 끝남', 공정 중이면 '돌아왔습니다' 알림
        self._lost_at = None
        self._lost_mono = 0.0
        self._ended_while_lost = False
        self.pending_toasts = []        # (수준, 글) — event_loop 가 모든 화면에 토스트로 보낸다
        # ★ v0.7.0(A2): 실행 정보(작업자 · 시료 · 메모) — 시작 확인 창에서 받은 것(다음 시작까지 남는다)
        self.run_info = {}
        # ★ v0.7.0(A5): 공정 중 압력 상한 · 히터 이탈 경고(PC 감시)
        self._press_since = None        # 압력 상한을 넘은 채 마지막으로 본 때(일시정지면 None — 시간이 흐르지 않는다)
        self._press_acc = 0.0           # 넘은 채 이어진 시간(일시정지 동안은 더하지 않는다 — v0.7.1 W2)
        self._press_warned = False
        self._press_abort_sent = False
        self._press_task = None         # 띄운 즉시 중단 태스크(v0.7.1 W3 — 참조를 잡아 둔다)
        self._abort_why = ""            # 즉시 중단 이유(압력 상한 — PC 가 즉시 중단) — 끝 결과 문구
        self._hdev_since = {}
        self._hdev_warned = set()
        self.watch_notices = []         # 지금 이어지는 경고(Program Notices)
        self.offline_checked = False    # v0.7.0(P6): 켜진 뒤 꺼진 동안 끝난 공정을 판정해 봤나(실행마다 한 번)
        self.start_sent = False         # v0.7.1(P2): 이 실행에서 공정 시작 명령을 보낸 적이 있나
        self.adopt_count = 0            # 이 실행에서 이어받은 횟수
        self.may_resume = False         # 이번 이어받기가 같은 데이터 로그 파일을 다시 열어도 되나

    # ===================== 시작 조건 =====================
    def start_checks(self) -> list:
        """[{key, label, ok, detail}] — 항목마다 현재 값과 이유를 함께 준다."""
        st = self.state
        link = st.link
        cfg = st.cfg
        out = []

        conn = bool(link and link.connected)
        cfg_err = getattr(link, "config_error", "") if link else ""
        out.append({"key": "plc", "label": "PLC Link · Heartbeat", "ok": conn and link.plc_hb_ok,
                    "detail": NO_ADDR_TEXT if cfg_err else
                    (link.addr_text if link else "") + ("" if conn else " — 연결 안 됨")})
        if not conn:
            return out
        # 장비 ID — 막혔으면(다른 장비·ID 없음) 연결 안 됨 때처럼 여기서 끝낸다
        from .plclink import id_text
        blocked = link.id_block_text() if hasattr(link, "id_block_text") else ""
        out.append({"key": "device_id", "label": "Device ID", "ok": not blocked,
                    "short": id_text(getattr(link, 'device_id', None)),     # v0.8.1: 시작 조건 자리 글
                    "detail": f"읽은 ID {id_text(getattr(link, 'device_id', None))} / "
                              f"이 장비 {id_text(DEV.DEVICE_ID)}" + (f" — {blocked}" if blocked else "")})
        if blocked:
            return out

        s = link.status
        code = s[A.D_STATE]
        out.append({"key": "state", "label": "Device State", "ok": code not in RUNNING_STATES
                    and code != A.STATE_SAFE_STOP,
                    "detail": A.STATE_NAMES.get(code, str(code))})

        crit = [a["name"] for a in st.alarms.list() if a["crit"]]
        out.append({"key": "alarm", "label": "No Critical Alarm", "ok": not crit,
                    "detail": " · ".join(crit) if crit else "없음"})

        ilk = s[A.D_INTERLOCK]
        out.append({"key": "pump", "label": "Pump", "ok": A.bit(ilk, A.ILK_PUMP),
                    "detail": "운전 중" if A.bit(ilk, A.ILK_PUMP) else "펌프 운전·알람을 확인하세요"})
        out.append({"key": "valve_ok", "label": "Valve Permit",
                    "ok": A.bit(ilk, A.ILK_VALVE_OK),
                    "detail": self._valve_ok_detail(s)})

        conv = st.conv
        cur = conv.cvg.to_torr(s[A.D_CVG_RAW])
        target = (cfg.get("params") or {}).get("base_press_torr")
        # 화면은 cur · target 숫자로 게이지와 같은 규칙(fmt.torr)으로 다시 쓴다(detail 은 로그용)
        out.append({"key": "base", "label": "Base Pressure", "ok": A.bit(ilk, A.ILK_VACUUM),
                    "detail": f"현재 {_torr(cur)} / 목표 {_torr(target)} Torr",
                    "cur": cur, "target": target})

        if DEV.HAS_O3:
            ok = A.bit(ilk, A.ILK_O3_OK)
            out.append({"key": "o3", "label": "O3 Permit", "ok": ok,
                        "detail": "준비됨" if ok else "O3 라인을 먼저 켜세요",
                        "action": None if ok else "o3_on"})
            # ★ 래더의 공정 시작 조건은 O3 허가(바이패스 펌프·IV-B·5 s·알람 없음·O3 한계 > 0)만 보고
            #   발생기 요청은 보지 않는다 — 발생기만 꺼진 채 시작하면 O3 없이 돈다. O3 를 쓰는
            #   레시피면 발생기 출력(보조 출력 b9)을 시작 조건에 넣는다.
            if _uses_o3(self.recipe):
                gen = A.bit(s[A.D_AUX_OUT], A.AUX_O3_GEN)
                out.append({"key": "o3_gen", "label": "O3 Generator ON", "ok": gen,
                            "detail": "켜짐" if gen else "레시피가 O3 를 씁니다 — [O3 라인 켜기]로 발생기를 켜세요",
                            "action": None if gen else "o3_on"})

        from .leakcheck import leak
        if leak.running:
            # ★ v0.7.1(L7): 점검 중에는 시작 조건에 넣어 Start 단추를 막는다(시작 창을 다 채운 뒤 거절되지 않게)
            out.append({"key": "leakcheck", "label": "Leak Check 진행 중", "ok": False,
                        "detail": "누설률 점검이 끝나거나 취소한 뒤에 시작하세요"})
        res = st.recipe_check or {}
        out.append({"key": "recipe", "label": "Recipe Check",
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
            out.append({"key": "heater", "label": f"Heater Stable (±{band:g} ℃ · {need:g} s)", "ok": not bad,
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
            out.append({"key": "rf", "label": "RF Ready", "ok": not miss,
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
        """시작 조건 'Valve Permit' 글 — v0.8.0: valve_permit_missing 하나로(순서 · 문구는 예전 그대로: Pump 는 시작 조건에
        따로 칸이 있어 빼고, 기본 인터락은 맨 뒤)."""
        full = valve_permit_missing(s)
        if not full:
            return "허가됨"
        miss = [m for m in full if m not in ("Pump 운전", "PLC 허가 조건 확인")]
        if "기본 인터락" in miss:
            miss = [m for m in miss if m != "기본 인터락"] + ["기본 인터락"]
        if miss:
            return "필요: " + " · ".join(miss)
        # ★ v0.8.1: 허가가 꺼졌는데 빠진 것이 Pump 뿐(펌프 알람으로 펌프만 멈추고 IV-E 가 아직 열린 순간 등)이면
        #   '허가됨' 이 아니라 그것을, 아무 조건도 못 찾으면 PLC 쪽 확인을 적는다
        return "필요: Pump 운전" if "Pump 운전" in full else "필요: PLC 허가 조건 확인"

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
    def set_run_info(self, info):
        """v0.7.0(A2): 작업자 · 시료 · 메모(정리한 것) — 다음 시작까지 남는다."""
        self.run_info = clean_run_info(info)

    def begin(self, push_log, push_notice, notify=None, info=None):
        """시작 흐름을 러너가 쥐는 백그라운드 태스크로 띄우고 바로 돌아온다.
        ★ 명령 통로를 붙잡지 않는다 — 베이스 압력 대기(최대 30 분) 동안에도 대기 취소·알람 확인·
          벤트·펌핑 정지·전체 닫기가 바로 처리돼야 한다. (성공, 메시지)"""
        if self.busy:
            return False, "이미 시작 절차가 진행 중입니다"
        if not self.recipe:
            return False, "레시피를 고르세요"
        if info is not None:
            self.set_run_info(info)

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
        # ★ v0.4.14: 앞 공정이 아직 이 PC 의 공정 구간(active_run)인데 PLC 는 이미 공정 중이 아니면 — 중단 직후
        #   (0~0.1 s) 새 시작 — 끝 판정(tick)을 먼저 한다. 아래에서 _abort_sent · _last_pos 를 지우면 앞 공정이
        #   '중단 (PLC 중단)' · '(공정 중 위치를 보지 못함)' 으로 적혔다
        pending_logs = []
        ended_now = False
        link = st.link
        if (self.active_run and link and link.connected
                and link.status[A.D_STATE] not in RUNNING_STATES):
            self.tick(lambda lv, m: pending_logs.append((lv, m)))
            ended_now = not self.active_run and self._b13_wait is None and self._end_deferred is None
        # ★ 미뤄 둔 앞 공정의 끝(b13 대기 · 중단 결과 대기)을 지금 본 값으로 먼저 적고 앞 데이터 로그를 닫는다 —
        #   아래에서 상태를 지우면 앞 끝이 사라지거나 틀리게 적힌다
        if not self._flush_pending_end(lambda lv, m: pending_logs.append((lv, m))) and ended_now:
            self._close_datalog()
        for lv, m in pending_logs:
            await push_log(m, lv)
        self._gen += 1
        self._cancel_why = "운전자"
        self._cancel = False
        self._abort_sent = False
        self._abort_pending = False
        self._end_deferred = None
        self._stop_reserved = False
        self._stopping_seen = False
        self._adopted = False
        self._last_pos = None
        self._last_seq = None
        self.last_result = ""
        self._b13_wait = None
        self._abort_unknown = False
        self._abort_why = ""                # v0.7.1(W3)
        self._press_abort_sent = False

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
        self.start_sent = True              # v0.7.1(P2): 이 실행에서 시작 명령을 보냈다(이어받기 같은 파일 조건)
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
                           f"예상 {_hms(R.total_ms(st.cfg, rec))}){run_info_text(self.run_info)}", "ok")
            return True, "공정을 시작했습니다"
        if result is None:
            if "응답 없음" in text or "받았을 수" in text:
                # ★ v0.4.14: 보냈지만 응답을 못 받았다 — 거절이 아니다. PLC 가 시작했으면 이어받기 루프가 몇 초 안에
                #   이어 간다(즉시 중단 명령과 같은 판단 — commands.py)
                why = f"시작 결과 모름 — PLC 상태로 확인합니다 ({text})"
                await push_log(why, "warn")
            else:
                # ★ v0.4.15: 보내지도 못했다(막힌 이유 · 코드 쓰기 전 오류) — '결과 모름' 이 아니다
                why = f"{START_NOT_SENT} — {text}"
                await push_log(why, "err")
            return False, why
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
        alarm = " · 알람 'Recipe Table Check Failed'" if A.bit(a0, A.ALM0_RECIPE) else ""
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
            if (self.active_run or self._was_running) and self._lost_at is None:
                self._lost_at, self._lost_mono = time.time(), time.monotonic()     # v0.7.0(P3 · P7)
            self._reset_watch()
            return
        s = link.status
        self._track_heaters(s)
        running = s[A.D_STATE] in RUNNING_STATES
        if self._lost_at is not None:
            self._link_back(s, running)
        if not self.active_run and self._b13_wait is None and self._id_blocked():
            # ★ v0.4.15: 장비 ID 가 막힌 연결(다른 장비의 PLC)이고 이 PC 가 맡은 공정이 없다 — 그 공정의 끝을 이 장비
            #   이벤트 로그에 적거나(골라 둔 레시피 이름으로) RF 감시를 하지 않는다. '공정 중을 봤다'도 세우지 않는다
            self._was_running = False
            return
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
            if self.active_run:
                self._watch_limits(s, push_log_sync)
            self._was_running = True
            if s[A.D_STATE] == A.STATE_STOPPING and (self.active_run or self._was_running):
                self._stopping_seen = True
            if not self.started_at:
                self.started_at = time.time()
            # ★ 마지막 위치는 공정 중에 본 값으로 — 끝난 뒤의 상태 영역은 래더에서 정상 완료면
            #   '블록 N+1' 이 되고, 다른 끝에서도 다음 시작 전까지 남은 값일 뿐이다.
            if s[A.D_SEQ_BLOCK]:
                # ★ v0.7.1(M1): 그룹 회차도 — PLC 재시작으로 끝나면 끝 스냅샷의 회차는 0 이다
                self._last_pos = (s[A.D_SEQ_BLOCK], s[A.D_SEQ_STEP],
                                  A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1]), s[A.D_SEQ_GROUP_PASS])
                if s[A.D_SEQ_STATE] in (3, 4, 7):
                    self._last_seq = s[A.D_SEQ_STATE]       # v0.7.1(M3): 블록 준비(3)였으면 그 블록은 세지 않는다
            return
        self._reset_watch()
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

    # ---- v0.7.0(P3 · P7): 공정 구간 중 끊겼던 링크가 다시 붙었다 ----
    def _link_back(self, s, running: bool):
        """다시 붙은 첫 상태. 공정 아님(끊긴 동안 끝남)이면 끝 결과에 ' · PC 끊긴 동안 끝남'(걸린 시간은 다시 연결된
        때까지), 공정 중이고 끊긴 시간이 PC Heartbeat Timeout 이상이었거나 알람0 b6 이 서 있으면 토스트 한 번.
        ★ PC 가 스스로 알람 리셋을 보내지 않는다 — 트립을 풀지는 운전자가 정한다(Alarm Reset)."""
        lost_s = time.monotonic() - self._lost_mono
        self._lost_at = None
        if self._id_blocked():
            return
        if self.active_run and not running:
            # ★ v0.7.1(P4): 끊기기 전에 이미 끝을 봤으면(b13 대기 · 미뤄 둔 끝) '끊긴 동안 끝남' 이 아니다
            if self._b13_wait is None and self._end_deferred is None:
                self._ended_while_lost = True
            return
        if running and self.active_run:
            link = self.state.link
            wdt = link._wdt_s() if hasattr(link, "_wdt_s") else A.PRM_DEFAULTS["pc_wdt_ms"] / 1000.0
            if lost_s >= wdt or A.bit(s[A.D_ALARM0], A.ALM0_PC_LINK):
                self.pending_toasts.append(("warn", f"PC 연결이 돌아왔습니다 — 끊긴 {lost_s:.0f} s 동안 공정은 "
                                                    "계속되었습니다"))

    # ---- v0.7.0(A5): 공정 중 압력 상한 · 히터 이탈 경고(PC 감시) ----
    def _reset_watch(self):
        self._press_since = None
        self._press_acc = 0.0
        self._press_warned = False
        self._hdev_since = {}
        if self._hdev_warned:
            self._hdev_warned = set()
        self.watch_notices = []

    def _watch_limits(self, s, push_log_sync):
        """공정 중(일시정지 제외) CVG 가 process.press_high_torr 를 넘은 채 press_high_s 이어지면 경고(이벤트 로그 ·
        토스트 한 번, 이어지는 동안 Program Notices). press_high_abort 면 즉시 중단을 한 번 보낸다.
        사용 채널(통신 정상)의 |PV − SV| 가 heater_dev_c 를 넘은 채 heater_dev_s 이어지면 채널별 경고 한 번,
        돌아오면 해제 로그. ★ PC 감시다 — PC 가 꺼져 있으면 동작하지 않는다(PLC 래더 감시가 아니다)."""
        pr = self.state.cfg.get("process") or {}
        now = time.monotonic()
        notes = []
        paused = s[A.D_STATE] == A.STATE_PAUSE
        lim = _num(pr.get("press_high_torr"), 10.0)
        hold = _num(pr.get("press_high_s"), 5.0)
        cvg = self.state.conv.cvg.to_torr(s[A.D_CVG_RAW]) if self.state.conv else None
        if paused and lim > 0:
            # ★ v0.7.1(W2): 일시정지는 타이머를 멈추기만 한다 — '해제' 로 끝내지 않는다(재개 뒤 다시 경고 · 토스트 없음)
            self._press_since = None
            if self._press_warned:
                notes.append(("warn", "공정 중 압력 상한 — 일시정지 중(재개하면 다시 본다) · PC 감시(process.press_high_torr)"))
        elif lim > 0 and cvg is not None and cvg > lim:
            if self._press_since is not None:
                self._press_acc += max(0.0, now - self._press_since)
            self._press_since = now
            if self._press_acc >= hold:
                msg = f"공정 중 압력 상한 — CVG {_torr(cvg)} Torr > {lim:g} Torr ({hold:g} s 넘게)"
                notes.append(("warn", msg + " · PC 감시(process.press_high_torr)"))
                if not self._press_warned:
                    self._press_warned = True
                    push_log_sync("warn", msg)
                    self.pending_toasts.append(("warn", msg))
                    if pr.get("press_high_abort") and not self._press_abort_sent:
                        self._press_abort_sent = True
                        push_log_sync("warn", "압력 상한 — PC 가 즉시 중단을 보냅니다(process.press_high_abort)")
                        self._press_task = self._spawn(self._press_abort(push_log_sync))
        else:
            if self._press_warned:
                push_log_sync("info", "공정 중 압력 상한 해제")
            self._press_since = None
            self._press_acc = 0.0
            self._press_warned = False
        dev = _num(pr.get("heater_dev_c"), 15.0)
        dhold = _num(pr.get("heater_dev_s"), 120.0)
        link = self.state.link
        for h in (self.state.cfg.get("heaters") or []) if dev > 0 else []:
            if not h.get("enabled"):
                continue
            ch = h["ch"]
            sv_raw = link.cmd_reg(A.D_HEATER_SV + ch - 1) if hasattr(link, "cmd_reg") else None
            power = link.cmd_reg(A.D_HEATER_POWER) if hasattr(link, "cmd_reg") else None
            # ★ v0.7.1(W1): 전원이 켜진 채널(D01010 의 그 비트)만 본다 — 사용으로만 두고 전원을 끈 채널은 보지 않는다
            if not _tc_ok(s, h) or sv_raw is None or not (power is not None and (power >> (ch - 1)) & 1):
                self._hdev_since.pop(ch, None)
                continue
            pv, sv = _temp(s[A.D_HEATER_PV + ch - 1]), _temp(sv_raw)
            if abs(pv - sv) > dev:
                self._hdev_since.setdefault(ch, now)
                if now - self._hdev_since[ch] >= dhold and ch not in self._hdev_warned:
                    self._hdev_warned.add(ch)
                    msg = (f"히터 이탈 — CH{ch} {h.get('name', '')} 현재 {pv:.1f} ℃ / 설정 {sv:.1f} ℃ "
                           f"(±{dev:g} ℃ 밖 {dhold:g} s 넘게)")
                    push_log_sync("warn", msg)
                    self.pending_toasts.append(("warn", msg))
            else:
                self._hdev_since.pop(ch, None)
                if ch in self._hdev_warned:
                    self._hdev_warned.discard(ch)
                    push_log_sync("info", f"히터 이탈 해제 — CH{ch} {h.get('name', '')} {pv:.1f} ℃")
        for ch in sorted(self._hdev_warned):
            notes.append(("warn", f"히터 이탈 CH{ch} — PC 감시(process.heater_dev_c)"))
        self.watch_notices = notes

    def _spawn(self, coro):
        try:
            return asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()            # 루프 밖(시험의 동기 호출) — 보낼 수 없다
            return None

    async def _press_abort(self, log_sync=None):
        """압력 상한 — 즉시 중단을 한 번 보낸다(운전자 즉시 중단과 같은 결과 처리, 이유만 다르다).
        ★ v0.7.1(W3): 끝 결과의 이유(_abort_why)는 실제로 보낼 때만 세운다 — 일찍 돌아가면 뒤의 운전자 중단이 '압력 상한'
          으로 적히지 않게. 거절 · 실패는 이벤트 로그에도 남긴다."""
        log_sync = log_sync or (lambda lv, m: logger.write(lv, m))
        link = self.state.link
        if not (link and link.connected) or link.status[A.D_STATE] not in RUNNING_STATES:
            log_sync("warn", "압력 상한 즉시 중단을 보내지 않았습니다 — PLC 연결 없음 또는 공정이 이미 끝남")
            return
        self._abort_why = "압력 상한 — PC 가 즉시 중단"
        self.abort_begin()
        done = unknown = False
        try:
            result, text = await link.send_command(A.CMD_ABORT)
            done = result == A.RESULT_OK
            unknown = result is None and ("응답 없음" in text or "받았을 수" in text)
            logger.command("즉시 중단(압력 상한)", link._cmd_no, text, "local")
            if not done and not unknown:
                log_sync("warn", f"압력 상한 즉시 중단이 처리되지 않았습니다 — {text}")
        except Exception as e:  # noqa: BLE001
            log_sync("warn", f"압력 상한 즉시 중단 실패: {type(e).__name__}: {e}")
        finally:
            if not done and not unknown:
                self._abort_why = ""
            self.abort_result(done, unknown)

    def _needs_b13_wait(self, s) -> bool:
        """시퀀서 8 · 안전 정지 아님 · 이번 공정의 b13 이 아직 안 보임 — 1 s 기다린다."""
        # ★ v0.7.1(P1): 트립만 남은 안전 정지는 안전 정지로 보지 않는다(b13 1 s 대기를 건너뛰지 않게)
        return (s[A.D_SEQ_STATE] == 8 and (s[A.D_STATE] != A.STATE_SAFE_STOP or trip_only(s))
                and not self._b13_rose(s))

    def _b13_rose(self, s) -> bool:
        """이번 공정 중 0→1 로 바뀐 b13(시작 때 서 있었으면 그 뒤 0 을 본 다음에 선 것)."""
        return A.bit(s[A.D_ALARM0], A.ALM0_RECIPE) and (not self._b13_pre or self._b13_cleared)

    @staticmethod
    def _with_b13(s0, s):
        s0 = list(s0)
        s0[A.D_ALARM0] |= s[A.D_ALARM0] & (1 << A.ALM0_RECIPE)
        return s0

    def _flush_pending_end(self, log_sync=None) -> bool:
        """시작 흐름 첫머리 — 미뤄 둔 앞 공정의 끝을 지금 본 값으로 적고 앞 데이터 로그를 닫는다. 적었으면 True.
        log_sync 를 주면 끝 줄을 그것으로 남긴다(시작 흐름이 모아서 화면에 보낸다)."""
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
            return False
        if cur is not None and self._b13_rose(cur):
            s0 = self._with_b13(s0, cur)
        self._finish(s0, at, log_sync or pl)
        self._close_datalog()
        return True

    def _close_datalog(self):
        """앞 공정의 데이터 로그를 끝 결과와 함께 닫는다 — 새 공정이 시작 가장자리로 새 파일을 연다."""
        dl = self.state.datalog
        if dl is not None and dl.fp:
            dl.note_end(self.last_result)
            dl.close()
            dl._was_running = False

    def _finish(self, s, ended_at, push_log_sync):
        took = ended_at - (self.started_at or ended_at)
        self.last_result = self.end_result(s)
        lost = self._ended_while_lost
        if lost:
            self.last_result += LOST_TAIL           # v0.7.0(P3): 링크가 끊긴 동안 끝났다(다시 붙은 첫 상태가 공정 아님)
        # ★ v0.4.14: 이어받은 공정은 시작 시각을 모른다 — 걸린 시간은 이어받은 뒤부터라고 적는다
        took_txt = (("이어받은 뒤 " if self._adopted else "걸린 시간 ") + _hms(int(took * 1000))
                    + (" (다시 연결된 때까지)" if lost else ""))
        push_log_sync("ok" if result_level(self.last_result) == "ok" else "warn",
                      f"공정 {self.last_result} — {self.active_name or ''} · "
                      f"{took_txt} · 마지막 위치 {self.where_text()}")
        self._count_maint(s, push_log_sync)
        self._ended_while_lost = False
        self._abort_why = ""
        self._press_abort_sent = False
        self.started_at = 0.0
        self._abort_sent = False
        self._stop_reserved = False
        self._end_deferred = None
        self._abort_unknown = False
        self.active_run = False                 # 데이터 로그 구간 끝(끝 판정과 같은 순간)

    def _count_maint(self, s, push_log_sync):
        """v0.7.0(A4): 끝 판정 때 레시피 밸브 열림 횟수 · 열린 시간을 정비 카운터에 더한다(두 번 세지 않게 데이터 로그
        이름을 기억한다). 표 없이 이어받은 공정은 세지 않는다."""
        try:
            from . import maintenance
            dl = self.state.datalog
            key = (dl.name if (dl is not None and dl.fp) else "") or f"run-{int(self.started_at or time.time())}"
            msg = maintenance.count_run(self.state.cfg, key, self.active_recipe, s, self._last_pos,
                                        self.last_result.startswith("정상 종료"),
                                        words=(self.active_table or {}).get("words"), last_seq=self._last_seq,
                                        result=self.last_result)
            if msg:
                push_log_sync("info", msg)
        except Exception as e:  # noqa: BLE001
            _maint_failed(e, lambda m: push_log_sync("warn", m))

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
        b, st, c = self._last_pos[:3]
        gp = self._last_pos[3] if len(self._last_pos) > 3 else 0
        return f"블록 {b} · 스텝 {st} · 사이클 {c}" + (f" · 그룹 회차 {gp}" if gp > 1 else "")

    def _plan(self) -> dict:
        """끝 판정이 쓰는 블록 수 · 블록 반복 · 그룹 — plan_of(지금 공정의 표, 레시피)."""
        return plan_of(self.active_table, self.active_recipe)

    def _block_count(self) -> int:
        return self._plan()["nb"]

    def end_result(self, s) -> str:
        """끝났을 때의 결과(이벤트 로그·데이터 로그 메타·목록이 이 값을 쓴다) — 규칙은 judge_end(순수 함수)."""
        return judge_end(s, self.active_table, self.active_recipe, crit_text=self._crit_text(s),
                         stop_reserved=self._stop_reserved, stopping_seen=self._stopping_seen,
                         b13_rose=self._b13_rose(s), abort_sent=self._abort_sent,
                         abort_unknown=self._abort_unknown, abort_why=self._abort_why)

    def _crit_text(self, s) -> str:
        """켜진 중대 알람 이름. ★ Powder: 래더는 공정 중 안전 정지면 O3 허가 알람(알람1 b3)도 함께 세운다
        (P30 행 13 + P35 행 26) — O3 발생기 알람 입력이 꺼져 있고 다른 중대 알람이 있으면 b3 는 원인이
        아니라 '뒤따름'으로 적는다."""
        # ★ v0.7.1(P5): 끝 스냅샷의 중대 비트가 먼저(라이브 알람 목록이 비어도 EMO 를 놓치지 않게), 없으면 라이브 목록,
        #   둘 다 없으면 crit_text_of — b6 만 서 있으면 'PC Link Lost (트립 남음)'
        crit = crit_alarms_of(s) or [a for a in self.state.alarms.list() if a["crit"]]
        if not crit:
            return crit_text_of(s, [])
        if DEV.HAS_O3 and len(crit) > 1 and not A.bit(s[A.D_INPUT1], A.IN1_O3_ALM):
            b3 = f"A1-{A.ALM1_O3_GEN:02d}"
            follow = [a["name"] for a in crit if a["code"] == b3]
            cause = [a["name"] for a in crit if a["code"] != b3]
            if follow:
                return " · ".join(cause) + " · 뒤따름: " + follow[0]
        return " · ".join(a["name"] for a in crit)

    def _last_cycle_of_all(self, blk, cyc, reps, s) -> bool:
        return last_cycle_of_all(self._plan(), blk, cyc, reps, s)

    def _table_fault(self, blk: int, step: int = 0) -> str:
        return table_fault((self.active_table or {}).get("words"), blk, step)

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
        # ★ v0.4.14: PLC 가 공정 중인데 이 PC 가 맡은 공정이 없다(이어받기 전) — 골라 둔 레시피를 실행 중 레시피로
        #   보이지 않는다. 이름은 '(이어받는 중)', 남은 시간은 내지 않는다
        foreign = running and not self.active_run and self._id_blocked()   # v0.4.15: 다른 장비 공정
        # ★ v0.4.16: busy(시작 태스크가 아직 안 끝남) 대신 phase — 명령 1 응답을 못 받은 직후 약 0.2 s 동안 앞 공정
        #   이름이 보이던 것
        adopting = running and not self.active_run and self.phase == IDLE and not foreign
        out = {
            "running": running,
            "phase": self.phase,
            "message": self.message,
            # 공정 중이면 시작 때 스냅샷, 아니면 지금 고른 레시피
            "recipe": (FOREIGN_NAME if foreign else ADOPTING_NAME if adopting
                       else (self.active_name if running else self.recipe_name)),
            "number": None if (adopting or foreign) else ((self.active_table if running else self.table) or {}).get("number"),
            "adopting": adopting,
            "foreign": foreign,
            # v0.4.15: 이어받은 공정 — 경과는 이어받은 뒤부터(화면은 '이어받은 뒤 …')
            "adopted": bool(running and self.active_run and self._adopted),
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
        # 시작 조건은 화면이 아니라 서버가 판정한다 — 화면은 항목과 이유만 그린다.
        # ★ v0.8.0: 공정 중에도 checks 를 보낸다(화면의 시작 조건 칸은 흐리게 '다음 시작 때 확인') —
        #   can_start · blocking 은 공정이 아닐 때만
        # ★ v0.8.1: 공정 중 시작 조건은 화면용이다 — progress() 는 live(5 Hz)와 데이터 로그 줄이 부르므로 이 계산의 예외가
        #   공정 중 live · 데이터 로그를 깨뜨리지 않게 checks = [] 로 내고, 경고는 같은 예외 종류마다 한 실행에 한 번만.
        #   공정이 아닐 때는 시작 판단이라 예외를 삼키지 않는다
        warned = self.__dict__.setdefault("_checks_warned", set())
        if running:
            try:
                ok, blocking, checks = self.can_start()
            except Exception as e:
                checks = []
                kind = type(e).__name__
                if kind not in warned:
                    warned.add(kind)
                    logger.write("warn", f"공정 중 시작 조건 계산 실패(화면의 시작 조건만 비움 — 공정 · 데이터 로그는 계속): "
                                         f"{kind}: {logger.clean(e, 160)}")
        else:
            warned.clear()
            ok, blocking, checks = self.can_start()
        out["checks"] = checks
        if not running:
            out["can_start"] = ok
            out["blocking"] = [c["label"] for c in blocking]
            if self.recipe:
                out["estimate"] = self.estimate()
        rec = None if (adopting or foreign) else self.active_recipe
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
    def _id_blocked(self) -> bool:
        """장비 ID 가 맞지 않아 막힌 연결(다른 장비의 PLC)인가."""
        link = self.state.link
        return bool(link and hasattr(link, "id_block_text") and link.id_block_text())

    def adopt_wanted(self) -> bool:
        """이어받기를 시도할 때인가 — PLC 가 공정 중인데 이 PC 가 맡은 공정이 없고(active_run 거짓),
        이 PC 의 시작 절차 · 끝 처리(중단 결과 · b13 대기 · 미뤄 둔 끝) 중이 아니다.
        ★ 골라 둔 레시피는 막지 않는다 — 이어받으면 PLC 에 올라간 레시피로 바뀐다."""
        link = self.state.link
        if not (link and link.connected) or link.status[A.D_STATE] not in RUNNING_STATES:
            return False
        # ★ v0.4.14: 장비 ID 가 맞지 않아 막힌 연결이면 이어받지 않는다(다른 장비 공정의 데이터 로그 · 끝 판정이
        #   이 장비 폴더에 생겼다)
        if self._id_blocked():
            return False
        return not (self.active_run or self.busy or self._abort_pending
                    or self._b13_wait is not None or self._end_deferred is not None)

    async def check_offline_end(self, push_log) -> str:
        """v0.7.0(P6) — PC 가 꺼진 동안 끝난 공정. 프로그램이 켜진 뒤 처음으로 PLC 와 붙었을 때(ID 맞음 · 공정 중
        아님 · PLC 표 되읽음) 한 번: 가장 최근 메타가 끝 판정이 없고 번호 · 합계가 PLC 표와 같고 시퀀서 끝 값이 6 또는
        8 이면(시퀀서 0 · 블록 0 은 PLC 재시작이라 판정하지 않음) judge_end 로(계획은 메타의 레시피 사본과 표) 결과를
        정해 메타에 쓴다. 판정했으면 결과 글, 아니면 ''."""
        link = self.state.link
        if not (link and link.connected) or getattr(link, "plc_hb_stalled", False):
            return ""
        if self._id_blocked() or getattr(link, "id_state", "") not in ("ok", "unset"):
            return ""
        if link.status[A.D_STATE] in RUNNING_STATES and not (self.active_run or self.busy):
            # ★ v0.7.1(P3): PLC 가 공정 중 — 이어받기가 맡는다. 기회를 쓰지 않는다(이어받기가 되읽는 동안 공정이 끝나
            #   그만두면 다시 본다)
            return ""
        self.offline_checked = True             # 첫 기회에 한 번(아래 조건이 안 맞아도 다시 보지 않는다)
        if link.status[A.D_STATE] in RUNNING_STATES or self.active_run or self.busy:
            return ""
        from . import datalog as D
        name, meta = D.latest_meta()
        if not name or D.judged(meta):
            return ""
        words = await link.read_recipe_area()
        if not words:
            self.offline_checked = False        # 표를 못 읽었다 — 다음 기회에 다시
            return ""
        info = R.from_plc_words(words)
        s = list(link.status)
        if meta.get("number") != info["number"] or meta.get("checksum") != info["checksum"]:
            return ""
        if s[A.D_SEQ_STATE] not in (6, 8) or (s[A.D_SEQ_STATE] == 0 and s[A.D_SEQ_BLOCK] == 0):
            return ""
        res = judge_end(s, {"words": list(words)}, meta.get("recipe"),
                        b13_rose=A.bit(s[A.D_ALARM0], A.ALM0_RECIPE)) + OFF_TAIL
        new = dict(meta)
        if meta.get("ended"):
            new["log_closed"] = meta["ended"]   # 프로그램이 닫은 시각
        new.update(result=res, ended="모름", judged_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        storage.atomic_write_json(D.os.path.join(D.paths.DATALOG_DIR, name + ".recipe.json"), new)
        await push_log(f"PC 가 꺼진 동안 끝난 공정 — {name}: {res}",
                       "ok" if result_level(res) == "ok" else "warn")
        try:
            from . import maintenance
            msg = maintenance.count_run(self.state.cfg, name, meta.get("recipe"), s, None, res.startswith("정상 종료"),
                                        words=list(words), result=res)
            if msg:
                await push_log(msg, "info")
        except Exception as e:  # noqa: BLE001
            fail = []
            _maint_failed(e, fail.append)
            await push_log(fail[0], "warn")
        return res

    async def adopt_running(self, push_log) -> bool:
        """PC 를 다시 켰는데(또는 링크가 다시 붙었는데) PLC 가 이미 공정 중이면 레시피를 되찾아 이어 간다.
        server 의 이어받기 루프가 링크가 붙어 있는 동안 몇 초마다 부른다(adopt_wanted 일 때만 실제로 한다).
        이어받았으면 True.
        ★ b13 은 늘 되읽기 전 상태로 정한다(되읽는 동안 선 b13 은 이번 공정 것).
        ★ 되읽기가 세 번 다 실패하면 이름 · 표 없이 이어받는다 — 데이터 로그를 열고 끝 판정을 한다(표가 있어야
          하는 이유 글만 '표 모름'). 되읽는 중 끊기면 로그를 남기고 다음 시도로 넘긴다."""
        if not self.adopt_wanted():
            return False
        link = self.state.link
        b13_pre = A.bit(link.status[A.D_ALARM0], A.ALM0_RECIPE)
        words = None
        for i in range(ADOPT_READ_TRIES):             # 되읽기가 실패하면 다시(한 번 놓쳤다고 버리지 않는다)
            if i:
                await asyncio.sleep(ADOPT_READ_GAP_S)
            if not link.connected:
                break
            words = await link.read_recipe_area()
            if words:
                break
        if not link.connected:
            await push_log("PLC 가 공정 중인데 레시피 표를 되읽는 중 연결이 끊겼습니다 — 다시 붙으면 이어받기를 "
                           "다시 시도합니다", "warn")
            return False
        # ★ 되읽는 동안 공정이 끝났거나 이 PC 가 공정을 맡았으면 이어받지 않는다
        if not self.adopt_wanted():
            if not self.active_run and link.connected and link.status[A.D_STATE] not in RUNNING_STATES:
                self.offline_checked = False    # v0.7.1(P3): 되읽는 동안 끝났다 — 꺼진 동안 끝난 공정 판정을 다시 본다
            return False
        # ★ v0.4.14: 상태를 먼저 다 정하고 로그는 뒤에 — push_log 가 양보하면 그 사이 운전자 중단을 잃을 수 있다
        if words:
            info = R.from_plc_words(words)
            name = storage.find_by_number(info["number"])
            # 끝 판정의 표 근거는 PLC 에 실제로 올라가 있는 표(되읽은 것)
            if name and self.select(name)[0]:
                self.run = {"name": name, "recipe": self.recipe,
                            "table": dict(self.table or {}, words=list(words))}
                msg = f"PLC 가 이미 공정 중입니다 — 레시피 [{name}] (번호 {info['number']})로 이어 갑니다"
            else:
                self.recipe = None
                self.recipe_name = f"(PLC 번호 {info['number']})"
                self.table = {"number": info["number"], "checksum": info["checksum"]}
                self.state.recipe_check = {}
                self.run = {"name": self.recipe_name, "recipe": None, "table": dict(self.table, words=list(words))}
                msg = (f"PLC 가 이미 공정 중입니다 — 번호 {info['number']} 에 맞는 로컬 레시피가 없어 "
                       f"이름 없이 표시합니다")
        else:
            self.recipe = None
            self.recipe_name = ADOPT_NO_TABLE_NAME
            self.table = None
            self.state.recipe_check = {}
            self.run = {"name": self.recipe_name, "recipe": None, "table": None}
            msg = (f"PLC 가 이미 공정 중인데 레시피 표를 {ADOPT_READ_TRIES}번 읽지 못했습니다 — 표 없이 "
                   "이어 갑니다(데이터 로그 · 끝 판정은 하고, 표가 필요한 이유는 '표 모름')")
        self._was_running = True
        self.started_at = time.time()
        self.active_run = True                  # 이어받은 공정도 데이터 로그 구간
        self._run_started_mono = time.monotonic()
        self._adopted = True
        # ★ v0.7.1(P2): 같은 데이터 로그 파일을 다시 열어도 되는가 — 켠 뒤 첫 이어받기이고 이 실행에서 시작 명령을 보낸
        #   적이 없을 때만(시간 창은 데이터 로그가 메타의 시작 시각 · 예상 시간으로 본다)
        self.adopt_count += 1
        self.may_resume = self.adopt_count == 1 and not self.start_sent
        # ★ b13 은 리셋 전까지 남는다 — 이어받는 순간 이미 서 있었으면 '이번 공정 중 선 것'이 아니다
        self._gen += 1
        self._b13_pre = b13_pre
        self._b13_cleared = False
        self._abort_sent = self._abort_pending = self._abort_unknown = False
        self._end_deferred = self._b13_wait = None
        self._abort_why = ""                # v0.7.1(W3)
        self._press_abort_sent = False
        # 시작 흐름과 같게 지운다
        self._stop_reserved = False
        self._stopping_seen = link.status[A.D_STATE] == A.STATE_STOPPING
        self._last_pos = None
        self._last_seq = None
        self.last_result = ""
        self._rf_watch = None
        self._rf_warned = set()
        # ★ v0.7.2: 이어받았으면 꺼진 동안 끝난 공정 판정 기회는 끝 — 다음 루프(최대 3 s)까지 미루면 그 사이 이어받은
        #   공정이 끝났을 때 아직 열린 메타를 'PC 꺼진 동안 끝남' 으로 판정할 수 있었다
        self.offline_checked = True
        await push_log(msg, "warn")
        return True


# ===================== 끝 판정 (순수 함수 — 러너 · 꺼진 동안 끝난 공정 판정이 같이 쓴다) =====================
TRIP_LEFT_TEXT = "PC Link Lost (트립 남음)"
LOST_TAIL = " · PC 끊긴 동안 끝남"
OFF_TAIL = " · PC 꺼진 동안 끝남(다시 켠 뒤 PLC 끝 상태로 판정)"


def plan_of(table, recipe) -> dict:
    """끝 판정이 쓰는 블록 수 · 블록 반복 · 그룹 — {"nb", "repeats": [블록별 반복], "groups": [(시작, 끝, 반복)]}.
    ★ v0.4.14: 되읽은 표(words)가 있으면 그 표에서(R.from_plc_words) — '(PLC 번호 N)' 으로 이어받아 로컬
      레시피가 없어도 사이클 후 정지를 가른다. 표가 없으면 레시피, 둘 다 없으면 nb = 0."""
    tbl = table or {}
    if tbl.get("words"):
        info = R.from_plc_words(tbl["words"])
        return {"nb": int(info["block_count"]),
                "repeats": [int(b["repeat"] or 0) for b in info["blocks"]],
                "groups": [(g["from_block"], g["to_block"], g["repeat"] or 1) for g in info["groups"]]}
    rec = recipe or {}
    blocks = rec.get("blocks") or []
    groups = []
    for g in rec.get("groups") or []:
        try:
            groups.append((int(g.get("from_block")), int(g.get("to_block")), int(g.get("repeat") or 1)))
        except (TypeError, ValueError, AttributeError):
            continue
    nb = int(tbl["block_count"]) if tbl.get("block_count") else len(blocks)
    return {"nb": nb, "repeats": [int(b.get("repeat") or 1) for b in blocks], "groups": groups}


def crit_alarms_of(s) -> list:
    """끝 스냅샷의 켜진 중대 알람 [{code, name}] (워드0 → 워드1, 비트 순)."""
    out = []
    for word, defs, tag in ((s[A.D_ALARM0], DEV.ALARMS0, "A0"), (s[A.D_ALARM1], DEV.ALARMS1, "A1")):
        out += [{"code": f"{tag}-{d['bit']:02d}", "name": d["name"]} for d in defs if d["crit"] and (word >> d["bit"]) & 1]
    return out


def crit_names_of(s) -> list:
    """끝 스냅샷의 알람 워드에서 켜진 중대 알람 이름(알람 탭 순서 — 워드0 → 워드1, 비트 순)."""
    out = []
    for word, defs in ((s[A.D_ALARM0], DEV.ALARMS0), (s[A.D_ALARM1], DEV.ALARMS1)):
        out += [d["name"] for d in defs if d["crit"] and (word >> d["bit"]) & 1]
    return out


def crit_text_of(s, names=None) -> str:
    """안전 정지 원인 글. 중대 알람 이름(names — 없으면 끝 스냅샷에서). ★ v0.7.0(P3): 중대 알람이 없어도 알람0 b6
    'PC Link Lost' 가 서 있으면 그것이 원인(공정 뒤 남은 PC 끊김 트립) — 'PC Link Lost (트립 남음)'."""
    names = crit_names_of(s) if names is None else names
    if names:
        return " · ".join(names)
    if A.bit(s[A.D_ALARM0], A.ALM0_PC_LINK):
        return TRIP_LEFT_TEXT
    return "중대 알람 이름 없음"


def last_cycle_of_all(plan, blk, cyc, reps, s) -> bool:
    """사이클 후 정지가 마지막 블록의 마지막 사이클(그 블록이 그룹 안이면 마지막 그룹 회차)에 겹쳤나."""
    nb = plan["nb"]
    if not (nb and blk == nb and reps and cyc >= reps):
        return False
    for a, z, r in plan["groups"]:
        if a <= blk <= z and s[A.D_SEQ_GROUP_PASS] < r:
            return False
    return True


def table_fault(words, blk: int, step: int = 0) -> str:
    """레시피 표 오류의 위치 · 이유 — 시작 스냅샷 표를 래더 적재 검사처럼 본다(부호 있는 16비트).
    블록 항목 → 그 스텝 시간(P40 행 116~117, 20 ~ 3,276,700 ms) → 그룹 순. 걸리는 것이 없으면 "".
    래더는 그룹 진입이 거절돼도 다음 블록을 적재한 뒤 끝내므로 D00021 은 그 다음 블록이다."""
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


def judge_end(s, table=None, recipe=None, *, crit_text=None, stop_reserved=False, stopping_seen=False,
              b13_rose=False, abort_sent=False, abort_unknown=False, abort_why="") -> str:
    """끝났을 때의 결과(이벤트 로그 · 데이터 로그 메타 · 목록이 이 값을 쓴다). 래더 동작 기준, 앞이 우선:

    1. 정상 완료 — 시퀀서 6 이고 D00021 > 블록 수(래더는 SEQ_BLOCK 을 블록 수 + 1 로 올린 뒤 끝낸다).
       장비 상태 6 이어도 완료를 먼저 본다 → '정상 종료 (끝난 뒤 안전 정지: …)'. ★ v0.7.0(P): 공정 중 남은 PC 끊김
       트립(알람0 b6)은 공정이 끝난 다음 스캔에 안전 정지를 세운다 — 끝 스냅샷이 그 사이 한 스캔(장비 상태 1)이어도
       b6 이 서 있으면 같은 글('… 끝난 뒤 안전 정지: PC Link Lost (트립 남음)')
    2. 안전 정지(장비 상태 6) — 운전자 중단보다 앞선다(중단을 누른 순간 비상정지가 났다면 원인은 비상정지).
       단 중대 알람 없이 트립만 남은 사이클 후 정지는 3 으로(끝난 뒤 안전 정지)
    3. 사이클 후 정지 — 시퀀서 6 이고 D00021 ≤ 블록 수. 마지막 블록의 마지막 사이클(그 블록이 그룹 안이면
       마지막 그룹 회차)이면 다 끝난 것 → '정상 종료 (사이클 후 정지와 겹침)'
    4. 레시피 표 오류 — 시퀀서 8 AND 이번 공정 중 선 알람0 b13(중대 아님 — 장비 상태 1). 시작 때부터 서 있던
       b13 이면 스냅샷 표로 그 블록 · 스텝 · 그룹이 정말 적재에서 걸리는지 보고 정한다. 운전자 중단보다 앞선다
    5. 운전자 중단 — 명령 5 결과 0(abort_why 가 있으면 그 이유 — 예: 압력 상한). 결과를 못 받았으면 '결과 확인 안 됨'
    6. PLC 재시작 — 끝 스냅샷이 시퀀서 0 · 블록 0(P00 첫 스캔이 지웠다). 원인이 남은 알람(장비 상태 6)은 뒤따름.
    7. 시퀀서 8 — PLC 중단 / 그 밖 — 끝 확인 안 됨
    table · recipe: 그 공정의 계획(표 words 가 있으면 표가 우선). crit_text: 안전 정지 원인 글(없으면 끝 스냅샷에서)."""
    crit = crit_text if crit_text is not None else crit_text_of(s)
    if trip_only(s):
        # ★ v0.7.1(P1): 안전 정지 원인이 공정 뒤 남은 PC 끊김 트립뿐이다 — 공정이 끝난 다음 스캔에 선 것이라 끝의 진짜
        #   원인(운전자 중단 · 표 오류 · 결과 모름 · 압력 상한 …)을 가리지 않게 상태가 안전 정지가 아닌 것처럼 판정하고
        #   정상 종료가 아닌 결과에는 꼬리를 붙인다
        s1 = list(s)
        s1[A.D_STATE] = A.STATE_IDLE
        res = _judge(s1, table, recipe, crit, True, stop_reserved, stopping_seen, b13_rose, abort_sent,
                     abort_unknown, abort_why)
        if not res.startswith("정상 종료") and "끝난 뒤 안전 정지" not in res:
            res += f" · 끝난 뒤 안전 정지: {crit}"
        return res
    res = _judge(s, table, recipe, crit, False, stop_reserved, stopping_seen, b13_rose, abort_sent,
                 abort_unknown, abort_why)
    if trip_pending(s) and not res.startswith("정상 종료") and "끝난 뒤 안전 정지" not in res:
        # ★ v0.7.2: 끝 스냅샷이 안전 정지 직전 한 스캔(시퀀서 8 · 장비 상태 ≠ 6 · b6 · 중대 알람 없음) — PLC 는 다음
        #   스캔에 안전 정지한다. 시퀀서 6 갈래(after_stop)와 같게 꼬리를 붙인다(원인은 스냅샷 그대로 트립)
        res += f" · 끝난 뒤 안전 정지: {TRIP_LEFT_TEXT}"
    return res


def _maint_failed(e, log):
    """v0.7.2: 정비 카운터 세기 실패 — 이벤트 로그(log — 파일 로그도 거기서)와 Program Notices 경고(다음에 세면 지운다).
    예전에는 파일 로그 한 줄로만 묻혀 v0.7.1 에서 하나도 세지 않는 것을 놓쳤다."""
    msg = f"정비 카운터 세기 실패(공정 결과와 무관 — 이번 공정의 밸브 열림을 세지 못함): {type(e).__name__}: {logger.clean(e, 160)}"
    try:
        from . import maintenance
        maintenance.note_count_error(msg)
    except Exception:  # noqa: BLE001
        pass
    log(msg)


def valve_permit_missing(s) -> list:
    """v0.8.0: 공정 밸브 허가(인터락 b4)에 빠진 조건 — 래더와 같은 조건(기본 인터락 · 펌프 · IV-E 열림 · 대기압 아님 ·
    안전 정지 요구 없음). 허가가 있으면 []. 허가가 없는데 위가 다 맞으면 ['PLC 허가 조건 확인']."""
    ilk, i0 = s[A.D_INTERLOCK], s[A.D_INPUT0]
    if A.bit(ilk, A.ILK_VALVE_OK):
        return []
    miss = []
    if not A.bit(ilk, A.ILK_BASIC):
        miss.append("기본 인터락")
    if not A.bit(ilk, A.ILK_PUMP):
        miss.append("Pump 운전")
    if not A.bit(i0, A.IN0_IVE_OPEN):
        miss.append("IV-E 열림")
    if A.bit(i0, A.IN0_ATM):
        miss.append("대기압 아님")
    if A.bit(ilk, A.ILK_SAFE_STOP_REQ):
        # ★ v0.7.0(P3): 안전 정지 요구가 공정 뒤 남은 PC 끊김 트립 때문이면(비상정지 · 중대 알람 없음) 할 일을 적는다
        only = (A.bit(s[A.D_ALARM0], A.ALM0_PC_LINK) and A.bit(i0, A.IN0_EMO)
                and not ((s[A.D_ALARM0] & DEV.CRITICAL_MASK0) or s[A.D_ALARM1]))
        miss.append("PC 끊김 트립 — Alarm Reset" if only else "안전 정지 요구 해제")
    return miss or ["PLC 허가 조건 확인"]


def trip_pending(s) -> bool:
    """v0.7.2: 시퀀서 8 로 끝났고 PC 끊김 트립(b6)이 남았는데 아직 안전 정지 전(장비 상태 ≠ 6) · 중대 알람 없음 —
    다음 스캔에 트립만으로 안전 정지한다."""
    return (s[A.D_SEQ_STATE] == 8 and s[A.D_STATE] != A.STATE_SAFE_STOP and A.bit(s[A.D_ALARM0], A.ALM0_PC_LINK)
            and not ((s[A.D_ALARM0] & DEV.CRITICAL_MASK0) or s[A.D_ALARM1]))


def trip_only(s) -> bool:
    """v0.7.1(P1): 장비 상태 6(안전 정지)의 원인이 공정 뒤 남은 PC 끊김 트립(알람0 b6)뿐 — 비상정지 입력 정상(스냅샷에
    입력이 있으면) · 중대 알람 없음."""
    if s[A.D_STATE] != A.STATE_SAFE_STOP or not A.bit(s[A.D_ALARM0], A.ALM0_PC_LINK):
        return False
    return not ((s[A.D_ALARM0] & DEV.CRITICAL_MASK0) or s[A.D_ALARM1])


def _judge(s, table, recipe, crit, trip_left, stop_reserved, stopping_seen, b13_rose, abort_sent, abort_unknown,
           abort_why) -> str:
    seq, st = s[A.D_SEQ_STATE], s[A.D_STATE]
    plan = plan_of(table, recipe)
    nb = plan["nb"]
    blk = s[A.D_SEQ_BLOCK]
    trip = A.bit(s[A.D_ALARM0], A.ALM0_PC_LINK)
    crit_any = bool((s[A.D_ALARM0] & DEV.CRITICAL_MASK0) or s[A.D_ALARM1])
    # 끝난 뒤 안전 정지(또는 다음 스캔에 선다)
    after_stop = st == A.STATE_SAFE_STOP or trip_left or (seq == 6 and trip)
    if seq == 6 and not nb and (stop_reserved or stopping_seen) and st != A.STATE_SAFE_STOP:
        # ★ v0.4.14: 표 없이 이어받은 공정 — 블록 수를 모른다. 공정 중 '사이클 후 정지 예약'을 봤으면 사이클 후
        #   정지(마지막 사이클과 겹쳤는지는 알 수 없다). 예전에는 '정상 종료'로 적혔다
        cyc = A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1])
        return f"사이클 후 정지 (블록 {blk} · 사이클 {cyc} · 표 모름)"
    if seq == 6 and (not nb or blk > nb):
        if after_stop:
            return f"정상 종료 (끝난 뒤 안전 정지: {crit})"
        return "정상 종료"
    if seq == 0 and blk == 0:
        follow = f" · 뒤따름: {crit}" if st == A.STATE_SAFE_STOP else ""
        return f"중단 (PLC 재시작 — 시퀀서 · 출력 초기화{follow})"
    if st == A.STATE_SAFE_STOP and not (seq == 6 and trip and not crit_any):
        return f"중단 (안전 정지 — {crit})"
    if seq == 6:
        # ★ 끝 스냅샷의 D00024 — 래더는 끝에서 사이클을 올리지 않는다(P40 행 55~56 · 66)
        cyc = A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1])
        repeats = plan["repeats"]
        reps = int(repeats[blk - 1] or 0) if 1 <= blk <= len(repeats) else 0
        if last_cycle_of_all(plan, blk, cyc, reps, s):
            if after_stop:
                return f"정상 종료 (끝난 뒤 안전 정지: {crit})"
            return "정상 종료 (사이클 후 정지와 겹침)"
        rep = f"/{reps}" if reps else ""
        tail = f" · 끝난 뒤 안전 정지: {crit}" if after_stop else ""
        return f"사이클 후 정지 (블록 {blk} · 사이클 {cyc}{rep}){tail}"
    if seq == 8 and A.bit(s[A.D_ALARM0], A.ALM0_RECIPE):
        words = (table or {}).get("words")
        if not words:
            # 표 없이 이어받았다 — 남은 b13 인지 가릴 표가 없으니 이번 공정 중 선 것만 표 오류로
            if b13_rose:
                return f"중단 (레시피 표 오류 — 블록 {blk} · 표 모름)"
        else:
            why = table_fault(words, blk, s[A.D_SEQ_STEP])
            if b13_rose or why:
                return f"중단 (레시피 표 오류 — {why or f'블록 {blk} 적재 거절'})"
    if abort_sent:
        return f"중단 ({abort_why or '운전자 중단'})"
    if abort_unknown:
        return f"중단 ({abort_why or '운전자 중단'} — 결과 확인 안 됨)"
    if seq == 8:
        return "중단 (PLC 중단)"
    return "중단 (끝 확인 안 됨)"


ADOPT_READ_TRIES = 3     # 이어받을 때 PLC 레시피 표 되읽기 시도 횟수
ADOPT_READ_GAP_S = 0.5   # 그 사이 간격
ADOPT_RETRY_S = 3.0      # PLC 가 공정 중인데 맡은 공정이 없으면 이만큼마다 이어받기를 다시 시도(server)
ADOPT_NO_TABLE_NAME = "(PLC 레시피 — 표 모름)"
ADOPTING_NAME = "(이어받는 중)"
FOREIGN_NAME = "(다른 장비 공정)"     # v0.4.16 줄임 — 빨간 띠가 이미 조작을 막았다고 말한다
START_NOT_SENT = "공정 시작 명령을 보내지 못했습니다"
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


def _num(v, default: float) -> float:
    try:
        return float(default if v is None or v == "" else v)
    except (TypeError, ValueError):
        return float(default)


# ===================== 실행 정보 (v0.7.0 A2) =====================
RUN_INFO_MAX = {"operator": 40, "sample": 80, "memo": 200}


def clean_run_info(info) -> dict:
    """작업자 · 시료 · 메모를 정리한다 — 문자열만, 제어 문자(줄바꿈 · 탭 포함)는 빈칸으로, 앞뒤 빈칸 정리, 길이 40 · 80 · 200."""
    out = {}
    info = info if isinstance(info, dict) else {}
    for k, n in RUN_INFO_MAX.items():
        v = info.get(k)
        v = v if isinstance(v, str) else ""
        v = "".join(" " if (ord(ch) < 32 or ord(ch) == 127 or 0x80 <= ord(ch) < 0xA0) else ch for ch in v)
        out[k] = " ".join(v.split())[:n]
    return out


def run_info_text(info) -> str:
    """이벤트 로그 시작 줄에 붙이는 ' · 작업자 … · 시료 …'(없으면 '')."""
    info = info or {}
    parts = [f"작업자 {info['operator']}" if info.get("operator") else "",
             f"시료 {info['sample']}" if info.get("sample") else ""]
    parts = [x for x in parts if x]
    return (" · " + " · ".join(parts)) if parts else ""


def _hms(ms) -> str:
    s = max(0, int((ms or 0) / 1000))
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
