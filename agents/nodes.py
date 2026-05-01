##
# Date: Apr 27, 2026
# Author: Ha Tran
# Description: Agent nodes for the RTL repair LangGraph.
#
# STABILIZATION PATCH NOTES:
# - iteration_count is owned exclusively by verifier_node, written as an
#   absolute value (state value + 1), to match overwrite-semantics in
#   AgentState. Other nodes never write this key.
# - review_attempts is owned exclusively by reviewer_node.
# - architect_node defensively filters LLM-supplied kwargs against each IR
#   class signature, drops the synthetic 'reasoning' field, and never
#   constructs an IR object with garbage parameters.
# - architect_node refuses to emit an empty properties module: returns
#   FATAL_ERROR so route_after_architect terminates the run.
# - verifier_node validates the rendered SVA before invoking SBY, and
#   uses a project-specific VCD path that mirrors the SBY workdir.
# - coder_node no longer touches iteration_count or fix_history accounting
#   beyond appending its candidate.
##

import json
import inspect
import os
import re

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agents.state import AgentState
from agents.property_ir import (
    Timing,
    MutualExclusion,
    Equality,
    ResetInvariant,
    HandshakeProperty,
    HoldProperty,
    Implication,
    TransitionProperty,
)
from agents.property_compiler import render_properties_module
from tools.verifier import VerifierBridge
from tools.cex_translator import CEXTranslator


load_dotenv()

# Single shared LLM instance. temperature=0 keeps generation deterministic
# enough for ablation studies; bump it for exploration if needed.
llm = ChatOpenAI(model="gpt-4o", temperature=0)


# ---------- Text helpers ----------

def extract_code_block(text: str, lang: str | None = None) -> str:
    """
    Extract code from markdown fences. If `lang` is given, prefer that fence.
    Falls back to any fenced block, then to a fence-stripped raw string.
    """
    if not text:
        return ""

    if lang:
        m = re.search(
            rf"```{lang}\s*(.*?)```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

    m = re.search(r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)```", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()

    return "\n".join(
        line for line in text.split("\n") if not line.strip().startswith("```")
    ).strip()


def strip_fences(code: str) -> str:
    """Defensive: remove any straggling fence markers from the LLM output."""
    if not code:
        return ""
    code = re.sub(r"```[a-zA-Z0-9_+-]*", "", code)
    return code.replace("```", "").strip()


# ---------- Contract node ----------

def contract_node(state: AgentState):
    """
    Extract a strict structural contract (interface) from the spec + RTL.
    Does NOT classify the design (no 'this is a fifo'). Just shape.
    """
    print("\n[Node: Contract] Extracting structural interface...")

    spec = state["raw_spec"]
    rtl_sample = state["buggy_verilog"][:500]

    prompt = f"""
You are a Formal Interface Extraction Engine.
Extract a STRICT structural contract from the spec and RTL sample.

DO NOT classify the module (no fifo / alu / axi labels).
ONLY determine: combinational vs sequential, ports + literal widths,
clock name (or null), reset name + polarity (or null/none).

SPEC:
{spec}

RTL SAMPLE:
{rtl_sample}

OUTPUT JSON:
{{
  "module_type": "combinational" | "sequential",
  "ports": {{ "signal_name": "[width]" }},
  "clk": "clk_name or null",
  "rst": {{
    "name": "rst_name or null",
    "polarity": "active_high" | "active_low" | "none"
  }}
}}

RULES:
- Widths must be literal (e.g., [7:0]); never use parameters.
- If there is no clock, the module is combinational.
- Output ONLY valid JSON, nothing else.
"""

    response = llm.invoke([SystemMessage(content=prompt)])
    try:
        raw = extract_code_block(response.content, lang="json")
        contract = json.loads(raw)
        if "module_type" not in contract or "ports" not in contract:
            raise ValueError("Contract missing required fields.")
    except Exception as e:
        return {
            "verification_status": "FATAL_ERROR",
            "error_log": f"Contract extraction failed: {e}",
        }

    return {"design_contract": contract}


# ---------- Architect node ----------

# Map of IR type names the LLM is allowed to use to their classes.
_IR_TYPE_MAP = {
    "MutualExclusion": MutualExclusion,
    "Equality": Equality,
    "ResetInvariant": ResetInvariant,
    "HandshakeProperty": HandshakeProperty,
    "HoldProperty": HoldProperty,
    "Implication": Implication,
    "TransitionProperty": TransitionProperty,
}


def _build_ir_object(item: dict, timing: Timing):
    """
    Construct one IR object from an LLM-emitted dict, defensively filtering
    keys against the target dataclass signature. Returns None if the type
    is unknown or required fields are missing.
    """
    cls = _IR_TYPE_MAP.get(item.get("type"))
    if cls is None:
        return None

    # Allowed parameter names for this dataclass.
    allowed = set(inspect.signature(cls).parameters.keys())

    kwargs = {k: v for k, v in item.items() if k in allowed}
    kwargs["name"] = item.get("name", "prop")
    kwargs["type"] = item.get("type")
    kwargs["timing"] = timing

    try:
        return cls(**kwargs)
    except TypeError as e:
        print(f"[Architect] Skipping malformed IR item {item.get('name')}: {e}")
        return None


