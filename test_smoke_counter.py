"""
Stage A smoke test - counter benchmark.

This test does TWO things:
  1. Verifies that the Stage A patches are actually active (not the old code)
  2. Runs the full pipeline on the simplest benchmark to confirm
     the agent loop completes without crashing.

If the precondition checks at the top fail, it means the patches were
not installed correctly. Run `bash install_stage_a.sh` again.
"""

from __future__ import annotations

import os
import sys


# ---------- Precondition checks (must pass BEFORE we spend any tokens) ----------

def check_patches_active() -> None:
    """Cheap, deterministic checks that the Stage A patches are in effect."""
    from agents.state import AgentState

    # Patch #1 added review_attempts as a tracked state key.
    if "review_attempts" not in AgentState.__annotations__:
        raise SystemExit(
            "[FATAL] AgentState has no 'review_attempts' key. "
            "Stage A patches are NOT installed. Run install_stage_a.sh."
        )

    # Patch #2 changed iteration_count from Annotated[int, operator.add]
    # to plain int (overwrite semantics). Annotated objects expose __metadata__.
    iter_ann = AgentState.__annotations__["iteration_count"]
    has_add_reducer = (
        hasattr(iter_ann, "__metadata__")
        and any(getattr(m, "__name__", "") == "add"
                for m in getattr(iter_ann, "__metadata__", ()))
    )
    if has_add_reducer:
        raise SystemExit(
            "[FATAL] iteration_count still uses operator.add reducer. "
            "Old state.py is still active. Run install_stage_a.sh."
        )

    # Patch #3: route_after_architect must reject empty SVA.
    from main_graph import route_after_architect
    fake_state = {"verification_status": "IR_READY", "formal_properties": ""}
    decision = route_after_architect(fake_state)  # type: ignore[arg-type]
    from langgraph.graph import END
    if decision != END:
        raise SystemExit(
            "[FATAL] route_after_architect does not fail-fast on empty SVA. "
            "Old main_graph.py is still active. Run install_stage_a.sh."
        )

    print("[precheck] Stage A patches confirmed active.")


# ---------- Main ----------

def main() -> int:
    os.makedirs("benchmarks", exist_ok=True)

    check_patches_active()

    spec = (
        "Parameterized synchronous up-counter (default WIDTH=8). On rising edge "
        "of clk: if rst is high, count is set to 0; else if en is high, count "
        "increments by 1; otherwise count holds. Wraps around on overflow."
    )

    with open("benchmarks_buggy/counter.v", "r") as f:
        code = f.read()

    print("=" * 60)
    print("STAGE A SMOKE TEST: counter benchmark")
    print("=" * 60)

    from main_graph import run_repair_system
    result = run_repair_system("counter", spec, code)

    status = result.get("verification_status", "UNKNOWN")
    iters = result.get("iteration_count", 0)
    review_attempts = result.get("review_attempts", 0)
    sva = result.get("formal_properties", "")

    print()
    print("=" * 60)
    print("RESULT SUMMARY")
    print("=" * 60)
    print(f"Status:           {status}")
    print(f"Iterations:       {iters}")
    print(f"Review attempts:  {review_attempts}")
    print(f"Has SVA:          {bool(sva.strip())}")
    print(f"SVA length:       {len(sva)} chars")

    print()
    print("=" * 60)
    print("STAGE A INVARIANT CHECKS")
    print("=" * 60)
    checks = []

    # Bug #1 fix: iteration_count must be bounded by MAX_ITERATIONS.
    from main_graph import MAX_ITERATIONS
    bug1 = 0 <= iters <= MAX_ITERATIONS
    checks.append(("Bug #1 (iter count bounded)", bug1,
                   f"iters={iters}, max={MAX_ITERATIONS}"))

    # Bug #3 fix: a non-FATAL run must have produced SVA.
    bug3 = bool(sva.strip()) or status == "FATAL_ERROR"
    checks.append(("Bug #3 (SVA non-empty if not FATAL)", bug3,
                   f"len(sva)={len(sva)} status={status}"))

    # Bug #4 fix: review_attempts must be an integer (was 'None' before patch).
    bug4 = isinstance(review_attempts, int)
    checks.append(("Bug #4 (review_attempts is int)", bug4,
                   f"type={type(review_attempts).__name__}"))

    # Compiler dispatch sanity: counter is sequential, so SVA must
    # contain past_valid bootstrap and exactly one 'input logic clk' line.
    seq_clk_decls = sva.count("input logic clk")
    seq_has_past = "past_valid" in sva
    bug2 = seq_has_past and seq_clk_decls == 1
    checks.append(("Bug #2 (single clk decl + past_valid)", bug2,
                   f"clk_decls={seq_clk_decls} past_valid={seq_has_past}"))

    all_pass = True
    for name, passed, detail in checks:
        marker = "PASS" if passed else "FAIL"
        print(f"  [{marker}] {name:40s} -- {detail}")
        all_pass = all_pass and passed

    print()
    if all_pass:
        print("All Stage A invariants hold.")
    else:
        print("Some invariants FAILED. See above.")
    return 0 if all_pass else 2


if __name__ == "__main__":
    sys.exit(main())
