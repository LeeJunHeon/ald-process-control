"""test/plc_ctl.py — PLC 시험 콘솔(가짜 PLC · 실제 PLC 모두).

실기 시운전 때 상태를 한 화면에 보고, 주소 하나씩 읽고 쓰고, 가짜 PLC 에는 고장을 넣는 명령줄 도구다.
★ 제어 프로그램과 같은 PLC 에 동시에 쓰지 않는다 — 프로그램이 돌고 있으면 status · read 만 쓴다.
★ 읽기 · 진단 위주다. 실기에서 밸브 · 히터 · MFC 를 하나씩 확인할 때는 프로그램의 조작 창(faceplate)을 쓴다 —
  명령 12/13/14 의 검사와 프로그램의 수동 잠금을 거친다.
★ 쓰기(v0.6.1 C1)는 허용 목록 안에서만:
    명령 데이터 D01004~D01042 — 그 워드의 뜻을 보이고 'y' 확인. 요청만 놓는다(명령 12/13/14 를 보내야 반영)
    레시피 영역 D02000~D03119 — 'y' 확인
    하트비트 · 명령 D01000~D01003 · PRM D01100~D01124 · 히터 D01010~D01023 — 옛 값 → 새 값을 보이고 'WRITE' 를
    그대로 쳐야. 히터 워드는 프로그램의 다음 명령 13 이 그대로 반영한다(되돌리는 명령줄을 찍는다 — v0.6.2 D2)
    그 밖(D04xxx 작업 이미지 등) — 거절. --raw 를 주고 주소를 한 번 더 쳐야만(래더가 대기 중 D04012 · D04050 을
    바로 요청으로 써서 다음 스캔에 밸브가 열리거나 RF 가 켜질 수 있다)
  확인은 누적이다(v0.6.2 D1): --raw 주소 다시 치기와 WRITE 는 따로 — 둘 다 해당하면 둘 다 묻는다.
  쓰기 전에 장비 ID(다르면 거절 — --force 일 때만. 0(ID 렁 전)이면 장비 이름을 쳐야) · 제어 프로그램이 붙어 있는지
  (D01000 하트비트를 PRM 와치독/3 + 0.3 s(최소 0.7 s) 동안 0.1 s 마다 읽어 바뀌면 거절) · 장비 상태(공정 중이면 거절)를
  보고, 쓰기 바로 직전에 장비 상태 · 하트비트를 한 번 더 본다.
  --yes(y 확인 생략)는 가짜 PLC 에만 듣는다(제어 포트가 답하면 가짜 PLC). 'WRITE' · 주소 다시 치기는 생략하지 않는다.
★ status 는 Modbus TCP 연결을 하나 잡는다 — 실제 PLC 의 동시 연결 수를 확인하고 쓴다.

예:
    python test/plc_ctl.py --port 15301 status                  (1 s 마다, Ctrl+C 로 끝)
    python test/plc_ctl.py --host 192.168.10.12 status --count 1 (실제 PLC 한 번)
    python test/plc_ctl.py --port 15301 read D00000 20
    python test/plc_ctl.py --port 15301 write D01040 0          (확인 질문 — 요청만 놓음, 명령 12 가 반영)
    python test/plc_ctl.py --port 15301 faults                  (가짜 PLC 전용 ↓)
    python test/plc_ctl.py --port 15301 fault emo on
    python test/plc_ctl.py --port 15301 cut blackhole 2.0       (unplug 2 = 케이블 뽑힘 · refuse 3 = 연결 거부 3 s)
    python test/plc_ctl.py --port 15301 id 1234                 (16 진 — 0x1234 · h1234 도)
"""
import sys
import json
import time
import socket
import struct
import argparse

import _pkg

_pkg.utf8_console()
A = _pkg.mod("addresses")
DEV = _pkg.mod("device")

