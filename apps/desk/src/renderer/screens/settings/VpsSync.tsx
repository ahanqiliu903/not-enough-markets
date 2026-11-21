import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { DEFAULT_REMOTE_BASE, type VpsConfig, type VpsError, type VpsState } from "@shared/vps";
import { nem, queryKeys } from "../../lib/api";
import "./vps.css";

/**
 * DESK-013: VPS sync settings. Stores host, user, port, key *path* and remote folder in the
 * app's userData (never a password or key contents), and pulls `data/snapshot/nem.db` and
 * `out/` with the system ssh + rsync. Unknown or changed host keys fail closed.
 */

const INTERVALS = [null, 5, 15, 30, 60] as const;

interface Form {
  host: string;
  user: string;
  port: string;
  keyPath: string;
  remoteBase: string;
  interval: string;
}

function toForm(c: VpsConfig | null): Form {
  return {
    host: c?.host ?? "",
    user: c?.user ?? "",
    port: String(c?.port ?? 22),
    keyPath: c?.keyPath ?? "",
    remoteBase: c?.remoteBase ?? DEFAULT_REMOTE_BASE,
    interval: c?.intervalMinutes === null || c?.intervalMinutes === undefined ? "" : String(c.intervalMinutes),
  };
}

function ErrorLine({ error }: { error: VpsError }) {
  return (
    <div role="alert" className="vps-error" data-kind={error.kind}>
      <span className="down">{error.message}</span>
      {error.detail !== undefined && error.detail !== "" && <pre className="vps-detail">{error.detail}</pre>}
    </div>
  );
}

export function VpsSync() {
  const client = useQueryClient();
  const q = useQuery({ queryKey: queryKeys.vps, queryFn: () => nem().sources.vps.get() });
  if (q.isPending) return <p className="dim">Loading VPS settings...</p>;
  if (q.isError) return <p role="alert">Could not read VPS settings: {String(q.error)}</p>;
  return (
    <VpsForm
      key={JSON.stringify(q.data.config)}
      state={q.data}
      onState={(s) => client.setQueryData(queryKeys.vps, s)}
      onSynced={() => void client.invalidateQueries()}
    />
  );
}

function VpsForm({ state, onState, onSynced }: { state: VpsState; onState: (s: VpsState) => void; onSynced: () => void }) {
  const [form, setForm] = useState<Form>(() => toForm(state.config));
  const [error, setError] = useState<VpsError | null>(null);
  const set = (k: keyof Form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  const config = (): VpsConfig => ({
    host: form.host.trim(),
    user: form.user.trim(),
    port: Number(form.port),
    keyPath: form.keyPath.trim(),
    remoteBase: form.remoteBase.trim(),
    intervalMinutes: form.interval === "" ? null : Number(form.interval),
  });

  const save = useMutation({
    mutationFn: () => nem().sources.vps.save(config()),
    onSuccess: (r) => {
      setError(r.ok ? null : r.error);
      if (r.ok) onState(r.state);
    },
  });
  const sync = useMutation({
    mutationFn: async () => {
      const saved = await nem().sources.vps.save(config());
      if (!saved.ok) return { saved };
      return { saved, result: await nem().sources.vps.sync() };
    },
    onSuccess: ({ saved, result }) => {
      setError(saved.ok ? null : saved.error);
      if (result !== undefined) {
        onState(result.state);
        onSynced();
      }
    },
  });
  const forget = useMutation({ mutationFn: () => nem().sources.vps.forget(), onSuccess: onState });
  const pick = useMutation({
    mutationFn: () => nem().sources.vps.pickKey(),
    onSuccess: (p) => {
      if (p !== null) setForm({ ...form, keyPath: p });
    },
  });

  const last = state.lastSync;
  const busy = sync.isPending || state.syncing;
  const field = (id: keyof Form, label: string, input: ReactNode) => (
    <div className="form-row">
      <label htmlFor={`vps-${id}`}>{label}</label>
      {input}
      {error?.field === (id === "interval" ? "intervalMinutes" : id) && <span className="down">{error.message}</span>}
    </div>
  );

  return (
    <section className="section vps-sync" aria-label="VPS sync">
      <h2 className="section-title">VPS sync</h2>
      <p className="dim vps-cache">
        Pulls <code>data/snapshot/nem.db</code> and <code>out/</code> from your server with the system{" "}
        <code>ssh</code> and <code>rsync</code> into <code>{state.cacheDir || "the app's cache"}</code>, then adds it
        as the &quot;VPS&quot; source. The server&apos;s host key must already be in your <code>known_hosts</code>{" "}
        (connect once with <code>ssh</code> in a terminal and check the fingerprint); an unknown or changed key stops
        the sync. Only these fields are stored, never a password or key contents. Keys with a passphrase need{" "}
        <code>ssh-add</code> first.
      </p>
      {field("host", "Host", <input id="vps-host" value={form.host} onChange={set("host")} placeholder="nem.example.org" spellCheck={false} />)}
      {field("user", "User", <input id="vps-user" value={form.user} onChange={set("user")} placeholder="root" spellCheck={false} />)}
      {field("port", "Port", <input id="vps-port" value={form.port} onChange={set("port")} inputMode="numeric" size={6} />)}
      {field(
        "keyPath",
        "Key path",
        <>
          <input id="vps-keyPath" value={form.keyPath} onChange={set("keyPath")} placeholder="/Users/you/.ssh/id_ed25519" spellCheck={false} size={40} />
          <button type="button" onClick={() => pick.mutate()}>
            Choose...
          </button>
        </>,
      )}
      {field("remoteBase", "Remote folder", <input id="vps-remoteBase" value={form.remoteBase} onChange={set("remoteBase")} spellCheck={false} size={40} />)}
      {field(
        "interval",
        "Auto sync",
        <select id="vps-interval" value={form.interval} onChange={set("interval")}>
          {INTERVALS.map((m) => (
            <option key={m ?? "off"} value={m ?? ""}>
              {m === null ? "off (Sync now only)" : `every ${m} min`}
            </option>
          ))}
        </select>,
      )}
      <div className="row-wrap">
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending}>
          Save
        </button>
        <button type="button" onClick={() => sync.mutate()} disabled={busy}>
          {busy ? "Syncing..." : "Sync now"}
        </button>
        {state.config !== null && (
          <button type="button" onClick={() => forget.mutate()}>
            Forget server
          </button>
        )}
        {save.isSuccess && save.data.ok && <span className="dim">Saved.</span>}
      </div>
      {error !== null && error.field === undefined && <ErrorLine error={error} />}
      {last !== null && (
        <div className="vps-last" data-testid="vps-last">
          <p className={last.ok ? "up" : "dim"}>
            Last sync {last.at}: {last.ok ? "ok" : "failed"}
            {last.steps.length > 0 && (
              <span className="dim">
                {" "}
                ({last.steps.map((s) => `${s.what === "db" ? "nem.db" : "out/"} ${s.ok ? "ok" : "failed"}`).join(", ")})
              </span>
            )}
          </p>
          {last.error !== undefined && <ErrorLine error={last.error} />}
        </div>
      )}
    </section>
  );
}
