"""
admin.py — 관리자 PIN · 잠금 해제 세션.

설정 편집만 PIN 을 요구한다. 레시피 편집·수동 조작·시뮬레이터 조작판은 PIN 없이 된다.

★ PIN 은 어디에도 그대로 남기지 않는다. data/admin_pin.json 에는
  PBKDF2-HMAC-SHA256 해시·무작위 salt·반복 횟수만 둔다. 화면·로그·웹소켓·config.json 에
  PIN 이나 해시를 내보내지 않는다(로그에는 성공·실패·잠금 사건만).
★ 잠금 해제는 이 PC(루프백) 연결에서만 된다. 서버가 무작위 토큰을 만들어 그 웹소켓
  연결에만 붙인다 — 다른 연결(다른 창·원격)은 같은 토큰을 써도 통하지 않는다.
★ 관리자 조작 없이 10 분, 공정 시작, [잠금] 중 하나면 다시 잠긴다.
★ 5 번 틀리면 5 분 동안 입력을 막는다.

PIN 을 잊었을 때: 프로그램을 끄고 data/admin_pin.json 을 지운 뒤 다시 켜서 새로 정한다.
"""

import os
import hmac
import time
import secrets
import hashlib

from . import logger
from . import paths
from .storage import atomic_write_json, read_json

ITERATIONS = 240_000
SALT_BYTES = 16
PIN_MIN, PIN_MAX = 4, 8
IDLE_S = 600.0                  # 관리자 조작 없이 이만큼 지나면 잠근다
MAX_FAILS = 5
BLOCK_S = 300.0                 # 연속 실패 뒤 입력을 막는 시간


def pin_path() -> str:
    return os.path.join(paths.DATA_DIR, "admin_pin.json")


def _hash(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)


def valid_pin(pin) -> bool:
    return isinstance(pin, str) and pin.isdigit() and PIN_MIN <= len(pin) <= PIN_MAX \
        and pin.isascii()


