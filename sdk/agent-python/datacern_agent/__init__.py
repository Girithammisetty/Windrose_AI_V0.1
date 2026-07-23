"""Datacern external-agent SDK — govern your own agent's writes (BRD 60 WS5)."""

from datacern_agent.client import (
    DatacernAgentClient,
    DatacernAgentError,
    Proposal,
    WRITE_PROPOSAL,
)

__all__ = ["DatacernAgentClient", "DatacernAgentError", "Proposal", "WRITE_PROPOSAL"]
__version__ = "0.1.0"
