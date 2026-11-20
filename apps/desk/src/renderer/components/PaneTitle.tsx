import type { ReactNode } from "react";
import { PixelIcon, type PixelIconName } from "./PixelIcon";

/** Pane title bar: pixel font (<= 24 chars) and an optional icon, fixed height. */
export function PaneTitle({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: PixelIconName;
  children?: ReactNode;
}) {
  return (
    <header className="pane-title">
      {icon !== undefined && <PixelIcon name={icon} />}
      <h1 className="pixel-font pane-title-text">{title.slice(0, 24)}</h1>
      <div className="pane-title-actions">{children}</div>
    </header>
  );
}
