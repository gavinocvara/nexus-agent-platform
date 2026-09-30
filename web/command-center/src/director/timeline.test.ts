import { describe, expect, it } from "vitest";

import type { CycleView, TransitionView } from "../data/types";
import { buildTimeline, evaluate } from "./timeline";

function transition(sequence: number, target: string, actors: TransitionView["actors"]): TransitionView {
  return {
    sequence,
    source: "x",
    target,
    stage: target.toUpperCase(),
    reason: `reason ${target}`,
    occurred_at: "2026-09-29T12:00:00Z",
    actors,
    evidence_note: null,
  };
}

function cycle(overrides: Partial<CycleView> = {}): CycleView {
  return {
    cycle_id: "c",
    mode: "propose",
    started_at: "2026-09-29T12:00:00Z",
    completed_at: "2026-09-29T12:00:00Z",
    phase_reached: "closed",
    decision: "no_work",
    failure: null,
    risk_level: null,
    selected_title: null,
    gates_passed: 0,
    gates_failed: 0,
    owner_action_required: false,
    published: false,
    repository_head: "0".repeat(40),
    model: null,
    transitions: [
      transition(1, "observe", ["resident_engineer"]),
      transition(2, "implement", ["patchforge"]),
      transition(3, "test", ["patchforge", "sentinelqa"]),
      transition(4, "decide", ["nexus"]),
    ],
    signals: [],
    candidates: [],
    risk: null,
    self_review: null,
    gates: [],
    change: null,
    decision_reasons: [],
    approval: null,
    budget: [],
    memory_reads: 0,
    memory_writes: 0,
    notifications: [],
    rollback_reason: null,
    sentinel: null,
    report_text: null,
    ...overrides,
  };
}

describe("buildTimeline", () => {
  it("creates one beat per recorded transition and nothing else", () => {
    const timeline = buildTimeline(cycle(), { step: 1 });
    expect(timeline.beats.map((beat) => beat.stage)).toEqual(["OBSERVE", "IMPLEMENT", "TEST", "DECIDE"]);
    expect(timeline.owner).toBeNull();
    expect(timeline.duration).toBe(4);
  });

  it("adds an owner beat only when the record asked the owner", () => {
    const approval = { question: "Ship it?" } as CycleView["approval"];
    const pending = buildTimeline(cycle({ approval }), { step: 1 });
    expect(pending.beats.at(-1)?.kind).toBe("owner");
    expect(pending.owner?.verdict).toBeNull();
    const decided = buildTimeline(cycle({ approval }), {
      step: 1,
      owner: { verdict: "ship", policy_outcome: "ship", note: "scripted" },
    });
    expect(decided.owner?.verdict).toBe("ship");
  });
});

describe("evaluate", () => {
  const timeline = buildTimeline(cycle(), { step: 1 });

  it("charges the core, travels a pulse, then lights the target", () => {
    const early = evaluate(timeline, 1.1);
    expect(early.beat?.stage).toBe("IMPLEMENT");
    expect(early.charge).toBeGreaterThan(0);
    expect(early.levels.patchforge).toBe(0);
    const travelling = evaluate(timeline, 1.4);
    expect(travelling.pulses.find((pulse) => pulse.target === "patchforge")?.progress).toBeGreaterThan(0);
    const lit = evaluate(timeline, 1.9);
    expect(lit.levels.patchforge).toBeGreaterThan(0.9);
  });

  it("never lights a system the record did not involve", () => {
    for (let t = 0; t < timeline.duration + 2; t += 0.05) {
      const frame = evaluate(timeline, t);
      expect(frame.levels.aegisops).toBe(0);
      expect(frame.levels.memory).toBe(0);
      expect(frame.levels.engram).toBe(0);
    }
  });

  it("decays after the beat and finishes", () => {
    const end = evaluate(timeline, timeline.duration + 3);
    expect(end.done).toBe(true);
    expect(end.levels.patchforge).toBeLessThan(0.01);
    expect(end.pulses).toHaveLength(0);
  });
});
