# SonEum 번역 서버 — 수어 gloss → 한국어 문장

temi 수어 통역 파이프라인 중 **문장 번역 단계**를 맡는 서버입니다.

```
temi 카메라 → MediaPipe 랜드마크 → gloss 분류기 → [문장 번역: 이 저장소] → TTS
```

gloss 나열(예: `나 / 감기 / 진료 / 원하다`)을 받아 창구 대화에 맞는 해요체 문장("감기 때문에 진료를 받고 싶어요.")으로 바꿉니다.

## 구조

```
app/
  main.py              FastAPI 라우트 (HTTP 계층만)
  service.py           TranslationService — 캐시·후보 수·마감 시간·fallback·로그. 게이트웨이가 직접 호출
  schemas.py           요청/응답 모델
  config.py            config.yaml + .env 로드
  logging_utils.py     원문을 남기지 않는 로그
  translators/
    base.py            Translator 추상 클래스
    api.py             ApiTranslator (few-shot 프롬프트 + 재시도)
    providers.py       LLM 제공자 어댑터 (ClaudeProvider / OpenAIProvider)
    fallback.py        규칙 기반 대체 번역
    kogpt2.py          스텁
    local_llm.py       스텁
    factory.py         설정 → 번역기
  prompting/
    system_prompt.py   시스템 프롬프트·사용자 프롬프트 생성
    retriever.py       few-shot 예시 선택 (select_examples: 단어 겹침 → 임베딩으로 교체 가능)
    parser.py          모델 JSON 출력 파싱
  data_tools/aihub_converter.py   AIHub 원본 → 내부 형식 (자리만)
data/examples.jsonl    직접 작성한 임시 예시 24개
config.yaml            번역기 선택과 각종 설정
eval.py                평가 스크립트
tests/                 pytest (실제 API는 호출하지 않음)
```

### 번역기 교체

`config.yaml`의 `translator.backend` 하나로 고릅니다. 환경변수 `TRANSLATOR_BACKEND`가 있으면 그 값이 우선합니다.

| backend | 설명 |
|---|---|
| `api` | 상용 LLM API. `api.provider`로 `claude` / `openai` 선택 |
| `kogpt2` | 스텁 (호출 시 NotImplementedError → 서비스가 fallback으로 응답) |
| `local_llm` | 스텁 |
| `fallback` | 규칙 기반만 사용 |

새 번역기는 `Translator`를 상속해 `translate(request) -> TranslationResult`만 구현한 뒤 `factory.py`에 등록하면 됩니다.
오픈소스 LLM은 `LLMProvider`를 하나 더 만들어 `ApiTranslator`에 넘기는 방식이 가장 간단합니다. 이렇게 하면 프롬프트·few-shot·파서를 그대로 재사용할 수 있습니다.

### WebSocket 게이트웨이에서 부르기

```python
from app.service import build_service
from app.schemas import TranslationRequest

service = build_service()  # 프로세스 시작 시 한 번
resp = await asyncio.to_thread(service.translate, TranslationRequest(glosses=[{"gloss": "감기"}]))
```

## API

### `POST /translate`

```json
{
  "glosses": [{"gloss": "신분증", "confidence": 0.92}, {"gloss": "없다"}, {"gloss": "휴대폰"}, {"gloss": "되다", "confidence": 0.41}],
  "sentence_type": "interrogative",
  "context": [{"speaker": "staff", "text": "신분증 보여주시겠어요?"}],
  "scenario": "hospital",
  "num_candidates": 1
}
```

| 필드 | 설명 |
|---|---|
| `glosses` | 필수. `gloss` 문자열, `confidence` 0~1 (선택) |
| `sentence_type` | `declarative` / `interrogative` / `unknown` (기본 `unknown`) |
| `context` | 직전 대화 최대 10턴. `speaker`: `staff` / `user` |
| `scenario` | `hospital` / `civil_office` / `university` / `general` (기본 `general`) |
| `num_candidates` | 1~3 (기본 1) |

