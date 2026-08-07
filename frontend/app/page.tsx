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
} from "lucide-react";
import GraphCanvas, { type AgentNode } from "@/components/GraphCanvas";
import MetricsPanel, { type Metrics, type EventLogEntry } from "@/components/MetricsPanel";
import ApprovalModal, { type ApprovalRequest } from "@/components/ApprovalModal";

/* ── Status types ──────────────────────────────────────────────────────── */
type RunStatus = "idle" | "compiling" | "running" | "success" | "failed";

/* ── Demo data ─────────────────────────────────────────────────────────── */
const DEMO_NODES: AgentNode[] = [
  { id: "planner",    role: "PLANNER",    label: "Planner",      status: "pending",  x: 250, y: 0 },
  { id: "researcher", role: "RESEARCHER", label: "Researcher",   status: "pending",  x: 80,  y: 140 },
  { id: "executor",   role: "EXECUTOR",   label: "Code Executor", status: "pending", x: 420, y: 140 },
  { id: "verifier",   role: "VERIFIER",   label: "Verifier",     status: "pending",  x: 150, y: 280 },
  { id: "reporter",   role: "REPORTER",   label: "Reporter",     status: "pending",  x: 350, y: 280 },
];

const DEMO_EDGES: [string, string][] = [
  ["planner", "researcher"],
  ["planner", "executor"],
  ["researcher", "verifier"],
  ["executor", "verifier"],
  ["verifier", "reporter"],
];

/* ── Page Component ────────────────────────────────────────────────────── */
export default function OrchestratorPage() {
  const [goalText, setGoalText] = useState("");
  const [provider, setProvider] = useState("openai");
  const [status, setStatus] = useState<RunStatus>("idle");
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
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number>(0);

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

  /* ── Simulate compile + run ──────────────────────────────────────────── */
  const handleCompileAndRun = useCallback(async () => {
    if (!goalText.trim()) return;

    /* Phase 1: Compile */
    setStatus("compiling");
    setSelectedNode(null);
    setEventLog([]);
    addLogEntry("SYSTEM", "-", `Compiling goal with ${provider}...`);

    await delay(1200);

    const graphNodes = DEMO_NODES.map((n) => ({ ...n, status: "pending" as const }));
    setNodes(graphNodes);
    setEdges(DEMO_EDGES);
    setMetrics({
      totalTokens: 0,
      totalCost: 0,
      nodesCompleted: 0,
      nodesTotal: graphNodes.length,
      elapsedMs: 0,
      nodeLatencies: {},
    });

    addLogEntry("COMPILE", "-", `Graph compiled: ${graphNodes.length} nodes, ${DEMO_EDGES.length} edges`);
    await delay(600);

    /* Phase 2: Execute */
    setStatus("running");
    startTimeRef.current = Date.now();

    /* Start elapsed timer */
    timerRef.current = setInterval(() => {
      setMetrics((prev) => ({
        ...prev,
        elapsedMs: Date.now() - startTimeRef.current,
      }));
    }, 100);

    const executionOrder = ["planner", "researcher", "executor", "verifier", "reporter"];

    for (let i = 0; i < executionOrder.length; i++) {
      const nodeId = executionOrder[i];
      const node = graphNodes.find((n) => n.id === nodeId)!;

      /* Mark running */
      setNodes((prev) =>
        prev.map((n) => (n.id === nodeId ? { ...n, status: "running" as const } : n))
      );
      addLogEntry("NODE_START", nodeId, `${node.label} started`);

      /* Simulate HITL approval for verifier */
      if (nodeId === "verifier") {
        setNodes((prev) =>
          prev.map((n) =>
            n.id === nodeId ? { ...n, status: "waiting_approval" as const } : n
          )
        );
        addLogEntry("APPROVAL", nodeId, "Approval required: schema validation");
        setApprovalRequest({
          id: `apr-${Date.now()}`,
          nodeId: "verifier",
          agentRole: "VERIFIER",
          tool: "validate_output",
          payload: { schema: "ExecutionResult", action: "verify_output_schema" },
        });

        /* Wait for approval */
        await waitForApproval();
        setApprovalRequest(null);
        addLogEntry("APPROVED", nodeId, "Approved by human operator");

        /* Resume running */
        setNodes((prev) =>
          prev.map((n) => (n.id === nodeId ? { ...n, status: "running" as const } : n))
        );
      }

      /* Simulate work */
      const workDuration = 800 + Math.random() * 1500;
      await delay(workDuration);

      /* Mark success */
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
        nodeLatencies: {
          ...prev.nodeLatencies,
          [nodeId]: Math.round(workDuration),
        },
      }));
    }

    /* Done */
    if (timerRef.current) clearInterval(timerRef.current);
    setStatus("success");
    addLogEntry("SYSTEM", "-", "Execution complete!");
  }, [goalText, provider, addLogEntry]);

  /* ── Approval wait (resolved externally) ─────────────────────────────── */
  const approvalResolverRef = useRef<(() => void) | null>(null);

  const waitForApproval = () =>
    new Promise<void>((resolve) => {
      approvalResolverRef.current = resolve;
    });

  const handleApprove = () => {
    approvalResolverRef.current?.();
  };
  const handleReject = () => {
    approvalResolverRef.current?.();
  };

  /* ── Reset ───────────────────────────────────────────────────────────── */
  const handleReset = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    setStatus("idle");
    setNodes([]);
    setEdges([]);
    setSelectedNode(null);
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
            placeholder="Describe your goal… e.g. 'Audit the REST API security and generate a report'"
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
              <option value="openai">OpenAI</option>
              <option value="gemini">Gemini</option>
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
