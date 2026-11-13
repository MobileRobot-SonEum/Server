# temi 에이전트 서버

어린이집 temi 로봇에서 교사가 말로 일을 시키면 LLM 에이전트가 temi 기능을 도구로 호출해 처리하는 서버입니다.
응급 판정과 영상 인식은 현장의 다른 서버가 맡고, 이 서버에는 텍스트만 들어옵니다. 설계 원칙은 [CLAUDE.md](CLAUDE.md)를 보세요.

> 진행 상황: **1단계 완료** (교사 모드 에이전트, 가짜 실행기, 역할별 API 키). 아이 모드·승인 흐름·이벤트는 2단계, Docker·EC2 문서는 3단계에서 추가됩니다.

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

## 접근 제어

모든 API(`/health` 제외)는 `X-API-Key` 헤더가 필요합니다. 키는 역할별로 `.env`에 둡니다.

| 역할 | 키 | 쓸 수 있는 API |
| --- | --- | --- |
| 교사 앱 | `TEACHER_APP_API_KEY` | `/agent/teacher`, `/approvals` |
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
