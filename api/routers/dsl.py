"""DSL parse + validate endpoint.

Mirrors the geo-lm contract (``POST /api/dsl/parse``) so the 3D dock can
debounce-validate operator-supplied DSL before kicking off a run. Pure
function: no persistence, no side effects — just parser/validator output
shaped for inline UI rendering.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.schemas import DSLParseError, DSLParseRequest, DSLParseResponse
from geo_nyc.parsers.dsl import parse_and_validate
from geo_nyc.parsers.dsl.errors import DSLError, DSLSyntaxError

router = APIRouter(prefix="/dsl", tags=["dsl"])


def _error_payload(exc: DSLError) -> DSLParseError:
    location = getattr(exc, "location", None)
    line = getattr(exc, "line", None)
    column = getattr(exc, "column", None)
    if location is not None:
        line = line or getattr(location, "line", None)
        column = column or getattr(location, "column", None)
    return DSLParseError(line=line, column=column, message=str(exc))


@router.post("/parse", response_model=DSLParseResponse)
async def parse_dsl(request: DSLParseRequest) -> DSLParseResponse:
    text = request.text or ""
    if not text.strip():
        return DSLParseResponse(is_valid=False, errors=[
            DSLParseError(message="DSL text is empty.")
        ])

    try:
        program, report = parse_and_validate(text)
    except DSLSyntaxError as exc:
        return DSLParseResponse(is_valid=False, errors=[_error_payload(exc)])
    except DSLError as exc:
        return DSLParseResponse(is_valid=False, errors=[_error_payload(exc)])

    errors = [_error_payload(err) for err in report.errors]
    return DSLParseResponse(
        is_valid=report.is_valid,
        rocks_count=len(program.rocks),
        depositions_count=len(program.depositions),
        erosions_count=len(program.erosions),
        intrusions_count=len(program.intrusions),
        errors=errors,
        warnings=list(report.warnings),
    )
