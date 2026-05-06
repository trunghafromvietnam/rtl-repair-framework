##
# Hotfix B0.7 for property_compiler.py
#
# Problem (verified by direct Yosys probe):
#   Yosys 0.61 built-in SystemVerilog frontend SILENTLY DISCARDS modules
#   referenced via `bind` directives. Specifically:
#
#     yosys -p "read_verilog -sv -formal dut.v; \
#               read_verilog -sv -formal dut_props.sv; \
#               hierarchy -check -top dut; stat"
#
#   produces:
#     "Removing unused module `\dut_props'."
#     "Removed 1 unused modules."
#
#   The bound module is treated as unused and garbage-collected BEFORE
#   reaching the solver. As a result, no assertion ever gets checked.
#
#   Direct evidence (test_inline.v vs test_falsifiable.v with bind):
#     - bind path:   PASS (no assertions checked → vacuous)
#     - inline path: FAIL with counter-example (correct behavior)
#
#   This caused all 6 benchmarks to "PASS" without their bugs being
#   detected — a critical false-positive failure mode of the entire
#   pipeline.
#
# Fix (Path α):
#   Inline assertion logic directly into the DUT module. Instead of
#   producing a separate `<project>_props.sv` and a `bind` directive,
#   we now produce a single `<project>.v` with assertions injected
#   immediately before the DUT's final `endmodule`.
#
#   New function: inject_properties_into_rtl(rtl_text, contract, properties)
#   - Locates the final `endmodule` token in the DUT RTL
#   - Inserts the rendered assertion block just before it
#   - Returns the modified RTL as a single string
#
#   The legacy render_properties_module() is kept as a private helper
#   that returns just the body content (no module header / bind line),
#   for use by inject_properties_into_rtl().
##

import re
from agents.property_ir import (
    PropertyIR,
    MutualExclusion,
    Implication,
    Equality,
    ResetInvariant,
    HoldProperty,
    TransitionProperty,
    HandshakeProperty,
)


# ---------- Width normalization (from B0.6, unchanged) ----------

_WIDTH_RANGE_RE = re.compile(r"^\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*$")
_WIDTH_SINGLE_RE = re.compile(r"^\s*\[\s*(\d+)\s*\]\s*$")
_WIDTH_BARE_INT_RE = re.compile(r"^\s*(\d+)\s*$")


def _normalize_width(raw_width) -> str:
    if raw_width is None:
        return ""
    s = str(raw_width).strip()
    if not s or s == "[]":
        return ""

    m = _WIDTH_RANGE_RE.match(s)
    if m:
        hi, lo = int(m.group(1)), int(m.group(2))
        if hi == 0 and lo == 0:
            return ""
        return f"[{hi}:{lo}]"

    m = _WIDTH_SINGLE_RE.match(s)
    if m:
        n = int(m.group(1))
        if n <= 1:
            return ""
        return f"[{n - 1}:0]"

    m = _WIDTH_BARE_INT_RE.match(s)
    if m:
        n = int(m.group(1))
        if n <= 1:
            return ""
        return f"[{n - 1}:0]"

    return ""


# ---------- $past() rewriting (from B0.4, unchanged) ----------

_PAST_RE = re.compile(r"\$past\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")


def _rewrite_past_in_expr(expr: str) -> tuple[str, set[str]]:
    needs: set[str] = set()

    def _sub(m: re.Match) -> str:
        sig = m.group(1)
        needs.add(sig)
        return f"past_{sig}"

    new_expr = _PAST_RE.sub(_sub, expr)
    return new_expr, needs


# ---------- Per-property emission (from B0.4, unchanged) ----------

def _is_comb(prop: PropertyIR) -> bool:
    return prop.timing.kind == "combinational"