class Admin:
    def __init__(self):
        self.sessions = {}      # ws -> {"token", "last"}
        self.fails = 0
        self.blocked_until = 0.0

    # ===================== PIN 파일 =====================
    def has_pin(self) -> bool:
        rec = read_json(pin_path())
        return isinstance(rec, dict) and bool(rec.get("hash")) and bool(rec.get("salt"))

    def _write_pin(self, pin: str):
        salt = secrets.token_bytes(SALT_BYTES)
        rec = {"algo": "pbkdf2_sha256", "iterations": ITERATIONS,
               "salt": salt.hex(), "hash": _hash(pin, salt, ITERATIONS).hex(),
               "set_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        os.makedirs(paths.DATA_DIR, exist_ok=True)
        atomic_write_json(pin_path(), rec)

    def _check_pin(self, pin: str) -> bool:
        rec = read_json(pin_path())
        if not isinstance(rec, dict):
            return False
        try:
            salt = bytes.fromhex(rec["salt"])
            want = bytes.fromhex(rec["hash"])
            it = int(rec.get("iterations") or ITERATIONS)
        except (KeyError, ValueError, TypeError):
            return False
        return hmac.compare_digest(_hash(str(pin), salt, it), want)

    # ===================== 판정 =====================
    def blocked_s(self) -> int:
        return max(0, int(round(self.blocked_until - time.monotonic())))

    def is_admin(self, ws, token=None) -> bool:
        """이 연결이 잠금 해제 상태인지. token 을 주면 그 연결에 붙은 토큰과도 맞춰 본다."""
        s = self.sessions.get(ws)
        if not s:
            return False
        if time.monotonic() - s["last"] > IDLE_S:
            self.sessions.pop(ws, None)
            logger.write("info", "관리자 잠금 — 10 분 동안 조작이 없었습니다")
            return False
        if token is not None and not hmac.compare_digest(str(token), s["token"]):
            return False
        return True

    def touch(self, ws):
        s = self.sessions.get(ws)
        if s:
            s["last"] = time.monotonic()

    def status(self, ws) -> dict:
        """화면에 보내는 상태. ★ PIN·해시는 넣지 않는다(토큰은 그 연결 자신에게만)."""
        s = self.sessions.get(ws) if self.is_admin(ws) else None
        return {
            "type": "admin",
            "has_pin": self.has_pin(),
            "unlocked": bool(s),
            "token": s["token"] if s else "",
            "left_s": int(IDLE_S - (time.monotonic() - s["last"])) if s else 0,
            "blocked_s": self.blocked_s(),
        }

    # ===================== 동작 =====================
    def _unlock(self, ws) -> str:
        token = secrets.token_urlsafe(24)
        self.sessions[ws] = {"token": token, "last": time.monotonic()}
        return token

    def setup(self, ws, pin: str, pin2: str, local: bool):
        """PIN 이 없을 때 처음 정한다. (성공, 이유)"""
        if not local:
            return False, "PIN 은 이 PC 에서만 정할 수 있습니다"
        if self.has_pin():
            return False, "PIN 이 이미 있습니다 — 바꾸려면 현재 PIN 을 확인하세요"
        if not valid_pin(pin):
            return False, f"PIN 은 숫자 {PIN_MIN}~{PIN_MAX}자리여야 합니다"
        if pin != pin2:
            return False, "두 번 입력한 PIN 이 다릅니다"
        try:
            self._write_pin(pin)
        except Exception as e:  # noqa: BLE001
            logger.write("err", f"관리자 PIN 저장 실패: {type(e).__name__}")
            return False, "PIN 을 저장하지 못했습니다 — 데이터 폴더 쓰기 권한을 확인하세요"
        logger.write("warn", "관리자 PIN 을 새로 정했습니다")
        self._unlock(ws)
        return True, "PIN 을 정했습니다 — 잠금 해제됨"

    def unlock(self, ws, pin: str, local: bool):
        if not local:
            logger.write("warn", "관리자 잠금 해제 거절 — 원격 연결")
            return False, "잠금 해제는 이 PC 에서만 됩니다"
        if not self.has_pin():
            return False, "PIN 이 아직 없습니다 — 먼저 정하세요"
        left = self.blocked_s()
        if left:
            return False, f"PIN 을 {MAX_FAILS}번 틀려 입력이 막혀 있습니다 — {left} s 뒤 다시 하세요"
        if self._check_pin(str(pin or "")):
            self.fails = 0
            self._unlock(ws)
            logger.write("ok", "관리자 잠금 해제")
            return True, "잠금 해제됨 (10 분 동안 조작이 없으면 다시 잠깁니다)"
        self.fails += 1
        logger.write("warn", f"관리자 PIN 틀림 ({self.fails}/{MAX_FAILS})")
        if self.fails >= MAX_FAILS:
            self.fails = 0
            self.blocked_until = time.monotonic() + BLOCK_S
            logger.write("err", f"관리자 PIN {MAX_FAILS}번 틀림 — {int(BLOCK_S // 60)} 분 동안 입력을 막습니다")
            return False, f"PIN 을 {MAX_FAILS}번 틀렸습니다 — {int(BLOCK_S // 60)} 분 동안 입력을 막습니다"
        return False, f"PIN 이 틀렸습니다 ({MAX_FAILS - self.fails}번 남음)"

    def change(self, ws, old: str, new: str, new2: str, local: bool):
        if not local:
            return False, "PIN 은 이 PC 에서만 바꿀 수 있습니다"
        left = self.blocked_s()
        if left:
            return False, f"입력이 막혀 있습니다 — {left} s 뒤 다시 하세요"
        if not self._check_pin(str(old or "")):
            self.fails += 1
            logger.write("warn", f"관리자 PIN 바꾸기 — 현재 PIN 틀림 ({self.fails}/{MAX_FAILS})")
            if self.fails >= MAX_FAILS:
                self.fails = 0
                self.blocked_until = time.monotonic() + BLOCK_S
                logger.write("err", f"관리자 PIN {MAX_FAILS}번 틀림 — 입력을 막습니다")
            return False, "현재 PIN 이 틀렸습니다"
        if not valid_pin(new):
            return False, f"새 PIN 은 숫자 {PIN_MIN}~{PIN_MAX}자리여야 합니다"
        if new != new2:
            return False, "두 번 입력한 새 PIN 이 다릅니다"
        self.fails = 0
        self._write_pin(new)
        logger.write("warn", "관리자 PIN 을 바꿨습니다")
        self._unlock(ws)
        return True, "PIN 을 바꿨습니다"

    def lock(self, ws, why: str = "잠금 누름"):
        if self.sessions.pop(ws, None):
            logger.write("info", f"관리자 잠금 — {why}")

    def lock_all(self, why: str) -> list:
        """모든 연결을 잠근다. 잠긴 연결 목록을 돌려준다(화면에 알리려고)."""
        locked = list(self.sessions)
        self.sessions.clear()
        if locked:
            logger.write("info", f"관리자 잠금 — {why}")
        return locked

    def expired(self) -> list:
        """시간이 지나 잠긴 연결들(주기 점검용)."""
        out = []
        for ws in list(self.sessions):
            if not self.is_admin(ws):
                out.append(ws)
        return out

    def forget(self, ws):
        self.sessions.pop(ws, None)


admin = Admin()
