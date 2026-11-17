# temi 앱 ↔ 에이전트 서버 HTTP API

> temi 앱(Android) 담당용. 상태: v0.1 (2026-11-17).
> 서버가 temi에 동작 명령을 보내는 WebSocket 채널은 [control_protocol.md](control_protocol.md)를 보세요.

## 요약

| 용도 | 방식 | 엔드포인트 |
| --- | --- | --- |
| 서버 살아 있는지 확인 | HTTP GET | `/health` |
| 아이 말 → 테미 답변 | HTTP POST | `/agent/child` |
| 서버 → temi 동작 명령 (이동, 말하기, 게임 등) | WebSocket (temi가 먼저 접속) | `/ws/temi` → [control_protocol.md](control_protocol.md) |

- 주소: `https://<서버 도메인>` (예: `https://temi-daycare.duckdns.org`). **HTTPS만 씁니다.** HTTP로 키를 보내지 마세요.
- 문자 인코딩: UTF-8, `Content-Type: application/json`.
- 인증: 모든 요청(`/health` 제외)에 헤더 `X-API-Key: <TEMI_API_KEY>`. 키는 팀에서 따로 전달합니다. 앱 코드나 저장소에 박아 넣지 말고 설정 화면이나 빌드 설정(local.properties 등)으로 넣으세요.
- temi 키로 쓸 수 있는 것은 `/agent/child`와 `/ws/temi`뿐입니다. 다른 API는 403.

## GET /health

인증 없이 서버 상태를 봅니다. 앱 시작 시, 네트워크 복구 시 확인용.

```http
GET /health
```

```json
200 OK
{
  "status": "ok",
  "executor": "control",
  "models": {
    "teacher": {"model": "qwen3:14b", "host": "ollama", "tool_mode": "auto", "reasoning_effort": "none", "reachable": null},
    "child":   {"model": "qwen3:14b", "host": "ollama", "tool_mode": "auto", "reasoning_effort": "none", "reachable": null}
  },
  "temi_connected": ["temi-classroom-1"]
}
```

- `status`가 `ok`이면 정상입니다. 응답이 없거나 5xx면 서버 문제입니다.
- `temi_connected`: 지금 명령 채널(`/ws/temi`)에 붙어 있는 temi의 `device_id` 목록.

## POST /agent/child

아이가 테미에게 한 말(음성 인식 결과 텍스트)을 보내면 테미가 말할 답변을 돌려줍니다.

### 요청

```http
POST /agent/child
X-API-Key: <TEMI_API_KEY>
Content-Type: application/json

{"text": "사과는 영어로 뭐야?", "device_id": "temi-classroom-1"}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `text` | string | ✔ | 아이 발화 텍스트. 1~300자 |
| `device_id` | string |  | temi 장치 ID (최대 64자, 영문·숫자·`.`·`_`·`-`). **꼭 보내 주세요**: 이 값이 있어야 영어 퀴즈처럼 앞의 대화를 이어 갈 수 있습니다(장치별 최근 3턴을 서버 메모리에만 2분간 보관). 명령 채널의 `device_id`와 같은 값을 쓰세요 |

### 응답 (200)

```json
{
  "reply": "사과는 영어로 apple이야! 같이 말해 볼까?",
  "alert_created": false,
  "alert_level": null,
  "alert_id": null,
  "game_started": null,
  "fallback_reason": null,
  "model": "qwen3:14b",
  "latency_ms": 820
}
```

| 필드 | 설명 | 앱이 할 일 |
| --- | --- | --- |
| `reply` | 테미가 말할 문장. 마크다운·이모지 없는 짧은 반말 1~2문장 | **그대로 TTS로 읽기** |
| `alert_created` | 이 발화로 교사 알림이 생겼는지 | 화면 표시용(선택). 교사 알림 전송은 서버가 함 |
| `alert_level` | `"urgent"`(긴급: 아픔·다침·폭력·위험), `"normal"`(정서: 엄마 보고 싶어·무서워·슬퍼), `null`(없음) | `urgent`면 테미가 아이 곁에 머물게 하는 등 앱 정책에 따라 동작(선택). **알림 전송은 앱이 따로 하지 않아도 됨** |
| `alert_id` | 서버의 교사 알림 번호 | 로그용 |
| `game_started` | 서버가 게임 시작 명령을 보냈으면 게임 이름 | 정보용. 실제 시작 명령은 명령 채널(`command`, `start_game`)로 따로 옴. **이 값을 보고 게임을 또 시작하지 마세요** |
| `fallback_reason` | 모델 답변 대신 정해진 답변을 쓴 이유. `urgent_fixed_reply`, `timeout`, `llm_error:…`, `promise:…`, `action_promise:…` 등 | 로그용. `reply`는 항상 그대로 읽으면 됨 |
| `model`, `latency_ms` | 쓴 모델, 서버 처리 시간 | 로그용 |

예시 — 긴급 발화:

```json
{"text": "배가 너무 아파", "device_id": "temi-classroom-1"}
```
```json
{"reply": "선생님께 바로 알려줄게. 여기서 같이 기다리자.", "alert_created": true, "alert_level": "urgent",
 "alert_id": 17, "game_started": null, "fallback_reason": "urgent_fixed_reply", "model": null, "latency_ms": 3}
