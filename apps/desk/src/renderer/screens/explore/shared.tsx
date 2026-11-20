import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";
import { strategyAddCommand, type KalshiError, type KalshiResult } from "@shared/kalshi";
import { LoreCard } from "../../components/LoreCard";
import { nem } from "../../lib/api";
import { ERROR_TEXT, type RulesCardContent } from "./format";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; error: KalshiError }
  | { state: "ok"; data: T; fetchedAt: string };

/** Flattens a query of a `KalshiResult` into loading / error value / data. */
export function loaded<T>(q: UseQueryResult<KalshiResult<T>>): Loaded<T> {
  if (q.data !== undefined) {
    return q.data.ok ? { state: "ok", data: q.data.data, fetchedAt: q.data.fetchedAt } : { state: "error", error: q.data.error };
  }
  if (q.error !== null) return { state: "error", error: { kind: "upstream", message: q.error.message } };
  return { state: "loading" };
}

export function KalshiErrorState({ error }: { error: KalshiError }) {
  const t = ERROR_TEXT[error.kind];
  return (
    <section className={`state state-error kalshi-error-${error.kind}`} role="alert" aria-label={t.title}>
      <h2 className="state-title">{t.title}</h2>
      <p>{t.reason}</p>
      <p className="dim">
        <code>{error.message}</code>
      </p>
    </section>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p className="dim" role="status" aria-label={`Loading ${what}`}>
      Loading {what}...
    </p>
  );
}

/** Rules card on hover/focus (LoreCard without tier colors: those are for verdicts). */
export function RulesCard({ content, label = "RULES" }: { content: RulesCardContent; label?: string }) {
  return (
    <LoreCard
      title={content.title}
      stats={content.stats}
      body={
        <>
          {content.rules.map((r) => (
            <span key={r} className="lore-text">
              {r}
            </span>
          ))}
          {content.notes.map((n) => (
            <span key={n} className="lore-text lore-note">
              {n}
            </span>
          ))}
        </>
      }
    >
      <span className="rules-chip pixel-font">{label}</span>
    </LoreCard>
  );
}

/**
 * Copies `uv run nem strategy add PORTFOLIO NAME --series S` through main (Electron
 * clipboard): the renderer keeps every permission denied, so navigator.clipboard isn't used.
 */
export function StartStrategy({ series }: { series: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const command = strategyAddCommand(series);
  return (
    <div className="start-strategy">
      <button
        type="button"
        className="start-button pixel-border"
        onClick={() => {
          void nem()
            .kalshi.copyStrategyCommand(series)
            .then(
              (r) => setStatus(r.ok ? "copied" : "failed"),
              () => setStatus("failed"),
            );
        }}
      >
        Start a strategy here
      </button>
      <code className="start-command">{command}</code>
      <span className="dim start-status" role="status">
        {status === "copied" ? "Copied. Replace PORTFOLIO and NAME, run it from the repo root." : status === "failed" ? "Couldn't copy; select the command above." : ""}
      </span>
    </div>
  );
}
