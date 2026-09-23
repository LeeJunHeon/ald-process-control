# INTERFACE — 서버 ↔ 화면 통신 약속

WebSocket 하나(`ws://<host>:<port>/ws`)로 주고받습니다. 모든 메시지는 JSON 객체이고
`type`(서버→화면) 또는 `cmd`(화면→서버) 키로 구분합니다.

트렌드 이력만 HTTP 로 받습니다(`GET /api/trend`). 나머지는 전부 WebSocket 입니다.

> **원칙**
> - 상태의 주인은 서버입니다. 화면은 받은 값만 그립니다.
> - `state` 는 구조가 바뀔 때, `telemetry` 는 값이 바뀔 때. 둘을 섞지 않습니다.
> - 값이 없으면 `null` 을 보냅니다. 화면은 `—` 로 그립니다. **`0` 과 반드시 구분합니다.**

---

## 1. 서버 → 화면

### 1-1. `state` — 전체 스냅샷

보내는 때: **접속 직후**, 그리고 구조가 바뀔 때(레시피 목록 변경, 공정 시작·중단, 알람 확인).

최상위 키:

| 키 | 내용 |
|---|---|
| `chamber` | id, name, subtitle, theme, accent, app_name, app_version, plc_addr, config_file |
| `conn` | ok, ts |
| `demo` | enabled, scenario — `enabled: true` 면 화면에 **데모** 칩을 항상 띄웁니다 |
| `process` | 아래 `telemetry.process` 와 같은 구조 |
| `lines` | 배열. id, kind, side, label, material, heated, enabled, generator, mfc, valves, **modes** |
| `modes` | `{모드id: {label, open[], kinds[]}}` |
| `always_open` | 공정 중 항상 열리는 밸브 태그 배열 |
| `chamber_io` | vent / rv / tv / dry_pump / hot_trap |
| `gauges` | baratron / convectron (label, unit, note) |
| `heaters` | 배열. id, label, line, default_sv, max, dev_warn, stable_band, stable_sec, enabled |
| `interlocks` | 배열. id, label |
| `alarm_cfg` `vacuum` `plc_cfg` `log_cfg` `server_cfg` | 설정 탭이 그대로 보여 주는 값 |
| `alarms` | 현재 알람 배열 |
| `alarm_history` | 알람 이력 배열(최근이 앞) |
| `recipes` | 레시피 이름 배열 |
| `logs` | 공정 로그 배열 (최근 500줄) |
| `access` | `{local: bool, mode: "이 PC" \| "보기 전용"}` ← **연결마다 값이 다릅니다** |
| `live` | 접속 직후 화면이 바로 그릴 수 있도록 동봉하는 `telemetry` 한 벌 |

`lines[].modes` 는 그 라인이 실제로 지원하는 공급 방식만 담습니다. 화면은 밸브 조합
규칙을 따로 알 필요가 없습니다(계산은 서버의 `config.line_modes`).

### 1-2. `telemetry` — 자주 바뀌는 값 (5 Hz)

```jsonc
{
  "type": "telemetry",
  "ts": 1758600000.0,          // epoch 초
  "clock": "11:42:05", "date": "2026-09-23",
  "plc":   { "connected": true, "hb_ok": true, "rtt_ms": 12 },
  "valves": { "P1-ALD": true, "PN-ALD": true },        // 태그: 열림 여부
  "mfc":    { "P1": { "sv": 0.0, "pv": 0.1 } },        // sccm, 미장착이면 null
  "heaters":{ "stage": { "sv": 200.0, "pv": 199.8, "out_pct": 41, "on": true } },
  "gauges": { "baratron": 0.235, "convectron": 0.236 },// Torr
  "io":     { "dry_pump": true, "rv": true, "vent": false, "tv_pct": 35.0 },
  "interlocks": { "air": true, "water": true },        // true = 정상
  "process": { /* 아래 */ },
  "alarms":  [ /* 아래 */ ]
}
```

`process`:

| 키 | 내용 |
|---|---|
| `mode` | `idle` \| `running` \| `paused` \| `stopping` |
| `stop_after_cycle` | 사이클 후 정지 예약 여부 |
| `recipe` | 실행 중인 레시피 이름 |
| `block` / `block_count` / `block_name` | 블록 위치(0-base)와 이름 |
| `cycle` / `cycles` | 현재 사이클 / 블록 반복 횟수 |
| `step` / `step_count` / `step_name` | 스텝 위치(0-base)와 이름 |
| `step_elapsed_s` / `step_total_s` | 현재 스텝 경과 / 전체 (초) |
| `steps` | `[{name, time_s}]` — 스텝 박스 표시용 |
| `elapsed_s` / `remaining_s` / `eta` | 경과 / 남은 시간 / 종료 예정 시각 |

`alarms[]`: `{code, level, msg, ts, date, ack}` — `level` 은 `err`(중대) / `warn`(경고) / `info`(정보).

### 1-3. `log` — 공정 로그 한 줄

```json
{ "type": "log", "level": "info", "msg": "사이클 128 / 300 완료", "ts": "11:41:58" }
```

`level`: `info` \| `ok` \| `warn` \| `err`. 화면 로그와 파일 로그에 함께 남습니다.

### 1-4. `notice` — 토스트

```json
{ "type": "notice", "level": "warn", "msg": "공정 중에는 수동 조작을 할 수 없습니다" }
```

명령을 보낸 사람에게만 보낼 수도 있고(거절 사유), 전체에 보낼 수도 있습니다.

### 1-5. 요청에 대한 응답

| type | 언제 | 내용 |
|---|---|---|
| `recipe_list` | `recipe_list` 명령 | `{names: []}` |
| `recipe` | `recipe_load` 명령 | `{name, recipe, preview}` |
| `preview` | `recipe_preview` 명령 | `{preview}` |

