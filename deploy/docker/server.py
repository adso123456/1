"""生产容器入口：同一端口提供 FastAPI API 与 React 静态页面。"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from step4_server import create_server


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_production_app():
    index_path = FRONTEND_DIST / "index.html"
    assets_path = FRONTEND_DIST / "assets"
    if not index_path.is_file() or not assets_path.is_dir():
        raise RuntimeError("镜像缺少 React 构建产物")

    app = create_server().create_app()
    app.router.routes[:] = [
        route for route in app.router.routes
        if getattr(route, "path", None) != "/"
    ]
    app.mount("/assets", StaticFiles(directory=assets_path), name="frontend-assets")

    @app.get("/{requested_path:path}", include_in_schema=False)
    async def frontend(requested_path: str):
        if requested_path == "api" or requested_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (FRONTEND_DIST / requested_path).resolve()
        if candidate.is_relative_to(FRONTEND_DIST.resolve()):
            if candidate.is_file():
                return FileResponse(candidate)
            if candidate.is_dir():
                directory_index = candidate / "index.html"
                if directory_index.is_file():
                    return FileResponse(directory_index)
        return FileResponse(index_path)

    return app


if __name__ == "__main__":
    uvicorn.run(
        create_production_app(),
        host="0.0.0.0",
        port=int(os.environ.get("VANNA_SERVER_PORT", "8000")),
        log_level="info",
    )
