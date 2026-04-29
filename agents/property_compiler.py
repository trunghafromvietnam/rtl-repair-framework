from agents.property_ir import *

def compile_property(prop: PropertyIR):

    clk = prop.timing.clk
    rst = prop.timing.rst

    # ===== MUTUAL EXCLUSION =====
    if isinstance(prop, MutualExclusion):
        a, b = prop.signals
        return f"assert property (@(posedge {clk}) !({a} && {b}));"

    # ===== IMPLICATION =====
    elif isinstance(prop, Implication):
        if prop.timing.kind == "combinational":
            return f"assert (!({prop.antecedent}) || ({prop.consequent}));"
        else:
            return f"""
assert property (@(posedge {prop.timing.clk})
    {prop.antecedent} |-> {prop.consequent}
);
"""

    # ===== EQUALITY =====
    elif isinstance(prop, Equality):
        return f"assert property (@(posedge {clk}) {prop.left} == {prop.right});"

    # ===== RESET INVARIANT =====
    elif isinstance(prop, ResetInvariant):
        return f"assert property (@(posedge {clk}) {prop.timing.rst} |-> ({prop.signal} == {prop.expected_value}));"

    # ===== HOLD PROPERTY =====
    elif isinstance(prop, HoldProperty):
        return f"""
assert property (@(posedge {prop.timing.clk})
    $stable({prop.signal})
);
"""

    # ===== TRANSITION =====
    elif isinstance(prop, TransitionProperty):
        return f"""
assert property (@(posedge {prop.timing.clk})
    {prop.condition} |-> {prop.next_state}
);
"""

    # ===== HANDSHAKE =====
    elif isinstance(prop, HandshakeProperty):
        return f"assert property (@(posedge {clk}) {prop.valid} |-> ##[0:$] {prop.ready});"

    else:
        raise ValueError(f"Unsupported property type: {type(prop)}")

def render_properties_module(project_name: str, contract: dict, properties: list[PropertyIR]):
    ports = contract.get("ports", {})
    clk = contract.get("clk", "clk")
    rst = contract.get("rst", {}).get("name", "rst")

    lines = []
    # 1. Module Header with literal widths
    port_decls = [f"    input logic {clk}"]
    if rst: port_decls.append(f"    input logic {rst}")
    for name, width in ports.items():
        port_decls.append(f"    input logic {width} {name}")

    lines.append(f"module {project_name}_props (\n" + ",\n".join(port_decls) + "\n);")

    # 2. Past Valid Bootstrap
    lines.append("\n    reg past_valid = 0;")
    lines.append("    initial past_valid = 0;")
    lines.append(f"    always @(posedge {clk}) past_valid <= 1;\n")

    # 3. Reset Release Assumption 
    if rst:
        lines.append(f"    always @(posedge {clk}) begin")
        lines.append(f"        if (!past_valid) assume({rst} == 1'b1);")
        lines.append(f"        else assume({rst} == 1'b0);")
        lines.append(f"    end\n")

    # 4. Assertions & Covers
    for prop in properties:
        lines.append(f"    {compile_property(prop)}")
        if hasattr(prop, 'left'):
            lines.append(f"    cover property (@(posedge {clk}) {prop.left} == {prop.right});")

    lines.append("\nendmodule")

    # 5. Bind Line
    lines.append(f"\nbind {project_name} {project_name}_props props_inst (.*);")

    return "\n".join(lines)