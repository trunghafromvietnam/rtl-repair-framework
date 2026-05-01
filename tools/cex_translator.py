##
# Date: Apr 27, 2026
# Author: Ha Tran
# Description: VCD counter-example translator. Reads SBY's trace.vcd and
# emits a structured timeline + per-signal value series for downstream
# consumption by the CEX analyzer.
#
# STABILIZATION PATCH NOTES:
# - Signal keys preserve hierarchy when leaf names collide (e.g.,
#   dut.count and props_inst.count). Previously the second one silently
#   overwrote the first.
# - The function now returns a dict with a stable schema even on missing
#   files, so callers can rely on the shape.
##

import os
from collections import Counter

from vcdvcd import VCDVCD


class CEXTranslator:
    def __init__(self, vcd_path: str):
        self.vcd_path = vcd_path

    def translate(self) -> dict:
        if not os.path.exists(self.vcd_path):
            return {
                "timeline": [],
                "signals": {},
                "error": f"trace.vcd not found at {self.vcd_path}",
            }

        vcd = VCDVCD(self.vcd_path)
        signal_names = list(vcd.signals)

        # Detect leaf-name collisions BEFORE we build the dict so we can
        # decide whether each entry needs to keep its full hierarchy.
        leaves = [name.split(".")[-1] for name in signal_names]
        leaf_counts = Counter(leaves)

        def _key_for(full: str) -> str:
            leaf = full.split(".")[-1]
            return full if leaf_counts[leaf] > 1 else leaf

        # Collect the union of all change times.
        all_times: set[int] = set()
        for name in signal_names:
            for t, _ in vcd[name].tv:
                all_times.add(t)
        sorted_times = sorted(all_times)

        # Per-signal time-value series, with collision-safe keys.
        signal_series = {
            _key_for(name): list(vcd[name].tv) for name in signal_names
        }

        return {
            "timeline": sorted_times,
            "signals": signal_series,
        }


if __name__ == "__main__":
    path = "benchmarks/counter_prove/engine_0/trace.vcd"
    print(CEXTranslator(path).translate())
