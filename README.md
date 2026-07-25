# LLM Usage Meter

여러 LLM 서비스의 **사용량·크레딧·한도**를 트레이(메뉴 바) 아이콘 하나로 모아 보는 크로스 플랫폼(macOS / Windows / Linux) 앱입니다. 아이콘을 클릭하면 아이콘 옆에 Qt 팝업이 열리고, 서비스마다 카드 하나가 표시됩니다.

지원 서비스:

- **Codex** (ChatGPT) — 플랜 사용량 윈도우, 구매한 크레딧 잔액, 사용량 한도 재설정권
- **OpenCode** — Go 플랜 사용량(5시간 / 주간 / 월간)과 Zen 크레딧 잔액을 별도 섹션으로 구분

Cursor AI 등 다른 서비스는 provider 패키지를 추가하는 방식으로 확장합니다([서비스 추가](#서비스-추가) 참고).

## 설치

Python 3.10+ 필요. 저장소 폴더에서:

- **macOS**: `setup.command` 더블클릭 (터미널에서는 `./setup.sh`)
- **Windows**: `setup.bat` 더블클릭 (python.org에서 Python 설치 시 "Add to PATH" 체크)
- **Linux**: `./setup.sh`

설치 스크립트는 프로젝트 안에 `.venv` 가상 환경을 만들고 필요한 패키지를 설치합니다. 실행 중인 인스턴스가 있으면 먼저 중지하므로, 업데이트할 때도 같은 스크립트를 실행하면 됩니다.

## 실행

설치가 끝나면 앱이 자동으로 시작됩니다. 이후 다시 실행할 때는:

- **macOS**: `run.command` 더블클릭 (터미널에서는 `./run.sh`)
- **Windows**: `run.bat` 더블클릭
- **Linux**: `./run.sh`

실행 스크립트는 앱을 터미널에서 **분리(detach)** 해서 띄우므로 터미널 창을 닫아도 앱은 계속 동작합니다. 로그는 `~/.llm-usage-meter/app.log` (Windows: `%USERPROFILE%\.llm-usage-meter\app.log`)에 기록됩니다.

터미널에 붙여서 디버그하려면 직접 실행하세요.

```sh
.venv/bin/python -m llm_meter          # Windows: .venv\Scripts\python.exe -m llm_meter
```

명령줄 옵션:

| 옵션 | 설명 |
| --- | --- |
| `--stop` | 실행 중인 인스턴스를 중지합니다. |
| `--replace` | 실행 중인 인스턴스를 중지한 뒤 시작합니다(실행 스크립트가 사용). |
| `--uninstall` | 앱을 중지하고 자동 시작 등록·저장된 로그인·데이터 폴더를 제거합니다. |

UI 언어는 OS 언어를 따릅니다(한국어 또는 영어). `LLM_METER_LANG=ko` 또는 `LLM_METER_LANG=en` 으로 강제할 수 있습니다.

## 팝업 사용법

트레이 아이콘을 클릭하면 아이콘 아래에 팝업이 열립니다. 팝업 밖을 클릭하거나 `Esc`를 누르면 닫히고, 아이콘을 다시 클릭해도 닫힙니다.

- **헤더**: `⟳` 모두 새로고침, `⚙` 설정 메뉴
- **카드**: 서비스 이름, 플랜 배지, `⋯` 서비스별 메뉴
- **섹션 제목**: 클릭하면 해당 웹페이지가 기본 브라우저로 열립니다
- **푸터**: 마지막 갱신 시각, 종료

트레이 아이콘은 사용률이 가장 높은 값을 바늘로 표시하는 게이지입니다. macOS에서는 template image로 처리되어 메뉴 바 색상에 자동으로 맞춰지고, Windows에서는 작업 표시줄 테마(`SystemUsesLightTheme`)에 따라 검정/흰색으로 바뀝니다.

### 설정 메뉴 (`⚙`)

- **새로고침 주기**: 5분 / 10분 / 30분 / 60분
- **표시할 서비스**: 카드로 표시할 서비스 선택
- **로그인 시 자동 시작**: OS 로그인 시 자동 실행
- **데이터 폴더 열기**: `~/.llm-usage-meter`
- **종료**

초기화·만료 카운트다운은 API를 다시 호출하지 않고 1분마다 로컬에서 갱신됩니다. 실제 API 조회는 새로고침 주기를 따릅니다.

## Codex 카드

```text
Codex  PLUS                                    ⋯
플랜 사용량 ↗
  주간                                        85%
  ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▭▭▭▭   3일 5시간 후 초기화
크레딧 ↗
  잔액                                     $12.50
  로컬 약 40~120회 · 클라우드 약 10~30회
사용량 한도 재설정 1개
  Full reset
  사용 가능 · 2026. 8. 13. 오전 2:25 만료 (17일 후)
```

- **플랜 사용량**: API의 `used_percent` 기준으로 윈도우(5시간 / 주간 등)마다 사용률과 초기화 카운트다운을 표시합니다.
- **크레딧**: 플랜 할당량을 넘겨 사용할 때 쓰이는 구매 크레딧 잔액입니다. 크레딧이 있으면 로컬/클라우드 메시지 예상 횟수도 함께 보여주고, 초과 사용 한도에 도달하면 안내 문구를 표시합니다.
- **사용량 한도 재설정**: 보유한 재설정권의 종류·상태·만료 시각을 **조회만** 합니다. 이 앱은 재설정권을 적용하거나 소모하지 않습니다.

`⋯` 메뉴: 지금 새로고침, 사용량 페이지 열기(`https://chatgpt.com/#settings/Usage`), 통계 페이지 열기(`https://chatgpt.com/codex/cloud/settings/analytics`), 로그인/로그아웃.

### Codex 로그인

`⋯` 메뉴 또는 카드의 버튼에서 **OpenAI로 로그인...** 을 선택하면 OpenAI device-code(인증 코드) 로그인이 시작됩니다.

1. 앱이 일회용 장치 코드를 요청합니다.
2. 장치 코드를 클립보드에 복사하고 OpenAI 확인 페이지를 기본 브라우저로 엽니다.
3. 브라우저에서 OpenAI 계정으로 로그인한 뒤 표시된 코드를 입력합니다.
4. 승인이 완료되면 앱이 OAuth 토큰을 교환해 OS credential 저장소에 보관합니다.
5. 로그인 직후 사용량을 조회하고, 이후 설정한 주기로 갱신합니다.

로그인은 15분 안에 완료해야 합니다. 진행 상태는 카드에 표시되고, 실패 원인은 로그에 기록됩니다. Codex CLI와 동일한 공개 OAuth client ID와 device-code 흐름을 사용하지만, Codex CLI의 `auth.json`을 비롯한 다른 로그인 파일은 읽거나 수정하지 않습니다.

API 호출에서 HTTP 401 또는 403이 발생하면 refresh token으로 자격 증명을 강제 갱신하고 해당 작업을 **한 번만** 재시도합니다. 네트워크 오류는 저장된 로그인을 삭제하지 않으며 다음 주기에 다시 시도합니다.

## OpenCode 카드

```text
OpenCode                                       ⋯
Go 플랜 사용량 ↗
  5시간                              $0.00 / $12
  ▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭   4시간 59분 후 초기화
  주간                               $5.70 / $30
  ▬▬▬▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭▭   1일 6시간 후 초기화
  월간                              $37.20 / $60
  ▬▬▬▬▬▬▬▬▬▬▬▬▭▭▭▭▭▭▭▭   4일 16시간 후 초기화
Zen 크레딧 ↗
  잔액                                    $16.13
  이번 달 사용                        $3.87 / $20
  자동 충전                                  꺼짐
```

- **Go 플랜 사용량**: 콘솔이 알려주는 사용률(%)에 기간별 한도를 곱해 금액으로 환산합니다. 기본 한도는 5시간 $12, 주간 $30, 월간 $60이며 설정 파일에서 바꿀 수 있습니다.
- **Zen 크레딧**: 크레딧 잔액, 이번 달 사용액과 한도, 자동 충전 설정을 표시합니다. 워크스페이스가 Zen 크레딧으로 결제되는 경우 Go 섹션에 안내 문구가 추가됩니다.

`⋯` 메뉴: 지금 새로고침, Go 사용량 페이지 열기, Zen 크레딧 페이지 열기, 통계 페이지 열기, 클립보드에서 세션 키 붙여넣기, 세션 키 입력, 로그아웃.

### OpenCode 세션 키

OpenCode 콘솔은 공개 API가 없어 브라우저 세션 쿠키를 그대로 사용합니다. 브라우저 쿠키를 직접 읽는 방식은 지원하지 않습니다(윈도우 크롬에서 사실상 동작하지 않아 제거했습니다). 세션 키는 **클립보드에서 붙여넣기** 또는 **직접 입력** 으로만 등록합니다.

1. <https://opencode.ai/auth> 에서 로그인합니다.
2. 개발자 도구(F12) → 애플리케이션/저장소 → 쿠키 → `https://opencode.ai`
3. `auth` 쿠키의 값을 복사합니다.
4. 카드의 **세션 키 입력...** 에 붙여넣습니다. (`auth=`, 따옴표, 끝의 `;`는 자동으로 정리됩니다.)

워크스페이스 ID는 첫 조회 때 `/auth` 리다이렉트로 찾아 설정 파일에 캐시합니다. 세션이 만료되면 저장된 키를 지우고 알림을 띄웁니다.

## 자격 증명 보안

Codex OAuth 토큰과 OpenCode 세션 키는 OS 보안 저장소에 보관합니다.

- **macOS**: 키체인
- **Windows**: Windows Credential Manager
- **Linux**: `keyring`이 선택한 시스템 credential backend

항목은 `LLM Usage Meter` 서비스 이름으로 저장되며, 비밀 정보는 `config.json`이나 프로젝트 파일에 기록하지 않습니다. Windows Credential Manager의 항목 크기 제한을 넘는 값은 압축한 뒤 여러 항목으로 나누어 저장하고, 마지막 manifest 교체를 commit 지점으로 사용합니다. 따라서 갱신 중에도 이전 값 또는 새 값만 읽습니다. 교체가 끝나면 이전 세대 조각을 제거합니다.

## 설정

비밀이 아닌 설정은 `~/.llm-usage-meter/config.json` (Windows: `%USERPROFILE%\.llm-usage-meter\config.json`)에 저장됩니다.

```json
{
  "refresh_interval": 10,
  "providers": {
    "codex": { "enabled": true },
    "opencode": {
      "enabled": true,
      "workspace_id": "wrk_...",
      "limits": { "rolling": 12, "weekly": 30, "monthly": 60 }
    }
  }
}
```

- `refresh_interval`: API 갱신 주기(분). `5`, `10`, `30`, `60` 중 하나이며 설정 메뉴에서도 변경할 수 있습니다.
- `providers.<id>.enabled`: 카드 표시 여부.
- `providers.opencode.limits`: Go 플랜의 기간별 금액 한도(달러). 요금제가 다르면 이 값을 조정하세요.
- 로그 파일과 단일 인스턴스 잠금 파일도 같은 폴더에 저장됩니다.

### 부팅 시 자동 시작

설정 메뉴의 **로그인 시 자동 시작**을 켜면 OS 로그인 시 앱이 실행됩니다.

- macOS: `~/Library/LaunchAgents/local.llm-usage-meter.plist` (LaunchAgent)
- Windows: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (pythonw로 콘솔 창 없이 실행)
- Linux: `~/.config/autostart/llm-usage-meter.desktop`

등록 정보에는 절대 경로가 들어가므로 프로젝트 폴더를 옮기면 다음 실행 때 경로를 갱신합니다. 중복 실행은 잠금 파일로 차단되며, 자동 시작된 상태에서 실행 스크립트를 다시 실행하면 기존 인스턴스를 교체하므로 트레이 아이콘이 두 개 생기지 않습니다.

## 삭제

삭제 스크립트는 실행 전에 확인을 묻고, 앱을 중지한 뒤 자동 시작 등록·OS credential 저장소의 로그인·데이터 폴더(`~/.llm-usage-meter`)·`.venv`를 제거합니다.

- **macOS**: `uninstall.command` 더블클릭 (터미널에서는 `./uninstall.sh`)
- **Windows**: `uninstall.bat` 더블클릭
- **Linux**: `./uninstall.sh`

프로젝트 폴더 자체는 남으니 완전히 지우려면 삭제 후 폴더를 직접 지우세요.

## 서비스 추가

`llm_meter/providers/<서비스>/` 패키지를 만들고 `Provider`를 상속한 클래스에서 다음을 구현한 뒤 `llm_meter/providers/__init__.py`의 목록에 등록하면 카드가 생깁니다. UI 코드는 수정하지 않습니다.

| 메서드 | 실행 스레드 | 역할 |
| --- | --- | --- |
| `is_authenticated()` | 워커 | 로그인 여부 |
| `load()` | 워커 | 네트워크 조회, 원본 데이터 반환 |
| `render(data)` | GUI | 원본 데이터를 섹션·지표로 변환 |
| `menu()` / `primary_action()` | GUI | `⋯` 메뉴와 로그인 버튼 |

`load()`와 `render()`가 분리되어 있어, 카운트다운은 네트워크 요청 없이 1분마다 다시 계산됩니다.

## 테스트

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Windows:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

테스트는 Codex 사용량·크레딧·재설정권 파싱과 401/403 단일 재시도, OpenCode 콘솔 SSR 파서와 Zen 빌링 추출, 자격 증명 압축·분할 저장, 설정 파일 읽기/쓰기, 금액·기간 표시, 팝업 렌더링(오프스크린 Qt)을 검사합니다. 실제 계정이나 네트워크 호출은 사용하지 않습니다.

## 문제 해결

- **로그**: `~/.llm-usage-meter/app.log`
- **앱이 보이지 않음**: 숨겨진 트레이 아이콘 영역을 확인하고 `run.command` / `run.bat`으로 교체 실행하세요.
- **Codex 로그인이 완료되지 않음**: 브라우저 승인을 15분 안에 마쳤는지 확인하고, 로그에서 `Device login approved`와 `OAuth credentials saved`를 확인하세요.
- **Codex 로그인이 만료됨**: 로그아웃한 뒤 다시 로그인하세요. 취소·만료·폐기된 refresh token은 복구할 수 없습니다.
- **OpenCode 세션이 만료됨**: `auth` 쿠키를 다시 복사해 세션 키를 등록하세요. 콘솔에서 로그아웃하면 키도 무효화됩니다.
- **OpenCode 사용량을 읽지 못함**: 콘솔 HTML 구조가 바뀐 경우입니다. 마지막 응답이 `~/.llm-usage-meter/opencode-last-fetch.html`에 저장되니 로그와 함께 확인하세요.
- **네트워크 오류**: 로그인은 유지되며 다음 주기에 자동 재시도합니다.
- **아이콘 색상이 배경과 맞지 않음(Windows)**: 작업 표시줄 테마를 5초마다 확인합니다. 잠시 기다리거나 앱을 다시 실행하세요.

## 내부 API 주의사항

이 앱은 문서화되지 않은 내부 엔드포인트를 사용합니다.

```text
GET https://chatgpt.com/backend-api/wham/usage
GET https://chatgpt.com/backend-api/wham/rate-limit-reset-credits
GET https://opencode.ai/workspace/<workspace>/go       (SSR HTML 파싱)
GET https://opencode.ai/auth, /auth/status
```

각 서비스는 엔드포인트, 응답 필드, 필요한 헤더, 인증 방식, 접근 정책을 예고 없이 변경하거나 제거할 수 있습니다. 로컬 설치가 그대로여도 앱이 동작하지 않을 수 있으며, 이는 이 프로젝트가 가진 본질적인 유지보수·호환성 위험입니다.

## 상표 및 비공식 앱 고지

LLM Usage Meter는 독립적으로 제작된 유틸리티입니다. OpenAI 또는 OpenCode의 공식 앱이 아니며, 두 회사가 보증·후원하거나 제휴한 프로젝트가 아닙니다.

ChatGPT, Codex, OpenAI, Blossom 로고와 OpenCode 관련 표장은 각 소유자의 상표 또는 자산입니다. `assets/codex-blossom.ico`는 모니터링 대상 서비스를 카드에서 식별하기 위한 목적으로만 사용하며, 단색 glyph로 비율을 유지해 크기만 조정합니다. 이 앱 자체의 브랜드로 표시하지 않습니다.
