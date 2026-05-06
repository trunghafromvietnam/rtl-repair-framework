# Sprint 1 — Stage B Progress

## Stage A foundation: COMPLETE
## Stage B Phase B.0 hotfix journey: COMPLETE (B0.4 → B0.7)

## Stage B Phase B.1 — Honest Baseline (Completed May 6)

First end-to-end run with B0.7 inline assertion injection:

| Project        | Status | Iter | Time   | Failure Mode                  |
|----------------|--------|------|--------|-------------------------------|
| counter        | FAIL   | 10   | 84s    | Timing: rst|->count==0 too strict |
| **alu**        | **PASS** | 2  | 18s    | TRUE POSITIVE: a&b->a|b       |
| **arbiter**    | **PASS** | 4  | 33s    | TRUE POSITIVE                 |
| axi_lite_slave | FAIL   | 10   | 141s   | Reviewer-coder loop           |
| uart_tx        | FAIL   | 10   | 124s   | Reviewer-coder loop           |
| fifo           | FAIL   | 10   | 99s    | Reviewer-coder loop           |

**Pass rate: 2/6 = 33% (HONEST baseline, no vacuous pass)**

## ALU True Positive Evidence
- Buggy:  `2'b11: result = a & b`
- Fixed:  `2'b11: result = a | b`  (Coder added comment "Fixed line")
- Verified: SBY k-induction proof, DONE PASS rc=0

## Failure Mode Categories Identified

### Mode 1 (counter): Timing Semantic Mismatch
LLM-generated `if (rst) assert (count == 0)` fires at cycle 0 before
flip-flop updates. RTL is correct but property checks count value
on the same cycle reset is asserted. Need: rewrite as
`if (fp_past_rst) assert (count == 0)`.

### Mode 2 (axi/uart/fifo): Reviewer-Coder Synchronization
"Review attempts exhausted. Forcing verifier..." pattern repeats.
Coder produces fix, Reviewer rejects, max attempts reached, force
verifier with same RTL. Loops without convergence.

## Phase B.3 — Targeted fixes (NEXT)
- [ ] B.3.1 B0.8 ResetInvariant timing fix (counter Mode 1)
- [ ] B.3.2 Fix reviewer-coder feedback loop (Mode 2)
- [ ] B.3.3 Re-run baseline, expect pass rate 4-6/6