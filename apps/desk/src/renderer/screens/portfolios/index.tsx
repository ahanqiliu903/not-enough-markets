import { useMemo, useState } from "react";
import type { PortfolioData, Snapshot } from "@shared/data";
import { PixelIcon } from "../../components/PixelIcon";
import { EmptyState } from "../../components/states";
import { useCompare } from "../../data/compare";
import { int, pct, signClass, signedUsd, ts, usd } from "../../data/format";
import { allStrategies, errorsForFile } from "../../data/hooks";
import { DataErrors, DATA_COMMANDS, SourceGate } from "../../data/states";
import { StrategyBadge } from "../../data/StrategyBadge";
import { useKeys } from "../../lib/keys";
import { useRegisterCommands, type Command } from "../../state/commands";
import { useNav } from "../../state/nav";

/** Screen 1: one card per portfolio, with its strategies and their verdict tiers. */
export default function PortfoliosScreen() {
  return (
    <SourceGate
      emptyTitle="No portfolios yet"
      emptyReason="There are no portfolios or verdicts to show. The desk displays exported engine results; it never computes them."
    >
      {(s) => <Portfolios snapshot={s} />}
    </SourceGate>
  );
}

function Stat({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value num ${className}`}>{value}</span>
    </div>
  );
}

function Portfolios({ snapshot }: { snapshot: Snapshot }) {
  const navigate = useNav((s) => s.navigate);
  const toggleCompare = useCompare((s) => s.toggle);
  const compareIds = useCompare((s) => s.ids);
  const rows = useMemo(() => allStrategies(snapshot), [snapshot]);
  const [sel, setSel] = useState(0);
  const selected = rows[Math.min(sel, rows.length - 1)];

  const open = (portfolio: string, strategy: string) => navigate("strategy", { portfolio, strategy });

  useKeys({
    j: () => setSel((i) => Math.min(rows.length - 1, i + 1)),
    k: () => setSel((i) => Math.max(0, i - 1)),
    Enter: () => {
      if (selected === undefined) return false;
      open(selected.portfolio.name, selected.row.strategy);
    },
    c: () => {
      if (selected === undefined) return false;
      toggleCompare(selected.id);
    },
  });

  const commands = useMemo<Command[]>(
    () =>
      rows.map((r) => ({
        id: `strategy:${r.id}`,
        title: `${r.row.strategy} (${r.portfolio.name})`,
        group: "Strategy",
        keywords: `${r.row.series} ${r.row.verdict}`,
        run: () => navigate("strategy", { portfolio: r.portfolio.name, strategy: r.row.strategy }),
      })),
    [rows, navigate],
  );
  useRegisterCommands(commands);

  if (snapshot.portfolios.length === 0) {
    return (
      <EmptyState
        title="No portfolios yet"
        reason={`${snapshot.source.label} has no out/<portfolio>/summary.csv, strategies.csv or trades.csv yet (looked in ${snapshot.outDir}). Run the engine with a csv reporter, then rsync or copy out/ here.`}
        commands={DATA_COMMANDS}
      />
    );
  }

  return (
    <div className="screen-stack">
      {snapshot.errors.length > 0 && <DataErrors errors={snapshot.errors} />}
      {snapshot.portfolios.map((p) => (
        <PortfolioCard
          key={p.name}
          p={p}
          selectedId={selected?.id}
          compareIds={compareIds}
          onOpen={open}
          onSelect={(id) => setSel(rows.findIndex((r) => r.id === id))}
        />
      ))}
      <p className="dim">
        j/k select, Enter opens a strategy, c adds it to Compare ({compareIds.length} picked). Hover a badge for its lore card.
      </p>
    </div>
  );
}

function PortfolioCard({
  p,
  selectedId,
  compareIds,
  onOpen,
  onSelect,
}: {
  p: PortfolioData;
  selectedId: string | undefined;
  compareIds: string[];
  onOpen: (portfolio: string, strategy: string) => void;
  onSelect: (id: string) => void;
}) {
  const s = p.summary;
  const summaryErrors = errorsForFile(p.errors, "summary.csv");
  const strategyErrors = errorsForFile(p.errors, "strategies.csv");
  const tradeErrors = errorsForFile(p.errors, "trades.csv");
  return (
    <section className="card pixel-border" aria-label={`Portfolio ${p.name}`}>
      <div className="card-head">
        <PixelIcon name="portfolio" className="card-icon" />
        <h2 className="card-title pixel-font">{p.name}</h2>
        {s !== null && <span className="tag">{s.mode.toUpperCase()}</span>}
        {s !== null && <span className="dim">as of {ts(s.as_of)}</span>}
      </div>
      {s !== null ? (
        <div className="stat-row">
          <Stat label="Equity" value={usd(s.equity_usd)} />
          <Stat label="Return" value={pct(s.return_rate)} className={signClass(s.return_rate)} />
          <Stat label="Realized P&L" value={signedUsd(s.realized_pnl_usd)} className={signClass(s.realized_pnl_usd)} />
          <Stat label="Interest" value={usd(s.interest_usd)} />
          <Stat
            label={s.interest_apy === null ? "P&L vs cash (no APY set)" : `P&L vs cash @ ${pct(s.interest_apy)} APY`}
            value={signedUsd(s.pnl_vs_cash_usd)}
            className={signClass(s.pnl_vs_cash_usd)}
          />
          <Stat label="Open positions" value={`${int(s.open_positions)} (${usd(s.open_cost_usd)})`} />
          <Stat label="Starting balance" value={usd(s.starting_balance_usd)} />
        </div>
      ) : (
        <DataErrors errors={summaryErrors} />
      )}
      {p.strategies === null ? (
        <DataErrors errors={strategyErrors} />
      ) : p.strategies.length === 0 ? (
        <EmptyState
          title="No strategies yet"
          reason={`Portfolio ${p.name} has no strategies in strategies.csv. Add one to the portfolio file, run the engine, then export again.`}
          commands={[`uv run nem strategy add ${p.name} NAME --series SERIES`, "uv run nem report"]}
        />
      ) : (
        <div className="grid-wrap">
          <table className="grid" aria-label={`Strategies in ${p.name}`}>
            <thead>
              <tr>
                <th>Verdict</th>
                <th>Strategy</th>
                <th>Series</th>
                <th>Status</th>
                <th className="num">Trades</th>
                <th className="num">Open</th>
                <th className="num">Win rate</th>
                <th className="num">Break-even</th>
                <th className="num">Edge</th>
                <th className="num">P&L</th>
                <th>Compare</th>
              </tr>
            </thead>
            <tbody>
              {p.strategies.map((r) => {
                const id = `${p.name}/${r.strategy}`;
                return (
                  <tr
                    key={r.strategy}
                    className={`selectable${selectedId === id ? " selected" : ""}${r.status === "retired" ? " retired" : ""}`}
                    aria-selected={selectedId === id}
                    data-strategy={r.strategy}
                    onClick={() => {
                      onSelect(id);
                      onOpen(p.name, r.strategy);
                    }}
                  >
                    <td>
                      <StrategyBadge row={r} />
                    </td>
                    <td>{r.strategy}</td>
                    <td>{r.series}</td>
                    <td className={r.status === "active" ? "" : "dim"}>{r.status}</td>
                    <td className="num">{int(r.trades)}</td>
                    <td className="num">{int(r.open)}</td>
                    <td className="num">{pct(r.win_rate)}</td>
                    <td className="num">{pct(r.break_even_rate)}</td>
                    <td className={`num ${signClass(r.edge_cents)}`}>{r.edge_cents === null ? "-" : `${r.edge_cents.toFixed(2)}c`}</td>
                    <td className={`num ${signClass(r.pnl_usd)}`}>{signedUsd(r.pnl_usd)}</td>
                    <td className="dim">{compareIds.includes(id) ? "picked" : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {tradeErrors.length > 0 && (
        <p className="dim" role="note">
          trades.csv for {p.name} has problems ({tradeErrors[0]?.message}); trade lists and P&L curves are unavailable.
        </p>
      )}
    </section>
  );
}
