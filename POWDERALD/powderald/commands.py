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

import time
import asyncio

from . import addresses as A
from . import device as DEV
from . import logger
from . import recipe as R
from . import storage
from . import settings
from .admin import admin
from .state import state
from .connection import manager, push_state, push_notice, push_log

# 원격(보기 전용)이 보낼 수 있는 명령. 나머지는 전부 거절한다.
# 레시피 불러오기 · 검증(요약 포함)은 파일을 바꾸지 않는 읽기라 원격에서도 된다 —
# 저장 · 올리기 · 삭제 · 이름 바꾸기 · 선택 · 시작은 그대로 거절한다.
READ_ONLY_CMDS = {"ping", "recipe_load", "recipe_validate", "alarm_history"}

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
    cmd = (data or {}).get("cmd") if isinstance(data, dict) else None
    # ★ 명령 이름은 문자열이고 등록된 이름일 때만 처리한다. 로그에는 앞 40자만, 제어 문자 없이,
    #   같은 연결에서 초당 한 번만 남긴다(거대한·개행 섞인 이름으로 로그를 부풀리거나 꾸미지 못하게).
    if not isinstance(cmd, str) or not cmd:
        return
    shown = logger.clean(cmd, 40)

    local = manager.is_local_ws(ws) if ws is not None else True
    if not local and cmd not in READ_ONLY_CMDS:
        host = logger.clean(getattr(getattr(ws, "client", None), "host", "?"), 60)
        manager.log_limited(ws, "warn", f"원격 조작 명령 거절: {shown} ({host})")
        await push_notice("원격 접속은 보기 전용입니다 — 조작은 장비 PC에서 하세요", "warn", ws)
        return
    if cmd not in _HANDLERS and cmd not in SIMPLE_CMDS and cmd not in READ_ONLY_CMDS:
        manager.log_limited(ws, "warn", f"알 수 없는 명령 무시: {shown}")
        await push_notice(f"알 수 없는 명령입니다: {shown}", "warn", ws)
        return
    if cmd == "ping":
        return
    from . import loops
    loops.note_work(f"명령 {cmd}")          # 권한 확인 뒤, 등록된 이름으로만

    fn = _HANDLERS.get(cmd)
    if fn is not None:
        await fn(data, ws)
        return
    code = SIMPLE_CMDS.get(cmd)
    if code is None:
        await push_notice(f"알 수 없는 명령입니다: {cmd}", "warn", ws)
        return
    if code == A.CMD_ABORT and state.runner:
        await _abort(ws)
        return
    if code in (A.CMD_VENT, A.CMD_PUMP_STOP, A.CMD_ALL_CLOSE) and state.runner:
        # ★ 베이스 압력 대기 중에 벤트 · 펌핑 정지 · 전체 닫기를 보내면 대기를 먼저 취소한다 —
        #   대기가 살아 있으면 나중에 누가 펌핑할 때 운전자 없이 공정이 시작된다
        state.runner.cancel_wait(A.CMD_NAMES.get(code, cmd))
    result = await _send_plc(code, ws)
    if code == A.CMD_STOP_AFTER_CYCLE and result == A.RESULT_OK and state.runner:
        state.runner.note_stop_after_cycle()


async def _abort(ws):
    """즉시 중단. ★ '운전자 중단'은 PLC 결과가 0(처리됨)이고 보낼 때 공정 중이었을 때만 적는다 —
    이미 끝났거나 안전 정지된 뒤에 누른 중단이 끝 기록(정상 종료·안전 정지 사유)을 덮지 않게."""
    runner, link = state.runner, state.link

    async def sender():
        # 상태 영역은 최대 한 주기 늦다 — 보내기 직전에 장비 상태를 새로 읽는다
        try:
            cur = (await link.client.read_holding(A.D_STATE, 1))[0]
        except Exception:  # noqa: BLE001
            cur = link.status[A.D_STATE]
        if cur not in RUNNING_STATES:
            return {"refused": True, "text": "진행 중인 공정이 없습니다 (이미 끝났습니다)"}
        runner.abort_begin()
        done = False
        try:
            result, text = await link.send_command(A.CMD_ABORT)
            done = result == A.RESULT_OK
        finally:
            runner.abort_result(done)
        return {"result": result, "text": text}

    await _send_plc(A.CMD_ABORT, ws, sender=sender)


# ===================== PLC 명령 =====================
async def _send_plc(code: int, ws, args: dict = None, what: str = "", sender=None):
    """사전 판정 → 보내기 → 결과 알림. sender 가 있으면 그것으로 보낸다
    (명령 12·13·14 처럼 잠금 안에서 PLC 값을 새로 읽고 써야 하는 것).
    sender() → {result, text, refused?}. refused 면 PLC 에 보내지 않은 것이다."""
    name = what or A.CMD_NAMES.get(code, str(code))
    link = state.link
    if not (link and link.connected):
        await push_notice(f"{name}: PLC 에 연결되어 있지 않습니다", "warn", ws)
        return None

    ok, why = precheck(code)
    if not ok:
        await push_notice(f"{name}: {why}", "err" if ("다른 장비" in why or "장비 ID" in why) else "warn", ws)
        logger.write("warn", f"명령 {name} 사전 거절 — {why}")
        return None

    no = link._cmd_no
    if sender is None:
        result, text = await link.send_command(code, args)
    else:
        res = await sender()
        if res.get("refused"):
            await push_notice(f"{name}: {res['text']}", "warn", ws)
            logger.write("warn", f"명령 {name} 사전 거절 — {res['text']}")
            return None
        result, text = res.get("result"), res.get("text", "")
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


