# Compliance packs

Evokai writes the documents a DPO and a reviewer ask for about an AI system from what it actually runs: the stored
configuration of an application and its recent activity, never personal data. Three packs, in English or Italian:

| Pack | Regulation or standard | What is in it |
|---|---|---|
| `ai-act` | Regulation (EU) 2024/1689 | the system and its intended purpose; the risk classification with its reasoning and the obligations that follow; technical documentation in the spirit of Annex IV (agents and versions, models and providers with their regions, tools and which change data, human oversight, red team and eval scores with trends, security controls with OWASP LLM Top 10 and NIST AI 600-1, logging and retention); the log of significant changes; what the organisation completes |
| `gdpr` | Regulation (EU) 2016/679 | the record of processing for the AI processing (Art. 30), sub-processors and recipients with the data protection profile and every external processor's residency, jurisdiction and terms as the provider catalog knows them, transfers, retention per type of data, a DPIA template (WP248 screening, systematic description, risks with the measures in force, residual risks and sign-off left to complete), data subject rights and how each is fulfilled, the DPA annex of technical and organisational measures (Art. 28, 32) |
| `controls` | ISO/IEC 27001:2022 Annex A, SOC 2 Trust Services Criteria, ISO/IEC 42001:2023 Annex A | which controls Evokai's features support, with evidence pointers (settings, tables, endpoints, tests) and a status: supported, available but not configured, partly supported, or the organisation's |

Every pack opens with the same disclaimer: it supports the controller's own assessment and is not legal advice. The
control mapping says plainly that it is support, not a certification: ISO/IEC 27001 certifies an organisation's ISMS,
ISO/IEC 42001 its AI management system, and SOC 2 reports on a service organisation's controls, never a piece of
software.

ISO/IEC 42001:2023 Annex A lists 38 controls (A.2 to A.10). The pack gives each one a short title in our own words
(the standard's text is copyrighted) and an honest status: most of them (the AI policy, objectives, people,
societal impact, reporting to outside parties, data acquisition and quality, customers) are the organisation's own
processes and are listed as such so the gap is visible. Evokai gives evidence where it has some: event logs (A.6.2.8),
technical documentation through the AI Act pack (A.6.2.7), verification through evals, the red team and policy tests
(A.6.2.4), the resources inventory (A.4.2, A.4.4), the DPIA template for impact assessments (A.5.2 to A.5.4, partly),
policy versions for the review of policies (A.2.4, partly), responsible use through the system policy (A.9.2:
supported once a system policy is saved) and suppliers through the provider catalog and the data protection profile
(A.10.3: supported once a profile is set).

## Where each statement comes from

The packs are pure functions of a **facts** document (`evokai.compliance.facts`): agents with their active versions,
channels, models, MCP servers, knowledge bases, code execution and each control's mode; MCP servers with their tools
and whether each changes data (`readOnlyHint`, `destructiveHint`); per-user connections (Microsoft 365, Google
Workspace: whether granted, the tool families, how many people connected) and channels (e-mail, Teams, Slack: kind,
name, agent, whether on); skills; knowledge bases with their retention and
access groups; workflows with their approval steps; grants; the application's settings (storage, retention,
residency, tracing content, memory, red team gate); the deployment's region, embeddings, guardrail, voice, code
sandbox, OpenTelemetry, shared state, single sign-on; red team and eval scores of the last runs; activity counts of
the last 30 days (traces by kind, conversations, security events by control and action, code runs); and the audit
log's changes to the system. Nothing that names a person is read: no subject, message, title, e-mail or audit actor;
the audit log contributes the action, its target and the kind of actor, and subject requests are left out.