def architect_node(state: AgentState):
    """
    Generate spec-driven Property IR objects, then compile them into a
    full SVA properties module.
    """
    print("[Node: Architect] Synthesizing Property IR from spec...")
    contract = state["design_contract"]

    prompt = f"""
You are a Principal Formal Verification Engineer.
Generate a JSON list of Property IR objects derived strictly from the SPEC.

SPEC:
{state['raw_spec']}

CONTRACT:
{json.dumps(contract, indent=2)}

ALLOWED IR TYPES AND THEIR FIELDS:
- MutualExclusion: signals (list of >=2 strings)
- Equality:        left, right
- Implication:     antecedent, consequent
- ResetInvariant:  signal, expected_value
- HoldProperty:    signal
- TransitionProperty: condition, next_state
- HandshakeProperty:  valid, ready

RULES:
- Every property MUST trace to a specific spec requirement.
- Use ONLY signal names declared in CONTRACT.ports (plus clk/rst).
- Each item must have: type, name, and the fields listed for that type.
- Do NOT invent new field names. Do NOT add 'condition' to Equality, etc.
- Output ONLY a JSON array. No prose, no markdown explanation.
"""

    response = llm.invoke([SystemMessage(content=prompt)])

    try:
        raw = extract_code_block(response.content, lang="json")
        ir_list = json.loads(raw)
        if not isinstance(ir_list, list) or not ir_list:
            raise ValueError("Architect returned an empty or non-list payload.")

        rst_name = (contract.get("rst") or {}).get("name") or "rst"
        timing = Timing(
            kind=contract.get("module_type", "sequential"),
            clk=contract.get("clk") or "clk",
            rst=rst_name,
        )

        properties = []
        for item in ir_list:
            obj = _build_ir_object(item, timing)
            if obj is not None:
                properties.append(obj)

        if not properties:
            return {
                "verification_status": "FATAL_ERROR",
                "error_log": "Architect: no valid IR objects after filtering.",
            }

        sva_module = render_properties_module(
            state["project_name"], contract, properties
        )

        print(f"[Architect] Compiled {len(properties)} properties.")
        return {
            "formal_properties": sva_module,
            "verification_status": "IR_READY",
        }

    except Exception as e:
        print(f"[Architect] FATAL: {e}")
        return {
            "verification_status": "FATAL_ERROR",
            "error_log": f"Architect failure: {e}",
        }


# ---------- Verifier node ----------

def _vcd_path(project: str) -> str:
    """SBY-derived VCD path for a successful counter-example trace."""
    return f"benchmarks/{project}_prove/engine_0/trace.vcd"


