/**
 * AE-03 V2 API Client & SSE Hook.
 *
 * Provides typed fetch wrappers for all V2 endpoints and a reactive
 * SSE hook that maps EventTracker events to frontend state updates.
 */

const API_BASE = "/api/v2";

/* ── Types ───────────────────────────────────────────────────────────── */

export interface RunRequest {
  goal: string;
  workspace_id?: string;
  user_id?: string;
}

export interface RunResponse {
  run_id: string;
  status: string;
  message: string;
  stream_url: string;
}

export interface RunStatusResponse {
  run_id: string;
  status: string;
  goal: string;
  task_count: number;
  tasks_completed: number;
  tasks_failed: number;
  current_task: string | null;
  errors: string[];
  metrics: Record<string, unknown>;
  updated_at: number;
}

export interface RunReportResponse {
  run_id: string;
  status: string;
  goal: string;
  report_content: string;
  artifacts: Record<string, unknown>[];
  cost_summary: CostSummary;
  event_count: number;
  audit_entries: Record<string, unknown>[];
  verification: Record<string, unknown> | null;
  metrics: Record<string, unknown>;
}

export interface CostSummary {
  run_id: string;
  total_cost_usd: number;
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_latency_ms: number;
  avg_latency_ms: number;
  calls: number;
  provider_breakdown: Record<string, ProviderCost>;
}

export interface ProviderCost {
  calls: number;
  tokens: number;
  cost_usd: number;
  latency_ms: number;
}

export interface ApprovalPayload {
  type: "approval_required";
  approval_id: string;
  run_id: string;
  agent_role: string;
  tool_name: string;
  risk_level: string;
  context_summary: string;
  payload: Record<string, unknown>;
  actions: string[];
}

export interface SSEEvent {
  event_id: string;
  event_type: string;
  run_id: string;
  timestamp: number;
  data: Record<string, unknown>;
  agent_role: string;
  task_id: string;
  duration_ms: number;
}

export interface AgentCapabilities {
  agents: Record<string, {
    role: string;
    allowed_tools: string[];
    can_invoke_llm: boolean;
    can_read_rag: boolean;
    can_write_artifacts: boolean;
    can_access_network: boolean;
    can_execute_code: boolean;
    max_retries: number;
    timeout_seconds: number;
    max_risk_level: string;
  }>;
}

/* ── API Fetch Helpers ───────────────────────────────────────────────── */

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const errBody = await res.text();
    throw new Error(`API ${res.status}: ${errBody}`);
  }
  return res.json() as Promise<T>;
}

/* ── Endpoints ───────────────────────────────────────────────────────── */

/** POST /api/v2/run — Start a new execution run. */
export async function startRun(request: RunRequest): Promise<RunResponse> {
  return apiFetch<RunResponse>("/run", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

/** GET /api/v2/run/{runId}/status — Get current run status. */
export async function getRunStatus(runId: string): Promise<RunStatusResponse> {
  return apiFetch<RunStatusResponse>(`/run/${runId}/status`);
}

/** GET /api/v2/run/{runId}/report — Get final run report. */
export async function getRunReport(runId: string): Promise<RunReportResponse> {
  return apiFetch<RunReportResponse>(`/run/${runId}/report`);
}

/** POST /api/v2/run/{runId}/approve — Resolve HITL approval. */
export async function resolveApproval(
  runId: string,
  approvalId: string,
  action: "approve" | "reject" | "request_changes",
  reason: string = ""
): Promise<Record<string, unknown>> {
  return apiFetch(`/run/${runId}/approve`, {
    method: "POST",
    body: JSON.stringify({ approval_id: approvalId, action, reason }),
  });
}

/** GET /api/v2/runs — List all runs. */
export async function listRuns(): Promise<{ runs: RunStatusResponse[]; total: number }> {
  return apiFetch(`/runs`);
}

/** GET /api/v2/tools — List registered tools. */
export async function listTools(): Promise<Record<string, unknown>> {
  return apiFetch(`/tools`);
}

/** GET /api/v2/agents — List agent capabilities. */
export async function listAgents(): Promise<AgentCapabilities> {
  return apiFetch<AgentCapabilities>(`/agents`);
}

/** GET /api/v2/hitl/pending — Get pending approvals. */
export async function getPendingApprovals(): Promise<{
  pending: ApprovalPayload[];
  count: number;
}> {
  return apiFetch(`/hitl/pending`);
}

/** GET /api/v2/observability/costs/{runId} — Get cost breakdown. */
export async function getCosts(runId: string): Promise<CostSummary> {
  return apiFetch<CostSummary>(`/observability/costs/${runId}`);
}

/** GET /api/v2/observability/events/{runId} — Get events. */
export async function getEvents(runId: string): Promise<{
  events: SSEEvent[];
  timeline: Record<string, unknown>[];
  summary: Record<string, number>;
}> {
  return apiFetch(`/observability/events/${runId}`);
}

/* ── SSE Stream ──────────────────────────────────────────────────────── */

export type SSEEventCallback = (event: SSEEvent) => void;

/**
 * Connect to the SSE stream for a run.
 * Returns a cleanup function to close the connection.
 */
export function connectSSE(
  runId: string,
  onEvent: SSEEventCallback,
  onError?: (error: Event) => void,
  onDone?: () => void,
): () => void {
  const url = `${API_BASE}/run/${runId}/stream`;
  const source = new EventSource(url);

  // Listen for all event types from EventTracker
  const eventTypes = [
    "RUN_CREATED", "PLAN_CREATED", "GRAPH_COMPILED", "SECURITY_CHECK",
    "TOOL_REQUESTED", "TOOL_ALLOWED", "TOOL_DENIED", "TOOL_EXECUTED",
    "AGENT_STARTED", "AGENT_COMPLETED", "AGENT_FAILED", "RETRY", "REPLAN",
    "RAG_SEARCH", "SOURCE_RETRIEVED", "CRITIC_STARTED", "CRITIC_COMPLETED",
    "CRITIC_FAILED", "VERIFICATION_STARTED", "VERIFICATION_COMPLETED",
    "APPROVAL_REQUESTED", "APPROVED", "REJECTED",
    "REPORT_CREATED", "RUN_COMPLETED",
  ];

  for (const type of eventTypes) {
    source.addEventListener(type, (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as SSEEvent;
        onEvent(data);
      } catch {
        // Ignore parse errors
      }
    });
  }

  // Generic message handler for unlisted event types
  source.onmessage = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data) as SSEEvent;
      onEvent(data);
    } catch {
      // Ignore
    }
  };

  // Done event
  source.addEventListener("done", () => {
    source.close();
    onDone?.();
  });

  source.onerror = (e: Event) => {
    onError?.(e);
    source.close();
  };

  return () => source.close();
}

/* ── Event → Node Status Mapping ─────────────────────────────────────── */

export function eventToNodeStatus(
  eventType: string,
): "pending" | "running" | "success" | "failed" | "retrying" | "waiting_approval" | null {
  switch (eventType) {
    case "AGENT_STARTED": return "running";
    case "AGENT_COMPLETED": return "success";
    case "AGENT_FAILED": return "failed";
    case "RETRY": return "retrying";
    case "APPROVAL_REQUESTED": return "waiting_approval";
    case "APPROVED": return "running";
    default: return null;
  }
}

/** Map agent_role from event to a graph node ID. */
export function roleToNodeId(role: string): string {
  return role.toLowerCase().replace(/\s+/g, "_");
}
