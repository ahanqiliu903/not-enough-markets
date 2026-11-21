import type { KalshiErrorKind, KalshiMarket, KalshiSeries } from "@shared/kalshi";
import type { LoreStat } from "../../components/LoreCard";
import { categoryFor, predictorNote } from "./catalog";

/** 0.932 -> "93.2c", 0.74 -> "74c", null -> "--". Prices tick in 0.1c above 90c / below 10c. */
export function formatPrice(p: number | null): string {
  if (p === null) return "--";
  const cents = p * 100;
  const whole = Math.round(cents);
  if (Math.abs(cents - whole) < 1e-6) return `${whole}c`;
  return `${cents.toFixed(1)}c`;
}

const qtyFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export function formatQty(q: number | null): string {
  return q === null ? "--" : qtyFmt.format(q);
}

/** Time left until `closeIso`: "12m 05s", "3h 12m", "2d 4h", or "closed". */
export function countdown(closeIso: string, nowMs: number): string {
  const ms = Date.parse(closeIso) - nowMs;
  if (!Number.isFinite(ms)) return "--";
  if (ms <= 0) return "closed";
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h < 1) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  if (h < 48) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

const etFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

/** Close times are shown in US Eastern, as Kalshi's tickers and rules use. */
export function formatEt(iso: string): string {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? etFmt.format(t) : "--";
}

export function isOpen(m: Pick<KalshiMarket, "status" | "closeTime">, nowMs: number): boolean {
  return (m.status === "active" || m.status === "open") && Date.parse(m.closeTime) > nowMs;
}

function num(v: number | null): string {
  return v === null ? "?" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(v);
}

/** What YES means, from strike_type and floor/cap (Kalshi's own wording is in the rules). */
export function yesMeans(m: KalshiMarket): string {
  if (m.customStrike) return "Custom strike: read the rules (feed_agrees can't evaluate it).";
  switch (m.strikeType) {
    case "greater":
      return `Result > ${num(m.floorStrike)} (strike_type greater)`;
    case "greater_or_equal":
      return `Result >= ${num(m.floorStrike)} (strike_type greater_or_equal)`;
    case "less":
      return `Result < ${num(m.capStrike)} (strike_type less)`;
    case "less_or_equal":
      return `Result <= ${num(m.capStrike)} (strike_type less_or_equal)`;
    case "between":
      return `Result in ${num(m.floorStrike)} to ${num(m.capStrike)} (strike_type between; edges per rules)`;
    case null:
      return "Strike type not given: read the rules.";
  }
}

export interface RulesCardContent {
  title: string;
  stats: LoreStat[];
  /** Kalshi's rule text, verbatim. */
  rules: string[];
  notes: string[];
}

/** Rules card for one market: settlement source, what YES means, close in ET, predictor note. */
export function marketRules(m: KalshiMarket, series: KalshiSeries | null, seriesTicker: string): RulesCardContent {
  const names = series?.settlementSources.map((s) => s.name) ?? [];
  const fallback = categoryFor(seriesTicker)?.settlement;
  const settlement = names.length > 0 ? names.join(", ") : (fallback ?? "see rules");
  const notes: string[] = [];
  const predictor = predictorNote(names, seriesTicker);
  if (predictor !== null) notes.push(`Predictor: ${predictor}`);
  if (m.canCloseEarly) notes.push("Can close early.");
  const text = `${m.rulesSecondary ?? ""} ${series?.importantInfo ?? ""}`.toLowerCase();
  if (text.includes("last fair price")) notes.push("Fallback: may resolve to Kalshi's last fair price if data is missing.");
  if (text.includes("revision")) notes.push("Data revisions can delay expiration.");
  const rules = [m.rulesPrimary, m.rulesSecondary].filter((r): r is string => r !== null);
  if (rules.length === 0) {
    const measurement = categoryFor(seriesTicker)?.measurement;
    if (measurement !== undefined) rules.push(`(seed) ${measurement}`);
  }
  return {
    title: "Rules",
    stats: [
      { label: "Settles on", value: settlement },
      { label: "YES means", value: yesMeans(m) },
      { label: "Closes (ET)", value: formatEt(m.closeTime) },
    ],
    rules,
    notes,
  };
}

export const ERROR_TEXT: Record<KalshiErrorKind, { title: string; reason: string }> = {
  offline: {
    title: "Kalshi unreachable",
    reason: "Couldn't reach api.elections.kalshi.com (offline, DNS, or timeout). NEM Desk retries on the next refresh.",
  },
  rate_limited: {
    title: "Rate limited",
    reason: "Kalshi answered 429 (too many requests) after retries with backoff. Wait a few seconds, then press r.",
  },
  upstream: {
    title: "Kalshi error",
    reason: "Kalshi returned a server error (5xx) after retries. It's on their side; try again shortly.",
  },
  not_found: { title: "Not found", reason: "Kalshi has no such series or market. Check the ticker." },
  bad_request: { title: "Bad request", reason: "The request was rejected. Check the ticker." },
  invalid_response: {
    title: "Unexpected response",
    reason: "Kalshi's response didn't parse (the wire format may have drifted). Nothing from it is shown.",
  },
  blocked: { title: "Request blocked", reason: "Only api.elections.kalshi.com/trade-api/v2 may be requested." },
};

/** Series-level rules card (tiles, series header): from live series data, else the seed. */
export function seriesRules(series: KalshiSeries | null, ticker: string): RulesCardContent {
  const cat = categoryFor(ticker);
  const names = series?.settlementSources.map((s) => s.name) ?? [];
  const predictor = predictorNote(names, ticker);
  const rules: string[] = [];
  if (cat !== undefined) rules.push(cat.measurement);
  if (series?.importantInfo != null) rules.push(series.importantInfo.replace(/\*\*/g, ""));
  return {
    title: ticker,
    stats: [
      { label: "Settles on", value: names.length > 0 ? names.join(", ") : (cat?.settlement ?? "see market rules") },
      ...(series !== null && series.frequency !== "" ? [{ label: "Frequency", value: series.frequency }] : []),
    ],
    rules,
    notes: [
      ...(predictor !== null ? [`Predictor: ${predictor}`] : []),
      "Each market's own rules (station, date, strike) are on its row and page.",
    ],
  };
}
