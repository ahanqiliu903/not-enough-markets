import { useId, useState, type ReactNode } from "react";
import { TIER_COLOR_VAR, type Tier } from "../lib/tier";

export interface LoreStat {
  label: string;
  value: string;
}

/**
 * Lore-card tooltip: dark card, tier-colored title, stat lines (e.g. win rate, break-even,
 * edge, sample size). Values arrive pre-formatted from the engine's export; this component
 * never computes statistics. Opens on hover and on keyboard focus.
 */
export function LoreCard({
  title,
  tier,
  stats,
  children,
}: {
  title: string;
  tier: Tier;
  stats: readonly LoreStat[];
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span
      className="lore-anchor"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={(e) => {
        if (e.key === "Escape") setOpen(false);
      }}
    >
      <span tabIndex={0} aria-describedby={open ? id : undefined} className="lore-trigger">
        {children}
      </span>
      {open && (
        <span role="tooltip" id={id} className="lore-card pixel-border">
          <span className="lore-title pixel-font" style={{ color: TIER_COLOR_VAR[tier] }}>
            {title}
          </span>
          {stats.map((s) => (
            <span key={s.label} className="lore-stat">
              <span className="lore-stat-label">{s.label}</span>
              <span className="num">{s.value}</span>
            </span>
          ))}
        </span>
      )}
    </span>
  );
}
