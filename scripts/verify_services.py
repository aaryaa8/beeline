"""Run this the moment each account comes online. It proves the credential
actually works, which is the thing you do not want to discover at 2pm.

    .venv/bin/python scripts/verify_services.py

Each check is independent. A red line tells you exactly which env var to fix.
"""
from __future__ import annotations

import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from overlap.config import cfg  # noqa: E402

OK, BAD, SKIP = "  \033[32mOK\033[0m  ", "  \033[31mFAIL\033[0m", "  \033[33mSKIP\033[0m"


def check_falkor() -> None:
    print(f"FalkorDB (memory)  [FALKOR_BACKEND={cfg.falkor_backend}]")
    try:
        from overlap.memory import Memory

        m = Memory()
        m.ensure_indices()
        m.g.query("RETURN 1")
        s = m.stats()
        print(f"{OK} {cfg.falkor_host}:{cfg.falkor_port} graph={cfg.graph_name} {s}")
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {str(e)[:120]}")
        if cfg.falkor_backend == "cloud":
            print("       cloud auth failing: re-set the instance password in the")
            print("       FalkorDB console (Modify), paste it into FALKORCLOUD_PASSWORD.")
        else:
            print("       is the local redis+falkordb module running on :6379?")


def check_rocketride() -> None:
    print("RocketRide (motion)")
    if not cfg.rocketride_auth:
        print(f"{SKIP} ROCKETRIDE_AUTH not set, running MOTION_BACKEND=local")
        return
    if cfg.rocketride_uri.startswith(("http://", "ws://")):
        print(f"{BAD} ROCKETRIDE_URI must be https:// or wss:// for Cloud")
        return
    try:
        import httpx

        # /status is the real health endpoint; the token authenticates it.
        # Pipeline execution is via the rocketride SDK, not a REST run path.
        r = httpx.get(
            f"{cfg.rocketride_uri}/status",
            headers={"Authorization": f"Bearer {cfg.rocketride_auth}"},
            timeout=15,
        )
        if r.status_code < 400:
            ver = r.json().get("data", {}).get("server", {}).get("version", {}).get("version", "?")
            print(f"{OK} {cfg.rocketride_uri} authenticated, server v{ver}")
        else:
            print(f"{BAD} {cfg.rocketride_uri}/status -> {r.status_code}: {r.text[:160]}")
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {e}")


def check_guild() -> None:
    print("Guild.ai (coordination)")
    if not cfg.guild_api_key:
        print(f"{SKIP} GUILD_API_KEY not set, running GUILD_BACKEND=local")
        return
    try:
        import httpx

        r = httpx.get(
            f"{cfg.guild_base_url}/v1/workspaces/{cfg.guild_workspace}/agents",
            headers={"Authorization": f"Bearer {cfg.guild_api_key}"},
            timeout=15,
        )
        print(f"{OK if r.status_code < 400 else BAD} {cfg.guild_base_url} -> {r.status_code}")
        if r.status_code >= 400:
            print(f"       body: {r.text[:200]}")
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {e}")


def check_laser() -> None:
    print("LaserData (real-time)")
    if cfg.laser_host in ("", "127.0.0.1") or not cfg.laser_password:
        print(f"{SKIP} no LASER_HOST/PASSWORD set")
        return
    try:
        import asyncio

        from apache_iggy import IggyClient  # noqa: F401

        async def go() -> None:
            c = IggyClient.from_connection_string(cfg.laser_connection_string)
            await asyncio.wait_for(c.connect(), timeout=12)
            await asyncio.wait_for(c.login_user(cfg.laser_username, cfg.laser_password), timeout=12)
            await asyncio.wait_for(c.ping(), timeout=8)

        asyncio.run(go())
        print(f"{OK} connected to {cfg.laser_host} via {cfg.laser_scheme}")
    except ImportError:
        print(f"{BAD} apache-iggy not installed: uv pip install -e '.[stream]'")
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {str(e)[:120]}")
        print(f"       host is live; the Warden HTTPS proxy path/scheme is the open question.")
        print(f"       ask the LaserData mentor for the exact iggy connection string,")
        print(f"       then set LASER_URI in .env. Demo runs on STREAM_BACKEND=local meanwhile.")


def check_llm() -> None:
    print("LLM (nudge copy, optional)")
    if not cfg.has_llm:
        print(f"{SKIP} ANTHROPIC_API_KEY not set, using template copy")
        return
    try:
        import anthropic

        c = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        c.messages.create(model=cfg.model, max_tokens=8,
                          messages=[{"role": "user", "content": "hi"}])
        print(f"{OK} {cfg.model}")
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"\n  cwd={os.getcwd()}\n")
    for fn in (check_falkor, check_rocketride, check_guild, check_laser, check_llm):
        fn()
        print()
