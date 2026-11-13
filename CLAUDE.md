# CLAUDE.md — temi 에이전트 서버

다음 세션에서 이어서 작업하기 위한 프로젝트 기준 문서. 아래 '설계 원칙'은 반드시 지킨다.

## 배경
- temi가 어린이집에서 게임, 식판·간식 배부, 응급 알림, 부모 모니터링을 한다. 교사가 말로 일을 시키면 LLM 에이전트가 temi 기능을 도구로 호출해 처리한다. 팀원 5명 중 이 저장소(에이전트 서버) 담당은 한 명.
- 배포: AWS EC2 g4dn.xlarge(NVIDIA T4 16GB)에서 Ollama로 LLM을 직접 서빙. 개발 중에는 노트북에서 돌리고, Gemini·Groq 무료 등급도 쓸 수 있게 한다.
- 아이 영상과 인식(MediaPipe), 응급 판정은 현장 노트북의 다른 서버가 맡는다. 이 서버에는 텍스트(교사 지시, 이벤트 기록, 아이 대화 문장)만 들어온다.
- Python 3.10 이상, FastAPI.

## 설계 원칙 (반드시 지키기)
1. **응급 대응은 에이전트가 하지 않는다.** 넘어짐 감지 → 알림은 현장 규칙 엔진이 바로 처리하고, 이 서버는 이벤트 기록을 받아 사고 보고서 초안을 쓰는 것만 한다. 서버에 응급 판정 로직을 두지 않는다.
2. **부모에게 가는 메시지는 교사 승인 후에만 전송한다.** 에이전트가 만든 부모용 메시지는 '승인 대기' 상태로 저장된다.
3. **모드별 허용 도구.**
   - 교사 모드: 모든 도구
   - 아이 모드: 대화와 정해진 게임 시작만 가능. 이동·배부·알림·기록 조회는 불가.
   - 모드 제한은 (a) LLM에 도구를 보여 주지 않고 (b) 실행 시점에도 다시 막는 이중 구조로 유지한다.
4. **아이 안전.**
   - 아이 말에 "아파", "때렸어", "엄마 보고 싶어" 같은 신호가 있으면 LLM으로 넘기기 전에 규칙으로 걸러서 교사 알림을 만든다.
   - LLM 답변은 짧고 쉬운 말로, 나이에 맞는 내용만, 확인할 수 없는 약속은 하지 않게 한다.
5. **개인정보.**
   - 기본 설정에서 아이 대화 원문은 로그·DB에 남기지 않는다. 교사 알림이 생긴 경우에만 해당 문장을 남긴다.
   - 아이 대화는 외부 API가 아니라 자체 서빙 LLM(Ollama)으로만 처리하는 설정을 기본으로 한다.

## 확정된 세부 결정 (2026-11-13, 사용자 확인)
- **위험 신호 2등급** (규칙은 `config/child_safety.yaml`, 팀이 직접 수정. 오탐 방지 `exclusions`: 아파트·아프리카·칼국수 등. 알림마다 걸린 규칙 `matched` 저장):
  - 긴급(아픔, 다침, 폭력, 위험한 행동): LLM에 넘기지 않고 고정 답변 + **긴급** 교사 알림.
    고정 답변은 약속 없이: "선생님께 바로 알려줄게. 여기서 같이 기다리자." ("금방 올 거야" 같은 약속 금지)
  - 정서(엄마 보고 싶어, 무서워, 슬퍼): **일반** 교사 알림 생성, 답변은 LLM이 위로하는 말로 하되 출력 검사는 그대로 적용.
  - 긴급 알림은 저장과 동시에 `TeacherNotifier`(지금은 로그 스텁)로 즉시 전송. 일반 알림은 저장만, 교사 앱이 `GET /alerts`로 조회·`POST /alerts/{id}/ack`.
  - 이 서버의 교사 알림은 아이 대화에서 생긴 것만. 넘어짐 등 현장 응급 알림은 규칙 엔진이 직접 보냄.
