##
# Updated: Apr 27, 2026
# Author: Ha Tran
# Description: Control center of the agent system (LangGraph orchestrator).
#
# STABILIZATION PATCH NOTES:
# - route_after_architect: hard fail-fast if formal_properties is missing,
#   empty, or contains no 'assert' keyword. This eliminates the silent
#   "empty SVA file -> SBY exits 0 -> false PASS" trap.
# - should_continue: cleaner status machine. Bounded retries on ERROR
#   (syntax / tool errors) so a stuck syntax bug does not burn the entire
#   iteration budget.
# - should_review_pass: uses the dedicated review_attempts counter from
#   AgentState instead of the (semantically wrong) len(fix_history).
# - Initial state explicitly sets every key the nodes will read, so
#   AgentState is always fully populated and TypedDict access is safe.
##

from langgraph.graph import StateGraph, START, END

from agents.state import AgentState
from agents.nodes import (
    coder_node,
    verifier_node,
    architect_node,
    contract_node,
    reviewer_node,
    cex_analyzer_node,
)
from tools.reporter import SignOffReporter


# Run-wide knobs
ABLATION_MODE = False
MAX_ITERATIONS = 10
MAX_REVIEW_ATTEMPTS = 3
MAX_ERROR_RETRIES = 3


# ---------- Routing functions ----------

def _has_real_assertions(sva_text: str) -> bool:
    """Cheap structural check that the rendered SVA module is non-empty."""
    if not sva_text or not sva_text.strip():
        return False
    # Must contain at least one assert statement and a module body.
    return ("assert" in sva_text) and ("module" in sva_text)


def route_after_architect(state: AgentState):
    """
    Hard fail-fast guard. If we don't have a usable properties module,
    going to the verifier would produce a meaningless PASS or PASS-by-
    omission. Terminate the run instead.
    """
    if state.get("verification_status") == "FATAL_ERROR":
        print("[Router] Architect produced FATAL_ERROR. Terminating.")
        return END

    if not _has_real_assertions(state.get("current_verilog", "")):
        print(
            "[Router] SAFETY GUARD: formal_properties is empty or has no "
            "assertions. Refusing to call verifier (would yield a false PASS)."
        )
        return END

    return "verifier"


def should_continue(state: AgentState):
    """
    Decide what happens after the verifier runs.
    """
    iters = state.get("iteration_count", 0)
    if iters >= MAX_ITERATIONS:
        print(f"[Orchestrator] Max iterations ({MAX_ITERATIONS}) reached.")
        return END

    status = state.get("verification_status")

    if status == "FATAL_ERROR":
        print("[Orchestrator] FATAL_ERROR from verifier. Terminating.")
        return END

    if status == "PASS":
        print("[Orchestrator] Verification PASSED.")
        return END

    if status == "FAIL_VACUOUS":
        # Property set is logically OK but environment is too constrained,
        # OR the property is trivially true. After a few attempts, escalate
        # back to the architect to regenerate properties.
        if iters >= 3:
            print("[Orchestrator] Vacuity persists. Escalating to architect.")
            return "to_architect"
        return "to_coder"

    if status == "ERROR":
        # Bound the number of times we retry on tool/syntax errors so a
        # broken candidate cannot consume the whole budget.
        if iters >= MAX_ERROR_RETRIES:
            print("[Orchestrator] Too many tool ERRORs. Terminating.")
            return END
        return "to_coder"

    # FAIL with a real counter-example -> deepest analysis path
    return "to_analyzer"


def should_review_pass(state: AgentState):
    """
    After the reviewer runs: either approve and verify, or send back to
    the coder. Bounded by review_attempts so we cannot deadlock on a
    review that always fails.
    """
    attempts = state.get("review_attempts", 0)
    review_status = state.get("verification_status", "")

    if review_status == "REVIEW_STAGE_SUCCESS":
        print("[Orchestrator] Review passed.")
        return "verifier"

    if attempts >= MAX_REVIEW_ATTEMPTS:
        print(
            "[Orchestrator] Review attempts exhausted. "
            "Forcing verifier so SBY surfaces the real error."
        )
        return "verifier"

    return "coder"


# ---------- Graph wiring ----------

workflow = StateGraph(AgentState)

workflow.add_node("contract", contract_node)
workflow.add_node("architect", architect_node)
workflow.add_node("coder", coder_node)
workflow.add_node("reviewer", reviewer_node)
workflow.add_node("verifier", verifier_node)
workflow.add_node("cex_analyzer", cex_analyzer_node)

workflow.add_edge(START, "contract")
workflow.add_edge("contract", "architect")

workflow.add_conditional_edges(
    "architect",
    route_after_architect,
    {"verifier": "verifier", END: END},
)

workflow.add_conditional_edges(
    "verifier",
    should_continue,
    {
        "to_coder": "coder",
        "to_analyzer": "cex_analyzer",
        "to_architect": "architect",
        END: END,
    },
)

workflow.add_edge("cex_analyzer", "coder")
workflow.add_edge("coder", "reviewer")

workflow.add_conditional_edges(
    "reviewer",
    should_review_pass,
    {"verifier": "verifier", "coder": "coder"},
)

app = workflow.compile()


# ---------- Public entrypoint ----------

def run_repair_system(project_name: str, spec: str, buggy_code: str):
    """
    Invoke the repair graph for a single (spec, buggy RTL) pair.
    Returns the final AgentState dict.
    """
    initial_state: AgentState = {
        "project_name": project_name,
        "raw_spec": spec,
        "buggy_verilog": buggy_code,
        "current_verilog": buggy_code,
        "design_contract": {},
        "formal_properties": "",
        "verification_status": "",
        "error_log": "",
        "cex_data": None,
        "cex_analysis": "",
        "fix_history": [],
        "reviewer_feedback": "",
        "iteration_count": 0,
        "review_attempts": 0,
    }
    print(f"[System] Invoking LangGraph for {project_name}...")
    result = app.invoke(initial_state)

    if result.get("verification_status") == "PASS":
        SignOffReporter().generate(result)

    return result


if __name__ == "__main__":
    fifo_spec = """
    Synchronous FIFO with parameters WIDTH and DEPTH.
    Write when wr_en && !full. Read when rd_en && !empty.
    Flags: empty is high when wr_ptr == rd_ptr;
    full is high when count equals DEPTH.
    """
    with open("benchmarks/fifo.v", "r") as f:
        fifo_code = f.read()

    print("[Main] Starting repair for: FIFO")
    result = run_repair_system("fifo", fifo_spec, fifo_code)
    print("\n[Main] FINAL REPAIRED RTL:\n", result["current_verilog"])
