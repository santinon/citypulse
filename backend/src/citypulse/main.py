"""FastAPI application factory. Run with `uv run uvicorn citypulse.main:create_app --factory`."""

from typing import Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from citypulse import __version__
from citypulse.settings import Settings

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok"]
    version: str


@router.get("/health")
def health() -> Health:
    """Liveness only: the process answers. It does not check the feed or the database."""
    return Health(status="ok", version=__version__)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="CityPulse API", version=__version__)
    app.state.settings = settings if settings is not None else Settings()
    app.include_router(router)
    return app