def verifier_node(state: AgentState):
    """
    Write the SBY config + RTL + properties module, run prove and cover,
    classify the outcome, and (on FAIL) translate the VCD into structured
    CEX data for the analyzer.
    """
    project = state["project_name"]
    sva = state.get("formal_properties", "")
    rtl = state.get("current_verilog", "")

    # Safety invariants. These should never trigger if the graph is wired
    # correctly (route_after_architect already enforces non-empty SVA),
    # but defense in depth is cheap.
    if not sva.strip() or "assert" not in sva:
        return {
            "verification_status": "FATAL_ERROR",
            "error_log": "Verifier called with empty/assertion-free SVA.",
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
    if "module" not in rtl:
        return {
            "verification_status": "FATAL_ERROR",
            "error_log": "Verifier called with non-Verilog current_verilog.",
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    os.makedirs("benchmarks", exist_ok=True)

    # Modest depths. FIFO/UART have larger state spaces, so we keep them
    # shallower; everything else gets a deeper bound.
    depth_value = 10 if project in {"fifo", "uart_tx"} else 40

    for mode in ("prove", "cover"):
        config = f"""[options]
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
        f.write(rtl.strip() + "\n")
    with open(f"benchmarks/{project}_props.sv", "w") as f:
        f.write(sva.strip() + "\n")

    bridge = VerifierBridge()
    result = bridge.run_full_verification(project)

    new_iter = state.get("iteration_count", 0) + 1
    update: dict = {
        "iteration_count": new_iter,
        "error_log": result.get("log", ""),
    }

    status = result.get("status", "ERROR")
    log_lower = (result.get("log") or "").lower()

    if status == "PASS":
        print(f"[Verifier] PASS (iter {new_iter})")
        update["verification_status"] = "PASS"

    elif status == "VACUOUS_PASS":
        print(f"[Verifier] VACUOUS PASS (iter {new_iter})")
        update["verification_status"] = "FAIL_VACUOUS"

    elif status == "FAIL":
        # Classify: is this a real CEX, or a tool/syntax error?
        if any(tok in log_lower for tok in ("syntax error", "parser error",
                                            "elaboration", "undefined symbol")):
            update["verification_status"] = "ERROR"
        else:
            try:
                update["cex_data"] = CEXTranslator(_vcd_path(project)).translate()
            except Exception as e:
                print(f"[Verifier] CEX translation failed: {e}")
                update["cex_data"] = None
            update["verification_status"] = "FAIL"

    else:
        update["verification_status"] = "ERROR"

    return update


# ---------- CEX analyzer node ----------

def cex_analyzer_node(state: AgentState):
    """
    Translate a CEX trace into a structured Root Cause Analysis (RCA).
    Returns a textual analysis the coder will consume.
    """
    print("[Node: CEX Analyzer] Running root-cause analysis...")

    cex = state.get("cex_data")
    if not isinstance(cex, dict) or "timeline" not in cex:
        return {
            "cex_analysis": (
                "No CEX trace available. This typically means a vacuous "
                "pass or a tool error rather than a real assertion failure."
            )
        }

    compressed = {
        "events": cex["timeline"][:10],
        "signals": list(cex.get("signals", {}).keys()),
    }

    system_prompt = """
You are a senior silicon debug engineer. Perform Root Cause Analysis on
a failed formal counter-example. Respond with the EXACT format below:

- ASSERTION_VIOLATED: <name>
- FAILURE_TIMESTAMP: <T>
- ROOT_CAUSE_ANALYSIS: <one paragraph>
- REPAIR_STRATEGY: <one paragraph of concrete instructions for the coder>

Use precise hardware terminology. No prose, no greetings.
"""

    user_input = f"""
SPECIFICATION:
{state['raw_spec']}

CONTRACT:
{state['design_contract']}

FAILED ASSERTIONS (full SVA module):
{state['formal_properties']}

CEX TIMELINE (compressed):
{compressed}
"""

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input),
    ])
    return {"cex_analysis": response.content.strip()}


# ---------- Coder node ----------

def coder_node(state: AgentState):
    """
    Apply a minimal patch to current_verilog based on the RCA.
    Appends one new candidate to fix_history. Does NOT touch
    iteration_count or review_attempts.
    """
    print(f"\n[Node: Coder] Patching RTL "
          f"(verifier iter {state.get('iteration_count', 0)})...")

    library_path = "agents/silicon_brain.json"
    brain_context = ""
    if os.path.exists(library_path):
        try:
            with open(library_path, "r") as f:
                past = json.load(f)
                brain_context = json.dumps(past[-2:], indent=2)
        except Exception:
            brain_context = ""

    system_prompt = f"""
You are a Senior RTL Repair Engineer.
Apply the SMALLEST POSSIBLE patch that fixes the formal counter-example.

HARD RULES:
1. Output the FULL module, but change AT MOST 3 lines.
2. Keep every port name and signal name EXACTLY as in CURRENT_VERILOG.
3. Do NOT introduce new always blocks unless strictly necessary.
4. Do NOT remove logic that is not directly responsible for the violation.
5. Output ONLY synthesizable Verilog. No markdown, no commentary.

CONTRACT (do not violate):
{state.get('design_contract', '')}

PRIOR SUCCESSFUL FIXES (for inspiration only):
{brain_context}
"""

    user_input = f"""
CURRENT_VERILOG:
{state['current_verilog']}

VERIFIER ERROR LOG:
{state.get('error_log', 'None')}

RCA FROM CEX ANALYZER:
{state.get('cex_analysis', 'No analysis available.')}
"""

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input),
    ])

    new_verilog = extract_code_block(response.content, lang="verilog")
    if not new_verilog:
        new_verilog = extract_code_block(response.content)
    new_verilog = strip_fences(new_verilog)

    if "module" not in new_verilog:
        # Don't crash the run; surface as a tool error so the verifier
        # path can absorb it and re-route.
        print("[Coder] WARNING: candidate has no 'module' keyword.")
        new_verilog = state["current_verilog"]

    return {
        "current_verilog": new_verilog,
        "fix_history": [new_verilog],
    }


# ---------- Reviewer node ----------

def reviewer_node(state: AgentState):
    """
    Lightweight semantic audit before re-invoking the verifier. Owns
    review_attempts.
    """
    print("[Node: Reviewer] Semantic alignment audit...")

    prompt = f"""
Review the candidate RTL against the contract. Check:
1. Reset polarity matches the contract.
2. All declared port widths match the contract.
3. No declared port is missing from the module header.

If any mismatch is found, start your response with 'REVIEW_FAILED' and
list the issues. Otherwise reply EXACTLY 'REVIEW_STAGE_SUCCESS'.

CANDIDATE_RTL:
{state['current_verilog']}

CONTRACT:
{state['design_contract']}
"""
    response = llm.invoke([SystemMessage(content=prompt)])
    new_attempts = state.get("review_attempts", 0) + 1

    if "REVIEW_FAILED" in response.content:
        return {
            "verification_status": "REVIEW_STAGE_FAIL",
            "error_log": response.content,
            "review_attempts": new_attempts,
        }

    return {
        "verification_status": "REVIEW_STAGE_SUCCESS",
        "review_attempts": new_attempts,
    }
