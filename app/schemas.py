"""요청/응답 모델. HTTP 계층과 WebSocket 게이트웨이가 함께 쓴다."""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SentenceType(str, Enum):
    declarative = "declarative"
    interrogative = "interrogative"
    unknown = "unknown"


class Scenario(str, Enum):
    hospital = "hospital"
    civil_office = "civil_office"
    university = "university"
    general = "general"


class GlossItem(BaseModel):
    gloss: str = Field(..., min_length=1, max_length=50)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class ContextTurn(BaseModel):
    speaker: str = Field("staff", description="staff(창구 직원) | user(수어 사용자)")
    text: str = Field(..., max_length=500)


class TranslationRequest(BaseModel):
    glosses: List[GlossItem] = Field(..., min_length=1, max_length=40)
    sentence_type: SentenceType = SentenceType.unknown
    context: List[ContextTurn] = Field(default_factory=list, max_length=10)
    scenario: Scenario = Scenario.general
    num_candidates: int = Field(1, ge=1, le=3)

    def gloss_words(self) -> List[str]:
        return [g.gloss.strip() for g in self.glosses]


class TranslationResult(BaseModel):
    """번역기(Translator)가 돌려주는 순수 결과."""
    sentence: str
    candidates: List[str] = Field(default_factory=list)
    sentence_type: SentenceType = SentenceType.declarative


class TranslationResponse(BaseModel):
    """서비스가 최종적으로 돌려주는 응답."""
    sentence: str
    candidates: List[str] = Field(default_factory=list)
    sentence_type: SentenceType
    backend: str
    latency_ms: float
    cached: bool = False
    low_confidence: List[str] = Field(default_factory=list, description="기준값보다 확신도가 낮았던 gloss")
