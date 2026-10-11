from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.services.maintenance import run_file_maintenance


settings = get_settings()
settings.web_asset_dir.mkdir(parents=True, exist_ok=True)
settings.teacher_asset_dir.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)


async def _maintenance_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_file_maintenance)
        except Exception:
            logger.exception("file maintenance failed")
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                stop.wait(), timeout=max(settings.maintenance_interval_seconds, 60)
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    task = asyncio.create_task(_maintenance_loop(stop))
    try:
        yield
    finally:
        stop.set()
        await task


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)
app.mount("/assets/rational", StaticFiles(directory=settings.web_asset_dir), name="assets")
app.mount(
    "/assets/teacher",
    StaticFiles(directory=settings.teacher_asset_dir),
    name="teacher-assets",
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": f"http_{exc.status_code}", "message": str(exc.detail), "details": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"code": "validation_error", "message": "请求参数无效",
                 "details": jsonable_encoder(exc.errors(), custom_encoder={ValueError: str})},
    )
