/**
 * Guild.ai agent: empath (real @guildai/agents-sdk).
 *
 * The gate and the human-in-the-loop spine, as a deterministic Guild `agent()`.
 * Given a proposal and the live state both people self-reported, plus whether
 * they have met and how recently the target was introduced, it returns a verdict
 * the motion layer records on the NUDGED edge:
 *
 *   approved -> deliver, and the only status that starts a cooldown.
 *   held     -> target needs a minute (recharging); retry when they reopen.
 *   vetoed   -> skip (heads-down/in-flow, already met, generic-only, cooldown).
 *
 * The learning half of the empath (updating affinity from feedback) stays in the
 * app, because it writes to FalkorDB and the affinity math must be byte-identical
 * across backends. This agent owns the gate only. Rules are deterministic on
 * purpose: a veto you can explain in one sentence is worth more than one a model
 * felt like issuing, and it makes the same input produce the same demo.
 *
 * Publish:  see guild/publish.sh
 */
import { agent, output } from "@guildai/agents-sdk";
import { z } from "zod";

const GENERIC = new Set([
  "ai", "tech", "technology", "startups", "software", "coding", "llm", "llms",
]);
const specific = (topics: string[]) => topics.filter((t) => !GENERIC.has(t));

const Verdict = z.object({
  approved: z.boolean(),
  status: z.enum(["approved", "held", "vetoed"]),
  reason: z.string(),
});

export default agent({
  description:
    "Approves, holds, or vetoes a proposed introduction based on emotional state and quality.",
  inputSchema: z.object({
    proposal: z.object({
      to_name: z.string(),
      complements: z.array(z.string()),
      specific_topics: z.array(z.string()),
      shared_topics: z.array(z.string()),
      connector: z
        .object({ connector_name: z.string() })
        .nullable(),
    }),
    // live context the pipeline reads from the graph and passes in:
    target_state: z.string().describe("open | heads-down | recharging | in-flow"),
    recipient_state: z.string(),
    target_name: z.string(),
    recipient_name: z.string(),
    already_met: z.boolean(),
    target_nudge_age_seconds: z.number().nullable().describe("seconds since target last delivered nudge, or null"),
    cooldown_seconds: z.number(),
  }),
  outputSchema: Verdict,
  stateSchema: z.object({}),
  tools: {},
  start: async (input) => {
    const p = input.proposal;

    // 1. The emotional gate. Only `open` people take part in active routing, as
    // either the target of an intro or the recipient of a nudge.
    for (const [state, name] of [
      [input.target_state, input.target_name] as const,
      [input.recipient_state, input.recipient_name] as const,
    ]) {
      if (state === "recharging") {
        return output({
          approved: false,
          status: "held" as const,
          reason: `${name} needs a minute (recharging). Holding until they are open again.`,
        });
      }
      if (state === "heads-down" || state === "in-flow") {
        return output({
          approved: false,
          status: "vetoed" as const,
          reason: `${name} is ${state} (building, do not disturb). Skipping for now.`,
        });
      }
    }

    // 2. Never repeat an introduction.
    if (input.already_met) {
      return output({
        approved: false,
        status: "vetoed" as const,
        reason: "They have already met. A repeat intro reads as noise.",
      });
    }

    // 3. Reject a match whose only overlap is generic.
    const spec = p.specific_topics.length ? p.specific_topics : specific(p.shared_topics);
    if (p.complements.length === 0 && spec.length === 0) {
      const generic = p.shared_topics.slice(0, 3).join(", ") || "nothing";
      return output({
        approved: false,
        status: "vetoed" as const,
        reason: `Only shared interest is ${generic}, too broad to be a real reason.`,
      });
    }

    // 4. Cooldown. Only delivered nudges start one, so a hold or veto never
    // cascades (the pipeline passes an age computed from approved edges only).
    const age = input.target_nudge_age_seconds;
    if (age !== null && age < input.cooldown_seconds) {
      return output({
        approved: false,
        status: "vetoed" as const,
        reason:
          `${p.to_name} was introduced ${Math.floor(age)}s ago. Cooling down for ` +
          `${input.cooldown_seconds}s so one popular person does not absorb every intro.`,
      });
    }

    // Approved. Say why in one line, grounded in the actual overlap.
    const basis = p.complements.length
      ? `your ask/offer match on ${p.complements[0]}`
      : `a real shared interest in ${spec[0]}`;
    const tail = p.connector ? ` ${p.connector.connector_name} can vouch for you.` : "";
    return output({
      approved: true,
      status: "approved" as const,
      reason: `Both open, not met, ${basis}.${tail}`,
    });
  },
});