# 펌핑 시작 결과 1 · 벤트 요청을 PLC 가 바로 지우는 래치 알람
PUMP_BLOCK_ALARMS = ((A.ALM0_PUMP, "펌프 알람"), (A.ALM0_AIR, "공압 저하"),
                     (A.ALM0_CW, "냉각수 이상"))


def _pump_block_reasons(s) -> list:
    """PLC 와 같은 기준 — 비상정지는 입력(D00008 b0), 나머지는 래치된 알람."""
    out = []
    if not A.bit(s[A.D_INPUT0], A.IN0_EMO):
        out.append("비상정지 입력")
    for bit, label in PUMP_BLOCK_ALARMS:
        if A.bit(s[A.D_ALARM0], bit):
            out.append(label + " 래치")
    return out


def precheck(code: int):
    """PLC 규칙과 같은 기준으로 미리 거른다. (통과여부, 이유)"""
    link = state.link
    if not (link and link.connected):
        return False, "PLC 에 연결되어 있지 않습니다"
    blocked = link.id_block_text() if hasattr(link, "id_block_text") else ""
    if blocked:
        return False, blocked
    s = link.status
    st = s[A.D_STATE]
    ilk = s[A.D_INTERLOCK]

    if st in RUNNING_STATES and code in (9, 10, 11, 12, 13, 14):
        return False, f"공정 중({A.STATE_NAMES.get(st, st)})에는 할 수 없습니다"
    if code in (A.CMD_PAUSE, A.CMD_STOP_AFTER_CYCLE, A.CMD_ABORT) and st not in RUNNING_STATES:
        return False, "진행 중인 공정이 없습니다"
    if code == A.CMD_PAUSE and st == A.STATE_PAUSE:
        return False, "이미 일시정지 중입니다"
    if code == A.CMD_RESUME and st != A.STATE_PAUSE:
        return False, "일시정지 중이 아닙니다"

    if code == A.CMD_PUMP_START:
        blocking = _pump_block_reasons(s)
        if blocking:
            return False, "막혀 있습니다 — " + " · ".join(blocking)
    if code == A.CMD_VENT:
        blocking = _pump_block_reasons(s)
        if blocking:
            return False, ("PLC 가 벤트 요청을 바로 지웁니다 — " + " · ".join(blocking)
                           + " (원인을 없애고 알람 리셋 뒤에 하세요)")
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

    if code in (A.CMD_VENT, A.CMD_PUMP_START):
        if code == A.CMD_VENT:
            need(s[A.D_STATE] not in RUNNING_STATES, "공정 중이 아닐 것")
        need(A.bit(i0, A.IN0_EMO), "비상정지 입력 정상")
        for bit, label in PUMP_BLOCK_ALARMS:
            need(not A.bit(s[A.D_ALARM0], bit), label + " 래치 해제(알람 리셋)")
    elif code == A.CMD_PROCESS_START:
        need(A.bit(ilk, A.ILK_VALVE_OK), "공정 밸브 허가")
        need(A.bit(ilk, A.ILK_VACUUM), "베이스 압력 도달")
        need(bool(s[A.D_RECIPE_OK]), "레시피 표 통과")
        if DEV.HAS_O3:
            need(A.bit(ilk, A.ILK_O3_OK), "O3 허가")
    else:
        need(not A.bit(ilk, A.ILK_SAFE_STOP_REQ), "안전 정지 요구 해제")
        need(A.bit(ilk, A.ILK_BASIC), "기본 인터락(비상정지·공압·N2·리드·냉각수)")

    if (code not in (A.CMD_VENT, A.CMD_PUMP_START) and not A.bit(ilk, A.ILK_BASIC)
            and "기본 인터락(비상정지·공압·N2·리드·냉각수)" not in miss):
        for bit, label in ((A.IN0_EMO, "비상정지"), (A.IN0_AIR, "공압"), (A.IN0_N2, "N2"),
                           (A.IN0_LID, "리드 닫힘"), (A.IN0_CW, "냉각수")):
            need(A.bit(i0, bit), label)
    return ("필요: " + " · ".join(miss)) if miss else "PLC 인터락 조건을 확인하세요"


def _running() -> bool:
    link = state.link
    return bool(link and link.connected and link.status[A.D_STATE] in RUNNING_STATES)


# ===================== 레시피 =====================
def _check_payload(cfg, recipe) -> dict:
    """검증 + 요약 — 작업 스레드에서 돈다(이벤트 루프를 붙잡지 않게)."""
    rec = R.upgrade(recipe or {})
    return {"type": "recipe_check", "check": R.validate(cfg, rec), "summary": R.summarize(cfg, rec)}


def _with_req(out: dict, req) -> dict:
    """화면이 붙인 요청 번호를 그대로 돌려준다 — 화면은 마지막 요청의 답만 쓴다(늦게 온 옛 답 무시).
    정수(0~2^31)만 받는다."""
    out["req"] = req if isinstance(req, int) and not isinstance(req, bool) and 0 <= req < 2 ** 31 else None
    return out


