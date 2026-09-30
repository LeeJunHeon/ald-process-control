# ALD 제어 프로그램

PEALD 와 Powder ALD 두 장비의 제어 프로그램입니다.
각 장비는 LS XGB PLC 한 대를 가지고, PC 한 대에서 두 프로그램이 좌우 반쪽 창으로 동시에 돕니다.

| | Powder ALD | PEALD |
|---|---|---|
| 폴더 · 패키지 | `POWDERALD/` · `powderald` | `PEALD/` · `peald` |
| 창 제목 | Powder ALD 공정 제어 | PEALD 공정 제어 |
| 테마 · 고유색 | 라이트 · 청록 | 다크 · 보라 |
| 기본 창 위치 | 왼쪽 절반 | 오른쪽 절반 |
| 웹 포트 / 시뮬레이터 포트 | 8101 / 15101 | 8201 / 15201 |
| exe | `POWDERALD_Control.exe` | `PEALD_Control.exe` |

> **현재 단계 (v0.4.2 — 3단계 뒤 추가 수정)**
> 1단계(저장소 분리 · 장비 정체성 · 설정 · PLC 통신 · 내장 시뮬레이터 · 운전 화면)에 더해
> 수동 조작 전체(밸브·MFC·히터·PCV/RF·O3 라인), 레시피(편집·검증·PLC 표 변환·올리기·
> 베이스 압력 대기 후 시작·진행 표시), 시뮬레이터 공정 실행, 공정 데이터 로그까지입니다.
>
> 0.3.1: 수동 요청의 기준을 PLC 반영 영역(`D04012~D04131`)으로 바꾸고(PC 는 요청을 따로
> 기억하지 않음), 시뮬레이터를 래더 확정 동작(내부 사본·명령 결과·펌핑/벤트·과온)에 맞췄습니다.
>
> 0.4.0: 관리자 PIN(설정 편집 전용) · 설정 편집(검증·바뀌는 항목 표·백업·PRM 재기록) ·
> 트렌드 이력(날짜별 SQLite) · 데이터 로그 보기 · exe 두 개 빌드(`build.bat`, `--selftest`).
> 설치·처음 실행 순서·백업·PIN 복구는 각 프로그램 README 의 '설치 · 배포' 에 있습니다.
>
> 0.4.1: 장비 ID(`D00019`, PEALD 'PE' / Powder 'PW') 확인 — 다른 장비의 PLC 면 아무것도 쓰지 않음,
> `plc.host` 코드 기본값 제거, 시뮬레이터 출력 단계를 래더(P60)와 같게.
>
> 0.4.2: 무거운 조회(트렌드 이력·내보내기·데이터 로그)를 작업 스레드로 옮기고 구간을 제한해
> PC 하트비트가 멈추지 않게 함(루프 지연 감시), 중단된 공정을 '정상 종료'로 기록하던 것 고침.
> **규칙: 이벤트 루프에서 큰 파일·DB 를 읽지 않는다.**

---

## 두 프로그램은 완전히 독립입니다

- 두 폴더 사이에 **import·경로 참조·공용 모듈·공용 설정·공용 데이터 폴더가 없습니다.**
- 공통 기반 코드는 양쪽에 복사해 각자 가집니다(**의도된 중복**) —
  한쪽을 고쳐도 다른 쪽은 영향이 없어야 하고, 한쪽이 멈춰도 다른 쪽은 돌아야 합니다.
- 런타임 자원도 전부 따로입니다: 웹 포트, 시뮬레이터 포트, 단일 실행 뮤텍스,
  Windows AppUserModelID, 데이터 폴더, 로그 파일 이름, exe 이름, 창 제목.
- 실행·검증·빌드는 **폴더마다 따로** 합니다. 루트에서 두 테스트를 한꺼번에 돌리지 않습니다.

장비 이름·테마·고유색·아이콘은 설정이 아니라 각 프로그램의 **장비 정의 모듈**에 고정되어
있습니다(`<패키지>/device.py`). 설정 파일을 잘못 복사해도 장비가 바뀌어 보이지 않습니다.
설정으로 바꿀 수 있는 것은 창 위치(left/right)와 포트뿐입니다.

---

## 실행

```bash
# Powder ALD
cd POWDERALD
pip install -r requirements.txt
python run.py                       # exe 옆 / 이 폴더의 config.json
python run.py --config 경로
python run.py --headless            # 창 없이 서버만 (개발·검증용)

# PEALD
cd PEALD
python run.py
```

두 개를 동시에 실행해도 충돌하지 않습니다. 개발 중에는 브라우저로도 같은 화면을 볼 수 있습니다
(`http://127.0.0.1:8101` / `:8201`).

`config.json` 이 없으면 `config/config.example.json` 으로 기동하고 화면에
**"예시 설정으로 실행 중"** 경고를 띄웁니다. 예시 설정은 내장 PLC 시뮬레이터를 켜 두었으므로
실장비 없이 바로 화면을 확인할 수 있습니다.

## 검증

```bash
cd POWDERALD && pip install -r requirements-dev.txt && python -m pytest -q
cd PEALD     && python -m pytest -q
```

주소표·비트 해석·환산 왕복·Modbus 통신·명령 핸드셰이크·하트비트·시뮬레이터 동작·
원격 보기 전용·단일 실행·설정 검증을 덮습니다.

## 빌드

```bash
POWDERALD/build.bat     (→ POWDERALD/dist/POWDERALD_Control/, 빌드 뒤 --selftest 자동)
PEALD/build.bat         (→ PEALD/dist/PEALD_Control/)
```

`config.json` 과 `data/` 는 번들에 넣지 않습니다(읽기 전용 임시 폴더로 가서 저장이
유실됩니다). 배포할 때 exe 폴더에 함께 둡니다. 설치할 PC 에는 **WebView2 런타임**이 필요합니다.

---

## 저장소 구조

```
README.md  .gitignore  .gitattributes
POWDERALD/                  Powder ALD 제어 프로그램
  README.md                 설정 항목 · PLC 통신 약속 · 화면 통신 약속
  run.py                    진입점
  powderald/                파이썬 패키지 (서버 · PLC 통신 · 시뮬레이터 · 장비 정의)
  frontend/                 index.html · css · js
  assets/                   아이콘
  config/config.example.json
  tests/                    pytest (이 폴더에서만 실행)
  pytest.ini  requirements.txt  requirements-dev.txt  build.spec
PEALD/                      PEALD 제어 프로그램 — 같은 구조, 패키지 이름 peald
```

## 추적하지 않는 파일

현장 값과 운전 기록은 저장소에 올리지 않습니다(`.gitignore`).

- `*/config.json` — 현장 IP·환산·풀스케일이 들어갑니다. 예시는 `config/config.example.json`
- `*/data/` — 프로그램 로그 · 알람 이력 · 데이터 로그 · 레시피 · (나중의) 관리자 PIN 해시
- `*/build/`, `*/dist/` — 빌드 산출물
- 스크린샷·임시 파일

예시 설정에는 `192.168.10.x` 형태의 **예시 IP** 만 들어 있습니다.

---

Copyright (c) 2026 VANAM INC. All rights reserved.
