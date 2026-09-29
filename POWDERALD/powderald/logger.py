"""
logger.py — 프로그램 로그 (날짜별 회전) + 알람 이력 파일.

로그 파일 이름에 장비 이름을 넣는다(PEALD-YYYYMMDD.log). 두 프로그램의 로그가 섞이면
어느 장비의 사건인지 구분할 수 없다.

★ 모든 PLC 명령은 (시각·명령·번호·결과·보낸 곳)을 여기 남긴다. 사고가 났을 때
  "누가 무엇을 언제 보냈는가"를 되짚을 수 있는 유일한 기록이다.
"""

import os
import csv
import glob
import time
import datetime

from . import paths
from . import device as DEV

_LEVELS = {"info": 0, "ok": 0, "warn": 1, "err": 2}

_cfg = {"level": "info", "keep": 90}
_abs_dir = None

# 로거 설정 전(import 단계)에 발생한 진단. 파일에 쓸 수 없으니 모아뒀다가 flush 한다.
_early = []
_early_flushed = False


def early(level: str, message: str):
    _early.append((level, message))


def drain_early() -> list:
    out = list(_early)
    _early.clear()
    return out


def configure(log_cfg: dict):
    global _abs_dir, _early_flushed
    log_cfg = log_cfg or {}
    _cfg["level"] = log_cfg.get("level", "info") or "info"
    try:
        _cfg["keep"] = max(0, int(log_cfg.get("keep_days", 90)))
    except (TypeError, ValueError):
        _cfg["keep"] = 90
    _abs_dir = paths.LOGS_DIR
    try:
        os.makedirs(_abs_dir, exist_ok=True)
        _cleanup()
    except Exception as e:  # noqa: BLE001
        # ★ 로그 시스템 자체의 실패라 로그로 알릴 수 없다 → print 유지(무한 재귀 방지).
        print(f"[warn] 로그 폴더 준비 실패: {e}")
    if not _early_flushed:
        _early_flushed = True
        for lv, msg in _early:
            write(lv, msg)


def _cleanup():
    if _cfg["keep"] <= 0 or not _abs_dir:
        return
    cutoff = time.time() - _cfg["keep"] * 86400
    for pat in (f"{DEV.LOG_PREFIX}-*.log", "alarms-*.csv"):
        for f in glob.glob(os.path.join(os.path.dirname(_abs_dir), "**", pat), recursive=True):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except Exception:  # noqa: BLE001
                pass


def write(level: str, message: str):
    """레벨 필터를 통과하면 오늘자 로그에 한 줄. 실패해도 앱에 영향 없음."""
    global _abs_dir
    if not _abs_dir:
        try:
            _abs_dir = paths.LOGS_DIR
            os.makedirs(_abs_dir, exist_ok=True)
        except Exception:  # noqa: BLE001
            return
    if _LEVELS.get(level, 0) < _LEVELS.get(_cfg["level"], 0):
        return
    try:
        ts = datetime.datetime.now()
        path = os.path.join(_abs_dir, f"{DEV.LOG_PREFIX}-{ts:%Y%m%d}.log")
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(f"{ts:%Y-%m-%d %H:%M:%S} [{level.upper()}] {message}\n")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 로그 기록 실패: {e}")


def command(code_name: str, no, result_text: str, origin: str):
    """PLC 명령 기록. 형식을 한 곳에 고정해 나중에 기계로 읽을 수 있게 한다."""
    write("info", f"명령 {code_name} (번호 {no}) → {result_text} · 보낸 곳 {origin}")


def alarm_event(kind: str, code: str, name: str, severity: str):
    """알람 발생·해제를 날짜별 CSV 에 남긴다. 화면 이력과 별개로 파일에 보존한다."""
    try:
        os.makedirs(paths.ALARMS_DIR, exist_ok=True)
        ts = datetime.datetime.now()
        path = os.path.join(paths.ALARMS_DIR, f"alarms-{ts:%Y%m%d}.csv")
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as fp:
            w = csv.writer(fp)
            if new:
                w.writerow(["시각", "구분", "코드", "내용", "등급"])
            w.writerow([f"{ts:%Y-%m-%d %H:%M:%S}", kind, code, name, severity])
    except Exception as e:  # noqa: BLE001
        write("warn", f"알람 이력 기록 실패: {e}")


def current_dir() -> str:
    return _abs_dir or paths.LOGS_DIR
