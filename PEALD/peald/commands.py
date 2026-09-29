"""
commands.py — 화면 명령 처리.

검사는 세 겹이고 앞의 두 겹은 전부 서버에서 한다.
  1) 권한 : 조작 명령은 이 PC(루프백) 접속에서만 받는다. 원격은 거절하고 로그를 남긴다.
  2) 상태 : PLC 연결이 끊겼거나, PLC 규칙상 지금 받을 수 없는 명령은 미리 거절한다(편의).
  3) PLC  : 최종 판단은 PLC 의 결과 코드다.

★ 2)는 어디까지나 편의다. PC 가 통과시켰다고 안전한 것이 아니라, PLC 가 0 을 돌려줘야
  실제로 처리된 것이다. 그래서 PC 쪽 판정을 근거로 "됐다"고 말하지 않는다.

★ 시간 초과(응답 없음)일 때 같은 명령을 자동으로 다시 보내지 않는다 —
  PLC 가 이미 받았을 수 있어 두 번 실행될 위험이 있다.
"""

import time

from . import addresses as A
from . import device as DEV
from . import logger
from . import recipe as R
from . import storage
from .state import state
from .connection import manager, push_state, push_notice, push_log

# 원격(보기 전용)이 보낼 수 있는 명령. 나머지는 전부 거절한다.
READ_ONLY_CMDS = {"ping"}

# 배기·알람 계열 — 화면 버튼 하나가 PLC 명령 하나에 대응한다.
SIMPLE_CMDS = {
    "pump_start": A.CMD_PUMP_START,
    "pump_stop": A.CMD_PUMP_STOP,
    "vent": A.CMD_VENT,
    "all_close": A.CMD_ALL_CLOSE,
    "alarm_ack": A.CMD_ALARM_ACK,
    "alarm_reset": A.CMD_ALARM_RESET,
    "process_pause": A.CMD_PAUSE,
    "process_resume": A.CMD_RESUME,
    "process_stop_after_cycle": A.CMD_STOP_AFTER_CYCLE,
    "process_abort": A.CMD_ABORT,
}

RUNNING_STATES = (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)
MANUAL_UNLOCK_S = 300.0         # 수동 밸브 잠금 해제가 유지되는 시간

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

    fn = _HANDLERS.get(cmd)
    if fn is not None:
        await fn(data, ws)
        return
    code = SIMPLE_CMDS.get(cmd)
    if code is None:
        await push_notice(f"알 수 없는 명령입니다: {cmd}", "warn", ws)
        return
    if code == A.CMD_ABORT and state.runner:
        state.runner.note_abort()
    await _send_plc(code, ws)


# ===================== PLC 명령 =====================
async def _send_plc(code: int, ws, args: dict = None, what: str = ""):
    name = what or A.CMD_NAMES.get(code, str(code))
    link = state.link
    if not (link and link.connected):
        await push_notice(f"{name}: PLC 에 연결되어 있지 않습니다", "warn", ws)
        return None

    ok, why = precheck(code)
    if not ok:
        await push_notice(f"{name}: {why}", "warn", ws)
        logger.write("warn", f"명령 {name} 사전 거절 — {why}")
        return None

    no = link._cmd_no
    result, text = await link.send_command(code, args)
    origin = getattr(getattr(ws, "client", None), "host", "local") if ws else "local"
    logger.command(name, no, text, origin)

    if result is None:
        await push_notice(f"{name}: {text}", "err", ws)
        await push_log(f"명령 {name} — {text}", "err")
        return None
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
    return result


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
    if code in (A.CMD_PAUSE, A.CMD_STOP_AFTER_CYCLE, A.CMD_ABORT) and st not in RUNNING_STATES:
        return False, "진행 중인 공정이 없습니다"
    if code == A.CMD_RESUME and st != A.STATE_PAUSE:
        return False, "일시정지 중이 아닙니다"

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
    """결과 1(인터락 조건 미달)일 때 무엇이 빠졌는지 비트를 풀어서 알려 준다."""
    link = state.link
    if not (link and link.connected):
        return "PLC 상태를 읽을 수 없습니다"
    s = link.status
    i0 = s[A.D_INPUT0]
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

    if not A.bit(ilk, A.ILK_BASIC) and "기본 인터락(비상정지·공압·N2·리드·냉각수)" not in miss:
        for bit, label in ((A.IN0_EMO, "비상정지"), (A.IN0_AIR, "공압"), (A.IN0_N2, "N2"),
                           (A.IN0_LID, "리드 닫힘"), (A.IN0_CW, "냉각수")):
            need(A.bit(i0, bit), label)
    return ("필요: " + " · ".join(miss)) if miss else "PLC 인터락 조건을 확인하세요"


