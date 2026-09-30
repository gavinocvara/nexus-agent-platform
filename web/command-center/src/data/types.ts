// Mirror of src/nexus/command_center/models.py (schema_version 1). The server owns these
// shapes; the client only reads them. Keep field names identical.

export type Provenance = "live" | "recorded" | "replay" | "static";
export type SourceState = "ok" | "absent" | "unreadable" | "unavailable" | "disabled" | "preparing";
export type Actor = "nexus" | "aegisops" | "patchforge" | "sentinelqa" | "resident_engineer" | "memory";
export type SystemId = Actor | "engram";
export type SystemStatus = "idle" | "dormant" | "active" | "pipeline" | "attention" | "offline" | "not_built";
export type Tone = "neutral" | "good" | "warn" | "bad";
export type GateStatus = "passed" | "failed" | "error" | "not_run";
export type Verdict = "ship" | "revise" | "reject";

export interface SourceStatus {
  source: string;
  label: string;
  state: SourceState;
  provenance: Provenance;
  detail: string | null;
  observed_at: string | null;
}

export interface Fact {
  label: string;
  value: string;
  provenance: Provenance;
  tone: Tone;
}

export interface TransitionView {
  sequence: number;
  source: string;
  target: string;
  stage: string;
  reason: string;
  occurred_at: string;
  actors: Actor[];
  evidence_note: string | null;
}

export interface GateView {
  gate: string;
  owner: "patchforge" | "sentinelqa" | "repository";
  status: GateStatus;
  summary: string;
  evidence_sha256: string | null;
}

export interface RiskView {
  level: "low" | "medium" | "high";
  category_level: string;
  path_level: string;
  size_level: string;
  uncertain: boolean;
  governing_paths: string[];
  reasons: string[];
}

export interface ReviewItemView {
  question: string;
  answer: "clear" | "concern" | "unknown";
  note: string;
}

export interface SelfReviewView {
  items: ReviewItemView[];
  blocking: boolean;
  requires_human: boolean;
  reviewed_diff_sha256: string | null;
}

export interface SignalView {
  kind: string;
  severity: "info" | "warning" | "failure";
  source: string;
  summary: string;
  untrusted_text: boolean;
  instruction_like: number;
  observed_at: string;
}

export interface CandidateView {
  candidate_id: string;
  title: string;
  category: string;
  category_risk: "low" | "medium" | "high";
  expected_paths: string[];
  score: number;
  rank: number;
  blockers: string[];
  derived_from_untrusted_text: boolean;
  selected: boolean;
  abandoned: boolean;
}

export interface PublicationView {
  authority: string;
  repository: string;
  base_branch: string;
  branch: string;
  pull_request_number: number;
  pull_request_url: string;
  draft: boolean;
  published_by: string;
  published_at: string;
}

export interface ChangeView {
  base_sha: string;
  branch: string | null;
  commit_sha: string | null;
  changed_files: string[];
  additions: number;
  deletions: number;
  diff_bytes: number;
  diff_sha256: string;
  patch_result_sha256: string | null;
  sentinel_verdict_sha256: string | null;
  rollback_reference: string;
  publication: PublicationView | null;
}

export interface DecisionView {
  decision_id: string;
  request_id: string;
  verdict: Verdict;
  decided_by: string;
  reason: string;
  channel: string;
  decided_at: string;
}

export interface ApprovalView {
  request_id: string;
  cycle_id: string;
  title: string;
  question: string;
  problem: string;
  root_cause: string;
  proposed_fix: string;
  why: string;
  behavior_changed: string;
  files_affected: string[];
  validation: GateView[];
  benchmark_impact: string;
  security_impact: string;
  risk: RiskView;
  confidence: number;
  rollback_plan: string;
  recommendation: Verdict;
  dry_run: boolean;
  created_at: string;
  status: "pending" | "decided";
  decision: DecisionView | null;
  governed_channels: string[];
}

export interface NotificationView {
  event: string;
  channel: string;
  title: string;
  delivered: boolean;
  attempts: number;
  error_code: string | null;
  sent_at: string | null;
}

export interface BudgetLine {
  dimension: string;
  used: number;
  limit: number | null;
}

export interface SentinelFindingView {
  code: string;
  category: string;
  severity: string;
  detail: string;
  path: string | null;
}

export interface SentinelVerdictView {
  verdict: string;
  summary: string;
  findings: SentinelFindingView[];
  specification_runs: number;
  patchforge_checks_agree: boolean | null;
  lock_sha256: string;
  completed_at: string;
}

export interface CycleSummary {
  cycle_id: string;
  mode: string;
  started_at: string;
  completed_at: string;
  phase_reached: string;
  decision: string;
  failure: string | null;
  risk_level: string | null;
  selected_title: string | null;
  gates_passed: number;
  gates_failed: number;
  owner_action_required: boolean;
  published: boolean;
}

