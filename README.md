# ALD Process Control

2챔버 ALD 장비의 공정 제어·감시 프로그램입니다.
챔버마다 **별도 프로세스**로 실행되며, 한쪽이 멈춰도 다른 쪽은 영향을 받지 않습니다.

- 백엔드: Python + FastAPI (상태·명령·값 공급)
- 화면: pywebview 창 안의 HTML/CSS/JS (외부 라이브러리 없음, 오프라인 전제)
- 통신: WebSocket 1개 (`/ws`) — 상태는 서버가 주인입니다

> **현재 단계 (v0.1.0)**
> PLC 통신은 아직 없습니다. 장비 값은 **값 공급자(provider)** 인터페이스로만 들어오고,
> 이번 단계에서는 `demo` provider 가 공정을 흉내 냅니다. 화면 헤더에 **데모** 칩이 항상
> 표시되므로 실장비와 혼동할 일은 없습니다. 다음 단계에서 `plc` provider 로 갈아끼웁니다.

---

## 실행

```bash
pip install -r requirements.txt

# 챔버 1 — 화면 왼쪽 절반, 라이트 테마, 데모 시나리오 running
python backend/server.py --config config/chamber1.example.json

# 챔버 2 — 화면 오른쪽 절반, 다크 테마, 데모 시나리오 idle
python backend/server.py --config config/chamber2.example.json
```

두 개를 동시에 실행해도 충돌하지 않습니다(포트·단일 실행 뮤텍스·데이터 폴더가 전부 챔버별).

`--config` 를 생략하면 **exe 옆**(개발 중에는 프로젝트 루트)의 `config.json` 을 읽습니다.
납품할 때는 같은 exe 를 챔버별 폴더 두 곳에 두고, 폴더마다 `config.json` 을 둡니다.

개발 중에는 브라우저로도 같은 화면을 볼 수 있습니다: `http://127.0.0.1:<port>`
창 없이 서버만 띄우려면 `--headless` 를 붙입니다(검증용).

### 검증

```bash
pip install -r requirements-dev.txt
python -m pytest test -q
```

> 실제 프로그램이 떠 있는 상태에서 테스트를 돌려도 됩니다. 단일 실행 뮤텍스는
> `create_app(single_instance=False)` 로 검증 하네스에서만 꺼집니다.

---

## 폴더 구조

```
backend/
  server.py        진입점: FastAPI, 라우트, WebSocket, lifespan, --config 인자
  window.py        pywebview 창: 좌/우 반쪽 배치, 챔버별 단일 실행, 포트 대체, 종료 확인
  paths.py         BUNDLE_ROOT / DATA_ROOT / 챔버별 데이터 폴더
  logger.py        챔버별 파일 로그
  version.py       APP_NAME / APP_VERSION
  config.py        설정 로드·검증·기본값 (장비 구성의 유일한 출처)
  state.py         서버가 주인인 상태 스냅샷
  connection.py    WebSocket 연결 관리, 로컬/원격 구분, push_*
  commands.py      화면 명령 → 권한·상태 검사 → provider 호출
  loops.py         provider 샘플링(10 Hz), 트렌드 기록, telemetry 전송(5 Hz)
  trend_buffer.py  링버퍼 (1 Hz 1시간 + 압력 10 Hz 최근 10분)
  recipe_model.py  레시피 계산·검증 (순수 함수)
  storage.py       레시피 파일 I/O (원자적 쓰기, 파일명 검증)
  providers/
    base.py        provider 인터페이스  ← 장비와 나머지 코드의 유일한 경계
    demo.py        데모 공정 흉내
frontend/
  index.html
  css/tokens.css   색·간격·글꼴 변수 (light / dark) — 색은 여기에만
  css/style.css    레이아웃·공통 컴포넌트
  js/app.js        서버 통신 한 곳 (연결·재연결, send)
  js/core.js       상태 수신 → 뷰 디스패치, 탭, fit(), 토스트, 종료 모달
  js/fmt.js        숫자·단위·시간 포맷 한 곳
  js/views/        main · schematic · recipe · trend · alarm · setup
config/            chamber1.example.json · chamber2.example.json
test/              pytest
```

실행 중 만들어지는 데이터는 전부 챔버별로 갈립니다(저장소에는 담지 않습니다).

```
data/<chamber.id>/
  logs/      프로그램 로그(날짜별)
  recipes/   레시피 JSON
  datalog/   공정 데이터 로그 (이번 단계에서는 폴더만 만듭니다)
```

---

## 설계에서 지키는 것

- **상태의 주인은 서버입니다.** 화면은 값을 지어내지 않습니다. 연결이 끊기면 `—` 를
  보여주고 2초마다 다시 붙습니다.
- **장비 구성은 설정 파일에만 있습니다.** 라인 개수, 밸브 태그, 공급 방식, 히터 채널,
  인터락은 전부 `config` 에서 옵니다. 코드에는 장비 이름도 밸브 태그도 없습니다.
  자세한 항목은 [CONFIG.md](CONFIG.md) 를 보세요.
- **밸브 조합을 코드에 두지 않습니다.** "이 공급 방식은 어떤 밸브를 여는가"는
  `modes` 정의에서 계산합니다(`recipe_model.step_open_tags`).
- **안전 판정은 서버가 합니다.** 화면의 버튼 잠금은 편의일 뿐이고, 실제 차단은
  `commands.py` 가 합니다. 공정 중 수동 조작, 검증에 실패한 레시피로 시작하기,
  전구체·반응물 ALD 밸브 동시 개방은 서버가 거절합니다.
- **색은 `tokens.css` 에만 있습니다.** 다크 테마는 같은 변수 이름의 다른 값입니다.
- **오프라인 전제.** 웹폰트·CDN·외부 네트워크 요청이 하나도 없습니다.
  글꼴은 Windows 기본 탑재(Malgun Gothic / Consolas)만 씁니다.
- 단위는 Torr, °C, sccm, s 로 고정합니다. 숫자 포맷은 `fmt.js` 한 곳에서만 합니다.

통신 약속(메시지 종류와 스키마)은 [INTERFACE.md](INTERFACE.md) 에 있습니다.

---

## 원격 보기와 조작 권한

서버는 기본으로 `127.0.0.1` 에만 바인드합니다.
`server.host` 를 `0.0.0.0` 으로 바꾸면 다른 PC에서도 화면을 볼 수 있지만,
**조작 명령은 이 PC(루프백) 접속에서만 받습니다.**

- 원격 접속: `state` · `telemetry` · `log` 수신과 레시피 조회만 허용
- 원격의 조작 명령: 거절 + 파일 로그 기록 + 화면 알림
- 판정은 서버가 합니다(화면 잠금은 개발자 도구로 풀 수 있으므로 믿지 않습니다)

관리자 PIN 기능은 이번 단계에서 화면 표시만 합니다. 코드에는 기본 PIN 이나 그 해시를
두지 않으며, 나중에 저장할 때도 데이터 폴더(추적 제외)에 해시로만 둡니다.

---

## 빌드

```bash
pyinstaller build.spec --clean --noconfirm
# 결과: dist/ALDControl/ALDControl.exe
```

`config.json` 과 `data/` 는 번들에 넣지 않습니다(읽기 전용 임시 폴더로 가서 저장이
유실됩니다). 배포할 때 exe 폴더에 `config.json` 을 함께 둡니다.

버전은 `backend/version.py` 에서 관리합니다.

---

Copyright (c) 2026 VANAM INC. All rights reserved.