- **아이 모드 처리 순서**: child_filter → (통과) LLM(start_game만, 최대 2회 호출) → output_guard(길이 80자, 약속·금지 표현 → 기본 답변) → 응답. LLM 실패 시 기본 답변 + `fallback_reason`.
- **아이 단기 기억**: temi(device_id)별 최근 3턴을 프로세스 메모리에만, 2분 무응답 시 삭제(`CHILD_MEMORY_TURNS`, `CHILD_MEMORY_TTL_S`, 0이면 끔). DB·로그·파일 금지, 재시작 시 사라짐. device_id 없으면 기억 안 함. 긴급 발화는 기억에 넣지 않음.
- **원문 저장**: 아이 발화 원문은 teacher_alerts(알림이 생긴 경우)에만. 로그에는 알림 건도 원문을 쓰지 않음(메타데이터만). `STORE_CHILD_UTTERANCES` 같은 끄는 설정은 두지 않음.
- **아이 모드 외부 API 차단**: 허용 목록 `CHILD_ALLOWED_LLM_HOSTS`(기본 `localhost,127.0.0.1`). 개발 중 EC2 Ollama를 부를 땐 그 주소를 추가. Gemini·Groq 등 외부 API 도메인(`app/config.py`의 `BLOCKED_EXTERNAL_LLM_DOMAINS`)은 목록에 있어도 거부. 위반 시 서버 시작 거부.
- **사고 보고서**: `/events`로 '해제' 이벤트(교사가 "테미, 괜찮아"로 해제)가 들어오면 그 사건의 보고서 초안을 자동 생성해 '초안' 상태로 저장. 교사 요청으로 다시 만드는 것도 가능.
- **접근 제어**: 헤더 `X-API-Key`, 역할별 키(.env). `teacher_app` → `/agent/teacher`, `/approvals` / `temi` → `/agent/child` / `rule_engine` → `/events`. `/health`는 공개(비밀값 없음). 역할 키가 서로 같으면 시작 거부.
- **승인 흐름**: approvals 상태 pending → approved → sent | send_failed, 또는 pending → rejected(사유 필수). `MessageSender.send`는 `ApprovalService.approve()`에서만 호출(테스트가 소스를 검사함). 승인 시 교사가 child·text 수정 가능, child 없거나 미등록이면 400, 이미 처리된 건 409.
- **사고 부모 알림**: 해제 시 child=NULL로 승인 대기 생성(규칙 엔진은 보통 아이를 모름). 같은 사건에 한 번만.
- **보고서**: LLM 없이 규칙 기반 템플릿(`app/services/incidents.py`). 기록에 없는 항목은 "[교사 확인 필요]". 재생성 시 version+1, 이전 draft는 superseded.
- **이벤트 형식**: `docs/events.md` (규칙 엔진 담당과 합의 전 초안 v0.1). event_id 중복은 200+duplicate, 모르는 필드 무시, 시간대 없는 시각은 422.
- **모델 태그**: Ollama 라이브러리에서 확인한 이름만 쓴다(2026-11-13: qwen3, qwen3.5, gemma4는 tools·thinking 지원, exaone3.5는 tools 미지원).
- Gemini 3 이상은 thinking을 끌 수 없음. `gemini-3.8-flash`는 `minimal`을 400으로 거부 → `low` 사용(2026-11-13 실측). Ollama는 `reasoning_effort=none`으로 끔.
- 실측(2026-11-13): `gemini-3.6-flash`로 교사 지시(get_locations→go_to→start_game) 완료, 다중 턴 도구 호출에서 thought signature 문제 없음. `gemini-3.8-flash`는 혼잡 시 503.
- 사용자 개발 환경은 Windows PowerShell. 안내 명령은 PowerShell 문법으로 쓴다(`VAR=값 명령` 불가 → `$env:VAR="값"`).
- Gemini 3의 도구 호출 응답에 붙는 추가 필드(thought signature)를 보존하려고, 응답의 원래 tool_call 객체를 그대로 대화 기록에 돌려보낸다(`LLMResponse.assistant_message`).
- Groq는 `messages[].name`을 거부하므로 클라이언트가 보내기 전에 뺀다.

## 진행 상황
- [x] 1단계: 저장소 준비, LLM 클라이언트, 에이전트 루프, 가짜 실행기, `/agent/teacher`, `/health`, 역할별 API 키, 테스트
- [x] 2단계: 아이 모드(`/agent/child`), SQLite 저장소, 승인 흐름(`/approvals`), `/events` + 해제 시 보고서 자동 초안, `/alerts`, `/reports`, 도구 3개 추가
- [ ] 3단계: `scripts/eval_models.py`(교사 20·아이 20 문항, 직접 만든 초안 표시) → `results/eval_models_{날짜}.md`, Docker(dev CPU / EC2 GPU compose), README EC2 배포 문서

## 구조
```
app/
  config.py          설정(.env, 중첩은 __), 아이 모드 호스트 검사
  main.py            create_app() — DI(테스트에서 LLM·실행기 주입)
  llm/               OpenAICompatClient(tools → JSON 대체, 재시도 1회), json_fallback 파서
  agent/             registry(도구·모드·인자 검사), loop(AgentRunner, trace), prompts
  tools/             robot_tools, info_tools  → build_registry()
  executors/         RobotExecutor 인터페이스, MockRobotExecutor, ControlExecutor(스텁)
  agent/child.py     아이 모드 ChildAgent
  safety/            rules(yaml 로더), child_filter, output_guard, memory(단기 기억)
  services/          approvals(승인·전송), incidents(이벤트·보고서), notify(MessageSender, TeacherNotifier 스텁)
  storage/           Repository 인터페이스, SQLiteRepository(:memory: 가능)
  api/               auth(역할별 키), routes(agent·health), records(events·approvals·alerts·reports), schemas
  data/              children.json(가상 5명), locations.json, games.json
config/child_safety.yaml  위험 신호·제외어·약속 표현·고정 답변
docs/events.md            규칙 엔진 이벤트 형식 (팀 공유용)
scripts/demo_teacher.py   교사 지시 1개 실행 기록 출력 (--mock-llm)
scripts/demo_incident.py  감지→해제→보고서·승인 대기→승인→전송 스텁 시나리오
scripts/demo_child.py     아이 모드 예시 3개 (모의 LLM)
tests/                    ScriptedLLM(conftest)로 LLM 모의
```

## 명령
- 설치: `py -3 -m venv .venv` → `.venv/Scripts/python -m pip install -e ".[dev]"`
- 테스트: `.venv/Scripts/python -m pytest -q`
- 서버: `.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload`
- 실행 예시: `.venv/Scripts/python scripts/demo_teacher.py --mock-llm`

## 작업 규칙
- 테스트에서 실제 LLM을 부르지 않는다(ScriptedLLM, httpx.MockTransport).
- 새 도구는 `allowed_modes`를 명시하고, 아이 모드 허용은 원칙 3에 맞는지 확인한다.
- 아이 발화 원문을 `log.*`에 넣지 않는다(알림이 생긴 경우도). 테스트(`test_child_api.py`)가 caplog로 검사한다.
- `MessageSender.send` 호출을 `ApprovalService.approve()` 밖에 추가하지 않는다.
- 실제 원아 정보를 저장소에 넣지 않는다.
