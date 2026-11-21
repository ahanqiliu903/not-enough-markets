import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  liveBlock,
  reasonError,
  REASON_MAX,
  type HaltAction,
  type HaltApi,
  type HaltResult,
  type HaltState,
} from "@shared/halt";
import { nem, queryKeys } from "../lib/api";
import "./halt.css";

/**
 * DESK-021: halt and resume over SSH, off by default. Settings holds the flag; the Status
 * screen shows the controls only while it is on. Every check here is repeated in main,
 * which is what actually decides; the UI just explains and disables.
 */

const OFF: HaltState = { enabled: false, target: null, runsAs: null, portfolios: [], strategies: [], halts: [], running: false };

/** Test doubles may have no `vps` (or no `halt`) API at all: treat that as off. */
function haltApi(): HaltApi | null {
  return (nem().sources as { vps?: { halt?: HaltApi } }).vps?.halt ?? null;
}

export function useHaltState() {
  return useQuery({
    queryKey: queryKeys.halt,
    queryFn: () => haltApi()?.get() ?? Promise.resolve(OFF),
  });
}

/** Settings section: the experimental flag. */
export function HaltSettings() {
  const client = useQueryClient();
  const q = useHaltState();
  const toggle = useMutation({
    mutationFn: (on: boolean) => haltApi()?.setEnabled(on) ?? Promise.resolve(OFF),
    onSuccess: (s) => client.setQueryData(queryKeys.halt, s),
  });
  const on = q.data?.enabled ?? false;
  return (
    <section className="section" aria-label="Halt and resume">
      <h2 className="section-title">Halt and resume</h2>
      <p className="dim">
        Runs the engine&apos;s own <code>nem halt</code> / <code>nem resume</code> on the VPS above over the system{" "}
        <code>ssh</code> (same key, host key checking and remote folder as sync). Paper portfolios only, typed
        confirmation, nothing retried. When on, the controls appear on the Status screen.
      </p>
      <div className="form-row">
        <label htmlFor="halt-enabled">Enable halt/resume over SSH (experimental)</label>
        <input
          id="halt-enabled"
          type="checkbox"
          checked={on}
          disabled={q.data === undefined || toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)}
        />
        <span className="dim">{on ? "on" : "off"}</span>
      </div>
    </section>
  );
}

/** Status screen panel; renders nothing while the flag is off. */
export function HaltControls() {
  const q = useHaltState();
  const s = q.data;
  if (s?.enabled !== true) return null;
  return (
    <section className="panel-box halt-controls" aria-label="Halt controls">
      <h3>Halt / resume over SSH (experimental)</h3>
      {s.target === null ? (
        <p className="dim">Set up VPS sync in Settings first: commands run on that server.</p>
      ) : (
        <>
          <p className="dim halt-where">
            Runs on <code>{s.target}</code> as <code>{s.runsAs ?? "?"} ...</code>. Scopes, modes and halts below come
            from the last VPS sync, not the active source. Paper portfolios only: live or unknown-mode portfolios are
            refused until live trading (M8) is reviewed.
          </p>
          <ActionForm action="halt" state={s} />
          <ActionForm action="resume" state={s} />
        </>
      )}
    </section>
  );
}

function scopeLabel(scope: string): string {
  return scope === "*" ? "* (everything)" : scope;
}

