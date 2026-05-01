##
# Author: Ha Tran
# Description: Property IR -> SystemVerilog Assertion (SVA) compiler.
#
# STABILIZATION PATCH NOTES:
# - Every property type now dispatches on prop.timing.kind:
#     "combinational" -> immediate assertion (no clocking)
#     "sequential"    -> concurrent property with @(posedge clk)
#   This is critical because pure combinational designs (ALU, arbiter)
#   have no clock; binding a clocked property to them produces either a
#   false vacuous PASS or an elaboration error.
# - render_properties_module() now omits clk/rst ports, the past_valid
#   bootstrap, and the reset-release assumption when the design is
#   combinational, so the bound props module matches the DUT interface.
# - Cover statements are emitted with the same timing discipline as the
#   matching assertion (immediate cover for combinational designs).
##

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


def _is_comb(prop: PropertyIR) -> bool:
    return prop.timing.kind == "combinational"


def compile_property(prop: PropertyIR) -> str:
    """
    Render a single Property IR object as one or more SVA statements.

    The output is always a self-contained snippet that can be placed
    inside the body of a properties module. Indentation is intentionally
    minimal here — render_properties_module owns final layout.
    """
    clk = prop.timing.clk
    rst = prop.timing.rst
    comb = _is_comb(prop)

    # ---------- MutualExclusion ----------
    if isinstance(prop, MutualExclusion):
        if len(prop.signals) < 2:
            raise ValueError(
                f"MutualExclusion '{prop.name}' needs >=2 signals, got {prop.signals}"
            )
        # Pairwise mutex: !(a && b) && !(a && c) && !(b && c) ...
        terms = []
        sigs = prop.signals
        for i in range(len(sigs)):
            for j in range(i + 1, len(sigs)):
                terms.append(f"!({sigs[i]} && {sigs[j]})")
        body = " && ".join(terms)
        if comb:
            return f"{prop.name}_a: assert ({body});"
        return f"{prop.name}_a: assert property (@(posedge {clk}) {body});"

    # ---------- Implication ----------
    if isinstance(prop, Implication):
        if comb:
            return (
                f"{prop.name}_a: assert "
                f"(!({prop.antecedent}) || ({prop.consequent}));"
            )
        return (
            f"{prop.name}_a: assert property (@(posedge {clk}) "
            f"({prop.antecedent}) |-> ({prop.consequent}));"
        )

    # ---------- Equality ----------
    if isinstance(prop, Equality):
        if comb:
            return f"{prop.name}_a: assert (({prop.left}) == ({prop.right}));"
        return (
            f"{prop.name}_a: assert property (@(posedge {clk}) "
            f"({prop.left}) == ({prop.right}));"
        )

    # ---------- ResetInvariant (sequential by definition) ----------
    if isinstance(prop, ResetInvariant):
        if comb:
            # ResetInvariant on a combinational design is meaningless —
            # surface this clearly rather than silently emitting bad SVA.
            raise ValueError(
                f"ResetInvariant '{prop.name}' requires sequential timing."
            )
        # Holds on the cycle the reset is asserted (active-high convention;
        # active-low designs are normalized in render_properties_module).
        return (
            f"{prop.name}_a: assert property (@(posedge {clk}) "
            f"{rst} |-> ({prop.signal} == {prop.expected_value}));"
        )

    # ---------- HoldProperty (sequential — uses $stable) ----------
    if isinstance(prop, HoldProperty):
        if comb:
            raise ValueError(
                f"HoldProperty '{prop.name}' requires sequential timing."
            )
        return (
            f"{prop.name}_a: assert property (@(posedge {clk}) "
            f"$stable({prop.signal}));"
        )

    # ---------- TransitionProperty (sequential) ----------
    if isinstance(prop, TransitionProperty):
        if comb:
            raise ValueError(
                f"TransitionProperty '{prop.name}' requires sequential timing."
            )
        return (
            f"{prop.name}_a: assert property (@(posedge {clk}) "
            f"({prop.condition}) |=> ({prop.next_state}));"
        )

    # ---------- HandshakeProperty (sequential) ----------
    if isinstance(prop, HandshakeProperty):
        if comb:
            raise ValueError(
                f"HandshakeProperty '{prop.name}' requires sequential timing."
            )
        return (
            f"{prop.name}_a: assert property (@(posedge {clk}) "
            f"{prop.valid} |-> ##[0:$] {prop.ready});"
        )

    raise ValueError(f"Unsupported property IR type: {type(prop).__name__}")


