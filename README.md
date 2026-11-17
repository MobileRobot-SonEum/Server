# temi 에이전트 서버

어린이집 temi 로봇에서 교사가 말로 일을 시키면 LLM 에이전트가 temi 기능을 도구로 호출해 처리하는 서버입니다.
응급 판정과 영상 인식은 현장의 다른 서버가 맡고, 이 서버에는 텍스트만 들어옵니다. 설계 원칙은 [CLAUDE.md](CLAUDE.md)를 보세요.

> 진행 상황: **3단계 완료** (교사·아이 모드 에이전트, 승인 흐름, 이벤트·사고 보고서, 교사 알림, 모델 비교 평가, Docker, EC2 배포 문서).

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

`config/child_safety.yaml`에서 위험 신호 패턴(긴급·정서), 오탐 제외어, 약속·행동 약속 표현, 고정 답변을 고칩니다. 교사 모드 응급 지시 규칙은 `config/teacher_emergency.yaml`입니다. 고친 뒤 서버를 다시 시작하세요.
알림마다 걸린 규칙이 `matched`에 남으니(`GET /alerts`) 오탐·누락을 보고 조정하면 됩니다.

## 접근 제어

모든 API(`/health` 제외)는 `X-API-Key` 헤더가 필요합니다. 키는 역할별로 `.env`에 둡니다.

| 역할 | 키 | 쓸 수 있는 API |
| --- | --- | --- |
| 교사 앱 | `TEACHER_APP_API_KEY` | `/agent/teacher`, `/approvals`, `/alerts`, `/reports` |
| temi | `TEMI_API_KEY` | `/agent/child` |
| 현장 규칙 엔진 | `RULE_ENGINE_API_KEY` | `/events` |

키 만들기: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

## Ollama 설치 (Windows 노트북)

1. https://ollama.com/download 에서 Windows용 설치 파일(OllamaSetup.exe)을 받아 실행합니다. 설치가 끝나면 Ollama가 백그라운드에서 `http://localhost:11434`로 실행됩니다.
2. PowerShell에서 확인합니다.
   ```powershell
   ollama --version
   ollama list
   ```

### 노트북에서 아이 모드 시험하기 (작은 모델)

노트북에서는 14B급 모델이 느리므로 작은 모델로 흐름을 확인합니다. 품질 비교는 EC2(T4)에서 [평가 스크립트](#모델-비교-평가)로 합니다.

| 모델 | 크기 | 특징 |
| --- | --- | --- |
| `qwen3:4b` | 2.5GB | 도구 호출 지원(게임 시작), thinking은 `reasoning_effort=none`으로 끔 |
| `exaone3.5:2.4b` | 1.6GB | 한국어에 강함, 도구 호출 미지원 → JSON 방식으로 자동 대체 |

```powershell
ollama pull qwen3:4b
```

`.env`에서 아이 모드 모델만 바꿉니다(교사 모드는 Gemini 그대로 둬도 됨).

```
CHILD_LLM__BASE_URL=http://localhost:11434/v1
CHILD_LLM__MODEL=qwen3:4b
CHILD_LLM__REASONING_EFFORT=none
CHILD_LLM__TIMEOUT_S=60   # 노트북 시험용. 운영값은 10
CHILD_DEADLINE_S=60       # 노트북 시험용. 운영값은 10 (넘으면 기본 답변)
```

```powershell
.venv/Scripts/python scripts/try_child.py                    # 대화형, 빈 줄로 종료
.venv/Scripts/python scripts/try_child.py "엄마 보고 싶어"
.venv/Scripts/python scripts/eval_models.py --models qwen3:4b --modes child --repeats 1   # 아이 문항 평가
```

CPU만 있는 노트북은 답변 하나에 수 초~수십 초가 걸릴 수 있어서 위처럼 시간 제한을 늘려 시험합니다. 평가도 같은 이유로 `--child-timeout 60 --child-deadline 60`을 붙이세요(결과 파일에 그 값이 기록됨).

## 모델 비교 평가

```powershell
.venv/Scripts/python scripts/eval_models.py                  # .env의 EVAL_MODELS를 차례로, 문항당 3회
.venv/Scripts/python scripts/eval_models.py --models qwen3:14b,exaone3.5:7.8b --only T05,C06 --repeats 1
```

- 문항: `scripts/eval_cases/` (교사 21개, 아이 22개, 직접 만든 초안)
- 서버에 없는 모델은 건너뛰고 결과에 표시합니다. 모든 모델이 같은 생성 설정(temperature 0.2, max_tokens 512, reasoning_effort none)으로 돌고, 그 값을 결과 파일 맨 위에 적습니다.
- 아이 모드 평가는 허용 목록 호스트(Ollama)에서만 돕니다(가상 문항이라 외부 API로 돌리려면 `--allow-external-child`).
- 결과: `results/eval_models_*.md` (비교표, 문항별 성공 횟수 0~3, 틀린 문항), `results/eval_runs_*.csv`, `results/eval_child_replies_*.csv` (팀 5점 채점용)

## Docker

```powershell
docker compose -f docker/compose.dev.yml up --build      # 노트북: 서버만 (http://localhost:8000)
```

EC2(GPU)는 `docker/compose.ec2.yml`을 씁니다(Ollama + 서버 + Caddy HTTPS). [docs/deploy_ec2.md](docs/deploy_ec2.md)를 보세요.

## EC2 g4dn 배포

[docs/deploy_ec2.md](docs/deploy_ec2.md): 인스턴스·AMI·디스크 산정, 보안 그룹, HTTPS, 비용(켜고 끄기, 예산 알림, 유휴 자동 중지), 처음 띄운 뒤 할 일.
**쓰지 않을 때는 반드시 인스턴스를 중지하세요.**
