import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { SHEETS_INTERVALS, type SheetsError, type SheetsState, type SheetsSyncInfo } from "@shared/sheets";
import { nem, queryKeys } from "../../lib/api";
import "./vps.css";

/**
 * DESK-012: a Google Sheet the engine's `sheets` reporter writes, read with no Google
 * account: the sheet is shared "Anyone with the link: Viewer" and main fetches each tab as
 * CSV. The link is stored by main in userData; this screen only ever gets a masked ID.
 */

interface Form {
  sheet: string;
  portfolios: string;
  interval: string;
}

function toForm(s: SheetsState): Form {
  return {
    sheet: "",
    portfolios: s.portfolios.join(", "),
    interval: s.intervalMinutes === null ? "" : String(s.intervalMinutes),
  };
}

function ErrorLine({ error }: { error: SheetsError }) {
  return (
    <div role="alert" className="vps-error" data-kind={error.kind}>
      <span className="down">{error.message}</span>
    </div>
  );
}

function LastSync({ last }: { last: SheetsSyncInfo }) {
  return (
    <div className="vps-last" data-testid="sheets-last">
      <p className={last.ok ? "up" : "dim"}>
        Last sync {last.at}: {last.ok ? "ok" : "failed"}
      </p>
      {last.tabs.length > 0 && (
        <table className="grid" aria-label="Sheet tabs">
          <tbody>
            {last.tabs.map((t) => (
              <tr key={`${t.portfolio} ${t.tab}`}>
                <td>
                  {t.portfolio} {t.tab}
                </td>
                <td className={t.ok ? "up" : "down"}>{t.ok ? "ok" : (t.error?.kind ?? "failed")}</td>
                <td className="num">{t.rows !== undefined ? `${t.rows} rows` : ""}</td>
                <td className="dim">{t.error?.message ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {last.error !== undefined && last.tabs.every((t) => t.error?.message !== last.error?.message) && <ErrorLine error={last.error} />}
    </div>
  );
}

export function SheetsSource() {
  const client = useQueryClient();
  const q = useQuery({ queryKey: queryKeys.sheets, queryFn: () => nem().sources.sheets.get() });
  if (q.isPending) return <p className="dim">Loading Google Sheet settings...</p>;
  if (q.isError) return <p role="alert">Could not read Google Sheet settings: {String(q.error)}</p>;
  return (
    <SheetsForm
      key={`${q.data.sheetMasked ?? ""}|${q.data.portfolios.join(",")}|${String(q.data.intervalMinutes)}`}
      state={q.data}
      onState={(s) => client.setQueryData(queryKeys.sheets, s)}
      onSynced={() => void client.invalidateQueries()}
    />
  );
}

function SheetsForm({ state, onState, onSynced }: { state: SheetsState; onState: (s: SheetsState) => void; onSynced: () => void }) {
  const [form, setForm] = useState<Form>(() => toForm(state));
  const [error, setError] = useState<SheetsError | null>(null);
  const set = (k: keyof Form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const request = () => ({
    sheet: form.sheet.trim(),
    portfolios: form.portfolios.split(/[\s,]+/).filter((p) => p !== ""),
    intervalMinutes: form.interval === "" ? null : Number(form.interval),
  });

  const save = useMutation({
    mutationFn: () => nem().sources.sheets.save(request()),
    onSuccess: (r) => {
      setError(r.ok ? null : r.error);
      if (r.ok) onState(r.state);
    },
  });
  const sync = useMutation({
    mutationFn: async () => {
      const saved = await nem().sources.sheets.save(request());
      if (!saved.ok) return { saved };
      return { saved, result: await nem().sources.sheets.sync() };
    },
    onSuccess: ({ saved, result }) => {
      setError(saved.ok ? null : saved.error);
      if (result !== undefined) {
        onState(result.state);
        onSynced();
      }
    },
  });
  const forget = useMutation({
    mutationFn: () => nem().sources.sheets.forget(),
    onSuccess: (s) => {
      onState(s);
      onSynced();
    },
  });

  const busy = sync.isPending || state.syncing;
  const fieldError = (f: SheetsError["field"]) =>
    error !== null && error.field === f ? (
      <span className="down" role="alert">
        {error.message}
      </span>
    ) : null;

  return (
    <section className="section vps-sync" aria-label="Google Sheet">
      <h2 className="section-title">Google Sheet</h2>
      <p className="dim vps-cache">
        Reads the tabs <code>nem report</code> publishes with the <code>sheets</code> reporter (
        <code>&lt;portfolio&gt; summary</code>, <code>strategies</code>, <code>trades</code>) without a Google account
        or key: in Sheets choose Share, General access &quot;Anyone with the link&quot;, role Viewer, then paste the
        link here. Anyone holding the link can read the sheet. The link is kept only in this app&apos;s settings and
        is never logged; each tab is checked against the same schemas as the CSV files.
      </p>
      <div className="form-row">
        <label htmlFor="sheets-sheet">Sheet link or ID</label>
        <input
          id="sheets-sheet"
          value={form.sheet}
          onChange={set("sheet")}
          placeholder={state.sheetMasked !== null ? `saved: ${state.sheetMasked} (paste to replace)` : "https://docs.google.com/spreadsheets/d/..."}
          spellCheck={false}
          autoComplete="off"
          size={48}
        />
        {fieldError("sheet")}
      </div>
      <div className="form-row">
        <label htmlFor="sheets-portfolios">Portfolios</label>
        <input id="sheets-portfolios" value={form.portfolios} onChange={set("portfolios")} placeholder="btc, weather" spellCheck={false} size={32} />
        {fieldError("portfolios")}
      </div>
      <div className="form-row">
        <label htmlFor="sheets-interval">Auto refresh</label>
        <select id="sheets-interval" value={form.interval} onChange={set("interval")}>
          <option value="">off (Sync now only)</option>
          {SHEETS_INTERVALS.map((m) => (
            <option key={m} value={m}>
              every {m} min
            </option>
          ))}
        </select>
        {fieldError("intervalMinutes")}
        {state.nextPollAt !== null && <span className="dim">next {state.nextPollAt}</span>}
      </div>
      <div className="row-wrap">
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending}>
          Save
        </button>
        <button type="button" onClick={() => sync.mutate()} disabled={busy}>
          {busy ? "Syncing..." : "Sync now"}
        </button>
        {state.configured && (
          <button type="button" onClick={() => forget.mutate()}>
            Forget sheet
          </button>
        )}
        {save.isSuccess && save.data.ok && <span className="dim">Saved.</span>}
      </div>
      {error !== null && error.field === undefined && <ErrorLine error={error} />}
      {state.lastSync !== null && <LastSync last={state.lastSync} />}
    </section>
  );
}
