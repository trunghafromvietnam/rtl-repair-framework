#!/usr/bin/env python3
##
# Phase B.4 — Multi-run statistical baseline.
#
# Runs the full repair pipeline N times per benchmark to capture LLM
# nondeterminism. Output is a flat CSV with one row per (benchmark, run)
# pair, suitable for downstream variance analysis.
#
# Usage:
#   python run_n_times.py                  # default: 5 runs per bench
#   python run_n_times.py --runs 3         # custom count
#   python run_n_times.py --benchmarks alu # subset
#
# Output:
#   logs/sprint1/multi_run_baseline.csv
#     columns: benchmark, run_id, status, iterations, time_seconds,
#              vacuous, error_message
##

import argparse
import csv
import os
import sys
import time
from datetime import datetime

# Default benchmarks (matches run_benchmarks.py)
DEFAULT_BENCHMARKS = [
    ("counter", "Parameterized synchronous up-counter (default WIDTH=8). On rising edge of clk: if rst is high, count is set to 0; else if en is high, count increments by 1; otherwise count holds. Wraps around on overflow."),
    ("alu", "Combinational 32-bit ALU with 4 operations selected by 2-bit op: 00=ADD (a+b), 01=SUB (a-b), 10=AND (a&b), 11=OR (a|b). Output zero is high when result equals 0."),
    ("arbiter", "2-input round-robin arbiter. On each rising edge of clk, if both req[0] and req[1] are high, alternate which gets gnt; if only one is high, that one gets gnt; if neither, both gnts are 0. After reset, gnt[0] has priority on the first conflict."),
    ("axi_lite_slave", "AXI4-Lite slave with single 32-bit register at address 0. Implements write channel (awvalid/awready/awaddr/wvalid/wready/wdata/bvalid/bready) and read channel (arvalid/arready/araddr/rvalid/rready/rdata). On reset, all ready/valid outputs go low. Write transactions: awvalid AND wvalid both high triggers data write. Read transactions: arvalid high reads stored register."),
    ("uart_tx", "UART transmitter with 16-bit baud divider. Inputs: clk, rst, tx_start, data_in[7:0]. Outputs: tx (serial), busy. On tx_start (when not busy), transmit start bit (0), 8 data bits LSB first, stop bit (1). busy is high during transmission. tx idles high when not transmitting."),
    ("fifo", "Synchronous FIFO with parameter DEPTH=8 and WIDTH=8. Inputs: clk, rst, wr_en, rd_en, data_in. Outputs: data_out, full, empty. Reset clears all entries. wr_en pushes data_in if not full. rd_en pops to data_out if not empty. Concurrent wr_en + rd_en allowed."),
]


def run_one(project_name: str, spec: str, run_id: int) -> dict:
    """
    Run the repair pipeline once. Returns a dict with all metrics.
    Imports lazily so import errors don't kill the whole sweep.
    """
    from main_graph import run_repair_system

    rtl_path = f"benchmarks_buggy/{project_name}.v"
    if not os.path.exists(rtl_path):
        return {
            "benchmark": project_name,
            "run_id": run_id,
            "status": "ERROR",
            "iterations": 0,
            "time_seconds": 0.0,
            "vacuous": "NO",
            "error_message": f"buggy RTL not found at {rtl_path}",
        }

    with open(rtl_path) as f:
        buggy_rtl = f.read()

    t0 = time.time()
    try:
        result = run_repair_system(project_name, spec, buggy_rtl)
        elapsed = time.time() - t0
        status = result.get("verification_status", "UNKNOWN")
        iters = result.get("iteration_count", 0)
        vacuous = "YES" if status == "FAIL_VACUOUS" else "NO"
        # Normalize FAIL_VACUOUS -> FAIL for status column
        status_norm = "FAIL" if status == "FAIL_VACUOUS" else status

        return {
            "benchmark": project_name,
            "run_id": run_id,
            "status": status_norm,
            "iterations": iters,
            "time_seconds": round(elapsed, 2),
            "vacuous": vacuous,
            "error_message": "",
        }
    except Exception as e:
        elapsed = time.time() - t0
        return {
            "benchmark": project_name,
            "run_id": run_id,
            "status": "CRASH",
            "iterations": 0,
            "time_seconds": round(elapsed, 2),
            "vacuous": "NO",
            "error_message": str(e)[:200],
        }


def cleanup_workdirs():
    """Remove SBY workdirs and benchmark outputs between runs."""
    import shutil
    import glob

    for pattern in ["benchmarks/*_prove", "benchmarks/*_cover"]:
        for path in glob.glob(pattern):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)

    for pattern in [
        "benchmarks/*.sby",
        "benchmarks/*_props.sv",
        "benchmarks/counter.v",
        "benchmarks/alu.v",
        "benchmarks/arbiter.v",
        "benchmarks/axi_lite_slave.v",
        "benchmarks/uart_tx.v",
        "benchmarks/fifo.v",
    ]:
        for path in glob.glob(pattern):
            if os.path.isfile(path):
                os.remove(path)


def main():
    parser = argparse.ArgumentParser(description="Phase B.4 multi-run baseline")
    parser.add_argument("--runs", type=int, default=5,
                        help="Runs per benchmark (default: 5)")
    parser.add_argument("--benchmarks", type=str, default=None,
                        help="Comma-separated subset (default: all)")
    parser.add_argument("--output", type=str,
                        default="logs/sprint1/multi_run_baseline.csv",
                        help="Output CSV path")
    args = parser.parse_args()

    # Filter benchmarks if requested.
    if args.benchmarks:
        wanted = set(args.benchmarks.split(","))
        benchmarks = [(n, s) for n, s in DEFAULT_BENCHMARKS if n in wanted]
    else:
        benchmarks = DEFAULT_BENCHMARKS

    if not benchmarks:
        print("[ERROR] No benchmarks selected.")
        return 2

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    total_runs = len(benchmarks) * args.runs
    print(f"=== Phase B.4 Multi-Run Baseline ===")
    print(f"Benchmarks: {[n for n, _ in benchmarks]}")
    print(f"Runs per benchmark: {args.runs}")
    print(f"Total runs: {total_runs}")
    print(f"Output: {args.output}")
    print(f"Started: {datetime.now().isoformat(timespec='seconds')}")
    print()

    # Open CSV for incremental writes (so we don't lose data on crash).
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "benchmark", "run_id", "status",
            "iterations", "time_seconds", "vacuous", "error_message",
        ])
        writer.writeheader()
        f.flush()

        run_counter = 0
        for project_name, spec in benchmarks:
            for run_id in range(1, args.runs + 1):
                run_counter += 1
                print(f"[{run_counter}/{total_runs}] {project_name} run {run_id}/{args.runs}...")

                cleanup_workdirs()

                row = run_one(project_name, spec, run_id)

                writer.writerow(row)
                f.flush()

                print(f"  -> {row['status']} iters={row['iterations']} "
                      f"time={row['time_seconds']}s vacuous={row['vacuous']}")
                if row.get("error_message"):
                    print(f"     err: {row['error_message']}")
                print()

    print(f"Done. Results -> {args.output}")
    print(f"Finished: {datetime.now().isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
