/**
 * Guild.ai agent: router.
 *
 * Turns an approved match into a physical path. The multi-hop graph traversal
 * lives in the RocketRide pipeline against FalkorDB (Guild has no graph); this
 * agent receives the ordered hops and folds in the on-the-way connector who can
 * vouch, producing the shape the delivery renders alongside the icebreaker's
 * opener. Mirrors router_local in ../src/overlap/agents.py.
 *
 * Deploy:  guild agent save && guild agent publish
 */
import { agent } from "@guildai/agents-sdk";
import { z } from "zod";

const Hop = z.object({
  id: z.string(),
  name: z.string(),
  zone: z.string().nullable(),
  reason: z.string(),
});

const Input = z.object({
  route: z.object({
    target_zone: z.string().nullable(),
    hops: z.array(Hop),
  }),
  connector: z.any().nullable(),
});

const Output = z.object({
  target_zone: z.string().nullable(),
  hops: z.array(Hop),
  connector: z.any().nullable(),
});

export default agent({
  name: "router",
  description: "Folds the ordered path and the vouch connector into the delivery.",
  input: Input,
  output: Output,

  async run({ input }) {
    const { route, connector } = input;

    // Prefer the warm-intro connector the matchmaker found; otherwise the first
    // "knows <target>" hop on the path is the one who can vouch.
    let vouch = connector;
    if (!vouch) {
      const hop = route.hops.find((h) => h.reason.startsWith("knows "));
      if (hop) vouch = { connector_id: hop.id, connector_name: hop.name };
    }

    return { target_zone: route.target_zone, hops: route.hops, connector: vouch };
  },
});
