# temi 에이전트 서버

어린이집 temi 로봇에서 교사가 말로 일을 시키면 LLM 에이전트가 temi 기능을 도구로 호출해 처리하는 서버입니다.
응급 판정과 영상 인식은 현장의 다른 서버가 맡고, 이 서버에는 텍스트만 들어옵니다. 설계 원칙은 [CLAUDE.md](CLAUDE.md)를 보세요.

> 진행 상황: **2단계 완료** (교사·아이 모드 에이전트, 승인 흐름, 이벤트·사고 보고서, 교사 알림). Docker·EC2 문서와 모델 비교 평가는 3단계에서 추가됩니다.

## 로컬 실행

```bash
py -3 -m venv .venv                      # macOS/Linux: python3 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
copy .env.example .env                   # 값 수정 (bash: cp)
.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload
```

- API 문서: http://localhost:8000/docs
- 테스트: `.venv/Scripts/python -m pytest -q` (실제 LLM 호출 없음)

### Ollama 없이 시험해 보기 (Gemini 무료 키)

`.env`에서 교사 모드 LLM을 Gemini로 바꿉니다 (`.env.example`의 [B] 블록).
아이 모드는 외부 API를 쓸 수 없으므로 `CHILD_LLM__BASE_URL`은 localhost 그대로 두세요(교사 모드만 쓰면 Ollama가 없어도 됩니다).

```powershell
# 교사 지시 하나의 실행 기록 출력
.venv/Scripts/python scripts/demo_teacher.py "식당으로 가서 햇살반 간식 배부 시작해 줘"
# 모의 LLM으로 (네트워크 없음), go_to 일시 실패 1회 주입
.venv/Scripts/python scripts/demo_teacher.py --mock-llm --fail-go-to
# .env를 고치지 않고 모델만 잠깐 바꾸기 (PowerShell)
$env:TEACHER_LLM__MODEL="gemini-3.6-flash"; .venv/Scripts/python scripts/demo_teacher.py "놀이방으로 가서 숨바꼭질 시작해 줘"
```

### API 호출 예

```bash
curl -X POST http://localhost:8000/agent/teacher \
  -H "X-API-Key: $TEACHER_APP_API_KEY" -H "Content-Type: application/json" \
  -d '{"instruction": "놀이방으로 가서 숨바꼭질 시작해 줘"}'

curl "http://localhost:8000/health?check_llm=true"
```

응답의 `status`는 `completed`(완료), `needs_teacher`(교사에게 질문, `question` 참고), `failed`(LLM 호출 실패) 중 하나이며, `trace`에 단계별 실행 기록이 들어갑니다.

### 시나리오 데모 (.env 불필요, 메모리 DB)

```powershell
.venv/Scripts/python scripts/demo_incident.py   # 넘어짐 감지 → 해제 → 보고서 초안·부모 알림 승인 대기 → 승인 → 전송 스텁
.venv/Scripts/python scripts/demo_child.py      # 아이 모드: 일반 / 정서 / 긴급 발화 (모의 LLM)
```

## API

| API | 역할 | 설명 |
| --- | --- | --- |
| `POST /agent/teacher` | teacher_app | 교사 지시 → 최종 답변 + 실행 기록 |
| `POST /agent/child` | temi | 아이 발화 → 짧은 답변 (+ 교사 알림 생성 여부) |
| `POST /events` | rule_engine | 현장 이벤트 기록 ([docs/events.md](docs/events.md)) |
| `GET /approvals?status=` | teacher_app | 부모 메시지 승인 대기 목록 (기본 pending) |
| `POST /approvals/{id}/approve` | teacher_app | 승인 → 전송. 본문 `{child, text, decided_by}` 선택 |
| `POST /approvals/{id}/reject` | teacher_app | 거절. 본문 `{reason}` 필수 |
| `GET /alerts`, `POST /alerts/{id}/ack` | teacher_app | 아이 대화에서 생긴 교사 알림 |
| `GET /reports`, `GET /reports/{id}` | teacher_app | 사고 보고서 초안 |
| `GET /health` | 공개 | 상태, 모드별 모델 |

## 아이 안전 규칙 고치기

`config/child_safety.yaml`에서 위험 신호 패턴(긴급·정서), 오탐 제외어, 약속 표현, 고정 답변을 고칩니다. 고친 뒤 서버를 다시 시작하세요.
알림마다 걸린 규칙이 `matched`에 남으니(`GET /alerts`) 오탐·누락을 보고 조정하면 됩니다.

## 접근 제어

모든 API(`/health` 제외)는 `X-API-Key` 헤더가 필요합니다. 키는 역할별로 `.env`에 둡니다.

| 역할 | 키 | 쓸 수 있는 API |
| --- | --- | --- |
| 교사 앱 | `TEACHER_APP_API_KEY` | `/agent/teacher`, `/approvals`, `/alerts`, `/reports` |
| temi | `TEMI_API_KEY` | `/agent/child` |
| 현장 규칙 엔진 | `RULE_ENGINE_API_KEY` | `/events` |

키 만들기: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

## Ollama 설치와 모델 받기

(3단계에서 자세히 작성 예정)

```bash
# https://ollama.com/download 에서 설치 후
ollama pull qwen3:14b
ollama pull exaone3.5:7.8b
```

## EC2 g4dn 배포

(3단계에서 작성 예정: 드라이버 포함 AMI, 보안 그룹 IP 제한, 미사용 시 인스턴스 중지)
