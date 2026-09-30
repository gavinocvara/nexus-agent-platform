import type { CycleView, ReplayCatalog, ReplayEpisode, Snapshot, StreamSignal } from "./types";

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return (await response.json()) as T;
}

export const api = {
  snapshot: (signal?: AbortSignal) => getJson<Snapshot>("/api/v1/snapshot", signal),
  cycle: (id: string, signal?: AbortSignal) =>
    getJson<CycleView>(`/api/v1/cycles/${encodeURIComponent(id)}`, signal),
  replayCatalog: (signal?: AbortSignal) => getJson<ReplayCatalog>("/api/v1/replay", signal),
  replayEpisode: (name: string, signal?: AbortSignal) =>
    getJson<ReplayEpisode>(`/api/v1/replay/${encodeURIComponent(name)}`, signal),
};

export type ConnectionState = "connecting" | "open" | "reconnecting" | "offline";

export interface StreamHandlers {
  onSnapshot: (snapshot: Snapshot) => void;
  onSignal: (signal: StreamSignal) => void;
  onState: (state: ConnectionState) => void;
}

/**
 * Server-to-client only. EventSource reconnects by itself (the server sends `retry`);
 * after repeated failures we back off and fall back to one snapshot fetch per attempt.
 */
export function openStream(handlers: StreamHandlers): () => void {
  let source: EventSource | null = null;
  let closed = false;
  let failures = 0;
  let timer: number | undefined;

  const connect = () => {
    if (closed) return;
    handlers.onState(failures === 0 ? "connecting" : "reconnecting");
    source = new EventSource("/api/v1/stream");
    source.addEventListener("open", () => {
      failures = 0;
      handlers.onState("open");
    });
    source.addEventListener("snapshot", (event) => {
      handlers.onSnapshot(JSON.parse((event as MessageEvent<string>).data) as Snapshot);
    });
    source.addEventListener("signal", (event) => {
      handlers.onSignal(JSON.parse((event as MessageEvent<string>).data) as StreamSignal);
    });
    source.addEventListener("error", () => {
      if (source?.readyState === EventSource.CLOSED) {
        source.close();
        failures += 1;
        handlers.onState(failures > 3 ? "offline" : "reconnecting");
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(failures, 5));
        timer = window.setTimeout(() => {
          api
            .snapshot()
            .then(handlers.onSnapshot)
            .catch(() => undefined)
            .finally(connect);
        }, delay);
      } else {
        handlers.onState("reconnecting");
      }
    });
  };

  connect();
  return () => {
    closed = true;
    if (timer !== undefined) window.clearTimeout(timer);
    source?.close();
  };
}