RUNNING_STATES = (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)
GUARDED = range(A.D_PC_HB, A.D_CMD_ARG + 1)                       # 하트비트 · 명령 D01000~D01003
CMD_DATA = range(A.D_MANUAL_VALVE, A.D_O3_SV + 1)                 # 명령 데이터 D01004~D01042
PRM_AREA = range(A.D_PRM_PC_WDT_MS, A.D_PRM_O3_MAX + 1)           # PRM D01100~D01124
RECIPE_AREA = range(A.D_RCP_STEP_COUNT, A.RCP_SUM_END + 1)        # 레시피 표 D02000~D03119
HEATER_WORDS = range(A.D_HEATER_POWER, A.D_HEATER_SV + 12)        # 히터 전원 · 설정 온도 D01010~D01023(명령 13 이 되읽음)
HB_WATCH_S = 0.7                                                  # 제어 프로그램이 붙어 있나 — 하트비트를 지켜보는 최소 시간
HB_WATCH_BAD_S = 1.3                                              # 와치독 PRM 이 0 이거나 이상할 때
DEVICE_WORD = DEV.KEY.upper()                                     # ID 0 일 때 칠 이름(PEALD / POWDERALD)
ATTACHED_MSG = ("거절: 제어 프로그램이 이 PLC 에 붙어 있습니다(PC 하트비트 D01000 이 바뀜) — 프로그램을 끄고 쓰거나, "
                "프로그램의 조작 창으로 하세요")

PRM_NAMES = {
    A.D_PRM_PC_WDT_MS: "PC 하트비트 끊김 판정 ms", A.D_PRM_BASE_PRESS: "베이스 압력(CVG 원시값)",
    A.D_PRM_PUMP_TIMEOUT: "베이스 도달 제한 s", A.D_PRM_VENT_TIMEOUT: "대기압 도달 제한 s",
    A.D_PRM_MFC_STABLE: "MFC 안정 판정 s", A.D_PRM_MFC_TOL: "MFC1 허용 편차(원시값)",
    A.D_PRM_MFC_TIMEOUT: "MFC 안정 대기 제한 s", A.D_PRM_VALVE_MIN_MS: "펄스 밸브 최소 열림 ms",
    A.D_PRM_RF_MAX: "RF 설정 상한(원시값)", A.D_PRM_RF_REF_MAX: "반사 전력 한계(원시값)",
    A.D_PRM_RF_REF_MS: "반사 초과 허용 ms", A.D_PRM_RF_MAX_PRESS: "RF 허가 최대 압력(CVG 원시값)",
    A.D_PRM_O3_MAX: "O3 설정 상한(원시값)",
}


def word_name(addr: int) -> str:
    """쓰기 허용 목록 안 워드의 뜻(확인 질문에 보인다)."""
    if addr == A.D_PC_HB:
        return "PC 하트비트"
    if addr == A.D_CMD_NO:
        return "명령 번호(쓰면 PLC 가 명령을 실행한다)"
    if addr == A.D_CMD_CODE:
        return "명령 코드"
    if addr == A.D_CMD_ARG:
        return "명령 인자(예비)"
    if A.D_MANUAL_VALVE <= addr < A.D_MANUAL_VALVE + 4:
        k = addr - A.D_MANUAL_VALVE
        return f"수동 밸브 요청 bit{16 * k}~{16 * k + 15} — 명령 12(수동 적용)가 반영"
    if addr == A.D_MANUAL_AUX:
        return "수동 보조 출력 요청 — 명령 12(수동 적용)가 반영"
    if addr == A.D_HEATER_POWER:
        return "히터 전원 요청 b0~b11 — 명령 13(히터 적용)이 반영"
    if A.D_HEATER_SV <= addr < A.D_HEATER_SV + 12:
        return f"CH{addr - A.D_HEATER_SV + 1:02d} 설정 온도(×0.1 ℃) — 명령 13(히터 적용)이 반영"
    if A.D_MFC_SV <= addr < A.D_MFC_SV + 8:
        return f"MFC{addr - A.D_MFC_SV + 1} 수동 설정(원시값) — 명령 14(MFC 수동 적용)가 반영"
    if addr == A.D_PCV_SV:
        return "PCV 목표 — 명령 12(수동 적용)가 반영" if DEV.HAS_PCV else "예비(이 장비는 PCV 없음)"
    if addr == A.D_RF_SV:
        return "RF 전력 — 명령 12(수동 적용)가 반영" if DEV.HAS_RF else "예비(이 장비는 RF 없음)"
    if addr == A.D_O3_SV:
        return "O3 설정 — 명령 12(수동 적용)가 반영" if DEV.HAS_O3 else "예비(이 장비는 O3 없음)"
    if A.D_PRM_HEATER_MAX <= addr < A.D_PRM_HEATER_MAX + 12:
        return f"PRM CH{addr - A.D_PRM_HEATER_MAX + 1:02d} 과온 한계(×0.1 ℃)"
    if addr in PRM_NAMES:
        return "PRM " + PRM_NAMES[addr]
    if addr in RECIPE_AREA:
        return "레시피 표"
    return "예비"


