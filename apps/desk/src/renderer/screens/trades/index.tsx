import { keepPreviousData, useQueries } from "@tanstack/react-query";
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Snapshot, TradeColumn, TradeResult, TradeRow, TradesFilter, TradesPage } from "@shared/data";
import { EmptyState } from "../../components/states";
import { EXCLUDED_NOTE, excludedFlag, int, MARK_LABEL, noBidFlag, num, signClass, signedUsd, ts, usd } from "../../data/format";
import { DataErrors, DATA_COMMANDS, SourceGate } from "../../data/states";
import { nem, queryKeys } from "../../lib/api";
import { useNav } from "../../state/nav";

/** Screen 4: every trade, virtualized; filtered, sorted and paged in main. */
export default function TradesScreen() {
  return (
    <SourceGate emptyTitle="No trades" emptyReason="There are no paper trades to list.">
      {(s) => <Trades snapshot={s} />}
    </SourceGate>
  );
}

const PAGE_SIZE = 200;
const ROW_PX = 20;

interface Col {
  id: TradeColumn;
  label: string;
  width: number;
  num?: boolean;
  cell: (r: TradeRow) => string;
  tone?: (r: TradeRow) => string;
}

const TRADE_COLS: readonly Col[] = [
  { id: "opened_at", label: "Opened (UTC)", width: 170, cell: (r) => ts(r.opened_at) },
  { id: "portfolio", label: "Portfolio", width: 100, cell: (r) => r.portfolio },
  { id: "strategy", label: "Strategy", width: 130, cell: (r) => r.strategy },
  { id: "ticker", label: "Ticker", width: 210, cell: (r) => r.ticker },
  { id: "side", label: "Side", width: 50, cell: (r) => r.side.toUpperCase() },
  { id: "contracts", label: "Qty", width: 60, num: true, cell: (r) => int(r.contracts) },
  { id: "entry_price_cents", label: "Price c", width: 70, num: true, cell: (r) => r.entry_price_cents.toFixed(1) },
  { id: "fee_usd", label: "Fee", width: 64, num: true, cell: (r) => usd(r.fee_usd) },
  { id: "cost_per_contract_cents", label: "Cost c", width: 70, num: true, cell: (r) => r.cost_per_contract_cents.toFixed(2) },
  { id: "result", label: "Result", width: 64, cell: (r) => r.result, tone: (r) => (r.result === "won" ? "up" : r.result === "lost" ? "down" : "dim") },
  { id: "excluded", label: "Statistics", width: 220, cell: (r) => excludedFlag(r) || "counted", tone: (r) => (r.excluded === true ? "excluded-flag" : "dim") },
  { id: "pnl_usd", label: "P&L", width: 80, num: true, cell: (r) => signedUsd(r.pnl_usd), tone: (r) => signClass(r.pnl_usd) },
  { id: "unrealized_pnl_usd", label: "Unrealized", width: 90, num: true, cell: (r) => signedUsd(r.unrealized_pnl_usd), tone: (r) => signClass(r.unrealized_pnl_usd) },
  { id: "mark_value_usd", label: "Mark value", width: 84, num: true, cell: (r) => usd(r.mark_value_usd) },
  { id: "mark_bid_cents", label: "Mark c", width: 64, num: true, cell: (r) => num(r.mark_bid_cents, 1) },
  {
    id: "mark_contracts",
    label: "Marked qty",
    width: 250,
    num: true,
    cell: (r) => {
      const flag = noBidFlag(r.mark_contracts, r.contracts);
      return flag ? `${int(r.mark_contracts)} · ${flag}` : int(r.mark_contracts);
    },
    tone: (r) => (noBidFlag(r.mark_contracts, r.contracts) ? "down" : ""),
  },
  { id: "mark_mid_cents", label: "Mid c", width: 60, num: true, cell: (r) => num(r.mark_mid_cents, 1) },
  { id: "marked_at", label: "Marked (UTC)", width: 170, cell: (r) => ts(r.marked_at) },
  { id: "settled_at", label: "Settled (UTC)", width: 170, cell: (r) => ts(r.settled_at) },
  { id: "close_time", label: "Close (UTC)", width: 170, cell: (r) => ts(r.close_time) },
  { id: "window_id", label: "Window", width: 190, cell: (r) => r.window_id },
  { id: "mode", label: "Mode", width: 56, cell: (r) => r.mode },
];

