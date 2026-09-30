// Pure storytelling model: a cycle record becomes beats; a time becomes a frame.
// Nothing here invents activity: every beat is a transition the record contains, the
// owner question exists only when the record asked one, and a publication beat exists
// only when the record carries a publication.

import type { Actor, CycleView, OwnerStepView, SystemId, Verdict } from "../data/types";

export type BeatKind = "phase" | "owner" | "publication";

export interface Beat {
  index: number;
  kind: BeatKind;
  stage: string;
  label: string;
  start: number;
  end: number;
  actors: Actor[];
  note: string | null;
}

export interface Timeline {
  beats: Beat[];
  duration: number;
  owner: { start: number; resolveAt: number; verdict: Verdict | null; outcome: string | null } | null;
}

export interface TimelineOptions {
  step: number;
  owner?: OwnerStepView | null;
  /** A decision already on record for a live cycle. */
  recordedVerdict?: Verdict | null;
}

const OWNER_BEAT_STEPS = 3;

export function buildTimeline(cycle: CycleView, options: TimelineOptions): Timeline {
  const { step } = options;
  const beats: Beat[] = [];
  let clock = 0;
  for (const transition of cycle.transitions) {
    if (transition.target === "created") continue;
    beats.push({
      index: beats.length,
      kind: "phase",
      stage: transition.stage,
      label: transition.reason,
      start: clock,
      end: clock + step,
      actors: transition.actors,
      note: transition.evidence_note,
    });
    clock += step;
    if (transition.target === "decide" && cycle.change?.publication) {
      beats.push({
        index: beats.length,
        kind: "publication",
        stage: "PUBLISH",
        label: `Draft pull request #${cycle.change.publication.pull_request_number}`,
        start: clock,
        end: clock + step,
        actors: ["nexus"],
        note: "Draft only; a human reviews and merges.",
      });
      clock += step;
    }
  }
  let owner: Timeline["owner"] = null;
  if (cycle.approval) {
    const verdict = options.owner?.verdict ?? options.recordedVerdict ?? null;
    const duration = step * OWNER_BEAT_STEPS;
    beats.push({
      index: beats.length,
      kind: "owner",
      stage: "OWNER",
      label: cycle.approval.question,
      start: clock,
      end: clock + duration,
      actors: ["nexus"],
      note: verdict ? null : "Waiting on the owner; silence is never approval.",
    });
    owner = {
      start: clock,
      resolveAt: clock + duration * 0.62,
      verdict,
      outcome: options.owner?.policy_outcome ?? null,
    };
    clock += duration;
  }
  return { beats, duration: clock, owner };
}

export interface Pulse {
  target: Actor;
  /** 0 at the core surface, 1 at the target orb. */
  progress: number;
  strength: number;
}

export interface Frame {
  beat: Beat | null;
  levels: Record<SystemId, number>;
  pulses: Pulse[];
  /** Blue energy building inside the core before a dispatch. */
  charge: number;
  /** Core flash for policy, report, and owner beats. */
  coreFlash: number;
  ownerVisible: boolean;
  ownerResolved: boolean;
  done: boolean;
}

export function emptyLevels(): Record<SystemId, number> {
  return {
    nexus: 0,
    aegisops: 0,
    patchforge: 0,
    sentinelqa: 0,
    resident_engineer: 0,
    memory: 0,
    engram: 0,
  };
}

const smooth = (edge0: number, edge1: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
};

const DECAY_SECONDS = 1.4;

/** The frame at time ``t`` seconds; stateless, so scrubbing and tests are exact. */
export function evaluate(timeline: Timeline, t: number): Frame {
  const levels = emptyLevels();
  const pulses: Pulse[] = [];
  let charge = 0;
  let coreFlash = 0;
  let current: Beat | null = null;
  for (const beat of timeline.beats) {
    const length = beat.end - beat.start;
    if (t >= beat.start && t < beat.end) current = beat;
    if (t < beat.start) continue;
    const u = (t - beat.start) / length;
    const after = t - beat.end;
    const decay = after <= 0 ? 1 : Math.exp(-after / (DECAY_SECONDS / 3));
    for (const actor of beat.actors) {
      if (actor === "nexus") {
        const flash = after <= 0 ? Math.sin(Math.PI * Math.min(1, u)) : 0;
        coreFlash = Math.max(coreFlash, flash);
        levels.nexus = Math.max(levels.nexus, (after <= 0 ? 1 : decay) * 0.8);
        continue;
      }
      if (after <= 0) {
        charge = Math.max(charge, smooth(0, 0.22, u) * (1 - smooth(0.3, 0.5, u)));
        const progress = (u - 0.18) / 0.42;
        if (progress > 0 && progress < 1.08) {
          pulses.push({ target: actor, progress: Math.min(progress, 1), strength: 1 });
        }
      }
      const lit = smooth(0.52, 0.66, u) * decay;
      levels[actor] = Math.max(levels[actor], lit);
    }
  }
  const owner = timeline.owner;
  const ownerVisible = owner !== null && t >= owner.start;
  const ownerResolved = owner !== null && owner.verdict !== null && t >= owner.resolveAt;
  return {
    beat: current,
    levels,
    pulses,
    charge,
    coreFlash,
    ownerVisible,
    ownerResolved,
    done: t >= timeline.duration,
  };
}
