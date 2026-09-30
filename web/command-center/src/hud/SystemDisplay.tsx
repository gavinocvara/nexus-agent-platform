import type { ReactNode } from "react";

import type { CycleView, Provenance, Snapshot, SystemView } from "../data/types";
import { STOPS, SYSTEMS } from "../director/systems";
import { formatNumber, humanize, shortSha, timeAgo, upper } from "../lib/format";
import { useStore } from "../state/store";
import { Chip, Empty, Facts, Gates, Prov, Row, Section, SourceChip, StatusChip } from "./common";

function CycleFacts({ cycle, prov }: { cycle: CycleView; prov: Provenance }) {
  return (
    <dl className="nx-facts">
      <Row label="Decision" value={upper(cycle.decision)} prov={prov} tone={cycle.failure ? "bad" : undefined} />
      <Row label="Mode" value={upper(cycle.mode)} prov={prov} />
      <Row label="Phase reached" value={upper(cycle.phase_reached)} prov={prov} />
      {cycle.risk_level ? <Row label="Risk" value={upper(cycle.risk_level)} prov={prov} tone={cycle.risk_level === "high" ? "bad" : cycle.risk_level === "medium" ? "warn" : "good"} /> : null}
      <Row
        label="Gates"
        value={`${cycle.gates_passed} passed · ${cycle.gates_failed} failed`}
        prov={prov}
        tone={cycle.gates_failed ? "bad" : cycle.gates_passed ? "good" : undefined}
      />
      {cycle.failure ? <Row label="Failure" value={upper(cycle.failure)} prov={prov} tone="bad" /> : null}
      {prov !== "replay" ? <Row label="Completed" value={timeAgo(cycle.completed_at)} prov={prov} /> : null}
    </dl>
  );
}

function NoCycle() {
  return (
    <Empty title="No cycle recorded here">
      This machine has no resident engineer cycle record. A dry run changes no code:{" "}
      <code>python -m nexus.software_engineer cycle</code> (with the engineer enabled), or open{" "}
      <b>Replay</b> to watch recorded evaluation episodes.
    </Empty>
  );
}

function Overview({ snapshot, cycle, replay }: { snapshot: Snapshot; cycle: CycleView | null; replay: boolean }) {
  return (
    <>
      <Section title="Sources">
        <ul className="nx-list">
          {snapshot.sources.map((source) => (
            <li key={source.source}>
              <SourceChip state={source.state} />
              <span>
                {source.label}
                {source.detail ? <span className="nx-mono"> · {source.detail}</span> : null}
              </span>
              <Prov p={source.provenance} />
            </li>
          ))}
        </ul>
      </Section>
      <Section title={replay ? "Episode cycle" : "Latest cycle"}>
        {cycle ? <CycleFacts cycle={cycle} prov={replay ? "replay" : "recorded"} /> : <NoCycle />}
      </Section>
      <Section title="Architecture">
        <p className="nx-display__role">
          NEXUS dispatches through the Atlas boundary to five systems. Conduits carry energy only when a
          record proves that system took part.
        </p>
      </Section>
    </>
  );
}

