"""Pinned protocol/library versions (BRD 14 §8: one constants module).

Everything version-bearing that crosses a wire or a compliance record is pinned
here so a bump is a single, reviewable change.
"""

from __future__ import annotations

# Pinned SDK / protocol versions (BRD 14 §8 contract note).
A2A_PROTOCOL_VERSION = "1.0"
LANGGRAPH_MAJOR = "0.2+"
TEMPORAL_SDK = "temporalio>=1.7"

# The proposal-execution grant issuer identity. tool-plane's ProposalVerifier is
# configured with PROPOSAL_JWT_ISSUER = this exact string and rejects any other
# `iss` (services/tool-plane/internal/authz/proposal.go).
GRANT_ISSUER = "windrose-agent-runtime"

# Grant lifetime — short-lived per the tool-plane contract ("≤ a few minutes").
GRANT_TTL_SECONDS = 120

# Kafka topics (BRD 14 §6).
TOPIC_AGENT_RUN = "ai.agent_run.v1"
TOPIC_PROPOSAL = "ai.proposal.v1"
TOPIC_CODE_EXECUTED = "ai.code_executed.v1"
TOPIC_AGENT_EVENTS = "agent.events.v1"

# Temporal task queue (BRD 14 §4 Temporal design notes).
TEMPORAL_TASK_QUEUE = "agents-pool"

# Session model (ART-FR-021).
IDLE_TIMEOUT_SECONDS = 15 * 60
MAX_LIFETIME_SECONDS = 8 * 3600

# Proposal defaults (ART-FR-041).
PROPOSAL_DEFAULT_TTL_SECONDS = 7 * 24 * 3600

# Reflection hard cap (BR-5).
MAX_REFLECTIONS_HARD_CAP = 3

# Tool tiers (mirrors tool-plane domain).
TIER_READ = "read"
TIER_WRITE_PROPOSAL = "write-proposal"
TIER_WRITE_DIRECT = "write-direct"
TIER_ADMIN = "admin"

# Side-effect classes used by the auto-execute policy matrix (ART-FR-043).
SIDE_EFFECTS = ("none", "reversible", "destructive")
