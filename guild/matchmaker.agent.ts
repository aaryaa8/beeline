/**
 * Guild.ai agent: matchmaker (real @guildai/agents-sdk).
 *
 * A deterministic Guild `agent()` (not an llmAgent): it decides WHO to introduce
 * and nothing else. All graph traversal happens upstream in the RocketRide
 * pipeline against FalkorDB; this agent receives the already-gated candidates
 * (each carrying its complements, shared topics, affinity, and warm-intro path)
 * and picks the single best one by an intent-first score. It never writes the
 * message: that is the icebreaker's job, so the division of labour is real.
 *
 * Deterministic on purpose. A reproducible choice you can explain in one line
 * beats a model that picks differently each run, and it needs no model access.
 *
 * Publish:  see guild/publish.sh  (guild agent init -> save -> publish)
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

const Proposal = z.object({
  from_id: z.string(),
  from_name: z.string(),
  to_id: z.string(),
  to_name: z.string(),
  to_role: z.string().nullable(),
  shared_topics: z.array(z.string()),
  complements: z.array(z.string()),
  specific_topics: z.array(z.string()),
  connector: WarmIntro,
  confidence: z.number(),
});

// Mirrors GENERIC_TOPICS in ../src/overlap/agents.py. Keep the two in sync.
const GENERIC = new Set([
  "ai", "tech", "technology", "startups", "software", "coding", "llm", "llms",
]);
const specific = (topics: string[]) => topics.filter((t) => !GENERIC.has(t));

// Intent-first score: 2.0*complements + 1.0*specific_shared + affinity + (0.5 if warm).
// Complements dominate because people come to rooms with asks and offers, not
// just interests. A warm-intro path is worth half a shared interest: an intro
// you can act on beats one you cannot.
const score = (c: z.infer<typeof Candidate>) =>
  2.0 * c.complements.length +
  1.0 * specific(c.shared_topics).length +
  c.affinity +
  (c.warm_intro ? 0.5 : 0);

export default agent({
  description:
    "Picks the single best person to introduce someone to, intent-first. Decides who, not how.",
  inputSchema: z.object({
    person: z.object({
      id: z.string(),
      name: z.string(),
      role: z.string().nullable(),
      interests: z.array(z.string()),
    }).describe("The person we are finding a match for"),
    candidates: z.array(Candidate).describe(
      "Pre-gated candidates from the memory graph, each with complements/affinity/warm_intro",
    ),
  }),
  // `selected` is null when there is no open candidate with a real reason.
  outputSchema: z.object({ selected: Proposal.nullable() }),
  stateSchema: z.object({}),
  tools: {},
  start: async (input) => {
    const { person, candidates } = input;
    if (candidates.length === 0) return output({ selected: null });

    const best = [...candidates].sort((a, b) => score(b) - score(a))[0];
    const spec = specific(best.shared_topics);
    const connector = best.warm_intro;
    const confidence = Math.min(
      0.95,
      0.4 + 0.2 * best.complements.length + 0.1 * spec.length + (connector ? 0.15 : 0),
    );

    return output({
      selected: {
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
      },
    });
  },
});
