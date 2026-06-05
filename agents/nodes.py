##
# Hotfix B0.7 for nodes.py (verifier_node + architect_node)
#
# Companion to property_compiler_b07.py.
#
# Changes in B0.7:
#   1. architect_node now calls inject_properties_into_rtl() instead of
#      render_properties_module(). It produces a SINGLE merged RTL
#      string containing both the original DUT and the inlined
#      assertion block.
#
#   2. verifier_node writes only ONE file ({project}.v) and the SBY
#      script reads only that file. The separate {project}_props.sv
#      is no longer produced.
#
#   3. AgentState `formal_properties` now holds the inlined assertion
#      *body* (for reporting / sign-off) rather than a full module.
#      Sanity checks elsewhere that look for `assert` and `module`
#      still pass because the merged RTL contains both.
#
# All other nodes (contract, cex_analyzer, coder, reviewer) are
# unchanged from B0.4/B0.5/B0.6.
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
from agents.property_compiler import (
    inject_properties_into_rtl,
    _render_assertion_body,
)
from tools.verifier import VerifierBridge
from tools.cex_translator import CEXTranslator


load_dotenv()

llm = ChatOpenAI(model="gpt-4o", temperature=0)


# ---------- Text helpers (unchanged) ----------

def extract_code_block(text: str, lang: str | None = None) -> str:
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
    if not code:
        return ""
    code = re.sub(r"```[a-zA-Z0-9_+-]*", "", code)
    return code.replace("```", "").strip()


# ---------- Contract node (unchanged) ----------

def contract_node(state: AgentState):
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


# ---------- Architect node (B0.7: inject into RTL instead of bind) ----------

_IR_TYPE_MAP = {
    "MutualExclusion": MutualExclusion,
    "Equality": Equality,
    "ResetInvariant": ResetInvariant,
    "HandshakeProperty": HandshakeProperty,
    "HoldProperty": HoldProperty,
    "Implication": Implication,
    "TransitionProperty": TransitionProperty,
}

_FORBIDDEN_VALUE_TOKENS = set(_IR_TYPE_MAP.keys()) | {
    "PropertyIR", "Timing",
}


def _is_value_field_clean(item: dict) -> tuple[bool, str]:
    value_fields = ("left", "right", "antecedent", "consequent",
                    "signal", "expected_value", "valid", "ready",
                    "condition", "next_state")

    for field in value_fields:
        val = item.get(field)
        if not isinstance(val, str):
            continue
        for token in _FORBIDDEN_VALUE_TOKENS:
            if re.search(rf"\b{re.escape(token)}\b", val):
                return False, f"field '{field}' contains forbidden token '{token}'"

    sigs = item.get("signals")
    if isinstance(sigs, list):
        for s in sigs:
            if isinstance(s, str):
                for token in _FORBIDDEN_VALUE_TOKENS:
                    if re.search(rf"\b{re.escape(token)}\b", s):
                        return False, f"signals[] contains forbidden token '{token}'"

    return True, ""


def _build_ir_object(item: dict, timing: Timing):
    cls = _IR_TYPE_MAP.get(item.get("type"))
    if cls is None:
        return None

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


