"""FastAPI 应用入口：仅监听 127.0.0.1（绑定地址由 run.py / uvicorn 参数控制）。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.api import api_router
from backend.core.config import APP_NAME, APP_VERSION, DATA_DIR, FRONTEND_DIR
from backend.db.database import Base, engine
from backend.db.seed import ensure_seeded


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    ensure_seeded()
    yield


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")

app.include_router(api_router)

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
