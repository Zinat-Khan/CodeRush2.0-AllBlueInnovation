"use client";

import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  type NodeTypes,
  Handle,
  Position,
  BackgroundVariant,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Brain,
  Search,
  Code2,
  ShieldCheck,
  FileText,
  GitBranch,
  Bot,
  Workflow,
} from "lucide-react";

/* ── Public Types ──────────────────────────────────────────────────────── */
export type NodeStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "retrying"
  | "waiting_approval";

export interface AgentNode {
  id: string;
  role: string;
  label: string;
  status: NodeStatus;
  x: number;
  y: number;
}

interface GraphCanvasProps {
  nodes: AgentNode[];
  edges: [string, string][];
  onNodeClick: (node: AgentNode) => void;
  selectedNodeId: string | null;
}

/* ── Status → Color Map ───────────────────────────────────────────────── */
const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: "var(--status-pending)",
  running: "var(--status-running)",
  success: "var(--status-success)",
  failed: "var(--status-failed)",
  retrying: "var(--status-retrying)",
  waiting_approval: "var(--status-approval)",
};

const STATUS_BG: Record<NodeStatus, string> = {
  pending: "rgba(107, 114, 128, 0.10)",
  running: "rgba(245, 158, 11, 0.12)",
  success: "rgba(34, 197, 94, 0.12)",
  failed: "rgba(239, 68, 68, 0.12)",
  retrying: "rgba(249, 115, 22, 0.12)",
  waiting_approval: "rgba(59, 130, 246, 0.12)",
};

/* ── Role → Icon Map ──────────────────────────────────────────────────── */
const ROLE_ICONS: Record<string, React.ReactNode> = {
  PLANNER: <Brain size={16} />,
  RESEARCHER: <Search size={16} />,
  EXECUTOR: <Code2 size={16} />,
  VERIFIER: <ShieldCheck size={16} />,
  REPORTER: <FileText size={16} />,
  SUB_GRAPH: <GitBranch size={16} />,
  ANALYST: <Bot size={16} />,
};

/* ── Custom Agent Node ─────────────────────────────────────────────────── */
type AgentNodeData = {
  label: string;
  role: string;
  status: NodeStatus;
  isSelected: boolean;
};

function AgentNodeComponent({ data }: NodeProps<Node<AgentNodeData>>) {
  const { label, role, status, isSelected } = data;
  const color = STATUS_COLORS[status];
  const bg = STATUS_BG[status];
  const icon = ROLE_ICONS[role] || <Bot size={16} />;

  return (
    <>
      <Handle
        type="target"
        position={Position.Top}
        style={{
          width: 8,
          height: 8,
          background: color,
          border: "2px solid var(--bg-secondary)",
        }}
      />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 16px",
          background: bg,
          border: `1.5px solid ${isSelected ? color : "var(--border-default)"}`,
          borderRadius: "var(--radius-md)",
          minWidth: 150,
          cursor: "pointer",
          transition: "all 200ms ease",
          boxShadow: isSelected
            ? `0 0 16px ${color}40`
            : status === "running"
              ? `0 0 12px ${color}30`
              : "none",
          ...(status === "running"
            ? { animation: "pulse-glow 2s ease-in-out infinite" }
            : {}),
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: "var(--radius-sm)",
            background: `${color}20`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: color,
            flexShrink: 0,
          }}
        >
          {icon}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-primary)",
              marginBottom: 2,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {label}
          </div>
          <div
            style={{
              fontSize: 10,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: color,
            }}
          >
            {status.replace("_", " ")}
          </div>
        </div>
        {/* Pulsing dot for running / approval */}
        {(status === "running" || status === "waiting_approval") && (
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: color,
              animation: "pulse-glow 1.5s ease-in-out infinite",
              flexShrink: 0,
            }}
          />
        )}
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{
          width: 8,
          height: 8,
          background: color,
          border: "2px solid var(--bg-secondary)",
        }}
      />
    </>
  );
}

const nodeTypes: NodeTypes = {
  agentNode: AgentNodeComponent as unknown as NodeTypes[string],
};

/* ── Graph Canvas Component ────────────────────────────────────────────── */
export default function GraphCanvas({
  nodes: agentNodes,
  edges: agentEdges,
  onNodeClick,
  selectedNodeId,
}: GraphCanvasProps) {
  /* Convert to React Flow format */
  const rfNodes: Node<AgentNodeData>[] = useMemo(
    () =>
      agentNodes.map((n) => ({
        id: n.id,
        type: "agentNode",
        position: { x: n.x, y: n.y },
        data: {
          label: n.label,
          role: n.role,
          status: n.status,
          isSelected: n.id === selectedNodeId,
        },
      })),
    [agentNodes, selectedNodeId]
  );

  const rfEdges: Edge[] = useMemo(
    () =>
      agentEdges.map(([source, target]) => {
        const sourceNode = agentNodes.find((n) => n.id === source);
        const isActive =
          sourceNode?.status === "running" || sourceNode?.status === "success";
        return {
          id: `${source}-${target}`,
          source,
          target,
          animated: sourceNode?.status === "running",
          style: {
            stroke: isActive
              ? "var(--accent-secondary)"
              : "var(--border-strong)",
            strokeWidth: isActive ? 2 : 1.5,
            opacity: isActive ? 1 : 0.5,
          },
        };
      }),
    [agentEdges, agentNodes]
  );

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node<AgentNodeData>) => {
      const agentNode = agentNodes.find((n) => n.id === node.id);
      if (agentNode) onNodeClick(agentNode);
    },
    [agentNodes, onNodeClick]
  );

  /* Empty state */
  if (agentNodes.length === 0) {
    return (
      <div className="empty-state">
        <Workflow size={48} strokeWidth={1.2} />
        <p>
          Enter a goal and click <strong>Compile & Run</strong> to generate
          an execution graph.
        </p>
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      onNodeClick={handleNodeClick}
      fitView
      fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
      proOptions={{ hideAttribution: true }}
      minZoom={0.3}
      maxZoom={2}
      nodesDraggable={false}
      nodesConnectable={false}
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={24}
        size={1}
        color="var(--border-subtle)"
      />
      <Controls
        showInteractive={false}
        style={{ bottom: 16, left: 16 }}
      />
      <MiniMap
        nodeColor={(n: Node) => {
          const data = n.data as AgentNodeData;
          return STATUS_COLORS[data?.status || "pending"];
        }}
        maskColor="rgba(6, 8, 13, 0.7)"
        style={{ bottom: 16, right: 16, width: 120, height: 80 }}
      />
    </ReactFlow>
  );
}