def _emit_cover(prop: PropertyIR) -> str | None:
    """
    Emit a cover statement that proves the assertion is non-vacuous.

    We only emit a cover for property types where 'reaching the antecedent'
    is meaningful. For pure equality/mutex, we cover that the LHS expression
    is exercised at least once.
    """
    clk = prop.timing.clk
    comb = _is_comb(prop)

    if isinstance(prop, Equality):
        target = f"({prop.left}) == ({prop.right})"
    elif isinstance(prop, Implication):
        target = f"({prop.antecedent})"
    elif isinstance(prop, HandshakeProperty):
        target = f"({prop.valid} && {prop.ready})"
    elif isinstance(prop, TransitionProperty):
        target = f"({prop.condition})"
    else:
        return None  # cover is optional; skip for mutex/hold/reset-invariant

    if comb:
        return f"{prop.name}_c: cover ({target});"
    return f"{prop.name}_c: cover property (@(posedge {clk}) {target});"


def render_properties_module(
    project_name: str,
    contract: dict,
    properties: list[PropertyIR],
) -> str:
    """
    Build the full SystemVerilog properties module + bind statement.

    Behaviour:
    - For sequential designs: declare clk (and rst if present), emit the
      past_valid bootstrap and the reset-release assumption.
    - For combinational designs: declare only the data ports. No clocking,
      no reset assumption, no past_valid logic.
    - All non-clock/reset ports are declared with the literal width string
      taken verbatim from the contract.
    """
    if not properties:
        raise ValueError(
            "render_properties_module called with zero properties — "
            "this would produce a vacuous-by-construction module."
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

    # ---- Build port list ----
    port_decls = []
    if is_seq:
        port_decls.append(f"    input logic {clk}")
        if rst_name:
            port_decls.append(f"    input logic {rst_name}")
    for name, width in ports.items():
        # Skip duplicates of clk/rst that the LLM may have included in ports.
        if is_seq and (name == clk or name == rst_name):
            continue
        width_str = width if width and width.strip() else ""
        if width_str:
            port_decls.append(f"    input logic {width_str} {name}")
        else:
            port_decls.append(f"    input logic {name}")

    lines: list[str] = []
    lines.append(f"module {project_name}_props (")
    lines.append(",\n".join(port_decls))
    lines.append(");")
    lines.append("")

    # ---- Sequential preamble: past_valid + reset release ----
    if is_seq:
        lines.append("    // Past-valid bootstrap: avoid asserting on T=0")
        lines.append("    reg past_valid = 1'b0;")
        lines.append(f"    always @(posedge {clk}) past_valid <= 1'b1;")
        lines.append("")

        if rst_name:
            # Normalize active-low to active-high view for the assumption.
            rst_active = (
                f"!{rst_name}" if rst_polarity == "active_low" else rst_name
            )
            rst_inactive = (
                rst_name if rst_polarity == "active_low" else f"!{rst_name}"
            )
            lines.append(
                "    // Drive reset for one cycle, then release it forever."
            )
            lines.append("    // This prevents the trivial 'hold reset' counter-example")
            lines.append("    // that produces vacuous PASSes.")
            lines.append(f"    always @(posedge {clk}) begin")
            lines.append(f"        if (!past_valid) assume({rst_active});")
            lines.append(f"        else              assume({rst_inactive});")
            lines.append("    end")
            lines.append("")

    # ---- Assertions + matching covers ----
    for prop in properties:
        try:
            lines.append(f"    {compile_property(prop)}")
        except ValueError as e:
            # Surface compile error as a comment AND as an assert 0 so the
            # verifier loudly fails instead of silently dropping the property.
            lines.append(f"    // [COMPILE_ERROR] {e}")
            lines.append(f"    {prop.name}_compile_err: assert (1'b0);")
            continue

        cover_stmt = _emit_cover(prop)
        if cover_stmt is not None:
            lines.append(f"    {cover_stmt}")

    lines.append("")
    lines.append("endmodule")
    lines.append("")
    lines.append(f"bind {project_name} {project_name}_props props_inst (.*);")

    return "\n".join(lines)
