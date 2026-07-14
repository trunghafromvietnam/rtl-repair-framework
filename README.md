cat > README.md << 'EOF'
# RTL Repair Framework

A multi-agent pipeline that couples a large language model with an entirely
open-source formal verification backend (Yosys, SymbiYosys, Z3) to repair RTL
through counterexample-guided iteration. The framework synthesizes formal
properties from a specification, verifies the design, and feeds counterexamples
back to the LLM until the design is proved correct by k-induction or an
iteration budget is exhausted.

This repository accompanies the paper *Open-Source LLM-Driven Formal
Verification: A Multi-Agent Pipeline for RTL Repair* (preprint link TBD).

## Results summary

The framework reliably repairs the `alu` benchmark (5/5 runs, proven by
k-induction). The remaining five benchmarks fail consistently, each through a
distinct, characterized failure mode. We report this honestly: this is a
feasibility study with a detailed failure analysis, not a high-pass-rate system.

| Benchmark | Pass | Avg iters | Avg time (s) | Outcome |
|---|---|---|---|---|
| alu | 5/5 | 2.0 | 16.5 ± 3.0 | proven correct |
| counter | 0/5 | 10.0 | 47.8 ± 3.4 | bounded-cover vacuity |
| arbiter | 0/5 | 10.0 | 86.7 ± 2.8 | spec ambiguity |
| axi_lite_slave | 0/5 | 10.0 | 127.1 ± 20.9 | multi-property |
| uart_tx | 0/5 | 10.0 | 125.3 ± 18.7 | temporal logic |
| fifo | 0/3* | 10.0 | 108.7 ± 3.5 | temporal logic |

\* For `fifo`, two of five runs aborted due to an OpenAI API quota limit, not a
verification failure. Those runs are excluded from all statistics.

Raw data: `logs/sprint1/multi_run_b11.csv` and `logs/sprint1/multi_run_b11.log`.

## Environment

Results were produced with the following versions:

| Component | Version |
|---|---|
| Python | 3.12.3 |
| LangGraph | 1.0.6 |
| openai (client library) | 2.15.0 |
| LLM | GPT-4o (temperature 0) |
| Yosys | 0.61+39 (git sha1 49e595079) |
| SymbiYosys (SBY) | 0.61 |
| Z3 | 4.15.5 |

Yosys, SBY, and Z3 must be installed separately and available on `PATH`.

## Installation

```bash
git clone https://github.com/trunghafromvietnam/rtl-repair-framework.git
cd rtl-repair-framework
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set your OpenAI API key:

```bash
echo "OPENAI_API_KEY=sk-..." > .env
```

## Reproducing the results

Run the full multi-run evaluation (six benchmarks, five runs each):

```bash
python run_n_times.py --runs 5 --output logs/sprint1/multi_run_b11.csv
```

Expect roughly 50–60 minutes and a non-trivial number of API calls. Because the
language model is nondeterministic even at temperature 0, exact timings will
vary; pass/fail outcomes have been stable across our runs.

## Architecture

Six nodes orchestrated by a LangGraph state graph:

- **contract** — extracts the structural interface (ports, clock, reset polarity)
- **architect** — generates a typed Property IR and inlines compiled SVA assertions into the DUT
- **verifier** — runs Yosys + SBY + Z3 in prove and cover modes
- **cex_analyzer** — parses the VCD counterexample and localizes the fault
- **coder** — applies the repair
- **reviewer** — audits semantic alignment before re-verification

## Known limitations

- Assertions are injected inline rather than via `bind`. The Yosys 0.61
  SystemVerilog frontend silently discards `bind`-referenced modules, which
  causes SymbiYosys to return a false PASS. See Section VI-C of the paper.
- The benchmark suite is small (six modules, one injected bug each).
- All experiments use a single LLM (GPT-4o).

## License

MIT — see `LICENSE`.
EOF