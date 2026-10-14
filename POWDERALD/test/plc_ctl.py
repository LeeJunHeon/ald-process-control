"""test/plc_ctl.py — PLC 시험 콘솔(가짜 PLC · 실제 PLC 모두).

실기 시운전 때 상태를 한 화면에 보고, 주소 하나씩 읽고 쓰고, 가짜 PLC 에는 고장을 넣는 명령줄 도구다.
★ 제어 프로그램과 같은 PLC 에 동시에 쓰지 않는다 — 프로그램이 돌고 있으면 status · read 만 쓴다.
★ 쓰기는 대상 주소 · 장비 ID · 장비 상태를 먼저 보이고 확인을 받는다. 공정 중(준비 · 공정 중 · 일시정지 ·
  사이클 후 정지 중)이면 거절한다. 하트비트 · 명령 영역(D01000~D01003)은 'WRITE' 를 그대로 쳐야 쓴다.
  --yes(확인 생략)는 가짜 PLC 에만 듣는다(제어 포트가 답하면 가짜 PLC).

예:
    python test/plc_ctl.py --port 15301 status                  (1 s 마다, Ctrl+C 로 끝)
    python test/plc_ctl.py --host 192.168.10.12 status --count 1 (실제 PLC 한 번)
    python test/plc_ctl.py --port 15301 read D00000 20
    python test/plc_ctl.py --port 15301 write D01010 1          (확인 질문)
    python test/plc_ctl.py --port 15301 faults                  (가짜 PLC 전용 ↓)
    python test/plc_ctl.py --port 15301 fault emo on
    python test/plc_ctl.py --port 15301 cut blackhole 2.0       (refuse 3 = 연결 거부 3 s)
    python test/plc_ctl.py --port 15301 id 0x1234
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
paths = _pkg.mod("paths")

RUNNING_STATES = (A.STATE_READY, A.STATE_RUN, A.STATE_PAUSE, A.STATE_STOPPING)
GUARDED = range(A.D_PC_HB, A.D_CMD_ARG + 1)        # 하트비트 · 명령 영역


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
    import os
    import tempfile
    # 설정만 읽고 data/ 에는 쓰지 않는다 — 없는 폴더를 데이터 자리로(폴더를 만들지 않음)
    paths.set_data_root(os.path.join(tempfile.gettempdir(), "ald_plc_ctl_no_data"))
    cfg, _p, _s = _pkg.mod("config").load(config_path)
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


def cmd_write(args, mb: Modbus, is_fake: bool) -> int:
    addr = _pkg.parse_int(args.addr)
    vals = [_pkg.parse_int(v) for v in args.values]
    s = mb.read(0, 30)
    print(f"대상: {mb.host}:{mb.port} (unit {mb.unit}) — {'가짜 PLC' if is_fake else '실제 PLC'}")
    print(f"장비 ID 0x{s[A.D_DEVICE_ID]:04X} (이 프로그램 장비 {DEV.NAME} 0x{DEV.DEVICE_ID:04X})"
          f"{'' if s[A.D_DEVICE_ID] == DEV.DEVICE_ID else '  ← 다른 장비!'}")
    print(f"장비 상태 {A.STATE_NAMES.get(s[A.D_STATE], s[A.D_STATE])}")
    print(f"쓰기: D{addr:05d}~D{addr + len(vals) - 1:05d} ← {vals}")
    if s[A.D_STATE] in RUNNING_STATES:
        print("거절: 공정 중에는 쓰지 않습니다")
        return 3
    guarded = any(a in GUARDED for a in range(addr, addr + len(vals)))
    if guarded:
        print("★ 하트비트 · 명령 영역입니다 — 제어 프로그램이 돌고 있으면 명령이 겹칩니다. 쓰려면 WRITE 를 치세요")
        if input("> ").strip() != "WRITE":
            print("취소")
            return 1
    elif not (args.yes and is_fake):
        if input("쓸까요? [y/N] ").strip().lower() not in ("y", "yes"):
            print("취소")
            return 1
    mb.write(addr, vals)
    back = mb.read(addr, len(vals))
    print(f"썼습니다 — 되읽은 값 {back}")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=f"PLC 시험 콘솔 ({DEV.KEY})")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--unit", type=int, default=1)
    ap.add_argument("--ctl-port", type=int, default=0, help="가짜 PLC 제어 포트(기본 --port + 1)")
    ap.add_argument("--config", default="", help="환산에 쓸 설정 파일(기본 예시 설정)")
    ap.add_argument("--yes", action="store_true", help="확인 생략 — 가짜 PLC 에만")
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
    p.add_argument("mode", choices=("blackhole", "refuse"))
    p.add_argument("seconds", type=float)
    p = sub.add_parser("id")
    p.add_argument("value")
    p = sub.add_parser("speed")
    p.add_argument("value", type=float)
    p = sub.add_parser("plant")
    p.add_argument("state", help="atm · vac · hot")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    ctl_port = args.ctl_port or (args.port + 1)
    fake_ops = {"ping": {"op": "ping"}, "faults": {"op": "faults"}}
    if args.cmd in fake_ops or args.cmd in ("fault", "cut", "id", "speed", "plant"):
        req = fake_ops.get(args.cmd) or {
            "fault": lambda: {"op": "fault", "key": args.key, "on": args.onoff == "on"},
            "cut": lambda: {"op": "cut", "mode": args.mode, "s": args.seconds},
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
            addr, cnt = _pkg.parse_int(args.addr), _pkg.parse_int(args.count)
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
