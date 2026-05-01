##
# Author: Ha Tran
# Description: Benchmark runner. Iterates over the test suite, runs the
# full repair graph on each, and logs per-project metrics to CSV.
#
# STABILIZATION PATCH NOTES:
# - Replaced `os.system("rm -rf benchmarks/*/")` (shell injection prone,
#   could remove unintended files) with a Python-side cleanup that ONLY
#   deletes per-project work directories.
# - Logger now records additional metrics that the ablation study will
#   need: tokens-equivalent (iterations as a proxy), vacuity flag, and
#   final verification status without ambiguity.
# - Each benchmark is wrapped in a try/except so a crash on one module
#   does not abort the entire suite.
##

import os
import shutil
import time

from main_graph import run_repair_system, ABLATION_MODE


RESULTS_FILE = (
    "ablation_mode_results.csv" if ABLATION_MODE else "full_system_results.csv"
)


BENCHMARKS = [
    {
        "name": "counter",
        "spec": (
            "Parameterized synchronous up-counter (default WIDTH=8). On the "
            "rising edge of clk: if rst is high, count is set to 0; else if "
            "en is high, count increments by 1; otherwise count holds. "
            "Wraps around on overflow."
        ),
        "file": "benchmarks_buggy/counter.v",
    },
    {
        "name": "alu",
        "spec": (
            "Combinational ALU. Opcode 2'b00: ADD, 2'b01: SUB, 2'b10: AND, "
            "2'b11: OR. Output 'zero' is high only when result is 0."
        ),
        "file": "benchmarks_buggy/alu.v",
    },
    {
        "name": "arbiter",
        "spec": (
            "2-request combinational fixed-priority arbiter. req0 has higher "
            "priority than req1. Only one grant (gnt0, gnt1) is asserted at a "
            "time. If both req0 and req1 are high, gnt0=1 and gnt1=0."
        ),
        "file": "benchmarks_buggy/arbiter.v",
    },
    {
        "name": "axi_lite_slave",
        "spec": (
            "Minimal AXI4-Lite slave with one 32-bit register at address 0x0. "
            "Write: AWVALID & WVALID handshake. Read: ARVALID handshake. "
            "Single outstanding transaction. Synchronous active-high reset."
        ),
        "file": "benchmarks_buggy/axi_lite_slave.v",
    },
    {
        "name": "uart_tx",
        "spec": (
            "8N1 UART transmitter (1 start bit '0', 8 data bits LSB first, "
            "1 stop bit '1'). Input tx_start triggers transmission. tx_busy "
            "is high during operation. Parameter CLKS_PER_BIT defines baud "
            "rate timing."
        ),
        "file": "benchmarks_buggy/uart_tx.v",
    },
    {
        "name": "fifo",
        "spec": (
            "Synchronous FIFO with parameters WIDTH and DEPTH. Write when "
            "wr_en && !full. Read when rd_en && !empty. Flags: empty is high "
            "when wr_ptr == rd_ptr; full is high when count equals DEPTH."
        ),
        "file": "benchmarks_buggy/fifo.v",
    },
]


def _safe_clean_workdirs(name: str):
    """Remove only the SBY workdirs for a specific project."""
    for suffix in ("prove", "cover"):
        path = os.path.join("benchmarks", f"{name}_{suffix}")
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def _log_row(project: str, status: str, iterations: int, duration: float, vacuous: str):
    file_exists = os.path.isfile(RESULTS_FILE) and os.stat(RESULTS_FILE).st_size > 0
    with open(RESULTS_FILE, "a") as f:
        if not file_exists:
            f.write("Project,Status,Iterations,Time(s),Vacuous\n")
        f.write(f"{project},{status},{iterations},{duration:.2f},{vacuous}\n")


if __name__ == "__main__":
    os.makedirs("benchmarks", exist_ok=True)
    print(f"Starting evaluation. ABLATION_MODE={ABLATION_MODE}")

    if os.path.exists(RESULTS_FILE):
        os.remove(RESULTS_FILE)

    for test in BENCHMARKS:
        _safe_clean_workdirs(test["name"])

        try:
            with open(test["file"], "r") as f:
                code = f.read()
        except FileNotFoundError:
            print(f"[Bench] SKIP {test['name']}: source file missing.")
            _log_row(test["name"], "MISSING_INPUT", 0, 0.0, "NO")
            continue

        print(f"\n[Bench] Running {test['name']}")
        start = time.time()
        try:
            result = run_repair_system(test["name"], test["spec"], code)
        except Exception as e:
            duration = time.time() - start
            print(f"[Bench] CRASH on {test['name']}: {e}")
            _log_row(test["name"], "CRASH", 0, duration, "NO")
            continue

        duration = time.time() - start
        status = result.get("verification_status", "UNKNOWN")
        iters = result.get("iteration_count", 0)
        vacuity = "YES" if status == "FAIL_VACUOUS" else "NO"

        _log_row(test["name"], status, iters, duration, vacuity)
        print(
            f"[Bench] {test['name']}: status={status} iters={iters} "
            f"time={duration:.2f}s vacuous={vacuity}"
        )

    print(f"\nDone. Results -> {RESULTS_FILE}")
