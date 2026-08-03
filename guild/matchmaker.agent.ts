/**
 * Guild.ai agent: matchmaker.
 *
 * Deliberately thin. All graph traversal happens in the RocketRide pipeline
 * against FalkorDB; this agent receives the candidates and the warm-intro path
 * and decides which single introduction is worth making, plus how to word it.
 *
 * Keeping it thin is the point: the Python side owns the stack, and Guild owns
 * the division of labour between specialists. Nothing crosses the language
 * boundary except JSON.
 *
 * Deploy:  guild agent save && guild agent publish
 */
import { agent } from "@guildai/agents-sdk";
import { z } from "zod";

const Candidate = z.object({
  id: z.string(),
  name: z.string(),
  role: z.string().nullable(),
  shared_topics: z.array(z.string()),
  overlap: z.number(),
  location: z.string().nullable(),
});

const Input = z.object({
  person: z.object({
    id: z.string(),
    name: z.string(),
    role: z.string().nullable(),
    interests: z.array(z.string()),
  }),
  candidates: z.array(Candidate),
  warm_intro: z
    .object({
      connector_id: z.string(),
      connector_name: z.string(),
      via_topics: z.array(z.string()),
    })
    .nullable(),
});

const Output = z.object({
  from_id: z.string(),
  from_name: z.string(),
  to_id: z.string(),
  to_name: z.string(),
  to_role: z.string().nullable(),
  shared_topics: z.array(z.string()),
  specific_topics: z.array(z.string()),
  connector: z.any().nullable(),
  confidence: z.number(),
  message: z.string(),
});

// Mirrors GENERIC_TOPICS in ../src/overlap/agents.py. Keep the two in sync.
const GENERIC = new Set([
  "ai", "tech", "technology", "startups", "software", "coding", "llm", "llms",
]);

export default agent({
  name: "matchmaker",
  description: "Proposes one introduction, justified by a path found in the memory graph.",
  input: Input,
  output: Output,

  async run({ input, llm }) {
    const { person, candidates, warm_intro } = input;
    if (candidates.length === 0) return null;

    const best = candidates[0];
    const specific = best.shared_topics.filter((t) => !GENERIC.has(t));
    const confidence = Math.min(
      0.95,
      0.35 + 0.2 * specific.length + (warm_intro ? 0.2 : 0),
    );

    const route = warm_intro
      ? `${warm_intro.connector_name} knows them and can introduce you.`
      : "No mutual connection yet, so introduce yourself directly.";

    const message = await llm.text({
      prompt:
        "Write a two-sentence nudge telling one person at a hackathon why to go " +
        "meet another, and one specific thing to open with. Be concrete and plain. " +
        "No em dashes, no exclamation marks.\n\n" +
        `Person: ${person.name}, ${person.role}, interested in ${person.interests.join(", ")}\n` +
        `Should meet: ${best.name}, ${best.role}\n` +
        `Shared interests: ${best.shared_topics.join(", ")}\n` +
        `Route: ${route}`,
      maxTokens: 150,
    });

    return {
      from_id: person.id,
      from_name: person.name,
      to_id: best.id,
      to_name: best.name,
      to_role: best.role,
      shared_topics: best.shared_topics,
      specific_topics: specific,
      connector: warm_intro,
      confidence: Math.round(confidence * 100) / 100,
      message: message.trim(),
    };
  },
});
