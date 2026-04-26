from fastapi import FastAPI

from api.routers.layers import router as layers_router
from api.routers.optimize import router as optimize_router


app = FastAPI(
    title="geo-nyc Part 3 API",
    version="0.1.0",
    description="GIS layers and optimization service for Urban Subsurface AI.",
)

app.include_router(layers_router)
app.include_router(optimize_router)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
