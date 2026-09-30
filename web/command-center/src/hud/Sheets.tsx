import { useEffect, useState } from "react";

import { api } from "../data/api";
import type { CycleView, Snapshot } from "../data/types";
import { SYSTEMS } from "../director/systems";
import { humanize, shortSha, timeAgo, upper, utcStamp } from "../lib/format";
import { useStore } from "../state/store";
import { brainLabel, Chip, Empty, Facts, Gates, Prov, SourceChip, StatusChip } from "./common";

/** The whole Command Center as a document: the accessible equivalent of the scene. */
export function DataView({ fallback = false }: { fallback?: boolean }) {
  const snapshot = useStore((state) => state.snapshot);
  if (!snapshot) {
    return (
      <div className="nx-sheet" role="region" aria-label="Data view">
        <p className="nx-label">Connecting…</p>
      </div>
    );
  }
  return (
    <div className="nx-sheet" role="region" aria-label="Data view" tabIndex={-1}>
      <div className="nx-sheet__inner">
        <h2>System data</h2>
        <p className="nx-sheet__lede">
          {fallback
            ? "The 3D view is unavailable on this device, so the Command Center is shown as a document. Everything the scene shows is here."
            : "Everything the spatial view shows, as text. Each value names its provenance: LIVE, REC (recorded), STATIC, or REPLAY."}{" "}
          Snapshot {snapshot.sequence}, generated {utcStamp(snapshot.generated_at)}.
        </p>
        <h3>Systems</h3>
        <div className="nx-cols">
          {snapshot.systems.map((system) => (
            <article className="nx-card" key={system.id} aria-labelledby={`nx-data-${system.id}`}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
                <h4 id={`nx-data-${system.id}`} style={{ margin: 0, color: "var(--nx-text)", fontSize: 16, letterSpacing: "0.06em" }}>
                  {system.designation} · {system.name}
                </h4>
                <StatusChip status={system.status} />
              </div>
              <p className="nx-sheet__lede" style={{ fontSize: 13 }}>
                {system.role}. {system.status_detail}.
              </p>
              <Facts facts={system.facts} />
            </article>
          ))}
        </div>
        <h3>Sources</h3>
        <table className="nx-table">
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col">State</th>
              <th scope="col">Provenance</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {snapshot.sources.map((source) => (
              <tr key={source.source}>
                <td>{source.label}</td>
                <td>
                  <SourceChip state={source.state} />
                </td>
                <td>
                  <Prov p={source.provenance} />
                </td>
                <td>{source.detail ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <CycleSection snapshot={snapshot} />
        <h3>Lab</h3>
        <div className="nx-cols">
          <div className="nx-card">
            <h4>Service health · {upper(snapshot.health.overall)}</h4>
            {snapshot.health.services.length ? (
              <ul className="nx-list">
                {snapshot.health.services.map((service) => (
                  <li key={service.service}>
                    <Chip tone={service.status === "healthy" ? "good" : "bad"}>{service.status}</Chip>
                    <span>{upper(service.service)}</span>
                    <Prov p="live" />
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="No reading">{upper(snapshot.health.state)}</Empty>
            )}
          </div>
          <div className="nx-card">
            <h4>Incident catalog</h4>
            <ul className="nx-list">
              {snapshot.lab_scenarios.map((scenario) => (
                <li key={scenario.id}>
                  <span className="nx-mono">{scenario.id}</span>
                  <span>{scenario.title}</span>
                  <Prov p="static" />
                </li>
              ))}
            </ul>
          </div>
        </div>
        <h3>Memory</h3>
        <div className="nx-cols">
          <div className="nx-card">
            <h4>Engineer · {upper(snapshot.memory.engineer.state)}</h4>
            {Object.entries(snapshot.memory.engineer.by_status).map(([status, count]) => (
              <div key={status} className="nx-gate">
                <span />
                <span>{humanize(status)}</span>
                <span className="nx-mono">{count}</span>
              </div>
            ))}
          </div>
          <div className="nx-card">
            <h4>AegisOps brain · {brainLabel(snapshot.memory.aegisops_brain.state)}</h4>
            {Object.entries(snapshot.memory.aegisops_brain.by_type).map(([type, count]) => (
              <div key={type} className="nx-gate">
                <span />
                <span>{humanize(type)}</span>
                <span className="nx-mono">{count}</span>
              </div>
            ))}
          </div>
          <div className="nx-card">
            <h4>Engram · not built</h4>
            <p className="nx-sheet__lede" style={{ fontSize: 13 }}>
              {SYSTEMS.engram.blurb}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function CycleSection({ snapshot }: { snapshot: Snapshot }) {
  const cycle = snapshot.latest_cycle;
  return (
    <>
      <h3>Resident engineer</h3>
      {snapshot.pending_approval ? (
        <div className="nx-card" style={{ borderColor: "var(--nx-line-strong)" }}>
          <h4 style={{ color: "var(--nx-amber)" }}>Owner decision required</h4>
          <p style={{ margin: 0, fontWeight: 600 }}>{snapshot.pending_approval.question}</p>
          {snapshot.pending_approval.governed_channels.map((line) => (
            <pre key={line} className="nx-report" style={{ maxHeight: "none", padding: 8, marginTop: 8 }}>
              {line}
            </pre>
          ))}
        </div>
      ) : null}
      {cycle ? (
        <p className="nx-sheet__lede">
          Latest cycle <span className="nx-mono">{shortSha(cycle.cycle_id, 8)}</span>: {upper(cycle.decision)} in{" "}
          {upper(cycle.mode)} mode, completed {timeAgo(cycle.completed_at)}. {cycle.gates_passed} gates passed,{" "}
          {cycle.gates_failed} failed.
        </p>
      ) : (
        <Empty title="No cycle recorded">No resident engineer cycle has run on this machine.</Empty>
      )}
    </>
  );
}

function CycleDetail({ cycle, replay }: { cycle: CycleView; replay: boolean }) {
  const prov = replay ? "replay" : "recorded";
  return (
    <div>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>Cycle {shortSha(cycle.cycle_id, 8)}</h3>
        <Chip tone={cycle.failure ? "bad" : "neutral"}>{cycle.decision}</Chip>
        <Prov p={prov} />
        <span className="nx-mono" style={{ color: "var(--nx-text-3)", fontSize: 11 }}>
          head {shortSha(cycle.repository_head)} · {upper(cycle.mode)}
          {cycle.model ? ` · model ${cycle.model}` : " · no model"}
        </span>
      </div>
      <div className="nx-cols" style={{ marginTop: 16 }}>
        <div>
          <h4>Transitions</h4>
          <ol className="nx-timeline">
            {cycle.transitions.map((item) => (
              <li key={item.sequence}>
                <span className="nx-timeline__stage">{item.stage}</span>
                <span className="nx-timeline__actors">
                  {item.actors.map((actor) => (
                    <span className="nx-actor" key={actor} style={{ color: SYSTEMS[actor].accent }}>
                      {SYSTEMS[actor].short}
                    </span>
                  ))}
                </span>
                <div>{item.reason}</div>
                {item.evidence_note ? <div className="nx-timeline__meta">{item.evidence_note}</div> : null}
                <div className="nx-timeline__meta">
                  #{item.sequence} · {replay ? "fixed evaluation clock" : utcStamp(item.occurred_at)}
                </div>
              </li>
            ))}
          </ol>
        </div>
        <div>
          <h4>Gates</h4>
          <Gates gates={cycle.gates} />
          <h4>Decision reasons</h4>
          <ul className="nx-list">
            {cycle.decision_reasons.map((reason) => (
              <li key={reason} style={{ gridTemplateColumns: "1fr" }}>
                {reason}
              </li>
            ))}
          </ul>
          {cycle.risk ? (
            <>
              <h4>Risk</h4>
              <span className="nx-risk" data-level={cycle.risk.level}>
                {cycle.risk.level}
              </span>
              <ul className="nx-list">
                {cycle.risk.reasons.map((reason) => (
                  <li key={reason} style={{ gridTemplateColumns: "1fr" }}>
                    {reason}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <h4>Notifications</h4>
          {cycle.notifications.length ? (
            <ul className="nx-list">
              {cycle.notifications.map((item, index) => (
                <li key={`${item.event}-${index}`}>
                  <Chip tone={item.delivered ? "good" : "warn"}>{item.delivered ? "sent" : "not sent"}</Chip>
                  <span>
                    {humanize(item.event)} · {item.title}
                  </span>
                  <span className="nx-mono">{item.error_code ?? ""}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="None">No notification in this cycle.</Empty>
          )}
        </div>
      </div>
      <div className="nx-cols" style={{ marginTop: 16 }}>
        <div>
          <h4>Signals ({cycle.signals.length})</h4>
          <ul className="nx-list">
            {cycle.signals.slice(0, 20).map((signal, index) => (
              <li key={`${signal.kind}-${index}`}>
                <Chip tone={signal.severity === "failure" ? "bad" : signal.severity === "warning" ? "warn" : "neutral"}>
                  {signal.kind}
                </Chip>
                <span>
                  {signal.summary}
                  {signal.untrusted_text ? <span className="nx-mono"> · untrusted</span> : null}
                  {signal.instruction_like ? <span className="nx-mono"> · {signal.instruction_like} instruction-like</span> : null}
                </span>
                <span />
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4>Candidates</h4>
          {cycle.candidates.length ? (
            <table className="nx-table">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Candidate</th>
                  <th scope="col">Score</th>
                </tr>
              </thead>
              <tbody>
                {cycle.candidates.map((candidate) => (
                  <tr key={candidate.candidate_id} aria-selected={candidate.selected}>
                    <td className="nx-mono">{candidate.rank}</td>
                    <td>
                      {candidate.title}{" "}
                      <span className="nx-mono" style={{ color: "var(--nx-text-3)" }}>
                        {humanize(candidate.category)} · {candidate.category_risk}
                        {candidate.selected ? " · selected" : ""}
                        {candidate.abandoned ? " · abandoned" : ""}
                      </span>
                    </td>
                    <td className="nx-mono">{candidate.score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty title="No candidates">Nothing was worth doing.</Empty>
          )}
          {cycle.self_review ? (
            <>
              <h4>Self-review</h4>
              {cycle.self_review.items.map((item) => (
                <div className="nx-gate" key={item.question} data-status={item.answer === "clear" ? "passed" : "failed"}>
                  <span className="nx-gate__mark" />
                  <span>{humanize(item.question)}</span>
                  <span className="nx-gate__hash">{item.answer.toUpperCase()}</span>
                </div>
              ))}
            </>
          ) : null}
        </div>
      </div>
      {cycle.report_text ? (
        <>
          <h4>Report</h4>
          <pre className="nx-report">{cycle.report_text}</pre>
        </>
      ) : null}
    </div>
  );
}

export function ActivityView() {
  const snapshot = useStore((state) => state.snapshot);
  const mode = useStore((state) => state.mode);
  const episode = useStore((state) => state.replay.episode);
  const signals = useStore((state) => state.live.signals);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<CycleView | null>(null);
  useEffect(() => {
    if (!selected || mode === "replay") return;
    const controller = new AbortController();
    api
      .cycle(selected, controller.signal)
      .then(setDetail)
      .catch(() => undefined);
    return () => controller.abort();
  }, [selected, mode]);
  const replay = mode === "replay";
  const cycle = replay ? (episode?.cycle ?? null) : selected && detail?.cycle_id === selected ? detail : (snapshot?.latest_cycle ?? null);
  return (
    <div className="nx-sheet" role="region" aria-label="Activity" tabIndex={-1}>
      <div className="nx-sheet__inner">
        <h2>Activity</h2>
        <p className="nx-sheet__lede">
          The technical record behind the animation: every transition, gate, signal, and notification the runtime
          attested.{replay ? " Replay: evaluation episode on a fixed clock." : ""}
        </p>
        <div className="nx-activity">
          <div>
            <h4>{replay ? "Episode" : "Cycles"}</h4>
            {replay ? (
              <p className="nx-sheet__lede">{episode ? episode.title : "Choose an episode in the replay deck."}</p>
            ) : snapshot?.cycles.length ? (
              <table className="nx-table">
                <tbody>
                  {snapshot.cycles.map((item) => (
                    <tr key={item.cycle_id} aria-selected={cycle?.cycle_id === item.cycle_id}>
                      <td>
                        <button onClick={() => setSelected(item.cycle_id)}>{shortSha(item.cycle_id, 8)}</button>
                      </td>
                      <td>{upper(item.decision)}</td>
                      <td className="nx-mono">{timeAgo(item.completed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty title="No cycles">Nothing recorded on this machine.</Empty>
            )}
            {!replay ? (
              <>
                <h4>Stream signals</h4>
                {signals.length ? (
                  <ul className="nx-list">
                    {[...signals].reverse().map((signal) => (
                      <li key={`${signal.kind}-${signal.at}`}>
                        <span className="nx-mono">{signal.kind}</span>
                        <span>{signal.detail ?? shortSha(signal.subject, 8)}</span>
                        <span />
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty title="Quiet">No state change since this page opened.</Empty>
                )}
              </>
            ) : null}
          </div>
          {cycle ? <CycleDetail cycle={cycle} replay={replay} /> : <Empty title="Nothing to show">No cycle selected.</Empty>}
        </div>
      </div>
    </div>
  );
}
