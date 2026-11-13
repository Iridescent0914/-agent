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
from app.services.maintenance import (
    run_system_maintenance,
    seconds_until_next_business_midnight,
)
from app.services.question_import import ImportProblem
from app.services.question_import_worker import process_one as process_question_import


settings = get_settings()
settings.web_asset_dir.mkdir(parents=True, exist_ok=True)
for chapter in ("rational", "expr"):
    (settings.web_asset_dir / chapter).mkdir(parents=True, exist_ok=True)
settings.teacher_asset_dir.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)


async def _maintenance_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_system_maintenance)
        except Exception:
            logger.exception("system maintenance failed")
        timeout = min(
            max(settings.maintenance_interval_seconds, 60),
            seconds_until_next_business_midnight() + 1,
        )
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                stop.wait(), timeout=timeout
            )


async def _question_import_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            while not stop.is_set() and await asyncio.to_thread(process_question_import):
                pass
        except Exception:
            logger.exception("question import worker failed")
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=max(settings.question_import_worker_interval_seconds, 5))


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    task = asyncio.create_task(_maintenance_loop(stop))
    import_task = asyncio.create_task(_question_import_loop(stop))
    try:
        yield
    finally:
        stop.set()
        await task
        await import_task


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)
for chapter in ("rational", "expr"):
    app.mount(
        f"/assets/{chapter}",
        StaticFiles(directory=settings.web_asset_dir / chapter),
        name=f"assets-{chapter}",
    )
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


@app.exception_handler(ImportProblem)
async def import_problem_handler(_: Request, exc: ImportProblem) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={
        "code": exc.code, "message": str(exc), "details": None,
    })


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"code": "validation_error", "message": "请求参数无效",
                 "details": jsonable_encoder(exc.errors(), custom_encoder={ValueError: str})},
    )
