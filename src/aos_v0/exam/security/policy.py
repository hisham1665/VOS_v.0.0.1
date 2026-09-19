"""Role-based authorization policy (role -> action capability matrix).

This is the *policy* and the ``authorize`` decision helper. Enforcement at an
actual HTTP boundary is the API-layer responsibility (spec Ph14); the kernel
exposes the policy so the gateway cannot drift from the batch CLI's own
intent, and the reviewer-run permissions match the Phase-11 review operations.
"""

from __future__ import annotations

from typing import List

ROLE_EXAMINER = "examiner"
ROLE_ADMIN = "admin"
ROLE_REVIEWER = "reviewer"

# action namespace for the exam pipeline
ACT_RUN_BATCH = "exam.batch.run"
ACT_RUN_REPORTING = "exam.reporting.run"
ACT_RUN_RESEARCH = "exam.research.run"
ACT_RUN_HEALTH = "system.health.check"
ACT_REVIEW_LIST = "review.list"
ACT_REVIEW_RESOLVE = "review.resolve"
ACT_AUDIT_READ = "audit.read"
ACT_USERS_MANAGE = "system.users.manage"

# role -> allowed actions
RBAC_RULE: dict = {
    ROLE_EXAMINER: [
        ACT_RUN_BATCH,
        ACT_RUN_REPORTING,
        ACT_RUN_RESEARCH,
        ACT_RUN_HEALTH,
        ACT_REVIEW_LIST,
        ACT_REVIEW_RESOLVE,
        ACT_AUDIT_READ,
    ],
    ROLE_ADMIN: [
        ACT_RUN_BATCH,
        ACT_RUN_REPORTING,
        ACT_RUN_RESEARCH,
        ACT_RUN_HEALTH,
        ACT_REVIEW_LIST,
        ACT_REVIEW_RESOLVE,
        ACT_AUDIT_READ,
        ACT_USERS_MANAGE,
    ],
    ROLE_REVIEWER: [ACT_REVIEW_LIST, ACT_REVIEW_RESOLVE],
}


def actions_for(role: str) -> List[str]:
    return list(RBAC_RULE.get(role, []))


def authorize(role: str, action: str) -> bool:
    """Decide whether ``role`` may perform ``action`` (pure denylist-safe)."""
    return action in actions_for(role)