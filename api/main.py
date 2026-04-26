"""FastAPI application entry point for geo-nyc.

Run with::

    uvicorn api.main:app --reload

Or, equivalently::

    python -m api.main

The lifespan handler is responsible for ensuring all storage
directories exist and for closing the LLM provider's connection pool on
shutdown so we don't leak sockets in tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import documents, dsl, health, layers, optimize, runs
from geo_nyc import __version__
from geo_nyc.ai import get_default_provider, reset_provider_cache
from geo_nyc.config import get_settings
from geo_nyc.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_directories()
    log.info(
        "geo-nyc starting | version=%s | fixtures=%s | gempy=%s",
        __version__,
        settings.use_fixtures,
        settings.enable_gempy,
    )

    yield

    try:
        provider = get_default_provider()
    except Exception:
        provider = None
    if provider is not None:
        await provider.aclose()
    reset_provider_cache()
    log.info("geo-nyc shutdown complete")


def create_app() -> FastAPI:
    """App factory.

    Using a factory (rather than a module-level ``app = FastAPI(...)``)
    keeps tests cleanly isolated: each test that needs a fresh app
    instance can call ``create_app()`` after mutating env vars.
    """

    settings = get_settings()
    app = FastAPI(
        title="geo-nyc API",
        description=(
            "Local-only backend for Urban Subsurface AI. "
            "Translates NYC geological PDFs to interactive 3D models without leaving the device."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(documents.router, prefix="/api")
    app.include_router(dsl.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(layers.router, prefix="/api")
    app.include_router(optimize.router, prefix="/api")

    settings.ensure_directories()
    app.mount(
        "/static/exports",
        StaticFiles(directory=str(settings.exports_dir), check_dir=False),
        name="exports",
    )
    app.mount(
        "/static/fields",
        StaticFiles(directory=str(settings.fields_dir), check_dir=False),
        name="fields",
    )
    app.mount(
        "/static/layers",
        StaticFiles(directory=str(settings.data_layer_layers_dir), check_dir=False),
        name="layers",
    )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": "geo-nyc",
            "version": __version__,
            "docs": "/docs",
            "health": "/api/health",
        }

    return app


app = create_app()


def run() -> None:
    """Entry point used by the ``geo-nyc`` console script."""

    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
