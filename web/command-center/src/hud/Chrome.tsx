import { useEffect, useState } from "react";

import type { SystemStatus } from "../data/types";
import { STOPS, SYSTEMS } from "../director/systems";
import { timeAgo, upper } from "../lib/format";
import { type View, useStore } from "../state/store";

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <>{now.toISOString().slice(11, 19)} UTC</>;
}

const VIEWS: Array<{ id: View; label: string }> = [
  { id: "space", label: "SPACE" },
  { id: "data", label: "DATA" },
  { id: "activity", label: "ACTIVITY" },
];

export function TopBar() {
  const mode = useStore((state) => state.mode);
  const setMode = useStore((state) => state.setMode);
  const view = useStore((state) => state.view);
  const setView = useStore((state) => state.setView);
  const connection = useStore((state) => state.connection);
  const snapshot = useStore((state) => state.snapshot);
  const webgl = useStore((state) => state.webgl);
  return (
    <header className="nx-topbar">
      <div className="nx-brand">
        <span className="nx-brand__mark">NEXUS</span>
        <span className="nx-brand__sub">COMMAND CENTER</span>
      </div>
      <div className="nx-modes" role="group" aria-label="Data source">
        <button className="nx-mode" data-mode="live" aria-pressed={mode === "live"} onClick={() => setMode("live")}>
          <span className="nx-dot" aria-hidden="true" />
          LIVE
        </button>
        <button className="nx-mode" data-mode="replay" aria-pressed={mode === "replay"} onClick={() => setMode("replay")}>
          REPLAY
        </button>
      </div>
      <div className="nx-conn" data-state={connection} role="status">
        STREAM <b>{upper(connection)}</b>
        {snapshot ? <> · SEQ {snapshot.sequence}</> : null}
      </div>
      <div className="nx-views" role="tablist" aria-label="View">
        {VIEWS.map((item) => (
          <button
            key={item.id}
            role="tab"
            className="nx-tab"
            aria-selected={view === item.id}
            disabled={item.id === "space" && !webgl}
            onClick={() => setView(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="nx-meta">
        v{snapshot?.version ?? "…"} · <Clock />
      </div>
    </header>
  );
}

const GROUPS: number[][] = [[0, 1], [2], [3, 4, 5], [6]];

export function Menu() {
  const stop = useStore((state) => state.stop);
  const goTo = useStore((state) => state.goTo);
  const systems = useStore((state) => state.snapshot?.systems);
  const mode = useStore((state) => state.mode);
  const entry = STOPS[stop] ?? STOPS[0]!;
  const statusOf = (index: number): SystemStatus | "none" => {
    const system = STOPS[index]?.system;
    if (!system || mode === "replay") return "none";
    return systems?.find((item) => item.id === system)?.status ?? "dormant";
  };
  const blurb =
    entry.system === null
      ? "One platform, five systems. Scroll to travel through them; drag to look around; select any orb to inspect it."
      : SYSTEMS[entry.system].blurb;
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      event.stopPropagation();
      const next = Math.max(0, Math.min(STOPS.length - 1, stop + (event.key === "ArrowDown" ? 1 : -1)));
      goTo(next);
      document.getElementById(`nx-stop-${next}`)?.focus();
    }
  };
  return (
    <nav className="nx-menu" aria-label="Systems">
      <p className="nx-label nx-menu__eyebrow">
        {entry.system ? SYSTEMS[entry.system].designation : "NX-ALL"} // {mode === "replay" ? "REPLAY" : "SYSTEMS"}
      </p>
      <h1 className="nx-menu__title">{entry.system ? SYSTEMS[entry.system].short : "NEXUS"}</h1>
      <ol className="nx-menu__list" onKeyDown={onKeyDown}>
        {GROUPS.map((group) => (
          <li className="nx-menu__group" key={group.join()}>
            <ol className="nx-menu__list" style={{ margin: 0 }}>
              {group.map((index) => {
                const item = STOPS[index]!;
                return (
                  <li key={item.id}>
                    <button
                      id={`nx-stop-${index}`}
                      className="nx-menu__item"
                      aria-current={index === stop ? "true" : undefined}
                      data-status={statusOf(index)}
                      onClick={() => goTo(index)}
                    >
                      <span className="nx-menu__status" aria-hidden="true" />
                      {item.label}
                    </button>
                  </li>
                );
              })}
            </ol>
          </li>
        ))}
      </ol>
      <p className="nx-menu__blurb">{blurb}</p>
    </nav>
  );
}

// The resident engineer's real phase vocabulary, in order.
const RAIL = [
  "OBSERVE",
  "UNDERSTAND",
  "PRIORITIZE",
  "INVESTIGATE",
  "PLAN",
  "IMPLEMENT",
  "TEST",
  "REVIEW",
  "RISK",
  "DECIDE",
  "PUBLISH",
  "VERIFY",
  "LEARN",
  "REPORT",
  "OWNER",
];
const RAIL_CODE: Record<string, string> = {
  OBSERVE: "OBS",
  UNDERSTAND: "MEM",
  PRIORITIZE: "PRI",
  INVESTIGATE: "INV",
  PLAN: "PLN",
  IMPLEMENT: "IMP",
  TEST: "TST",
  REVIEW: "REV",
  RISK: "RSK",
  DECIDE: "DEC",
  PUBLISH: "PUB",
  VERIFY: "VER",
  LEARN: "LRN",
  REPORT: "RPT",
  OWNER: "OWN",
};

function Rail() {
  const stage = useStore((state) => state.stage);
  const mode = useStore((state) => state.mode);
  const episode = useStore((state) => state.replay.episode);
  const latest = useStore((state) => state.snapshot?.latest_cycle ?? null);
  const cycle = mode === "replay" ? (episode?.cycle ?? null) : latest;
  // While a new cycle is in flight, the previous record's phases say nothing about it.
  const inFlight = stage?.source === "live";
  const reached = new Set(inFlight ? [] : (cycle?.transitions ?? []).map((item) => item.stage));
  if (!inFlight && cycle?.change?.publication) reached.add("PUBLISH");
  if (!inFlight && cycle?.approval) reached.add("OWNER");
  const currentIndex = stage ? RAIL.indexOf(stage.stage) : -1;
  let source = "NO CYCLE RECORDED";
  if (stage?.source === "replay") source = "REPLAY · EVALUATION EPISODE";
  else if (stage?.source === "recorded") source = "RECORDED PLAYBACK · LATEST CYCLE";
  else if (stage?.source === "live") source = "LIVE · CYCLE IN FLIGHT";
  else if (mode === "replay") source = episode ? "REPLAY · PAUSED" : "REPLAY · CHOOSE AN EPISODE";
  else if (cycle) source = `LATEST CYCLE · ${timeAgo(cycle.completed_at).toUpperCase()}`;
  return (
    <div className="nx-rail" aria-label="Cycle stages">
      <div className="nx-rail__head">
        <span className="nx-rail__source" data-source={stage?.source ?? mode}>
          {source}
        </span>
        {cycle && !inFlight ? <span className="nx-rail__source">{upper(cycle.decision)}</span> : null}
      </div>
      <div className="nx-rail__track" role="list">
        {RAIL.map((name, index) => {
          let state = "idle";
          if (currentIndex >= 0) {
            if (index === currentIndex) state = "current";
            else if (reached.has(name) && index < currentIndex) state = "done";
          } else if (reached.has(name)) state = "reached";
          return (
            <div
              key={name}
              role="listitem"
              className="nx-rail__seg"
              data-state={state}
              data-kind={name === "OWNER" ? "owner" : "phase"}
              aria-current={state === "current" ? "step" : undefined}
              title={name}
            >
              <span aria-hidden="true">{RAIL_CODE[name]}</span>
              <span className="nx-sr">{name}</span>
            </div>
          );
        })}
      </div>
      <div className="nx-rail__caption" aria-live="off">
        {stage ? (
          <>
            <b>{stage.stage}</b>
            {stage.label}
          </>
        ) : cycle ? (
          <>
            <b>{upper(cycle.phase_reached)}</b>
            {cycle.decision_reasons[0] ?? ""}
          </>
        ) : (
          "Nothing has run on this machine yet."
        )}
      </div>
    </div>
  );
}

function StatusBlock() {
  const snapshot = useStore((state) => state.snapshot);
  const dismissed = useStore((state) => state.dismissedRequest);
  const dismiss = useStore((state) => state.dismissRequest);
  const mode = useStore((state) => state.mode);
  const stage = useStore((state) => state.stage);
  const episode = useStore((state) => state.replay.episode);
  let big = "CONNECTING";
  let sub = "";
  let tone = "idle";
  if (mode === "replay") {
    big = stage ? stage.stage : episode ? "REPLAY READY" : "REPLAY";
    sub = episode ? episode.title : "Recorded evaluation episodes · not live";
    tone = stage ? "active" : "idle";
  } else if (snapshot) {
    if (snapshot.active_run) {
      big = "CYCLE IN FLIGHT";
      sub = snapshot.active_run.stage
        ? `Phase ${snapshot.active_run.stage} · started ${timeAgo(snapshot.active_run.started_at)}`
        : `Started ${timeAgo(snapshot.active_run.started_at)} · phase not published`;
      tone = "active";
    } else if (snapshot.pending_approval) {
      big = "OWNER DECISION";
      sub = "NEXUS is waiting on you · read-only here";
      tone = "attention";
    } else {
      big = snapshot.cycles.length ? `${snapshot.cycles.length} CYCLES RECORDED` : "STANDING BY";
      sub = snapshot.latest_cycle
        ? `Last: ${upper(snapshot.latest_cycle.decision)} · ${timeAgo(snapshot.latest_cycle.completed_at)}`
        : "No cycle has run on this machine";
    }
  }
  return (
    <div className="nx-status" data-tone={tone} role="status">
      <div className="nx-status__big">{big}</div>
      <div className="nx-status__sub">
        <span className="nx-status__key" aria-hidden="true">
          {mode === "replay" ? "R" : "N"}
        </span>
        {sub}
        {mode === "live" && snapshot?.pending_approval && dismissed === snapshot.pending_approval.request_id ? (
          <button className="nx-btn" style={{ marginLeft: 8, padding: "2px 8px" }} onClick={() => dismiss("")}>
            Show question
          </button>
        ) : null}
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="nx-legend" aria-label="Controls">
      <span className="nx-key">
        <span className="nx-kbd">SCROLL</span>NAVIGATE
      </span>
      <span className="nx-key">
        <span className="nx-kbd">DRAG</span>ORBIT
      </span>
      <span className="nx-key">
        <span className="nx-kbd">R-DRAG</span>PAN
      </span>
      <span className="nx-key">
        <span className="nx-kbd">CTRL+SCROLL</span>ZOOM
      </span>
      <span className="nx-key">
        <span className="nx-kbd">ESC</span>OVERVIEW
      </span>
    </div>
  );
}

function Stepper() {
  const step = useStore((state) => state.step);
  return (
    <div className="nx-stepper">
      <button onClick={() => step(-1)} aria-label="Previous system">
        ◀ PREV
      </button>
      <button onClick={() => step(1)} aria-label="Next system">
        NEXT ▶
      </button>
    </div>
  );
}

export function Footer() {
  return (
    <footer className="nx-footer">
      <StatusBlock />
      <Rail />
      <Legend />
      <Stepper />
    </footer>
  );
}
