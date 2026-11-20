import { useState } from "react";
import { FlashValue } from "../../components/FlashValue";
import { LoreCard } from "../../components/LoreCard";
import { PIXEL_ICON_NAMES, PixelIcon } from "../../components/PixelIcon";
import { EmptyState, SchemaError, StaleBanner } from "../../components/states";
import { TierBadge } from "../../components/TierBadge";
import { TIER_COLOR_VAR, TIER_LABELS, type Tier } from "../../lib/tier";
import { colorTokens, contrastRatio, colors, typeScale } from "../../theme";

const TIERS: { tier: Tier; rule: string }[] = [
  { tier: "common", rule: "undecided, edge <= 0" },
  { tier: "rare", rule: "undecided, edge > 0" },
  { tier: "legendary", rule: "verdict edge" },
  { tier: "cursed", rule: "verdict no_edge" },
];

/** Dev-only: every token, tier badge and the type scale in one place. Sample values only. */
export default function KitchenSinkScreen() {
  const [v, setV] = useState(0.912);
  const [staleSample] = useState(() => new Date(Date.now() - 42 * 60 * 1000));
  return (
    <div className="screen-stack">
      <p className="dim">Dev-only reference. All numbers on this page are made-up samples.</p>

      <section className="section">
        <h2 className="section-title">Color tokens</h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Swatch</th>
              <th>Token</th>
              <th>Value</th>
              <th>Use</th>
              <th className="num">vs bg</th>
              <th className="num">vs panel</th>
            </tr>
          </thead>
          <tbody>
            {colorTokens.map((t) => (
              <tr key={t.name}>
                <td>
                  <span className="swatch" style={{ background: `var(--${t.name})` }} />
                </td>
                <td>--{t.name}</td>
                <td>{t.value}</td>
                <td className="dim">{t.use}</td>
                <td className="num">{t.text ? contrastRatio(t.value, colors.bg).toFixed(2) : "-"}</td>
                <td className="num">
                  {t.text ? contrastRatio(t.value, colors.panel).toFixed(2) : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="section">
        <h2 className="section-title">Verdict tiers</h2>
        <div className="row-wrap">
          {TIERS.map(({ tier }) => (
            <LoreCard
              key={tier}
              title={`Sample ${TIER_LABELS[tier]}`}
              tier={tier}
              stats={[
                { label: "win rate", value: "91.2%" },
                { label: "break-even", value: "91.0%" },
                { label: "edge", value: "+0.2c" },
                { label: "sample size", value: "40 trades" },
              ]}
            >
              <TierBadge tier={tier} />
            </LoreCard>
          ))}
        </div>
        <ul className="plain-list">
          {TIERS.map(({ tier, rule }) => (
            <li key={tier}>
              <span style={{ color: TIER_COLOR_VAR[tier] }}>{TIER_LABELS[tier]}</span>{" "}
              <span className="dim">{rule}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="section">
        <h2 className="section-title">Type scale</h2>
        {typeScale.map((s) => (
          <div key={s.name} style={{ fontSize: `var(--fs-${s.name})` }}>
            fs-{s.name} ({s.factor}x) <span className="num">0123456789 91.0%</span>{" "}
            <span className="dim">{s.use}</span>
          </div>
        ))}
        <div className="pixel-font">PIXEL FONT: PANE TITLES</div>
      </section>

      <section className="section">
        <h2 className="section-title">Pixel icons</h2>
        <div className="row-wrap">
          {PIXEL_ICON_NAMES.map((n) => (
            <span key={n} className="icon-sample">
              <PixelIcon name={n} /> <span className="dim">{n}</span>
            </span>
          ))}
        </div>
      </section>

      <section className="section">
        <h2 className="section-title">Value flash</h2>
        <button type="button" onClick={() => setV((x) => x + 0.001)}>
          up
        </button>{" "}
        <button type="button" onClick={() => setV((x) => x - 0.001)}>
          down
        </button>{" "}
        <FlashValue value={v} format={(x) => `${(x * 100).toFixed(1)}%`} />
      </section>

      <section className="section">
        <h2 className="section-title">States</h2>
        <StaleBanner newest={staleSample} source="sample.db" />
        <EmptyState
          title="Sample empty state"
          reason="Explains why nothing is shown."
          commands={["make demo", "uv run nem export"]}
        />
        <SchemaError
          file="export/summary.json"
          field="schema_version"
          expectedVersion="1"
          foundVersion="2"
        />
      </section>
    </div>
  );
}
