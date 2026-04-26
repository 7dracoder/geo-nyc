"""Health and readiness endpoints.

These are the first integration point for the frontend and the demo
operator. ``/api/health`` is liveness; ``/api/llm/health`` is the LLM
backend readiness probe (Groq by default, or Ollama when configured).
"""

from __future__ import annotations

from fastapi import APIRouter

from api.schemas import HealthResponse, LLMHealthResponse
from geo_nyc import __version__
from geo_nyc.ai import get_default_provider
from geo_nyc.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Process liveness")
async def get_health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        version=__version__,
        use_fixtures=settings.use_fixtures,
        enable_gempy=settings.enable_gempy,
    )


@router.get("/llm/health", response_model=LLMHealthResponse, summary="LLM provider readiness")
async def get_llm_health() -> LLMHealthResponse:
    provider = get_default_provider()
    snapshot = await provider.health_check()
    # health_check() never raises — every key is already shaped for the
    # response model.
    return LLMHealthResponse(**snapshot)