def _running() -> bool:
    link = state.link
    return bool(link and link.connected and link.status[A.D_STATE] in RUNNING_STATES)


# ===================== 레시피 =====================
async def _cmd_recipe_validate(d, ws):
    """편집할 때마다 불린다 — 계산·검증은 서버 한 곳에서만 한다."""
    rec = d.get("recipe") or {}
    res = R.validate(state.cfg, rec)
    await manager.send_to(ws, {
        "type": "recipe_check", "check": res,
        "summary": R.summarize(state.cfg, rec),
    })


async def _cmd_recipe_load(d, ws):
    name = d.get("name") or ""
    data = storage.load(name)
    if data is None:
        await push_notice(f"레시피를 열 수 없습니다: {name} (다른 장비의 형식일 수 있습니다)",
                          "warn", ws)
        return
    await manager.send_to(ws, {
        "type": "recipe", "name": name, "recipe": data,
        "check": R.validate(state.cfg, data),
        "summary": R.summarize(state.cfg, data),
    })


async def _cmd_recipe_save(d, ws):
    name = (d.get("name") or "").strip()
    rec = d.get("recipe") or {}
    if not storage.valid_name(name):
        await push_notice("레시피 이름에 쓸 수 없는 문자가 있습니다", "warn", ws)
        return
    rec["format"] = DEV.RECIPE_FORMAT
    rec["name"] = name
    res = R.validate(state.cfg, rec)
    if res["errors"]:
        await push_notice(f"검증 오류 {len(res['errors'])}건 — 고친 뒤 저장하세요", "warn", ws)
        return
    # ★ 실행 중인 레시피는 덮어쓸 수 없다(다른 이름으로 저장은 된다).
    if _running() and state.runner and state.runner.recipe_name == name:
        await push_notice("실행 중인 레시피는 덮어쓸 수 없습니다 — 다른 이름으로 저장하세요",
                          "warn", ws)
        return
    rec["modified"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec.setdefault("created", rec["modified"])
    if not storage.save(name, rec):
        await push_notice("레시피를 저장하지 못했습니다 — 폴더 쓰기 권한을 확인하세요", "err", ws)
        return
    await push_log(f"레시피 저장 [{name}] 번호 {R.recipe_number(rec)}", "ok")
    await push_notice(f"저장했습니다: {name}", "ok", ws)
    await push_state()


async def _cmd_recipe_delete(d, ws):
    name = d.get("name") or ""
    if _running() and state.runner and state.runner.recipe_name == name:
        await push_notice("실행 중인 레시피는 삭제할 수 없습니다", "warn", ws)
        return
    if not storage.delete(name):
        await push_notice("레시피를 삭제하지 못했습니다", "warn", ws)
        return
    await push_log(f"레시피 삭제 [{name}]", "warn")
    await push_notice(f"삭제했습니다: {name}", "ok", ws)
    await push_state()


async def _cmd_recipe_rename(d, ws):
    old, new = d.get("name") or "", (d.get("new_name") or "").strip()
    if not storage.valid_name(new):
        await push_notice("새 이름에 쓸 수 없는 문자가 있습니다", "warn", ws)
        return
    if storage.exists(new):
        await push_notice(f"같은 이름이 이미 있습니다: {new}", "warn", ws)
        return
    data = storage.load(old)
    if data is None:
        await push_notice(f"레시피를 열 수 없습니다: {old}", "warn", ws)
        return
    if _running() and state.runner and state.runner.recipe_name == old:
        await push_notice("실행 중인 레시피는 이름을 바꿀 수 없습니다", "warn", ws)
        return
    data["name"] = new
    data["modified"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if not storage.save(new, data):
        await push_notice("이름을 바꾸지 못했습니다", "err", ws)
        return
    storage.delete(old)
    await push_log(f"레시피 이름 변경 [{old}] → [{new}]", "info")
    await push_state()


async def _cmd_recipe_select(d, ws):
    ok, why = state.runner.select(d.get("name") or "")
    if not ok:
        await push_notice(why, "warn", ws)
        return
    await push_state()


async def _cmd_recipe_upload(d, ws):
    """PLC 로 올리기. 공정 중·PLC 끊김이면 거절한다."""
    name = d.get("name") or (state.runner.recipe_name if state.runner else "")
    link = state.link
    if not (link and link.connected):
        await push_notice("PLC 에 연결되어 있지 않습니다", "warn", ws)
        return
    if _running():
        # ★ PLC 는 작업본으로 돌아 영향이 없지만, 화면과 실제가 어긋나 보이므로 막는다.
        await push_notice("공정 중에는 레시피를 올릴 수 없습니다", "warn", ws)
        return
    ok, why = state.runner.select(name)
    if not ok:
        await push_notice(why, "warn", ws)
        return
    res = R.validate(state.cfg, state.runner.recipe)
    if res["errors"]:
        await push_notice(f"검증 오류 {len(res['errors'])}건 — 올릴 수 없습니다", "warn", ws)
        return
    tbl = state.runner.table
    good, detail = await link.upload_recipe(tbl["words"], tbl["checksum"])
    await push_log(f"레시피 올리기 [{name}] 번호 {tbl['number']} — {detail}",
                   "ok" if good else "err")
    await push_notice(f"레시피 올리기: {detail}", "ok" if good else "err", ws)
    await refresh_plc_recipe()
    await push_state()


async def refresh_plc_recipe():
    """지금 PLC 에 올라가 있는 레시피 요약을 갱신한다(역변환 + 이름 찾기)."""
    link = state.link
    if not (link and link.connected):
        state.plc_recipe = {}
        return
    words = await link.read_recipe_area()
    if not words:
        return
    info = R.from_plc_words(words)
    info["name"] = storage.find_by_number(info["number"]) or ""
    info["plc_ok"] = bool(link.status[A.D_RECIPE_OK])
    state.plc_recipe = info


async def _cmd_plc_recipe_read(d, ws):
    await refresh_plc_recipe()
    await push_state()


# ===================== 공정 시작 흐름 =====================
async def _cmd_process_start(d, ws):
    name = d.get("name")
    if name:
        ok, why = state.runner.select(name)
        if not ok:
            await push_notice(why, "warn", ws)
            return
    ok, msg = await state.runner.start(push_log, push_notice)
    await push_notice(msg, "ok" if ok else "warn", ws)
    await push_state()


async def _cmd_process_cancel_wait(d, ws):
    if state.runner.cancel_wait():
        await push_notice("베이스 압력 대기를 취소합니다", "info", ws)
    else:
        await push_notice("대기 중이 아닙니다", "warn", ws)


# ===================== 수동 조작 =====================
async def _cmd_manual_unlock(d, ws):
    """배관도 밸브 조작 잠금 해제. 5분 뒤·공정 시작 때 자동으로 잠긴다."""
    on = bool(d.get("on", True))
    state.manual_unlock_until = (time.monotonic() + MANUAL_UNLOCK_S) if on else 0.0
    await push_log(f"수동 조작 잠금 {'해제' if on else '설정'}", "info")
    await push_state()


def _manual_allowed():
    if _running():
        return False, "공정 중에는 수동 조작을 할 수 없습니다"
    if time.monotonic() >= state.manual_unlock_until:
        return False, "수동 조작이 잠겨 있습니다 — 잠금 해제를 먼저 켜세요"
    return True, ""


async def _cmd_manual_valve(d, ws):
    tag = d.get("tag") or ""
    want = bool(d.get("on"))
    ok, why = _manual_allowed()
    if not ok:
        await push_notice(why, "warn", ws)
        return
    v = next((x for x in DEV.VALVES if x["tag"] == tag), None)
    if v is None or v.get("auto"):
        await push_notice(f"수동으로 다룰 수 없는 밸브입니다: {tag}", "warn", ws)
        return

    link = state.link
    req = link.manual_valve
    new = (req | (1 << v["bit"])) if want else (req & ~(1 << v["bit"]))
    # ★ 전구체와 반응물을 함께 여는 요청은 PC 가 막는다(PLC 도 둘 다 막고 알람을 낸다).
    pre = any(new & (1 << b) for b in DEV.PRECURSOR_VALVE_BITS)
    rea = any(new & (1 << b) for b in DEV.REACTANT_VALVE_BITS)
    if pre and rea:
        await push_notice("전구체 밸브와 반응물 밸브를 함께 열 수 없습니다 — "
                          "PLC 가 둘 다 막고 중대 알람을 냅니다", "warn", ws)
        return

    link.manual_valve = new & DEV.MANUAL_VALVE_MASK
    state.manual_unlock_until = time.monotonic() + MANUAL_UNLOCK_S   # 조작하면 시간 연장
    res = await _send_plc(A.CMD_MANUAL_APPLY, ws, link.manual_args(),
                          what=f"밸브 {tag} {'열기' if want else '닫기'}")
    if res != A.RESULT_OK:
        link.manual_valve = req          # 거절되면 PC 요청도 되돌린다


async def _cmd_manual_mfc(d, ws):
    if _running():
        await push_notice("공정 중에는 MFC 를 바꿀 수 없습니다", "warn", ws)
        return
    vals = d.get("sccm") or {}
    args = {}
    named = []
    for m in state.cfg.get("mfc") or []:
        no = m["no"]
        if str(no) not in vals and no not in vals:
            continue
        v = vals.get(str(no), vals.get(no))
        sc = state.conv.mfc.get(no)
        fs = m.get("full_scale_sccm")
        # ★ 풀스케일을 모르면 원시값을 만들 수 없다 — 0 으로 쓰면 의도와 정반대가 된다.
        if sc is None or sc.full is None:
            await push_notice(f"MFC{no} 풀스케일이 정해지지 않아 설정할 수 없습니다 — "
                              f"설정에서 full_scale_sccm 을 넣으세요", "warn", ws)
            return
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv < 0 or (fs is not None and fv > float(fs)):
            await push_notice(f"MFC{no} 설정이 범위를 벗어납니다 (0~{fs})", "warn", ws)
            return
        args[A.D_MFC_SV + no - 1] = sc.to_raw(fv)
        named.append(f"MFC{no}={fv:g}")
    if not args:
        await push_notice("바꿀 MFC 값이 없습니다", "warn", ws)
        return
    await _send_plc(A.CMD_MFC_APPLY, ws, args, what="MFC 수동 적용 (" + " ".join(named) + ")")


async def _cmd_manual_heater(d, ws):
    if _running():
        await push_notice("공정 중에는 히터를 바꿀 수 없습니다", "warn", ws)
        return
    from .convert import heater_raw
    sv = d.get("sv") or {}
    power = d.get("power") or {}
    link = state.link
    cur_power = link.heater_power
    args = {}
    named = []
    for h in state.cfg.get("heaters") or []:
        ch = h["ch"]
        key = str(ch)
        if key in sv or ch in sv:
            v = sv.get(key, sv.get(ch))
            mx = h.get("max_c")
            if mx is None:
                await push_notice(f"CH{ch} 는 과온 한계가 정해지지 않아 설정할 수 없습니다",
                                  "warn", ws)
                return
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not (0 <= fv <= float(mx)):
                await push_notice(f"CH{ch} 설정 온도는 0~{mx:g} ℃ 이어야 합니다", "warn", ws)
                return
            args[A.D_HEATER_SV + ch - 1] = heater_raw(fv)
            named.append(f"CH{ch}={fv:g}℃")
        if key in power or ch in power:
            on = bool(power.get(key, power.get(ch)))
            bit = 1 << (ch - 1)
            if not (DEV.HEATER_POWER_MASK & bit):
                continue
            cur_power = (cur_power | bit) if on else (cur_power & ~bit)
            named.append(f"CH{ch} {'ON' if on else 'OFF'}")
    cur_power &= DEV.HEATER_POWER_MASK
    args[A.D_HEATER_POWER] = cur_power
    if not named:
        await push_notice("바꿀 히터 값이 없습니다", "warn", ws)
        return
    res = await _send_plc(A.CMD_HEATER_APPLY, ws, args,
                          what="히터 적용 (" + " ".join(named) + ")")
    if res == A.RESULT_OK:
        link.heater_power = cur_power
    elif res is not None:
        # PLC 가 거절했으면 PC 가 기억하는 전원 비트도 PLC 값으로 되돌린다
        await _refresh_heater_power()


async def _refresh_heater_power():
    link = state.link
    if not (link and link.connected):
        return
    try:
        regs = await link.client.read_holding(A.D_HEATER_POWER, 1)
        link.heater_power = regs[0]
    except Exception:  # noqa: BLE001
        pass


async def _cmd_manual_pcv(d, ws):
    if not DEV.HAS_PCV:
        await push_notice("이 장비에는 PCV 가 없습니다", "warn", ws)
        return
    if _running():
        await push_notice("공정 중에는 PCV 를 바꿀 수 없습니다", "warn", ws)
        return
    try:
        pct = float(d.get("pct"))
    except (TypeError, ValueError):
        await push_notice("PCV 목표 값이 올바르지 않습니다", "warn", ws)
        return
    if not (0 <= pct <= 100):
        await push_notice("PCV 목표는 0~100 % 이어야 합니다", "warn", ws)
        return
    args = state.link.manual_args({A.D_PCV_SV: state.conv.pcv.to_raw(pct)})
    await _send_plc(A.CMD_MANUAL_APPLY, ws, args, what=f"PCV 목표 {pct:g} %")


async def _cmd_manual_rf(d, ws):
    """RF 시험 — 대기 중에만. 전력이 0 이면 PLC 가 요청을 지우므로 PC 도 막는다."""
    if not DEV.HAS_RF:
        await push_notice("이 장비에는 RF 가 없습니다", "warn", ws)
        return
    if _running():
        await push_notice("공정 중에는 RF 를 수동으로 켤 수 없습니다", "warn", ws)
        return
    on = bool(d.get("on"))
    link = state.link
    extra = {}
    if on:
        try:
            watt = float(d.get("watt"))
        except (TypeError, ValueError):
            await push_notice("RF 전력 값이 올바르지 않습니다", "warn", ws)
            return
        lim = (state.cfg.get("params") or {}).get("rf_max_w")
        if watt <= 0:
            await push_notice("RF 전력이 0 이면 PLC 가 RF 요청을 지웁니다 — "
                              "전력을 먼저 넣으세요", "warn", ws)
            return
        if lim is not None and watt > float(lim):
            await push_notice(f"RF 전력이 상한을 넘습니다 (최대 {lim:g} W)", "warn", ws)
            return
        extra[A.D_RF_SV] = state.conv.rf.to_raw(watt)
        link.manual_aux |= 1 << A.AUX_RF
    else:
        link.manual_aux &= ~(1 << A.AUX_RF)
    link.manual_aux &= DEV.AUX_CMD_MASK
    await _send_plc(A.CMD_MANUAL_APPLY, ws, link.manual_args(extra),
                    what=f"RF {'켜기' if on else '끄기'}")


async def _cmd_manual_o3(d, ws):
    """O3 라인 — 켜기는 바이패스 펌프·IV-B·발생기를 한꺼번에 요청하고,
    끄기는 발생기만 먼저 끈 뒤 지연을 두고 나머지를 끈다(배관에 남은 O3 를 뺀다)."""
    if not DEV.HAS_O3:
        await push_notice("이 장비에는 O3 라인이 없습니다", "warn", ws)
        return
    if _running():
        await push_notice("공정 중에는 O3 라인을 바꿀 수 없습니다", "warn", ws)
        return
    link = state.link
    action = d.get("action") or "on"

    if action == "set":
        try:
            v = float(d.get("value"))
        except (TypeError, ValueError):
            await push_notice("O3 설정 값이 올바르지 않습니다", "warn", ws)
            return
        lim = (state.cfg.get("params") or {}).get("o3_max")
        if v < 0 or (lim is not None and v > float(lim)):
            await push_notice(f"O3 설정이 상한을 넘습니다 (0~{lim})", "warn", ws)
            return
        await _send_plc(A.CMD_MANUAL_APPLY, ws,
                        link.manual_args({A.D_O3_SV: state.conv.o3.to_raw(v)}),
                        what=f"O3 설정 {v:g}")
        return

    if action == "on":
        try:
            v = float(d.get("value") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v <= 0:
            await push_notice("O3 설정을 먼저 넣으세요 (0 이면 PLC 가 발생기를 막습니다)",
                              "warn", ws)
            return
        link.manual_aux |= (1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB) | (1 << A.AUX_O3_GEN)
        link.manual_aux &= DEV.AUX_CMD_MASK
        await _send_plc(A.CMD_MANUAL_APPLY, ws,
                        link.manual_args({A.D_O3_SV: state.conv.o3.to_raw(v)}),
                        what="O3 라인 켜기")
        await push_log("O3 라인 켜기 — 바이패스 펌프 → IV-B → 5 s 뒤 발생기", "info")
        return

    # 끄기: 발생기 먼저, 지연 뒤 나머지
    link.manual_aux &= ~(1 << A.AUX_O3_GEN)
    link.manual_aux &= DEV.AUX_CMD_MASK
    res = await _send_plc(A.CMD_MANUAL_APPLY, ws, link.manual_args(), what="O3 발생기 끄기")
    if res != A.RESULT_OK:
        return
    delay = float((state.cfg.get("process") or {}).get("o3_off_delay_s") or 10)
    state.o3_off_at = time.monotonic() + delay
    await push_log(f"O3 발생기를 껐습니다 — {delay:g} s 뒤 바이패스 라인을 닫습니다", "info")


async def _cmd_manual_o3_finish(d, ws):
    """화면이 지연을 센 뒤 부른다 — 바이패스 펌프·IV-B 를 끈다."""
    if not DEV.HAS_O3 or _running():
        return
    link = state.link
    link.manual_aux &= ~((1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB))
    link.manual_aux &= DEV.AUX_CMD_MASK
    await _send_plc(A.CMD_MANUAL_APPLY, ws, link.manual_args(), what="O3 바이패스 라인 닫기")


# ===================== 시뮬레이터 조작판 =====================
async def _cmd_sim_fault(d, ws):
    if not state.sim:
        await push_notice("시뮬레이터가 아닙니다", "warn", ws)
        return
    key = d.get("key") or ""
    on = bool(d.get("on"))
    if key not in state.sim.faults:
        await push_notice(f"알 수 없는 시험 입력입니다: {key}", "warn", ws)
        return
    state.sim.set_fault(key, on)
    await push_log(f"[시뮬레이터] 시험 입력 {key} {'켬' if on else '끔'}", "info")
    await push_state()


async def _cmd_alarm_popup_close(d, ws):
    state.alarm_popup = False


# ===================== 종료 =====================
async def _cmd_exit(d, ws):
    if _running():
        await push_notice("공정 중에는 프로그램을 종료할 수 없습니다", "warn", ws)
        return
    await push_log("운전자 요청으로 프로그램을 종료합니다", "warn")
    if _shutdown_handler:
        _shutdown_handler()


_HANDLERS = {
    "exit": _cmd_exit,
    "sim_fault": _cmd_sim_fault,
    "alarm_popup_close": _cmd_alarm_popup_close,
    # 레시피
    "recipe_validate": _cmd_recipe_validate,
    "recipe_load": _cmd_recipe_load,
    "recipe_save": _cmd_recipe_save,
    "recipe_delete": _cmd_recipe_delete,
    "recipe_rename": _cmd_recipe_rename,
    "recipe_select": _cmd_recipe_select,
    "recipe_upload": _cmd_recipe_upload,
    "plc_recipe_read": _cmd_plc_recipe_read,
    # 공정
    "process_start": _cmd_process_start,
    "process_cancel_wait": _cmd_process_cancel_wait,
    # 수동
    "manual_unlock": _cmd_manual_unlock,
    "manual_valve": _cmd_manual_valve,
    "manual_mfc": _cmd_manual_mfc,
    "manual_heater": _cmd_manual_heater,
    "manual_pcv": _cmd_manual_pcv,
    "manual_rf": _cmd_manual_rf,
    "manual_o3": _cmd_manual_o3,
    "manual_o3_finish": _cmd_manual_o3_finish,
}
