import { TIER_LABELS, tier as tierOf, type Tier, type Verdict } from "../lib/tier";
import { PixelIcon } from "./PixelIcon";

type Props =
  | { tier: Tier; verdict?: never; edgeCents?: never }
  | { tier?: never; verdict: Verdict; edgeCents: number | null | undefined };

/**
 * Tier badge: always shows the text label, so color is never the only signal.
 * Fixed width and height, so swapping the pixel font for mono does not move anything.
 */
export function TierBadge(props: Props) {
  const t: Tier = props.tier ?? tierOf(props.verdict, props.edgeCents);
  const label = TIER_LABELS[t];
  return (
    <span className={`tier-badge tier-${t}`} data-tier={t} title={label}>
      <PixelIcon name={`tier-${t}`} size={12} />
      <span className="pixel-font tier-badge-label">{label}</span>
    </span>
  );
}
