"""Exception hierarchy for geo-nyc.

Every internal failure should subclass :class:`GeoNYCError` so callers can
catch a single base class. HTTP layers translate these to status codes in
``api.errors`` (added later); never let raw Pydantic / httpx errors leak
across the public boundary.
"""

from __future__ import annotations


class GeoNYCError(Exception):
    """Base class for all geo-nyc raised errors."""


class ConfigurationError(GeoNYCError):
    """Settings or environment misconfiguration."""


class NotFoundError(GeoNYCError):
    """Requested artifact / run / document does not exist."""


class ValidationError(GeoNYCError):
    """Input failed schema or semantic validation."""


# --- LLM layer -------------------------------------------------------------


class LLMError(GeoNYCError):
    """Base for LLM provider failures."""


class LLMConnectionError(LLMError):
    """Could not reach the LLM (e.g. Ollama not running)."""


class LLMTimeoutError(LLMError):
    """LLM call exceeded the configured timeout."""


class LLMResponseError(LLMError):
    """LLM returned a non-2xx HTTP status or unparseable payload."""


# --- DSL ------------------------------------------------------------------


class DSLError(GeoNYCError):
    """Base for DSL parsing/validation errors."""


class DSLSyntaxError(DSLError):
    """DSL text could not be parsed."""


class DSLValidationError(DSLError):
    """DSL parsed but failed semantic validation."""


# --- Modeling -------------------------------------------------------------


class ModelingError(GeoNYCError):
    """Base for GemPy / mesh / field modeling failures."""


class GemPyUnavailableError(ModelingError):
    """GemPy was requested but is not installed in this environment."""


class MeshExportError(ModelingError):
    """Mesh export to .gltf failed."""


class FieldExportError(ModelingError):
    """Scalar field export failed."""


# --- Documents -----------------------------------------------------------


class DocumentError(GeoNYCError):
    """Base for /api/documents pipeline failures."""


class DocumentNotFoundError(NotFoundError, DocumentError):
    """No document with the requested id exists on disk."""


class PDFExtractionError(DocumentError):
    """PDF text extraction failed."""


class UnsupportedDocumentError(DocumentError):
    """Document content is missing or not a supported format."""


# --- Run orchestration ----------------------------------------------------


class RunError(GeoNYCError):
    """Base for /api/run pipeline failures."""


class RunNotFoundError(NotFoundError, RunError):
    """No run with the requested id exists on disk."""