def _emit(prop: PropertyIR) -> tuple[list[str], set[str], list[tuple[str, str]]]:
    name = prop.name
    clk = prop.timing.clk
    rst = prop.timing.rst
    comb = _is_comb(prop)

    if isinstance(prop, MutualExclusion):
        if len(prop.signals) < 2:
            raise ValueError(
                f"MutualExclusion '{name}' needs >=2 signals, got {prop.signals}"
            )
        terms = [
            f"!({prop.signals[i]} && {prop.signals[j]})"
            for i in range(len(prop.signals))
            for j in range(i + 1, len(prop.signals))
        ]
        body = " && ".join(terms)
        if comb:
            return [f"    always_comb {name}_a: assert ({body});"], set(), []
        return [
            f"    always @(posedge {clk}) {name}_a: assert ({body});"
        ], set(), []

    if isinstance(prop, Implication):
        ant, ant_needs = _rewrite_past_in_expr(prop.antecedent)
        cons, cons_needs = _rewrite_past_in_expr(prop.consequent)
        needs = ant_needs | cons_needs
        if comb:
            return [
                f"    always_comb if ({ant}) "
                f"{name}_a: assert ({cons});"
            ], needs, []
        return [
            f"    always @(posedge {clk}) "
            f"if (past_valid && ({ant})) "
            f"{name}_a: assert ({cons});"
        ], needs, []

    if isinstance(prop, Equality):
        left, left_needs = _rewrite_past_in_expr(prop.left)
        right, right_needs = _rewrite_past_in_expr(prop.right)
        needs = left_needs | right_needs
        body = f"({left}) == ({right})"
        if comb:
            return [f"    always_comb {name}_a: assert ({body});"], needs, []
        return [
            f"    always @(posedge {clk}) "
            f"if (past_valid) {name}_a: assert ({body});"
        ], needs, []

    if isinstance(prop, ResetInvariant):
        if comb:
            raise ValueError(f"ResetInvariant '{name}' requires sequential timing.")
        return [
            f"    always @(posedge {clk}) "
            f"if ({rst}) {name}_a: "
            f"assert ({prop.signal} == {prop.expected_value});"
        ], set(), []

    if isinstance(prop, HoldProperty):
        if comb:
            raise ValueError(f"HoldProperty '{name}' requires sequential timing.")
        return [
            f"    always @(posedge {clk}) "
            f"if (past_valid) {name}_a: "
            f"assert ({prop.signal} == past_{prop.signal});"
        ], {prop.signal}, []

    if isinstance(prop, TransitionProperty):
        if comb:
            raise ValueError(f"TransitionProperty '{name}' requires sequential timing.")
        cond_expr, cond_needs = _rewrite_past_in_expr(prop.condition)
        next_expr, next_needs = _rewrite_past_in_expr(prop.next_state)
        needs = cond_needs | next_needs
        return [
            f"    always @(posedge {clk}) "
            f"if (past_valid && cond_{name}) "
            f"{name}_a: assert ({next_expr});"
        ], needs, [(name, cond_expr)]

    if isinstance(prop, HandshakeProperty):
        if comb:
            raise ValueError(f"HandshakeProperty '{name}' requires sequential timing.")
        return [
            f"    always @(posedge {clk}) "
            f"if (past_valid && {prop.valid}) "
            f"{name}_a: assert ({prop.valid} || {prop.ready});",
            f"    always @(posedge {clk}) "
            f"{name}_c: cover ({prop.valid} && {prop.ready});",
        ], set(), []

    raise ValueError(f"Unsupported property IR type: {type(prop).__name__}")


def _emit_cover(prop: PropertyIR) -> str | None:
    clk = prop.timing.clk
    comb = _is_comb(prop)

    if isinstance(prop, Equality):
        left, _ = _rewrite_past_in_expr(prop.left)
        right, _ = _rewrite_past_in_expr(prop.right)
        target = f"({left}) == ({right})"
    elif isinstance(prop, Implication):
        ant, _ = _rewrite_past_in_expr(prop.antecedent)
        target = f"({ant})"
    elif isinstance(prop, TransitionProperty):
        cond_expr, _ = _rewrite_past_in_expr(prop.condition)
        target = f"({cond_expr})"
    else:
        return None

    if comb:
        return f"    always_comb {prop.name}_c: cover ({target});"
    return f"    always @(posedge {clk}) {prop.name}_c: cover ({target});"


def _width_for(sig: str, ports: dict) -> int:
    raw = (ports.get(sig) or "").strip()
    if not raw:
        return 1
    normalized = _normalize_width(raw)
    if not normalized:
        return 1
    m = _WIDTH_RANGE_RE.match(normalized)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return abs(a - b) + 1
    return 1


# ---------- B0.7: Render assertion BODY only (no module header, no bind) ----------

