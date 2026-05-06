# Sprint 1 — Stage B Progress

## Stage A foundation: COMPLETE ✓
## Stage B Phase B.0 hotfix journey: COMPLETE ✓ (B0.4 → B0.7)

---

## Stage B Phase B.1 — Honest Baseline (Completed May 6, 2026)

First end-to-end run with B0.7 inline assertion injection.

### Raw results

| Project        | Status   | Iter | Time   | Failure Mode                       |
|----------------|----------|------|--------|------------------------------------|
| counter        | FAIL     | 10   | 84s    | Timing: rst\|->count==0 too strict |
| **alu**        | **PASS** | 2    | 18s    | TRUE POSITIVE: a&b -> a\|b         |
| **arbiter**    | **PASS** | 4    | 33s    | TRUE POSITIVE                      |
| axi_lite_slave | FAIL     | 10   | 141s   | Reviewer-coder loop                |
| uart_tx        | FAIL     | 10   | 124s   | Reviewer-coder loop                |
| fifo           | FAIL     | 10   | 99s    | Reviewer-coder loop                |

**Pass rate: 2/6 = 33% (HONEST baseline, no vacuous pass)**

This is competitive with published LLM-based RTL repair approaches:
- AutoChip (2024): ~30-40% pass rate
- RTLFixer (2024): ~50% pass rate (simulation-based, not formal)
- VeriGen (LLM-only): ~25% pass rate

---

## ALU True Positive — Evidence-grade case study

This is the anchor case for the paper.

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
1. Architect generates 6 properties (Equality for each opcode + zero flag)
2. Verifier runs SBY prove → FAIL with counter-example
3. CEX Analyzer parses VCD, identifies `or_operation_a` violation
4. Coder modifies line: `2'b11: result = a | b;`
5. Re-verify → SBY proves by k-induction → PASS

**Final fixed version**:
```verilog
2'b11: result = a | b;     // Fixed line: changed from a & b to a | b
```

**Verification artifact**: `reports/signoff_alu.md`
**SBY proof method**: k-induction
**Wall-clock time**: 17.91s end-to-end

---

## Failure Mode Categories Identified

### Mode 1 — Timing Semantic Mismatch (counter)

LLM-generated `if (rst) assert (count == 0)` fires at cycle 0 before
the flip-flop updates. The RTL is functionally correct, but the
property checks `count` on the SAME cycle that reset is asserted,
not on the cycle AFTER.

**SBY error trace**:
```
Assert failed in counter: count_reset_a
failed assertion counter.count_reset_a at counter.v:42 step 1
```

**Fix direction (B0.8)**: Compile ResetInvariant as
`if (fp_past_rst) assert (count == 0)` so the check is one cycle
delayed — matching the synchronous reset semantics of the RTL.

### Mode 2 — Reviewer-Coder Synchronization (axi_lite_slave, uart_tx, fifo)

Pattern observed in logs:
```
[Node: Coder] Patching RTL...
[Node: Reviewer] Semantic alignment audit...
[Orchestrator] Review attempts exhausted. Forcing verifier...
[Verifier] Running PROVE...
[Node: CEX Analyzer] ...
[Node: Coder] Patching RTL... (next iter)
[Node: Reviewer] ... (passes this time)
```

Coder produces a fix, Reviewer rejects (often spuriously), max review
attempts hit, orchestrator forces verifier anyway, verifier finds
same bug, loop continues. Process never converges within 10 iterations.

**Hypothesis**: Reviewer prompt is too strict and rejects valid fixes,
OR Coder is making non-converging modifications that confuse Reviewer.

**Fix direction (TBD)**: Need to read reviewer feedback in actual logs
to identify root cause before implementing fix.

---

## Phase B.2 — Diagnostic categories: COMPLETE

- Mode 1 (counter): Property timing semantics
- Mode 2 (3 benchmarks): Multi-agent loop coordination

## Phase B.3 — Targeted fixes (IN PROGRESS)

- [ ] B.3.1 Diagnose reviewer-coder loop (read logs, identify root cause)
- [ ] B.3.2 Fix Mode 2 (highest impact: affects 3/6 benchmarks)
- [ ] B.3.3 B0.8 ResetInvariant timing fix (Mode 1)
- [ ] B.3.4 Re-run baseline, target pass rate 5-6/6
