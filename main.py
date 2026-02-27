"""
FastAPI entry point — flat layout version.
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Interview Scheduler API starting...")
    yield
    logger.info("Interview Scheduler API shutting down...")


app = FastAPI(
    title="Interview Scheduler API",
    description="Autonomous AI-powered interview scheduling",
    version="1.0.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health", tags=["ops"])
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}


@app.get("/", tags=["ops"])
async def root():
    return {
        "message": "Interview Scheduler API is running",
        "docs": "/docs",
        "health": "/health",
    }


# ── Routers — using importlib to avoid conflict with 'requests' HTTP library
import importlib.util, sys


def _load_router(filename: str, module_name: str):
    try:
        spec = importlib.util.spec_from_file_location(module_name, f"/app/{filename}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        app.include_router(mod.router)
        logger.info("Registered router: %s", module_name)
    except Exception as e:
        logger.warning("Could not load router %s: %s", filename, e)


_load_router("scheduling_requests.py", "scheduling_requests")
_load_router("auth.py", "auth_routes")
_load_router("webhooks.py", "webhook_routes")
