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
  body,
  children,
}: {
  title: string;
  /** Verdict tier colors the title. Omit for non-verdict cards (e.g. market rules): amber. */
  tier?: Tier;
  stats: readonly LoreStat[];
  /** Optional free text under the stat lines (e.g. settlement rules). */
  body?: ReactNode;
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
          <span
            className="lore-title pixel-font"
            style={{ color: tier !== undefined ? TIER_COLOR_VAR[tier] : "var(--amber)" }}
          >
            {title}
          </span>
          {stats.map((s) => (
            <span key={s.label} className="lore-stat">
              <span className="lore-stat-label">{s.label}</span>
              <span className="num">{s.value}</span>
            </span>
          ))}
          {body !== undefined && <span className="lore-body">{body}</span>}
        </span>
      )}
    </span>
  );
}
