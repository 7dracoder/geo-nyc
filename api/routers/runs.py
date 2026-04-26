"""Run orchestration endpoints.

Fixture-mode pipeline: synchronously turn a request into a
:class:`RunManifest` plus on-disk artifacts (mesh, depth field, DSL,
extraction, validation report) and return the manifest verbatim.

Later phases swap the synchronous fixture pipeline for an async LLM
extraction + GemPy modelling pipeline; the response shape and route
table stay identical.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.schemas import RunListResponse, RunManifest, RunRequest
from geo_nyc.exceptions import DocumentNotFoundError, RunError, RunNotFoundError
from geo_nyc.runs import RunService, get_run_service

router = APIRouter(tags=["runs"])


@router.post(
    "/run",
    response_model=RunManifest,
    status_code=status.HTTP_201_CREATED,
)
async def post_run(
    request: RunRequest,
    service: RunService = Depends(get_run_service),
) -> RunManifest:
    payload = request.model_dump(exclude_none=True)
    try:
        return await service.acreate_run(
            request_payload=payload,
            fixture_name=request.fixture_name or "nyc_demo",
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RunError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/run/{run_id}", response_model=RunManifest)
async def get_run(
    run_id: str,
    service: RunService = Depends(get_run_service),
) -> RunManifest:
    try:
        return service.get_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except RunError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    limit: int = 50,
    service: RunService = Depends(get_run_service),
) -> RunListResponse:
    items = service.list_runs(limit=limit)
    return RunListResponse(items=items, total=len(items))
