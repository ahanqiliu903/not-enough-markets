import {
  TRADE_COLUMNS,
  type CurveKey,
  type DataError,
  type PnlCurve,
  type TradeColumn,
  type TradeRow,
  type TradesFilter,
  type TradesQuery,
} from "@shared/data";
import type { LoadedPortfolio, LoadedSource } from "./source";

/**
 * Trades live in main and are filtered, sorted and paged here, so the renderer only ever
 * holds the rows on screen. The last filtered + sorted index is cached, which makes
 * scrolling (same query, new offset) a slice.
 */

export interface TradeRef {
  p: LoadedPortfolio;
  i: number;
}

/** Errors that block trades.csv in any of these portfolios (missing file, schema, ...). */
function blockedErrors(portfolios: LoadedPortfolio[]): DataError[] {
  return portfolios
    .filter((p) => p.trades === null)
    .flatMap((p) => p.errors.filter((e) => e.file.endsWith("trades.csv")));
}

export function selectPortfolios(source: LoadedSource, portfolio: string | undefined): LoadedPortfolio[] {
  return portfolio === undefined ? source.portfolios : source.portfolios.filter((p) => p.name === portfolio);
}

export function filterTrades(source: LoadedSource, f: TradesFilter): { refs: TradeRef[]; errors: DataError[] } {
  const ps = selectPortfolios(source, f.portfolio);
  const errors = blockedErrors(ps);
  if (errors.length > 0) return { refs: [], errors };
  const text = f.text?.trim().toLowerCase() ?? "";
  const refs: TradeRef[] = [];
  for (const p of ps) {
    const rows = p.trades ?? [];
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (r === undefined) continue;
      if (f.strategy !== undefined && r.strategy !== f.strategy) continue;
      if (f.result !== undefined && r.result !== f.result) continue;
      if (
        text !== "" &&
        !r.ticker.toLowerCase().includes(text) &&
        !r.window_id.toLowerCase().includes(text) &&
        !r.strategy.toLowerCase().includes(text)
      ) {
        continue;
      }
      refs.push({ p, i });
    }
  }
  return { refs, errors: [] };
}

/** Nulls sort last in both directions; strings by code unit (timestamps sort as text). */
export function sortTrades(refs: TradeRef[], column: TradeColumn, dir: "asc" | "desc"): TradeRef[] {
  const sign = dir === "asc" ? 1 : -1;
  const keyed = refs.map((ref) => ({ ref, v: ref.p.trades?.[ref.i]?.[column] ?? null }));
  keyed.sort((a, b) => {
    if (a.v === null || b.v === null) return a.v === b.v ? 0 : a.v === null ? 1 : -1;
    if (a.v < b.v) return -sign;
    if (a.v > b.v) return sign;
    return 0;
  });
  return keyed.map((k) => k.ref);
}

export function isTradeColumn(v: unknown): v is TradeColumn {
  return typeof v === "string" && (TRADE_COLUMNS as readonly string[]).includes(v);
}

export class TradeIndex {
  private key = "";
  private refs: TradeRef[] = [];
  private errors: DataError[] = [];

  query(source: LoadedSource, revision: number, q: Omit<TradesQuery, "offset" | "limit">): { refs: TradeRef[]; errors: DataError[] } {
    const key = JSON.stringify([revision, q.portfolio, q.strategy, q.result, q.text, q.sort?.column, q.sort?.dir]);
    if (key !== this.key) {
      const f = filterTrades(source, q);
      this.refs = q.sort !== undefined ? sortTrades(f.refs, q.sort.column, q.sort.dir) : f.refs;
      this.errors = f.errors;
      this.key = key;
    }
    return { refs: this.refs, errors: this.errors };
  }

  page(source: LoadedSource, revision: number, q: TradesQuery): { total: number; rows: TradeRow[]; errors: DataError[] } {
    const { refs, errors } = this.query(source, revision, q);
    const rows = refs.slice(q.offset, q.offset + q.limit).map((r) => r.p.trades?.[r.i]).filter((r): r is TradeRow => r !== undefined);
    return { total: refs.length, rows, errors };
  }
}

function csvCell(s: string): string {
  return /[",\r\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

/** The matching rows as CSV, with the engine's own cell text and the contract's columns. */
export function tradesCsv(refs: TradeRef[]): string {
  const lines = [TRADE_COLUMNS.join(",")];
  for (const { p, i } of refs) {
    const raw = p.tradesRaw[i];
    if (raw === undefined) continue;
    lines.push(TRADE_COLUMNS.map((c) => csvCell(raw[p.tradesHeader.indexOf(c)] ?? "")).join(","));
  }
  return `${lines.join("\n")}\n`;
}

/**
 * Cumulative realized P&L per strategy over settled trades, ordered by settled_at. A
 * running sum of the engine's pnl_usd for charting; no statistic is derived here.
 */
export function pnlCurves(source: LoadedSource, keys: CurveKey[]): { curves: PnlCurve[]; errors: DataError[] } {
  const curves: PnlCurve[] = [];
  const errors: DataError[] = [];
  for (const k of keys) {
    const p = source.portfolios.find((x) => x.name === k.portfolio);
    if (p === undefined) continue;
    if (p.trades === null) {
      errors.push(...p.errors.filter((e) => e.file.endsWith("trades.csv")));
      continue;
    }
    const settled = p.trades.filter((r) => r.strategy === k.strategy && r.settled_at !== null && r.pnl_usd !== null);
    settled.sort((a, b) => ((a.settled_at ?? "") < (b.settled_at ?? "") ? -1 : (a.settled_at ?? "") > (b.settled_at ?? "") ? 1 : 0));
    const t: number[] = [];
    const pnl: number[] = [];
    let sum = 0;
    for (const r of settled) {
      sum += r.pnl_usd ?? 0;
      t.push(Date.parse(r.settled_at ?? "") / 1000);
      // Round to a hundredth of a cent so float noise never shows up as a number.
      pnl.push(Math.round(sum * 10000) / 10000);
    }
    curves.push({ portfolio: k.portfolio, strategy: k.strategy, t, pnl });
  }
  return { curves, errors: [...new Map(errors.map((e) => [JSON.stringify(e), e])).values()] };
}
