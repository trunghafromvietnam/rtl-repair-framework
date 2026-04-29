##
# Date: Feb 24, 2026
# Author: Ha Tran
# Description: This file includes all nodes of the agent loop.
##

from dotenv import load_dotenv
from agents.state import AgentState
from agents.prompts import ARCHITECT_SYSTEM_PROMPT
from agents.property_ir import *
from agents.property_compiler import compile_property, render_properties_module
from tools.verifier import VerifierBridge
from tools.cex_translator import CEXTranslator
from langchain_core.messages import HumanMessage, SystemMessage
# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
import re
import json
import os
import inspect

load_dotenv()

# Helpers
def extract_code_block(text: str, lang: str | None = None) -> str:
    """
    Extract code from markdown fences. If lang provided, prefer that fence.
    Returns cleaned code-only string.
    """
    if not text:
        return ""

    if lang:
        pattern = rf"```{lang}\s*(.*?)```"
        m = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()

    m = re.search(r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)```", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()

    lines = text.split('\n')
    clean_lines = [line for line in lines if not line.strip().startswith('```')]
    
    return "\n".join(clean_lines).strip()

def must_be_code_only(code: str, *, kind: str) -> str:
    """
    Hard gate: reject obvious prose contamination.
    kind: 'verilog' or 'sva' or 'json'
    """
    if not code:
        return ""

    code = re.sub(r"```[a-zA-Z0-9_+-]*", "", code)
    code = code.replace("```", "").strip()

    return code

llm = ChatOpenAI(model="gpt-4o", temperature=0)
# llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro")

def contract_node(state: AgentState):
    print("\n[Node: Contract] Canonical Interface Inference from Spec...")
    
    spec = state['raw_spec']
    rtl_sample = state['buggy_verilog'][:500] 
    
    prompt = f"""
    You are a Formal Interface Extraction Engine.

    Your job is to extract a STRICT structural contract from SPEC.

    DO NOT classify module as fifo/alu/etc.
    ONLY determine:
    - combinational vs sequential
    - ports and widths
    - clock/reset

    SPEC:
    {spec}

    RTL SAMPLE:
    {rtl_sample}

    OUTPUT JSON:

    {{
    "module_type": "combinational | sequential",
    "ports": {{
        "signal_name": "[width]"
    }},
    "clk": "clk_name or null",
    "rst": {{
        "name": "rst_name or null",
        "polarity": "active_high | active_low | none"
    }}
    }}

    STRICT RULES:
    - DO NOT infer protocol names (no fifo, alu, axi...)
    - WIDTH must be literal (e.g., [7:0])
    - If no clock => combinational
    """
    response = llm.invoke([SystemMessage(content=prompt)])
    try:
        import json
        clean_json = extract_code_block(response.content, lang="json")
        contract_dict = json.loads(clean_json)
        if "error" in contract_dict: raise ValueError("Contract inference failed")
    except:
        return {"verification_status": "ERROR", "error_log": "Contract Extraction Failure"}

    return {"design_contract": contract_dict}

def architect_node(state: AgentState):
    print("[Node: Architect] Synthesizing SPEC-DRIVEN Formal Property IR...")
    contract = state["design_contract"]
    
    TYPE_MAP = {
        "MutualExclusion": MutualExclusion,
        "Equality": Equality,
        "ResetInvariant": ResetInvariant,
        "HandshakeProperty": HandshakeProperty,
        "HoldProperty": HoldProperty,
        "Implication": Implication,
        "TransitionProperty": TransitionProperty
    }
    
    prompt = f"""
    You are a Principal Formal Engineer. Generate a JSON list of Property IR objects.
    STRICT RULE: Every property MUST be derived from the SPEC.
    
    SPEC: {state['raw_spec']}
    CONTRACT: {json.dumps(contract)}
    
    OUTPUT FORMAT:
    [
      {{
        "type": "Equality", 
        "name": "alu_op_check", 
        "reasoning": "Spec states result = a + b when op=00",
        "left": "result", 
        "right": "a + b"
      }}
    ]
    """
    
    response = llm.invoke([SystemMessage(content=prompt)])
    try:
        ir_list = json.loads(extract_code_block(response.content, "json"))
        
        if not ir_list:
            raise ValueError("LLM returned an empty property list.")

        timing = Timing(
            kind=contract["module_type"], 
            clk=contract.get("clk", "clk"), 
            rst=contract["rst"].get("name", "rst")
        )
        
        properties = []
        for item in ir_list:
            cls = TYPE_MAP.get(item['type'])
            if cls:
                sig = inspect.signature(cls)
                valid_params = sig.parameters.keys()
                
                filtered_args = {k: v for k, v in item.items() if k in valid_params}
                
                filtered_args['name'] = item.get('name', 'prop')
                filtered_args['type'] = item.get('type')
                filtered_args['timing'] = timing
                
                properties.append(cls(**filtered_args))
        
        if not properties:
            return {"verification_status": "FATAL_ERROR", "error_log": "Invariant Violated: No valid IR objects mapped."}
        
        sva_module = render_properties_module(state["project_name"], contract, properties)
        
        print(f"[Architect] Successfully generated {len(properties)} properties.")
        return {
            "formal_properties": sva_module,
            "verification_status": "IR_READY"
        }
        
    except Exception as e:
        print(f"[Architect FATAL] {e}")
        return {"verification_status": "FATAL_ERROR", "error_log": f"Architect Failure: {e}"}