def _load_payload(cfg, name):
    """파일 읽기 + 검증 + 요약 — 작업 스레드에서. 열 수 없으면 None."""
    data = storage.load(name)
    if data is None:
        return None
    return {"type": "recipe", "name": name, "recipe": data,
            "check": R.validate(cfg, data), "summary": R.summarize(cfg, data)}


async def _cmd_recipe_validate(d, ws):
    """편집할 때마다 불린다 — 계산·검증은 서버 한 곳에서만 한다.
    ★ 작업 스레드에서 돌리고, 한 연결에서는 한 번에 하나만. 도는 동안 온 요청은 가장 최근 것 하나만
      남긴다(편집기는 마지막 결과만 쓴다) — 쏟아지는 검증 요청이 루프·스레드를 붙잡지 못하게."""
    meta = manager.active.get(ws)
    if meta is None or "q" not in meta:
        # 대기열이 없는 연결(시험) — 바로 돌려준다
        out = await asyncio.to_thread(_check_payload, state.cfg, d.get("recipe"))
        await manager.send_to(ws, _with_req(out, d.get("req")))
        return
    if meta.get("v_busy"):
        meta["v_next"] = d
        return
    meta["v_busy"] = True
    asyncio.create_task(_validate_worker(ws, meta, d))


async def _validate_worker(ws, meta, d):
    try:
        while d is not None and not meta.get("closed"):
            out = _with_req(await asyncio.to_thread(_check_payload, state.cfg, d.get("recipe")), d.get("req"))
            await manager.send_to(ws, out)
            d = meta.pop("v_next", None)
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"레시피 검증 오류: {type(e).__name__}: {logger.clean(e, 200)}")
    finally:
        meta["v_busy"] = False
        meta.pop("v_next", None)


async def _cmd_recipe_load(d, ws):
    name = d.get("name") if isinstance(d.get("name"), str) else ""
    out = await asyncio.to_thread(_load_payload, state.cfg, name)
    if out is None:
        await push_notice(f"레시피를 열 수 없습니다: {logger.clean(name, 80)} (다른 장비의 형식일 수 있습니다)",
                          "warn", ws)
        return
    await manager.send_to(ws, out)


async def _cmd_recipe_save(d, ws):
    """저장. ★ 결과(저장한 이름 · 거절 이유)를 그 연결에 recipe_saved 로 돌려준다 — 화면은 이 답을
    받은 뒤에만 '저장됨'으로 바꾼다(거절된 저장이 저장된 것처럼 보이지 않게)."""
    name = d.get("name") if isinstance(d.get("name"), str) else ""
    name = name.strip()
    ok, why, level, rec = _save_recipe(name, d.get("recipe"))
    if not ok:
        await push_notice(why, level, ws)
        await manager.send_to(ws, {"type": "recipe_saved", "ok": False, "name": name, "why": why})
        return
    await push_log(f"레시피 저장 [{name}] 번호 {R.recipe_number(rec)}", "ok")
    await push_notice(f"저장했습니다: {name}", "ok", ws)
    await manager.send_to(ws, {"type": "recipe_saved", "ok": True, "name": name,
                               "number": R.recipe_number(rec)})
    await push_state()


