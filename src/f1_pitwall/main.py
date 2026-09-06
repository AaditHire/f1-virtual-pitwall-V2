from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from f1_pitwall.api.routes import router
from f1_pitwall.core.config import Settings
from f1_pitwall.core.exceptions import NotFound, ProviderError
from f1_pitwall.core.logging import configure_logging
from f1_pitwall.services.hub import Hub


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        async with httpx.AsyncClient(
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": "F1Pitwall/0.1 (public data hub)"},
        ) as client:
            app.state.hub = Hub(client, settings or Settings.from_env())
            yield

    app = FastAPI(title="F1 Virtual Pit Wall", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError):
        return JSONResponse(
            status_code=503,
            content={
                "error": "provider_unavailable",
                "provider": exc.provider,
                "detail": exc.message,
            },
        )

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    app.include_router(router)
    return app


app = create_app()
