"""API 路由聚合。"""
from fastapi import APIRouter

from backend.api import export, jobs, map as map_api, orgs, prefs, search, system

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(search.router)
api_router.include_router(jobs.router)
api_router.include_router(orgs.router)
api_router.include_router(prefs.router)
api_router.include_router(map_api.router)
api_router.include_router(export.router)
