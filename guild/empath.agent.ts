/**
 * Guild.ai agent: empath.
 *
 * The gate and the human-in-the-loop spine. Given a proposal and the live state
 * of both people, it can approve, hold, or veto. Rules are deterministic on
 * purpose: a hold you can explain in one sentence is worth more on stage than one
 * a model felt like issuing, and it means the same input always produces the same
 * demo. Mirrors empath_local in ../src/overlap/agents.py.
 *
 *   approved -> deliver, and the only status that starts a cooldown.
 *   held     -> target needs a minute (recharging); retry when they reopen.
 *   vetoed   -> skip (heads-down/in-flow, already met, generic-only, cooldown).
 *
 * The feedback learning update is a memory write, so it stays in Python; this
 * agent owns the gate.
 *
 * Deploy:  guild agent save && guild agent publish
 */
import { agent } from "@guildai/agents-sdk";
import { z } from "zod";

const Input = z.object({
  proposal: z.object({
    from_id: z.string(),
    to_id: z.string(),
    to_name: z.string(),
    shared_topics: z.array(z.string()),
    specific_topics: z.array(z.string()),
    complements: z.array(z.string()),
    connector: z.any().nullable(),
  }),
  recipient_state: z.string().nullable(),
  recipient_name: z.string().nullable(),
  target_state: z.string().nullable(),
  target_name: z.string().nullable(),
  already_met: z.boolean(),
  target_nudge_age: z.number().nullable(),
  cooldown_seconds: z.number(),
});

const Output = z.object({
  approved: z.boolean(),
  status: z.enum(["approved", "held", "vetoed"]),
  reason: z.string(),
});

export default agent({
  name: "empath",
  description: "The emotional gate. Approves, holds, or vetoes a proposed introduction.",
  input: Input,
  output: Output,

  async run({ input }) {
    const {
      proposal, recipient_state, recipient_name, target_state, target_name,
      already_met, target_nudge_age, cooldown_seconds,
    } = input;

    // 1. The emotional gate, target first then recipient. Only `open` routes.
    for (const [state, name] of [
      [target_state, target_name || "target"],
      [recipient_state, recipient_name || "recipient"],
    ] as const) {
      if (state === "recharging") {
        return {
          approved: false, status: "held",
          reason: `${name} needs a minute (recharging). Holding until they are open again.`,
        };
      }
      if (state === "heads-down" || state === "in-flow") {
        return {
          approved: false, status: "vetoed",
          reason: `${name} is ${state} (building, do not disturb). Skipping for now.`,
        };
      }
    }

    // 2. Never repeat an introduction.
    if (already_met) {
      return {
        approved: false, status: "vetoed",
        reason: "They have already met. A repeat intro reads as noise.",
      };
    }

    // 3. Reject a match whose only overlap is generic.
    if (proposal.complements.length === 0 && proposal.specific_topics.length === 0) {
      const generic = proposal.shared_topics.slice(0, 3).join(", ") || "nothing";
      return {
        approved: false, status: "vetoed",
        reason: `Only shared interest is ${generic}, too broad to be a real reason.`,
      };
    }

    // 4. Cooldown. Only delivered (approved) nudges start one upstream, so a hold
    // or veto never cascades into vetoing the whole room.
    if (target_nudge_age !== null && target_nudge_age < cooldown_seconds) {
      return {
        approved: false, status: "vetoed",
        reason:
          `${proposal.to_name} was introduced ${Math.floor(target_nudge_age)}s ago. ` +
          `Cooling down for ${cooldown_seconds}s so one popular person does not absorb every intro.`,
      };
    }

    const basis = proposal.complements.length > 0
      ? `your ask/offer match on ${proposal.complements[0]}`
      : `a real shared interest in ${proposal.specific_topics[0]}`;
    const tail = proposal.connector
      ? ` ${proposal.connector.connector_name} can vouch for you.`
      : "";
    return { approved: true, status: "approved", reason: `Both open, not met, ${basis}.${tail}` };
  },
});