def _save_recipe(name: str, recipe):
    """(성공, 이유, 알림 등급, 저장한 레시피)."""
    # 저장은 새 키로만 — 옛 키(반복 그룹 from / to)는 여기서 바꾼다
    rec = R.upgrade(recipe or {})
    if not isinstance(rec, dict):
        return False, "레시피 형식이 올바르지 않습니다", "warn", None
    if not storage.valid_name(name):
        return False, "레시피 이름에 쓸 수 없는 문자가 있습니다", "warn", None
    why = _flow_block(name)
    if why:
        return False, why, "warn", None
    rec["format"] = DEV.RECIPE_FORMAT
    rec["name"] = name
    res = R.validate(state.cfg, rec)
    if res["errors"]:
        return False, f"검증 오류 {len(res['errors'])}건 — 고친 뒤 저장하세요", "warn", None
    # ★ 실행 중인 레시피는 덮어쓸 수 없다(다른 이름으로 저장은 된다).
    if _running() and state.runner and state.runner.active_name == name:
        return False, "실행 중인 레시피는 덮어쓸 수 없습니다 — 다른 이름으로 저장하세요", "warn", None
    rec["modified"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec.setdefault("created", rec["modified"])
    if not storage.save(name, rec):
        return False, "레시피를 저장하지 못했습니다 — 폴더 쓰기 권한을 확인하세요", "err", None
    return True, "", "ok", rec


async def _cmd_recipe_delete(d, ws):
    """삭제. ★ 결과를 그 연결에 recipe_deleted {ok, name, why} 로 돌려준다 — 화면은 ok 일 때만
    편집기를 비운다(거절되면 고치던 내용을 잃지 않게)."""
    name = d.get("name") if isinstance(d.get("name"), str) else ""
    ok, why, level = _delete_recipe(name)
    await push_notice(f"삭제했습니다: {name}" if ok else why, "ok" if ok else level, ws)
    await manager.send_to(ws, {"type": "recipe_deleted", "ok": ok, "name": name, "why": "" if ok else why})
    if ok:
        await push_log(f"레시피 삭제 [{name}]", "warn")
        await push_state()


def _delete_recipe(name: str):
    why = _flow_block(name)
    if why:
        return False, why, "warn"
    if _running() and state.runner and state.runner.active_name == name:
        return False, "실행 중인 레시피는 삭제할 수 없습니다", "warn"
    if not storage.delete(name):
        return False, "레시피를 삭제하지 못했습니다", "warn"
    return True, "", "ok"


async def _cmd_recipe_rename(d, ws):
    """이름 바꾸기. ★ 결과를 그 연결에 recipe_renamed {ok, old, new, why} 로 — 화면은 ok 일 때만
    이름을 바꾼다(거절된 이름으로 다음 저장이 남의 레시피를 덮지 않게)."""
    old = d.get("name") if isinstance(d.get("name"), str) else ""
    new = (d.get("new_name") if isinstance(d.get("new_name"), str) else "").strip()
    ok, why, level = _rename_recipe(old, new)
    if ok:
        await push_log(f"레시피 이름 변경 [{old}] → [{new}]", "info")
    else:
        await push_notice(why, level, ws)
    await manager.send_to(ws, {"type": "recipe_renamed", "ok": ok, "old": old, "new": new,
                               "why": "" if ok else why})
    if ok:
        await push_state()


def _rename_recipe(old: str, new: str):
    why = _flow_block(old)
    if why:
        return False, why, "warn"
    if not storage.valid_name(new):
        return False, "새 이름에 쓸 수 없는 문자가 있습니다", "warn"
    if storage.exists(new):
        return False, f"같은 이름이 이미 있습니다: {new}", "warn"
    data = storage.load(old)
    if data is None:
        return False, f"레시피를 열 수 없습니다: {old}", "warn"
    if _running() and state.runner and state.runner.active_name == old:
        return False, "실행 중인 레시피는 이름을 바꿀 수 없습니다", "warn"
    data["name"] = new
    data["modified"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if not storage.save(new, data):
        return False, "이름을 바꾸지 못했습니다", "err"
    storage.delete(old)
    return True, "", "ok"


async def _cmd_recipe_select(d, ws):
    why = _flow_block()
    if why:
        await push_notice(why, "warn", ws)
        return
    ok, why = state.runner.select(d.get("name") or "")
    if not ok:
        await push_notice(why, "warn", ws)
        return
    await push_state()


async def _cmd_recipe_upload(d, ws):
    """PLC 로 올리기. 공정 중·PLC 끊김이면 거절한다."""
    name = d.get("name") or (state.runner.recipe_name if state.runner else "")
    why = _flow_block()
    if why:
        await push_notice(why, "warn", ws)
        return
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
    """시작 흐름을 백그라운드로 띄우고 바로 돌아온다 — 같은 연결의 다음 명령(대기 취소 등)이
    바로 처리돼야 한다. 결과는 흐름이 끝날 때 알린다."""
    if state.runner.busy:
        await push_notice("이미 시작 절차가 진행 중입니다", "warn", ws)
        return
    name = d.get("name")
    if name:
        ok, why = state.runner.select(name)
        if not ok:
            await push_notice(why, "warn", ws)
            return

    async def notify(msg, ok):
        await push_notice(msg, "ok" if ok else "warn", ws)
        await push_state()

    ok, msg = state.runner.begin(push_log, push_notice, notify)
    await push_notice(msg, "info" if ok else "warn", ws)
    await push_state()


def _flow_block(name: str = None) -> str:
    """시작 흐름 동안 막는 조작의 이유(없으면 ''). name 을 주면 그 레시피가 지금 선택된 것일 때만."""
    r = state.runner
    if not (r and r.busy):
        return ""
    if name is not None and name != r.recipe_name:
        return ""
    return "공정 시작 절차가 진행 중입니다 — 끝나거나 취소한 뒤에 하세요"


async def _cmd_alarm_history(d, ws):
    """알람 이력(읽기 전용 — 원격에서도). live 의 alarm_hist_ver 가 바뀌면 화면이 다시 받는다."""
    await manager.send_to(ws, {"type": "alarm_history", "items": list(state.alarms.history),
                               "ver": state.alarms.ver})


async def _cmd_process_cancel_wait(d, ws):
    if state.runner.cancel_wait():
        await push_notice("베이스 압력 대기를 취소합니다", "info", ws)
    else:
        await push_notice("대기 중이 아닙니다", "warn", ws)


# ===================== 수동 조작 =====================
# ★ PC 는 수동 요청을 따로 기억하지 않는다. 명령 12·14 는 보낼 때마다 PLC 반영 영역
#   (D04012~D04131)을 잠금 안에서 새로 읽고, 거기에 이번 변경만 얹어 전부 쓴다.
#   밸브 하나를 눌러도 PCV·RF·O3·다른 MFC 는 지금 값 그대로여야 한다.
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


async def _manual_send(ws, what: str, mutate):
    """명령 12 — 반영 영역 기준 + 이번 변경. 결과 0 이어도 반영 안 된 비트가 있으면 알린다."""
    link = state.link
    box = {}

    async def sender():
        r = await link.manual_apply(mutate)
        box.update(r)
        return r

    res = await _send_plc(A.CMD_MANUAL_APPLY, ws, what=what, sender=sender)
    if res == A.RESULT_OK and (box.get("lost_valve") or box.get("lost_aux")):
        from .plclink import describe_bits
        msg = (f"{what}: PLC 에 반영되지 않았습니다 — "
               f"{describe_bits(box['lost_valve'], box['lost_aux'])} "
               f"({_lost_reason(box)})")
        await push_notice(msg, "warn", ws)
        await push_log(msg, "warn")
    return res, box


def _lost_reason(box) -> str:
    """명령 12 는 받았는데 반영 영역에 없는 비트의 이유."""
    link = state.link
    s = link.status
    why = []
    if box.get("lost_valve") and A.bit(s[A.D_INPUT0], A.IN0_ATM):
        why.append("챔버 대기압 입력이면 PLC 가 밸브 요청을 바로 지웁니다")
    if box.get("lost_aux") and DEV.HAS_RF and not (box.get("sent") or {}).get("rf"):
        why.append("RF 전력이 0 이면 PLC 가 RF 요청을 버립니다")
    if A.bit(s[A.D_INTERLOCK], A.ILK_SAFE_STOP_REQ):
        why.append("안전 정지 요구 중")
    return " · ".join(why) if why else link.clear_reason(bool(box.get("lost_valve")))


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

    def mutate(cur):
        bit = 1 << v["bit"]
        new = (cur["valve"] | bit) if want else (cur["valve"] & ~bit)
        # ★ 전구체와 반응물을 함께 여는 요청은 PC 가 막는다(PLC 도 둘 다 막고 알람을 낸다).
        pre = any(new & (1 << b) for b in DEV.PRECURSOR_VALVE_BITS)
        rea = any(new & (1 << b) for b in DEV.REACTANT_VALVE_BITS)
        if pre and rea:
            return None, ("전구체 밸브와 반응물 밸브를 함께 열 수 없습니다 — "
                          "PLC 가 둘 다 막고 중대 알람을 냅니다")
        cur["valve"] = new
        return cur, ""

    state.manual_unlock_until = time.monotonic() + MANUAL_UNLOCK_S   # 조작하면 시간 연장
    await _manual_send(ws, f"밸브 {tag} {'열기' if want else '닫기'}", mutate)


async def _cmd_manual_mfc(d, ws):
    if _running():
        await push_notice("공정 중에는 MFC 를 바꿀 수 없습니다", "warn", ws)
        return
    vals = d.get("sccm") or {}
    changes = {}
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
        changes[no] = sc.to_raw(fv)
        named.append(f"MFC{no}={fv:g}")
    if not changes:
        await push_notice("바꿀 MFC 값이 없습니다", "warn", ws)
        return

    async def sender():
        r, t = await state.link.mfc_apply(changes)
        return {"result": r, "text": t}

    await _send_plc(A.CMD_MFC_APPLY, ws, what="MFC 수동 적용 (" + " ".join(named) + ")",
                    sender=sender)


def _tc_comm_ok(h) -> bool:
    """그 채널 온도조절기 국번의 통신 정상 비트(D00054)."""
    link = state.link
    if not (link and link.connected):
        return False
    station = int(h.get("station") or ((int(h["ch"]) - 1) // 4 + 1))
    return A.bit(link.status[A.D_TC_COMM], station - 1)


TC_NO_COMM_TEXT = "온도조절기 통신이 없어 PLC 과온 감시가 동작하지 않습니다 — 전원을 켤 수 없습니다"


def _ot_latched() -> bool:
    link = state.link
    return bool(link and link.connected and A.bit(link.status[A.D_ALARM0], A.ALM0_OT))


async def _cmd_manual_heater(d, ws):
    if _running():
        await push_notice("공정 중에는 히터를 바꿀 수 없습니다", "warn", ws)
        return
    from .convert import heater_raw
    sv = d.get("sv") or {}
    power = d.get("power") or {}
    sv_changes = {}
    pw_changes = {}
    named = []
    no_comm_sv = []
    for h in state.cfg.get("heaters") or []:
        ch = h["ch"]
        key = str(ch)
        mx = h.get("max_c")
        if key in sv or ch in sv:
            v = sv.get(key, sv.get(ch))
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
            sv_changes[ch] = heater_raw(fv)
            named.append(f"CH{ch}={fv:g}℃")
            if not _tc_comm_ok(h):
                no_comm_sv.append(f"CH{ch}")
        if key in power or ch in power:
            on = bool(power.get(key, power.get(ch)))
            bit = 1 << (ch - 1)
            if not (DEV.HEATER_POWER_MASK & bit):
                continue
            if on and mx is None:
                # ★ PLC 는 한계 0 인 채널을 막지 않는다(소프트 과온 감시만 안 한다).
                #   감시 없는 히터를 켜지 않도록 PC 가 막는다.
                await push_notice(f"CH{ch} 는 과온 한계가 정해지지 않아 켤 수 없습니다 — "
                                  f"설정에서 max_c 를 넣으세요", "warn", ws)
                return
            if on and not _tc_comm_ok(h):
                # ★ PLC 과온 감시(현재값 > 한계)는 온도조절기 통신으로 읽은 현재값이 있어야 동작한다.
                #   통신이 없는 채널은 하드웨어 과온 스위치 말고 보호가 없다.
                await push_notice(f"CH{ch}: {TC_NO_COMM_TEXT}", "warn", ws)
                return
            if on and _ot_latched():
                await push_notice("과온 알람이 래치돼 있어 히터를 켤 수 없습니다 — "
                                  "PLC 가 매 스캔 전원을 끕니다. 원인을 없애고 알람 리셋 뒤에 켜세요",
                                  "warn", ws)
                return
            pw_changes[bit] = on
            named.append(f"CH{ch} {'ON' if on else 'OFF'}")
    if not named:
        await push_notice("바꿀 히터 값이 없습니다", "warn", ws)
        return

    def mutate(cur_power, cur_sv):
        p = cur_power
        for bit, on in pw_changes.items():
            p = (p | bit) if on else (p & ~bit)
        for ch, raw in sv_changes.items():
            cur_sv[ch - 1] = raw
        return p & DEV.HEATER_POWER_MASK, cur_sv, ""

    box = {}

    async def sender():
        r = await state.link.heater_apply(mutate)
        box.update(r)
        return r

    res = await _send_plc(A.CMD_HEATER_APPLY, ws, what="히터 적용 (" + " ".join(named) + ")",
                          sender=sender)
    if res == A.RESULT_OK and no_comm_sv:
        why = (f"{' · '.join(no_comm_sv)} 설정 온도는 PLC 에 기록했지만 온도조절기 통신이 없어 "
               f"온도조절기에 전달되지 않았습니다")
        await push_notice(why, "warn", ws)
        await push_log(why, "warn")
    if box.get("reverted"):
        await push_log("히터 적용이 거절돼 D01010·D01012~ 를 이전 값으로 되돌렸습니다", "warn")


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
    raw = state.conv.pcv.to_raw(pct)

    def mutate(cur):
        cur["pcv"] = raw
        return cur, ""

    await _manual_send(ws, f"PCV 목표 {pct:g} %", mutate)


async def _cmd_manual_rf(d, ws):
    """RF 시험 — 대기 중에만. 전력이 0 이면 PLC 가 요청을 버리므로 PC 도 막는다."""
    if not DEV.HAS_RF:
        await push_notice("이 장비에는 RF 가 없습니다", "warn", ws)
        return
    if _running():
        await push_notice("공정 중에는 RF 를 수동으로 켤 수 없습니다", "warn", ws)
        return
    on = bool(d.get("on"))
    raw = None
    if on:
        try:
            watt = float(d.get("watt"))
        except (TypeError, ValueError):
            await push_notice("RF 전력 값이 올바르지 않습니다", "warn", ws)
            return
        lim = (state.cfg.get("params") or {}).get("rf_max_w")
        if watt <= 0:
            await push_notice("RF 전력이 0 이면 PLC 가 RF 요청을 버립니다 — "
                              "전력을 먼저 넣으세요", "warn", ws)
            return
        if lim is not None and watt > float(lim):
            await push_notice(f"RF 전력이 상한을 넘습니다 (최대 {lim:g} W)", "warn", ws)
            return
        raw = state.conv.rf.to_raw(watt)

    def mutate(cur):
        if on:
            cur["aux"] |= 1 << A.AUX_RF
            cur["rf"] = raw
        else:
            cur["aux"] &= ~(1 << A.AUX_RF)
        return cur, ""

    await _manual_send(ws, f"RF {'켜기' if on else '끄기'}", mutate)


O3_LINE_BITS = ((1 << A.AUX_BYPASS_PUMP) | (1 << A.AUX_IVB)) if DEV.HAS_O3 else 0


async def _cmd_manual_o3(d, ws):
    """O3 라인 — 켜기는 바이패스 펌프·IV-B·발생기를 한꺼번에 요청하고,
    끄기는 발생기만 먼저 끈 뒤 지연을 두고 나머지를 끈다(배관에 남은 O3 를 뺀다).
    ★ 지연은 서버 타이머가 센다 — 화면을 닫거나 새로 고쳐도 마무리된다."""
    if not DEV.HAS_O3:
        await push_notice("이 장비에는 O3 라인이 없습니다", "warn", ws)
        return
    if _running():
        await push_notice("공정 중에는 O3 라인을 바꿀 수 없습니다", "warn", ws)
        return
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
        raw = state.conv.o3.to_raw(v)

        def set_mutate(cur):
            cur["o3"] = raw
            return cur, ""

        await _manual_send(ws, f"O3 설정 {v:g}", set_mutate)
        return

    if action == "on":
        try:
            v = float(d.get("value") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v <= 0:
            await push_notice("O3 설정을 먼저 넣으세요 (0 이면 발생기가 켜져도 O3 가 나오지 않습니다)",
                              "warn", ws)
            return
        # ★ 래더: O3 한계(PRM_O3_MAX)가 0 이면 O3 허가(인터락 b9)가 나지 않아 발생기가 켜지지 않는다.
        #   설정값이 아니라 PLC 에서 되읽은 값을 본다.
        back = state.link.prm_readback.get(A.D_PRM_O3_MAX) if state.link else None
        if not back:
            await push_notice("PLC 의 O3 한계(PRM_O3_MAX)가 0 이라 O3 허가가 나지 않습니다 — "
                              "설정의 params.o3_max 를 넣고 PLC 파라미터가 맞춰졌는지 확인하세요",
                              "warn", ws)
            return
        _cancel_o3_timer()
        raw = state.conv.o3.to_raw(v)

        def on_mutate(cur):
            cur["aux"] |= O3_LINE_BITS | (1 << A.AUX_O3_GEN)
            cur["o3"] = raw
            return cur, ""

        res, _ = await _manual_send(ws, "O3 라인 켜기", on_mutate)
        if res == A.RESULT_OK:
            await push_log("O3 라인 켜기 — 바이패스 펌프 → IV-B → 5 s 뒤 발생기", "info")
        return

    # 끄기: 발생기 먼저, 지연 뒤 나머지
    def off_mutate(cur):
        cur["aux"] &= ~(1 << A.AUX_O3_GEN)
        return cur, ""

    res, _ = await _manual_send(ws, "O3 발생기 끄기", off_mutate)
    if res != A.RESULT_OK:
        return
    delay = float((state.cfg.get("process") or {}).get("o3_off_delay_s") or 10)
    _cancel_o3_timer()
    state.o3_off_at = time.monotonic() + delay
    state.o3_off_task = asyncio.create_task(_o3_finish_later(delay))
    await push_log(f"O3 발생기를 껐습니다 — {delay:g} s 뒤 바이패스 라인을 닫습니다", "info")


def _cancel_o3_timer():
    t = getattr(state, "o3_off_task", None)
    if t and not t.done():
        t.cancel()
    state.o3_off_task = None
    state.o3_off_at = 0.0


async def _o3_finish_later(delay: float):
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    state.o3_off_at = 0.0
    state.o3_off_task = None
    try:
        await o3_finish()
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"O3 바이패스 라인 닫기 오류: {type(e).__name__}: {e}")


async def o3_finish():
    """지연이 끝나면 바이패스 펌프·IV-B 를 끈다. 그 사이 공정이 시작됐거나
    PLC 가 라인을 지웠으면 취소하고 로그만 남긴다."""
    link = state.link
    if not (link and link.connected):
        await push_log("O3 바이패스 라인 닫기 취소 — PLC 연결이 끊겼습니다", "warn")
        return
    if _running():
        await push_log("O3 바이패스 라인 닫기 취소 — 공정이 시작됐습니다", "warn")
        return
    if state.runner and state.runner.busy:
        # ★ 시작 흐름(올리기 · 베이스 압력 대기 · 시작 명령) 중 — 닫으면 O3 허가가 빠져 시작이 거절되거나
        #   시작 직후 b3 로 중단된다
        await push_log("O3 바이패스 라인 닫기 취소 — 공정 시작 절차가 진행 중입니다", "warn")
        return

    def mutate(cur):
        if not (cur["aux"] & O3_LINE_BITS):
            return None, "PLC 가 이미 O3 라인을 지웠습니다"
        if cur["aux"] & (1 << A.AUX_O3_GEN):
            return None, "O3 발생기가 다시 켜져 있습니다"
        cur["aux"] &= ~O3_LINE_BITS
        return cur, ""

    box = {}

    async def sender():
        r = await link.manual_apply(mutate)
        box.update(r)
        return r

    res = await _send_plc(A.CMD_MANUAL_APPLY, None, what="O3 바이패스 라인 닫기", sender=sender)
    if box.get("refused"):
        await push_log(f"O3 바이패스 라인 닫기 취소 — {box.get('text')}", "warn")
    return res


# ===================== 관리자 PIN =====================
# ★ 조작 명령은 handle_command 가 이미 루프백 연결만 받는다. 여기서도 한 번 더 본다 —
#   관리자 잠금 해제는 설정 파일을 바꾸는 권한이라 이중으로 막는다.
def _local(ws) -> bool:
    return manager.is_local_ws(ws) if ws is not None else True


async def _admin_reply(ws, ok: bool, msg: str):
    await push_notice(msg, "ok" if ok else "warn", ws)
    if ws is not None:
        await manager.send_to(ws, admin.status(ws))


async def _cmd_admin_status(d, ws):
    await manager.send_to(ws, admin.status(ws))


async def _cmd_admin_setup(d, ws):
    ok, msg = admin.setup(ws, str(d.get("pin") or ""), str(d.get("pin2") or ""), _local(ws))
    await _admin_reply(ws, ok, msg)


async def _cmd_admin_unlock(d, ws):
    ok, msg = admin.unlock(ws, str(d.get("pin") or ""), _local(ws))
    await _admin_reply(ws, ok, msg)


async def _cmd_admin_lock(d, ws):
    admin.lock(ws, "잠금 누름")
    await _admin_reply(ws, True, "관리자 잠금")


async def _cmd_admin_change(d, ws):
    ok, msg = admin.change(ws, str(d.get("old") or ""), str(d.get("new") or ""),
                           str(d.get("new2") or ""), _local(ws))
    await _admin_reply(ws, ok, msg)


async def _need_admin(d, ws) -> bool:
    if not _local(ws) or not admin.is_admin(ws, d.get("token")):
        await push_notice("관리자 잠금 상태입니다 — PIN 으로 잠금을 해제하세요", "warn", ws)
        if ws is not None:
            await manager.send_to(ws, admin.status(ws))
        return False
    admin.touch(ws)
    return True


# ===================== 설정 편집 =====================
def _save_blocked() -> str:
    if _running():
        return "시퀀서 동작 중(공정 준비·실행·일시정지·사이클 후 정지 예약)에는 설정을 저장할 수 없습니다"
    if state.runner and state.runner.busy:
        return "공정 시작 절차가 진행 중입니다 — 끝난 뒤에 저장하세요"
    return ""


async def _cmd_config_preview(d, ws):
    """바뀌는 항목 표 · 검증 결과를 그 화면에만 돌려준다(저장하지 않는다)."""
    if not await _need_admin(d, ws):
        return
    res = settings.prepare(state.cfg, d.get("edits") or {})
    out = settings.public(res)
    out["type"] = "config_preview"
    out["blocked"] = _save_blocked()
    await manager.send_to(ws, out)


async def _cmd_config_save(d, ws):
    if not await _need_admin(d, ws):
        return
    why = _save_blocked()
    if why:
        await push_notice(why, "warn", ws)
        return
    res = settings.prepare(state.cfg, d.get("edits") or {})
    if not res["ok"]:
        await push_notice(f"설정 검증 오류 {len(res['errors'])}건 — 저장하지 않았습니다", "warn", ws)
        await manager.send_to(ws, {**settings.public(res), "type": "config_preview",
                                   "blocked": ""})
        return
    if not res["diff"]:
        await push_notice("바뀐 항목이 없습니다", "info", ws)
        return
    try:
        bk = settings.write(res)
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"설정 저장 실패: {type(e).__name__}: {e}")
        await push_notice(f"설정을 저장하지 못했습니다 — {type(e).__name__}", "err", ws)
        return
    from . import config as config_mod
    cfg, problems, source = config_mod.load(res["new_cfg"]["_path"])
    state.install_config(cfg, problems, source)
    await push_log(f"설정 저장 — {len(res['diff'])}개 항목"
                   + (f" (백업 {bk})" if bk else " (config.json 새로 만듦)"), "ok")
    if res["restart"]:
        await push_log("PLC 연결 설정이 바뀌었습니다 — 프로그램을 다시 시작해야 반영됩니다", "warn")
    link = state.link
    if link and link.connected:
        try:
            mism = await link.rewrite_params()
        except Exception as e:  # noqa: BLE001
            mism = [f"PRM 다시 쓰기 실패: {e}"]
        if mism:
            await push_log("PRM 되읽기 불일치 — " + " · ".join(mism[:3]), "err")
            await push_notice("PLC 파라미터 되읽기 불일치 — 설정 탭 PRM 표를 확인하세요", "err", ws)
        else:
            await push_log("PLC 파라미터를 다시 쓰고 되읽어 확인했습니다", "ok")
    else:
        await push_log("PLC 가 연결되어 있지 않아 파라미터는 다음 연결 때 씁니다", "warn")
    await push_notice("설정을 저장했습니다", "ok", ws)
    await manager.send_to(ws, {"type": "config_saved", "backup": bk,
                               "restart": res["restart"]})
    await push_state()


# ===================== 트렌드 내보내기 · 폴더 열기 =====================
async def _cmd_trend_export(d, ws):
    from .trendlog import trendlog
    try:
        t0, t1 = float(d.get("t0")), float(d.get("t1"))
    except (TypeError, ValueError):
        await push_notice("내보낼 구간이 올바르지 않습니다", "warn", ws)
        return
    if t1 <= t0:
        await push_notice("끝 시각이 시작 시각보다 앞입니다", "warn", ws)
        return
    try:
        # ★ 작업 스레드에서 쓴다 — 7일치는 십수 초가 걸린다(루프에서 돌면 하트비트가 멈춘다)
        keep = (state.cfg.get("log") or {}).get("trend_keep_days", 90)
        name, err = await trendlog.export_async(t0, t1, keep)
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"트렌드 내보내기 실패: {e}")
        await push_notice(f"내보내지 못했습니다 — {type(e).__name__}", "err", ws)
        return
    if err:
        await push_notice(f"내보내지 않았습니다 — {err}", "warn", ws)
        return
    await push_notice(f"저장했습니다: data/export/{name}", "ok", ws)
    await manager.send_to(ws, {"type": "trend_exported", "name": name})


OPEN_DIRS = {"export": "export", "datalog": "datalog", "backup": "config_backup"}


async def _cmd_open_folder(d, ws):
    """탐색기로 데이터 폴더를 연다 — 이 PC 화면에서만(원격은 handle_command 가 거절)."""
    import os
    import sys
    from . import paths
    sub = OPEN_DIRS.get(d.get("which") or "")
    if not sub:
        await push_notice("열 수 없는 폴더입니다", "warn", ws)
        return
    path = os.path.join(paths.DATA_DIR, sub)
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)      # noqa: S606 — 고정된 데이터 폴더만 연다
    await push_notice(f"폴더를 열었습니다: data/{sub}", "info", ws)


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
    "alarm_history": _cmd_alarm_history,
    # 수동
    "manual_unlock": _cmd_manual_unlock,
    "manual_valve": _cmd_manual_valve,
    "manual_mfc": _cmd_manual_mfc,
    "manual_heater": _cmd_manual_heater,
    "manual_pcv": _cmd_manual_pcv,
    "manual_rf": _cmd_manual_rf,
    "manual_o3": _cmd_manual_o3,
    # 관리자 · 설정
    "admin_status": _cmd_admin_status,
    "admin_setup": _cmd_admin_setup,
    "admin_unlock": _cmd_admin_unlock,
    "admin_lock": _cmd_admin_lock,
    "admin_change": _cmd_admin_change,
    "config_preview": _cmd_config_preview,
    "config_save": _cmd_config_save,
    # 이력
    "trend_export": _cmd_trend_export,
    "open_folder": _cmd_open_folder,
}