def area_of(addr: int) -> str:
    """'guard' · 'cmd' · 'prm' · 'recipe' · 'other'(허용 목록 밖)."""
    if addr in GUARDED:
        return "guard"
    if addr in CMD_DATA:
        return "cmd"
    if addr in PRM_AREA:
        return "prm"
    if addr in RECIPE_AREA:
        return "recipe"
    return "other"


def hb_watch_s(wdt_ms: int) -> float:
    """하트비트를 지켜볼 시간 — 프로그램의 하트비트 주기는 와치독/3 까지 허용된다(설정 검사). 그 한 주기 + 여유."""
    if not 0 < int(wdt_ms) <= 60000:
        return HB_WATCH_BAD_S
    return max(HB_WATCH_S, wdt_ms / 3000.0 + 0.3)


def program_attached(mb):
    """(붙어 있나, 마지막 하트비트). 제어 프로그램이 이 PLC 에 붙어 있으면 PC 하트비트(D01000)가 바뀐다.
    ★ v0.6.2(D3): 0.7 s 두 번 읽기는 하트비트 주기가 그보다 길면(heartbeat_ms 1000 등) 놓친다 —
      PRM PC 와치독(D01100)으로 지켜볼 시간을 정하고 0.1 s 마다 읽어 바뀌면 바로 '붙어 있음'."""
    a = mb.read(A.D_PC_HB, 1)[0]
    end = time.monotonic() + hb_watch_s(mb.read(A.D_PRM_PC_WDT_MS, 1)[0])
    while time.monotonic() < end:
        time.sleep(0.1)
        b = mb.read(A.D_PC_HB, 1)[0]
        if b != a:
            return True, b
    return False, a