// The table instance only holds column + sort state (sorting is manual, done in main);
// rows are drawn from the paged results below.
const columns: ColumnDef<TradeRow>[] = TRADE_COLS.map((c) => ({
  id: c.id,
  header: c.label,
  accessorFn: (r: TradeRow) => r[c.id],
  enableSorting: true,
}));
const gridTemplate = TRADE_COLS.map((c) => `${c.width}px`).join(" ");
const RESULTS: readonly TradeResult[] = ["won", "lost", "open"];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function Trades({ snapshot }: { snapshot: Snapshot }) {
  const params = useNav((s) => s.params);
  const [portfolio, setPortfolio] = useState(params["portfolio"] ?? "");
  const [strategy, setStrategy] = useState(params["strategy"] ?? "");
  const [result, setResult] = useState<"" | TradeResult>("");
  const [text, setText] = useState("");
  const [hideExcluded, setHideExcluded] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([{ id: "opened_at", desc: true }]);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const debouncedText = useDebounced(text, 120);

  // Following a link from another screen replaces the filter.
  useEffect(() => {
    setPortfolio(params["portfolio"] ?? "");
    setStrategy(params["strategy"] ?? "");
  }, [params]);

  const filter = useMemo<TradesFilter>(() => {
    const f: TradesFilter = {};
    if (portfolio !== "") f.portfolio = portfolio;
    if (strategy !== "") f.strategy = strategy;
    if (result !== "") f.result = result;
    if (debouncedText.trim() !== "") f.text = debouncedText.trim();
    if (hideExcluded) f.hideExcluded = true;
    return f;
  }, [portfolio, strategy, result, debouncedText, hideExcluded]);
  const sort = sorting[0];
  const base = useMemo(
    () => ({ ...filter, ...(sort !== undefined ? { sort: { column: sort.id as TradeColumn, dir: sort.desc ? ("desc" as const) : ("asc" as const) } } : {}) }),
    [filter, sort],
  );

  const table = useReactTable({
    data: [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    manualSorting: true,
    enableSortingRemoval: false,
    getCoreRowModel: getCoreRowModel(),
  });

  const scrollRef = useRef<HTMLDivElement>(null);
  const [total, setTotal] = useState(0);
  const virtualizer = useVirtualizer({
    count: total,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_PX,
    overscan: 30,
  });
  const items = virtualizer.getVirtualItems();
  const pageIdx = useMemo(() => {
    const set = new Set<number>([0]);
    for (const it of items) set.add(Math.floor(it.index / PAGE_SIZE));
    return [...set].sort((a, b) => a - b);
  }, [items]);

  const pages = useQueries({
    queries: pageIdx.map((p) => ({
      queryKey: [...queryKeys.trades, base, p],
      queryFn: (): Promise<TradesPage> => nem().data.trades({ ...base, offset: p * PAGE_SIZE, limit: PAGE_SIZE }),
      placeholderData: keepPreviousData,
      staleTime: 60_000,
    })),
  });
  const first = pages[0]?.data;
  const firstTotal = first?.ok === true ? first.total : 0;
  useEffect(() => setTotal(firstTotal), [firstTotal]);
  useEffect(() => {
    if (scrollRef.current !== null) scrollRef.current.scrollTop = 0;
  }, [base]);

  const rowAt = (i: number): TradeRow | undefined => {
    const p = Math.floor(i / PAGE_SIZE);
    const q = pages[pageIdx.indexOf(p)]?.data;
    if (q?.ok !== true || q.offset !== p * PAGE_SIZE) return undefined;
    return q.rows[i - q.offset];
  };

  const portfolios = snapshot.portfolios.map((p) => p.name);
  const strategies = [
    ...new Set(
      snapshot.portfolios
        .filter((p) => portfolio === "" || p.name === portfolio)
        .flatMap((p) => (p.strategies ?? []).map((s) => s.strategy)),
    ),
  ].sort();
  const totalTrades = snapshot.portfolios.reduce((n, p) => n + (p.tradeCount ?? 0), 0);

  const exportCsv = async () => {
    setExportMsg("Exporting...");
    const r = await nem().data.tradesCsv(base);
    if (!r.ok) {
      setExportMsg(`Export failed: ${r.errors[0]?.message ?? "unknown error"}`);
      return;
    }
    download(`trades-${portfolio || "all"}${strategy ? `-${strategy}` : ""}.csv`, r.csv);
    setExportMsg(`Exported ${int(r.rows)} rows.`);
  };

  if (first?.ok === false) {
    return <DataErrors errors={first.errors} />;
  }
  if (first !== undefined && totalTrades === 0 && snapshot.portfolios.every((p) => p.tradeCount !== null)) {
    return (
      <EmptyState
        title="No trades"
        reason={`${snapshot.source.label} has trades.csv files but no trades in them yet. Paper trading records them; export again after the engine has traded.`}
        commands={["uv run nem run", ...DATA_COMMANDS.slice(1)]}
      />
    );
  }

  return (
    <div className="trades-screen">
      <div className="toolbar" role="toolbar" aria-label="Trade filters">
        <select aria-label="Portfolio" value={portfolio} onChange={(e) => { setPortfolio(e.target.value); setStrategy(""); }}>
          <option value="">All portfolios</option>
          {portfolios.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <select aria-label="Strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
          <option value="">All strategies</option>
          {strategies.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select aria-label="Result" value={result} onChange={(e) => setResult(e.target.value as "" | TradeResult)}>
          <option value="">Any result</option>
          {RESULTS.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
        <input
          type="search"
          data-screen-filter
          aria-label="Filter trades"
          placeholder="/ filter ticker, window, strategy"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <label className="toggle" title={`Hide trades ${EXCLUDED_NOTE}`}>
          <input type="checkbox" checked={hideExcluded} onChange={(e) => setHideExcluded(e.target.checked)} />
          Hide excluded
        </label>
        <span className="spacer" />
        <span className="dim" data-testid="trade-count">
          {int(total)} of {int(totalTrades)} trades
        </span>
        <button type="button" onClick={() => void exportCsv()} disabled={total === 0}>
          Export CSV
        </button>
        {exportMsg !== null && <span className="dim" role="status">{exportMsg}</span>}
      </div>
      <p className="dim trades-note">Mark columns (open trades): {MARK_LABEL}, from the engine; contracts without bids count at $0 (flagged "no bids"); empty when settled, stale or closed. Muted rows are {EXCLUDED_NOTE}.</p>
      <div className="blotter" ref={scrollRef} role="table" aria-label="Trades" aria-rowcount={total + 1}>
        <div className="blotter-head" role="row" style={{ gridTemplateColumns: gridTemplate }}>
          {table.getHeaderGroups()[0]?.headers.map((h) => {
            const col = TRADE_COLS.find((c) => c.id === h.id);
            const dir = h.column.getIsSorted();
            return (
              <button
                key={h.id}
                type="button"
                role="columnheader"
                className={col?.num === true ? "num" : ""}
                aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
                onClick={h.column.getToggleSortingHandler()}
              >
                {col?.label}
                {dir === "asc" ? " ^" : dir === "desc" ? " v" : ""}
              </button>
            );
          })}
        </div>
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {items.map((it) => {
            const r = rowAt(it.index);
            return (
              <div
                key={it.key}
                role="row"
                aria-rowindex={it.index + 2}
                className={`blotter-row${r?.excluded === true ? " excluded" : ""}`}
                style={{ transform: `translateY(${it.start}px)`, gridTemplateColumns: gridTemplate }}
              >
                {TRADE_COLS.map((c) => (
                  <span key={c.id} role="cell" className={`${c.num === true ? "num " : ""}${r !== undefined ? (c.tone?.(r) ?? "") : "dim"}`}>
                    {r !== undefined ? c.cell(r) : "..."}
                  </span>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
