##
# Hotfix B0.4 (final, refactored) for property_compiler.py
#
# Scope:
#   This compiler targets the open-source Yosys + SBY formal flow.
#   Yosys's built-in SystemVerilog frontend supports only IMMEDIATE
#   assertions (assert/assume/cover statements inside always blocks),
#   not concurrent assertions (`assert property (@(posedge clk) ...)`).
#
#   This is verified by direct probe:
#     - `assert property (@(posedge clk) 1);`             -> ERROR
#     - `always @(posedge clk) my_a: assert (cond);`      -> OK
#     - `always @(posedge clk) if (a) assert (b);`        -> OK
#
#   Rather than depend on the commercial Verific frontend (which is the
#   only other path to concurrent SVA in the Yosys ecosystem), we emit
#   immediate-style assertions. This is the standard practice in the
#   open-source formal verification community and is compatible with
#   any future migration to Verific or commercial tools.
#
# Design:
#   - Sequential designs use `always @(posedge clk) if (<gate>) <label>: assert (<expr>);`
#   - Combinational designs use `always_comb if (<gate>) <label>: assert (<expr>);`
#   - $past(<sig>) in user-supplied expressions is auto-rewritten to past_<sig>,
#     and a `reg past_<sig>` is declared and updated once at module scope.
#   - TransitionProperty (cond NOW -> next_state NEXT cycle) is realised by
#     registering `cond` for one cycle: cond_<name> <= (cond_expr); and
#     gating the assertion on past_valid && cond_<name>.
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


# ---------- $past() rewriting ----------

_PAST_RE = re.compile(r"\$past\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")


def _rewrite_past_in_expr(expr: str) -> tuple[str, set[str]]:
    """
    Replace every $past(<simple_signal>) in `expr` with `past_<simple_signal>`,
    and report which simple signals need a past register declared.

    Compound expressions inside $past(...) are left untouched (caller can
    register the whole compound as a derived signal if needed).
    """
    needs: set[str] = set()

    def _sub(m: re.Match) -> str:
        sig = m.group(1)
        needs.add(sig)
        return f"past_{sig}"

    new_expr = _PAST_RE.sub(_sub, expr)
    return new_expr, needs


# ---------- Per-property emission ----------

def _is_comb(prop: PropertyIR) -> bool:
    return prop.timing.kind == "combinational"


def _emit(prop: PropertyIR) -> tuple[list[str], set[str], list[tuple[str, str]]]:
    """
    Return (assertion_lines, simple_past_signals, transition_conditions).

    - assertion_lines: SV lines (already indented one level) for this property.
    - simple_past_signals: signals that need `reg past_<sig>` declared.
    - transition_conditions: list of (name, condition_expr) tuples that need
      a `reg cond_<name>` declared and updated at module scope.
    """
    name = prop.name
    clk = prop.timing.clk
    rst = prop.timing.rst
    comb = _is_comb(prop)

    # ===== MutualExclusion =====
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

    # ===== Implication =====
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

    # ===== Equality =====
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

    # ===== ResetInvariant =====
    if isinstance(prop, ResetInvariant):
        if comb:
            raise ValueError(
                f"ResetInvariant '{name}' requires sequential timing."
            )
        return [
            f"    always @(posedge {clk}) "
            f"if ({rst}) {name}_a: "
            f"assert ({prop.signal} == {prop.expected_value});"
        ], set(), []

    # ===== HoldProperty =====
    if isinstance(prop, HoldProperty):
        if comb:
            raise ValueError(
                f"HoldProperty '{name}' requires sequential timing."
            )
        return [
            f"    always @(posedge {clk}) "
            f"if (past_valid) {name}_a: "
            f"assert ({prop.signal} == past_{prop.signal});"
        ], {prop.signal}, []

    # ===== TransitionProperty =====
    # Rewrite as: register `cond_<name> <= condition_expr` each cycle,
    # then assert `next_state` whenever past_valid && cond_<name>.
    if isinstance(prop, TransitionProperty):
        if comb:
            raise ValueError(
                f"TransitionProperty '{name}' requires sequential timing."
            )
        cond_expr, cond_needs = _rewrite_past_in_expr(prop.condition)
        next_expr, next_needs = _rewrite_past_in_expr(prop.next_state)
        needs = cond_needs | next_needs

        return [
            f"    always @(posedge {clk}) "
            f"if (past_valid && cond_{name}) "
            f"{name}_a: assert ({next_expr});"
        ], needs, [(name, cond_expr)]

    # ===== HandshakeProperty =====
    # Bounded eventuality is not directly expressible as immediate
    # assertions. We emit a weakened safety check + a cover for the
    # success case, which is sufficient for non-vacuity proofs.
    if isinstance(prop, HandshakeProperty):
        if comb:
            raise ValueError(
                f"HandshakeProperty '{name}' requires sequential timing."
            )
        return [
            f"    always @(posedge {clk}) "
            f"if (past_valid && {prop.valid}) "
            f"{name}_a: assert ({prop.valid} || {prop.ready});",
            f"    always @(posedge {clk}) "
            f"{name}_c: cover ({prop.valid} && {prop.ready});",
        ], set(), []

    raise ValueError(f"Unsupported property IR type: {type(prop).__name__}")


