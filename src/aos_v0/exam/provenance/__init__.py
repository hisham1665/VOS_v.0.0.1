"""Phase 14 reproducibility record.

Stores everything needed to re-derive one evaluation: exam identity/version,
answer-key & rubric content versions, the models selected per capability
(trace-derived), capability DNA, AOS configuration fingerprint, timestamp and
the evaluation result. ``fingerprint`` is a stable digest over the *immutable*
fields so integrity/immutability can be verified later; the timestamp is an
attribute, not part of the identity.
"""

from .provenance import (
    EvaluationProvenance,
    build_provenance,
    fingerprint,
    provenance_cli_main,
    provenance_fingerprint,
    verify_provenance,
)

__all__ = [
    "EvaluationProvenance",
    "build_provenance",
    "fingerprint",
    "provenance_cli_main",
    "provenance_fingerprint",
    "verify_provenance",
]