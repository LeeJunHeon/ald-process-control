# CONFIG — 설정 파일 항목

**이 파일이 장비 구성의 유일한 출처입니다.** 라인 개수, 밸브 태그, 공급 방식, 히터 채널,
인터락은 전부 여기서 옵니다. 코드에는 장비 이름도 밸브 태그도 없습니다 —
현장마다 구성이 다르고, 코드를 고치지 않고 납품할 수 있어야 하기 때문입니다.

- 읽는 위치: `--config <경로>`, 없으면 **exe 옆**(개발 중에는 프로젝트 루트)의 `config.json`
- 예시: `config/chamber1.example.json`, `config/chamber2.example.json`
- 검증: 기동할 때 `config.py` 가 검사합니다. **문제가 있어도 기동은 계속하고**
  화면 로그와 파일 로그로 알립니다(설정 한 줄 때문에 화면이 안 뜨면 원인조차 볼 수 없습니다).

---

## chamber

| 키 | 설명 |
|---|---|
| `id` | 챔버 식별자. **데이터 폴더(`data/<id>/`)와 단일 실행 뮤텍스 이름에 들어갑니다.** 챔버마다 반드시 달라야 합니다 |
| `name` | 화면 배지와 창 제목에 쓰는 이름 (예: `ALD-1`) |
| `subtitle` | 헤더 부제 |

## server

| 키 | 기본 | 설명 |
|---|---|---|
| `host` | `127.0.0.1` | 바인드 주소. `0.0.0.0` 으로 바꾸면 원격에서 **보기만** 됩니다 |
| `port` | `8001` | 챔버마다 다르게. 이미 쓰이고 있으면 `port+1` … 최대 10개까지 자동으로 찾습니다 |

## ui

| 키 | 값 | 설명 |
|---|---|---|
| `theme` | `light` \| `dark` | `<html data-theme="...">` 로 들어갑니다 |
| `accent` | 색 | 챔버 강조색. **헤더 배지에만** 씁니다 |
| `window.side` | `left` \| `right` | Windows 작업 영역(작업표시줄 제외)의 정확히 절반 폭 × 전체 높이로 배치합니다. DPI 125 %·150 % 에서도 정확히 절반이 되도록 창을 띄운 뒤 물리 픽셀로 다시 맞춥니다. 실패하면 화면 크기 기준으로 폴백합니다 |

## plc

이번 단계에서는 **화면 표시만** 합니다. 다음 단계에서 실제로 씁니다.

`ip`, `port`, `unit_id`, `poll_ms`, `heartbeat_s`, `timeout_s`

> 예시 설정에는 `192.168.10.x` 형태의 **예시 값**만 들어 있습니다. 현장 주소는 납품할 때
> 넣고, 그 파일은 저장소에 올리지 않습니다.

## demo

| 키 | 설명 |
|---|---|
| `enabled` | `true` 면 데모 provider 를 씁니다. 화면에 **데모** 칩이 항상 뜹니다 |
| `scenario` | `running`(공정 중간부터 진행) \| `idle`(대기, 시작 조건 충족) |
| `sample` | 샘플 레시피 내용. 레시피 폴더가 비어 있을 때만 하나 만들어 줍니다 |

`demo.sample` 항목: `name`, `memo`, `film`, `precursor_line`, `precursor_mode`,
`reactant_line`, `reactant_mode`, `carrier_sccm`, `stage_sv`, `soak_min`, `throttle_pct`,
`cycles`, `pre_purge_s`, `post_purge_s`, `pulse_p_s`, `purge1_s`, `pulse_r_s`, `purge2_s`

> 막질·전구체 조합을 코드에 두지 않으려고 샘플 내용도 설정에서 읽습니다.
> `sample` 이 없으면 샘플을 만들지 않습니다. 실장비 모드에서도 만들지 않습니다.

---

## lines — 가스 라인 (배열, 개수 가변)

