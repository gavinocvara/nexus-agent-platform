import { create } from "zustand";

import type { ConnectionState } from "../data/api";
import type {
  ReplayCatalog,
  ReplayEpisode,
  Snapshot,
  StreamSignal,
} from "../data/types";
import { STOPS } from "../director/systems";

export type Mode = "live" | "replay";
export type View = "space" | "data" | "activity";
export type Quality = "high" | "medium" | "low";

export interface StageInfo {
  index: number;
  stage: string;
  label: string;
  kind: "phase" | "owner" | "publication";
  source: "replay" | "recorded" | "live";
}

export interface ReplayState {
  catalog: ReplayCatalog | null;
  episode: ReplayEpisode | null;
  playing: boolean;
  speed: number;
  /** performance.now() when playback last (re)started. */
  startedAt: number;
  /** Seconds of episode time accumulated before startedAt. */
  offset: number;
  ownerVisible: boolean;
  ownerResolved: boolean;
}

export interface LiveEffects {
  /** A just-recorded cycle whose real transition order plays once. */
  playback: { cycleId: string; startedAt: number } | null;
  signals: Array<StreamSignal & { at: number }>;
}

interface CommandCenterState {
  snapshot: Snapshot | null;
  connection: ConnectionState;
  mode: Mode;
  view: View;
  stop: number;
  quality: Quality;
  reducedMotion: boolean;
  webgl: boolean;
  dismissedRequest: string | null;
  stage: StageInfo | null;
  replay: ReplayState;
  live: LiveEffects;
  announcement: string;
  setSnapshot: (snapshot: Snapshot) => void;
  setConnection: (state: ConnectionState) => void;
  receiveSignal: (signal: StreamSignal) => void;
  setMode: (mode: Mode) => void;
  setView: (view: View) => void;
  goTo: (stop: number) => void;
  step: (delta: number) => void;
  setQuality: (quality: Quality) => void;
  setEnvironment: (env: { reducedMotion: boolean; webgl: boolean }) => void;
  dismissRequest: (requestId: string) => void;
  setStage: (stage: StageInfo | null) => void;
  setReplayCatalog: (catalog: ReplayCatalog) => void;
  loadEpisode: (episode: ReplayEpisode) => void;
  playPause: () => void;
  restart: () => void;
  setSpeed: (speed: number) => void;
  seek: (seconds: number) => void;
  setOwnerFrame: (visible: boolean, resolved: boolean) => void;
  announce: (text: string) => void;
}

const now = () => performance.now();

export function replayTime(replay: ReplayState, at: number = now()): number {
  if (!replay.playing) return replay.offset;
  return replay.offset + ((at - replay.startedAt) / 1000) * replay.speed;
}

export const useStore = create<CommandCenterState>((set, get) => ({
  snapshot: null,
  connection: "connecting",
  mode: "live",
  view: "space",
  stop: 0,
  quality: "high",
  reducedMotion: false,
  webgl: true,
  dismissedRequest: null,
  stage: null,
  replay: {
    catalog: null,
    episode: null,
    playing: false,
    speed: 1,
    startedAt: 0,
    offset: 0,
    ownerVisible: false,
    ownerResolved: false,
  },
  live: { playback: null, signals: [] },
  announcement: "",
  setSnapshot: (snapshot) => set({ snapshot }),
  setConnection: (connection) => set({ connection }),
  receiveSignal: (signal) => {
    const at = now();
    const live = get().live;
    const signals = [...live.signals, { ...signal, at }].slice(-40);
    let playback = live.playback;
    if (signal.kind === "cycle.recorded") playback = { cycleId: signal.subject, startedAt: at };
    set({ live: { playback, signals } });
    const text = describeSignal(signal);
    if (text) set({ announcement: text });
  },
  setMode: (mode) => {
    if (mode === get().mode) return;
    set({
      mode,
      stage: null,
      replay: { ...get().replay, playing: false, offset: 0, ownerVisible: false, ownerResolved: false },
      announcement: mode === "replay" ? "Replay mode. Recorded evaluation episodes, not live activity." : "Live mode.",
    });
  },
  setView: (view) => set({ view }),
  goTo: (stop) => set({ stop: Math.max(0, Math.min(STOPS.length - 1, stop)) }),
  step: (delta) => get().goTo(get().stop + delta),
  setQuality: (quality) => set({ quality }),
  setEnvironment: ({ reducedMotion, webgl }) => set({ reducedMotion, webgl }),
  dismissRequest: (requestId) => set({ dismissedRequest: requestId }),
  setStage: (stage) => set({ stage }),
  setReplayCatalog: (catalog) => set({ replay: { ...get().replay, catalog } }),
  loadEpisode: (episode) =>
    set({
      replay: {
        ...get().replay,
        episode,
        playing: true,
        startedAt: now(),
        offset: 0,
        ownerVisible: false,
        ownerResolved: false,
      },
      stage: null,
      announcement: `Replay: ${episode.title}.`,
    }),
  playPause: () => {
    const replay = get().replay;
    if (replay.playing) {
      set({ replay: { ...replay, playing: false, offset: replayTime(replay) } });
    } else {
      set({ replay: { ...replay, playing: true, startedAt: now() } });
    }
  },
  restart: () =>
    set({
      replay: { ...get().replay, playing: true, startedAt: now(), offset: 0, ownerVisible: false, ownerResolved: false },
    }),
  setSpeed: (speed) => {
    const replay = get().replay;
    set({ replay: { ...replay, speed, offset: replayTime(replay), startedAt: now() } });
  },
  seek: (seconds) => {
    const replay = get().replay;
    set({ replay: { ...replay, offset: Math.max(0, seconds), startedAt: now() } });
  },
  setOwnerFrame: (ownerVisible, ownerResolved) => {
    const replay = get().replay;
    if (replay.ownerVisible === ownerVisible && replay.ownerResolved === ownerResolved) return;
    set({ replay: { ...replay, ownerVisible, ownerResolved } });
  },
  announce: (announcement) => set({ announcement }),
}));

function describeSignal(signal: StreamSignal): string | null {
  switch (signal.kind) {
    case "run.started":
      return "A resident engineer cycle started.";
    case "run.ended":
      return "The resident engineer cycle ended.";
    case "cycle.recorded":
      return `Cycle recorded: ${signal.detail ?? "decision unknown"}.`;
    case "decision.pending":
      return "NEXUS is waiting on an owner decision.";
    case "decision.recorded":
      return `Owner decision recorded: ${signal.detail ?? ""}.`;
    case "publication.recorded":
      return `Draft pull request ${signal.subject} recorded.`;
    case "health.changed":
      return `Lab health changed to ${signal.subject}.`;
    default:
      return null;
  }
}
