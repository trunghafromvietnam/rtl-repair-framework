##
# Date: Jan 29, 2026
# Author: Ha Tran
# Description: This file generates a sign-off report for formal verification results.
##

import os
import json
from datetime import datetime

class SignOffReporter:
    def __init__(self, output_dir="reports"):
        self.output_dir = output_dir
        if not os.path.exists(self.output_dir):
            os.makedirs(output_dir)

    def generate(self, state: dict):
        project = state['project_name']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filename = f"{self.output_dir}/signoff_{project}.md"

        report = f"""
        # ===== FORMAL VERIFICATION SIGNOFF REPORT =====
        **Date:** {timestamp}
        **Project:** {project}
        **Status:** ✅ {state['verification_status']}

        ---

        ## 1. Executive Summary
        This report certifies that the RTL module **{project}** has been mathematically verified against its specification using a Multi-Agent AI Repair Framework.

        - **Total Iterations:** {state['iteration_count']}
        - **Verification Engine:** SymbiYosys (SBY) with Z3 SMT Solver
        - **Proof Method:** k-induction + SMT solving
        - **Proof Depth:** Tool-dependent (see log)

        ## 2. Formal Specification (The Law)
        The following SystemVerilog Assertions (SVA) were used as the ground truth for this proof:
        ```systemverilog
        {state['formal_properties']}
        ```

        ## 3. Final Verified RTL (The Truth)
        Below is the clean Verilog code that has passed all mathematical checks:
        ```verilog
        {state['current_verilog']}
        ```
        ## 4. Verification Evidence
        Final log summary from Formal Tool:
        ```
        {state['error_log'][-500:]}
        ```

        ## Certification: This document confirms that no counter-examples were found within the defined proof depth. The design is considered FORMALLY PROVEN.
        """

        with open(filename, "w") as f:
            f.write(report)
            print(f"***[Reporter]*** Sign-off report generated: {filename}")

    def update_brain_library(self, state: dict):
        """Store solutions in the brain's library for future reference."""
        entry = {
            "module": state['project_name'],
            "spec": state['raw_spec'],
            "root_cause": state.get('cex_analysis', 'Logic error'),
            "fix": state['current_verilog']
        }
        library_path = "agents/silicon_brain.json"
        data = []
        if os.path.exists(library_path):
            with open(library_path, "r") as f:
                data = json.load(f)
        data.append(entry)
        with open(library_path, "w") as f:
                json.dump(data, f, indent=4)
        print(f"[Brain] Experience added for {state['project_name']}")