function NexusPanel({ snapshot, cycle, replay }: { snapshot: Snapshot; cycle: CycleView | null; replay: boolean }) {
  const config = snapshot.engineer_config;
  return (
    <>
      <Section title="Ship policy">
        <p className="nx-display__role">Autonomous shipping requires every one of these gates to pass:</p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {config.required_autonomous_gates.map((gate) => (
            <Chip key={gate} tone="neutral">
              {humanize(gate)}
            </Chip>
          ))}
        </div>
        {cycle?.decision_reasons.length ? (
          <>
            <h4 className="nx-label" style={{ margin: "14px 0 4px" }}>
              {replay ? "Episode" : "Latest"} decision reasons <Prov p={replay ? "replay" : "recorded"} />
            </h4>
            <ul className="nx-list">
              {cycle.decision_reasons.slice(0, 6).map((reason) => (
                <li key={reason} style={{ gridTemplateColumns: "1fr" }}>
                  {reason}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </Section>
      <Section title="Owner decisions">
        {snapshot.decisions.length === 0 ? (
          <Empty title="None recorded">Decisions arrive through signed Slack or the owner CLI; silence is never approval.</Empty>
        ) : (
          <ul className="nx-list">
            {snapshot.decisions.map((decision) => (
              <li key={decision.decision_id}>
                <Chip tone={decision.verdict === "ship" ? "good" : decision.verdict === "reject" ? "bad" : "warn"}>{decision.verdict}</Chip>
                <span>
                  {decision.reason} <span className="nx-mono">· {decision.channel}</span>
                </span>
                <span className="nx-mono">{timeAgo(decision.decided_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Publications">
        {snapshot.publications.length === 0 ? (
          <Empty title="No draft pull request recorded">Publication follows an owner SHIP (or the opted-in autonomous path) and is always a draft.</Empty>
        ) : (
          <ul className="nx-list">
            {snapshot.publications.map((item) => (
              <li key={item.pull_request_url}>
                <span className="nx-mono">#{item.pull_request_number}</span>
                <a className="nx-link" href={item.pull_request_url} target="_blank" rel="noreferrer noopener">
                  {item.branch}
                </a>
                <span className="nx-mono">{item.draft ? "DRAFT" : ""}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}

function AegisPanel({ snapshot, replay }: { snapshot: Snapshot; replay: boolean }) {
  const health = snapshot.health;
  const brain = snapshot.memory.aegisops_brain;
  return (
    <>
      {replay ? (
        <Empty title="Not part of this episode">Replay episodes come from the resident engineer's evaluation catalog.</Empty>
      ) : null}
      <Section title="Lab services">
        {health.services.length === 0 ? (
          <Empty title={health.state === "disabled" ? "Health polling disabled" : "No lab reading"}>
            {health.state === "disabled"
              ? "Set NEXUS_COMMAND_CENTER_HEALTH_ENABLED=true to read lab health."
              : "Start the lab with "}
            {health.state !== "disabled" ? <code>docker compose up -d</code> : null}
          </Empty>
        ) : (
          <ul className="nx-list">
            {health.services.map((service) => (
              <li key={service.service}>
                <Chip tone={service.status === "healthy" ? "good" : service.status === "unhealthy" ? "bad" : "warn"}>{service.status}</Chip>
                <span>{upper(service.service)}</span>
                <Prov p="live" />
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Incident catalog">
        <ul className="nx-list">
          {snapshot.lab_scenarios.map((scenario) => (
            <li key={scenario.id}>
              <span className="nx-mono">{scenario.target_service.toUpperCase()}</span>
              <span>{scenario.title}</span>
              <Prov p="static" />
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Investigator memory">
        {brain.state === "ok" ? (
          <dl className="nx-facts">
            {Object.entries(brain.by_type).map(([type, count]) => (
              <Row key={type} label={humanize(type)} value={count} prov="recorded" />
            ))}
          </dl>
        ) : (
          <Empty title={brain.state === "absent" ? "No private brain on this machine" : "Brain unreadable"}>
            The investigator's memory is created by its benchmarked runs.
          </Empty>
        )}
      </Section>
      <Section title="Instrumentation gap">
        <p className="nx-display__role">
          Individual investigator runs are not persisted where the Command Center reads, so AegisOps is never shown as
          investigating. Lab health above is live.
        </p>
      </Section>
    </>
  );
}

function ResidentPanel({ snapshot, cycle, replay }: { snapshot: Snapshot; cycle: CycleView | null; replay: boolean }) {
  const config = snapshot.engineer_config;
  const prov: Provenance = replay ? "replay" : "recorded";
  const yes = (value: boolean) => (value ? "YES" : "NO");
  return (
    <>
      {snapshot.active_run && !replay ? (
        <Section title="In flight">
          <dl className="nx-facts">
            <Row label="Cycle" value={shortSha(snapshot.active_run.cycle_id, 8)} prov="live" />
            <Row label="Started" value={timeAgo(snapshot.active_run.started_at)} prov="live" />
            <Row label="Phase" value={snapshot.active_run.phase ? upper(snapshot.active_run.phase) : "NOT OBSERVABLE"} prov="live" />
          </dl>
        </Section>
      ) : null}
      <Section title={replay ? "Episode cycle" : "Latest cycle"}>
        {cycle ? (
          <>
            <CycleFacts cycle={cycle} prov={prov} />
            {cycle.selected_title ? (
              <p className="nx-display__role" style={{ marginTop: 10 }}>
                Selected: <b style={{ color: "var(--nx-text)" }}>{cycle.selected_title}</b>
              </p>
            ) : null}
            {cycle.self_review ? (
              <p className="nx-display__role">
                Self-review: {cycle.self_review.items.filter((item) => item.answer !== "clear").length} concerns
                {cycle.self_review.blocking ? " · blocking" : ""}
                {cycle.self_review.requires_human ? " · needs the owner" : ""}
              </p>
            ) : null}
          </>
        ) : (
          <NoCycle />
        )}
      </Section>
      <Section title="Configuration">
        <dl className="nx-facts">
          <Row label="Enabled" value={yes(config.enabled)} prov="static" />
          <Row label="Mode" value={upper(config.mode)} prov="static" />
          <Row label="Sandbox" value={upper(config.sandbox)} prov="static" />
          <Row label="Model configured" value={yes(config.model_configured)} prov="static" />
          <Row label="Publish from cycle" value={yes(config.publish_from_cycle)} prov="static" />
          <Row label="Slack webhook" value={config.slack_webhook_present ? "PRESENT" : "ABSENT"} prov="static" />
          <Row label="Slack owner" value={config.slack_owner_configured ? "SET" : "UNSET"} prov="static" />
          <Row label="Publisher token" value={config.github_token_present ? "PRESENT" : "ABSENT"} prov="static" />
        </dl>
      </Section>
      {cycle ? (
        <Section title="Budget">
          {cycle.budget
            .filter((line) => line.limit !== null && line.limit > 0)
            .slice(0, 6)
            .map((line) => (
              <div key={line.dimension} style={{ margin: "6px 0" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span>{humanize(line.dimension)}</span>
                  <span className="nx-mono">
                    {formatNumber(line.used)} / {formatNumber(line.limit ?? 0)}
                  </span>
                </div>
                <div className="nx-bar">
                  <span style={{ width: `${Math.min(100, (line.used / (line.limit || 1)) * 100)}%` }} />
                </div>
              </div>
            ))}
        </Section>
      ) : null}
      {!replay && snapshot.cycles.length > 1 ? (
        <Section title="History">
          <ul className="nx-list">
            {snapshot.cycles.slice(0, 8).map((item) => (
              <li key={item.cycle_id}>
                <span className="nx-mono">{shortSha(item.cycle_id, 8)}</span>
                <span>{upper(item.decision)}</span>
                <span className="nx-mono">{timeAgo(item.completed_at)}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </>
  );
}

function ScriptedNote({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <Empty title="Scripted stand-in">
      In this evaluation episode a scripted executor plays this system. Its gate results are scripted; the engineer's
      decisions around them are real runtime behavior.
    </Empty>
  );
}

function PatchForgePanel({ cycle, replay, scripted }: { cycle: CycleView | null; replay: boolean; scripted: boolean }) {
  const prov: Provenance = replay ? "replay" : "recorded";
  const change = cycle?.change ?? null;
  return (
    <>
      <ScriptedNote show={scripted} />
      <Section title="Latest change">
        {change ? (
          <>
            <dl className="nx-facts">
              <Row label="Files" value={change.changed_files.length} prov={prov} />
              <Row label="Lines" value={`+${change.additions} −${change.deletions}`} prov={prov} />
              <Row label="Diff" value={shortSha(change.diff_sha256)} prov={prov} />
              <Row label="Patch result" value={shortSha(change.patch_result_sha256)} prov={prov} />
              {change.branch ? <Row label="Branch" value={change.branch} prov={prov} /> : null}
            </dl>
            <ul className="nx-list" style={{ marginTop: 8 }}>
              {change.changed_files.slice(0, 8).map((file) => (
                <li key={file} style={{ gridTemplateColumns: "1fr" }}>
                  <span className="nx-mono" style={{ color: "var(--nx-text-2)" }}>
                    {file}
                  </span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <Empty title="No change produced">
            {cycle ? "The recorded cycle stopped before the executor produced a change." : "No cycle recorded."}
          </Empty>
        )}
      </Section>
      <Section title="PatchForge gates">
        {cycle ? <Gates gates={cycle.gates} owner="patchforge" /> : <Empty title="No gates">Nothing has run.</Empty>}
      </Section>
    </>
  );
}

function SentinelPanel({ cycle, replay, scripted }: { cycle: CycleView | null; replay: boolean; scripted: boolean }) {
  const verdict = cycle?.sentinel ?? null;
  return (
    <>
      <ScriptedNote show={scripted} />
      <Section title="Verification gates">
        {cycle ? <Gates gates={cycle.gates} owner="sentinelqa" /> : <Empty title="No gates">Nothing has run.</Empty>}
      </Section>
      <Section title="Verdict">
        {verdict ? (
          <>
            <dl className="nx-facts">
              <Row label="Verdict" value={upper(verdict.verdict)} prov="recorded" tone={verdict.verdict === "passed" ? "good" : "bad"} />
              <Row label="Specification runs" value={verdict.specification_runs} prov="recorded" />
              <Row label="Lock" value={shortSha(verdict.lock_sha256)} prov="recorded" />
            </dl>
            <p className="nx-display__role">{verdict.summary}</p>
            {verdict.findings.length ? (
              <ul className="nx-list">
                {verdict.findings.slice(0, 8).map((finding) => (
                  <li key={finding.code + finding.detail}>
                    <Chip tone={finding.severity === "blocking" ? "bad" : "neutral"}>{finding.category}</Chip>
                    <span>{humanize(finding.code)}</span>
                    <span />
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <Empty title="No verdict file">
            {replay
              ? "Evaluation episodes carry gate results only."
              : "SentinelQA writes its verdict beside the candidate when a cycle's executor runs."}
          </Empty>
        )}
      </Section>
      <Section title="Contract">
        <p className="nx-display__role">
          The candidate never controls what correctness means: pristine tests are locked by Git objects, candidate test
          changes are rejected, and the runner must report a planted canary before any pass.
        </p>
      </Section>
    </>
  );
}

function MemoryPanel({ snapshot, cycle, replay }: { snapshot: Snapshot; cycle: CycleView | null; replay: boolean }) {
  const engineer = snapshot.memory.engineer;
  return (
    <>
      {replay && cycle ? (
        <Section title="Episode memory">
          <dl className="nx-facts">
            <Row label="Reads" value={cycle.memory_reads} prov="replay" />
            <Row label="Writes" value={cycle.memory_writes} prov="replay" />
          </dl>
        </Section>
      ) : null}
      <Section title="Engineer memory">
        {engineer.state === "ok" ? (
          <>
            <dl className="nx-facts">
              {Object.entries(engineer.by_status).map(([status, count]) => (
                <Row key={status} label={humanize(status)} value={count} prov="recorded" />
              ))}
            </dl>
            {engineer.recent.length ? (
              <ul className="nx-list" style={{ marginTop: 10 }}>
                {engineer.recent.slice(0, 5).map((entry) => (
                  <li key={entry.memory_id} style={{ gridTemplateColumns: "1fr" }}>
                    <span className="nx-mono">
                      {upper(entry.status)} · {entry.confidence}% · cycle {shortSha(entry.cycle_id, 8)}
                    </span>
                    <span>{entry.content}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <Empty title={engineer.state === "absent" ? "No memory store yet" : "Memory unreadable"}>
            Created by the first cycle; opened read-only here.
          </Empty>
        )}
      </Section>
      <Section title="AegisOps brain">
        <dl className="nx-facts">
          <Row
            label="Records"
            value={snapshot.memory.aegisops_brain.state === "ok" ? snapshot.memory.aegisops_brain.total : upper(snapshot.memory.aegisops_brain.state)}
            prov="recorded"
          />
        </dl>
      </Section>
      <Section title="Engram">
        <Empty title="Not built">{SYSTEMS.engram.blurb}</Empty>
      </Section>
    </>
  );
}

export function SystemDisplay() {
  const stop = useStore((state) => state.stop);
  const snapshot = useStore((state) => state.snapshot);
  const mode = useStore((state) => state.mode);
  const episode = useStore((state) => state.replay.episode);
  const entry = STOPS[stop] ?? STOPS[0]!;
  const replay = mode === "replay";
  if (!snapshot) {
    return (
      <main className="nx-display nx-brackets" aria-busy="true" id="nx-main">
        <p className="nx-label">Connecting to NEXUS…</p>
      </main>
    );
  }
  const cycle = replay ? (episode?.cycle ?? null) : snapshot.latest_cycle;
  const systemId = entry.system ?? "nexus";
  const system: SystemView | undefined = snapshot.systems.find((item) => item.id === systemId);
  const scripted = replay && (episode?.scripted_systems ?? []).includes(systemId);
  let body: ReactNode;
  switch (entry.id) {
    case "overview":
      body = <Overview snapshot={snapshot} cycle={cycle} replay={replay} />;
      break;
    case "nexus":
      body = <NexusPanel snapshot={snapshot} cycle={cycle} replay={replay} />;
      break;
    case "aegisops":
      body = <AegisPanel snapshot={snapshot} replay={replay} />;
      break;
    case "resident_engineer":
      body = <ResidentPanel snapshot={snapshot} cycle={cycle} replay={replay} />;
      break;
    case "patchforge":
      body = <PatchForgePanel cycle={cycle} replay={replay} scripted={scripted} />;
      break;
    case "sentinelqa":
      body = <SentinelPanel cycle={cycle} replay={replay} scripted={scripted} />;
      break;
    default:
      body = <MemoryPanel snapshot={snapshot} cycle={cycle} replay={replay} />;
  }
  const title = entry.id === "overview" ? "System overview" : SYSTEMS[systemId].name;
  return (
    <main className="nx-display nx-brackets" key={`${entry.id}:${mode}`} id="nx-main" aria-labelledby="nx-display-title" tabIndex={-1}>
      <header className="nx-display__head">
        <span className="nx-display__designation">{entry.id === "overview" ? "NX-ALL" : SYSTEMS[systemId].designation}</span>
        <h2 className="nx-display__name" id="nx-display-title">
          {title}
        </h2>
        {replay ? <Chip tone="active">Replay</Chip> : system ? <StatusChip status={system.status} /> : null}
      </header>
      {system && entry.id !== "overview" ? (
        <>
          <p className="nx-display__role">{system.role}</p>
          {!replay ? <p className="nx-display__role" style={{ color: "var(--nx-text)" }}>{system.status_detail}</p> : null}
          {!replay ? (
            <Section title="Readout">
              <Facts facts={system.facts} />
            </Section>
          ) : null}
        </>
      ) : null}
      {body}
    </main>
  );
}
