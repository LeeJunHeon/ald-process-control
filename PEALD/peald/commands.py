"""
commands.py — 화면 명령 → 권한·상태 검사 → PLC 명령 핸드셰이크.

검사는 세 겹이고 앞의 두 겹은 전부 서버에서 한다.
  1) 권한 : 조작 명령은 이 PC(루프백) 접속에서만 받는다. 원격은 거절하고 로그를 남긴다.
  2) 상태 : PLC 연결이 끊겼거나, PLC 규칙상 지금 받을 수 없는 명령은 미리 거절한다(편의).
  3) PLC  : 최종 판단은 PLC 의 결과 코드다. 위 두 겹을 통과해도 PLC 가 거절할 수 있다.

★ 2)는 어디까지나 편의다. PC 가 통과시켰다고 안전한 것이 아니라, PLC 가 0 을 돌려줘야
  실제로 처리된 것이다. 그래서 PC 쪽 판정을 근거로 "됐다"고 말하지 않는다.

★ 시간 초과(응답 없음)일 때 같은 명령을 자동으로 다시 보내지 않는다 —
  PLC 가 이미 받았을 수 있어 두 번 실행될 위험이 있다. 운전자에게 확인하라고 알린다.
"""

from . import addresses as A
from . import device as DEV
from . import logger
from .state import state
from .connection import manager, push_state, push_notice, push_log

# 원격(보기 전용)이 보낼 수 있는 명령. 나머지는 전부 거절한다.
READ_ONLY_CMDS = {"ping"}

# 이번 단계에서 화면이 보낼 수 있는 명령 → PLC 명령 코드
CMD_MAP = {
    "pump_start": A.CMD_PUMP_START,
    "pump_stop": A.CMD_PUMP_STOP,
    "vent": A.CMD_VENT,
    "all_close": A.CMD_ALL_CLOSE,
    "alarm_ack": A.CMD_ALARM_ACK,
    "alarm_reset": A.CMD_ALARM_RESET,
}

# PLC 가 공정 중으로 보는 상태 — 수동·배기 계열을 받지 않는다.
RUNNING_STATES = (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)

_shutdown_handler = None


def set_shutdown_handler(fn):
    global _shutdown_handler
    _shutdown_handler = fn


async def handle_command(data: dict, ws=None):
    cmd = (data or {}).get("cmd")
    if not cmd:
        return

    local = manager.is_local_ws(ws) if ws is not None else True
    if not local and cmd not in READ_ONLY_CMDS:
        host = getattr(getattr(ws, "client", None), "host", "?")
        logger.write("warn", f"원격 조작 명령 거절: {cmd} ({host})")
        await push_notice("원격 접속은 보기 전용입니다 — 조작은 장비 PC에서 하세요", "warn", ws)
        return

    if cmd == "exit":
        await _cmd_exit(ws)
        return
    if cmd == "sim_fault":
        await _cmd_sim_fault(data, ws)
        return
    if cmd == "alarm_popup_close":
        state.alarm_popup = False
        return

    code = CMD_MAP.get(cmd)
    if code is None:
        await push_notice(f"알 수 없는 명령입니다: {cmd}", "warn", ws)
        return
    await _send_plc(code, ws)


# ===================== PLC 명령 =====================
async def _send_plc(code: int, ws):
    name = A.CMD_NAMES.get(code, str(code))
    link = state.link
    if not (link and link.connected):
        await push_notice(f"{name}: PLC 에 연결되어 있지 않습니다", "warn", ws)
        return

    ok, why = precheck(code)
    if not ok:
        # PC 쪽 사전 판정. PLC 에 보내기 전에 막고 이유를 보여 준다.
        await push_notice(f"{name}: {why}", "warn", ws)
        logger.write("warn", f"명령 {name} 사전 거절 — {why}")
        return

    no = link._cmd_no
    result, text = await link.send_command(code)
    origin = getattr(getattr(ws, "client", None), "host", "local") if ws else "local"
    logger.command(name, no, text, origin)

    if result is None:
        await push_notice(f"{name}: {text}", "err", ws)
        await push_log(f"명령 {name} — {text}", "err")
        return
    if result == A.RESULT_OK:
        await push_notice(f"{name}: 처리되었습니다", "ok", ws)
        await push_log(f"명령 {name} — 처리됨", "ok")
    else:
        detail = text
        if result == A.RESULT_INTERLOCK:
            detail = f"{text} — {explain_interlock(code)}"
        await push_notice(f"{name}: {detail}", "warn", ws)
        await push_log(f"명령 {name} — {detail}", "warn")
    await push_state()


