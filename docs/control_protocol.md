# temi 명령 채널 규격 (WebSocket `/ws/temi`)

> Android(temi 앱) 담당용. 상태: **v1 (2026-11-17)**. 바꾸고 싶은 점은 이 문서에 적어 주세요.
> 아이 대화 API(`/agent/child`)는 [temi_api.md](temi_api.md)를 보세요.

## 왜 temi가 먼저 접속하나

temi는 학교 와이파이(NAT) 뒤에 있어서 서버(EC2)가 temi로 먼저 접속할 수 없습니다.
그래서 **temi 앱이 서버로 WebSocket을 열어 두고**, 서버는 그 연결로 명령을 보냅니다.

```
temi 앱 ──(wss 연결, 계속 유지)──▶ Caddy(443) ──▶ 에이전트 서버 /ws/temi
        ◀── command ───────────────────────────────
        ── ack, result ──────────────────────────▶
```

## 접속

```
wss://<서버 도메인>/ws/temi
X-API-Key: <TEMI_API_KEY>        (temi 역할 키, /agent/child와 같은 키)
X-Device-Id: temi-classroom-1     (장치 ID: 영문·숫자·.·_·- 1~64자, /agent/child의 device_id와 같은 값)
```

- **키는 헤더로만** 보냅니다(URL 쿼리에 넣지 않음). 반드시 `wss://`(TLS)를 씁니다.
- 키가 틀리거나, temi 키가 아니거나, `X-Device-Id`가 없거나 형식이 틀리면 **접속 단계에서 HTTP 403**으로 거부됩니다. 재시도하지 말고 설정을 확인하세요.
- 같은 `device_id`로 다시 접속하면 **이전 연결은 서버가 닫습니다**(close code `4000`). 앱이 재접속할 때 이전 소켓을 정리하지 못했어도 괜찮습니다.

### 접속 직후

1. 서버 → temi: `welcome`
   ```json
   {"type": "welcome", "id": "a1b2c3d4", "device_id": "temi-classroom-1", "protocol": 1,
    "ping_interval_s": 20, "executor": "control", "server_time": "2026-11-17T10:30:00+09:00"}
   ```
   `executor`가 `mock`이면 서버가 아직 가짜 실행기 모드라서 에이전트 명령은 보내지 않습니다(연결은 유지). 단, 교사 앱 **정지 버튼의 `stop`은 mock 모드에서도 보냅니다**.
2. temi → 서버: `hello` (권장)
   ```json
   {"type": "hello", "id": "e5f6a7b8", "device_id": "temi-classroom-1", "app_version": "1.0.3", "protocol": 1,
    "locations": ["햇살반", "별빛반", "식당", "놀이방", "현관", "교사실", "충전기"]}
   ```
   `locations`는 temi에 저장된 위치 이름(`Robot.getInstance().getLocations()`). 서버 목록과 다르면 서버 로그에 경고가 남습니다.

## 메시지 공통 규칙

- WebSocket **텍스트 프레임 하나에 JSON 객체 하나**, UTF-8.
- 모든 메시지에 `type`(문자열)과 `id`(문자열)가 있습니다. `id`가 없으면 서버가 `error`로 답합니다.
- `command`의 `id`가 곧 **작업 ID(job_id)**입니다. `ack`·`result`에는 그 `id`를 그대로 넣습니다.
- 모르는 필드는 무시하세요(서버가 필드를 늘려도 앱이 깨지지 않게).

## 메시지 종류

| type | 방향 | 필드 | 설명 |
| --- | --- | --- | --- |
| `welcome` | 서버→temi | `device_id`, `protocol`, `ping_interval_s`, `executor`, `server_time` | 접속 확인 |
| `hello` | temi→서버 | `device_id`, `app_version`, `protocol`, `locations` | 앱 정보 (권장) |
| `command` | 서버→temi | `action`, `args`, `timeout_s` | 동작 명령 |
| `ack` | temi→서버 | — | 명령을 **받자마자** (실행 전에) |
| `result` | temi→서버 | `status`, `detail` | 동작이 끝났을 때 |
| `ping` / `pong` | 양쪽 | — | 연결 유지. `ping`을 받으면 같은 `id`로 `pong` |
| `error` | 서버→temi | `message` | 잘못된 메시지에 대한 서버 응답 |

### command

