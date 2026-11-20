import { useMemo, useState } from "react";
import type { Snapshot, StrategyRow } from "@shared/data";
import { EmptyState } from "../../components/states";
import { LineChart, SERIES_COLORS, type LineSeries } from "../../data/charts";
import { COMPARE_MAX, useCompare } from "../../data/compare";
import { cents, int, num, pct, range, signClass, signedUsd, usd } from "../../data/format";
import { allStrategies, useCurves, type StrategyRef } from "../../data/hooks";
import { alignByTime, alignByTrade } from "../../data/series";
import { DataErrors, SourceGate } from "../../data/states";
import { StrategyBadge } from "../../data/StrategyBadge";

/** Screen 3: two to four strategies side by side (retired included). */
export default function CompareScreen() {
  return (
    <SourceGate
      emptyTitle="Nothing to compare"
      emptyReason="Compare needs at least two strategies from a data source."
    >
      {(s) => <Compare snapshot={s} />}
    </SourceGate>
  );
}

const fmtUsd = (v: number) => usd(v, v !== 0 && Math.abs(v) < 10 ? 2 : 0);

interface StatLine {
  label: string;
  value: (r: StrategyRow) => string;
  tone?: (r: StrategyRow) => string;
}

const STATS: readonly StatLine[] = [
  { label: "Status", value: (r) => r.status },
  { label: "Series", value: (r) => r.series },
  { label: "Settled trades", value: (r) => int(r.trades) },
  { label: "Open", value: (r) => int(r.open) },
  { label: "Win rate", value: (r) => pct(r.win_rate) },
  { label: "95% interval", value: (r) => range(pct(r.win_rate_lo), pct(r.win_rate_hi)) },
  { label: "Break-even", value: (r) => pct(r.break_even_rate) },
  { label: "Edge / contract", value: (r) => cents(r.edge_cents), tone: (r) => signClass(r.edge_cents) },
  { label: "Edge interval", value: (r) => range(cents(r.edge_lo_cents), cents(r.edge_hi_cents)) },
  { label: "z / p", value: (r) => `${num(r.z)} / ${num(r.p)}` },
  { label: "P&L", value: (r) => signedUsd(r.pnl_usd), tone: (r) => signClass(r.pnl_usd) },
  { label: "P&L / trade", value: (r) => signedUsd(r.pnl_per_trade_usd, 4) },
  { label: "ROI", value: (r) => pct(r.roi) },
  { label: "Max drawdown", value: (r) => usd(r.max_drawdown_usd) },
  { label: "Trades to detect", value: (r) => (r.trades_to_detect === null ? "-" : `${int(r.trades_to_detect)} (${cents(r.trades_to_detect_edge_cents)})`) },
  { label: "Brier", value: (r) => num(r.brier) },
];

function Compare({ snapshot }: { snapshot: Snapshot }) {
  const all = useMemo(() => allStrategies(snapshot), [snapshot]);
  const ids = useCompare((s) => s.ids);
  const toggle = useCompare((s) => s.toggle);
  const [byTime, setByTime] = useState(false);
  const picked = useMemo(
    () => ids.map((id) => all.find((r) => r.id === id)).filter((r): r is StrategyRef => r !== undefined),
    [ids, all],
  );

  if (all.length < 2) {
    return (
      <EmptyState
        title="Nothing to compare"
        reason={`This source has ${all.length} strateg${all.length === 1 ? "y" : "ies"}; Compare needs at least two.`}
        commands={["uv run nem strategy add PORTFOLIO NAME --series SERIES", "uv run nem report"]}
      />
    );
  }

  return (
    <div className="screen-stack">
      <section className="section" aria-label="Pick strategies">
        <h2 className="section-title">
          Strategies ({picked.length} of {COMPARE_MAX})
        </h2>
        <div className="compare-pick">
          {all.map((r) => (
            <label key={r.id}>
              <input
                type="checkbox"
                checked={ids.includes(r.id)}
                onChange={() => toggle(r.id)}
                disabled={!ids.includes(r.id) && ids.length >= COMPARE_MAX}
              />
              {r.row.strategy} <span className="dim">{r.portfolio.name}{r.row.status !== "active" ? `, ${r.row.status}` : ""}</span>
            </label>
          ))}
        </div>
      </section>
      {picked.length < 2 ? (
        <EmptyState
          title="Pick at least two strategies"
          reason="Tick two to four strategies above, or press c on a strategy in Portfolios or Strategy detail."
        />
      ) : (
        <>
          <Curves picked={picked} byTime={byTime} setByTime={setByTime} />
          <div className="grid-wrap">
            <table className="grid" aria-label="Compare statistics">
              <thead>
                <tr>
                  <th />
                  {picked.map((r) => (
                    <th key={r.id}>{r.row.strategy} <span className="dim">{r.portfolio.name}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="dim">Verdict</td>
                  {picked.map((r) => (
                    <td key={r.id}><StrategyBadge row={r.row} /></td>
                  ))}
                </tr>
                {STATS.map((s) => (
                  <tr key={s.label}>
                    <td className="dim">{s.label}</td>
                    {picked.map((r) => (
                      <td key={r.id} className={`num ${s.tone?.(r.row) ?? ""}`}>{s.value(r.row)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Curves({ picked, byTime, setByTime }: { picked: StrategyRef[]; byTime: boolean; setByTime: (v: boolean) => void }) {
  const keys = useMemo(() => picked.map((r) => ({ portfolio: r.portfolio.name, strategy: r.row.strategy })), [picked]);
  const q = useCurves(keys);
  const chart = useMemo(() => {
    const data = q.data;
    if (data?.ok !== true) return null;
    const curves = keys.map((k) => data.curves.find((c) => c.portfolio === k.portfolio && c.strategy === k.strategy) ?? { ...k, t: [], pnl: [] });
    const a = byTime ? alignByTime(curves) : alignByTrade(curves);
    const series: LineSeries[] = curves.map((c, i) => ({
      label: c.strategy,
      color: SERIES_COLORS[i % SERIES_COLORS.length] ?? SERIES_COLORS[0],
      values: a.ys[i] ?? [],
    }));
    return { x: a.x, series };
  }, [q.data, keys, byTime]);

  return (
    <section className="panel-box" aria-label="P&L curves">
      <div className="card-head">
        <h3>Cumulative P&L from a common start</h3>
        <span className="segmented" role="group" aria-label="X axis">
          <button type="button" aria-pressed={!byTime} onClick={() => setByTime(false)}>Trade #</button>
          <button type="button" aria-pressed={byTime} onClick={() => setByTime(true)}>Time</button>
        </span>
      </div>
      {q.isPending ? (
        <p className="dim">Loading trades...</p>
      ) : q.data?.ok === false ? (
        <DataErrors errors={q.data.errors} />
      ) : chart === null || chart.x.length === 0 ? (
        <p className="dim">No settled trades yet.</p>
      ) : (
        <>
          <div className="legend">
            {chart.series.map((s) => (
              <span key={s.label}>
                <span className="legend-swatch" style={{ background: s.color }} />
                {s.label}
              </span>
            ))}
          </div>
          <LineChart x={chart.x} series={chart.series} time={byTime} label="Cumulative P&L, compared" yFormat={fmtUsd} />
          <p className="dim">
            {byTime
              ? "Every curve is 0 at the earliest first settlement among them and steps at each of its settlements."
              : "Every curve starts at 0 at trade 0; x is the strategy's own settled-trade count."}
          </p>
        </>
      )}
    </section>
  );
}
