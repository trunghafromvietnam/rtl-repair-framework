# Sprint 1 — Stage B Progress

## Stage A foundation: COMPLETE ✓
## Stage B Phase B.0 hotfix journey: COMPLETE ✓ (B0.4 → B0.7)

---

## Stage B Phase B.1 — Honest Baseline (DECLARED COMPLETE May 6, 2026)

**Strategic decision (May 6, 2026):** Stop single-run debugging at 2/6 pass
rate. Move forward to multi-run statistical baseline (Phase B.4) where
LLM nondeterminism becomes data, not bug.

### Final baseline numbers

| Project        | Status   | Iter | Time   | Failure Mode (if any)            |
|----------------|----------|------|--------|----------------------------------|
| counter        | FAIL     | 10   | 84s    | Property timing semantics        |
| **alu**        | **PASS** | 2    | 18s    | TRUE POSITIVE: a&b -> a\|b       |
| **arbiter**    | **PASS** | 4    | 33s    | TRUE POSITIVE                    |
| axi_lite_slave | FAIL     | 10   | 141s   | ResetInvariant + property syntax |
| uart_tx        | FAIL     | 10   | 124s   | Logic bug (Coder cannot fix)     |
| fifo           | FAIL     | 10   | 99s    | Logic bug (Coder cannot fix)     |

**Pass rate: 2/6 = 33% (honest, no vacuous false positives)**

**Reference points** (from published literature):
- AutoChip 2024: 30-40% pass rate (LLM + simulation feedback)
- RTLFixer 2024: ~50% pass rate (LLM + Verilator simulation)
- VeriGen 2023: ~25% pass rate (LLM-only zero-shot)

### Why we stop debugging here

After 8 hotfix iterations (B0.1 → B0.7) over 6 days, each fix exposed
a new failure mode. This is classic diminishing returns territory.
The remaining failures span 3 distinct, unrelated root causes — fixing
all three would require 1+ additional week of work without proportional
paper value.

By contrast, **Phase B.4 (multi-run × 5 per benchmark)** can show that
some currently-failing benchmarks pass *some* of the time. This converts
"single-run fail" into "variance across runs" — a more honest and
informative result for a paper that argues the framework is probabilistic.

---

## ALU True Positive — Anchor case study for the paper

This is **the** end-to-end case study that proves the thesis works.

**Bug in buggy version** (`benchmarks_buggy/alu.v`):
```verilog
case (op)
    2'b00: result = a + b;
    2'b01: result = a - b;
    2'b10: result = a & b;
    2'b11: result = a & b;     // BUG: should be OR
endcase
```

**Pipeline trace**:
1. Architect generates 6 properties (one Equality per opcode + zero flag)
2. Verifier runs SBY prove → FAIL with VCD counter-example
3. CEX Analyzer parses trace, identifies `or_operation_a` violation
4. Coder modifies single line: `2'b11: result = a | b;`
5. Re-verify → SBY proves by k-induction → PASS

**Final fix** (line edit, 3 chars changed):
```verilog
2'b11: result = a | b;     // Fixed line: changed from a & b to a | b
```

**Verification artifact**: `reports/signoff_alu.md`
**Proof method**: SBY/Yosys + Z3 SMT, k-induction
**Wall-clock time**: 17.91s end-to-end
**Iterations**: 2 (1 fail + 1 fix + verify)

This single case demonstrates the entire research thesis: an LLM-driven
pipeline using only open-source formal verification tools can detect
AND fix a real RTL bug, with mathematical proof of correctness.

---

## Engineering Insight — Yosys 0.61 bind directive bug

During development we discovered that Yosys 0.61's built-in
SystemVerilog frontend silently discards modules referenced via
`bind` directives. Specifically, the warning emitted is:

```
Removing unused module `\dut_props'.
Removed 1 unused modules.
```

The bound module is treated as unused and garbage-collected before
reaching the SMT solver. Without the assertions reaching the solver,
SBY trivially returns PASS regardless of RTL correctness.

We confirmed this through a discriminating probe: identical buggy
RTL with identical assertions produced PASS via bind path and FAIL
with counter-example via inline path.

**Workaround (B0.7)**: inline assertion logic directly into the DUT
module before its final `endmodule`. This is the standard practice
in the open-source formal verification community when targeting
Yosys without the commercial Verific frontend.

This finding is candidate for an "Engineering Notes" section in
the paper — useful to the open-source EDA community.

---

## Failure Mode Categorization (for paper Discussion section)

### Mode 1 — Property Timing Semantic Mismatch (counter)
LLM-generated `if (rst) assert (count == 0)` fires at cycle 0 before
the synchronous reset takes effect through flip-flops. Future fix:
property compiler emits `if (fp_past_rst) assert (...)`.

### Mode 2 — Architectural fragility under multi-property pressure (axi_lite_slave)
With 8+ properties, multiple ResetInvariants compete and the Coder
cannot satisfy all simultaneously. Coder convergence fails over
10 iterations. Future fix: property prioritization or relaxed
verification depth.

### Mode 3 — Logic Bugs Beyond Coder Capability (uart_tx, fifo)
Bugs in temporal logic (e.g., `tx_start_triggers_busy` at step 2)
require multi-cycle reasoning that current Coder prompt does not
adequately support. Future fix: stronger Coder prompt with state
machine reasoning examples, or higher-quality CEX analysis.

---

## Phase B.4 — Multi-run Statistical Baseline (NEXT — start now)

**Goal**: convert "single-run fail" into "% pass over N runs" — give
LLM nondeterminism a chance to surface as data.

**Plan**:
- 6 benchmarks × 5 runs each = 30 runs total
- Track per-run: status, iterations, time, token cost
- Output: mean ± std for each metric, per benchmark
- Cost estimate: ~$8-10 OpenAI tokens
- Time estimate: 2 days (1 day infra, 1 day run + analyze)

**Deliverable**: `logs/sprint1/multi_run_baseline.csv` (30 rows) +
`logs/sprint1/SUMMARY.md` (per-benchmark variance table).

## Phase B.5 — Metrics Infrastructure (after B.4)

**Goal**: tools/metrics.py for paper-grade measurement.

Tracks:
- Edit distance (Levenshtein) buggy → fixed RTL
- Lines changed in DUT (excluding inlined property block)
- Total OpenAI tokens used
- Cost in USD per run
- Wall-clock time per node (Contract / Architect / Verifier / Coder / Reviewer)

## Sprint 2 — Benchmark expansion (after B.5)

**Goal**: 6 → 18-20 modules from HDLBits, OpenCores.
With 3 bug variants per module → ~60-80 test cases.
Cost estimate: $30 OpenAI for full evaluation.
