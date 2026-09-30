import { useRef, useState } from "react";

import type { ApprovalView, OwnerStepView } from "../data/types";
import { shortSha, upper } from "../lib/format";
import { useStore } from "../state/store";
import { Gates } from "./common";

function OwnerCard({
  approval,
  replay,
  resolved,
  ownerStep,
  onHide,
}: {
  approval: ApprovalView;
  replay: boolean;
  resolved: boolean;
  ownerStep: OwnerStepView | null;
  onHide: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [expanded, setExpanded] = useState(false);
  const passed = approval.validation.filter((gate) => gate.status === "passed").length;
  const ran = approval.validation.filter((gate) => gate.status !== "not_run").length;
  const verdict = replay ? (resolved ? ownerStep?.verdict ?? null : null) : (approval.decision?.verdict ?? null);
  return (
    <section className="nx-owner" role="dialog" aria-modal="false" aria-labelledby="nx-owner-q">
      <div className="nx-owner__frame">
        {verdict ? (
          <div className="nx-stamp" data-verdict={verdict}>
            {verdict.toUpperCase()}
            <small>{replay ? "SCRIPTED EVALUATION VERDICT" : "OWNER DECISION ON RECORD"}</small>
          </div>
        ) : null}
        <div className="nx-owner__eyebrow">
          <span className="nx-label">{replay ? "Owner question · replay" : "Owner decision required"}</span>
          <span className="nx-label nx-mono">REQ {shortSha(approval.request_id, 8)}</span>
          <span className="nx-label nx-mono">CYCLE {shortSha(approval.cycle_id, 8)}</span>
          {approval.dry_run ? <span className="nx-label">DRY RUN</span> : null}
        </div>
        <h2 className="nx-owner__question" id="nx-owner-q" tabIndex={-1} ref={heading}>
          {approval.question}
        </h2>
        <p className="nx-owner__title">{approval.title}</p>
        <dl className="nx-owner__strip">
          <div>
            <dt>Risk</dt>
            <dd>
              <span className="nx-risk" data-level={approval.risk.level}>
                {approval.risk.level}
              </span>
            </dd>
          </div>
          <div>
            <dt>Confidence</dt>
            <dd>{approval.confidence}%</dd>
          </div>
          <div>
            <dt>Gates</dt>
            <dd>
              {passed}/{ran} passed
            </dd>
          </div>
          <div>
            <dt>Recommendation</dt>
            <dd className="nx-owner__recv">{approval.recommendation.toUpperCase()}</dd>
          </div>
        </dl>
        {expanded ? (
        <div className="nx-owner__grid" id="nx-owner-evidence">
          <div>
            <h3>Problem</h3>
            <p>{approval.problem}</p>
            <h3>Root cause</h3>
            <p>{approval.root_cause}</p>
            <h3>Proposed fix</h3>
            <p>{approval.proposed_fix}</p>
            <h3>Behavior changed</h3>
            <p>{approval.behavior_changed}</p>
            <h3>Rollback</h3>
            <p>{approval.rollback_plan}</p>
          </div>
          <div>
            <h3>Risk</h3>
            <p>
              <span className="nx-risk" data-level={approval.risk.level}>
                {approval.risk.level}
              </span>{" "}
              <span className="nx-mono" style={{ fontSize: 11, color: "var(--nx-text-3)" }}>
                confidence {approval.confidence}%
              </span>
            </p>
            <ul className="nx-list" style={{ marginTop: 6 }}>
              {approval.risk.reasons.slice(0, 4).map((reason) => (
                <li key={reason} style={{ gridTemplateColumns: "1fr" }}>
                  {reason}
                </li>
              ))}
            </ul>
            <h3>Validation</h3>
            <Gates gates={approval.validation} />
            <h3>Security impact</h3>
            <p>{approval.security_impact}</p>
            {approval.files_affected.length ? (
              <>
                <h3>Files</h3>
                <p className="nx-mono" style={{ fontSize: 11 }}>
                  {approval.files_affected.slice(0, 6).join("  ")}
                </p>
              </>
            ) : null}
          </div>
        </div>
        ) : null}
        <div className="nx-owner__foot">
          <div className="nx-owner__channels">
            <p>
              {replay
                ? "In the live system the owner answers through a governed channel:"
                : "Decide through a governed channel. The Command Center is read-only and cannot record a decision."}
            </p>
            {approval.governed_channels.map((line) => (
              <pre key={line}>{line}</pre>
            ))}
            {replay && resolved && ownerStep ? (
              <p style={{ marginTop: 8 }}>
                {ownerStep.note} Ship policy outcome: <b>{upper(ownerStep.policy_outcome)}</b>.
              </p>
            ) : null}
          </div>
          <div className="nx-owner__actions">
            <button
              className="nx-btn"
              aria-expanded={expanded}
              aria-controls="nx-owner-evidence"
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "Less" : "Evidence"}
            </button>
            <button className="nx-btn" onClick={onHide}>
              Hide panel
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

export function OwnerOverlay() {
  const mode = useStore((state) => state.mode);
  const view = useStore((state) => state.view);
  const pending = useStore((state) => state.snapshot?.pending_approval ?? null);
  const dismissed = useStore((state) => state.dismissedRequest);
  const dismiss = useStore((state) => state.dismissRequest);
  const episode = useStore((state) => state.replay.episode);
  const ownerVisible = useStore((state) => state.replay.ownerVisible);
  const ownerResolved = useStore((state) => state.replay.ownerResolved);
  if (view !== "space") return null;
  if (mode === "replay") {
    const approval = episode?.cycle.approval;
    if (!episode || !approval || !ownerVisible || dismissed === approval.request_id) return null;
    return (
      <OwnerCard
        approval={approval}
        replay
        resolved={ownerResolved}
        ownerStep={episode.owner_step}
        onHide={() => dismiss(approval.request_id)}
      />
    );
  }
  if (!pending || dismissed === pending.request_id) return null;
  return <OwnerCard approval={pending} replay={false} resolved={false} ownerStep={null} onHide={() => dismiss(pending.request_id)} />;
}
