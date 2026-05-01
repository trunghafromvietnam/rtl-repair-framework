##
# Author: Ha Tran
# Description: Thin wrapper around SymbiYosys (`sby`) for prove + cover runs.
#
# STABILIZATION PATCH NOTES:
# - No more chdir gymnastics: invoke sby with cwd= argument so the parent
#   process working directory is never mutated.
# - Workdir is wiped before each run so a stale trace.vcd from a previous
#   iteration cannot be mistaken for the current one.
# - Cover hit counting uses a more robust regex and only counts unique
#   cover statements that were actually reached.
# - run_full_verification documents the precise definition of VACUOUS_PASS
#   used by the orchestrator.
##

import os
import re
import shutil
import subprocess


class VerifierBridge:
    def __init__(self, work_dir: str = "benchmarks"):
        self.work_dir = work_dir

    # ---------- single-mode invocation ----------

    def run_sby(self, project_name: str, mode: str = "prove") -> dict:
        """
        Run `sby` for one mode (prove | cover) on a single project.
        Returns a dict with 'status' or 'covers' depending on mode, and 'log'.
        """
        print(f"[Verifier] Running {mode.upper()} for {project_name}.sby...")

        sby_file = f"{project_name}_{mode}.sby"
        out_dir = f"{project_name}_{mode}"

        # Clean prior workdir so we never pick up stale traces.
        full_out = os.path.join(self.work_dir, out_dir)
        if os.path.isdir(full_out):
            shutil.rmtree(full_out, ignore_errors=True)

        try:
            process = subprocess.run(
                ["sby", "-f", sby_file, "-d", out_dir],
                cwd=self.work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,  # absolute ceiling per mode
            )
        except subprocess.TimeoutExpired as e:
            return {
                "status": "ERROR",
                "log": f"sby timeout after {e.timeout}s",
            }
        except FileNotFoundError:
            return {
                "status": "ERROR",
                "log": "sby executable not found in PATH",
            }

        log = (process.stdout or "") + (process.stderr or "")

        if mode == "prove":
            status = "PASS" if process.returncode == 0 else "FAIL"
            return {"status": status, "log": log}

        # cover mode
        # Count unique reached covers. SBY logs lines like
        # "Reached cover statement at <name> in step N".
        reached = set(re.findall(r"reached cover statement at (\S+)", log.lower()))
        return {"covers": len(reached), "log": log}

    # ---------- full prove + cover run ----------

    def run_full_verification(self, project_name: str) -> dict:
        """
        Run prove first; if prove succeeds but no cover statement is
        reachable, classify as VACUOUS_PASS. This catches the classic
        "constraints made the design unreachable" failure mode.
        """
        prove = self.run_sby(project_name, "prove")

        if prove["status"] != "PASS":
            return prove  # FAIL or ERROR — no need to run cover

        cover = self.run_sby(project_name, "cover")
        if cover.get("covers", 0) == 0:
            return {
                "status": "VACUOUS_PASS",
                "log": prove["log"] + "\n--- COVER LOG ---\n" + cover.get("log", ""),
            }

        return {
            "status": "PASS",
            "log": prove["log"],
        }


if __name__ == "__main__":
    print("Verifier bridge ready.")