export interface CycleView extends CycleSummary {
  repository_head: string;
  model: string | null;
  transitions: TransitionView[];
  signals: SignalView[];
  candidates: CandidateView[];
  risk: RiskView | null;
  self_review: SelfReviewView | null;
  gates: GateView[];
  change: ChangeView | null;
  decision_reasons: string[];
  approval: ApprovalView | null;
  budget: BudgetLine[];
  memory_reads: number;
  memory_writes: number;
  notifications: NotificationView[];
  rollback_reason: string | null;
  sentinel: SentinelVerdictView | null;
  report_text: string | null;
}

export interface ActiveRunView {
  cycle_id: string;
  started_at: string;
  lease_expires_at: string;
  phase: string | null;
  phase_source: "unobservable" | "progress_file";
  sequence: number | null;
  stage: string | null;
  description: string | null;
  phase_updated_at: string | null;
  executor: "dry_run" | "patchforge" | "other" | null;
  actors: Actor[];
  pipeline: Actor[];
}

export interface InterruptedRunView {
  cycle_id: string;
  started_at: string | null;
  reason: string;
  recovered_at: string | null;
}

export interface MemoryEntryView {
  memory_id: string;
  category: string;
  status: string;
  lifecycle: string;
  confidence: number;
  tags: string[];
  created_at: string;
  cycle_id: string;
  content: string;
}

export interface EngineerMemoryView {
  state: SourceState;
  total: number;
  by_category: Record<string, number>;
  by_status: Record<string, number>;
  by_lifecycle: Record<string, number>;
  recent: MemoryEntryView[];
}

export interface BrainMemoryView {
  state: SourceState;
  total: number;
  by_type: Record<string, number>;
  by_state: Record<string, number>;
}

export interface MemoryView {
  engineer: EngineerMemoryView;
  aegisops_brain: BrainMemoryView;
}

export interface ServiceHealthView {
  service: string;
  status: "healthy" | "unhealthy" | "unavailable" | "unknown";
  dependencies: Record<string, string>;
}

export interface HealthView {
  state: SourceState;
  overall: "healthy" | "degraded" | "unavailable" | "unknown";
  services: ServiceHealthView[];
  observed_at: string | null;
}

export interface LabScenarioView {
  id: string;
  title: string;
  target_service: string;
}

export interface EngineerConfigView {
  enabled: boolean;
  mode: string;
  sandbox: string;
  model_configured: boolean;
  model_recipes_allowed: boolean;
  publish_from_cycle: boolean;
  read_issues: boolean;
  slack_webhook_present: boolean;
  slack_owner_configured: boolean;
  github_token_present: boolean;
  slack_channel_label: string;
  schedule_cron_intent: string;
  budget: BudgetLine[];
}

export interface SystemView {
  id: SystemId;
  name: string;
  designation: string;
  role: string;
  status: SystemStatus;
  status_detail: string;
  last_activity_at: string | null;
  facts: Fact[];
}

export interface Snapshot {
  schema_version: 1;
  mode: "live";
  version: string;
  sequence: number;
  generated_at: string;
  sources: SourceStatus[];
  systems: SystemView[];
  active_run: ActiveRunView | null;
  interrupted: InterruptedRunView[];
  latest_cycle: CycleView | null;
  cycles: CycleSummary[];
  pending_approval: ApprovalView | null;
  decisions: DecisionView[];
  publications: PublicationView[];
  memory: MemoryView;
  health: HealthView;
  lab_scenarios: LabScenarioView[];
  engineer_config: EngineerConfigView | null;
  required_autonomous_gates: string[];
}

export interface OwnerStepView {
  verdict: Verdict;
  policy_outcome: string;
  note: string;
}

export interface ReplayEpisodeSummary {
  name: string;
  title: string;
  synopsis: string;
  decision: string;
  risk_level: string | null;
  record_sha256: string;
}

export interface ReplayEpisode extends ReplayEpisodeSummary {
  provenance: string;
  tempo_note: string;
  scripted_systems: SystemId[];
  cycle: CycleView;
  owner_step: OwnerStepView | null;
}

export interface ReplayCatalog {
  state: SourceState;
  detail: string | null;
  episodes: ReplayEpisodeSummary[];
}

export type SignalKind =
  | "run.started"
  | "run.phase"
  | "run.ended"
  | "cycle.recorded"
  | "decision.pending"
  | "decision.recorded"
  | "publication.recorded"
  | "health.changed";

export interface StreamSignal {
  kind: SignalKind;
  subject: string;
  detail: string | null;
}