def _build_architect_prompt(spec: str, contract: dict) -> str:
    port_names = list((contract.get("ports") or {}).keys())
    clk = contract.get("clk")
    rst_name = (contract.get("rst") or {}).get("name")
    valid_signals = port_names + ([clk] if clk else []) + ([rst_name] if rst_name else [])
    valid_signals = [s for s in valid_signals if s]

    return f"""
You are a Principal Formal Verification Engineer. Generate a JSON list
of Property IR objects derived strictly from the SPEC.

SPEC:
{spec}

CONTRACT:
{json.dumps(contract, indent=2)}

VALID SIGNAL NAMES (use ONLY these in expressions):
{json.dumps(valid_signals)}

ALLOWED IR TYPES AND THEIR REQUIRED FIELDS:
- Equality:           left, right
- Implication:        antecedent, consequent
- ResetInvariant:     signal, expected_value
- MutualExclusion:    signals (>= 2 names)
- HoldProperty:       signal
- TransitionProperty: condition, next_state
- HandshakeProperty:  valid, ready

GOOD EXAMPLES (using signal names from a HYPOTHETICAL ALU):
[
  {{"type": "Equality", "name": "add_op",
    "left": "result", "right": "(op == 2'b00) ? a + b : result"}},
  {{"type": "Implication", "name": "zero_flag",
    "antecedent": "result == 8'b0", "consequent": "zero == 1'b1"}}
]

GOOD EXAMPLES (using signal names from a HYPOTHETICAL counter):
[
  {{"type": "ResetInvariant", "name": "count_reset",
    "signal": "count", "expected_value": "8'b00000000"}},
  {{"type": "TransitionProperty", "name": "count_inc",
    "condition": "en && !rst",
    "next_state": "count == $past(count) + 1"}}
]

FORBIDDEN VALUES (NEVER put these strings in any field except 'type'):
{json.dumps(sorted(_FORBIDDEN_VALUE_TOKENS))}

HARD RULES:
1. Every property MUST trace to a specific spec requirement.
2. Use ONLY signal names listed in VALID SIGNAL NAMES.
3. Each item must have: type, name, and the fields listed for that type.
4. Do NOT invent new fields.
5. Output ONLY a JSON array. No prose, no markdown explanation.
"""


def architect_node(state: AgentState):
    """
    B0.7: produce inlined assertion-augmented RTL instead of a separate
    properties module + bind. The result is a single Verilog string
    that the verifier writes to disk and SBY reads.
    """
    print("[Node: Architect] Synthesizing Property IR from spec...")
    contract = state["design_contract"]

    prompt = _build_architect_prompt(state["raw_spec"], contract)
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

        clean_items = []
        for item in ir_list:
            ok, reason = _is_value_field_clean(item)
            if not ok:
                print(f"[Architect] DROPPING property '{item.get('name')}': {reason}")
                continue
            clean_items.append(item)

        # B0.9: filter incompatible IR types based on module timing.
        # Combinational modules cannot use timing-dependent properties.
        module_type = contract.get("module_type", "sequential")
        if module_type == "combinational":
            SEQ_ONLY_TYPES = {
                "ResetInvariant",
                "TransitionProperty",
                "HoldProperty",
                "HandshakeProperty",
            }
            before = len(clean_items)
            clean_items = [
                item for item in clean_items
                if item.get("type") not in SEQ_ONLY_TYPES
            ]
            dropped = before - len(clean_items)
            if dropped > 0:
                print(f"[Architect] B0.9: dropped {dropped} sequential-only "
                      f"properties from combinational module.")

        if not clean_items:
            return {
                "verification_status": "FATAL_ERROR",
                "error_log": "Architect: all properties failed validation.",
            }

        properties = []
        for item in clean_items:
            obj = _build_ir_object(item, timing)
            if obj is not None:
                properties.append(obj)

        if not properties:
            return {
                "verification_status": "FATAL_ERROR",
                "error_log": "Architect: no valid IR objects after filtering.",
            }

        # B0.7: inject assertions directly into the DUT RTL.
        merged_rtl = inject_properties_into_rtl(
            state["current_verilog"], contract, properties
        )

        # Also keep the assertion body alone for reports / debugging.
        assertion_body = _render_assertion_body(contract, properties)

        print(f"[Architect] Compiled {len(properties)} properties "
              f"({len(ir_list) - len(properties)} dropped). "
              f"Assertions inlined into DUT.")

        return {
            "current_verilog": merged_rtl,         # DUT + assertions
            "formal_properties": assertion_body,    # assertion block (for reports)
            "verification_status": "IR_READY",
        }

    except Exception as e:
        print(f"[Architect] FATAL: {e}")
        return {
            "verification_status": "FATAL_ERROR",
            "error_log": f"Architect failure: {e}",
        }


# ---------- Verifier node (B0.7: single-file SBY config) ----------

def _vcd_path(project: str) -> str:
    return f"benchmarks/{project}_prove/engine_0/trace.vcd"