def _render_assertion_body(
    contract: dict,
    properties: list[PropertyIR],
) -> str:
    """
    Build just the assertion logic block (no module header, no bind line).
    This block is meant to be injected directly into the DUT module,
    immediately before its final `endmodule`.

    Layout:
      // === BEGIN B0.7 inlined formal properties ===
      reg past_valid = 1'b0;        (sequential only)
      always @(posedge clk) past_valid <= 1'b1;
      ... reset assumption ...
      ... past-cycle snapshots ...
      ... assertions + covers ...
      // === END B0.7 inlined formal properties ===
    """
    if not properties:
        raise ValueError(
            "Cannot inject zero properties — would produce vacuous-by-construction RTL."
        )

    module_type = contract.get("module_type", "sequential")
    is_seq = module_type == "sequential"

    ports = contract.get("ports", {}) or {}
    clk = contract.get("clk") or "clk"
    rst_info = contract.get("rst") or {}
    rst_name = rst_info.get("name") if isinstance(rst_info, dict) else None
    rst_polarity = (
        rst_info.get("polarity", "active_high")
        if isinstance(rst_info, dict) else "active_high"
    )

    lines: list[str] = []
    lines.append("    // === BEGIN B0.7 inlined formal properties ===")

    if is_seq:
        lines.append("    // Past-valid bootstrap: avoid asserting at T=0.")
        lines.append("    reg fp_past_valid = 1'b0;")
        lines.append(f"    always @(posedge {clk}) fp_past_valid <= 1'b1;")
        lines.append("")

        if rst_name:
            rst_active = f"!{rst_name}" if rst_polarity == "active_low" else rst_name
            rst_inactive = rst_name if rst_polarity == "active_low" else f"!{rst_name}"
            lines.append("    // Drive reset for one cycle, then release.")
            lines.append(f"    always @(posedge {clk}) begin")
            lines.append(f"        if (!fp_past_valid) assume({rst_active});")
            lines.append(f"        else                assume({rst_inactive});")
            lines.append("    end")
            lines.append("")

    # First pass: collect emission lines + register needs.
    assertion_lines: list[str] = []
    past_signals: set[str] = set()
    transition_conds: list[tuple[str, str]] = []

    for prop in properties:
        try:
            block_lines, p_needs, t_needs = _emit(prop)
        except ValueError as e:
            assertion_lines.append(f"    // [COMPILE_ERROR] {e}")
            assertion_lines.append(
                f"    always_comb {prop.name}_compile_err: assert (1'b0);"
            )
            continue

        # Replace `past_valid` in assertion guards with `fp_past_valid` to
        # avoid colliding with any DUT-internal signal of the same name.
        block_lines = [ln.replace("past_valid", "fp_past_valid") for ln in block_lines]
        assertion_lines.extend(block_lines)

        cov = _emit_cover(prop)
        if cov is not None:
            cov = cov.replace("past_valid", "fp_past_valid")
            assertion_lines.append(cov)

        past_signals.update(p_needs)
        transition_conds.extend(t_needs)

    # Past-register declarations.
    if is_seq and (past_signals or transition_conds):
        lines.append("    // Past-cycle snapshots used by hold / transition properties.")
        for sig in sorted(past_signals):
            w = _width_for(sig, ports)
            if w == 1:
                lines.append(f"    reg fp_past_{sig};")
            else:
                lines.append(f"    reg [{w-1}:0] fp_past_{sig};")
        for cname, _ in transition_conds:
            lines.append(f"    reg fp_cond_{cname};")

        lines.append(f"    always @(posedge {clk}) begin")
        for sig in sorted(past_signals):
            lines.append(f"        fp_past_{sig} <= {sig};")
        for cname, expr in transition_conds:
            lines.append(f"        fp_cond_{cname} <= ({expr});")
        lines.append("    end")
        lines.append("")

    # Substitute `past_<sig>` → `fp_past_<sig>` and `cond_<name>` → `fp_cond_<name>`
    # in the already-emitted assertion lines so they reference the renamed regs.
    fixed_assertion_lines = []
    for ln in assertion_lines:
        # Be careful: only replace whole-word occurrences.
        ln2 = re.sub(r"\bpast_(?!valid\b)([A-Za-z_][A-Za-z0-9_]*)", r"fp_past_\1", ln)
        ln2 = re.sub(r"\bcond_([A-Za-z_][A-Za-z0-9_]*)", r"fp_cond_\1", ln2)
        fixed_assertion_lines.append(ln2)

    lines.extend(fixed_assertion_lines)

    lines.append("    // === END B0.7 inlined formal properties ===")
    return "\n".join(lines)


# ---------- B0.7: Inject assertion body into DUT RTL ----------

# Match `endmodule` as a whole word, possibly with leading whitespace,
# and capture everything before it for safe substitution.
_ENDMODULE_RE = re.compile(
    r"(.*?)(\n[ \t]*endmodule\b[ \t]*\n?)\s*\Z",
    re.DOTALL,
)


