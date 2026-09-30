import { beforeEach, describe, expect, it } from "vitest";

import type { ActiveRunView } from "../data/types";
import { liveRunFrame, resetLivePhase } from "./director";
import { evaluate } from "./timeline";

const base = () => evaluate({ beats: [], duration: 0, owner: null }, 0);

function run(overrides: Partial<ActiveRunView>): ActiveRunView {
  return {
    cycle_id: "c1",
    started_at: "2026-09-30T12:00:00Z",
    lease_expires_at: "2026-09-30T13:00:00Z",
    phase: null,
    phase_source: "unobservable",
    sequence: null,
    stage: null,
    description: null,
    phase_updated_at: null,
    executor: null,
    actors: [],
    pipeline: [],
    ...overrides,
  };
}

describe("liveRunFrame", () => {
  beforeEach(() => resetLivePhase());

  it("with only a lease, lights the engineer and nothing else", () => {
    const frame = liveRunFrame(run({}), base(), 0.2, 0);
    expect(frame.levels.resident_engineer).toBe(1);
    for (const id of ["patchforge", "sentinelqa", "memory", "aegisops"] as const) expect(frame.levels[id]).toBe(0);
    expect(frame.pulses.every((pulse) => pulse.target === "resident_engineer")).toBe(true);
  });

  it("half-lights the executor pipeline and dispatches once per published phase", () => {
    const implement = run({
      phase: "implement",
      phase_source: "progress_file",
      sequence: 6,
      stage: "IMPLEMENT",
      executor: "patchforge",
      actors: ["resident_engineer"],
      pipeline: ["patchforge", "sentinelqa"],
    });
    const start = liveRunFrame(implement, base(), 0.2, 1000);
    expect(start.levels.patchforge).toBe(0.5);
    expect(start.levels.sentinelqa).toBe(0.5);
    const travelling = liveRunFrame(implement, base(), 0.2, 1000 + 0.6 * 1600);
    expect(travelling.pulses.some((pulse) => pulse.target === "patchforge")).toBe(true);
    expect(travelling.pulses.some((pulse) => pulse.target === "sentinelqa")).toBe(false);
    const later = liveRunFrame(implement, base(), 0.2, 1000 + 5000);
    expect(later.pulses.some((pulse) => pulse.target === "patchforge")).toBe(false);
  });

  it("flashes the core for policy phases instead of sending it a pulse", () => {
    const decide = run({ phase: "decide", sequence: 10, stage: "DECIDE", executor: "dry_run", actors: ["resident_engineer", "nexus"] });
    const frame = liveRunFrame(decide, base(), 0.2, 2000 + 800);
    expect(frame.levels.nexus).toBeGreaterThan(0);
    expect(frame.pulses.some((pulse) => (pulse.target as string) === "nexus")).toBe(false);
  });
});
