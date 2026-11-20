import type { StrategyRow } from "@shared/data";
import { LoreCard } from "../components/LoreCard";
import { TierBadge } from "../components/TierBadge";
import { tier } from "../lib/tier";
import { cents, int, pct, range, signedUsd } from "./format";

const VERDICT_TEXT: Record<StrategyRow["verdict"], string> = {
  edge: "edge (SPRT)",
  no_edge: "no edge (SPRT)",
  undecided: "undecided (SPRT)",
};

/** Tier badge with the strategy's lore card on hover/focus. Values straight from the export. */
export function StrategyBadge({ row }: { row: StrategyRow }) {
  return (
    <LoreCard
      title={row.strategy}
      tier={tier(row.verdict, row.edge_cents)}
      stats={[
        { label: "verdict", value: VERDICT_TEXT[row.verdict] },
        { label: "settled trades", value: int(row.trades) },
        { label: "win rate", value: pct(row.win_rate) },
        { label: "95% interval", value: range(pct(row.win_rate_lo), pct(row.win_rate_hi)) },
        { label: "break-even", value: pct(row.break_even_rate) },
        { label: "edge / contract", value: cents(row.edge_cents) },
        { label: "edge interval", value: range(cents(row.edge_lo_cents), cents(row.edge_hi_cents)) },
        { label: "P&L", value: signedUsd(row.pnl_usd) },
        {
          label: "trades to detect",
          value: row.trades_to_detect === null ? "-" : `${int(row.trades_to_detect)} (${cents(row.trades_to_detect_edge_cents)})`,
        },
      ]}
    >
      <TierBadge verdict={row.verdict} edgeCents={row.edge_cents} />
    </LoreCard>
  );
}