`enabled: false` 인 라인은 화면에 **미장착**으로 표시되고 레시피에서 쓸 수 없습니다.

### id 규칙

| id | 뜻 |
|---|---|
| `PN` | 전구체측 N2 |
| `P1` ~ `P3` | 전구체 |
| `RN` | 반응물측 N2 |
| `R1` ~ `R2` | 반응물 |

### kind — 배관도 행 템플릿을 정합니다

| kind | 배관 |
|---|---|
| `n2` | MFC → ALD |
| `canister` | MFC → IN → 캐니스터 → OUT → ALD (캐리어 우회 `BYP` 가 있을 수 있음) |
| `gas` | MFC → 발생기(선택) → ALD |

### 필드

| 키 | 설명 |
|---|---|
| `side` | `precursor` \| `reactant`. **전구체·반응물 동시 개방 검출의 기준입니다** |
| `label` | 용도 (예: `TMA 캐리어`) |
| `material` | 물질 (예: `TMA`) |
| `heated` | 가열 캐니스터 여부. 배관도에서 색으로 구분합니다 |
| `generator` | `gas` 라인의 발생기 이름 (선택) |
| `mfc` | `{full_scale, gas, purge_sccm}` — `purge_sccm` 은 N2 라인의 퍼지 유량 |
| `valves` | 역할별 태그. **`<라인id>-<역할>` 규칙을 지켜야 합니다** |

### 밸브 역할

`IN` / `OUT` / `BYP` / `ALD` — 태그는 반드시 `P1-IN`, `P1-ALD` 처럼 씁니다.
모든 라인에 `ALD` 는 있어야 합니다.

---

## modes — 공급 방식

**밸브 조합을 코드에 하드코딩하지 않기 위한 장치입니다.**
"이 방식은 어떤 밸브를 여는가"는 전부 여기서 나옵니다.

```jsonc
"modes": {
  "vapor":   { "label": "증기압", "open": ["OUT", "BYP", "ALD"], "kinds": ["canister"] },
  "carrier": { "label": "캐리어", "open": ["IN", "OUT", "ALD"],  "kinds": ["canister"] },
  "direct":  { "label": "직공급", "open": ["ALD"],               "kinds": ["n2", "gas"] }
}
```

| 키 | 설명 |
|---|---|
| `label` | 화면에 보이는 이름 |
| `open` | 이 방식이 여는 밸브 **역할** 목록 |
| `kinds` | (선택) 적용할 라인 종류. 없으면 모든 종류 |

**어떤 라인이 어떤 방식을 지원하는가**는 자동으로 계산합니다:
`open` 이 요구하는 역할을 그 라인이 전부 가지고 있고, `kinds` 에 맞으면 지원합니다.

- 챔버1 `P1` 은 `BYP` 가 있어 → 증기압·캐리어 둘 다 지원
- 챔버1 `P2` 는 `BYP` 가 없어 → 캐리어만 지원
- `kinds` 가 필요한 이유: 캐니스터도 `ALD` 밸브를 가지므로 "직공급"이 역할만 보면
  성립하지만, 실제로는 아무것도 흐르지 않습니다.

## process

| 키 | 설명 |
|---|---|
| `always_open` | 공정 중 항상 열리는 밸브 태그 (예: `["PN-ALD", "RN-ALD"]`). 모든 스텝의 열림 목록에 더해집니다 |

## chamber_io

| 키 | 설명 |
|---|---|
| `vent` | `{tag, label}` — 벤트 밸브 |
| `rv` | `{tag, label}` — 러핑 밸브 |
| `tv` | `{tag, label}` — 스로틀 밸브(개도 %) |
| `dry_pump` | `{label}` |
| `hot_trap` | `{label}` |

## gauges

`baratron`, `convectron` — 각각 `{label, unit, note}`.

## heaters (배열)

