/**
 * Guild.ai agent: icebreaker (real @guildai/agents-sdk).
 *
 * A deterministic Guild `agent()`: given the matchmaker's proposal it writes the
 * OPENER, the two sentences a nervous human can walk up and say. Sentence one is
 * why to meet them (the complementary ask/offer, or a real shared interest);
 * sentence two is one concrete thing to open with, naming the warm-intro
 * connector when there is one.
 *
 * Kept deterministic (a template, not an LLM) so the opener is reproducible on
 * stage and needs no model access. The local Python path can swap in an LLM when
 * ANTHROPIC_API_KEY is set; the Guild-hosted version stays template-based on
 * purpose. No em dashes, no exclamation marks (house style, and calmer copy).
 *
 * Publish:  see guild/publish.sh
 */
import { agent, output } from "@guildai/agents-sdk";
import { z } from "zod";

const WarmIntro = z
  .object({
    connector_id: z.string(),
    connector_name: z.string(),
    via_topics: z.array(z.string()),
  })
  .nullable();

const Proposal = z.object({
  from_name: z.string(),
  to_name: z.string(),
  to_role: z.string().nullable(),
  // ask/offer of the person we are nudging, so we can phrase the complement.
  from_ask: z.string().nullable(),
  from_offer: z.string().nullable(),
  complements: z.array(z.string()),
  specific_topics: z.array(z.string()),
  connector: WarmIntro,
});

function complementPhrase(p: z.infer<typeof Proposal>): string {
  const c = p.complements[0];
  if (!c) return "";
  if (c === p.from_offer) return `they are looking for ${c}, which you offer`;
  if (c === p.from_ask) return `they offer ${c}, which you are looking for`;
  return `your ask and their offer line up on ${c}`;
}

export default agent({
  description:
    "Writes a two-sentence opener for a proposed introduction: why to meet them and how to start.",
  inputSchema: z.object({ proposal: Proposal }),
  outputSchema: z.object({ message: z.string() }),
  stateSchema: z.object({}),
  tools: {},
  start: async (input) => {
    const p = input.proposal;

    // Sentence 1: the reason. Prefer the intent match, then a real shared
    // interest, then a plain nudge.
    let why: string;
    if (p.complements.length) {
      why = `Go find ${p.to_name}, ${complementPhrase(p)}.`;
    } else if (p.specific_topics.length) {
      why = `Go find ${p.to_name}, you both work on ${p.specific_topics.slice(0, 2).join(" and ")}.`;
    } else {
      why = `Go find ${p.to_name}, there is a real reason to talk.`;
    }

    // Sentence 2: how to open. Lean on the connector if there is one.
    const topic = p.complements[0] || p.specific_topics[0] || "what they are working on";
    const how = p.connector
      ? `Open by mentioning ${p.connector.connector_name}, who already knows them, then bring up ${topic}.`
      : `Open by asking what they are doing with ${topic}.`;

    return output({ message: `${why} ${how}` });
  },
});
