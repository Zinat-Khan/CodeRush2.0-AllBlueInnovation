"use client";

import { useEffect, useRef } from "react";
import {
  BarChart3,
  Coins,
  Hash,
  Timer,
  CheckCircle2,
  Layers,
} from "lucide-react";

/* ── Public Types ──────────────────────────────────────────────────────── */
export interface Metrics {
  totalTokens: number;
  totalCost: number;
  nodesCompleted: number;
  nodesTotal: number;
  elapsedMs: number;
  nodeLatencies: Record<string, number>;
}

export interface EventLogEntry {
  id: string;
  timestamp: number;
  type: string;
  nodeId: string;
  message: string;
}

interface MetricsPanelProps {
  metrics: Metrics;
  eventLog: EventLogEntry[];
  status: string;
}

/* ── Event type → color ───────────────────────────────────────────────── */
const EVENT_COLORS: Record<string, string> = {
  SYSTEM: "var(--text-muted)",
  COMPILE: "var(--accent-violet)",
  NODE_START: "var(--status-running)",
  NODE_END: "var(--status-success)",
  APPROVAL: "var(--status-approval)",
  APPROVED: "var(--accent-emerald)",
  ERROR: "var(--status-failed)",
};

/* ── Metrics Panel Component ──────────────────────────────────────────── */
export default function MetricsPanel({
  metrics,
  eventLog,
  status,
}: MetricsPanelProps) {
  const logEndRef = useRef<HTMLDivElement>(null);

  /* Auto-scroll log to bottom */
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [eventLog.length]);

  const maxLatency = Math.max(1, ...Object.values(metrics.nodeLatencies));

  return (
    <div className="glass-card metrics-panel" id="metrics-panel">
      {/* ── Metric Grid ──────────────────────────────────────────────── */}
      <div className="flex items-center gap-sm" style={{ marginBottom: 12 }}>
        <BarChart3 size={15} style={{ color: "var(--accent-secondary)" }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
          Live Metrics
        </span>
      </div>

      <div className="metric-grid">
        <div className="metric-item">
          <div className="metric-label">
            <Hash size={10} style={{ display: "inline", verticalAlign: "middle" }} /> Tokens
          </div>
          <div className="metric-value tokens">
            {metrics.totalTokens.toLocaleString()}
          </div>
        </div>

        <div className="metric-item">
          <div className="metric-label">
            <Coins size={10} style={{ display: "inline", verticalAlign: "middle" }} /> Cost
          </div>
          <div className="metric-value cost">
            ${metrics.totalCost.toFixed(4)}
          </div>
        </div>

        <div className="metric-item">
          <div className="metric-label">
            <CheckCircle2 size={10} style={{ display: "inline", verticalAlign: "middle" }} /> Nodes
          </div>
          <div className="metric-value">
            {metrics.nodesCompleted}
            <span style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 400 }}>
              /{metrics.nodesTotal}
            </span>
          </div>
        </div>

        <div className="metric-item">
          <div className="metric-label">
            <Timer size={10} style={{ display: "inline", verticalAlign: "middle" }} /> Elapsed
          </div>
          <div className="metric-value">
            {(metrics.elapsedMs / 1000).toFixed(1)}
            <span style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 400 }}>s</span>
          </div>
        </div>
      </div>

      {/* ── Per-Node Latency Bars ────────────────────────────────────── */}
      {Object.keys(metrics.nodeLatencies).length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div
            className="flex items-center gap-sm"
            style={{ marginBottom: 8, fontSize: 12, fontWeight: 600, color: "var(--text-muted)" }}
          >
            <Layers size={12} />
            Node Latency
          </div>
          {Object.entries(metrics.nodeLatencies).map(([nodeId, latencyMs]) => (
            <div className="latency-bar-container" key={nodeId}>
              <div className="latency-bar-label">
                <span style={{ color: "var(--text-secondary)" }}>{nodeId}</span>
                <span style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                  {latencyMs}ms
                </span>
              </div>
              <div className="latency-bar-track">
                <div
                  className="latency-bar-fill"
                  style={{ width: `${(latencyMs / maxLatency) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Event Log ────────────────────────────────────────────────── */}
      {eventLog.length > 0 && (
        <div>
          <div
            className="flex items-center gap-sm"
            style={{ marginBottom: 8, fontSize: 12, fontWeight: 600, color: "var(--text-muted)" }}
          >
            Execution Log
          </div>
          <div className="event-log" id="event-log">
            {eventLog.map((entry) => {
              const elapsedSec = ((entry.timestamp - eventLog[0].timestamp) / 1000).toFixed(1);
              return (
                <div className="event-log-entry" key={entry.id}>
                  <span className="event-time">{elapsedSec}s</span>
                  <span
                    className="event-type"
                    style={{ color: EVENT_COLORS[entry.type] || "var(--text-secondary)" }}
                  >
                    {entry.type}
                  </span>
                  <span className="event-node">{entry.nodeId !== "-" ? entry.nodeId : ""}</span>
                  <span style={{ color: "var(--text-secondary)", flex: 1 }}>
                    {entry.message}
                  </span>
                </div>
              );
            })}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {/* Empty state */}
      {status === "idle" && eventLog.length === 0 && (
        <div style={{ textAlign: "center", padding: 16, color: "var(--text-muted)", fontSize: 13 }}>
          Metrics will appear once execution begins.
        </div>
      )}
    </div>
  );
}
