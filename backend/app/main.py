from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.cache import cache
from app.config import settings
from app.database import read_engine, write_engine
from app.metrics import setup_metrics


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await cache.close()
    await write_engine.dispose()
    await read_engine.dispose()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """Liveness probe used by Docker Compose, Nginx and Kubernetes."""
    return {"status": "ok"}


# IMPORTANT: register /health and /metrics before the catch-all
# GET /{short_code} route, otherwise they would be treated as short codes.
setup_metrics(app)

app.include_router(router)
