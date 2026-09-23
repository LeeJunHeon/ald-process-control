"""
commands.py — 화면 명령 처리.

흐름:  화면 → {"cmd": ...} → 권한 검사(로컬 여부) → 상태 검사 → provider 호출 → push

★ 두 겹의 검사를 전부 서버에서 한다. 화면 쪽 잠금(버튼 disabled)은 개발자 도구로 풀 수
  있으므로 신뢰하지 않는다.
   1) 권한 : 조작 명령은 이 PC(루프백) 접속에서만 받는다. 원격은 거절하고 로그를 남긴다.
   2) 상태 : 공정 중에는 수동 밸브·펌핑·벤트·전체 닫기를 거절한다. 공정이 여는 밸브를
             사람이 동시에 건드리면 전구체와 반응물이 챔버에서 만난다.
"""

import logger
import storage
import recipe_model
from state import state
from connection import manager, push_state, push_notice, push_log

# 원격(보기 전용)이 보낼 수 있는 명령. 나머지는 전부 거절한다.
READ_ONLY_CMDS = {"recipe_list", "recipe_load", "recipe_preview"}

# 공정 중에 거절할 수동 조작. 공정이 밸브를 쥐고 있는 동안 사람이 끼어들면 안 된다.
BLOCKED_WHILE_RUNNING = {"valve_toggle", "pump", "vent", "all_close"}

RUNNING_MODES = ("running", "paused", "stopping")

_shutdown_handler = None


def set_shutdown_handler(fn):
    """server.py 가 창 종료 함수를 주입한다(window 를 import하면 순환)."""
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

    if cmd in BLOCKED_WHILE_RUNNING and _is_running():
        await push_notice("공정 중에는 수동 조작을 할 수 없습니다 — 먼저 공정을 멈추세요", "warn", ws)
        return

    fn = _HANDLERS.get(cmd)
    if fn is None:
        await push_notice(f"알 수 없는 명령입니다: {cmd}", "warn", ws)
        return
    await fn(data, ws)


def _is_running() -> bool:
    return ((state.snap or {}).get("process") or {}).get("mode") in RUNNING_MODES


def _p():
    return state.provider


async def _result(ok: bool, why: str, ws, success_msg: str = ""):
    """provider 결과를 토스트로 알리고, 성공이면 구조 변화를 반영해 state 를 다시 보낸다."""
    if ok:
        if success_msg:
            await push_notice(success_msg, "ok", ws)
    else:
        await push_notice(why or "명령을 처리하지 못했습니다", "warn", ws)


# ===================== 밸브 · 배기 =====================
async def _cmd_valve_toggle(d, ws):
    tag = d.get("tag")
    ok, why = _p().set_valve(tag, bool(d.get("open")))
    await _result(ok, why, ws)


async def _cmd_pump(d, ws):
    ok, why = _p().pump()
    await _result(ok, why, ws, "펌핑을 시작했습니다")


async def _cmd_vent(d, ws):
    ok, why = _p().vent()
    await _result(ok, why, ws, "벤트를 시작했습니다")


async def _cmd_all_close(d, ws):
    ok, why = _p().all_close()
    await _result(ok, why, ws, "모든 밸브를 닫았습니다")


async def _cmd_heater_apply(d, ws):
    ok, why = _p().set_heater_sv(d.get("sv") or {})
    await _result(ok, why, ws, "히터 SV를 적용했습니다")


# ===================== 공정 =====================
async def _cmd_process_start(d, ws):
    name = d.get("recipe") or ""
    recipe = storage.load_recipe(name)
    if recipe is None:
        await push_notice(f"레시피를 불러올 수 없습니다: {name}", "warn", ws)
        return
    errs = recipe_model.validate(state.cfg, recipe)
    hard = [e for e in errs if e["level"] == "err"]
    if hard:
        # 검증에 걸린 레시피로 공정을 시작하면 전구체·반응물 동시 개방 같은 사고가 난다.
        await push_notice(f"레시피 검증 실패 — {hard[0]['msg']}", "err", ws)
        await push_log(f"공정 시작 거절 — 레시피 검증 실패: {hard[0]['msg']}", "err")
        return
    ok, why = _p().start_process(recipe)
    await _result(ok, why, ws)
    if ok:
        await push_state()


async def _cmd_process_pause(d, ws):
    ok, why = _p().pause()
    await _result(ok, why, ws)


async def _cmd_process_stop_after_cycle(d, ws):
    ok, why = _p().stop_after_cycle()
    await _result(ok, why, ws)


async def _cmd_process_abort(d, ws):
    ok, why = _p().abort()
    await _result(ok, why, ws, "공정을 즉시 중단했습니다")
    if ok:
        await push_state()


