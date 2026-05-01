##
# Date: Feb 24, 2026
# Author: Ha Tran
# Description: AgentState — the "medical record" of the repair process.
#
# DESIGN NOTES (Stabilization patch):
# - iteration_count uses default overwrite semantics. Only ONE node (verifier)
#   is responsible for incrementing it, by writing the new absolute value.
#   This eliminates the double-count caused by operator.add accumulation when
#   multiple nodes returned this key.
# - fix_history keeps operator.add (concat) — this is the correct semantic:
#   each coder pass appends one new candidate.
# - review_attempts is a NEW dedicated counter for the coder<->reviewer loop,
#   so it cannot be confused with verifier iterations.
##

from typing import TypedDict, Annotated, Optional, List
import operator


class AgentState(TypedDict):
    # ---------- Inputs (immutable across the run) ----------
    project_name: str
    raw_spec: str
    buggy_verilog: str
    design_contract: dict

    # ---------- Working artifacts ----------
    formal_properties: str          # rendered SVA module
    current_verilog: str            # latest candidate RTL

    # ---------- Verification feedback ----------
    verification_status: Optional[str]   # PASS | FAIL | FAIL_VACUOUS | ERROR | FATAL_ERROR | IR_READY | REVIEW_STAGE_*
    error_log: Optional[str]
    cex_data: Optional[dict]
    cex_analysis: str

    # ---------- Tracking ----------
    fix_history: Annotated[List[str], operator.add]   # append-only list of candidate RTLs
    reviewer_feedback: str
    iteration_count: int                              # OVERWRITE semantics — owned by verifier_node
    review_attempts: int                              # OVERWRITE semantics — owned by reviewer_node
