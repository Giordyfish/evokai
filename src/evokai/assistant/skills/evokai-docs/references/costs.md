# Cost governance

Where an application's usage is heading this month, alerts before a cap is reached, the cheapest model that still
passes an agent's evals, and agents that would gain from prompt caching. People see shares of the caps and costs as a
percentage of the current model's, never an amount (see the hard rule in [architecture](architecture.md)). Code:
`src/evokai/costs/` (`forecast`, `alerts`, `recommend`, `service`).

## Prices

Every model call is priced by the model that actually answered (`evokai.llm.prices`), in millionths of a dollar:

1. **The provider catalog** ([data protection](data-protection.md#the-provider-catalog)): Evokai's own list prices where they
   differ from the shared data (Bedrock's EU profiles at the Stockholm list) or where the shared data lacks the
   provider (Scaleway, OVHcloud, IONOS, STACKIT, in euros), and a deployment's own (`EVOKAI_PROVIDERS_FILE`). Prices
   in euros are converted at `EVOKAI_PRICE_EUR_USD` (default 1.2, a deployment's assumption that errs high, not a
   quote). An endpoint with a regional surcharge (Mistral's and OpenAI's EU endpoints, Azure's Data Zone and Regional
   deployments) applies it to the prices not stated for it.
2. **[genai-prices](https://github.com/pydantic/genai-prices)** (MIT, Pydantic), the price data pydantic-ai uses
   for `RunUsage`: input, output, cache read and cache write (5 minute and 1 hour) prices of most providers' current
   models, with their long-context tiers. Its bundled snapshot is used as installed, never updated over the network.
3. **The fallback**: a model nobody prices is charged like an expensive one ($3.30 in, $16.50 out per million
   tokens), never as free, logged once (`price_unknown`) and flagged in the console's model list (`rateSource:
   fallback` in `GET /v1/admin/grants`).

Self-hosted providers (Ollama, vLLM, LM Studio, a deployment's own `self_hosted` server) have no per-token charge.
Input tokens include the ones read from and written to the prompt cache; a cache price the source does not state is
the input price (never cheaper than it may be), except on Anthropic models, whose cache prices are documented
multiples of the input price everywhere (0.1x read, 1.25x write for 5 minutes, 2x for 1 hour). Guard classifiers are
priced from their catalog entry (Bedrock Guardrails and Azure Content Safety per 1,000 text units or records; OpenAI
and Mistral moderation free).

Amounts stay on the server: caps, forecasts and recommendations use them, people see shares of the caps.

## Forecast

For every cap of the application, at the end of its current window (a month, a week from its reset weekday, a day;
see [Limits](configuration.md#limits)), and for each person who used it in a window (against the subject cap they are
most likely to reach, else the application's monthly cap), from the daily usage that counts against the caps (evals
excluded). Days are the application's `timezone` days, and a cap in another currency than USD is compared in USD at
the application's `exchange_rate`:

1. **weekday factors**: each weekday's mean over the overall mean of the last 28 complete days, shrunk towards 1 (two
   pseudo-days), so quiet weekends stay quiet without one busy Monday making every Monday busy;
2. **trend**: least squares over the de-seasonalised days (a flat mean with fewer than 7 days);
3. **projection**: every day left is the line times its weekday factor (never below zero), what is left of today in
   proportion; the forecast is the window so far plus the projection;
4. **band**: the residual standard deviation times the square root of the days left gives a low and a high forecast.

`GET /v1/admin/costs/forecast` answers, for the monthly cap, `usedPct`, `forecastPct`, `lowPct`, `highPct`, `trend`
(rising, flat, falling), `daysLeft`, `resetsAt` and the window day by day (days so far as `usedPct`, days left as
`forecastPct`); `caps`, the same shares for every application cap with its `window` and `resetsAt`; the ten people
most likely to reach a cap (with its `period`), the thresholds and the alerts of the current windows.

## Alerts

`cost_alert_thresholds` (default 75, 90, 100: shares of a cap). When the forecast of a cap at the end of its window
reaches one, for the application or for a person (when the application has subject caps), an alert fires **once** per
scope, window and threshold (a unique row in `evokai_cost_alerts`, whichever instance sees it first). The window is
the alert's `period`: `2026-11` for a month from the 1st, else its first day and a letter, `2026-11-14w` (week),
`2026-11-15m` (month from the 15th), `2026-11-16d` (day); `cap` says the period:

- a log line `cost_alert` (warning) and the row (an event the console lists, with when and how it was delivered);
- the console's banner until someone acknowledges it (`POST /v1/admin/costs/alerts/{id}/ack`);
- a webhook, when `cost_alert_webhook` is set: https only, on a domain of `EVOKAI_ALERT_WEBHOOK_ALLOW`, resolving to
  public addresses only, no redirects, a 5 second timeout. The JSON body carries shares only
  (`{type: "evokai.cost_alert", application, scope, subjectKey, period, cap, threshold, forecastPct, usedPct, firedAt}`; a
  person appears as a keyed hash). It is signed: `X-Evokai-Signature: sha256=<hex>` is the HMAC-SHA256 of
  `<X-Evokai-Timestamp>.<body>` with `cost_alert_secret` (stored encrypted, never shown). Without a secret nothing is
  sent.

Evokai has no e-mail sender of its own: the webhook is how an application turns alerts into e-mails, chat messages or
pages. Forecasts are checked by the hourly housekeeping job and whenever the console opens the forecast.

## The cheapest model that passes

`POST /v1/admin/agents/{agent}/model-recommendations {tolerance?}` runs the agent's enabled eval cases (at most 20) on
its current model and on up to four models cheaper by list price that it may use: the deployment's allow-list, the
application's allowed models, its data residency and the model grants (as primary). Each case runs as in an eval
(`evokai.evals`), forcing the model (`TurnInput.model_override`, evaluation only); usage is kind `eval` tagged
`evokai:models`, within the eval budget. `GET` answers the latest comparison: per model the pass rate and its change,
**the cost on the same cases as a percentage of the current model's** (`relativeCostPct`), latency, whether it passes
(pass rate within `tolerance` points, 5 by default) and the recommendation: the cheapest model that passes.
`POST /v1/admin/agents/{agent}/switch-model {model}` saves a new version on that model (its fallbacks unchanged), with
everything a save does: allow-lists, residency, grants, evals, red team and its gate.

## Prompt cache suggestions

`GET /v1/admin/costs/cache-suggestions` lists the agents on an Anthropic model (on Bedrock or the Anthropic API) with
`prompt_cache: off`
whose stable prompt (instructions, preloaded skills, the list of skills) reaches the model's minimum cacheable prefix
(4,096 tokens on Haiku, 1,024 otherwise), with the share of an average call's input it would serve. See
[Prompt caching](architecture.md#prompt-caching).

## Settings

| Setting | Where | Default |
|---|---|---|
| `cost_alert_thresholds` | application (`PATCH /v1/admin/application`, YAML `cost_alerts: {thresholds}`, console Limits) | 75, 90, 100 |
| `cost_alert_webhook` | application (YAML `cost_alerts: {webhook}`) | none |
| `cost_alert_secret` | application (write-only; `""` clears) | none |
| `EVOKAI_ALERT_WEBHOOK_ALLOW` | environment (JSON list of domains) | `[]`: no webhook |
