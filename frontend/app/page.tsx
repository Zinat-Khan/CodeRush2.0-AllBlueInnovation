"use client";

import { useCallback, useRef, useState } from "react";
import {
  Play,
  Cpu,
  Zap,
  Activity,
  RotateCcw,
  Loader2,
  Workflow,
  FileText,
  Shield,
  AlertTriangle,
} from "lucide-react";
import GraphCanvas, { type AgentNode } from "@/components/GraphCanvas";
import MetricsPanel, { type Metrics, type EventLogEntry } from "@/components/MetricsPanel";
import ApprovalModal, { type ApprovalRequest } from "@/components/ApprovalModal";
import {
  startRun,
  connectSSE,
  resolveApproval,
  getRunReport,
  eventToNodeStatus,
  roleToNodeId,
  type SSEEvent,
  type RunReportResponse,
} from "@/lib/api";

/* ── Status types ──────────────────────────────────────────────────────── */
type RunStatus = "idle" | "compiling" | "running" | "success" | "failed";

/* ── Default LangGraph Nodes ───────────────────────────────────────────── */
const LANGGRAPH_NODES: AgentNode[] = [
  { id: "planner",      role: "PLANNER",        label: "Planner",       status: "pending", x: 250, y: 0 },
  { id: "router",       role: "ORCHESTRATOR",   label: "Task Router",   status: "pending", x: 250, y: 120 },
  { id: "researcher",   role: "RESEARCHER",     label: "Researcher",    status: "pending", x: 60,  y: 240 },
  { id: "tool_execution", role: "TOOL_EXECUTION", label: "Tool Executor", status: "pending", x: 250, y: 240 },
  { id: "analyst",      role: "ANALYST",        label: "Analyst",       status: "pending", x: 440, y: 240 },
  { id: "critic",       role: "CRITIC",         label: "Critic",        status: "pending", x: 130, y: 370 },
  { id: "verifier",     role: "VERIFIER",       label: "Verifier",      status: "pending", x: 370, y: 370 },
  { id: "reporter",     role: "REPORTER",       label: "Reporter",      status: "pending", x: 250, y: 490 },
];

const LANGGRAPH_EDGES: [string, string][] = [
  ["planner", "router"],
  ["router", "researcher"],
  ["router", "tool_execution"],
  ["router", "analyst"],
  ["researcher", "critic"],
  ["tool_execution", "critic"],
  ["analyst", "critic"],
  ["critic", "verifier"],
  ["verifier", "reporter"],
];

