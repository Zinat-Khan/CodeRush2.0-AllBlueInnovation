"use client";

import { useCallback, useEffect } from "react";
import { ShieldAlert, Check, X } from "lucide-react";

/* -- Public Types -------------------------------------------------------- */
export interface ApprovalRequest {
  id: string;
  nodeId: string;
  agentRole: string;
  tool: string;
  payload: Record<string, unknown>;
}

interface ApprovalModalProps {
  request: ApprovalRequest;
  onApprove: () => void;
  onReject: () => void;
}

/* -- Approval Modal Component -------------------------------------------- */
export default function ApprovalModal({
  request,
  onApprove,
  onReject,
}: ApprovalModalProps) {
  /* Close on Escape */
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onReject();
    },
    [onReject]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div
      className="modal-overlay"
      id="approval-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onReject();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-title"
    >
      <div className="modal-content">
        <h2 id="approval-title">
          <ShieldAlert size={20} />
          Human Approval Required
        </h2>

        <div className="modal-section">
          <div className="modal-section-label">Agent</div>
          <div className="modal-section-value">
            <span style={{ color: "var(--accent-secondary)" }}>
              {request.nodeId}
            </span>
            <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>
              ({request.agentRole})
            </span>
          </div>
        </div>

        <div className="modal-section">
          <div className="modal-section-label">Requested Tool</div>
          <div className="modal-section-value" style={{ color: "var(--accent-cyan)" }}>
            {request.tool}
          </div>
        </div>

        <div className="modal-section">
          <div className="modal-section-label">Payload Preview</div>
          <div className="modal-section-value">
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 12,
                margin: 0,
                color: "var(--text-secondary)",
              }}
            >
              {JSON.stringify(request.payload, null, 2)}
            </pre>
          </div>
        </div>

        <div className="modal-actions">
          <button
            id="reject-btn"
            className="btn btn-danger"
            onClick={onReject}
          >
            <X size={15} />
            Reject
          </button>
          <button
            id="approve-btn"
            className="btn btn-success"
            onClick={onApprove}
            autoFocus
          >
            <Check size={15} />
            Approve
          </button>
        </div>
      </div>
    </div>
  );
}

