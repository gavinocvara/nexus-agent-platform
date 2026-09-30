import type { Actor, SystemId } from "../data/types";

export interface SystemMeta {
  id: SystemId;
  name: string;
  short: string;
  designation: string;
  accent: string;
  /** Orbit angle in degrees (0 = +x, 90 = away from the overview camera). */
  angle: number;
  radius: number;
  height: number;
  size: number;
  blurb: string;
}

export const CORE_RADIUS = 1.6;
export const ORBIT_RADIUS = 7.6;

export const SYSTEMS: Record<SystemId, SystemMeta> = {
  nexus: {
    id: "nexus",
    name: "NEXUS",
    short: "NEXUS",
    designation: "NX-00",
    accent: "#ffab3d",
    angle: 0,
    radius: 0,
    height: 0,
    size: CORE_RADIUS,
    blurb:
      "The platform core. Every agent is dispatched from here, every change meets the ship policy here, and only the owner can overrule it.",
  },
  aegisops: {
    id: "aegisops",
    name: "AegisOps",
    short: "AEGISOPS",
    designation: "AO-01",
    accent: "#ff6f4a",
    angle: 150,
    radius: ORBIT_RADIUS,
    height: 0.9,
    size: 0.78,
    blurb:
      "SRE and incident investigation. Reads the lab only through typed, bounded, read-only diagnostic tools.",
  },
  patchforge: {
    id: "patchforge",
    name: "PatchForge",
    short: "PATCHFORGE",
    designation: "PF-02",
    accent: "#ffc14d",
    angle: 294,
    radius: ORBIT_RADIUS,
    height: -0.7,
    size: 0.78,
    blurb:
      "Turns an engineering task into a tested patch inside a disposable workspace whose Git and validation evidence belong to the runtime.",
  },
  sentinelqa: {
    id: "sentinelqa",
    name: "SentinelQA",
    short: "SENTINELQA",
    designation: "SQ-03",
    accent: "#52e0b8",
    angle: 6,
    radius: ORBIT_RADIUS,
    height: 0.45,
    size: 0.78,
    blurb:
      "Independent verification. Re-runs the candidate against the locked, pristine specification and refuses a pass it cannot prove.",
  },
  resident_engineer: {
    id: "resident_engineer",
    name: "Resident Engineer",
    short: "RESIDENT ENGINEER",
    designation: "RE-04",
    accent: "#a393ff",
    angle: 222,
    radius: ORBIT_RADIUS,
    height: -0.95,
    size: 0.82,
    blurb:
      "A bounded daily engineering cycle over this repository: observe, prioritize, plan, implement, test, review, classify risk, and ask when it must.",
  },
  memory: {
    id: "memory",
    name: "Memory",
    short: "MEMORY",
    designation: "MM-05",
    accent: "#9cc3ff",
    angle: 78,
    radius: ORBIT_RADIUS,
    height: 0.7,
    size: 0.74,
    blurb:
      "Each agent's private memory. It carries knowledge, never authority, and every record cites the cycle that produced it.",
  },
  engram: {
    id: "engram",
    name: "Engram",
    short: "ENGRAM",
    designation: "EG-06",
    accent: "#6b7280",
    angle: 96,
    radius: 11.6,
    height: 1.9,
    size: 0.6,
    blurb:
      "Curated, provenance-verified knowledge exchange between agents. Planned for roadmap Phase 13; not built.",
  },
};

export const ORBITING: Actor[] = [
  "aegisops",
  "resident_engineer",
  "patchforge",
  "sentinelqa",
  "memory",
];

export function systemPosition(id: SystemId): [number, number, number] {
  const meta = SYSTEMS[id];
  if (meta.radius === 0) return [0, 0, 0];
  const theta = (meta.angle * Math.PI) / 180;
  return [meta.radius * Math.cos(theta), meta.height, -meta.radius * Math.sin(theta)];
}

export interface Stop {
  id: "overview" | SystemId;
  label: string;
  system: SystemId | null;
}

export const STOPS: Stop[] = [
  { id: "overview", label: "OVERVIEW", system: null },
  { id: "nexus", label: "NEXUS CORE", system: "nexus" },
  { id: "aegisops", label: "AEGISOPS", system: "aegisops" },
  { id: "resident_engineer", label: "RESIDENT ENGINEER", system: "resident_engineer" },
  { id: "patchforge", label: "PATCHFORGE", system: "patchforge" },
  { id: "sentinelqa", label: "SENTINELQA", system: "sentinelqa" },
  { id: "memory", label: "MEMORY / ENGRAM", system: "memory" },
];

export function stopIndexFor(system: SystemId): number {
  const target = system === "engram" ? "memory" : system;
  return Math.max(
    0,
    STOPS.findIndex((stop) => stop.system === target),
  );
}
