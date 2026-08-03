/**
 * Guild.ai agent: matchmaker.
 *
 * Decides WHO only. All graph traversal happens in the RocketRide pipeline
 * against FalkorDB; this agent receives the gated candidates (each already
 * carrying its complements, shared topics, affinity, and warm-intro path) and
 * picks the single best introduction. It does NOT write the message: that is the
 * icebreaker's job, so the division of labour between specialists is real.
 *
 * Mirrors matchmaker_local in ../src/overlap/agents.py exactly. Nothing crosses
 * the language boundary except JSON.
 *
 * Deploy:  guild agent save && guild agent publish
 */
import { agent } from "@guildai/agents-sdk";
import { z } from "zod";

const WarmIntro = z
  .object({
    connector_id: z.string(),
    connector_name: z.string(),
    via_topics: z.array(z.string()),
  })
  .nullable();

const Candidate = z.object({
  id: z.string(),
  name: z.string(),
  role: z.string().nullable(),
  zone: z.string().nullable(),
  state: z.string().nullable(),
  shared_topics: z.array(z.string()),
  complements: z.array(z.string()),
  overlap: z.number(),
  affinity: z.number(),
  warm_intro: WarmIntro,
});

const Input = z.object({
  person: z.object({
    id: z.string(),
    name: z.string(),
    role: z.string().nullable(),
    interests: z.array(z.string()),
  }),
  candidates: z.array(Candidate),
});

const Output = z.object({
  from_id: z.string(),
  from_name: z.string(),
  to_id: z.string(),
  to_name: z.string(),
  to_role: z.string().nullable(),
  shared_topics: z.array(z.string()),
  complements: z.array(z.string()),
  specific_topics: z.array(z.string()),
  connector: z.any().nullable(),
  confidence: z.number(),
});

// Mirrors GENERIC_TOPICS in ../src/overlap/agents.py. Keep the two in sync.
const GENERIC = new Set([
  "ai", "tech", "technology", "startups", "software", "coding", "llm", "llms",
]);

const specific = (topics: string[]) => topics.filter((t) => !GENERIC.has(t));

// Intent-first score: 2.0*complements + 1.0*specific + affinity + (0.5 if warm).
const score = (c: z.infer<typeof Candidate>) =>
  2.0 * c.complements.length +
  1.0 * specific(c.shared_topics).length +
  c.affinity +
  (c.warm_intro ? 0.5 : 0);

export default agent({
  name: "matchmaker",
  description: "Picks the single best person to introduce. Decides who, not how.",
  input: Input,
  output: Output,

  async run({ input }) {
    const { person, candidates } = input;
    if (candidates.length === 0) return null;

    const best = [...candidates].sort((a, b) => score(b) - score(a))[0];
    const spec = specific(best.shared_topics);
    const connector = best.warm_intro;
    const confidence = Math.min(
      0.95,
      0.4 + 0.2 * best.complements.length + 0.1 * spec.length + (connector ? 0.15 : 0),
    );

    return {
      from_id: person.id,
      from_name: person.name,
      to_id: best.id,
      to_name: best.name,
      to_role: best.role,
      shared_topics: best.shared_topics,
      complements: best.complements,
      specific_topics: spec,
      connector,
      confidence: Math.round(confidence * 100) / 100,
    };
  },
});
