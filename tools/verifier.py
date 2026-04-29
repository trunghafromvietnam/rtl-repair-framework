import subprocess # Command from Python to Terminal
import os # Talk to the operating system
import re # Process String

class VerifierBridge:
    def __init__(self, work_dir="."):
        self.work_dir = work_dir

    def run_sby(self, project_name: str, mode="prove"):
        print(f"[Verifier] Running {mode.upper()} with {project_name}.sby...")

        current_dir = os.getcwd()
        try:
            os.chdir("benchmarks")

            sby_file = f"{project_name}_{mode}.sby"

            process = subprocess.run(
                ["sby", "-f", sby_file, "-d", f"{project_name}_{mode}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            log = process.stdout + process.stderr

            if mode == "prove":
                status = "PASS" if process.returncode == 0 else "FAIL"
                return {"status": status, "log": log}

            elif mode == "cover":
                matches = re.findall(r"reached (\d+) cover", log.lower())
                cover_hits = sum(int(m) for m in matches) if matches else 0
                return {"covers": cover_hits, "log": log}

        except Exception as e:
            return {"status": "ERROR", "log": str(e)}

        finally:
            os.chdir(current_dir)

    def run_full_verification(self, project_name):
        prove = self.run_sby(project_name, "prove")
        cover = self.run_sby(project_name, "cover")

        if prove["status"] == "PASS" and cover.get("covers", 0) == 0:
            return {
                "status": "VACUOUS_PASS",
                "log": prove["log"]
            }

        return prove
    
if __name__ == "__main__":
    bridge = VerifierBridge()
    print("Verifier Bridge is ready to connect!")