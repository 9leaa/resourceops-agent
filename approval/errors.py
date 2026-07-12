from __future__ import annotations


class ApprovalTransitionConflict(ValueError):
    """Raised when an approval state transition loses a conditional update race."""