Every block, list item and table row carries `sources`: keys of a registry (`evokai.compliance.texts.SOURCES`) that
name the setting, table or endpoint behind it, such as `app.residency` ("Application setting residency
(evokai_applications.residency)") or `mcp.tools`. The renderers number them as footnotes in order of first use. Parts
only the controller can state are flagged `human` ("to be completed"), gaps the configuration shows are flagged `warn`
(logs kept less than six months for a high-risk system, a model outside the EU geography, no red team gate).

## The controller's statements

Only the controller can say what the system is for. The **profile** (`PUT /v1/admin/compliance/profile`, owner; the
Compliance page) holds: the system's name, description and intended purpose, the controller and processor
organisations, the AI Act questionnaire (role, Article 5 practices, Annex III areas, Annex I safety component,
profiling, Article 6(3) conditions, emotion recognition, biometric categorisation, deep fakes, public-interest text,
whether the AI disclosure is shown) and the GDPR statements (purposes, data subjects and categories that replace or
extend the inferred ones, special categories, legal basis, recipients, transfers, the DPIA screening answers, DPO).
Free text with a personal identifier or a secret is refused (400 `personal_data`), so a pack never carries one. Every
save is in the audit log (`compliance.profile`).

## Classification

`evokai.compliance.classify`, in the order a reviewer applies the Regulation:

1. any **Article 5** practice: prohibited, and the only obligation is to stop;
2. an **Annex I** safety component (Art. 6(1)): high-risk;
3. an **Annex III** area: high-risk when the system profiles natural persons or no Article 6(3) condition holds;
   otherwise not high-risk with the assessment documented (Art. 6(4)) and registered (Art. 49(2));
4. otherwise **transparency obligations** when the system interacts with natural persons (an agent serves browser
   sessions or an e-mail, Teams or Slack channel, inferred from the configuration) or recognises emotions, categorises biometrically or makes deep fakes,
   else **minimal risk**.

Whatever the level: Article 50(1) disclosure when people talk to the agents (flagged when the questionnaire does not
confirm the notice is shown), Article 50(2) marking for the synthetic content the configuration produces (text
always, audio with server voice, images with `code_run` charts), 50(3) and 50(4) where answered, Article 4 AI
literacy, and the general-purpose model providers' own obligations (Art. 53, 55). A high-risk system gets Articles 9
to 15, 17, 26, 27, 43, 49, 72 and 73, each with what Evokai provides towards it.

## API

All under `/v1/admin/compliance` with an admin key or a console login (reads: viewer; snapshots: operator; profile:
owner). `lang` is `en` or `it` (default: the application's locale); `format` is `json` (default), `md` or `html`.

| Method | Path | What |
|---|---|---|
| GET | `/{pack}` | a pack generated now (`ai-act`, `gdpr`, `controls`) |
| GET, PUT | `/profile` | the controller's statements |
| GET | `/snapshots` | snapshots, newest first (without their facts) |
| POST | `/snapshots` | take a snapshot now (`{label?}`, no personal data); audited as `compliance.snapshot` |
| GET | `/snapshots/{id}/{pack}` | a pack as it was at the snapshot |
| GET | `/snapshots/diff?from=&to=` | what changed: added, removed and changed statements per pack, the classification before and after, the facts that differ |

The HTML is one self-contained document (inline styles, no script, no external resource) with a print stylesheet (A4,
rows that do not split, footnotes on their own page), served with its own Content Security Policy. The Markdown uses
footnotes (`[^n]`). The JSON is the pack itself: `sections` of `blocks` (`p`, `list`, `table`, `fields`) with
`items`, every one with `sources`, and `footnotes`.

The Python SDK has `admin.compliance` (`profile`, `put_profile`, `pack`, `snapshots`, `snapshot`, `snapshot_pack`,
`diff`); the TypeScript client has `admin.compliance` with the same calls (`packText` and `snapshotText` for Markdown
and HTML).

## Snapshots

A snapshot stores the facts of the moment (with the profile), so a reviewer reads the packs as they were on that date
and in either language later. Snapshots are kept in the console settings table under `cmp.s:<application>:<id>` (the
profile under `cmp.p:<application>`), need no migration, and are never changed. Ids sort by time. The diff renders
both snapshots in the requested language and compares statements by their ids, then the facts by their paths (activity
counts left out).

## Console

The **Compliance** page of an application shows the pack chosen (AI Act, GDPR, and ISO 27001, SOC 2 and ISO 42001) in the console's
language with its footnotes and flags, with Print (the HTML in a new tab) and downloads as HTML, Markdown and JSON; the
profile form; the snapshots, a snapshot's HTML, and the comparison of two of them.

## Limits

- The packs describe Evokai's part of the system. The application's own interface (does it show the AI disclosure?),
  its MCP servers, its identity provider and the deployment's infrastructure (backups, encryption at rest, network)
  are stated by the controller or flagged to complete.
- Personal data is kept out by construction (the facts read no person) and by test (`tests/test_compliance.py` scans
  every format of every pack, in both languages, with the guard's identifier detector after a conversation full of
  identifiers). Free text the controller writes is checked on save with the same detector, which recognises formats
  (tax codes, IBANs, e-mails, phones, cards), not names.
- Legal dates are those of the Regulation as adopted; the pack asks to check later amendments.
