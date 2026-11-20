import type { PixelIconName } from "../../components/PixelIcon";

/**
 * Category seed, hand-copied from docs/kalshi-tickers.txt (fetched 2026-10-08). Kalshi adds,
 * renames and retires series: live data always wins, and the search tab reaches any series.
 */

export interface SeedSeries {
  ticker: string;
  /** City, coin, ... */
  label: string;
  /** Feed parameter hint: Coinbase product or NWS station. */
  feedHint?: string;
}

export interface Category {
  id: string;
  label: string;
  icon: PixelIconName;
  /** Settlement source as written in the series rules. */
  settlement: string;
  /** What is measured, in one sentence. */
  measurement: string;
  /** Common NEM feed for this category and how it differs from settlement. */
  predictor: string;
  series: readonly SeedSeries[];
}

const CRYPTO_PREDICTOR =
  "Feed coinbase_spot (Coinbase spot) is a proxy, not the settlement value: Kalshi settles on the CF Benchmarks Real Time Index.";
const WEATHER_PREDICTOR =
  "Feed nws_forecast (NWS forecast for the station's lat/lon) is a predictor only: Kalshi settles on The Weather Company's reported value, not the NWS climate report.";

const CITIES: readonly [string, string, string, string][] = [
  // [city, station, high series, low series]
  ["Atlanta", "KATL", "KXHIGHTATL", "KXLOWTATL"],
  ["Austin", "KAUS", "KXHIGHAUS", "KXLOWTAUS"],
  ["Boston", "KBOS", "KXHIGHTBOS", "KXLOWTBOS"],
  ["Chicago", "KMDW", "KXHIGHCHI", "KXLOWTCHI"],
  ["Dallas", "KDFW", "KXHIGHTDAL", "KXLOWTDAL"],
  ["Denver", "KDEN", "KXHIGHDEN", "KXLOWTDEN"],
  ["Houston", "KHOU", "KXHIGHTHOU", "KXLOWTHOU"],
  ["Las Vegas", "KLAS", "KXHIGHTLV", "KXLOWTLV"],
  ["Los Angeles", "KLAX", "KXHIGHLAX", "KXLOWTLAX"],
  ["Louisville", "KSDF", "KXHIGHTSDF", "KXLOWTSDF"],
  ["Miami", "KMIA", "KXHIGHMIA", "KXLOWTMIA"],
  ["Minneapolis", "KMSP", "KXHIGHTMIN", "KXLOWTMIN"],
  ["New Orleans", "KMSY", "KXHIGHTNOLA", "KXLOWTNOLA"],
  ["New York City", "KNYC", "KXHIGHNY", "KXLOWTNYC"],
  ["Newark", "KEWR", "KXHIGHTEWR", "KXLOWTEWR"],
  ["Oklahoma City", "KOKC", "KXHIGHTOKC", "KXLOWTOKC"],
  ["Philadelphia", "KPHL", "KXHIGHPHIL", "KXLOWTPHIL"],
  ["Phoenix", "KPHX", "KXHIGHTPHX", "KXLOWTPHX"],
  ["San Antonio", "KSAT", "KXHIGHTSATX", "KXLOWTSATX"],
  ["San Diego", "KSAN", "KXHIGHTSAN", "KXLOWTSAN"],
  ["San Francisco", "KSFO", "KXHIGHTSFO", "KXLOWTSFO"],
  ["Seattle", "KSEA", "KXHIGHTSEA", "KXLOWTSEA"],
  ["Trenton", "KTTN", "KXHIGHTTTN", "KXLOWTTTN"],
  ["Washington DC", "KDCA", "KXHIGHTDC", "KXLOWTDC"],
];

export const CATEGORIES: readonly Category[] = [
  {
    id: "crypto15m",
    label: "Crypto 15m",
    icon: "coin",
    settlement: "CF Benchmarks",
    measurement:
      "60-second average of the CF Benchmarks Real Time Index before close vs the same average before open.",
    predictor: CRYPTO_PREDICTOR,
    series: [
      { ticker: "KXBTC15M", label: "Bitcoin", feedHint: "BTC-USD" },
      { ticker: "KXETH15M", label: "Ether", feedHint: "ETH-USD" },
      { ticker: "KXSOL15M", label: "Solana", feedHint: "SOL-USD" },
      { ticker: "KXXRP15M", label: "XRP", feedHint: "XRP-USD" },
      { ticker: "KXDOGE15M", label: "Dogecoin", feedHint: "DOGE-USD" },
      { ticker: "KXBNB15M", label: "BNB", feedHint: "BNB-USD" },
      { ticker: "KXHYPE15M", label: "Hype", feedHint: "HYPE-USD" },
      { ticker: "KXNEAR15M", label: "NEAR", feedHint: "NEAR-USD" },
      { ticker: "KXZEC15M", label: "Zcash", feedHint: "ZEC-USD" },
      { ticker: "KXCRYPTOLEAD15M", label: "Coin race (custom strike)" },
    ],
  },
  {
    id: "temp-high",
    label: "Daily high",
    icon: "temp-high",
    settlement: "The Weather Company",
    measurement: "Maximum temperature at the station named in the rules, for the market's date.",
    predictor: WEATHER_PREDICTOR,
    series: CITIES.map(([city, station, high]) => ({ ticker: high, label: city, feedHint: station })),
  },
  {
    id: "temp-low",
    label: "Daily low",
    icon: "temp-low",
    settlement: "The Weather Company",
    measurement: "Minimum temperature at the station named in the rules, for the market's date.",
    predictor: WEATHER_PREDICTOR,
    series: CITIES.map(([city, station, , low]) => ({ ticker: low, label: city, feedHint: station })),
  },
];

export const SEARCH_TAB = { id: "search", label: "Search", icon: "search" } as const satisfies {
  id: string;
  label: string;
  icon: PixelIconName;
};

export function categoryFor(series: string): Category | undefined {
  return CATEGORIES.find((c) => c.series.some((s) => s.ticker === series));
}

export function seedFor(series: string): SeedSeries | undefined {
  for (const c of CATEGORIES) {
    const s = c.series.find((x) => x.ticker === series);
    if (s !== undefined) return s;
  }
  return undefined;
}

/**
 * Predictor note from the live settlement source names, falling back to the seed category.
 * A feed is a predictor, not the settlement source.
 */
export function predictorNote(settlementNames: readonly string[], series: string): string | null {
  const names = settlementNames.join(" ").toLowerCase();
  if (names.includes("cf benchmarks")) return CRYPTO_PREDICTOR;
  if (names.includes("weather company")) return WEATHER_PREDICTOR;
  return categoryFor(series)?.predictor ?? null;
}