def precheck(code: int):
    """PLC 규칙과 같은 기준으로 미리 거른다. (통과여부, 이유)"""
    link = state.link
    if not (link and link.connected):
        return False, "PLC 에 연결되어 있지 않습니다"
    s = link.status
    st = s[A.D_STATE]
    ilk = s[A.D_INTERLOCK]
    a0 = s[A.D_ALARM0]

    if st in RUNNING_STATES and code in (9, 10, 11, 12, 13, 14):
        return False, f"공정 중({A.STATE_NAMES.get(st, st)})에는 할 수 없습니다"

    if code == A.CMD_PUMP_START:
        blocking = []
        for bit, label in ((A.ALM0_EMO, "비상정지"), (A.ALM0_PUMP, "펌프 알람"),
                           (A.ALM0_AIR, "공압 저하"), (A.ALM0_CW, "냉각수 이상")):
            if (a0 >> bit) & 1:
                blocking.append(label)
        if blocking:
            return False, "알람 때문에 막혀 있습니다 — " + " · ".join(blocking)
    if code == A.CMD_VENT and not (ilk >> A.ILK_VENT_OK) & 1:
        return False, "벤트 허가가 없습니다 — " + explain_interlock(code)
    if code == A.CMD_MANUAL_APPLY and (ilk >> A.ILK_SAFE_STOP_REQ) & 1:
        return False, "안전 정지 요구 중에는 수동 조작을 할 수 없습니다"
    return True, ""


def explain_interlock(code: int) -> str:
    """결과 1(인터락 조건 미달)일 때 무엇이 빠졌는지 비트를 풀어서 알려 준다.
    ★ "인터락 미달"만 보여 주면 운전자가 무엇을 고쳐야 할지 알 수 없다."""
    link = state.link
    if not (link and link.connected):
        return "PLC 상태를 읽을 수 없습니다"
    s = link.status
    i0, i1 = s[A.D_INPUT0], s[A.D_INPUT1]
    ilk = s[A.D_INTERLOCK]
    miss = []

    def need(cond, label):
        if not cond:
            miss.append(label)

    if code == A.CMD_VENT:
        need(s[A.D_STATE] not in RUNNING_STATES, "공정 중이 아닐 것")
        need(A.bit(i0, A.IN0_IVE_CLOSE), "IV-E 닫힘")
        need(A.bit(i0, A.IN0_EMO), "비상정지 해제")
    elif code == A.CMD_PUMP_START:
        need(A.bit(i0, A.IN0_EMO), "비상정지 해제")
        need(not A.bit(i0, A.IN0_PUMP_ALM), "펌프 알람 해제")
        need(A.bit(i0, A.IN0_AIR), "공압 정상")
        need(A.bit(i0, A.IN0_CW), "냉각수 정상")
    elif code == A.CMD_PROCESS_START:
        need(A.bit(ilk, A.ILK_VALVE_OK), "공정 밸브 허가")
        need(A.bit(ilk, A.ILK_VACUUM), "베이스 압력 도달")
        need(bool(s[A.D_RECIPE_OK]), "레시피 표 통과")
        if DEV.HAS_O3:
            need(A.bit(ilk, A.ILK_O3_OK), "O3 허가")
    else:
        need(not A.bit(ilk, A.ILK_SAFE_STOP_REQ), "안전 정지 요구 해제")
        need(A.bit(ilk, A.ILK_BASIC), "기본 인터락(비상정지·공압·N2·리드·냉각수)")

    # 공통으로 빠지기 쉬운 것
    if not A.bit(ilk, A.ILK_BASIC) and "기본 인터락(비상정지·공압·N2·리드·냉각수)" not in miss:
        for bit, label in ((A.IN0_EMO, "비상정지"), (A.IN0_AIR, "공압"), (A.IN0_N2, "N2"),
                           (A.IN0_LID, "리드 닫힘"), (A.IN0_CW, "냉각수")):
            need(A.bit(i0, bit), label)
    return ("필요: " + " · ".join(miss)) if miss else "PLC 인터락 조건을 확인하세요"


# ===================== 시뮬레이터 조작판 =====================
async def _cmd_sim_fault(data: dict, ws):
    """이상 입력을 켜고 끈다. 시뮬레이터일 때, 이 PC 접속에서만."""
    if not state.sim:
        await push_notice("시뮬레이터가 아닙니다", "warn", ws)
        return
    key = data.get("key") or ""
    on = bool(data.get("on"))
    if key not in state.sim.faults:
        await push_notice(f"알 수 없는 시험 입력입니다: {key}", "warn", ws)
        return
    state.sim.set_fault(key, on)
    await push_log(f"[시뮬레이터] 시험 입력 {key} {'켬' if on else '끔'}", "info")
    await push_state()


# ===================== 종료 =====================
async def _cmd_exit(ws):
    link = state.link
    if link and link.connected and link.status[A.D_STATE] in RUNNING_STATES:
        await push_notice("공정 중에는 프로그램을 종료할 수 없습니다", "warn", ws)
        return
    await push_log("운전자 요청으로 프로그램을 종료합니다", "warn")
    if _shutdown_handler:
        _shutdown_handler()
