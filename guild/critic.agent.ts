/**
 * Guild.ai agent: critic.
 *
 * The counterweight. The matchmaker is rewarded for proposing; this one is
 * rewarded for refusing. Every rule here exists because the matchmaker would
 * otherwise break it.
 *
 * Rules are deterministic on purpose. A veto you can explain in one sentence is
 * worth more on stage than a veto a model felt like issuing, and it means the
 * same input always produces the same demo.
 *
 * Deploy:  guild agent save && guild agent publish
 */
import { agent } from "@guildai/agents-sdk";
import { z } from "zod";

const Input = z.object({
  proposal: z.object({
    from_id: z.string(),
    from_name: z.string(),
    to_id: z.string(),
    to_name: z.string(),
    shared_topics: z.array(z.string()),
    specific_topics: z.array(z.string()),
    connector: z.any().nullable(),
    confidence: z.number(),
  }),
  already_met: z.boolean(),
  target_nudge_age: z.number().nullable(),
  cooldown_seconds: z.number(),
});

const Output = z.object({
  approved: z.boolean(),
  reason: z.string(),
});

export default agent({
  name: "critic",
  description: "Approves or vetoes a proposed introduction. Holds the rules the matchmaker ignores.",
  input: Input,
  output: Output,

  async run({ input }) {
    const { proposal, already_met, target_nudge_age, cooldown_seconds } = input;

    if (already_met) {
      return {
        approved: false,
        reason: "They have already met. A repeat intro reads as noise.",
      };
    }

    // Only delivered introductions start a cooldown. Counting vetoes here would
    // let a single refusal cascade into vetoing the entire room.
    if (target_nudge_age !== null && target_nudge_age < cooldown_seconds) {
      return {
        approved: false,
        reason:
          `${proposal.to_name} was nudged ${Math.floor(target_nudge_age)}s ago. ` +
          `Cooling down for ${cooldown_seconds}s so one popular person does not ` +
          `absorb every introduction.`,
      };
    }

    if (proposal.specific_topics.length === 0) {
      const generic = proposal.shared_topics.slice(0, 3).join(", ") || "nothing";
      return {
        approved: false,
        reason:
          `Only shared interest is ${generic}, which is too broad to be a real ` +
          `reason at an AI hackathon.`,
      };
    }

    if (proposal.confidence < 0.5) {
      return {
        approved: false,
        reason: `Confidence ${proposal.confidence} below threshold.`,
      };
    }

    const route = proposal.connector
      ? ` ${proposal.connector.connector_name} can make the introduction.`
      : "";

    return {
      approved: true,
      reason:
        `Genuine overlap on ${proposal.specific_topics.slice(0, 2).join(", ")}, ` +
        `and they have not met.${route}`,
    };
  },
});
