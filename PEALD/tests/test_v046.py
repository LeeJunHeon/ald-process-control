"""v0.4.6 — 화면(서버 쪽): 원격 보기의 읽기 전용 레시피 명령 · 저장 결과 답 · 서식 규칙.

화면 동작(창 겹침 · 그래프 띠 · 끊김 표시 · 입력 보존)은 headless 브라우저로 확인한다(README 참고).
"""
import json
import types

import pytest

from peald import commands as C
from peald import recipe as R
from peald import storage
from peald.state import state

from test_process import wired, short_recipe, pumped   # noqa: F401


class FakeWS:
    def __init__(self, host="127.0.0.1"):
        self.client = types.SimpleNamespace(host=host)
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    def of(self, kind):
        return [m for m in self.sent if m.get("type") == kind]

    def notices(self):
        return [m["msg"] for m in self.of("notice")]


def reg(ws, local):
    """연결 목록에 올린다(서버가 로컬/원격을 여기서 판단한다)."""
    from peald.connection import manager
    manager.active[ws] = {"local": local}
    return ws


@pytest.fixture
def remote():
    from peald.connection import manager
    ws = reg(FakeWS("192.168.10.50"), False)
    yield ws
    manager.active.clear()


# ===================== 10. 원격: 레시피 보기는 되고 조작은 안 된다 =====================
async def test_remote_can_load_and_validate_recipe(wired, remote):
    lk, sim, cfg = wired
    assert storage.save("원격보기", short_recipe("원격보기"))
    await C.handle_command({"cmd": "recipe_load", "name": "원격보기"}, remote)
    got = remote.of("recipe")
    assert got and got[0]["name"] == "원격보기" and got[0]["summary"]["total_ms"] > 0
    await C.handle_command({"cmd": "recipe_validate", "recipe": short_recipe("원격보기")}, remote)
    chk = remote.of("recipe_check")
    assert chk and not chk[0]["check"]["errors"] and chk[0]["summary"]["block_count"] == 1
    assert not any("보기 전용" in n for n in remote.notices())


@pytest.mark.parametrize("cmd", [
    {"cmd": "recipe_save", "name": "원격저장"},
    {"cmd": "recipe_upload", "name": "원격보기"},
    {"cmd": "recipe_delete", "name": "원격보기"},
    {"cmd": "recipe_rename", "name": "원격보기", "new_name": "바뀐이름"},
    {"cmd": "recipe_select", "name": "원격보기"},
    {"cmd": "process_start"},
    {"cmd": "alarm_popup_close"},
])
async def test_remote_recipe_changes_still_refused(wired, remote, cmd):
    lk, sim, cfg = wired
    assert storage.save("원격보기", short_recipe("원격보기"))
    if cmd["cmd"] == "recipe_save":
        cmd = dict(cmd, recipe=short_recipe("원격저장"))
    state.alarm_popup = True
    await C.handle_command(cmd, remote)
    assert any("보기 전용" in n for n in remote.notices()), remote.sent
    assert storage.exists("원격보기") and not storage.exists("원격저장") and not storage.exists("바뀐이름")
    assert state.runner.recipe_name == ""
    assert state.alarm_popup is True          # 원격은 알람 창 상태를 바꾸지 못한다(화면에서만 닫는다)


# ===================== 14. 저장 결과 답 =====================
async def test_save_replies_result_to_that_connection(wired):
    lk, sim, cfg = wired
    ws = reg(FakeWS(), True)
    await C.handle_command({"cmd": "recipe_save", "name": "답", "recipe": short_recipe("답")}, ws)
    ok = ws.of("recipe_saved")
    assert ok and ok[0]["ok"] is True and ok[0]["name"] == "답" and ok[0]["number"] == R.recipe_number(
        storage.load("답"))
    bad = short_recipe("답")
    bad["blocks"][0]["steps"][0]["time_ms"] = 5
    ws2 = reg(FakeWS(), True)
    await C.handle_command({"cmd": "recipe_save", "name": "답", "recipe": bad}, ws2)
    no = ws2.of("recipe_saved")
    assert no and no[0]["ok"] is False and "검증 오류" in no[0]["why"]
    assert storage.load("답")["blocks"][0]["steps"][0]["time_ms"] == 100     # 덮어쓰지 않았다
    ws3 = reg(FakeWS(), True)
    await C.handle_command({"cmd": "recipe_save", "name": "a/b", "recipe": short_recipe("x")}, ws3)
    assert ws3.of("recipe_saved")[0]["ok"] is False
    from peald.connection import manager
    manager.active.clear()


# ===================== 22. 서식 =====================
@pytest.mark.parametrize("v,want", [(759.67, "759.67"), (0.5, "0.500"), (3.1e-3, "3.1E-3"),
                                    (0, "0"), (1e-5, "1.0E-5"), (None, "—")])
def test_server_torr_matches_screen_rule(v, want):
    from peald.process import _torr
    assert _torr(v) == want


async def test_base_check_sends_numbers(wired):
    lk, sim, cfg = wired
    checks = {c["key"]: c for c in state.runner.start_checks()}
    b = checks["base"]
    assert "cur" in b and "target" in b and isinstance(b["cur"], float)


def test_one_degree_sign():
    from peald.trendlog import columns_meta
    units = {c["unit"] for c in columns_meta()}
    assert "°C" not in units and "℃" in units


# ===================== A. 창 겹침 · Esc (headless 브라우저) =====================
from test_v045 import served, _browser_page   # noqa: E402,F401

TOP = """(id) => { const m = document.getElementById(id); if (!m || m.hidden) return false;
  const b = m.querySelector('.modal-box').getBoundingClientRect();
  const e = document.elementFromPoint(b.left + b.width / 2, b.top + 12);
  return !!(e && e.closest('#' + id)); }"""


async def test_confirm_and_alarm_above_manual_window(served):
    pw_api = pytest.importorskip("playwright.async_api")
    async with pw_api.async_playwright() as pw:
        b, page = await _browser_page(pw, served)
        try:
            # v0.4.5 편집기 시험이 가려 둔 창 숨김 규칙을 걷어 낸다(여기서는 창 자체를 본다)
            await page.evaluate("() => document.querySelectorAll('style').forEach(e => "
                                "{ if (e.textContent.indexOf('#alarmModal') >= 0) e.remove(); })")
            await page.evaluate("() => { const m = document.getElementById('alarmModal'); "
                                "if (!m.hidden) document.querySelector('[data-am=close]').click(); }")
            await page.evaluate("() => core.setTab('main')")
            await page.click("[data-bind='mnOpen']")
            await page.fill("[data-mnhsv='2']", "55")
            await page.click("[data-mn='heater']")
            assert await page.evaluate(TOP, "confirmModal"), "확인 창이 수동 창 뒤에 숨었다"
            # 떠 있는 동안 같은 요청은 새로 띄우지 않고, Esc 는 취소 — 콜백도 지운다
            await page.evaluate("() => core.confirmAsk('다른 것', 'x', '실행', () => { window.__ran = 1; })")
            await page.keyboard.press("Escape")
            assert await page.evaluate("() => document.getElementById('confirmModal').hidden")
            assert await page.evaluate("() => window.__ran") is None
            # 알람 창은 모든 창 위
            await page.evaluate("() => core.applyLive(Object.assign({}, core.state.live, "
                                "{alarm_popup: true, alarms: [{code: 'A0.0', name: '시험', crit: true, since: '00:00:00'}]}))")
            assert await page.evaluate(TOP, "alarmModal")
            # 포커스는 창 안으로
            assert await page.evaluate("() => !!document.activeElement.closest('#alarmModal')")
        finally:
            await b.close()
