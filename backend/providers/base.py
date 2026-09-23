"""
providers/base.py — 장비 값 공급자(provider) 인터페이스.

★ 이 파일이 "화면·서버 로직"과 "장비"를 가르는 유일한 경계다.
  provider 밖의 코드(commands/loops/state/frontend)는 값이 데모에서 왔는지 실제 PLC에서
  왔는지 알면 안 된다. 다음 단계에서 providers/plc.py 를 추가하고 config.demo.enabled 로
  갈아끼우면 나머지는 한 줄도 고치지 않는다.

계약
  - 모든 메서드는 예외를 던지지 않는다. 실패는 (False, "사유") 로 돌려준다.
    장비 한 번 못 읽었다고 서버 루프가 죽으면 화면 전체가 멈추기 때문이다.
  - read_snapshot() 은 "지금 장비가 이렇다"는 사실만 담는다. 화면 표현(색·문구)은 넣지 않는다.
  - tick(now) 는 시뮬레이션용 시간 진행 훅이다. 실장비 provider 는 통신 폴링에 쓰거나 비워 둔다.
"""

from typing import Tuple

Result = Tuple[bool, str]      # (성공 여부, 사유/메시지)

OK: Result = (True, "")


class Provider:
    """값 공급자 기본형. 모든 메서드는 기본적으로 '미지원'을 돌려준다 —
    구현하지 않은 기능이 조용히 성공한 것처럼 보이면 안 된다."""

    #: 화면 헤더의 '데모' 칩 표시 여부. 실장비 provider 는 False.
    is_demo = False

    # ---------- 수명 주기 ----------
    async def start(self) -> None:
        """통신 연결 등 준비. 실패해도 예외를 던지지 않는다."""

    async def stop(self) -> None:
        """정리."""

    def tick(self, now: float) -> None:
        """주기 호출(샘플링 주기). 시뮬레이션 시간 진행 또는 폴링 처리."""

    # ---------- 읽기 ----------
    def read_snapshot(self) -> dict:
        """장비 현재값 한 덩어리.

        반환 키
          plc        {connected, hb_ok, rtt_ms}
          valves     {태그: bool}                       열림 여부
          mfc        {라인id: {sv, pv}}                 sccm
          heaters    {히터id: {sv, pv, out_pct, on, state}}
          gauges     {baratron, convectron}             Torr
          io         {dry_pump, rv, vent, tv_pct}
          interlocks {인터락id: bool}                   True = 정상
          process    {mode, recipe, block, block_name, cycle, cycles,
                      step, steps, step_name, step_elapsed_s, step_total_s,
                      elapsed_s, remaining_s, eta}
          alarms     [{code, level, msg, ts, ack, cleared}]
        """
        return {}

    # ---------- 공정 ----------
    def start_process(self, recipe: dict) -> Result:
        return False, "이 공급자는 공정 시작을 지원하지 않습니다"

    def pause(self) -> Result:
        return False, "이 공급자는 일시정지를 지원하지 않습니다"

    def stop_after_cycle(self) -> Result:
        return False, "이 공급자는 사이클 후 정지를 지원하지 않습니다"

    def abort(self) -> Result:
        return False, "이 공급자는 즉시 중단을 지원하지 않습니다"

    # ---------- 수동 조작 ----------
    def set_valve(self, tag: str, open_: bool) -> Result:
        return False, "이 공급자는 밸브 조작을 지원하지 않습니다"

    def pump(self) -> Result:
        return False, "이 공급자는 펌핑을 지원하지 않습니다"

    def vent(self) -> Result:
        return False, "이 공급자는 벤트를 지원하지 않습니다"

    def all_close(self) -> Result:
        return False, "이 공급자는 전체 밸브 닫기를 지원하지 않습니다"

    def set_heater_sv(self, updates: dict) -> Result:
        return False, "이 공급자는 히터 SV 적용을 지원하지 않습니다"

    # ---------- 알람 ----------
    def ack_alarm(self, code: str = "") -> Result:
        return False, "이 공급자는 알람 확인을 지원하지 않습니다"

    def reset_alarms(self) -> Result:
        return False, "이 공급자는 알람 리셋을 지원하지 않습니다"

    # ---------- 이력 ----------
    def alarm_history(self) -> list:
        return []

    def drain_logs(self) -> list:
        """provider 가 만든 공정 로그를 꺼내 간다. [(level, message)]"""
        return []
