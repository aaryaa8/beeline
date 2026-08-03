"""FastAPI front door.

Holds the wiring: a background consumer pulls events off the LaserData stream,
hands each to the RocketRide motion layer, and pushes the result to the browser
over SSE so the graph grows while you watch.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import seed as seedmod
from . import stream as streammod
from .config import cfg
from .memory import Memory
from .motion import Motion

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"

app = FastAPI(title="Overlap")

memory = Memory()
bus = streammod.build_stream()
motion = Motion(memory)

# browser subscribers
_clients: list[asyncio.Queue[str]] = []


def _broadcast(kind: str, data: Any) -> None:
    payload = json.dumps({"kind": kind, "data": data})
    for q in list(_clients):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


async def _consume() -> None:
    """The spine: stream -> motion -> browser."""
    async for event in bus.subscribe():
        _broadcast("event", event)
        delivered_before = len(motion.outbox)
        try:
            record = await motion.handle(event)
        except Exception as exc:  # keep the demo alive
            _broadcast("error", {"message": str(exc), "event": event})
            continue
        _broadcast("trace", record)
        _broadcast("graph", memory.snapshot())
        _broadcast("stats", memory.stats())
        # only when this event actually produced one, otherwise the panel
        # repeats the previous introduction on every subsequent event
        if len(motion.outbox) > delivered_before:
            _broadcast("outbox", motion.outbox[-1])


@app.on_event("startup")
async def _startup() -> None:
    memory.ensure_indices()
    await bus.start()
    asyncio.create_task(_consume())


# ---------------------------------------------------------------------- #
# routes
# ---------------------------------------------------------------------- #

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/graph")
async def graph() -> JSONResponse:
    return JSONResponse(memory.snapshot())


@app.get("/api/stats")
async def stats() -> JSONResponse:
    return JSONResponse(
        {
            **memory.stats(),
            "backends": {
                "memory": "falkordb",
                "stream": cfg.stream_backend,
                "motion": cfg.motion_backend,
                "guild": cfg.guild_backend,
            },
        }
    )


@app.get("/api/bridges")
async def bridges() -> JSONResponse:
    return JSONResponse(memory.bridge_topics())


@app.get("/api/outbox")
async def outbox() -> JSONResponse:
    return JSONResponse(motion.outbox[-20:])


@app.get("/api/trace")
async def trace() -> JSONResponse:
    return JSONResponse(motion.trace[-20:])


@app.post("/api/event")
async def post_event(request: Request) -> JSONResponse:
    body = await request.json()
    event = streammod.make_event(body["type"], **body.get("payload", {}))
    await bus.publish(event)
    return JSONResponse({"published": event})


@app.post("/api/seed")
async def post_seed(request: Request) -> JSONResponse:
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    delay = float(body.get("delay", 0.7))

    async def run() -> None:
        for event in seedmod.opening_sequence():
            await bus.publish(event)
            await asyncio.sleep(delay)

    asyncio.create_task(run())
    return JSONResponse({"seeding": len(seedmod.opening_sequence()), "delay": delay})


@app.post("/api/reset")
async def post_reset() -> JSONResponse:
    memory.reset()
    motion.outbox.clear()
    motion.trace.clear()
    _broadcast("graph", memory.snapshot())
    _broadcast("stats", memory.stats())
    _broadcast("reset", {})
    return JSONResponse({"ok": True})


@app.get("/api/stream")
async def sse(request: Request) -> StreamingResponse:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
    _clients.append(q)

    async def gen():
        try:
            yield f"data: {json.dumps({'kind': 'graph', 'data': memory.snapshot()})}\n\n"
            yield f"data: {json.dumps({'kind': 'stats', 'data': memory.stats()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in _clients:
                _clients.remove(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