function ActionForm({ action, state }: { action: HaltAction; state: HaltState }) {
  const client = useQueryClient();
  const options =
    action === "halt"
      ? ["*", ...state.portfolios.map((p) => p.name), ...state.strategies]
      : state.halts.map((h) => h.scope);
  const [scope, setScope] = useState(options[0] ?? "");
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [result, setResult] = useState<HaltResult | null>(null);
  const current = options.includes(scope) ? scope : (options[0] ?? "");
  const run = useMutation({
    mutationFn: () =>
      haltApi()?.run(action === "halt" ? { action, scope: current, reason, confirm } : { action, scope: current, confirm }) ??
      Promise.reject(new Error("Not running inside NEM Desk")),
    onSuccess: (r) => {
      setResult(r);
      client.setQueryData(queryKeys.halt, r.state);
      if (r.ok) {
        setConfirm("");
        setReason("");
        void client.invalidateQueries();
      }
    },
  });
  const id = `halt-${action}`;
  if (options.length === 0) {
    return (
      <div className="halt-form" aria-label={action === "halt" ? "Halt" : "Resume"} role="group">
        <h4>{action === "halt" ? "Halt" : "Resume"}</h4>
        <p className="dim">{action === "halt" ? "No portfolios in the VPS cache yet: sync first." : "No active halts in the last VPS sync."}</p>
      </div>
    );
  }
  const blocked = liveBlock(current, state.portfolios);
  const reasonProblem = action === "halt" && reason !== "" ? reasonError(reason) : null;
  const ready =
    blocked === null &&
    confirm === current &&
    (action === "resume" || reasonError(reason) === null) &&
    !run.isPending &&
    !state.running;
  return (
    <div className="halt-form" aria-label={action === "halt" ? "Halt" : "Resume"} role="group">
      <h4>{action === "halt" ? "Halt (stop new entries)" : "Resume (clear a halt)"}</h4>
      <div className="form-row">
        <label htmlFor={`${id}-scope`}>Scope</label>
        <select
          id={`${id}-scope`}
          value={current}
          onChange={(e) => {
            setScope(e.target.value);
            setConfirm("");
            setResult(null);
          }}
        >
          {options.map((o) => (
            <option key={o} value={o}>
              {scopeLabel(o)}
            </option>
          ))}
        </select>
      </div>
      {action === "halt" && (
        <div className="form-row">
          <label htmlFor={`${id}-reason`}>Reason</label>
          <input
            id={`${id}-reason`}
            value={reason}
            maxLength={REASON_MAX}
            size={40}
            spellCheck={false}
            placeholder="why (shown by nem status)"
            onChange={(e) => setReason(e.target.value)}
          />
          {reasonProblem !== null && <span className="down">{reasonProblem}</span>}
        </div>
      )}
      {blocked !== null ? (
        <p role="alert" className="down" data-testid={`${id}-blocked`}>
          {blocked}
        </p>
      ) : (
        <div className="form-row">
          <label htmlFor={`${id}-confirm`}>
            Type <code>{current}</code> to confirm
          </label>
          <input
            id={`${id}-confirm`}
            value={confirm}
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => setConfirm(e.target.value)}
          />
          <button type="button" className="halt-go" disabled={!ready} onClick={() => run.mutate()}>
            {run.isPending ? "Running..." : action === "halt" ? `Halt ${current}` : `Resume ${current}`}
          </button>
        </div>
      )}
      {run.isError && (
        <p role="alert" className="down">
          {String(run.error)}
        </p>
      )}
      {result !== null && <ResultView result={result} />}
    </div>
  );
}

function ResultView({ result }: { result: HaltResult }) {
  return (
    <div className="halt-result" data-testid="halt-result" data-ok={result.ok}>
      {result.ok ? (
        <p className="up">
          Done. {result.synced ? "Synced: status.json refreshed." : "The follow-up VPS sync failed; Sync now in Settings to refresh."}
        </p>
      ) : (
        <p role="alert" className="down" data-kind={result.error.kind}>
          Failed ({result.error.kind}): {result.error.message} Nothing was retried.
        </p>
      )}
      {result.command !== null && <pre className="halt-output">$ {result.command}</pre>}
      {result.output !== "" && <pre className="halt-output">{result.output}</pre>}
      {!result.ok && result.error.detail !== undefined && result.error.detail !== "" && result.error.detail !== result.output && (
        <pre className="halt-output">{result.error.detail}</pre>
      )}
    </div>
  );
}
