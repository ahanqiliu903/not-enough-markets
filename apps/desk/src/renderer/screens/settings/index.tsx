import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { EmptyState } from "../../components/states";
import { useSourcesState } from "../../data/hooks";
import { ConnectSource, DATA_COMMANDS } from "../../data/states";
import { nem } from "../../lib/api";
import { useSettings } from "../../state/settings";
import { FONT_SIZE_MAX, FONT_SIZE_MIN } from "../../theme";
import { VpsSync } from "./VpsSync";

/** A found DB inside the source shows relative to it; an attached one in full. */
function dbLabel(root: string, db: string | null, attached: boolean): string {
  if (db === null) return "no database";
  const shown = !attached && db.startsWith(`${root}/`) ? db.slice(root.length + 1) : db;
  return `DB: ${shown}${attached ? " (attached)" : ""}`;
}

/** Screen 8: data sources (DESK-004) and display settings. */
export default function SettingsScreen() {
  const { pixelAccents, fontSize, setPixelAccents, setFontSize } = useSettings();
  const sources = useSourcesState();
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries();
  const setActive = useMutation({ mutationFn: (id: string) => nem().sources.setActive(id), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => nem().sources.remove(id), onSuccess: refresh });
  const [dbMessage, setDbMessage] = useState<string | null>(null);
  const attach = useMutation({
    mutationFn: (id: string) => nem().sources.attachDb(id),
    onSuccess: (r) => {
      setDbMessage(r.ok || r.message === "cancelled" ? null : r.message);
      refresh();
    },
  });
  const detach = useMutation({ mutationFn: (id: string) => nem().sources.detachDb(id), onSuccess: refresh });

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
                    <td className="dim" data-testid={`source-db-${s.id}`}>
                      {dbLabel(s.path, sources.data.dbPaths?.[s.id] ?? null, s.dbPath !== undefined)}
                    </td>
                    <td>
                      {!active && (
                        <button type="button" onClick={() => setActive.mutate(s.id)}>
                          Use
                        </button>
                      )}{" "}
                      <button type="button" onClick={() => attach.mutate(s.id)} aria-label={`Attach database to ${s.label}`}>
                        Attach DB...
                      </button>{" "}
                      {s.dbPath !== undefined && (
                        <>
                          <button type="button" onClick={() => detach.mutate(s.id)} aria-label={`Detach database from ${s.label}`}>
                            Detach DB
                          </button>{" "}
                        </>
                      )}
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
        {dbMessage !== null && (
          <p role="alert" className="down">
            {dbMessage}
          </p>
        )}
        <p className="dim">
          The Market screen reads a source&apos;s snapshot database: <code>nem.db</code> or{" "}
          <code>data/snapshot/nem.db</code> inside the folder, or a file attached with &quot;Attach DB&quot;. Use a
          snapshot (<code>deploy/snapshot.sh</code>), never the live <code>data/nem.db</code>; it is opened read-only.
        </p>
        <ConnectSource />
      </section>

      <VpsSync />

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
