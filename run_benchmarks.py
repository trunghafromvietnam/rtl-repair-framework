import time
import os
from main_graph import run_repair_system, ABLATION_MODE

RESULTS_FILE = "ablation_mode_results.csv" if ABLATION_MODE else "full_system_results.csv"

BENCHMARKS = [
    {
        "name": "counter",
        "spec": "Parameterized synchronous up-counter (default WIDTH=8). Rising clk: If rst is high, count=0. Else if en is high, count increments by 1. Otherwise, count must hold its value. Wraps around on overflow.",
        "file": "benchmarks_buggy/counter.v"
    },
    {
        "name": "alu",
        "spec": "Combinational ALU. Opcode 2'b00: ADD, 2'b01: SUB, 2'b10: AND, 2'b11: OR. Output 'zero' is high only when result is 0.",
        "file": "benchmarks_buggy/alu.v"
    },
    {
        "name": "arbiter",
        "spec": "2-request combinational fixed-priority arbiter. Policy: req0 has higher priority than req1. Only one grant (gnt0, gnt1) asserted at a time. If both req0 and req1 are high, gnt0=1 and gnt1=0.",
        "file": "benchmarks_buggy/arbiter.v"
    },
    {
        "name": "axi_lite_slave",
        "spec": "Minimal AXI4-Lite slave with one 32-bit register at address 0x0. Write: AWVALID & WVALID handshake. Read: ARVALID handshake. Single outstanding transaction. Synchronous active-high reset.",
        "file": "benchmarks_buggy/axi_lite_slave.v"
    },
    {
        "name": "uart_tx",
        "spec": "8N1 UART transmitter (1 start bit '0', 8 data bits LSB first, 1 stop bit '1'). Input tx_start triggers transmission. tx_busy is high during operation. Parameter CLKS_PER_BIT defines baud rate timing.",
        "file": "benchmarks_buggy/uart_tx.v"
    },
    {
        "name": "fifo",
        "spec": "Synchronous FIFO with parameters WIDTH and DEPTH. Write when wr_en && !full. Read when rd_en && !empty. Flags: empty is high when wr_ptr == rd_ptr; full is high when count equals DEPTH.",
        "file": "benchmarks_buggy/fifo.v"
    }
]

def logger(project, status, iterations, duration, vacuity_flag):
    file_exists = os.path.isfile(RESULTS_FILE)
    with open(RESULTS_FILE, "a") as f:
        if not file_exists or os.stat(RESULTS_FILE).st_size == 0:
            f.write("Project,Status,Iterations,Time(s),Vacuous\n")
        f.write(f"{project},{status},{iterations},{duration:.2f},{vacuity_flag}\n")

if __name__ == "__main__":
    if not os.path.exists("benchmarks"):
        os.makedirs("benchmarks")
    
    print(f"🚀 Starting Automated Research Evaluation (Ablation Mode: {ABLATION_MODE})...")
    
    if os.path.exists(RESULTS_FILE):
        os.remove(RESULTS_FILE)
        
    for test in BENCHMARKS:
        os.system("rm -rf benchmarks/*/ ") 
        
        start_time = time.time()
        with open(test["file"], "r") as f:
            code = f.read()
            
        print(f"\n[Bench] Testing module: {test['name']}")
        result = run_repair_system(test["name"], test["spec"], code)
        
        duration = time.time() - start_time
        status = result["verification_status"]
        iters = result["iteration_count"]
        cex_used = "YES" if not ABLATION_MODE else "NO"
        vacuity = "YES" if result["verification_status"] == "FAIL_VACUOUS" else "NO"
         
        logger(test["name"], status, iters, duration, vacuity)
        print(f"✅ Finished {test['name']} - Status: {status} - Time: {duration:.2f}s - Ablation Mode: {cex_used} - Vacuous: {vacuity}")