응답:

```json
{
  "sentence": "신분증이 없는데 휴대폰으로 해도 되나요?",
  "candidates": ["신분증이 없는데 휴대폰으로 해도 되나요?", "..."],
  "sentence_type": "interrogative",
  "backend": "api:claude:claude-haiku-5-5",
  "latency_ms": 642.1,
  "cached": false,
  "low_confidence": ["되다"]
}
```

- 확신도가 `candidates.low_confidence_threshold`(기본 0.6)보다 낮은 gloss가 있으면 후보를 자동으로 3개까지 만듭니다. temi 화면에서 고르는 용도입니다.
- `num_candidates`가 1이고 확신도가 낮은 gloss도 없으면 `candidates`는 빈 목록입니다.
- API가 실패하면 `backend`가 `fallback(timeout)`, `fallback(parse_error)`처럼 이유와 함께 표시됩니다.
- 같은 입력은 LRU 캐시에서 바로 돌려줍니다(`cached: true`). fallback 결과는 캐시하지 않습니다.

### `GET /health`

```json
{"status": "ok", "backend": "api:claude:claude-haiku-5-5", "fallback": "fallback"}
```

## 응답 속도

- 목표: 번역 단계 1초 이내 (전체 목표는 수어가 끝난 뒤 2초 안에 음성 출력)
- 기본 모델은 `claude-haiku-5-5`이고, thinking을 끄고 effort를 `low`로 둡니다.
- 시도당 타임아웃은 `api.timeout_s`(기본 3초), 재시도는 1회입니다.
- 재시도를 포함한 전체 마감은 `api.total_deadline_s`(기본 3.5초)입니다. 마감 시간 안에 응답이 없으면 결과를 기다리지 않고 fallback으로 응답합니다.
- 모든 요청의 지연은 로그의 `latency_ms`에 남고, `eval.py`가 평균과 p95를 보여 줍니다.

## 개인정보와 로그

- 기본 설정에서는 gloss·번역 문장·대화 맥락 원문을 **로그에 남기지 않습니다**. 남는 것은 시각, request_id, backend, 지연, 성공 여부, fallback 이유, gloss 개수뿐입니다.
- 예외 메시지에는 프롬프트 일부가 섞일 수 있어서, 예외는 클래스 이름만 기록합니다.
- uvicorn 접근 로그는 Docker에서 끕니다(`--no-access-log`). 요청 본문은 원래 기록되지 않습니다.
- 디버깅이 필요할 때만 `logging.log_raw_text: true` 또는 `LOG_RAW_TEXT=true`로 원문 로그를 켭니다. 운영 환경에서는 켜지 마세요.
- API 키는 `.env`로만 받습니다. `.env`는 `.gitignore`에 들어 있고, 저장소에는 `.env.example`만 올립니다.
- 참고: `api` 번역기를 쓰면 gloss와 대화 맥락이 외부 LLM 제공자에게 전송됩니다. 보고서에 이 점과 제공자의 데이터 보관 정책을 적어 두세요.

## 로컬 실행 (Windows)

```powershell
# Python 3.11
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

copy .env.example .env     # ANTHROPIC_API_KEY 입력
uvicorn app.main:app --reload --port 8000
```

macOS/Linux에서는 `source .venv/bin/activate`, `cp .env.example .env`를 쓰면 됩니다.

동작 확인:

```powershell
curl.exe http://localhost:8000/health
curl.exe -X POST http://localhost:8000/translate -H "Content-Type: application/json" `
  -d '{\"glosses\":[{\"gloss\":\"나\"},{\"gloss\":\"감기\"},{\"gloss\":\"진료\"},{\"gloss\":\"원하다\"}],\"scenario\":\"hospital\"}'
