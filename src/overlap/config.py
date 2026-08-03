"""Central config. Every external service can run in `local` mode so the whole
system boots with zero credentials, then flips to the real service with one
env var once the account exists."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str) -> str:
    return os.getenv(name, default).strip().lower()


@dataclass(frozen=True)
class Config:
    # --- FalkorDB: the memory layer -------------------------------------
    # FALKOR_BACKEND=local uses the FALKOR_* vars (localhost by default);
    # =cloud uses the FALKORCLOUD_* vars for the managed FalkorDB Cloud
    # instance. The demo runs on local (fast, offline-proof); cloud is the
    # "managed infrastructure" story and a fallback.
    falkor_backend: str = _flag("FALKOR_BACKEND", "local")
    graph_name: str = os.getenv("OVERLAP_GRAPH", "overlap")

    @property
    def falkor_host(self) -> str:
        if self.falkor_backend == "cloud":
            return os.getenv("FALKORCLOUD_HOST", "")
        return os.getenv("FALKOR_HOST", "localhost")

    @property
    def falkor_port(self) -> int:
        if self.falkor_backend == "cloud":
            return int(os.getenv("FALKORCLOUD_PORT", "0"))
        return int(os.getenv("FALKOR_PORT", "6379"))

    @property
    def falkor_username(self) -> str | None:
        if self.falkor_backend == "cloud":
            return os.getenv("FALKORCLOUD_USERNAME") or None
        return os.getenv("FALKOR_USERNAME") or None

    @property
    def falkor_password(self) -> str | None:
        if self.falkor_backend == "cloud":
            return os.getenv("FALKORCLOUD_PASSWORD") or None
        return os.getenv("FALKOR_PASSWORD") or None

    # --- RocketRide: the motion layer -----------------------------------
    # `local` runs the same decision logic in-process; `cloud` calls the
    # deployed .pipe. The contract between them is identical (see motion.py).
    motion_backend: str = _flag("MOTION_BACKEND", "local")
    rocketride_uri: str = os.getenv("ROCKETRIDE_URI", "https://api.rocketride.ai")
    rocketride_auth: str | None = os.getenv("ROCKETRIDE_AUTH") or None
    rocketride_pipeline: str = os.getenv("ROCKETRIDE_PIPELINE", "overlap-matchmaker")

    # --- LaserData: the real-time layer ---------------------------------
    stream_backend: str = _flag("STREAM_BACKEND", "local")
    laser_host: str = os.getenv("LASER_HOST", "127.0.0.1")
    laser_port: int = int(os.getenv("LASER_PORT", "8090"))
    # Managed LaserData fronts Iggy over an HTTPS Warden proxy, so the default
    # scheme is iggy+http on 443. Override LASER_URI wholesale with whatever the
    # LaserData mentor/docs give if the proxy needs a different path or scheme.
    laser_scheme: str = os.getenv("LASER_SCHEME", "iggy+http")
    laser_uri: str | None = os.getenv("LASER_URI") or None
    laser_username: str = os.getenv("LASER_USERNAME", "iggy")
    laser_password: str | None = os.getenv("LASER_PASSWORD") or None
    laser_stream: str = os.getenv("LASER_STREAM", "overlap")
    laser_topic: str = os.getenv("LASER_TOPIC", "events")

    @property
    def laser_connection_string(self) -> str:
        if self.laser_uri:
            return self.laser_uri
        pw = self.laser_password or ""
        port = 443 if self.laser_scheme == "iggy+http" else self.laser_port
        return f"{self.laser_scheme}://{self.laser_username}:{pw}@{self.laser_host}:{port}"

    # --- Guild.ai: the coordination layer -------------------------------
    # `local` runs matchmaker/critic in-process; `cloud` posts to the deployed
    # Guild agents. Same request/response shape either way.
    guild_backend: str = _flag("GUILD_BACKEND", "local")
    guild_api_key: str | None = os.getenv("GUILD_API_KEY") or None
    guild_workspace: str | None = os.getenv("GUILD_WORKSPACE") or None
    guild_base_url: str = os.getenv("GUILD_BASE_URL", "https://api.guild.ai")

    # --- LLM (used by the local motion fallback) ------------------------
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    model: str = os.getenv("OVERLAP_MODEL", "claude-sonnet-5")

    # --- Tuning knobs the critic uses -----------------------------------
    nudge_cooldown_seconds: int = int(os.getenv("NUDGE_COOLDOWN", "25"))
    min_shared_topics: int = int(os.getenv("MIN_SHARED_TOPICS", "1"))

    @property
    def has_llm(self) -> bool:
        return bool(self.anthropic_api_key)


cfg = Config()

# --- Beeline shared enums (frozen contract, BUILD_SPEC.md §4.1) -----------
# Floor-16 zones on the map. Positions are zone-level, not coordinates.
ZONES = ["Window", "Coffee", "Stage", "Cafe"]

# Self-reported emotional state. Only `open` people participate in active
# routing (as target or recipient); the others are paused with different copy:
#   heads-down / in-flow  -> "building, do not disturb" (skip silently)
#   recharging            -> "needs a minute" (hold and retry when back to open)
STATES = ["open", "heads-down", "recharging", "in-flow"]

# One-tap post-meeting feedback that drives the learning loop.
FEEDBACK = ["clicked", "neutral", "not-for-me"]