def verifier_node(state: AgentState):
    project = state["project_name"]

    depth_value = 10 if project in ["fifo", "uart_tx"] else 40

    for mode in ["prove", "cover"]:
        config = f"""
[options]
mode {mode}
depth {depth_value}
[engines]
smtbmc z3
[script]
read_verilog -sv {project}.v
read_verilog -sv {project}_props.sv
hierarchy -check -top {project}
prep -top {project}
[files]
{project}.v
{project}_props.sv
"""
        with open(f"benchmarks/{project}_{mode}.sby", "w") as f:
            f.write(config)

    with open(f"benchmarks/{project}.v", "w") as f:
        f.write(state["current_verilog"].strip() + "\n")
    with open(f"benchmarks/{project}_props.sv", "w") as f:
        f.write(state["formal_properties"].strip() + "\n")

    bridge = VerifierBridge()
    result = bridge.run_full_verification(project)
    
    vcd_path = f"benchmarks/{project}_prove/engine_0/trace.vcd"
    
    update = {
        "verification_status": result["status"],
        "error_log": result.get("log", ""),
        "iteration_count": 1 
    }

    status = result["status"]
    log = result.get("log", "")

    if status == "PASS":
        print(f"[Verifier] PASS")

    elif status == "VACUOUS_PASS":
        print("[Verifier] VACUOUS PASS DETECTED")
        update["verification_status"] = "FAIL_VACUOUS"

    elif status == "FAIL":
        log_lower = log.lower()

        if any(x in log_lower for x in ["syntax error", "parser error"]):
            update["verification_status"] = "ERROR"
        else:
            translator = CEXTranslator(vcd_path)
            update["cex_data"] = translator.translate()

    elif status == "ERROR":
        update["verification_status"] = "ERROR"

    return update

def cex_analyzer_node(state: AgentState):
    """
    Principal RTL Debugger: Performs Root Cause Analysis (RCA) on CEX traces.
    """
    print("[Node: CEX Analyzer] Analyzing failure trace and isolating root cause...")

    cex = state.get("cex_data")
    if cex is None or not isinstance(cex, dict) or "timeline" not in cex:
        return {"cex_analysis": "No Counter-Example (CEX) trace available. This usually happens during a Vacuous Pass or a Syntax Error."}

    compressed = {
        "events": cex["timeline"][:10],
        "signals": list(cex["signals"].keys())
    }
    
    llm_reasoner = ChatOpenAI(model="gpt-4o", temperature=0)

    system_prompt = """
    You are a Senior Silicon Debug Engineer at NVIDIA. Your task is to perform Root Cause Analysis (RCA) 
    on a failed Formal Verification trace (Counter-Example).

    ### INPUTS PROVIDED:
    1. DESIGN SPECIFICATION: The golden requirements.
    2. DESIGN CONTRACT: The signal naming conventions.
    3. FAILED ASSERTIONS: The formal properties that were violated.
    4. CEX TIMELINE: A step-by-step cycle-accurate trace of all signals leading to failure.

    ### YOUR TASKS:
    1. Identify the EXACT TIMESTAMP (Time) where the design state diverged from the expected behavior.
    2. Pinpoint the offending signals and their incorrect values.
    3. Explain the LOGIC GAP: Why did the current RTL fail the assertion? 
       (e.g., "The write pointer incremented despite the 'full' flag being high at T=20").
    4. Provide a STRATEGIC HINT for the Coder node (e.g., "Check the priority logic in the always block" or "Update reset condition for the count register").

    ### OUTPUT RULES:
    - Use professional engineering terminology (Clock edge, Setup/Hold, FSM State, Non-blocking assignment).
    - Be concise. No prose. Only technical analysis.
    - Format as:
      - ASSERTION_VIOLATED: [Name]
      - FAILURE_TIMESTAMP: [T]
      - ROOT_CAUSE_ANALYSIS: [Description]
      - REPAIR_STRATEGY: [Instructions for Coder]
    """

    user_input = f"""
    ### SPECIFICATION:
    {state['raw_spec']}

    ### DESIGN CONTRACT:
    {state['design_contract']}

    ### FAILED ASSERTIONS:
    {state['formal_properties']}

    ### CEX TIMELINE REPORT:
    {compressed}

    Please provide the RCA report.
    """

    response = llm_reasoner.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input)
    ])

    return {
        "cex_analysis": response.content.strip()
    }

