"""The FastAPI application: Anthropic-shaped routes over an Ollama backend."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import config as config_module
from .translate import build_request, build_response, system_text, as_blocks
from .stream import stream_response

log = logging.getLogger("claude_ollama")


def error_response(status: int, message: str, kind: str = "api_error") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": kind, "message": message}},
    )


def create_app(cfg: config_module.Config | None = None) -> FastAPI:
    cfg = cfg or config_module.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        timeout = httpx.Timeout(cfg.timeout, connect=10.0)
        async with httpx.AsyncClient(base_url=cfg.base_url, timeout=timeout) as client:
            app.state.client = client
            log.info("proxying to Ollama at %s", cfg.base_url)
            yield

    app = FastAPI(title="claude-ollama", lifespan=lifespan)
    app.state.cfg = cfg

    @app.get("/health")
    async def health() -> dict:
        """Report our own status and whether Ollama is actually reachable."""
        try:
            reply = await app.state.client.get("/api/tags", timeout=5.0)
            models = [m["name"] for m in reply.json().get("models", [])]
            upstream: dict[str, Any] = {"reachable": True, "models": models}
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as text
            upstream = {"reachable": False, "error": str(exc)}
        return {
            "ok": upstream["reachable"],
            "ollama": {"base_url": cfg.base_url, **upstream},
            "default_model": cfg.default_model,
            "num_ctx": cfg.num_ctx,
        }

    @app.get("/v1/models")
    async def list_models() -> Any:
        try:
            reply = await app.state.client.get("/api/tags")
            reply.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return error_response(502, f"cannot reach Ollama at {cfg.base_url}: {exc}")
        data = [
            {
                "type": "model",
                "id": model["name"],
                "display_name": model["name"],
                "created_at": model.get("modified_at", ""),
            }
            for model in reply.json().get("models", [])
        ]
        return {
            "data": data,
            "has_more": False,
            "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None,
        }

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request) -> Any:
        """An estimate. Ollama exposes no tokenizer endpoint to ask properly."""
        body = await request.json()
        text = system_text(body.get("system"))
        for message in body.get("messages", []):
            for block in as_blocks(message.get("content")):
                text += json.dumps(block)
        for tool in body.get("tools") or []:
            text += json.dumps(tool)
        return {"input_tokens": max(1, len(text) // 4)}

    @app.post("/v1/messages")
    async def messages(request: Request) -> Any:
        try:
            body = await request.json()
        except json.JSONDecodeError as exc:
            return error_response(400, f"invalid JSON body: {exc}", "invalid_request_error")

        payload = build_request(body, cfg)
        model = payload["model"]
        client: httpx.AsyncClient = app.state.client
        log.info(
            "%s -> %s (%d messages, %d tools, stream=%s)",
            body.get("model"),
            model,
            len(payload["messages"]),
            len(payload.get("tools", [])),
            payload["stream"],
        )

        if payload["stream"]:
            return StreamingResponse(
                stream_response(client, payload, cfg, model),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        reply = await _post_chat(client, payload)
        if isinstance(reply, JSONResponse):
            return reply
        return build_response(reply, model, cfg)

    return app


async def _post_chat(client: httpx.AsyncClient, payload: dict) -> dict | JSONResponse:
    """POST /api/chat, retrying once without ``think`` if the model rejects it."""
    for attempt, candidate in enumerate((payload, {k: v for k, v in payload.items() if k != "think"})):
        try:
            reply = await client.post("/api/chat", json=candidate)
        except httpx.HTTPError as exc:
            return error_response(502, f"cannot reach Ollama: {exc}")
        if reply.status_code < 400:
            return reply.json()
        text = reply.text
        retryable = attempt == 0 and reply.status_code == 400 and "think" in payload and "think" in text
        if not retryable:
            return error_response(reply.status_code, f"Ollama returned {reply.status_code}: {text[:500]}")
    return error_response(502, "Ollama request failed")  # pragma: no cover


def main() -> None:
    import uvicorn

    cfg = config_module.load()
    logging.basicConfig(
        level=logging.DEBUG if cfg.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    # httpx logs every request itself, which doubles the log for no gain.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")
