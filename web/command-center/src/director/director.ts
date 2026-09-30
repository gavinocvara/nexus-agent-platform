// Composes the per-frame scene state from the store. Called once per rendered frame by the
// scene's director; everything else reads `frame` without triggering React renders.

import type { Snapshot } from "../data/types";
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
    // A lease proves a cycle is in flight, nothing more: the engineer is lit and the
    // conduit carries a slow heartbeat. Other systems stay dark unless the phase is known.
    const levels = { ...result.levels, resident_engineer: 1 };
    const phase = run.phase;
    if (phase === "implement" || phase === "test") levels.patchforge = 1;
    if (phase === "test") levels.sentinelqa = Math.max(levels.sentinelqa, 0.8);
    const cycle = (elapsed % LIVE_PULSE_PERIOD) / LIVE_PULSE_PERIOD;
    const progress = (cycle - 0.15) / 0.45;
    const pulses = [...result.pulses];
    if (progress > 0 && progress < 1) {
      pulses.push({ target: "resident_engineer", progress, strength: 0.7 });
    }
    const charge = Math.max(result.charge, 0.35 + 0.25 * Math.sin(elapsed * 2.1));
    result = { ...result, levels, pulses, charge };
    if (!stage) {
      stage = {
        index: -1,
        stage: phase ? phase.toUpperCase() : "IN FLIGHT",
        label: phase
          ? "Phase reported by the cycle's progress file"
          : "Cycle in flight; its phase becomes visible when the record is written",
        kind: "phase",
        source: "live",
      };
    }
  }
  copy(frame, result);
  frame.attention = snapshot?.pending_approval ? 1 : 0;
  publishStage(stage);
}

// Visual test hook: expose the read-only frame state when the page is captured.
if (typeof window !== "undefined" && new URLSearchParams(window.location.search).has("capture")) {
  (window as unknown as { __nexusFrame: typeof frame }).__nexusFrame = frame;
}