def coder_node(state: AgentState):
    """
    AI programmer: Responsible for analyzing errors from CEX and fix Verilog. 
    """

    print(f"\n[Node: Coder] Fixing RTL (Iteration {state['iteration_count'] + 1})...")

    # Take data from SBY brain
    library_path = "agents/silicon_brain.json"
    brain_context = ""
    if os.path.exists(library_path):
        with open(library_path, "r") as f:
            past_experiences = json.load(f)
            brain_context = json.dumps(past_experiences[-2:], indent=2)

    system_prompt = f"""
    You are a Senior RTL Repair Engineer. 
    MISSION: Patch ONLY the logic that violates the formal assertion.
    STRICT RULES:
    1. DO NOT rewrite the entire module.
    2. ONLY modify the specific 'always' block or 'assign' statement identified by the CEX Analyzer.
    3. Maintain all internal signal names and structural hierarchy.
    4. Output the FULL module but with MINIMAL changes (Delta-fix).
    5. You may change AT MOST 3 lines of code.
    6. DO NOT introduce new always blocks unless absolutely necessary.
    7. DO NOT remove existing logic unless it is directly responsible for violation.
    
    CONTRACT TO FOLLOW: {state.get('design_contract', '')}

    ### SILICON BRAIN KNOWLEDGE (Past successful fixes):
    {brain_context}

    OUTPUT RULE: Return ONLY synthesizable Verilog code. No markdown, no explanations, no bullet points.
    """

    user_input = f"""
    ### TARGET RTL TO FIX (Naming is already verified, DO NOT change signal names):
    {state['current_verilog']} 

    ### VERIFIER FEEDBACK (The logic bug you must fix):
    {state.get('error_log', 'None')}

    ### EXPERT DEBUG ANALYSIS (Follow this!):
    {state.get('cex_analysis', 'No analysis available.')}

    Task: Fix the logic inside the 'always' block. 
    STRICT RULE: Keep all port names and wire names EXACTLY as they are in the CURRENT WORKING CODE.
    """

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input)
    ]
    
    response = llm.invoke(messages)

    raw = response.content
    new_verilog = extract_code_block(raw, lang="verilog")
    if not new_verilog:
        new_verilog = extract_code_block(raw)
    new_verilog = must_be_code_only(new_verilog, kind="verilog")

    # Sanity check "module" exists
    if "module" not in new_verilog:
        raise ValueError("[Sanitizer] Verilog output missing 'module' keyword")
    
    print("RAW LLM OUTPUT:\n", response.content)

    return {
        "current_verilog": new_verilog,
        "fix_history": [new_verilog],
        "iteration_count": state.get("iteration_count", 0)
    }

def reviewer_node(state: AgentState):
    print("[Node: Reviewer] Semantic Alignment Audit...")
    
    prompt = f"""
    Review the fixed RTL against the DESIGN CONTRACT.
    RTL: {state['current_verilog']}
    CONTRACT: {state['design_contract']}
    
    Check for:
    1. Reset polarity mismatch.
    2. Signal bit-width inconsistency.
    3. Missing functional ports.
    
    If any mismatch found, start with 'REVIEW_FAILED' and list the reasons.
    Otherwise, return 'REVIEW_STAGE_SUCCESS'.
    """
    response = llm.invoke([SystemMessage(content=prompt)])
    
    if "REVIEW_FAILED" in response.content:
        return {"verification_status": "REVIEW_STAGE_FAIL", "error_log": response.content}
    
    return {"verification_status": "REVIEW_STAGE_SUCCESS"}