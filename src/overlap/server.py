"""FastAPI front door.

Holds the wiring: a background consumer pulls events off the LaserData stream,
hands each to the RocketRide motion layer, and pushes the result to the browser
over SSE so the graph grows while you watch.

It also serves the two frontends: the projector map (`web/index.html` + its
`/style.css` and `/app.js`) and the phone check-in page (`web/join.html` at
`/join`). Every live signal the room produces enters here as an event on the
stream, so the map and the phones are looking at the same memory.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import uuid
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


def _stats_payload() -> dict[str, Any]:
    """memory.stats() plus the backends block the banner reads. Kept in one place
    so the SSE `stats` frames and the `/api/stats` poll carry the same shape —
    the frontend's setStats() looks for `.backends`, so every stats frame must
    include it, not just the HTTP one."""
    return {
        **memory.stats(),
        "backends": {
            "memory": cfg.falkor_backend,
            "stream": cfg.stream_backend,
            "motion": cfg.motion_backend,
            "guild": cfg.guild_backend,
        },
    }


def _mint_id(name: str) -> str:
    """Mint a stable-ish id for a live check-in: `live-<name-slug>-<rand>`. The
    slug keeps it human-readable in the graph and the trace; the short random
    suffix keeps two people with the same name from colliding."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "guest"
    return f"live-{slug}-{uuid.uuid4().hex[:4]}"


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
        # trace: the full motion record. The map's agent panel reads record.steps
        # off this frame (it wants the ms/proposal alongside the steps), so we
        # forward the whole record rather than just record["steps"].
        _broadcast("trace", record)
        _broadcast("graph", memory.snapshot())
        _broadcast("stats", _stats_payload())
        _broadcast("energy", memory.room_energy())
        # only when this event actually produced one, otherwise the panel
        # repeats the previous introduction on every subsequent event. A held or
        # vetoed nudge (and any feedback) never grows the outbox, so this stays a
        # faithful "a delivery happened" signal.
        if len(motion.outbox) > delivered_before:
            _broadcast("outbox", motion.outbox[-1])


@app.on_event("startup")
async def _startup() -> None:
    memory.ensure_indices()
    await bus.start()
    asyncio.create_task(_consume())


# ---------------------------------------------------------------------- #
# static: the two frontends (root-path references must resolve)
# ---------------------------------------------------------------------- #

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html", media_type="text/html")


@app.get("/style.css")
async def style_css() -> FileResponse:
    return FileResponse(WEB / "style.css", media_type="text/css")


@app.get("/app.js")
async def app_js() -> FileResponse:
    return FileResponse(WEB / "app.js", media_type="application/javascript")


@app.get("/join")
async def join() -> FileResponse:
    return FileResponse(WEB / "join.html", media_type="text/html")


# ---------------------------------------------------------------------- #
# reads
# ---------------------------------------------------------------------- #

@app.get("/api/graph")
async def graph() -> JSONResponse:
    return JSONResponse(memory.snapshot())


@app.get("/api/stats")
async def stats() -> JSONResponse:
    return JSONResponse(_stats_payload())


@app.get("/api/energy")
async def energy() -> JSONResponse:
    return JSONResponse(memory.room_energy())


@app.get("/api/route")
async def route(a: str, b: str) -> JSONResponse:
    return JSONResponse(memory.route(a, b))


@app.get("/api/bridges")
async def bridges() -> JSONResponse:
    return JSONResponse(memory.bridge_topics())


@app.get("/api/outbox")
async def outbox() -> JSONResponse:
    return JSONResponse(motion.outbox[-20:])


@app.get("/api/inbox/{person_id}")
async def inbox(person_id: str) -> JSONResponse:
    """Beelines addressed to one phone. A checked-in device polls this to learn
    it has been told to go meet someone (the nudge-reaches-the-phone loop). Only
    delivered introductions land in the outbox, so this is exactly the set of
    nudges the person should act on, newest first, capped at the last ~10."""
    mine = [it for it in motion.outbox if it.get("to_id") == person_id]
    return JSONResponse(list(reversed(mine))[:10])


@app.get("/api/trace")
async def trace() -> JSONResponse:
    return JSONResponse(motion.trace[-20:])


# ---------------------------------------------------------------------- #
# writes: every one publishes onto the stream so the motion pipeline runs
# ---------------------------------------------------------------------- #

@app.post("/api/event")
async def post_event(request: Request) -> JSONResponse:
    body = await request.json()
    event = streammod.make_event(body["type"], **body.get("payload", {}))
    await bus.publish(event)
    return JSONResponse({"published": event})


@app.post("/api/checkin")
async def post_checkin(request: Request) -> JSONResponse:
    """A live arrival from a phone. Mint an id, publish a `checkin` event onto the
    stream (so it flows through persist -> match -> gate like any other event),
    and hand the id back so the phone can post state/position/feedback as itself."""
    body = await request.json()
    pid = _mint_id(body.get("name", ""))
    event = streammod.checkin(
        id=pid,
        name=body.get("name", ""),
        role=body.get("role", ""),
        interests=body.get("interests", []) or [],
        ask=body.get("ask", ""),
        offer=body.get("offer", ""),
        zone=body.get("zone", ""),
        state=body.get("state", "open"),
    )
    await bus.publish(event)
    return JSONResponse({"id": pid})


@app.post("/api/state")
async def post_state(request: Request) -> JSONResponse:
    body = await request.json()
    event = streammod.state(id=body["id"], state=body["state"])
    await bus.publish(event)
    return JSONResponse({"published": event})


@app.post("/api/position")
async def post_position(request: Request) -> JSONResponse:
    body = await request.json()
    event = streammod.position(id=body["id"], zone=body["zone"])
    await bus.publish(event)
    return JSONResponse({"published": event})


@app.post("/api/feedback")
async def post_feedback(request: Request) -> JSONResponse:
    body = await request.json()
    event = streammod.feedback(
        from_id=body["from_id"], to_id=body["to_id"], value=body["value"]
    )
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
    sequence = seedmod.demo_sequence()

    async def run() -> None:
        for event in sequence:
            await bus.publish(event)
            await asyncio.sleep(delay)

    asyncio.create_task(run())
    return JSONResponse({"seeding": len(sequence), "delay": delay})


@app.post("/api/reset")
async def post_reset() -> JSONResponse:
    memory.reset()
    motion.outbox.clear()
    motion.trace.clear()
    _broadcast("graph", memory.snapshot())
    _broadcast("stats", _stats_payload())
    _broadcast("energy", memory.room_energy())
    _broadcast("reset", {})
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------- #
# SSE
# ---------------------------------------------------------------------- #

@app.get("/api/stream")
async def sse(request: Request) -> StreamingResponse:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
    _clients.append(q)

    async def gen():
        try:
            # replay current state so a fresh tab is never blank
            yield f"data: {json.dumps({'kind': 'graph', 'data': memory.snapshot()})}\n\n"
            yield f"data: {json.dumps({'kind': 'stats', 'data': _stats_payload()})}\n\n"
            yield f"data: {json.dumps({'kind': 'energy', 'data': memory.room_energy()})}\n\n"
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
