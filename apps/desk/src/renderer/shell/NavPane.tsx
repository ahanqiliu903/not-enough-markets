import { useRef } from "react";
import { PixelIcon } from "../components/PixelIcon";
import { comboLabel } from "../lib/keys";
import type { RouteDef } from "../routes";
import { useNav } from "../state/nav";
import { NAV_WIDTH_MAX, NAV_WIDTH_MIN, useSettings } from "../state/settings";

export function NavPane({ routes }: { routes: readonly RouteDef[] }) {
  const screen = useNav((s) => s.screen);
  const navigate = useNav((s) => s.navigate);
  return (
    <nav className="nav-pane" aria-label="Screens">
      <ul>
        {routes.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              className={`nav-item${r.id === screen ? " active" : ""}`}
              aria-current={r.id === screen ? "page" : undefined}
              onClick={() => navigate(r.id)}
            >
              <span className="nav-icon">
                {r.icon !== undefined && <PixelIcon name={r.icon} />}
              </span>
              <span className="nav-label">{r.title}</span>
              {r.hotkey !== undefined && (
                <kbd className="nav-key dim">{comboLabel(`mod+${r.hotkey}`)}</kbd>
              )}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** Draggable, keyboard-adjustable divider between nav and main pane. */
export function Splitter() {
  const width = useSettings((s) => s.navWidth);
  const setWidth = useSettings((s) => s.setNavWidth);
  const drag = useRef<{ x: number; w: number } | null>(null);
  return (
    <div
      className="splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize navigation"
      aria-valuemin={NAV_WIDTH_MIN}
      aria-valuemax={NAV_WIDTH_MAX}
      aria-valuenow={width}
      tabIndex={0}
      onPointerDown={(e) => {
        drag.current = { x: e.clientX, w: width };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (drag.current !== null) setWidth(drag.current.w + e.clientX - drag.current.x);
      }}
      onPointerUp={() => {
        drag.current = null;
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 40 : 10;
        if (e.key === "ArrowLeft") setWidth(width - step);
        else if (e.key === "ArrowRight") setWidth(width + step);
        else return;
        e.preventDefault();
      }}
    />
  );
}
