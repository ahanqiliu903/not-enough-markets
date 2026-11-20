import { useQuery } from "@tanstack/react-query";
import { EmptyState } from "../../components/states";
import { nem, queryKeys } from "../../lib/api";
import { useSettings } from "../../state/settings";
import { FONT_SIZE_MAX, FONT_SIZE_MIN } from "../../theme";

/** Screen 8: data sources (stub until DESK-003) and display settings. */
export default function SettingsScreen() {
  const { pixelAccents, fontSize, setPixelAccents, setFontSize } = useSettings();
  const sources = useQuery({ queryKey: queryKeys.sources, queryFn: () => nem().listSources() });

  return (
    <div className="screen-stack">
      <section className="section">
        <h2 className="section-title">Data sources</h2>
        {sources.isPending ? (
          <p className="dim">Loading sources...</p>
        ) : sources.isError ? (
          <p role="alert">Could not list data sources: {String(sources.error)}</p>
        ) : sources.data.length === 0 ? (
          <EmptyState
            title="No data sources"
            reason="NEM Desk reads files the engine exports. None are configured yet."
            commands={["make demo", "uv run nem export"]}
          />
        ) : (
          <ul className="plain-list">
            {sources.data.map((s) => (
              <li key={s.id}>
                {s.label} <span className="dim">({s.kind}) {s.path}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="section">
        <h2 className="section-title">Display</h2>
        <div className="form-row">
          <label htmlFor="pixel-accents">Pixel accents</label>
          <input
            id="pixel-accents"
            type="checkbox"
            checked={pixelAccents}
            onChange={(e) => setPixelAccents(e.target.checked)}
          />
          <span className="dim">{pixelAccents ? "on" : "off"}: pixel font and icons</span>
        </div>
        <div className="form-row">
          <label htmlFor="font-size">Font size</label>
          <input
            id="font-size"
            type="range"
            min={FONT_SIZE_MIN}
            max={FONT_SIZE_MAX}
            step={1}
            value={fontSize}
            onChange={(e) => setFontSize(Number(e.target.value))}
          />
          <span className="num">{fontSize}px</span>
        </div>
      </section>
    </div>
  );
}
