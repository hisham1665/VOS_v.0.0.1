"""Phase 14 security layer -- the parts the kernel can honestly own.

Per ``AOS_INTEGRATION_SPEC.md`` (Ph14) the heavy controls (transport auth,
role-based authz enforcement at the HTTP layer, student data isolation,
secure file storage backends, full API security) live on the deployment/API
layer, not in this batch CLI. This package provides the kernel-side contracts
the API layer and operators use:

* ``input_validation`` -- reject malformed/traversal/oversized sources before
  they reach the pipeline (defense in depth on the accept path, exact same
  go/error semantics as the config collector).
* ``access_log`` -- append-only, atomic ``access.log.jsonl`` audit of who did
  what and whether it succeeded (access logs).
* ``policy`` -- the role -> action capability matrix and ``authorize`` helper
  the (future) API gateway enforces; roles: examiner, admin, reviewer.
"""

from .access_log import AccessLog, AccessEvent, access_counts
from .input_validation import (
    PathIssue,
    validate_exam_payload,
    validate_paper_source,
)
from .policy import (
    RBAC_RULE,
    ROLE_EXAMINER,
    ROLE_ADMIN,
    ROLE_REVIEWER,
    authorize,
)

__all__ = [
    "AccessEvent",
    "AccessLog",
    "access_counts",
    "PathIssue",
    "validate_exam_payload",
    "validate_paper_source",
    "RBAC_RULE",
    "ROLE_EXAMINER",
    "ROLE_ADMIN",
    "ROLE_REVIEWER",
    "authorize",
]