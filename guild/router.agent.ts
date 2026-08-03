/**
 * Guild.ai agent: router (real @guildai/agents-sdk).
 *
 * Turns an approved match into a physical path, as a deterministic Guild
 * `agent()`. The multi-hop graph traversal that finds the route lives upstream
 * in the RocketRide pipeline against FalkorDB (Guild has no graph); this agent
 * receives the resolved route (target zone plus the one connector worth naming)
 * and turns it into the human line the nudge carries: "go to the Stage, say hi
 * to Priya on the way, she knows Marcus."
 *
 * Publish:  see guild/publish.sh
 */
import { agent, output } from "@guildai/agents-sdk";
import { z } from "zod";

const Hop = z.object({
  id: z.string(),
  name: z.string(),
  zone: z.string().nullable(),
  reason: z.string(),
});

const Route = z.object({
  target_zone: z.string().nullable(),
  hops: z.array(Hop),
  connector: z
    .object({ connector_id: z.string(), connector_name: z.string() })
    .nullable(),
});

export default agent({
  description:
    "Turns a resolved route (target zone plus connector) into the human walk-this-way line.",
  inputSchema: z.object({
    to_name: z.string(),
    route: Route,
  }),
  outputSchema: z.object({
    target_zone: z.string().nullable(),
    hops: z.array(Hop),
    connector: Route.shape.connector,
    line: z.string().describe("The human route line for the nudge"),
  }),
  stateSchema: z.object({}),
  tools: {},
  start: async (input) => {
    const r = input.route;
    let line = r.target_zone ? `Go to ${r.target_zone}.` : `Go find ${input.to_name}.`;
    const names = r.hops.map((h) => h.name).filter(Boolean);
    if (names.length) {
      line += ` Say hi to ${names.join(", then ")} on the way.`;
    }
    return output({
      target_zone: r.target_zone,
      hops: r.hops,
      connector: r.connector,
      line,
    });
  },
});