```json
{"type": "command", "id": "9f2c41d07a3b4e10", "action": "go_to", "args": {"location": "식당"}, "timeout_s": 120}
```

| action | args | 설명 | timeout_s |
| --- | --- | --- | --- |
| `go_to` | `{"location": "식당"}` | 저장된 위치로 이동 (`goTo`) | 120 |
| `speak` | `{"text": "손 씻고 오세요!"}` | TTS로 말하기 (`speak`) | 30 |
| `start_game` | `{"name": "숨바꼭질"}` | 게임 시작. 이름은 앱의 게임 목록과 같음: 숨바꼭질, 영어 동작 게임, 색깔 찾기, 동물 흉내 게임 | 30 |
| `start_delivery` | `{"kind": "snack", "targets": ["햇살반"]}` | 배부 시작. `kind`: `meal`(식판) / `snack`(간식). `targets`: 반 이름 또는 아이 이름 목록 | 600 |
| `stop` | `{}` | 하던 동작(이동·배부·게임·말하기) 멈춤 | 10 |

- **명령은 받은 순서대로 하나씩 실행하세요(큐).** 서버는 ack만 받고 다음 명령을 바로 보냅니다. 예를 들어 "식당 가서 간식 나눠 줘"는 `go_to`와 `start_delivery`가 잇달아 옵니다. 앞 명령의 `result`를 보낸 뒤 다음 명령을 실행해야 도착 전에 배부가 시작되지 않습니다.
- 앞 명령이 `failed`나 `rejected`로 끝나면 큐에 남은 명령을 실행하지 말고 각각 `rejected`(`detail.message: "앞 명령 실패로 취소"`)로 보내 주세요(예: 식당에 못 갔는데 배부 시작 방지).
- **`stop`은 큐를 기다리지 않고 바로** 실행합니다. 하던 동작을 멈추고, 큐에 남은 명령은 모두 `rejected`(`"stop으로 취소"`)로 보냅니다.
- ack는 큐에 넣는 순간(실행 전에) 보냅니다.
- `args`는 서버의 도구 입력과 같은 이름을 씁니다(그래서 게임은 `name`).
- 위치·게임 이름은 서버가 목록과 비교한 뒤에만 보냅니다. 그래도 앱에 없는 이름이면 `result`를 `rejected`로 보내세요.
- `timeout_s` 안에 `result`가 없으면 서버는 그 작업을 `timeout`으로 기록합니다(나중에 `result`가 오면 그 결과로 바꿉니다).

### ack — 받자마자

```json
{"type": "ack", "id": "9f2c41d07a3b4e10"}
```

- **실행을 시작하기 전에, 받는 즉시** 보내세요. 서버는 ack를 **5초** 기다립니다. 안 오면 교사에게 "temi가 응답하지 않아요"라고 알리고 작업을 `ack_timeout`으로 기록합니다.
- 서버가 ack를 받으면 교사에게 "시작했습니다"라고 답합니다. **완료는 기다리지 않습니다.**

### result — 끝났을 때

```json
{"type": "result", "id": "9f2c41d07a3b4e10", "status": "completed", "detail": {"message": "식당 도착"}}
```

| status | 언제 |
| --- | --- |
| `completed` | 정상 완료 (go_to 도착, speak 끝, 게임 시작됨, 배부 끝 등) |
| `failed` | 시도했지만 실패 (경로 막힘, 이동 중단 등). `detail.message`에 이유 |
| `rejected` | 실행하지 않음 (모르는 위치·게임, 충전 중, 다른 동작 중이라 거부 등). `detail.message`에 이유 |

- `detail`은 객체(권장, 예: `{"message": "...", "code": "OBSTACLE"}`)나 문자열 모두 받습니다.
- `go_to`는 temi SDK의 `OnGoToLocationStatusChangedListener`에서 `complete` → `completed`, `abort` → `failed`로 보내면 됩니다.
- **`stop`**: `stop` 명령 자체의 `result`(`completed`)를 보내고, 멈춘 원래 작업이 있으면 그 작업의 `result`를 `failed`(`detail.message: "stop 명령으로 중단"`), 큐에 남아 있던 작업은 `rejected`(`"stop으로 취소"`)로 보내 주세요.
- 큐에서 기다리는 동안에도 `timeout_s`는 흐릅니다. 서버는 `timeout_s` + 10초 안에 결과가 없으면 `timeout`으로 기록하므로, 큐가 길어질 것 같으면 늦게라도 `result`를 보내 주세요(늦게 온 결과로 기록이 바뀝니다).
- ack 없이 바로 `result`를 보내도 서버는 ack로도 처리합니다(아주 짧은 동작용). 그래도 ack를 먼저 보내는 것을 권합니다.