/* ── Page Component ────────────────────────────────────────────────────── */
export default function OrchestratorPage() {
  const [goalText, setGoalText] = useState("");
  const [provider, setProvider] = useState("google");
  const [status, setStatus] = useState<RunStatus>("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [nodes, setNodes] = useState<AgentNode[]>([]);
  const [edges, setEdges] = useState<[string, string][]>([]);
  const [selectedNode, setSelectedNode] = useState<AgentNode | null>(null);
  const [metrics, setMetrics] = useState<Metrics>({
    totalTokens: 0,
    totalCost: 0,
    nodesCompleted: 0,
    nodesTotal: 0,
    elapsedMs: 0,
    nodeLatencies: {},
  });
  const [eventLog, setEventLog] = useState<EventLogEntry[]>([]);
  const [approvalRequest, setApprovalRequest] = useState<ApprovalRequest | null>(null);
  const [report, setReport] = useState<RunReportResponse | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number>(0);
  const sseCleanupRef = useRef<(() => void) | null>(null);

  /* ── Log helper ──────────────────────────────────────────────────────── */
  const addLogEntry = useCallback((type: string, nodeId: string, message: string) => {
    setEventLog((prev) => [
      ...prev,
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        timestamp: Date.now(),
        type,
        nodeId,
        message,
      },
    ]);
  }, []);

  /* ── SSE Event Handler ───────────────────────────────────────────────── */
  const handleSSEEvent = useCallback((event: SSEEvent) => {
    const { event_type, agent_role, task_id, data, duration_ms } = event;

    // Map event to log entry
    const eventTypeMap: Record<string, string> = {
      RUN_CREATED: "SYSTEM",
      PLAN_CREATED: "COMPILE",
      AGENT_STARTED: "NODE_START",
      AGENT_COMPLETED: "NODE_END",
      AGENT_FAILED: "ERROR",
      TOOL_REQUESTED: "TOOL",
      TOOL_ALLOWED: "TOOL",
      TOOL_DENIED: "SECURITY",
      TOOL_EXECUTED: "TOOL",
      SECURITY_CHECK: "SECURITY",
      APPROVAL_REQUESTED: "APPROVAL",
      APPROVED: "APPROVED",
      REJECTED: "ERROR",
      RETRY: "RETRY",
      RUN_COMPLETED: "SYSTEM",
      CRITIC_STARTED: "NODE_START",
      CRITIC_COMPLETED: "NODE_END",
      VERIFICATION_STARTED: "NODE_START",
      VERIFICATION_COMPLETED: "NODE_END",
      REPORT_CREATED: "SYSTEM",
    };

    const logType = eventTypeMap[event_type] || "SYSTEM";
    const nodeId = roleToNodeId(agent_role || task_id || "-");
    const message = formatEventMessage(event_type, data, agent_role, duration_ms);
    addLogEntry(logType, nodeId, message);

    // Update node status from event
    const nodeStatus = eventToNodeStatus(event_type);
    if (nodeStatus && agent_role) {
      const targetNodeId = roleToNodeId(agent_role);
      setNodes((prev) =>
        prev.map((n) =>
          n.id === targetNodeId ? { ...n, status: nodeStatus } : n
        )
      );
    }

    // Update metrics from events
    if (event_type === "AGENT_COMPLETED") {
      const tokens = (data as Record<string, number>).tokens || 0;
      setMetrics((prev) => ({
        ...prev,
        totalTokens: prev.totalTokens + tokens,
        nodesCompleted: prev.nodesCompleted + 1,
        ...(agent_role && duration_ms
          ? {
              nodeLatencies: {
                ...prev.nodeLatencies,
                [roleToNodeId(agent_role)]: Math.round(duration_ms),
              },
            }
          : {}),
      }));
    }

    // Handle cost updates
    if (event_type === "RUN_COMPLETED") {
      const totalCost = (data as Record<string, number>).total_cost_usd || 0;
      setMetrics((prev) => ({ ...prev, totalCost }));
    }

    // Handle HITL approvals
    if (event_type === "APPROVAL_REQUESTED") {
      const approvalData = data as Record<string, string>;
      setApprovalRequest({
        id: approvalData.approval_id || `apr-${Date.now()}`,
        nodeId: roleToNodeId(agent_role || ""),
        agentRole: agent_role || "UNKNOWN",
        tool: approvalData.tool_name || "unknown_tool",
        payload: data,
      });
    }

    // Handle run completion
    if (event_type === "RUN_COMPLETED") {
      const runStatus = (data as Record<string, string>).status;
      if (timerRef.current) clearInterval(timerRef.current);
      setStatus(runStatus === "success" ? "success" : "failed");
    }
  }, [addLogEntry]);

  /* ── Compile & Run (Real API) ────────────────────────────────────────── */
  const handleCompileAndRun = useCallback(async () => {
    if (!goalText.trim()) return;

    // Phase 1: Setup
    setStatus("compiling");
    setSelectedNode(null);
    setEventLog([]);
    setReport(null);
    addLogEntry("SYSTEM", "-", `Starting execution with ${provider}...`);

    // Initialize graph
    const graphNodes = LANGGRAPH_NODES.map((n) => ({ ...n, status: "pending" as const }));
    setNodes(graphNodes);
    setEdges(LANGGRAPH_EDGES);
    setMetrics({
      totalTokens: 0,
      totalCost: 0,
      nodesCompleted: 0,
      nodesTotal: graphNodes.length,
      elapsedMs: 0,
      nodeLatencies: {},
    });

    try {
      // Phase 2: Call V2 API
      addLogEntry("COMPILE", "-", "Submitting goal to LangGraph WorkflowEngine...");
      const response = await startRun({
        goal: goalText,
        workspace_id: "default_workspace",
        user_id: "frontend_user",
      });

      setRunId(response.run_id);
      addLogEntry("COMPILE", "-", `Run ${response.run_id} started. Connecting SSE stream...`);

      // Phase 3: Start execution
      setStatus("running");
      startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setMetrics((prev) => ({
          ...prev,
          elapsedMs: Date.now() - startTimeRef.current,
        }));
      }, 100);

      // Phase 4: Connect SSE
      const cleanup = connectSSE(
        response.run_id,
        handleSSEEvent,
        (error) => {
          console.error("SSE error:", error);
          addLogEntry("ERROR", "-", "SSE connection error. Checking status...");
        },
        () => {
          addLogEntry("SYSTEM", "-", "SSE stream ended.");
          if (timerRef.current) clearInterval(timerRef.current);
        },
      );
      sseCleanupRef.current = cleanup;

    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      addLogEntry("ERROR", "-", `Failed to start run: ${errorMsg}`);
      setStatus("failed");
      if (timerRef.current) clearInterval(timerRef.current);

      // Fallback to demo mode if API is not available
      addLogEntry("SYSTEM", "-", "Falling back to demo mode...");
      await runDemoMode(graphNodes);
    }
  }, [goalText, provider, addLogEntry, handleSSEEvent]);

  /* ── Demo Mode Fallback ──────────────────────────────────────────────── */
  const runDemoMode = useCallback(async (graphNodes: AgentNode[]) => {
    setStatus("running");
    startTimeRef.current = Date.now();
    timerRef.current = setInterval(() => {
      setMetrics((prev) => ({ ...prev, elapsedMs: Date.now() - startTimeRef.current }));
    }, 100);

    const executionOrder = ["planner", "router", "researcher", "tool_execution", "analyst", "critic", "verifier", "reporter"];

    for (let i = 0; i < executionOrder.length; i++) {
      const nodeId = executionOrder[i];
      const node = graphNodes.find((n) => n.id === nodeId);
      if (!node) continue;

      setNodes((prev) =>
        prev.map((n) => (n.id === nodeId ? { ...n, status: "running" as const } : n))
      );
      addLogEntry("NODE_START", nodeId, `${node.label} started`);

      // Simulate HITL approval for verifier
      if (nodeId === "verifier") {
        setNodes((prev) =>
          prev.map((n) =>
            n.id === nodeId ? { ...n, status: "waiting_approval" as const } : n
          )
        );
        addLogEntry("APPROVAL", nodeId, "Approval required: output verification");
        setApprovalRequest({
          id: `apr-${Date.now()}`,
          nodeId: "verifier",
          agentRole: "VERIFIER",
          tool: "verify_output",
          payload: { schema: "AgentState", action: "verify_output" },
        });
        await waitForApproval();
        setApprovalRequest(null);
        addLogEntry("APPROVED", nodeId, "Approved by human operator");
        setNodes((prev) =>
          prev.map((n) => (n.id === nodeId ? { ...n, status: "running" as const } : n))
        );
      }

      const workDuration = 600 + Math.random() * 1200;
      await delay(workDuration);

      const tokensUsed = 200 + Math.floor(Math.random() * 600);
      const costIncr = parseFloat((tokensUsed * 0.000008).toFixed(6));

      setNodes((prev) =>
        prev.map((n) => (n.id === nodeId ? { ...n, status: "success" as const } : n))
      );
      addLogEntry("NODE_END", nodeId, `${node.label} completed (${tokensUsed} tokens)`);

      setMetrics((prev) => ({
        ...prev,
        totalTokens: prev.totalTokens + tokensUsed,
        totalCost: parseFloat((prev.totalCost + costIncr).toFixed(6)),
        nodesCompleted: prev.nodesCompleted + 1,
        nodeLatencies: { ...prev.nodeLatencies, [nodeId]: Math.round(workDuration) },
      }));
    }

    if (timerRef.current) clearInterval(timerRef.current);
    setStatus("success");
    addLogEntry("SYSTEM", "-", "Execution complete!");
  }, [addLogEntry]);

  /* ── Fetch Report ────────────────────────────────────────────────────── */
  const handleFetchReport = useCallback(async () => {
    if (!runId) return;
    try {
      const rpt = await getRunReport(runId);
      setReport(rpt);
      addLogEntry("SYSTEM", "-", "Report fetched successfully.");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      addLogEntry("ERROR", "-", `Failed to fetch report: ${msg}`);
    }
  }, [runId, addLogEntry]);

  /* ── Approval handlers ───────────────────────────────────────────────── */
  const approvalResolverRef = useRef<(() => void) | null>(null);

  const waitForApproval = () =>
    new Promise<void>((resolve) => {
      approvalResolverRef.current = resolve;
    });

  const handleApprove = useCallback(async () => {
    if (approvalRequest && runId) {
      try {
        await resolveApproval(runId, approvalRequest.id, "approve");
      } catch {
        // In demo mode, just resolve locally
      }
    }
    approvalResolverRef.current?.();
    setApprovalRequest(null);
  }, [approvalRequest, runId]);

  const handleReject = useCallback(async () => {
    if (approvalRequest && runId) {
      try {
        await resolveApproval(runId, approvalRequest.id, "reject");
      } catch {
        // In demo mode, just resolve locally
      }
    }
    approvalResolverRef.current?.();
    setApprovalRequest(null);
  }, [approvalRequest, runId]);

  /* ── Reset ───────────────────────────────────────────────────────────── */
  const handleReset = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (sseCleanupRef.current) sseCleanupRef.current();
    setStatus("idle");
    setNodes([]);
    setEdges([]);
    setSelectedNode(null);
    setRunId(null);
    setReport(null);
    setMetrics({ totalTokens: 0, totalCost: 0, nodesCompleted: 0, nodesTotal: 0, elapsedMs: 0, nodeLatencies: {} });
    setEventLog([]);
    setGoalText("");
  }, []);

  /* ── Render ──────────────────────────────────────────────────────────── */
  return (
    <div className="page-container">
      {/* Header */}
      <header className="page-header" id="page-header">
        <div className="flex items-center gap-md">
          <Workflow size={22} strokeWidth={2.5} style={{ color: "var(--accent-secondary)" }} />
          <h1>AE-03 Orchestrator</h1>
        </div>
        <div className="flex items-center gap-md">
          <div className="flex items-center gap-sm" style={{ fontSize: 13 }}>
            <span
              className={`status-dot ${status === "idle" ? "idle" : status === "running" || status === "compiling" ? "running" : status === "success" ? "success" : "failed"}`}
            />
            <span style={{ color: "var(--text-secondary)", textTransform: "capitalize" }}>
              {status === "compiling" ? "Compiling…" : status}
            </span>
            {runId && (
              <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                {runId}
              </span>
            )}
          </div>
          {status !== "idle" && (
            <button
              className="btn btn-ghost"
              onClick={handleReset}
              style={{ padding: "4px 10px", fontSize: 12 }}
              id="reset-btn"
            >
              <RotateCcw size={13} />
              Reset
            </button>
          )}
        </div>
      </header>

      {/* Canvas */}
      <div className="canvas-area" id="graph-canvas-area">
        <GraphCanvas
          nodes={nodes}
          edges={edges}
          onNodeClick={setSelectedNode}
          selectedNodeId={selectedNode?.id ?? null}
        />
      </div>

      {/* Sidebar */}
      <aside className="sidebar" id="sidebar">
        {/* Goal Input */}
        <div className="glass-card goal-panel" id="goal-panel">
          <label className="label" htmlFor="goal-input">
            Goal Prompt
          </label>
          <textarea
            id="goal-input"
            className="textarea"
            placeholder="Describe your goal… e.g. 'Research the impact of AI on healthcare and generate a report'"
            value={goalText}
            onChange={(e) => setGoalText(e.target.value)}
            disabled={status === "running" || status === "compiling"}
          />
          <div className="goal-actions">
            <select
              id="provider-select"
              className="select"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              disabled={status === "running" || status === "compiling"}
            >
              <option value="google">Google Gemini</option>
              <option value="openai">OpenAI</option>
              <option value="ollama">Ollama (Local)</option>
            </select>
            <button
              id="compile-run-btn"
              className="btn btn-primary"
              onClick={handleCompileAndRun}
              disabled={!goalText.trim() || status === "running" || status === "compiling"}
            >
              {status === "compiling" ? (
                <Loader2 size={15} className="animate-spin" />
              ) : status === "running" ? (
                <Activity size={15} />
              ) : (
                <Play size={15} />
              )}
              {status === "compiling"
                ? "Compiling…"
                : status === "running"
                  ? "Executing…"
                  : "Compile & Run"}
            </button>
          </div>
        </div>

        {/* Node Detail */}
        {selectedNode && (
          <div className="glass-card node-detail-panel" id="node-detail-panel">
            <h3>
              <Cpu size={15} style={{ color: "var(--accent-secondary)" }} />{" "}
              {selectedNode.label}
            </h3>
            <div className="node-detail-row">
              <span className="detail-label">ID</span>
              <span className="detail-value" style={{ fontFamily: "var(--font-mono)" }}>
                {selectedNode.id}
              </span>
            </div>
            <div className="node-detail-row">
              <span className="detail-label">Role</span>
              <span className="detail-value">{selectedNode.role}</span>
            </div>
            <div className="node-detail-row">
              <span className="detail-label">Status</span>
              <span className={`tag tag-${selectedNode.status === "waiting_approval" ? "approval" : selectedNode.status}`}>
                {selectedNode.status.replace("_", " ")}
              </span>
            </div>
            {metrics.nodeLatencies[selectedNode.id] && (
              <div className="node-detail-row">
                <span className="detail-label">Latency</span>
                <span className="detail-value">
                  {metrics.nodeLatencies[selectedNode.id]}ms
                </span>
              </div>
            )}
          </div>
        )}

        {/* Report */}
        {status === "success" && (
          <div className="glass-card" id="report-panel" style={{ padding: "12px 16px" }}>
            <div className="flex items-center gap-sm" style={{ marginBottom: 8 }}>
              <FileText size={15} style={{ color: "var(--accent-emerald)" }} />
              <h3 style={{ margin: 0, fontSize: 14 }}>Run Report</h3>
            </div>
            {report ? (
              <div style={{ fontSize: 12, color: "var(--text-secondary)", maxHeight: 200, overflow: "auto" }}>
                <p style={{ whiteSpace: "pre-wrap" }}>{report.report_content}</p>
                <div className="node-detail-row" style={{ marginTop: 8 }}>
                  <span className="detail-label">Cost</span>
                  <span className="detail-value">${report.cost_summary?.total_cost_usd?.toFixed(4) ?? "0"}</span>
                </div>
                <div className="node-detail-row">
                  <span className="detail-label">Events</span>
                  <span className="detail-value">{report.event_count}</span>
                </div>
              </div>
            ) : (
              <button className="btn btn-ghost" onClick={handleFetchReport} style={{ fontSize: 12 }}>
                Fetch Report
              </button>
            )}
          </div>
        )}

        {/* Metrics */}
        <MetricsPanel metrics={metrics} eventLog={eventLog} status={status} />
      </aside>

      {/* Approval Modal */}
      {approvalRequest && (
        <ApprovalModal
          request={approvalRequest}
          onApprove={handleApprove}
          onReject={handleReject}
        />
      )}
    </div>
  );
}