`preview` 구조:

```jsonc
{
  "summary": { "blocks": [{name, repeat, cycle_s, total_s, seq, step_count}],
               "total_s": 7740.0, "max_cycles": 300, "soak_min": 10 },
  "errors":  [ { "level": "err", "msg": "…", "where": "블록 2 · 스텝 1" } ],
  "ok":      true,
  "opens":   [ { "block": 1, "step": 0, "tags": ["P1-OUT","P1-BYP","P1-ALD","PN-ALD","RN-ALD"] } ]
}
```

계산과 검증은 **서버에서만** 합니다(`recipe_model.py`). 화면이 같은 계산을 따로 하면
"화면에는 통과인데 시작하면 거절"이 생깁니다.

---

## 2. 화면 → 서버

`{"cmd": "...", ...}` 형태입니다.

| cmd | 인자 | 하는 일 |
|---|---|---|
| `valve_toggle` | `tag`, `open` | 밸브 하나 열기/닫기 |
| `pump` | — | 펌핑 시작(러핑 열기, 벤트 닫기) |
| `vent` | — | 벤트 시작(러핑 닫기) |
| `all_close` | — | 모든 밸브 닫기 |
| `heater_apply` | `sv: {히터id: °C}` | 히터 SV 적용 |
| `process_start` | `recipe` | 레시피 검증 후 공정 시작 |
| `process_pause` | — | 일시정지 / 재개 (토글) |
| `process_stop_after_cycle` | — | 사이클 후 정지 예약 (토글) |
| `process_abort` | — | 즉시 중단 (ALD 밸브 닫고 N2 퍼지 유지) |
| `alarm_ack` | `code` (없으면 전체) | 알람 확인 |
| `alarm_reset` | — | 알람 해제 |
| `recipe_list` | — | 레시피 이름 목록 |
| `recipe_load` | `name` | 레시피 + preview |
| `recipe_save` | `name`, `recipe` | 검증 통과 시 저장(원자적 쓰기) |
| `recipe_delete` | `name` | 삭제 |
| `recipe_preview` | `recipe` | 계산·검증만(저장 안 함). 편집할 때마다 호출 |
| `settings_save` | — | **이번 단계에서는 "다음 단계에서 구현" 알림만** |
| `exit` | — | 프로그램 종료 |

### 2-1. 서버가 명령을 거절하는 경우

두 겹으로 검사하며, **둘 다 서버에서** 합니다.

1. **권한** — 조작 명령은 루프백(`127.0.0.1`, `::1`) 접속에서만 받습니다.
   원격에서 온 조작 명령은 거절하고 파일 로그에 남깁니다.
   원격이 보낼 수 있는 것: `recipe_list`, `recipe_load`, `recipe_preview`.
   주소를 판정할 수 없으면 **원격으로 봅니다**(안전한 쪽으로 틀립니다).

2. **상태** — 공정 중(`running`/`paused`/`stopping`)에는
   `valve_toggle` · `pump` · `vent` · `all_close` 를 거절합니다.
   공정이 쥐고 있는 밸브를 사람이 동시에 건드리면 전구체와 반응물이 챔버에서 만납니다.

그 밖에 거절하는 경우:

- 검증에 실패한 레시피로 `process_start` (`err` 가 하나라도 있으면)
- 검증에 실패한 레시피 `recipe_save`
- 경로 탈출·Windows 금지문자가 든 레시피 이름
- 실행 중인 레시피 `recipe_delete`
- 공정 중 `exit`

거절할 때는 항상 `notice` 로 이유를 돌려줍니다. 조용히 무시하지 않습니다.

---

## 3. HTTP

| 경로 | 내용 |
|---|---|
| `GET /` | 화면(index.html). 자산 URL에 버전을 붙여 캐시를 무효화합니다 |
| `GET /css/*`, `/js/*` | 정적 자산 |
| `GET /health` | `{ok, chamber, version}` |
| `GET /api/trend?sec=120\|600\|3600` | 트렌드 이력 |

`/api/trend` 응답:

```jsonc
{
  "now": 12345.6,            // 서버 monotonic 시계 — 화면이 벽시계로 옮깁니다
  "sec": 120,
  "slow": [ { "t": …, "p": 0.235, "c": 0.236, "h": {히터id: pv}, "m": {라인id: pv} } ],
  "fast": [ { "t": …, "p": 0.31 } ],   // 압력 전용 10 Hz — 0.1 s 펄스가 보이도록
  "pulses":[ { "t": …, "line": "TMA 펄스" } ]
}
```

`sec` 은 서버가 10 ~ 3600 으로 잘라냅니다. 이후 값은 화면이 `telemetry` 로 이어 붙입니다.
"공정 전체" 범위는 이번 단계에서 비활성입니다.

---

## 4. 값 공급자(provider) 경계

장비 값은 `backend/providers/base.py` 의 인터페이스로만 들어옵니다.
**provider 밖의 코드는 값이 데모에서 왔는지 실장비에서 왔는지 알지 못합니다.**

- 읽기: `read_snapshot()` → 위 `telemetry` 의 원재료
- 쓰기: `start_process` / `pause` / `stop_after_cycle` / `abort` /
  `set_valve` / `pump` / `vent` / `all_close` / `set_heater_sv` / `ack_alarm` / `reset_alarms`
- 모든 메서드는 예외를 던지지 않고 `(성공여부, 사유)` 를 돌려줍니다.
  장비를 한 번 못 읽었다고 서버 루프가 죽으면 화면 전체가 멈춥니다.

다음 단계에서 `providers/plc.py` 를 추가하고 `config.demo.enabled` 로 갈아끼웁니다.
이 문서의 메시지 계약은 그대로입니다.