```

예시 — 정서 발화:

```json
{"reply": "엄마가 보고 싶구나. 선생님한테도 알려 둘게.", "alert_created": true, "alert_level": "normal",
 "alert_id": 18, "game_started": null, "fallback_reason": null, "model": "qwen3:14b", "latency_ms": 1240}
```

### 개인정보

- 서버는 아이 발화 원문을 로그·DB에 남기지 않습니다. 교사 알림이 생긴 경우에만 그 문장을 교사 알림에 저장합니다.
- 앱도 아이 발화 텍스트를 로그(Logcat 포함)·파일에 남기지 마세요. 디버그 로그에는 길이나 `alert_level` 같은 메타데이터만 남기세요.

## 에러

| 코드 | 언제 | 본문 예 | 앱이 할 일 |
| --- | --- | --- | --- |
| 401 | `X-API-Key` 없음 / 틀림 | `{"detail": "API 키가 올바르지 않습니다"}` | 재시도하지 말고 설정 확인. 관리자 화면에 표시 |
| 403 | temi 키로 다른 API 호출 | `{"detail": "'temi' 역할은 이 API를 쓸 수 없습니다"}` | 코드 버그. 재시도하지 않음 |
| 422 | 요청 형식 오류 (`text` 비었음, 300자 초과, JSON 아님 등) | `{"detail": [{"loc": ["body", "text"], "msg": "..."}]}` | 재시도하지 않음. 300자 넘으면 앞부분만 보내기 |
| 5xx | 서버 오류, 서버 재시작 중, 프록시 오류(502/503/504) | (형식 일정하지 않음) | 아래 '대체 답변'을 말하고 1회만 재시도 |
| 네트워크 오류 / 시간 초과 | 와이파이 끊김, 서버 꺼짐 | — | 아래 '대체 답변' |

서버 내부에서 LLM이 느리거나 실패하면 **5xx가 아니라 200**과 정해진 답변(`fallback_reason`에 이유)이 옵니다.

### 대체 답변 (앱 안에 고정)

서버에 닿지 못했을 때 테미가 말할 문장입니다. 지킬 수 없는 약속을 넣지 마세요(예: "금방 올게" 금지).

> "지금은 대답하기 어려워. 선생님께 같이 물어보자."

## 권장 타임아웃·재시도

| 항목 | 값 | 이유 |
| --- | --- | --- |
| 연결 타임아웃 | 5초 | |
| `/agent/child` 읽기 타임아웃 | **15초** | 서버는 LLM 단계를 10초 안에 끊고 정해진 답변을 돌려줌. 네트워크 여유 5초 |
| 재시도 | 5xx·네트워크 오류일 때 **1회**, 1초 뒤 | 같은 발화를 여러 번 보내면 알림이 중복될 수 있음 |
| `/health` | 타임아웃 5초 | |

아이가 말하는 중에 이전 요청이 아직 안 끝났다면, 이전 요청 응답은 버리고 새 발화만 처리하는 것을 권합니다.

## 예시 코드 (Kotlin, OkHttp)

```kotlin
val client = OkHttpClient.Builder()
    .connectTimeout(5, TimeUnit.SECONDS)
    .readTimeout(15, TimeUnit.SECONDS)
    .build()

val body = JSONObject().put("text", utterance).put("device_id", deviceId).toString()
    .toRequestBody("application/json; charset=utf-8".toMediaType())
val req = Request.Builder()
    .url("$baseUrl/agent/child")
    .header("X-API-Key", temiApiKey)
    .post(body)
    .build()

client.newCall(req).execute().use { res ->
    when {
        res.isSuccessful -> speak(JSONObject(res.body!!.string()).getString("reply"))
        res.code == 401 || res.code == 403 -> showConfigError()
        else -> speak(FALLBACK_REPLY)   // 5xx: 1회 재시도 후
    }
}
```