### ping / pong

- 서버는 **20초마다** `ping`을 보냅니다. 같은 `id`로 `pong`을 보내세요.
- 앱도 20초마다 `ping`을 보내 주세요(학교 공유기의 NAT가 조용한 연결을 끊지 않게). 서버가 `pong`으로 답합니다.
- 서버는 **60초 동안 아무 메시지도 못 받으면** 연결을 닫습니다(close code `4001`).
- OkHttp의 WebSocket ping(`pingInterval`)은 프로토콜 수준 ping이라 위 JSON ping과 별개입니다. 둘 다 켜도 됩니다.

### error

```json
{"type": "error", "id": "e1", "message": "알 수 없는 type: dance"}
```

앱 로그에 남기고 연결은 유지하세요.

## 연결 끊김과 재접속

- 끊기면(네트워크, 서버 재시작, close `4000`/`4001`/`1001`) **3초 후 재접속**, 실패가 이어지면 최대 30초까지 점점 늘려 재시도하세요.
- 연결이 끊긴 순간 서버는 그 temi의 진행 중 작업(`sent`, `acked`)을 `disconnected`로 기록합니다. 재접속 후 이전 작업의 `result`를 보내도 서버는 받지 않으니(작업이 이미 닫힘) **재접속하면 이전 명령은 잊고** 새 명령만 처리하세요.
- temi가 연결돼 있지 않을 때 교사가 지시하면, 서버는 바로 "temi가 서버에 연결되어 있지 않아요"라고 교사에게 알립니다.

## close code

| code | 의미 |
| --- | --- |
| 1000 / 1001 | 정상 종료 / 서버 종료 |
| 4000 | 같은 device_id의 새 연결로 교체됨 |
| 4001 | ping 시간 초과 (60초 동안 메시지 없음) |
| (HTTP 403) | 접속 단계 거부: 키·device_id 확인 |

## 아이 모드와 안전

- 아이 대화(`/agent/child`)에서 나온 명령은 서버가 **`start_game`만** 보냅니다. 아이가 "밖으로 나가자"라고 해도 `go_to`는 오지 않습니다.
- 앱에서도 한 번 더 막기를 권합니다: 아이와 대화 중인 화면에서는 `start_game`, `stop`만 실행.
- 넘어짐 같은 **응급 대응은 이 채널로 하지 않습니다.** 현장 규칙 엔진이 바로 처리합니다.

## temi가 감지한 이벤트(넘어짐 등)는 어디로?

**이 채널로 보내지 않고, 현장 규칙 엔진에 보낸 뒤 규칙 엔진이 `POST /events`로 기록하는 방식을 제안합니다.**

- **응급 경로가 인터넷에 의존하면 안 됩니다.** 이 채널은 EC2까지 인터넷을 거칩니다. 와이파이·EC2·Caddy 중 하나라도 문제가 생기면 응급 신호가 늦거나 사라집니다. 현장 규칙 엔진은 같은 학교 네트워크에 있어서 더 빠르고 확실합니다.
- **판정은 한 곳에서 합니다.** 설계 원칙상 응급 판정과 알림은 규칙 엔진의 일입니다. temi 센서 신호도 규칙 엔진이 카메라 판정과 합쳐서 판단해야 중복 알림이나 엇갈린 판정이 없습니다.
- **기록 형식이 하나로 유지됩니다.** `/events`에는 이미 중복 방지(`event_id`), 시간대 검사, 해제 시 보고서 자동 생성이 있습니다. 같은 사건이 두 경로로 들어오면 보고서가 두 번 생길 수 있습니다.

그래서 이 채널에 `type: "event"`를 보내면 서버는 `error`("현장 규칙 엔진을 거쳐 POST /events로 보내세요")로 답합니다. 규칙 엔진과 temi 사이의 연결 방식(같은 공유기 안의 HTTP 등)은 규칙 엔진 담당과 정해 주세요.

## 교사 앱: 정지 버튼 (`POST /robot/stop`)