# ===================== 알람 =====================
async def _cmd_alarm_ack(d, ws):
    ok, why = _p().ack_alarm(d.get("code") or "")
    await _result(ok, why, ws)
    if ok:
        await push_state()


async def _cmd_alarm_reset(d, ws):
    ok, why = _p().reset_alarms()
    await _result(ok, why, ws)
    if ok:
        await push_state()


# ===================== 레시피 =====================
async def _cmd_recipe_list(d, ws):
    await manager.send_to(ws, {"type": "recipe_list", "names": storage.list_recipes()})


async def _cmd_recipe_load(d, ws):
    name = d.get("name") or ""
    if not storage.valid_recipe_name(name):
        await push_notice("레시피 이름이 올바르지 않습니다", "warn", ws)
        return
    recipe = storage.load_recipe(name)
    if recipe is None:
        await push_notice(f"레시피를 불러올 수 없습니다: {name}", "warn", ws)
        return
    await manager.send_to(ws, {"type": "recipe", "name": name, "recipe": recipe,
                               "preview": recipe_model.preview(state.cfg, recipe)})


async def _cmd_recipe_preview(d, ws):
    """편집할 때마다 불린다 — 계산·검증은 서버 한 곳에서만 한다(화면과 결과가 갈리지 않게)."""
    await manager.send_to(ws, {"type": "preview",
                               "preview": recipe_model.preview(state.cfg, d.get("recipe") or {})})


async def _cmd_recipe_save(d, ws):
    name = (d.get("name") or "").strip()
    recipe = d.get("recipe") or {}
    if not storage.valid_recipe_name(name):
        await push_notice("레시피 이름에 쓸 수 없는 문자가 있습니다", "warn", ws)
        return
    errs = [e for e in recipe_model.validate(state.cfg, recipe) if e["level"] == "err"]
    if errs:
        await push_notice(f"검증에 실패한 레시피는 저장할 수 없습니다 — {errs[0]['msg']}", "warn", ws)
        return
    recipe["name"] = name
    if not storage.save_recipe(name, recipe):
        await push_notice("레시피를 저장하지 못했습니다 — 폴더 쓰기 권한을 확인하세요", "err", ws)
        return
    await push_log(f"레시피 저장: {name}", "ok")
    await push_notice(f"저장했습니다: {name}", "ok", ws)
    await push_state()


async def _cmd_recipe_delete(d, ws):
    name = d.get("name") or ""
    if not storage.valid_recipe_name(name):
        await push_notice("레시피 이름이 올바르지 않습니다", "warn", ws)
        return
    if ((state.snap or {}).get("process") or {}).get("recipe") == name and _is_running():
        await push_notice("실행 중인 레시피는 삭제할 수 없습니다", "warn", ws)
        return
    if not storage.delete_recipe(name):
        await push_notice("레시피를 삭제하지 못했습니다", "warn", ws)
        return
    await push_log(f"레시피 삭제: {name}", "warn")
    await push_notice(f"삭제했습니다: {name}", "ok", ws)
    await push_state()


# ===================== 설정 · 종료 =====================
async def _cmd_settings_save(d, ws):
    # 설정 저장은 관리자 PIN·PLC 재설정과 묶여 있어 다음 단계에서 구현한다.
    # 지금 반쪽만 저장하면 화면과 실제 설정이 어긋난 채로 운전하게 된다.
    await push_notice("설정 저장은 다음 단계에서 구현됩니다", "info", ws)


async def _cmd_exit(d, ws):
    if _is_running():
        await push_notice("공정 중에는 프로그램을 종료할 수 없습니다", "warn", ws)
        return
    await push_log("운전자 요청으로 프로그램을 종료합니다", "warn")
    if _shutdown_handler:
        _shutdown_handler()


_HANDLERS = {
    "valve_toggle": _cmd_valve_toggle,
    "pump": _cmd_pump,
    "vent": _cmd_vent,
    "all_close": _cmd_all_close,
    "heater_apply": _cmd_heater_apply,
    "process_start": _cmd_process_start,
    "process_pause": _cmd_process_pause,
    "process_stop_after_cycle": _cmd_process_stop_after_cycle,
    "process_abort": _cmd_process_abort,
    "alarm_ack": _cmd_alarm_ack,
    "alarm_reset": _cmd_alarm_reset,
    "recipe_list": _cmd_recipe_list,
    "recipe_load": _cmd_recipe_load,
    "recipe_save": _cmd_recipe_save,
    "recipe_delete": _cmd_recipe_delete,
    "recipe_preview": _cmd_recipe_preview,
    "settings_save": _cmd_settings_save,
    "exit": _cmd_exit,
}
