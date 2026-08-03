/**
 * Guild.ai agent: icebreaker.
 *
 * Writes the OPENER. The matchmaker has already decided who; this agent turns
 * that proposal into a two-sentence nudge: why to meet them, and one concrete
 * thing to open with, naming the complementary ask/offer and the warm-intro
 * connector when there is one. No em dashes, no exclamation marks.
 *
 * This is the "icebreaker agent" the problem statement asks for by name. It
 * mirrors icebreaker_local in ../src/overlap/agents.py.
 *
 * Deploy:  guild agent save && guild agent publish
 */
import { agent } from "@guildai/agents-sdk";
import { z } from "zod";

const Party = z.object({
  name: z.string().nullable(),
  role: z.string().nullable().optional(),
  ask: z.string().nullable().optional(),
  offer: z.string().nullable().optional(),
});

const Input = z.object({
  proposal: z.object({
    to_name: z.string(),
    complements: z.array(z.string()),
    specific_topics: z.array(z.string()),
    connector: z.any().nullable(),
  }),
  me: Party,
  them: Party,
});

const Output = z.object({ opener: z.string() });

// Say the ask/offer match in the right direction, mirroring _complement_phrase.
function complementPhrase(me: z.infer<typeof Party>, complement: string): string {
  if (complement && complement === (me.offer || "")) {
    return `they are looking for ${complement}, which you offer`;
  }
  if (complement && complement === (me.ask || "")) {
    return `they offer ${complement}, which you are looking for`;
  }
  return complement ? `your ask and their offer line up on ${complement}` : "";
}

export default agent({
  name: "icebreaker",
  description: "Writes the two-sentence opener for a chosen introduction.",
  input: Input,
  output: Output,

  async run({ input, llm }) {
    const { proposal, me, them } = input;
    const complement = proposal.complements[0];
    const hook = proposal.specific_topics[0] || complement || null;
    const intent = complement ? complementPhrase(me, complement) : "no direct ask/offer match";
    const route = proposal.connector
      ? `${proposal.connector.connector_name} already knows them and can introduce you.`
      : "No mutual connection yet, so introduce yourself directly.";

    // Prefer the model; fall back to the deterministic template on any failure so
    // the opener still names the complement and the connector.
    try {
      const text = await llm.text({
        prompt:
          "Write a two-sentence nudge telling one person at a hackathon why to go " +
          "meet another, and one specific thing to open with. Name the complementary " +
          "ask/offer, and the connector if there is one. Be concrete and plain. " +
          "No em dashes, no exclamation marks.\n\n" +
          `Person: ${me.name}, ${me.role}\n` +
          `Should meet: ${them.name}, ${them.role}\n` +
          `Ask/offer match: ${intent}\n` +
          `Shared interests: ${proposal.specific_topics.join(", ")}\n` +
          `Route: ${route}`,
        maxTokens: 150,
      });
      return { opener: text.trim().replace(/—/g, ", ").replace(/ - /g, ", ") };
    } catch {
      const why = complement
        ? `Go find ${proposal.to_name}, ${complementPhrase(me, complement)}.`
        : hook
          ? `Go find ${proposal.to_name}, you both work on ${hook}.`
          : `Go find ${proposal.to_name}, there is a real reason to talk.`;
      const how = proposal.connector
        ? `Open by mentioning ${proposal.connector.connector_name}, who already knows them` +
          (hook ? `, then bring up ${hook}.` : " and can vouch for you.")
        : hook
          ? `Open by asking what they are doing with ${hook}.`
          : "Open by asking what brought them to the room today.";
      return { opener: `${why} ${how}` };
    }
  },
});
