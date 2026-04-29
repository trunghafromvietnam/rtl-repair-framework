##
# Updated: Feb 24, 2026
# Author: Ha Tran
# Description: This file is the control center of the agent system.
##

from langgraph.graph import StateGraph, START, END
from agents.state import AgentState
from agents.nodes import coder_node, verifier_node, architect_node, contract_node, reviewer_node, cex_analyzer_node
from tools.reporter import SignOffReporter

ABLATION_MODE = False

def route_after_architect(state: AgentState):
    if state.get("verification_status") == "FATAL_ERROR":
        return END 
    return "verifier"

def should_continue(state: AgentState):
    if state['iteration_count'] >= 10:
        print("[Orchestrator] Max iterations reached.")
        return END

    status = state.get('verification_status')
    
    if status == "FATAL_ERROR":
        print("🔴 SYSTEM SAFETY INVARIANT VIOLATED. TERMINATING.")
        return END

    if status == "PASS":
        return END
    
    if status == "FAIL_VACUOUS":
        if state['iteration_count'] >= 3:
            return "to_architect" 
        return "to_coder"

    if status == "ERROR":
        return "to_coder"

    return "to_analyzer"

def should_review_pass(state: AgentState):
    review_attempts = len([h for h in state.get('fix_history', [])]) 
    
    if "REVIEW_STAGE_SUCCESS" in state.get('verification_status', ''):
        print("[Orchestrator] Review Passed!")
        return "verifier"   
    
    if review_attempts >= 3:
        print("[Orchestrator] Naming mismatch persists. Forcing Verifier to trigger SBY errors...")
        return "verifier"

    return "coder"

# Build the workflow graph
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("contract", contract_node)
workflow.add_node("architect", architect_node)
workflow.add_node("coder", coder_node)
workflow.add_node("reviewer", reviewer_node)
workflow.add_node("verifier", verifier_node)
workflow.add_node("cex_analyzer", cex_analyzer_node)

# Define edges
workflow.add_edge(START, "contract")
workflow.add_edge("contract", "architect")

workflow.add_conditional_edges(
    "architect",
    route_after_architect,
    {
        "verifier": "verifier",
        END: END
    }
)

workflow.add_conditional_edges(
    "verifier",
    should_continue,
    {
        "to_coder": "coder",        
        "to_analyzer": "cex_analyzer", 
        "to_architect": "architect",  
        END: END
    }
)

workflow.add_edge("cex_analyzer", "coder")
workflow.add_edge("coder", "reviewer")

workflow.add_conditional_edges(
    "reviewer",
    should_review_pass,
    {
        "verifier": "verifier",
        "coder": "coder"        
    }
)

app = workflow.compile()

def run_repair_system(project_name: str, spec: str, buggy_code: str):
    initial_state = {
        "project_name": project_name,
        "raw_spec": spec,
        "buggy_verilog": buggy_code,
        "current_verilog": buggy_code,
        "formal_properties": "", 
        "fix_history": [],
        "iteration_count": 0,
        "design_contract": "",      
        "error_log": "None yet",    
        "cex_data": None,     
        "verification_status": ""    
    }
    print(f"[System] Invoking LangGraph for {project_name}...")
    result = app.invoke(initial_state)

    if result['verification_status'] == "PASS":
        reporter = SignOffReporter()
        reporter.generate(result)

    return result

if __name__ == "__main__":
    fifo_spec = """
    Design a Synchronous FIFO, Depth 16. 
    Implement Asynchronous Active-Low Reset. 
    Add a status flag almost_full which triggers when 14 slots are filled.
    """
    with open("benchmarks/fifo.v", "r") as f:
        fifo_code = f.read()

    print(f"[Main] Starting Repair for: FIFO")
    result = run_repair_system("fifo", fifo_spec, fifo_code)
    print("\n[Main] FINAL REPAIRED RTL:\n", result["current_verilog"])