| 키 | 설명 |
|---|---|
| `id` | 히터 채널 식별자 |
| `label` | 화면 이름 |
| `line` | (선택) 연결된 라인 id. 화면이 `P2 캐니스터 (TTIP)` 처럼 물질명을 붙입니다 |
| `default_sv` | 기본 설정 온도 (°C) |
| `max` | 최대 허용 온도 (°C) |
| `dev_warn` | 편차 경고 기준 (±°C) |
| `stable_band` / `stable_sec` | 안정 판정 (±°C / 지속 초). **없으면 시작 조건에서 보지 않습니다** |
| `enabled` | `false` 면 OFF/미장착으로 표시하고 SV 를 편집할 수 없습니다 |

`stage_heater` (최상위): 스테이지 히터의 id. 레시피의 `stage_sv` 가 적용될 채널입니다.

## interlocks (배열)

`{id, label}`. 화면에 칩으로 나열하고 시작 조건에서 검사합니다.
`plc_hb` 는 하트비트 전용이라 인터락 묶음 표시에서 제외합니다.

## alarms

| 키 | 설명 |
|---|---|
| `mfc_dev_pct_fs` | MFC 편차 경고 기준 (% FS) |
| `mfc_delay_s` | 편차 판정 지연 (초) |
| `mfc_action` | 편차 지속 시 동작 (표시용) |

## vacuum

| 키 | 설명 |
|---|---|
| `start_base_torr` | 공정 시작 베이스 압력 기준 |
| `base_timeout_s` | 베이스 도달 제한 시간 |
| `vent_timeout_s` | 벤트 ATM 도달 제한 시간 |
| `process_dev_pct` | 공정 압력 편차 경고 (±%) |

## log

| 키 | 기본 | 설명 |
|---|---|---|
| `enabled` | `true` | 파일 로그 on/off |
| `level` | `info` | `info` \| `warn` \| `err` |
| `keep_days` | `90` | 프로그램 로그 보관 일수 |
| `datalog_interval_s` | `1` | 데이터 로그 기록 주기 (다음 단계) |
| `datalog_keep_days` | `180` | 데이터 로그 보관 일수 (다음 단계) |

## access

| 키 | 기본 | 설명 |
|---|---|---|
| `local_only` | `true` | 조작은 이 PC 에서만. 원격은 보기 전용 |

---

## 검증에서 잡는 것

| 항목 | 등급 |
|---|---|
| `chamber.id` 없음, `server.port` 범위 밖 | err |
| `ui.theme` / `ui.window.side` 값이 잘못됨 | warn |
| `modes` 비어 있음, `open` 비어 있음 | err |
| `open` 에 알 수 없는 역할, `kinds` 에 알 수 없는 라인 종류 | err |
| 라인 id 중복, `kind`/`side` 잘못됨 | err |
| 밸브 태그 규칙 위반(`P1_ALD`), 라인·역할 불일치(`P1` 에 `P2-IN`) | err |
| 밸브 태그 중복, `ALD` 밸브 없음 | err |
| `always_open` 에 없는 태그 | err |
| 히터 id 중복, 인터락 id 중복 | err |
| 히터가 없는 라인 참조, `mfc.full_scale` 잘못됨 | warn |

---

## 두 예시 설정의 차이

| | chamber1 | chamber2 |
|---|---|---|
| id / name | `ald1` / ALD-1 | `ald2` / ALD-2 |
| 포트 | 8001 | 8002 |
| 테마 / 배치 | light / left | dark / right |
| 데모 시나리오 | `running` | `idle` |
| P1 | TMA (증기압) | TDMAHf (가열 75 °C, 캐리어) |
| P2 | TTIP (가열, 캐리어) | TEMAZr |
| P3 | 미장착 | 미장착 |
| R1 / R2 | H2O (증기압) / O3 (발생기, O2 1000 sccm) | 같음 |
| 스테이지 기본 | 200 °C | 250 °C |