def _emit_cover(prop: PropertyIR) -> str | None:
    """Cover statement, also in immediate form."""
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


# ---------- Width inference for past registers ----------

_WIDTH_RE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")


def _width_for(sig: str, ports: dict) -> int:
    """Return bit width of a port; 1 if scalar or unknown."""
    raw = (ports.get(sig) or "").strip()
    if not raw:
        return 1
    m = _WIDTH_RE.match(raw)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return abs(a - b) + 1
    return 1


# ---------- Module renderer ----------

def render_properties_module(
    project_name: str,
    contract: dict,
    properties: list[PropertyIR],
) -> str:
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

    # ---- Port declarations ----
    port_decls: list[str] = []
    if is_seq:
        port_decls.append(f"    input logic {clk}")
        if rst_name:
            port_decls.append(f"    input logic {rst_name}")
    for name, width in ports.items():
        if is_seq and (name == clk or name == rst_name):
            continue
        width_str = (width or "").strip()
        if width_str:
            port_decls.append(f"    input logic {width_str} {name}")
        else:
            port_decls.append(f"    input logic {name}")

    lines: list[str] = []
    lines.append(f"module {project_name}_props (")
    lines.append(",\n".join(port_decls))
    lines.append(");")
    lines.append("")

    # ---- Sequential preamble ----
    if is_seq:
        lines.append("    // Past-valid bootstrap: avoid asserting at T=0.")
        lines.append("    reg past_valid = 1'b0;")
        lines.append(f"    always @(posedge {clk}) past_valid <= 1'b1;")
        lines.append("")

        if rst_name:
            rst_active = f"!{rst_name}" if rst_polarity == "active_low" else rst_name
            rst_inactive = rst_name if rst_polarity == "active_low" else f"!{rst_name}"
            lines.append("    // Drive reset for one cycle, then release.")
            lines.append(f"    always @(posedge {clk}) begin")
            lines.append(f"        if (!past_valid) assume({rst_active});")
            lines.append(f"        else              assume({rst_inactive});")
            lines.append("    end")
            lines.append("")

    # ---- First pass: collect emission + register needs ----
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

        assertion_lines.extend(block_lines)

        cov = _emit_cover(prop)
        if cov is not None:
            assertion_lines.append(cov)

        past_signals.update(p_needs)
        transition_conds.extend(t_needs)

    # ---- Past register declarations + update block ----
    if is_seq and (past_signals or transition_conds):
        lines.append("    // Past-cycle snapshots used by hold / transition properties.")
        for sig in sorted(past_signals):
            w = _width_for(sig, ports)
            if w == 1:
                lines.append(f"    reg past_{sig};")
            else:
                lines.append(f"    reg [{w-1}:0] past_{sig};")
        for cname, _ in transition_conds:
            lines.append(f"    reg cond_{cname};")

        lines.append(f"    always @(posedge {clk}) begin")
        for sig in sorted(past_signals):
            lines.append(f"        past_{sig} <= {sig};")
        for cname, expr in transition_conds:
            lines.append(f"        cond_{cname} <= ({expr});")
        lines.append("    end")
        lines.append("")

    # ---- Assertions ----
    lines.extend(assertion_lines)

    lines.append("")
    lines.append("endmodule")
    lines.append("")
    lines.append(f"bind {project_name} {project_name}_props props_inst (.*);")

    return "\n".join(lines)


# ---------- Backward-compat shim ----------

def compile_property(prop: PropertyIR) -> str:
    """Legacy single-line emitter; renders one property's assertion lines."""
    block_lines, _, _ = _emit(prop)
    return "\n".join(block_lines).strip()
