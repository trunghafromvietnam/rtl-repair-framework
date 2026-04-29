##
# Date: Jan 20, 2026
# Author: Ha Tran
# Description: This script reads file .vcd from verifier.py (SBY commander) and turns it into readable format before
# sending to main.py.
##

from vcdvcd import VCDVCD
import os

class CEXTranslator:
    def __init__(self, vcd_path):
        self.vcd_path = vcd_path

    def translate(self):
        if not os.path.exists(self.vcd_path):
            return "Error: File trace.vcd not found."

        # Load file VCD
        vcd = VCDVCD(self.vcd_path)
        
        # Get signals' list
        signal_names = vcd.signals
        
        # Collect all times that have change
        all_times = set()
        for name in signal_names:
            for t, v in vcd[name].tv:
                all_times.add(t)
        
        sorted_times = sorted(list(all_times))

        report = "---***** ERROR TIMELINE REPORT (CEX) *****---\n\n"
        
        # Print column's title
        headers = [name.split('.')[-1] for name in sorted(signal_names)]
        report += f"{'Time':<10} | " + " | ".join(f"{h:<8}" for h in headers) + "\n"
        report += "-" * (13 + len(headers) * 11) + "\n"

        # Traverse through times to get values
        for t in sorted_times:
            row = f"{t:<10} | "
            for name in sorted(signal_names):
                val = vcd[name][t]
                row += f"{str(val):<8} | "
            report += row + "\n"
            
        return {
            "timeline": sorted_times,
            "signals": {
                name.split('.')[-1]: vcd[name].tv
                for name in signal_names
            }
        }

if __name__ == "__main__":
    path = "benchmarks/counter/engine_0/trace.vcd"
    translator = CEXTranslator(path)
    print(translator.translate())