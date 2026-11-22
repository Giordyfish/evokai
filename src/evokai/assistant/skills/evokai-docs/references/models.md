# Model suitability

Before anything is measured, Evokai estimates how well a model suits an agent. The estimate is shown with its reasons
and always labelled as an estimate: on the console's Assistant page for each candidate model of the
[console assistant](assistant.md), and in the agent editor next to the primary model. A measurement, when there is
one, replaces it. It never shows an amount.

`GET /v1/admin/agents/{agent}/suitability?model=<id>` (any console role) returns both steps:

```json
{
  "complexity": {"tier": "high", "factors": {"tools": 42, "promptTokens": 600, "skills": 1,
                 "confirmTools": 16, "languages": 2, "untrusted": 1}},
  "estimate": {"verdict": "recommended", "basis": "estimate",
               "reasons": ["context_ok", "tools_ok", "streaming_ok", "tau2_ok"],
               "facts": {"contextTokens": 200000, "needContext": 128000, "tau2Pass1": 85.3,
                         "tau2AsOf": "2026-05-05", "needTau2": 75.0, "tau2Source": "https://github.com/..."}}
}
```

## Step 1: how demanding the agent is

The complexity comes from the agent's saved definition (`evokai.llm.suitability.agent_complexity`). Each factor
scores 0, 1 or 2 points; the thresholds are constants in that module.

| Factor | 1 point from | 2 points from | Counted from |
| --- | --- | --- | --- |
| Tools | 6 | 21 | MCP tools as their servers last listed them, enabled client tools, built-ins (knowledge search, code, capabilities, subagents, handoffs) |
| Prompt tokens | 1,500 | 4,000 | the instructions (about 4 characters per token) |
| Skills | 1 | 3 | the skills it may load |
| Tools that ask for confirmation | 1 | 5 | MCP tools that are not read only |
| Languages | 2 | 4 | the languages it answers in (a system application: the console's two) |
| Untrusted inputs | 1 | 2 | MCP outputs, knowledge, outside capabilities, page text: each a way for hidden instructions to reach the model |

Three points or more is **medium**, six or more **high**, less **low**. The built-in assistant is high: dozens of
admin tools, sixteen of which change something, a skill, two languages and MCP outputs it must not obey.

## Step 2: what the model is known to do

The estimate uses only facts with a source (`src/evokai/llm/model_profiles.yaml`, each with its source and date):

- **Context window**: from Evokai's vendored models.dev snapshot (`context_windows.json`, with its fetch date) or the
  deployment's `EVOKAI_CONTEXT_WINDOWS`. The 128k default used for unknown models elsewhere is not a fact here.
- **Tool calling**: confirmed by a published τ²-bench result (the benchmark runs every task through tool calls).
- **Streaming**: Evokai streams every chat model of Bedrock, Anthropic, OpenAI (and Azure), Google and Mistral.
- **τ²-bench**: pass^1 per domain (airline, retail, telecom) as published in the benchmark's own repository
  ([sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench), the leaderboard submissions, read at
  a pinned commit). The estimate uses their mean, the figure [taubench.com](https://taubench.com/) shows. Only
  submissions with all three domains are included; newer submissions that publish a single domain are not.

| Tier | Needs |
| --- | --- |
| low | a context window of 32k tokens; tool calling when the agent has tools |
| medium | 64k, tool calling, streaming; a τ²-bench mean of at least 50 when one is published |
| high | 128k, tool calling, streaming and a τ²-bench mean of at least 75 |

The verdict:

- **Recommended (estimate)**: every need is met by a sourced fact.
- **Not recommended (estimate)**: a sourced fact misses a need (a window too small, a τ²-bench mean below the bar).
- **To verify**: a need has no sourced fact. Unknown is never recommended: run the evals.
- **Recommended (measured)** / **Not recommended (measured)**: the assistant's measured numbers (its release
  `models.yaml` or an owner's try) override the estimate.

### Why these thresholds

- **128k tokens for a complex agent**: the window the console assistant's measured requirements already ask for; a
  long conversation with dozens of tool schemas, a skill and tool results fills a smaller one before compaction can
  help.
- **τ²-bench 75 for a complex agent**: τ²-bench measures what a complex agent does, multi-turn conversations in which
  the model must follow a policy and call the right tools with the right arguments. A mean pass^1 of 75 means the model
  completes at least three tasks in four on its first try. In the submissions read on 2026-11-22 the frontier models
  are above it (Claude Opus 4.5 85.3, GPT-5.2 84.8, DeepSeek-V3.2 80.4, GPT-5 80.0, Claude Sonnet 4.5 76.4) and the
  older or smaller models well below (o4-mini 56.9, GPT-4.1 54.7, GPT-4.1-mini 53.0).
- **τ²-bench 50 as a floor for a medium agent**: below it a model fails more than half of such tasks.

Benchmarks move and their settings differ (the reasoning effort and the benchmark version are recorded with each
result); the estimate is a starting point to pick models to try, not a substitute for an agent's own evals
([evals](evals.md)).

### Keeping the profiles current

Add a model only with a figure read on its own page: the benchmark's repository or leaderboard, or the provider's
documentation, with the page's address as `source` and the date the figure refers to as `as_of`. Leave out what
cannot be read; the estimate works with gaps and says "to verify".