```

API 문서는 브라우저에서 http://localhost:8000/docs 를 열면 됩니다.

### 테스트

```powershell
pytest -q
```

테스트는 모의 제공자(`tests/conftest.py`의 `FakeProvider`)를 쓰기 때문에 실제 API를 호출하지 않고, API 키도 필요 없습니다.

### 평가

```powershell
python eval.py --backend api          # API 번역기
python eval.py --backend fallback     # 규칙 기반과 비교
python eval.py --backend api --no-type   # 문장 유형을 주지 않고 모델이 판단
```

- 예시를 seed 고정으로 few-shot 후보 70%, 평가용 30%로 나눕니다.
- 보는 지표: BLEU, chrF, 문장 유형 정확도, 응답 시간 평균/p95, fallback 전환 수, 지어내기 의심 문장 수
- 결과는 `results/eval_{backend}_{날짜시각}.md`에 저장됩니다.
- 지어내기 검사는 kiwipiepy 형태소 분석으로 출력 문장의 내용어를 뽑아, gloss와 기능어 목록에 없는 단어를 표시합니다. 오탐이 있으니 사람이 확인하는 목록으로 쓰세요.

## Docker 실행

```bash
cp .env.example .env    # 키 입력
docker compose up -d --build
docker compose logs -f
curl http://localhost:8000/health
```

로컬과 EC2에서 같은 `Dockerfile`과 `docker-compose.yml`을 씁니다. `config.yaml`과 `data/`는 볼륨으로 마운트되므로, 이 둘만 바꿀 때는 `docker compose restart`로 충분합니다.

## AWS EC2 배포

API 번역기는 GPU가 필요 없습니다. `t3.small` 정도면 충분합니다.

1. **인스턴스 생성**
   - AMI: Ubuntu 24.04 LTS
   - 유형: `t3.small`
   - 키 페어 생성
2. **보안 그룹 인바운드 규칙**
   - `22/tcp` SSH: 내 IP만 허용
   - `8000/tcp` 번역 서버: temi가 접속하는 네트워크(학교/연구실 공인 IP) 대역만 허용. `0.0.0.0/0`은 피하세요. 이 서버에는 인증이 없습니다.
   - 나중에 Nginx와 HTTPS를 붙이면 `443/tcp`만 열고 8000은 닫습니다.
3. **Docker 설치**
   ```bash
   sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
   sudo usermod -aG docker $USER && newgrp docker
   ```
4. **코드 받기와 환경변수 설정**
   ```bash
   git clone https://github.com/MobileRobot-SonEum/Server.git && cd Server
   cp .env.example .env && nano .env       # ANTHROPIC_API_KEY 입력
   chmod 600 .env
   ```
   키는 `.env` 파일에만 두고, 이미지나 저장소에는 넣지 마세요. 더 안전하게 하려면 AWS Systems Manager Parameter Store에 키를 넣고 시작 스크립트에서 `.env`를 만드는 방법도 있습니다.
5. **실행과 확인**
   ```bash
   docker compose up -d --build
   curl http://localhost:8000/health
   ```
   로컬 PC에서는 `curl http://<EC2 공인 IP>:8000/health`로 확인합니다. `restart: unless-stopped`가 설정되어 있어서 인스턴스를 재부팅해도 다시 뜹니다.
6. **업데이트**
   ```bash
   git pull && docker compose up -d --build
   ```

## 예시 데이터

`data/examples.jsonl`은 **직접 작성한 임시 예시**입니다(첫 줄에 표시). 형식은 다음과 같습니다.

```json
{"glosses": ["나", "감기", "진료", "원하다"], "sentence": "감기 때문에 진료를 받고 싶어요.", "type": "declarative", "scenario": "hospital"}
```

AIHub 한국수어(KSL-Guide 문장 데이터) 원본 형식을 확인하면 `app/data_tools/aihub_converter.py`를 구현해 같은 형식으로 변환할 예정입니다.

## 범위 밖

gloss 분류기, STT/TTS, WebSocket 게이트웨이는 이 저장소에서 다루지 않습니다.
