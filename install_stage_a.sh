#!/usr/bin/env bash
#
# install_stage_a.sh - Automated installer for Stage A stabilization patches.
#
# This script:
#   1. Verifies the project root layout
#   2. Verifies that all 8 patch files are present in this bundle
#   3. Compile-tests every patch BEFORE replacing anything
#   4. Replaces the 8 files in the project
#   5. Compile-tests the project after replacement
#   6. Runs a quick offline sanity check on the property compiler
#
# Usage:
#   1. Place this script + the `patches/` folder at the project root
#   2. Run:  bash install_stage_a.sh
#
# Safe to re-run: the script aborts before any destructive action if
# anything is wrong, and the project should already be under git so
# `git checkout .` can roll back any change.

set -euo pipefail

PROJECT_ROOT="$(pwd)"
PATCH_DIR="${PROJECT_ROOT}/patches"

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[0;33m"
BOLD="\033[1m"
RESET="\033[0m"

ok()    { echo -e "${GREEN}[OK]${RESET} $*"; }
fail()  { echo -e "${RED}[FAIL]${RESET} $*" >&2; exit 1; }
warn()  { echo -e "${YELLOW}[WARN]${RESET} $*"; }
step()  { echo; echo -e "${BOLD}=== $* ===${RESET}"; }


# ---------- Step 1: Project layout ----------
step "Step 1/6: Project layout check"

[ -d agents ] || fail "Missing directory: agents/. Run this script from the project root."
[ -d tools ]  || fail "Missing directory: tools/. Run this script from the project root."
[ -f main_graph.py ]    || fail "Missing main_graph.py at project root."
[ -f run_benchmarks.py ] || fail "Missing run_benchmarks.py at project root."
ok "Project layout looks correct."


# ---------- Step 2: Bundle layout ----------
step "Step 2/6: Patch bundle check"

[ -d "$PATCH_DIR" ] || fail "Missing patches/ folder. Place it next to install_stage_a.sh."

EXPECTED_PATCHES=(
  "patches/agents/state.py"
  "patches/agents/nodes.py"
  "patches/agents/property_compiler.py"
  "patches/main_graph.py"
  "patches/tools/cex_translator.py"
  "patches/tools/verifier.py"
  "patches/tools/reporter.py"
  "patches/run_benchmarks.py"
)

for f in "${EXPECTED_PATCHES[@]}"; do
  [ -f "$f" ] || fail "Patch missing: $f"
done
ok "All 8 patch files present."


# ---------- Step 3: Compile-test patches BEFORE touching the project ----------
step "Step 3/6: Pre-flight compile of patches"

python -m py_compile \
  patches/agents/state.py \
  patches/agents/nodes.py \
  patches/agents/property_compiler.py \
  patches/main_graph.py \
  patches/tools/cex_translator.py \
  patches/tools/verifier.py \
  patches/tools/reporter.py \
  patches/run_benchmarks.py \
  || fail "One or more patches do not compile. Aborting before any change."

ok "All 8 patches compile cleanly."


# ---------- Step 4: Replace files ----------
step "Step 4/6: Replacing project files"

cp patches/agents/state.py              agents/state.py             && ok "agents/state.py"
cp patches/agents/property_compiler.py  agents/property_compiler.py && ok "agents/property_compiler.py"
cp patches/agents/nodes.py              agents/nodes.py             && ok "agents/nodes.py"
cp patches/tools/cex_translator.py      tools/cex_translator.py     && ok "tools/cex_translator.py"
cp patches/tools/verifier.py            tools/verifier.py           && ok "tools/verifier.py"
cp patches/tools/reporter.py            tools/reporter.py           && ok "tools/reporter.py"
cp patches/main_graph.py                main_graph.py               && ok "main_graph.py"
cp patches/run_benchmarks.py            run_benchmarks.py           && ok "run_benchmarks.py"


# ---------- Step 5: Post-replacement compile + import sanity ----------
step "Step 5/6: Post-replacement verification"

python -m py_compile \
  agents/state.py agents/nodes.py agents/property_compiler.py \
  tools/cex_translator.py tools/verifier.py tools/reporter.py \
  main_graph.py run_benchmarks.py \
  || fail "Project does not compile after replacement. Inspect with 'git diff'."

ok "All 8 replaced files compile."

python - <<'PY' || fail "Import test failed. Inspect 'git diff' or stack trace above."
from agents.state import AgentState
from agents.nodes import (
    contract_node, architect_node, verifier_node,
    coder_node, cex_analyzer_node, reviewer_node,
)
from agents.property_compiler import render_properties_module
from tools.verifier import VerifierBridge
from tools.cex_translator import CEXTranslator
from tools.reporter import SignOffReporter
from main_graph import app, run_repair_system

# Stage A invariants the new state.py must expose.
required = {"review_attempts", "iteration_count", "fix_history"}
missing = required - set(AgentState.__annotations__.keys())
if missing:
    raise SystemExit(f"AgentState missing keys: {missing}")
print("[import-check] AgentState keys OK")
PY

ok "Import + AgentState schema check passed."


# ---------- Step 6: Offline compiler sanity ----------
step "Step 6/6: Property compiler offline sanity"

python - <<'PY' || fail "Property compiler sanity check failed."
from agents.property_ir import Timing, Equality
from agents.property_compiler import render_properties_module

# Pure combinational design: must NOT contain 'posedge clk' or 'past_valid'.
contract_comb = {
    "module_type": "combinational",
    "ports": {"a": "[7:0]", "b": "[7:0]", "result": "[7:0]"},
    "clk": None,
    "rst": {"name": None, "polarity": "none"},
}
t_comb = Timing(kind="combinational", clk="", rst="")
sva = render_properties_module(
    "alu_check",
    contract_comb,
    [Equality(name="add", type="Equality", timing=t_comb,
              left="result", right="a + b")],
)

bad_for_comb = ["posedge", "past_valid"]
for token in bad_for_comb:
    if token in sva:
        raise SystemExit(
            f"Combinational SVA must not contain '{token}'. Got:\n{sva}"
        )
print("[compiler-check] Combinational dispatch OK (no clocking, no past_valid)")

# Sequential design: must declare clk exactly ONCE.
contract_seq = {
    "module_type": "sequential",
    "ports": {"clk": "", "rst": "", "en": "", "count": "[7:0]"},  # LLM may include clk/rst here
    "clk": "clk",
    "rst": {"name": "rst", "polarity": "active_high"},
}
t_seq = Timing(kind="sequential", clk="clk", rst="rst")
sva2 = render_properties_module(
    "counter_check",
    contract_seq,
    [Equality(name="zero_after_rst", type="Equality", timing=t_seq,
              left="count", right="8'b0")],
)
clk_decls = sva2.count("input logic clk")
if clk_decls != 1:
    raise SystemExit(f"Expected exactly 1 'input logic clk' decl, found {clk_decls}\n{sva2}")
if "past_valid" not in sva2:
    raise SystemExit("Sequential SVA missing past_valid bootstrap.")
print("[compiler-check] Sequential dispatch OK (single clk decl, has past_valid)")
PY

ok "Property compiler dispatches combinational vs sequential correctly."


echo
echo -e "${GREEN}${BOLD}== Stage A patches installed and verified successfully ==${RESET}"
echo
echo "Next steps:"
echo "  1. git diff --stat   # see what changed"
echo "  2. git add -A && git commit -m 'feat(stage-a): integrate stabilization patches'"
echo "  3. git push"
echo "  4. python test_smoke_counter.py   # re-run smoke test"
echo
