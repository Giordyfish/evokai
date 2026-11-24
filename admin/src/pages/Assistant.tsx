/**
 * The built-in assistant: its state, the requirements a recommended model meets (always shown), the models with
 * their badges and measured numbers, a try of one model on the assistant's eval suite (progress and the share of the
 * eval budget, never an amount) and the activation, which asks for a second disclaimer when the model is not
 * recommended. Its owners also unlock the assistant's application and reset it, after a preview of every item the
 * reset deletes (downloadable as JSON) and typing its slug.
 */
import { useEffect, useState } from "preact/hooks";

import { adminJson, describe } from "../api";
import {
  activateBody,
  type AssistantState,
  badge,
  canActivate,
  type Candidate,
  needsSecondDisclaimer,
  requirementLines,
  trialRunning,
} from "../assistant/choice";
import {
  deleted,
  kindText,
  type ResetPreview,
  resetExport,
  slugTyped,
} from "../assistant/reset";
import { fitBadge, fitReasons } from "../fit";
import { download } from "../format";
import { num, t, tn } from "../i18n";
import { Empty, toast } from "../magic";
import { setTitle, useConsole } from "../state";
import { Bar, Btn, Field, Hero, Panel, Tag } from "../ui";

function Numbers({ c }: { c: Candidate }) {
  const m = c.measured;
  if (!m) return <span class="s">{t("assistant.notMeasuredYet")}</span>;
  return (
    <span class="s">
      {t("assistant.numbersLine", {
        tools: num(m.toolCallsPct ?? 0),
        confirm: num(m.confirmPct ?? 0),
        redteam: num(m.redteamPct ?? 0),
        context: num(Math.round((m.contextTokens ?? 0) / 1000)),
      })}
      {" · "}
      {m.source === "try"
        ? t("assistant.measuredTry")
        : t("assistant.measuredRelease", { version: m.evokaiVersion ?? "" })}
    </span>
  );
}