class Modbus:
    """작은 동기 Modbus TCP 클라이언트(읽기 3 · 쓰기 16)."""

    def __init__(self, host: str, port: int, unit: int = 1, timeout: float = 1.5):
        self.host, self.port, self.unit = host, int(port), int(unit)
        self.sock = socket.create_connection((host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.tid = 0

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _req(self, pdu: bytes) -> bytes:
        self.tid = (self.tid + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, self.unit) + pdu)
        head = self._recv(7)
        _tid, _p, length, _u = struct.unpack(">HHHB", head)
        body = self._recv(length - 1)
        if body[0] & 0x80:
            raise IOError(f"PLC 예외 응답 코드 {body[1]}")
        return body

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            part = self.sock.recv(n - len(buf))
            if not part:
                raise ConnectionError("PLC 가 연결을 끊었습니다")
            buf += part
        return buf

    def read(self, addr: int, count: int = 1) -> list:
        out = []
        while count > 0:
            n = min(125, count)
            body = self._req(struct.pack(">BHH", 3, addr, n))
            out += list(struct.unpack(">" + "H" * n, body[2:2 + 2 * n]))
            addr += n
            count -= n
        return out

    def write(self, addr: int, values: list):
        vals = [int(v) & 0xFFFF for v in values]
        self._req(struct.pack(">BHHB", 16, addr, len(vals), 2 * len(vals)) + struct.pack(">" + "H" * len(vals), *vals))


def fake_ctl(port: int, req: dict, timeout: float = 2.0):
    """가짜 PLC 제어 포트에 JSON 한 줄. 가짜 PLC 가 아니면 None."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout) as s:
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            f = s.makefile("r", encoding="utf-8")
            line = f.readline()
            return json.loads(line) if line else None
    except (OSError, ValueError):
        return None


def _bits(word, defs, ok_key=None):
    on = []
    for d in defs:
        if (int(word) >> d["bit"]) & 1:
            on.append(d.get("tag") or d.get("name"))
    return " ".join(on) if on else "—"


def _conv(config_path: str):
    # 설정만 읽고 data/ 에는 쓰지 않는다. --config 가 없으면 프로그램과 같은 기본 설정(config.json, 없으면 예시)(C5)
    cfg, used, what = _pkg.load_config(config_path)
    print(f"설정: {what} {used}", flush=True)
    return _pkg.mod("convert").Converters(cfg)


def status_text(mb: Modbus, conv) -> str:
    s = mb.read(0, 82)
    alm = [d["name"] for d in DEV.ALARMS0 if (s[A.D_ALARM0] >> d["bit"]) & 1]
    alm += [d["name"] for d in DEV.ALARMS1 if (s[A.D_ALARM1] >> d["bit"]) & 1]
    heat = [f"CH{i + 1} {_heater(s[A.D_HEATER_PV + i]):.1f}" for i in range(DEV.HEATER_COUNT)]
    mfc = []
    for i in range(DEV.MFC_COUNT):
        sc = conv.mfc.get(i + 1)
        v = sc.to_eng(s[A.D_MFC_PV + i]) if sc and sc.full else None
        mfc.append(f"MFC{i + 1} {'—' if v is None else f'{v:.1f}'}")
    lines = [
        f"{time.strftime('%H:%M:%S')}  {DEV.NAME} @ {mb.host}:{mb.port}  ID 0x{s[A.D_DEVICE_ID]:04X}"
        f"{'' if s[A.D_DEVICE_ID] == DEV.DEVICE_ID else f' (이 장비 0x{DEV.DEVICE_ID:04X} 아님!)'}  PLC 하트비트 {s[A.D_PLC_HB]}",
        f"  장비 상태 {A.STATE_NAMES.get(s[A.D_STATE], s[A.D_STATE])} · 시퀀서 {A.SEQ_NAMES.get(s[A.D_SEQ_STATE], s[A.D_SEQ_STATE])}"
        f" (블록 {s[A.D_SEQ_BLOCK]} · 스텝 {s[A.D_SEQ_STEP]} · 회차 {A.dword(s[A.D_SEQ_BLOCK_PASS], s[A.D_SEQ_BLOCK_PASS + 1])})"
        f" · 마지막 명령 {s[A.D_ACK_NO]} → 결과 {s[A.D_ACK_RESULT]}",
        f"  압력 CVG {conv.cvg.to_torr(s[A.D_CVG_RAW]):.3g} Torr" + (
            f" · CM {conv.cm.to_torr(s[A.D_CM_RAW]):.4g}" if conv.cm.installed else ""),
        "  " + " · ".join(heat),
        "  " + " · ".join(mfc),
        f"  밸브 출력 {_bits(s[A.D_VALVE_OUT], DEV.VALVES)}",
        f"  보조 출력 {_bits(s[A.D_AUX_OUT], DEV.AUX)}",
        f"  입력0 {_bits(s[A.D_INPUT0], DEV.INPUTS0)}",
        f"  입력1 {_bits(s[A.D_INPUT1], DEV.INPUTS1)}",
        f"  인터락 {_bits(s[A.D_INTERLOCK], DEV.INTERLOCKS)}",
        f"  알람 {' · '.join(alm) if alm else '없음'}{' (새 알람)' if s[A.D_ALARM_NEW] else ''}",
    ]
    if DEV.HAS_RF:
        lines.append(f"  RF 순방향 {conv.rf.to_eng(s[A.D_RF_FWD_RAW]) or 0:.1f} W · 반사 {conv.rf.to_eng(s[A.D_RF_REF_RAW]) or 0:.1f} W"
                     + (f" · PCV {conv.pcv.to_eng(s[A.D_PCV_RAW]) or 0:.0f} %" if DEV.HAS_PCV else ""))
    if DEV.HAS_O3:
        lines.append(f"  O3 {conv.o3.to_eng(s[A.D_O3_RAW]) or 0:.1f}")
    return "\n".join(lines)


def _heater(raw) -> float:
    raw = int(raw)
    if raw >= 0x8000:
        raw -= 0x10000
    return raw / 10.0


def _refuse_eof(ans) -> bool:
    if ans is None:
        print("입력이 끝나 쓰지 않음")
        return True
    return False


def cmd_write(args, mb: Modbus, is_fake: bool) -> int:
    try:
        addr = _pkg.parse_int(args.addr)
        vals = [_pkg.parse_int(v) for v in args.values]
    except ValueError:
        print(f"주소 · 값을 읽지 못했습니다: {args.addr} {' '.join(args.values)}")
        return 2
    # ★ v0.6.2(D8): 범위 밖은 거절 — 전에는 값은 조용히 & 0xFFFF, 주소는 struct.error traceback
    why = check_range(addr, len(vals)) or next((f"값 {v} 는 -32768~65535 밖입니다" for v in vals
                                                if not -32768 <= v <= 0xFFFF), "")
    if why:
        print(f"거절: {why}")
        return 2
    addrs = list(range(addr, addr + len(vals)))
    s = mb.read(0, 30)
    dev_id = s[A.D_DEVICE_ID]
    print(f"대상: {mb.host}:{mb.port} (unit {mb.unit}) — {'가짜 PLC' if is_fake else '실제 PLC'}")
    print(f"장비 ID 0x{dev_id:04X} (이 프로그램 장비 {DEV.NAME} 0x{DEV.DEVICE_ID:04X})"
          f"{'' if dev_id in (DEV.DEVICE_ID, 0) else '  ← 다른 장비!'}")
    print(f"장비 상태 {A.STATE_NAMES.get(s[A.D_STATE], s[A.D_STATE])}")
    print(f"쓰기: D{addr:05d}~D{addrs[-1]:05d} ← {vals}")
    if dev_id not in (DEV.DEVICE_ID, 0) and not args.force:
        print("거절: 이 장비의 PLC 가 아닙니다(장비 ID 다름) — 정말 쓰려면 --force")
        return 3
    if s[A.D_STATE] in RUNNING_STATES:
        print("거절: 공정 중에는 쓰지 않습니다")
        return 3
    attached, hb = program_attached(mb)
    if attached:
        print(ATTACHED_MSG)
        return 3
    kinds = {area_of(a) for a in addrs}
    if "other" in kinds and not args.raw:
        bad = [a for a in addrs if area_of(a) == "other"]
        print(f"거절: D{bad[0]:05d} 는 쓰기 허용 목록 밖입니다(명령 데이터 D01004~D01042 · 레시피 D02000~D03119 · "
              "하트비트 · 명령 D01000~D01003 · PRM D01100~D01124 만). PLC 작업 이미지(D04xxx)는 래더가 바로 요청으로 "
              "써서 다음 스캔에 밸브가 열리거나 RF 가 켜질 수 있습니다 — 정말 써야 하면 --raw 를 주고 주소를 한 번 더 치세요")
        return 3
    # ★ v0.6.2(D1): 확인은 누적이다 — 허용 목록 밖이 섞였다고 하트비트 · 명령 · PRM · 히터의 WRITE 확인을 건너뛰지 않는다
    strong = [a for a in addrs if area_of(a) in ("guard", "prm") or a in HEATER_WORDS]
    if dev_id == 0:
        # ★ v0.6.2(D5): ID 렁 전 PLC — --force(다른 장비도 통과) 대신 이 장비 이름을 치게
        print("★ 장비 ID 없음(0 — ID 렁 전): PLC 로 장비를 확인할 수 없습니다 — 이 PLC 가 이 장비 것인지 직접 확인하세요")
        ans = _pkg.ask(f"이 장비 이름({DEVICE_WORD})을 그대로 치세요 > ")
        if _refuse_eof(ans):
            return 1
        if ans.strip() != DEVICE_WORD:
            print("취소")
            return 1
    if "other" in kinds:
        print("★ 허용 목록 밖(--raw) — 명령 12 의 검사 · 프로그램의 수동 잠금을 건너뛰고 래더가 바로 씁니다")
        ans = _pkg.ask(f"주소를 한 번 더 치세요(D{addr:05d}) > ")
        if _refuse_eof(ans):
            return 1
        try:
            same = _pkg.parse_int(ans) == addr
        except ValueError:
            same = False
        if not same:
            print("취소")
            return 1
    old = mb.read(addr, len(vals))
    if strong:
        for a, o, n in zip(addrs, old, vals):
            print(f"  D{a:05d} {word_name(a)}: {o} → {n & 0xFFFF}")
        if kinds & {"guard"}:
            print("★ 하트비트 · 명령 영역입니다 — 제어 프로그램이 돌고 있으면 명령이 겹칩니다.")
        if kinds & {"prm"}:
            print("★ PRM(와치독 · 베이스 압력 · 과온 · RF 한계)입니다 — 프로그램이 연결할 때 설정값으로 다시 씁니다.")
        if any(a in HEATER_WORDS for a in addrs):
            print("★ 히터 전원 · 설정 온도 요청 워드입니다 — 프로그램의 다음 명령 13(히터 적용)이 이 값을 그대로 반영합니다"
                  "(화면에서 히터 하나만 바꿔도 12 채널 전부를 이 워드에서 읽어 씁니다). 확인이 끝나면 되돌리세요:")
            print("  " + revert_line(args, addr, old))
        print("쓰려면 WRITE 를 치세요")
        ans = _pkg.ask("> ")
        if _refuse_eof(ans):
            return 1
        if ans.strip() != "WRITE":
            print("취소")
            return 1
    elif "other" not in kinds:
        for a in addrs:
            print(f"  D{a:05d} {word_name(a)}")
        if "cmd" in kinds:
            print("★ 명령 데이터는 요청만 놓습니다 — 명령 12/13/14 를 보내야 반영됩니다(실기 확인은 프로그램의 조작 창으로)")
        if not (args.yes and is_fake):
            ans = _pkg.ask("쓸까요? [y/N] ")
            if _refuse_eof(ans):
                return 1
            if ans.strip().lower() not in ("y", "yes"):
                print("취소")
                return 1
    # 쓰기 바로 직전에 한 번 더 — 질문하는 사이에 공정이 시작됐거나 프로그램이 붙었을 수 있다
    st, hb2 = mb.read(A.D_STATE, 1)[0], mb.read(A.D_PC_HB, 1)[0]
    if st in RUNNING_STATES:
        print("거절: 공정 중이 되었습니다 — 쓰지 않음")
        return 3
    if hb2 != hb:
        print(ATTACHED_MSG + " — 쓰지 않음")
        return 3
    mb.write(addr, vals)
    back = mb.read(addr, len(vals))
    print(f"썼습니다 — 되읽은 값 {back}")
    if any(a in HEATER_WORDS for a in addrs):
        print("되돌리기: " + revert_line(args, addr, old))
    return 0


def revert_line(args, addr: int, old: list) -> str:
    """쓰기 전 값으로 되돌리는 명령줄."""
    host = f"--host {args.host} " if args.host not in ("127.0.0.1", "localhost") else ""
    return f"python test/plc_ctl.py {host}--port {args.port} write D{addr:05d} " + " ".join(str(v) for v in old)


def check_range(addr: int, count: int) -> str:
    """주소 0~65535 · 개수 1 이상 — 아니면 이유."""
    if count < 1:
        return f"개수 {count} — 1 이상이어야 합니다"
    if not 0 <= addr <= 0xFFFF or addr + count - 1 > 0xFFFF:
        return f"주소 D{addr} (+{count}) 는 0~65535 밖입니다"
    return ""


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=f"PLC 시험 콘솔 ({DEV.KEY})")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--unit", type=int, default=1)
    ap.add_argument("--ctl-port", type=int, default=0, help="가짜 PLC 제어 포트(기본 --port + 1)")
    ap.add_argument("--config", default="", help="환산에 쓸 설정 파일(기본: 프로그램 config.json, 없으면 예시 설정)")
    ap.add_argument("--yes", action="store_true", help="y 확인 생략 — 가짜 PLC 에만('WRITE' · 주소 다시 치기는 생략 안 함)")
    ap.add_argument("--force", action="store_true", help="장비 ID 가 달라도 쓴다(쓰지 마세요 — 다른 장비 PLC 일 수 있다)")
    ap.add_argument("--raw", action="store_true", help="허용 목록 밖 주소도 쓴다(주소를 한 번 더 쳐야 한다)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--every", type=float, default=1.0)
    p.add_argument("--count", type=int, default=0, help="몇 번 보고 끝낼지(0 = Ctrl+C 까지)")
    p = sub.add_parser("read")
    p.add_argument("addr")
    p.add_argument("count", nargs="?", default="1")
    p = sub.add_parser("write")
    p.add_argument("addr")
    p.add_argument("values", nargs="+")
    sub.add_parser("ping")
    sub.add_parser("faults")
    p = sub.add_parser("fault")
    p.add_argument("key")
    p.add_argument("onoff", choices=("on", "off"))
    p = sub.add_parser("cut")
    p.add_argument("mode", choices=("blackhole", "unplug", "refuse"))
    p.add_argument("seconds", type=float)
    p.add_argument("--on", choices=("now", "cmd"), default="now",
                   help="cmd = 다음 명령 번호(D01001) 쓰기가 오는 그 요청부터 끊는다(blackhole · unplug)")
    p = sub.add_parser("id")
    p.add_argument("value", help="장비 ID — 16 진(5045 · 0x5045 · h5045)")
    p = sub.add_parser("speed")
    p.add_argument("value", type=_pkg.parse_speed)
    p = sub.add_parser("plant")
    p.add_argument("state", help="atm · vac · hot")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.config:
        _pkg.check_config(args.config)        # ★ v0.6.2(D8): 직접 준 --config 를 못 읽으면 예시로 넘어가지 않고 종료 코드 2
    ctl_port = args.ctl_port or (args.port + 1)
    fake_ops = {"ping": {"op": "ping"}, "faults": {"op": "faults"}}
    if args.cmd in fake_ops or args.cmd in ("fault", "cut", "id", "speed", "plant"):
        req = fake_ops.get(args.cmd) or {
            "fault": lambda: {"op": "fault", "key": args.key, "on": args.onoff == "on"},
            "cut": lambda: {"op": "cut", "mode": args.mode, "s": args.seconds, "on": args.on},
            "id": lambda: {"op": "id", "value": args.value},
            "speed": lambda: {"op": "speed", "value": args.value},
            "plant": lambda: {"op": "state", "name": args.state},
        }[args.cmd]()
        if args.host not in ("127.0.0.1", "localhost"):
            print("가짜 PLC 전용 명령입니다 — 실제 PLC 에는 보내지 않습니다")
            return 2
        out = fake_ctl(ctl_port, req)
        if out is None:
            print(f"가짜 PLC 제어 포트 {ctl_port} 가 답하지 않습니다 — 가짜 PLC 가 떠 있나요?")
            return 2
        if args.cmd == "faults":
            for f in out.get("faults", []):
                print(f"  {'●' if f['on'] else '○'} {f['key']:<12} {f['name']}")
        else:
            print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 1

    is_fake = args.host in ("127.0.0.1", "localhost") and bool((fake_ctl(ctl_port, {"op": "ping"}, 0.5) or {}).get("fake"))
    try:
        mb = Modbus(args.host, args.port, args.unit)
    except OSError as e:
        print(f"PLC {args.host}:{args.port} 에 연결하지 못했습니다 — {e}")
        return 2
    try:
        if args.cmd == "status":
            conv = _conv(args.config)
            n = 0
            while True:
                print(status_text(mb, conv), flush=True)
                n += 1
                if args.count and n >= args.count:
                    return 0
                time.sleep(max(0.2, args.every))
                print()
        if args.cmd == "read":
            try:
                addr, cnt = _pkg.parse_int(args.addr), _pkg.parse_int(args.count)
            except ValueError:
                print(f"주소 · 개수를 읽지 못했습니다: {args.addr} {args.count}")
                return 2
            why = check_range(addr, cnt)
            if why:
                print(f"거절: {why}")
                return 2
            vals = mb.read(addr, cnt)
            for i, v in enumerate(vals):
                print(f"D{addr + i:05d} = {v:6d}  0x{v:04X}")
            return 0
        if args.cmd == "write":
            return cmd_write(args, mb, is_fake)
    except KeyboardInterrupt:
        return 0
    except (OSError, IOError, ConnectionError) as e:
        print(f"통신 오류: {e}")
        return 2
    finally:
        mb.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
