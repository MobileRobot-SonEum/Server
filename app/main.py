"""FastAPI HTTP 계층. 번역 로직은 app.service에 있다."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request

from app.schemas import TranslationRequest, TranslationResponse
from app.service import TranslationService, build_service


def create_app(service: Optional[TranslationService] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service or build_service()
        yield

    app = FastAPI(title="KSL gloss → 한국어 번역 서버", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> dict:
        svc: TranslationService = request.app.state.service
        return {"status": "ok", "backend": svc.backend_name, "fallback": svc.fallback.name}

    # 동기 함수로 두면 FastAPI가 스레드풀에서 실행하므로 이벤트 루프를 막지 않는다.
    @app.post("/translate", response_model=TranslationResponse)
    def translate(body: TranslationRequest, request: Request) -> TranslationResponse:
        svc: TranslationService = request.app.state.service
        return svc.translate(body)

    return app


app = create_app()
