##
# Hotfix B0.5 for tools/verifier.py
#
# Problem (false vacuity in counter benchmark, post-B0.4):
#   verifier.py reported VACUOUS_PASS even when SBY cover mode succeeded:
#       SBY [counter_cover] engine_0: Status: passed
#       SBY [counter_cover] DONE (PASS, rc=0)
#
#   Old run_sby() for cover mode used:
#       reached = set(re.findall(r"reached cover statement at (\S+)", log))
#   But SBY's cover mode log does NOT emit "reached cover statement at ...".
#   It emits "Status: passed" + returncode=0 when all cover points are
#   reachable, "Status: failed" + returncode!=0 when any cover point is
#   unreachable. The right metric is the engine's return status, not
#   substring matching of an obsolete log format.
#
# Fix:
#   Cover mode now classifies based on returncode:
#     returncode == 0   ->  cover passed   ->  covers > 0 (treat as 1)
#     returncode != 0   ->  cover failed   ->  covers == 0 (vacuity)
#
#   This restores the intended semantics of run_full_verification:
#     prove PASS + cover PASS  ->  proper PASS
#     prove PASS + cover FAIL  ->  VACUOUS_PASS
##

import os
import re
import shutil
import subprocess


class VerifierBridge:
    def __init__(self, work_dir: str = "benchmarks"):
        self.work_dir = work_dir

    def run_sby(self, project_name: str, mode: str = "prove") -> dict:
        """
        Run `sby` for one mode (prove | cover) on a single project.
        Returns a dict with status/covers and log.
        """
        print(f"[Verifier] Running {mode.upper()} for {project_name}.sby...")

        sby_file = f"{project_name}_{mode}.sby"
        out_dir = f"{project_name}_{mode}"

        # Wipe stale workdir so we never read an old trace.
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
                timeout=600,
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

        # ----- cover mode (B0.5 fix) -----
        # SBY cover mode uses returncode 0 = all covers reachable, else
        # at least one cover unreachable. We surface this as a numeric
        # cover count for backward compat with run_full_verification.
        cover_passed = (process.returncode == 0)

        # As a secondary signal, also count any "BMC failed" or
        # "Unreached cover" mentions, since rare engine paths may
        # report success even with unreachable covers.
        unreachable = len(re.findall(r"unreached cover", log, re.IGNORECASE))

        if cover_passed and unreachable == 0:
            return {"covers": 1, "log": log}
        return {"covers": 0, "log": log}

    def run_full_verification(self, project_name: str) -> dict:
        """
        Run prove first; if prove succeeds but no cover statement is
        reachable, classify as VACUOUS_PASS.
        """
        prove = self.run_sby(project_name, "prove")

        if prove["status"] != "PASS":
            return prove

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
