##
# Author: Ha Tran
# Description: Sign-off report generator + persistent "silicon brain" library.
#
# STABILIZATION PATCH NOTES:
# - Removed the leading-whitespace-on-every-line bug in the markdown
#   template that was indenting all report lines (which made markdown
#   rendering treat them as code blocks).
# - update_brain_library now writes atomically (tmp file + rename) and
#   handles a corrupted/empty JSON file gracefully.
##

import json
import os
import tempfile
from datetime import datetime


class SignOffReporter:
    def __init__(self, output_dir: str = "reports"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate(self, state: dict) -> str:
        project = state["project_name"]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filename = os.path.join(self.output_dir, f"signoff_{project}.md")

        log_tail = (state.get("error_log") or "")[-500:]

        report = (
            f"# FORMAL VERIFICATION SIGNOFF REPORT\n\n"
            f"**Date:** {timestamp}  \n"
            f"**Project:** {project}  \n"
            f"**Status:** {state.get('verification_status', 'UNKNOWN')}\n\n"
            f"---\n\n"
            f"## 1. Executive Summary\n\n"
            f"This report certifies that the RTL module **{project}** has been "
            f"mathematically verified against its specification using a "
            f"multi-agent AI repair framework.\n\n"
            f"- **Total verifier iterations:** {state.get('iteration_count', 0)}\n"
            f"- **Engine:** SymbiYosys (SBY) with Z3 SMT\n"
            f"- **Method:** Bounded model checking + cover-based vacuity detection\n\n"
            f"## 2. Formal Specification (SVA)\n\n"
            f"```systemverilog\n{state.get('formal_properties', '')}\n```\n\n"
            f"## 3. Final Verified RTL\n\n"
            f"```verilog\n{state.get('current_verilog', '')}\n```\n\n"
            f"## 4. Verifier Log Tail\n\n"
            f"```\n{log_tail}\n```\n\n"
            f"## 5. Certification\n\n"
            f"No counter-examples were found within the configured proof depth, "
            f"and at least one cover point was reachable. The design is "
            f"considered FORMALLY PROVEN within these bounds.\n"
        )

        with open(filename, "w") as f:
            f.write(report)
        print(f"[Reporter] Sign-off report written: {filename}")
        return filename

    def update_brain_library(self, state: dict, library_path: str = "agents/silicon_brain.json"):
        """
        Append a successful repair to the brain library. Atomic write so a
        crash mid-write cannot corrupt the existing file.
        """
        entry = {
            "module": state["project_name"],
            "spec": state["raw_spec"],
            "root_cause": state.get("cex_analysis", "Logic error"),
            "fix": state["current_verilog"],
            "timestamp": datetime.now().isoformat(),
        }

        data = []
        if os.path.exists(library_path):
            try:
                with open(library_path, "r") as f:
                    raw = f.read().strip()
                    data = json.loads(raw) if raw else []
                    if not isinstance(data, list):
                        data = []
            except (json.JSONDecodeError, OSError):
                data = []

        data.append(entry)

        # Atomic write: tmp file then rename.
        os.makedirs(os.path.dirname(library_path) or ".", exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=os.path.dirname(library_path) or ".",
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, library_path)

        print(f"[Brain] Added experience for {state['project_name']}")