/** The assistant's application: unlock it, or reset it to the shipped definition after a preview (owners). */
function SystemApp({
  state,
  onChanged,
}: {
  state: AssistantState;
  onChanged: () => Promise<void>;
}) {
  const [typed, setTyped] = useState("");
  const [preview, setPreview] = useState<ResetPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const app = state.applicationId ?? "";
  const slug = state.slug ?? "";
  const run = async (what: () => Promise<void>, fail: string) => {
    setBusy(true);
    try {
      await what();
    } catch (e) {
      toast(fail, describe(e), null);
    } finally {
      setBusy(false);
    }
  };
  const unlock = () =>
    run(async () => {
      await adminJson(app, "POST", "/v1/admin/application/unlock", {
        confirm: typed.trim(),
      });
      toast(t("reset.unlockedToast"), slug, null);
      setTyped("");
      await onChanged();
    }, t("reset.notUnlocked"));
  const loadPreview = () =>
    run(
      async () =>
        setPreview(
          await adminJson<ResetPreview>(
            app,
            "GET",
            "/v1/admin/application/reset/preview",
          ),
        ),
      t("reset.noPreview"),
    );
  const reset = () =>
    run(async () => {
      await adminJson(app, "POST", "/v1/admin/application/reset", {
        confirm: typed.trim(),
      });
      toast(t("reset.doneToast"), slug, null);
      setTyped("");
      setPreview(null);
      await onChanged();
    }, t("reset.notReset"));
  const gone = preview ? deleted(preview) : [];
  return (
    <Panel title={t("reset.title")}>
      <p class="hint">
        {state.locked ? t("reset.locked") : t("reset.unlocked")}
      </p>
      {state.updateAvailable ? (
        <p class="hint">{t("reset.updateAvailable")}</p>
      ) : null}
      {preview ? (
        <>
          <p class="hint">
            {gone.length
              ? tn("reset.previewText", gone.length)
              : t("reset.previewNothing")}
          </p>
          <div class="wx">
            <table class="tbl">
              <thead>
                <tr>
                  <th>{t("reset.kind")}</th>
                  <th>{t("reset.name")}</th>
                  <th>{t("reset.action")}</th>
                </tr>
              </thead>
              <tbody>
                {preview.items.map((i) => (
                  <tr key={`${i.kind}:${i.name}`}>
                    <td>{kindText(i.kind)}</td>
                    <td>
                      <code>{i.name}</code>
                    </td>
                    <td>
                      <Tag tone={i.action === "delete" ? "bad" : ""}>
                        {i.action === "delete"
                          ? t("reset.delete")
                          : t("reset.restore")}
                      </Tag>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {gone.length ? (
            <Btn
              kind="ghost"
              onClick={() => {
                const f = resetExport(preview);
                download(f.filename, f.text, "application/json");
              }}
            >
              {t("reset.download")}
            </Btn>
          ) : null}
        </>
      ) : null}
      <Field label={t("reset.typeSlug", { slug })}>
        <input
          class="in"
          value={typed}
          placeholder={slug}
          onInput={(e) => setTyped(e.currentTarget.value)}
        />
      </Field>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        {state.locked ? (
          <Btn
            kind="ghost"
            disabled={busy || !slugTyped(typed, slug)}
            onClick={() => void unlock()}
          >
            {t("reset.unlock")}
          </Btn>
        ) : null}
        {preview ? (
          <Btn
            kind="pri"
            disabled={busy || !slugTyped(typed, slug)}
            onClick={() => void reset()}
          >
            {t("reset.reset")}
          </Btn>
        ) : (
          <Btn kind="ghost" disabled={busy} onClick={() => void loadPreview()}>
            {t("reset.preview")}
          </Btn>
        )}
      </div>
    </Panel>
  );
}

export function Assistant() {
  setTitle(t("page.assistant"));
  const c = useConsole();
  const [state, setState] = useState<AssistantState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Candidate | null>(null);
  const [ticked, setTicked] = useState(false);
  const [busy, setBusy] = useState(false);
  const anyApp = c.apps[0]?.id ?? "";
  const system = state?.applicationId
    ? c.apps.find((a) => a.id === state.applicationId)
    : undefined;
  const owner = system?.role === "owner";

  const load = async () => {
    try {
      setState(
        await adminJson<AssistantState>(
          state?.applicationId ?? anyApp,
          "GET",
          "/v1/admin/assistant",
        ),
      );
      setError(null);
    } catch (e) {
      setError(describe(e));
    }
  };
  useEffect(() => {
    void load();
  }, [anyApp]);
  useEffect(() => {
    if (!trialRunning(state)) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [state?.trial?.status, state?.trial?.runId]);

  const activate = async () => {
    if (!picked || !state?.applicationId || !canActivate(picked, ticked))
      return;
    setBusy(true);
    try {
      await adminJson(
        state.applicationId,
        "POST",
        "/v1/admin/assistant/activate",
        activateBody(picked, ticked),
      );
      toast(t("assistant.activated"), picked.label, "sparkles");
      setPicked(null);
      setTicked(false);
      await load();
    } catch (e) {
      toast(t("assistant.notActivated"), describe(e), null);
    } finally {
      setBusy(false);
    }
  };
  const tryModel = async (cand: Candidate) => {
    if (!state?.applicationId) return;
    try {
      await adminJson(state.applicationId, "POST", "/v1/admin/assistant/try", {
        model: cand.model,
      });
      toast(t("assistant.tryStarted"), cand.label, null);
      await load();
    } catch (e) {
      toast(t("assistant.notStarted"), describe(e), null);
    }
  };

  if (error) return <Panel>{error}</Panel>;
  if (!state) return <Panel>{t("assistant.loading")}</Panel>;
  if (!state.available)
    return (
      <>
        <Hero title={t("page.assistant")} sub={t("assistant.sub")} />
        <Panel>
          <Empty k="ember">{t("assistant.unavailable")}</Empty>
        </Panel>
      </>
    );

  const trial = state.trial;
  return (
    <>
      <Hero
        title={t("page.assistant")}
        sub={t("assistant.sub")}
        actions={
          state.active ? (
            <Tag tone="ok" dot>
              {t("assistant.active", { model: state.model ?? "" })}
            </Tag>
          ) : (
            <Tag tone="warn" dot>
              {t("assistant.inactive")}
            </Tag>
          )
        }
      />
      <div class="grid">
        <Panel cls="disclaimer" title={t("assistant.disclaimerTitle")}>
          <p class="hint">{t("assistant.disclaimerText")}</p>
          <ul class="reqs">
            {requirementLines(state.requirements).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </Panel>
        {!owner ? (
          <Panel cls="disclaimer warn" title={t("assistant.needsOwnerTitle")}>
            <p class="hint">{t("assistant.needsOwner")}</p>
          </Panel>
        ) : null}
        {owner ? <SystemApp state={state} onChanged={load} /> : null}
        {trial ? (
          <Panel
            title={t("assistant.trialTitle", { model: trial.model ?? "" })}
          >
            <Bar value={trial.progressPct} />
            <p class="hint">
              {trial.status === "running"
                ? t("assistant.trialProgress", { pct: num(trial.progressPct) })
                : trial.status === "done"
                  ? t("assistant.trialDone")
                  : t("assistant.trialEnded", { status: trial.status })}
              {" · "}
              {t("assistant.trialBudget", { pct: num(trial.budgetPct) })}
            </p>
          </Panel>
        ) : null}
        <Panel cls="flat" title={t("assistant.candidates")}>
          {state.candidates.length ? (
            <div class="wx">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>{t("assistant.model")}</th>
                    <th>{t("assistant.status")}</th>
                    <th>{t("assistant.numbers")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {state.candidates.map((cand) => {
                    const b = cand.estimate
                      ? fitBadge(cand.estimate)
                      : badge(cand.status);
                    return (
                      <tr
                        key={cand.model}
                        aria-selected={picked?.model === cand.model}
                      >
                        <td>
                          <b>{cand.label}</b>
                          <div class="s">
                            <code>{cand.model}</code>
                          </div>
                        </td>
                        <td>
                          <Tag tone={b.tone} dot>
                            {b.text}
                          </Tag>
                          {cand.estimate ? (
                            <div class="s">
                              {fitReasons(cand.estimate).join(" · ")}
                            </div>
                          ) : null}
                        </td>
                        <td>
                          <Numbers c={cand} />
                        </td>
                        <td class="r" style="white-space:nowrap">
                          <span
                            title={
                              owner ? undefined : t("assistant.needsOwner")
                            }
                            style="display:inline-flex;gap:6px"
                          >
                            <Btn
                              sm
                              kind="ghost"
                              disabled={!owner || trialRunning(state)}
                              onClick={() => void tryModel(cand)}
                            >
                              {t("assistant.try")}
                            </Btn>
                            <Btn
                              sm
                              kind={cand.recommended ? "pri" : "ghost"}
                              disabled={!owner}
                              onClick={() => {
                                setPicked(cand);
                                setTicked(false);
                              }}
                            >
                              {state.model === cand.model
                                ? t("assistant.current")
                                : t("assistant.choose")}
                            </Btn>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty k="ember">{t("assistant.noCandidates")}</Empty>
          )}
        </Panel>
        {picked ? (
          <Panel
            cls={needsSecondDisclaimer(picked) ? "disclaimer warn" : ""}
            title={t("assistant.chosen", { model: picked.label })}
          >
            {needsSecondDisclaimer(picked) ? (
              <>
                <p class="hint">
                  {t(
                    picked.status === "not_measured"
                      ? "assistant.secondNotMeasured"
                      : "assistant.secondNotRecommended",
                  )}
                </p>
                <label class="tick">
                  <input
                    type="checkbox"
                    checked={ticked}
                    onChange={(e) =>
                      setTicked((e.currentTarget as HTMLInputElement).checked)
                    }
                  />{" "}
                  {t("assistant.secondTick")}
                </label>
              </>
            ) : (
              <p class="hint">{t("assistant.recommendedText")}</p>
            )}
            <div style="display:flex;gap:8px;margin-top:10px">
              <Btn
                kind="pri"
                disabled={!canActivate(picked, ticked)}
                busy={busy ? t("assistant.activating") : false}
                onClick={() => void activate()}
              >
                {t("assistant.activate")}
              </Btn>
              <Btn kind="ghost" onClick={() => setPicked(null)}>
                {t("common.cancel")}
              </Btn>
            </div>
          </Panel>
        ) : null}
      </div>
    </>
  );
}
