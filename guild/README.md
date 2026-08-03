# Beeline agents on Guild.ai

Four specialist agents, the coordination layer of Beeline. The problem statement
names **matchmaker** and **icebreaker** for this idea; **empath** and **router**
are our depth.

All four are real `@guildai/agents-sdk` agents and are **type-checked against the
live SDK** (a full scaffold + bundle build was verified to produce the publishable
`agent.js.gz`).

## The four agents

| File | Kind | Decides | I/O |
|---|---|---|---|
| `matchmaker.agent.ts` | deterministic `agent()` | who to introduce | in: `{person, candidates[]}` -> out: `{selected: proposal \| null}` |
| `icebreaker.agent.ts` | deterministic `agent()` | the opener to say | in: `{proposal}` -> out: `{message}` |
| `empath.agent.ts` | deterministic `agent()` | approve / hold / veto | in: `{proposal, target_state, recipient_state, already_met, target_nudge_age_seconds, cooldown_seconds, ...}` -> out: `{approved, status, reason}` |
| `router.agent.ts` | deterministic `agent()` | the walk-this-way line | in: `{to_name, route}` -> out: `{target_zone, hops, connector, line}` |

**Why deterministic (`agent()`, not `llmAgent()`):** the SDK's `agent()` primitive
takes a `start(input)` that returns `output(...)` with no model call. Our
deciders are deterministic on purpose (reproducible on stage, explainable in one
line, no model dependency), so they map onto `agent()` exactly. The icebreaker is
template-based here; the local Python path can swap in an LLM when
`ANTHROPIC_API_KEY` is set. Any of these could be upgraded to `llmAgent()` later
without changing its place in the pipeline.

**Graph access:** none of these touch FalkorDB. The multi-hop traversals (gated
candidates, warm-intro connector, the route) run upstream in the RocketRide
pipeline against FalkorDB, and the results are passed to each agent as input.
That keeps the agents pure functions over data, which is why they publish cleanly.

## Publish

```bash
bash guild/publish.sh --dry   # scaffold + build only, prove it compiles
bash guild/publish.sh         # build + save + publish all four to your account
```

The script scaffolds a real agent directory per agent under `guild/.build/`
(gitignored), drops in the code, builds the bundle, and runs
`guild agent save` + `guild agent publish`. Requires the `guild` CLI
authenticated (`guild auth status`).

## Wiring the app to the published agents

The app calls these through `src/overlap/agents.py`. With `GUILD_BACKEND=local`
(the demo default) the identical logic runs in-process. With `GUILD_BACKEND=guild`
the app invokes the published agents; wire `_call_guild()` in `agents.py` to the
Guild invocation endpoint once the agents are published and you have their ids.
The input/output shapes above are the contract, and they mirror the local Python
functions one to one, so the swap is a backend change, not a logic change.
