import hmac
import sqlite3

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from traceweave_memory.adapters.media import FileMedia
from traceweave_memory.adapters.sqlite import SQLiteRepository
from traceweave_memory.api.schemas import (
    AddRequest,
    AddResponse,
    SearchRequest,
    SearchResponse,
)
from traceweave_memory.application import MemoryService
from traceweave_memory.config import Settings
from traceweave_memory.domain import MediaError, MediaUnavailable, WriteConflict


class BodyLimit:
    """Bound actual ASGI bytes, including requests without Content-Length."""

    def __init__(self, app, maximum: int):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > self.maximum:
                response = JSONResponse({"detail": "request exceeds local body limit"}, 413)
                return await response(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        position = 0

        async def replay():
            nonlocal position
            if position < len(chunks):
                message = chunks[position]
                position += 1
                return message
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = SQLiteRepository(settings.data_dir / "memory.sqlite3")
    service = MemoryService(repository, FileMedia(settings.data_dir / "media"))
    app = FastAPI(title="TraceWeave Memory", version="0.1.0")
    app.add_middleware(BodyLimit, maximum=settings.max_body_bytes)
    app.state.service = service
    app.state.settings = settings

    def authorize(request: Request):
        if not settings.api_key:
            return
        if settings.auth_mode == "x-api-key":
            received = request.headers.get("x-api-key", "")
        else:
            header = request.headers.get("authorization", "")
            scheme, _, received = header.partition(" ")
            if scheme.casefold() != settings.auth_mode:
                received = ""
        if not hmac.compare_digest(received.encode(), settings.api_key.encode()):
            from fastapi import HTTPException

            raise HTTPException(401, "invalid memory system credential")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic errors can include the whole input/base64/key: never echo it.
        return JSONResponse({"detail": "invalid request schema"}, status_code=422)

    @app.exception_handler(WriteConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(MediaError)
    async def invalid_media(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(MediaUnavailable)
    async def missing_media(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, exc):
        return JSONResponse({"detail": "storage temporarily unavailable"}, status_code=503)

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return JSONResponse({"detail": "storage temporarily unavailable"}, status_code=503)

    @app.get("/health")
    def health():
        with repository.connect() as db:
            db.execute("SELECT 1 FROM requests LIMIT 1").fetchone()
        return {"status": "ok", "retrieval": "B0-lexical"}

    @app.post("/add", response_model=AddResponse, dependencies=[Depends(authorize)])
    def add(request: AddRequest):
        return service.add(request.model_dump(exclude_none=True))

    @app.post("/search", response_model=SearchResponse, dependencies=[Depends(authorize)])
    def search(request: SearchRequest):
        return service.search(request.model_dump(exclude_none=True))

    return app