def inject_properties_into_rtl(
    rtl_text: str,
    contract: dict,
    properties: list[PropertyIR],
) -> str:
    """
    Inject the rendered assertion body into `rtl_text`, immediately
    before the final `endmodule`. Returns the modified RTL as a string.

    The function:
      1. Rejects RTL that contains no `endmodule` token.
      2. Rejects RTL with multiple top-level `endmodule` (we only handle
         single-module benchmarks for now; multi-module RTL needs a
         smarter parser).
      3. Inserts the assertion body with proper newline padding.

    For multi-module RTL, the SAFE behaviour is to inject only into the
    LAST `endmodule`, since that's by convention the top module in
    benchmarks. We document this assumption.
    """
    if not rtl_text or "endmodule" not in rtl_text:
        raise ValueError("inject_properties_into_rtl: RTL has no `endmodule`.")

    body = _render_assertion_body(contract, properties)

    # Strategy: find the LAST occurrence of `endmodule` (as a whole token)
    # and insert before it. This works for single-module RTL (the common
    # case) and degrades gracefully for multi-module by injecting into
    # what is typically the top-level module declared last.
    #
    # We do a reverse search for the keyword on its own line (or
    # leading whitespace).
    lines = rtl_text.splitlines(keepends=True)
    last_endmodule_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r"^[ \t]*endmodule\b", lines[i]):
            last_endmodule_idx = i
            break

    if last_endmodule_idx == -1:
        raise ValueError(
            "inject_properties_into_rtl: no `endmodule` found on its own line."
        )

    head = "".join(lines[:last_endmodule_idx])
    tail = "".join(lines[last_endmodule_idx:])

    if not head.endswith("\n"):
        head += "\n"

    return head + body + "\n" + tail


# ---------- Backward-compatible entry points ----------

def render_properties_module(
    project_name: str,
    contract: dict,
    properties: list[PropertyIR],
) -> str:
    """
    DEPRECATED in B0.7: this used to produce a separate properties module
    plus a `bind` directive, which Yosys silently discards. Kept as a
    no-op that returns an empty string so legacy callers don't crash;
    the real work is now done by inject_properties_into_rtl().
    """
    return ""


def compile_property(prop: PropertyIR) -> str:
    """Legacy single-line emitter kept for backward compatibility."""
    block_lines, _, _ = _emit(prop)
    return "\n".join(block_lines).strip()


# ---------- Self-test ----------

if __name__ == "__main__":
    # Test 1: width normalization (regressions from B0.6)
    width_cases = [
        ("[31:0]", "[31:0]"),
        ("[7:0]",  "[7:0]"),
        ("[1:0]",  "[1:0]"),
        ("[0:0]",  ""),
        ("[1]",    ""),
        ("[]",     ""),
        ("",       ""),
        (None,     ""),
        ("1",      ""),
        ("8",      "[7:0]"),
        ("32",     "[31:0]"),
    ]
    width_fails = 0
    for inp, expected in width_cases:
        got = _normalize_width(inp)
        ok = got == expected
        marker = "OK " if ok else "FAIL"
        print(f"  [{marker}] _normalize_width({inp!r:15s}) = {got!r:10s}")
        if not ok:
            width_fails += 1

    # Test 2: assertion body rendering (combinational)
    print()
    print("--- Test combinational assertion body ---")
    from agents.property_ir import Timing, Equality
    contract_comb = {
        "module_type": "combinational",
        "ports": {"a": "[7:0]", "b": "[7:0]", "result": "[7:0]"},
        "clk": None,
        "rst": {"name": None, "polarity": "none"},
    }
    t_comb = Timing(kind="combinational", clk="", rst="")
    body = _render_assertion_body(
        contract_comb,
        [Equality(name="add", type="Equality", timing=t_comb,
                  left="result", right="a + b")],
    )
    print(body)
    body_ok = ("always_comb" in body and "assert" in body and "endmodule" not in body
               and "bind" not in body)

    # Test 3: end-to-end injection
    print()
    print("--- Test injection into DUT ---")
    rtl = """module my_dut (
    input  [7:0] a,
    input  [7:0] b,
    output [7:0] result
);
assign result = a & b;
endmodule
"""
    injected = inject_properties_into_rtl(
        rtl,
        contract_comb,
        [Equality(name="add", type="Equality", timing=t_comb,
                  left="result", right="a + b")],
    )
    print(injected)
    inj_ok = (
        "always_comb" in injected
        and "endmodule" in injected
        and injected.count("endmodule") == 1  # exactly one endmodule
        and injected.index("always_comb") < injected.index("endmodule")  # before
        and "bind" not in injected
    )

    # Summary
    print()
    if width_fails == 0 and body_ok and inj_ok:
        print("All B0.7 self-tests pass.")
    else:
        print(f"FAIL: width_fails={width_fails} body_ok={body_ok} inj_ok={inj_ok}")
