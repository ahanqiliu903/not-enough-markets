import { useEffect, useMemo, useRef, useState } from "react";
import { fuzzyFilter } from "../lib/fuzzy";
import { comboLabel, KEY_PRIORITY, useKeys } from "../lib/keys";
import { exploreQueryCommand } from "../screens/explore/commands";
import { useCommandStore, type Command } from "../state/commands";
import { useNav } from "../state/nav";

const MAX_RESULTS = 50;

/** Cmd/Ctrl+K palette: fuzzy search over every registered command. */
export function CommandPalette() {
  const open = useNav((s) => s.paletteOpen);
  if (!open) return null;
  return <PaletteDialog />;
}

function PaletteDialog() {
  const setOpen = useNav((s) => s.setPaletteOpen);
  const commands = useCommandStore((s) => s.commands);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreFocus = useRef<Element | null>(null);

  useEffect(() => {
    restoreFocus.current = document.activeElement;
    inputRef.current?.focus();
    return () => {
      const el = restoreFocus.current;
      if (el instanceof HTMLElement && el.isConnected) el.focus();
    };
  }, []);

  const results = useMemo(() => {
    const found = fuzzyFilter([...commands.values()], query, (c) => `${c.group} ${c.title} ${c.keywords ?? ""}`);
    // "explore SERIES" opens any series, not only the seeded ones.
    const typed = exploreQueryCommand(query);
    const exact = typed !== null ? found.find((c) => c.title.toLowerCase() === typed.title.toLowerCase()) : undefined;
    const first = exact ?? typed;
    const all = first !== null ? [first, ...found.filter((c) => c !== first)] : found;
    return all.slice(0, MAX_RESULTS);
  }, [commands, query]);
  const activeIndex = Math.min(active, Math.max(0, results.length - 1));

  const run = (c: Command | undefined) => {
    if (c === undefined) return;
    setOpen(false);
    c.run();
  };

  useKeys(
    {
      Escape: () => setOpen(false),
      ArrowDown: () => setActive((i) => Math.min(i + 1, results.length - 1)),
      ArrowUp: () => setActive((i) => Math.max(i - 1, 0)),
      "ctrl+n": () => setActive((i) => Math.min(i + 1, results.length - 1)),
      "ctrl+p": () => setActive((i) => Math.max(i - 1, 0)),
      Enter: () => run(results[activeIndex]),
      "mod+k": () => setOpen(false),
    },
    { priority: KEY_PRIORITY.modal, modal: true, inInputs: true },
  );

  const listId = "palette-results";
  return (
    <div className="palette-scrim" onMouseDown={() => setOpen(false)}>
      <div
        className="palette pixel-border"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="palette-input"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={results[activeIndex] ? `cmd-${results[activeIndex].id}` : undefined}
          aria-label="Search commands"
          placeholder="Jump to screen, portfolio, strategy, or run a command"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
        />
        <ul className="palette-list" role="listbox" id={listId}>
          {results.length === 0 && <li className="palette-empty dim">No matching commands</li>}
          {results.map((c, i) => (
            <li
              key={c.id}
              id={`cmd-${c.id}`}
              role="option"
              aria-selected={i === activeIndex}
              className={`palette-item${i === activeIndex ? " active" : ""}`}
              onMouseMove={() => setActive(i)}
              onClick={() => run(c)}
            >
              <span className="palette-group dim">{c.group}</span>
              <span className="palette-title">{c.title}</span>
              {c.shortcut !== undefined && (
                <kbd className="palette-shortcut">{comboLabel(c.shortcut)}</kbd>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
