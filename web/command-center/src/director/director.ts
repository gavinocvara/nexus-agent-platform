// Composes the per-frame scene state from the store. Called once per rendered frame by the
// scene's director; everything else reads `frame` without triggering React renders.

import type { ActiveRunView, Snapshot } from "../data/types";
import { replayTime, useStore, type StageInfo } from "../state/store";
import { buildTimeline, emptyLevels, evaluate, type Frame, type Timeline } from "./timeline";

export const REPLAY_STEP_SECONDS = 1.5;
export const PLAYBACK_STEP_SECONDS = 0.55;
const LIVE_PULSE_PERIOD = 3.6;

export const frame: Frame & { attention: number; activeRun: boolean; mode: "live" | "replay" } = {
  beat: null,
  levels: emptyLevels(),
  pulses: [],
  charge: 0,
  coreFlash: 0,
  ownerVisible: false,
  ownerResolved: false,
  done: true,
  attention: 0,
  activeRun: false,
  mode: "live",
};

const cache = new WeakMap<object, Timeline>();

function timelineFor(key: object, build: () => Timeline): Timeline {
  let timeline = cache.get(key);
  if (!timeline) {
    timeline = build();
    cache.set(key, timeline);
  }
  return timeline;
}

function copy(target: typeof frame, source: Frame) {
  target.beat = source.beat;
  target.levels = source.levels;
  target.pulses = source.pulses;
  target.charge = source.charge;
  target.coreFlash = source.coreFlash;
  target.ownerVisible = source.ownerVisible;
  target.ownerResolved = source.ownerResolved;
  target.done = source.done;
}

let lastStageKey = "";

function publishStage(stage: StageInfo | null) {
  const key = stage ? `${stage.source}:${stage.index}:${stage.stage}` : "";
  if (key === lastStageKey) return;
  lastStageKey = key;
  useStore.getState().setStage(stage);
}

export function updateFrame(elapsed: number, at: number): void {
  const state = useStore.getState();
  frame.mode = state.mode;
  if (state.mode === "replay") {
    const episode = state.replay.episode;
    if (!episode) {
      copy(frame, evaluate({ beats: [], duration: 0, owner: null }, 0));
      frame.attention = 0;
      frame.activeRun = false;
      publishStage(null);
      return;
    }
    const timeline = timelineFor(episode, () =>
      buildTimeline(episode.cycle, { step: REPLAY_STEP_SECONDS, owner: episode.owner_step }),
    );
    const t = replayTime(state.replay, at);
    const result = evaluate(timeline, t);
    copy(frame, result);
    frame.attention = result.ownerVisible && !result.ownerResolved ? 1 : 0;
    frame.activeRun = false;
    state.setOwnerFrame(result.ownerVisible, result.ownerResolved);
    publishStage(
      result.beat
        ? {
            index: result.beat.index,
            stage: result.beat.stage,
            label: result.beat.label,
            kind: result.beat.kind,
            source: "replay",
          }
        : null,
    );
    if (result.done && state.replay.playing) state.playPause();
    return;
  }
  liveFrame(state.snapshot, state.live.playback, elapsed, at);
}

// The phase the runtime last published, and when this page first saw it.
let livePhase: { cycleId: string; sequence: number; seenAt: number } | null = null;

export function resetLivePhase(): void {
  livePhase = null;
}
const DISPATCH_SECONDS = 1.6;

/**
 * A running cycle, drawn only from what the server derived from the runtime: the lease
 * lights the engineer; a published phase lights the systems its code path involves, and
 * each newly published phase is dispatched from the core once. Pipeline members (PatchForge
 * then SentinelQA inside one executor call) are half-lit: which one is working is unknown.
 */
export function liveRunFrame(run: ActiveRunView, base: Frame, elapsed: number, at: number): Frame {
  const levels = { ...base.levels, resident_engineer: 1 };
  const pulses = [...base.pulses];
  let charge = base.charge;
  let coreFlash = base.coreFlash;
  if (run.sequence !== null && run.phase !== null) {
    if (!livePhase || livePhase.cycleId !== run.cycle_id || livePhase.sequence !== run.sequence) {
      livePhase = { cycleId: run.cycle_id, sequence: run.sequence, seenAt: at };
    }
    const u = (at - livePhase.seenAt) / 1000 / DISPATCH_SECONDS;
    for (const actor of run.actors) {
      if (actor === "nexus") {
        levels.nexus = Math.max(levels.nexus, 0.8);
        coreFlash = Math.max(coreFlash, u < 1 ? Math.sin(Math.PI * u) : 0);
      } else {
        levels[actor] = Math.max(levels[actor], 1);
      }
    }
    for (const member of run.pipeline) levels[member] = Math.max(levels[member], 0.5);
    const targets = [...run.actors.filter((actor) => actor !== "nexus"), ...run.pipeline.slice(0, 1)];
    if (u < 1) {
      charge = Math.max(charge, Math.min(1, u / 0.25) * (1 - Math.min(1, Math.max(0, (u - 0.3) / 0.2))));
      const progress = (u - 0.2) / 0.55;
      if (progress > 0 && progress < 1) {
        for (const target of targets) pulses.push({ target, progress, strength: 1 });
      }
    }
  }
  // Heartbeat on the engineer's conduit while the lease is held.
  const beat = (elapsed % LIVE_PULSE_PERIOD) / LIVE_PULSE_PERIOD;
  const heartbeat = (beat - 0.15) / 0.45;
  if (heartbeat > 0 && heartbeat < 1) {
    pulses.push({ target: "resident_engineer", progress: heartbeat, strength: 0.6 });
  }
  charge = Math.max(charge, 0.3 + 0.2 * Math.sin(elapsed * 2.1));
  return { ...base, levels, pulses, charge, coreFlash };
}

function liveFrame(
  snapshot: Snapshot | null,
  playback: { cycleId: string; startedAt: number } | null,
  elapsed: number,
  at: number,
) {
  let result: Frame = evaluate({ beats: [], duration: 0, owner: null }, 0);
  let stage: StageInfo | null = null;
  const latest = snapshot?.latest_cycle ?? null;
  if (playback && latest && latest.cycle_id === playback.cycleId) {
    const timeline = timelineFor(latest, () =>
      buildTimeline(latest, { step: PLAYBACK_STEP_SECONDS }),
    );
    const t = (at - playback.startedAt) / 1000;
    if (t < timeline.duration + 2) {
      result = evaluate(timeline, t);
      if (result.beat) {
        stage = {
          index: result.beat.index,
          stage: result.beat.stage,
          label: result.beat.label,
          kind: result.beat.kind,
          source: "recorded",
        };
      }
    }
  }
  const run = snapshot?.active_run ?? null;
  frame.activeRun = run !== null;
  if (run) {
    result = liveRunFrame(run, result, elapsed, at);
    if (!stage) {
      stage = {
        index: run.sequence ?? -1,
        stage: run.stage ?? "IN FLIGHT",
        label:
          run.description ??
          "Cycle in flight; its phase is not published, so only the lease is shown",
        kind: "phase",
        source: "live",
      };
    }
  } else {
    livePhase = null;
  }
  copy(frame, result);
  frame.attention = snapshot?.pending_approval ? 1 : 0;
  publishStage(stage);
}

// Visual test hook: expose the read-only frame state when the page is captured.
if (typeof window !== "undefined" && new URLSearchParams(window.location.search).has("capture")) {
  (window as unknown as { __nexusFrame: typeof frame }).__nexusFrame = frame;
}
