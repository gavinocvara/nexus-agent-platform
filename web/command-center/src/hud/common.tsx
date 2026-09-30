import type { ReactNode } from "react";

import type { Fact, GateView, Provenance, SourceState, SystemStatus } from "../data/types";
import { humanize, shortSha, upper } from "../lib/format";

const PROV_LABEL: Record<Provenance, string> = {
  live: "LIVE",
  recorded: "REC",
  replay: "REPLAY",
  static: "STATIC",
};

export function Prov({ p }: { p: Provenance }) {
  return (
    <span className="nx-prov" data-p={p} title={`Provenance: ${p}`}>
      {PROV_LABEL[p]}
    </span>
  );
}

export function Chip({ tone, children }: { tone: string; children: ReactNode }) {
  return (
    <span className="nx-chip" data-tone={tone}>
      {children}
    </span>
  );
}

export function StatusChip({ status }: { status: SystemStatus }) {
  const text = status === "not_built" ? "NOT BUILT" : status === "pipeline" ? "IN PIPELINE" : status.toUpperCase();
  return <Chip tone={status}>{text}</Chip>;
}

/** The brain is "disabled" only where a deployment does not read it (ADR 0008 keeps it out of Compose). */
export function brainLabel(state: SourceState): string {
  return state === "disabled" ? "NOT VISIBLE" : upper(state);
}

export function SourceChip({ state }: { state: SourceState }) {
  const tone = state === "ok" ? "good" : state === "unreadable" || state === "unavailable" ? "bad" : "neutral";
  return <Chip tone={tone}>{upper(state)}</Chip>;
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="nx-section">
      <h3 className="nx-section__title">{title}</h3>
      {children}
    </section>
  );
}

export function Facts({ facts, replay = false }: { facts: Fact[]; replay?: boolean }) {
  return (
    <dl className="nx-facts">
      {facts.map((fact) => (
        <div key={fact.label}>
          <dt>{fact.label}</dt>
          <dd data-tone={fact.tone}>
            {fact.value}
            <Prov p={replay ? "replay" : fact.provenance} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Row({ label, value, prov, tone }: { label: string; value: ReactNode; prov: Provenance; tone?: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd data-tone={tone}>
        {value}
        <Prov p={prov} />
      </dd>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="nx-empty" role="note">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

export function Gates({ gates, owner }: { gates: GateView[]; owner?: GateView["owner"] }) {
  const shown = owner ? gates.filter((gate) => gate.owner === owner) : gates;
  if (shown.length === 0) return <Empty title="No gates recorded">This cycle ran no validation gate here.</Empty>;
  return (
    <div role="list">
      {shown.map((gate) => (
        <div className="nx-gate" data-status={gate.status} role="listitem" key={gate.gate}>
          <span className="nx-gate__mark" aria-hidden="true" />
          <span>
            {humanize(gate.gate)} <span className="nx-sr">{gate.status}</span>
          </span>
          <span className="nx-gate__hash" title={gate.evidence_sha256 ?? "no evidence: gate did not run"}>
            {gate.status === "not_run" ? "NOT RUN" : `${gate.status.toUpperCase()} · ${shortSha(gate.evidence_sha256, 8)}`}
          </span>
        </div>
      ))}
    </div>
  );
}