def verifier_node(state: AgentState):
    """
    B0.7: write only one file ({project}.v containing DUT + inlined
    assertions) and configure SBY to read just that file.
    """
    project = state["project_name"]
    rtl = state.get("current_verilog", "")
    sva = state.get("formal_properties", "")  # assertion body, for sanity check

    # Sanity invariants.
    if not rtl.strip() or "module" not in rtl:
        return {
            "verification_status": "FATAL_ERROR",
            "error_log": "Verifier called with empty / non-Verilog current_verilog.",
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
    if "assert" not in rtl:
        return {
            "verification_status": "FATAL_ERROR",
            "error_log": (
                "Verifier called with RTL that has no assert statement — "
                "Architect did not inject properties (would yield false PASS)."
            ),
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    os.makedirs("benchmarks", exist_ok=True)

    depth_value = 10 if project in {"fifo", "uart_tx"} else 40

    # B0.7: Single-file SBY config — only read {project}.v
    for mode in ("prove", "cover"):
        config = f"""[options]
mode {mode}
depth {depth_value}

[engines]
smtbmc z3

[script]
read_verilog -sv -formal {project}.v
hierarchy -check -top {project}
prep -top {project}

[files]
{project}.v
"""
        with open(f"benchmarks/{project}_{mode}.sby", "w") as f:
            f.write(config)

    # Write the merged RTL (DUT + inlined assertions).
    with open(f"benchmarks/{project}.v", "w") as f:
        f.write(rtl.strip() + "\n")

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


# ---------- CEX analyzer node (unchanged) ----------

def cex_analyzer_node(state: AgentState):
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

FAILED ASSERTIONS (assertion body only, inlined into DUT):
{state['formal_properties']}

CEX TIMELINE (compressed):
{compressed}
"""

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input),
    ])
    return {"cex_analysis": response.content.strip()}


# ---------- Coder node (B0.7: must NOT remove assertions) ----------

def coder_node(state: AgentState):
    """
    B0.7 important note: current_verilog now contains DUT + inlined
    assertions. The Coder must edit only the DUT logic and leave the
    assertion block intact. The prompt is updated to make that explicit.
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
1. Output the FULL module, but change AT MOST 3 lines of DUT logic.
2. Keep every port name and signal name EXACTLY as in CURRENT_VERILOG.
3. Do NOT remove or modify lines between
   "// === BEGIN B0.7 inlined formal properties ===" and
   "// === END B0.7 inlined formal properties ===".
   Those are the formal contract — touching them invalidates the proof.
4. Do NOT introduce new always blocks unless strictly necessary.
5. Output ONLY synthesizable Verilog. No markdown, no commentary.

CONTRACT (do not violate):
{state.get('design_contract', '')}

PRIOR SUCCESSFUL FIXES (for inspiration only):
{brain_context}
"""

    user_input = f"""
CURRENT_VERILOG (DUT + inlined assertions; DO NOT modify the inlined block):
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
        print("[Coder] WARNING: candidate has no 'module' keyword.")
        new_verilog = state["current_verilog"]

    # B0.7 safety: if the Coder accidentally stripped the inlined
    # assertion block, restore it from formal_properties.
    if "BEGIN B0.7 inlined formal properties" not in new_verilog:
        print("[Coder] WARNING: Coder removed inlined assertions; restoring.")
        # Re-inject by finding endmodule and inserting formal_properties block.
        body = state.get("formal_properties", "")
        if body:
            new_verilog = re.sub(
                r"(\n[ \t]*endmodule\b)",
                "\n" + body + r"\1",
                new_verilog,
                count=1,
            )

    return {
        "current_verilog": new_verilog,
        "fix_history": [new_verilog],
    }


# ---------- Reviewer node (unchanged) ----------

def reviewer_node(state: AgentState):
    print("[Node: Reviewer] Semantic alignment audit...")

    prompt = f"""
Review the candidate RTL against the contract. Check:
1. Reset polarity matches the contract.
2. All declared port widths match the contract.
3. No declared port is missing from the module header.

Note: the candidate RTL may contain an inlined formal-properties block
(between BEGIN/END B0.7 comments). That block is by design — do not
flag it as an error.

If any other mismatch is found, start your response with 'REVIEW_FAILED'
and list the issues. Otherwise reply EXACTLY 'REVIEW_STAGE_SUCCESS'.

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