/* ── Helpers ───────────────────────────────────────────────────────────── */
function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatEventMessage(
  eventType: string,
  data: Record<string, unknown>,
  agentRole: string,
  durationMs: number,
): string {
  switch (eventType) {
    case "RUN_CREATED": return `Run created: ${data.goal || ""}`;
    case "PLAN_CREATED": return `Plan created: ${data.task_count || 0} tasks`;
    case "AGENT_STARTED": return `${agentRole} started: ${data.description || ""}`;
    case "AGENT_COMPLETED": return `${agentRole} completed (${Math.round(durationMs)}ms, ${data.tokens || 0} tokens)`;
    case "AGENT_FAILED": return `${agentRole} failed: ${data.error || "unknown"}`;
    case "TOOL_REQUESTED": return `Tool requested: ${data.tool_name || ""}`;
    case "TOOL_ALLOWED": return `Tool allowed: ${data.tool_name || ""}`;
    case "TOOL_DENIED": return `⚠ Tool denied: ${data.tool_name || ""} — ${data.reason || ""}`;
    case "TOOL_EXECUTED": return `Tool executed: ${data.tool_name || ""}`;
    case "SECURITY_CHECK": return `Security: ${data.verdict || ""} (${data.rule || ""})`;
    case "APPROVAL_REQUESTED": return `Approval required: ${data.tool_name || ""}`;
    case "APPROVED": return `Approved: ${data.approval_id || ""}`;
    case "REJECTED": return `Rejected: ${data.approval_id || ""} — ${data.reason || ""}`;
    case "RETRY": return `Retrying ${agentRole}`;
    case "RUN_COMPLETED": return `Run completed: ${data.status || "unknown"} ($${(data.total_cost_usd as number)?.toFixed(4) || "0"})`;
    default: return `${eventType}: ${JSON.stringify(data).slice(0, 80)}`;
  }
}