LLM을 거치지 않고 바로 `stop` 명령을 보냅니다. 교사 앱의 정지 버튼에 연결하세요.

```http
POST /robot/stop
X-API-Key: <TEACHER_APP_API_KEY>
Content-Type: application/json

{"device_id": "temi-classroom-1"}      ← 생략하거나 본문 없이 보내면 연결된 모든 temi
```

```json
200 OK
{
  "all_acked": true,
  "results": [
    {"device_id": "temi-classroom-1", "ok": true, "job_id": "3c9e1f0a7b2d4e55", "status": "acked", "error": null}
  ]
}
```

| 상황 | 응답 | 교사 앱이 할 일 |
| --- | --- | --- |
| 모든 대상 temi가 stop을 받음 | 200, `all_acked: true` | "정지 명령을 보냈어요" |
| 일부 temi가 5초 안에 ack 없음 | 200, `all_acked: false`, 해당 temi `ok: false`, `status: "ack_timeout"` | 그 temi를 화면에서 직접 멈추라고 표시 |
| 지정한 temi가 연결 안 됨 / 연결된 temi가 없음 | **409**, `{"detail": "… 연결되어 있지 않아 정지 명령을 보낼 수 없습니다. temi 화면에서 직접 멈춰 주세요."}` | `detail`을 그대로 보여 주기 |
| 키 없음·틀림 / 다른 역할 키 / device_id 형식 오류 | 401 / 403 / 422 | |

- 여러 대에 보낼 때는 동시에 보내므로 응답은 최대 약 5초(ack 대기) 안에 옵니다.
- 실행기가 `mock`이어도 연결된 temi에는 보냅니다(안전 우선).
- temi 쪽 처리는 일반 `stop`과 같습니다: 바로 멈추고, 큐를 비우고, `result`를 보냅니다. 작업 기록의 `source_mode`는 `teacher_app`입니다.

## 교사 앱에서 결과 보기

| API (teacher_app 키) | 설명 |
| --- | --- |
| `GET /robot/jobs?status=&device_id=&limit=` | 최근 명령 기록 (최신 순) |
| `GET /robot/jobs/{job_id}` | 작업 하나: `status`, `detail`, `created_at`, `acked_at`, `finished_at` |
| `GET /robot/devices` | 지금 연결된 temi 목록 |

작업 상태: `sent`(보냄) → `acked`(temi가 받음) → `completed` / `failed` / `rejected`, 또는 `ack_timeout`, `timeout`, `disconnected`.

## 시험하기

서버를 control 모드로 띄우고(`ROBOT_EXECUTOR=control`) 가짜 temi를 붙입니다.

```powershell
.venv/Scripts/python scripts/fake_temi.py --url ws://localhost:8000/ws/temi --device temi-test-1
# 실패·거부·무응답 흉내: --fail go_to --reject start_delivery --no-ack speak --delay 5
```

## Kotlin 예시 (OkHttp)

```kotlin
val client = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build()
val req = Request.Builder()
    .url("wss://$host/ws/temi")
    .header("X-API-Key", temiApiKey)
    .header("X-Device-Id", deviceId)
    .build()

client.newWebSocket(req, object : WebSocketListener() {
    override fun onOpen(ws: WebSocket, response: Response) {
        ws.send(JSONObject().put("type", "hello").put("id", newId()).put("device_id", deviceId)
            .put("app_version", BuildConfig.VERSION_NAME).put("protocol", 1)
            .put("locations", JSONArray(robot.locations)).toString())
    }
    override fun onMessage(ws: WebSocket, text: String) {
        val msg = JSONObject(text)
        when (msg.getString("type")) {
            "ping" -> ws.send(JSONObject().put("type", "pong").put("id", msg.getString("id")).toString())
            "command" -> {
                val id = msg.getString("id")
                ws.send(JSONObject().put("type", "ack").put("id", id).toString())   // 받자마자
                execute(msg.getString("action"), msg.getJSONObject("args")) { status, message ->
                    ws.send(JSONObject().put("type", "result").put("id", id).put("status", status)
                        .put("detail", JSONObject().put("message", message)).toString())
                }
            }
        }
    }
    override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
        if (response?.code == 403) showConfigError() else scheduleReconnect()
    }
    override fun onClosed(ws: WebSocket, code: Int, reason: String) = scheduleReconnect()
})
```
