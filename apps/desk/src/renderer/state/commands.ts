import { useEffect } from "react";
import { create } from "zustand";

/**
 * Command registry behind the Cmd/Ctrl+K palette. The shell registers screen jumps and
 * global actions; later screens/data layer register entities (portfolios, strategies,
 * sources) with `useRegisterCommands`.
 */
export interface Command {
  id: string;
  title: string;
  group: "Screen" | "Portfolio" | "Strategy" | "Source" | "Action" | (string & {});
  /** Extra words the fuzzy matcher should see (not displayed). */
  keywords?: string;
  /** A key combo shown as a hint, e.g. "mod+1". */
  shortcut?: string;
  run: () => void;
}

interface CommandState {
  commands: Map<string, Command>;
  register: (cmds: readonly Command[]) => () => void;
}

export const useCommandStore = create<CommandState>()((set, get) => ({
  commands: new Map(),
  register: (cmds) => {
    const next = new Map(get().commands);
    for (const c of cmds) next.set(c.id, c);
    set({ commands: next });
    return () => {
      const after = new Map(get().commands);
      for (const c of cmds) if (after.get(c.id) === c) after.delete(c.id);
      set({ commands: after });
    };
  },
}));

/** Registers commands while mounted. Pass a memoized array to avoid re-registering. */
export function useRegisterCommands(cmds: readonly Command[]): void {
  const register = useCommandStore((s) => s.register);
  useEffect(() => register(cmds), [register, cmds]);
}
