import { useMutation, useQueryClient } from "@tanstack/react-query";
import { EmptyState } from "../../components/states";
import { useSourcesState } from "../../data/hooks";
import { ConnectSource, DATA_COMMANDS } from "../../data/states";
import { nem } from "../../lib/api";
import { useSettings } from "../../state/settings";
import { FONT_SIZE_MAX, FONT_SIZE_MIN } from "../../theme";

/** Screen 8: data sources (DESK-004) and display settings. */
export default function SettingsScreen() {
  const { pixelAccents, fontSize, setPixelAccents, setFontSize } = useSettings();
  const sources = useSourcesState();
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries();
  const setActive = useMutation({ mutationFn: (id: string) => nem().sources.setActive(id), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => nem().sources.remove(id), onSuccess: refresh });

  return (
    <div className="screen-stack">
      <section className="section" aria-label="Data sources">
        <h2 className="section-title">Data sources</h2>
        <p className="dim">
          A source is a folder of engine output: an <code>out/</code> folder or its parent (e.g.{" "}
          <code>~/nem-cache</code> holding an rsynced <code>out/</code>). The list is kept in the
          app&apos;s own settings; the folders are only read.
        </p>
        {sources.isPending ? (
          <p className="dim">Loading sources...</p>
        ) : sources.isError ? (
          <p role="alert">Could not list data sources: {String(sources.error)}</p>
        ) : sources.data.sources.length === 0 ? (
          <EmptyState
            title="No data sources"
            reason="NEM Desk reads files the engine exports. None are configured yet."
            commands={DATA_COMMANDS}
          />
        ) : (
          <table className="grid" aria-label="Sources">
            <tbody>
              {sources.data.sources.map((s) => {
                const active = s.id === sources.data.activeId;
                return (
                  <tr key={s.id} className={active ? "selected" : ""}>
                    <td>{active ? "active" : ""}</td>
                    <td>{s.label}</td>
                    <td className="dim">{s.path}</td>
                    <td>
                      {!active && (
                        <button type="button" onClick={() => setActive.mutate(s.id)}>
                          Use
                        </button>
                      )}{" "}
                      <button type="button" onClick={() => remove.mutate(s.id)} aria-label={`Remove ${s.label}`}>
                        Remove
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <ConnectSource />
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
