from dataclasses import dataclass
from typing import List, Optional, Dict, Union

# ===== Timing Model =====
@dataclass
class Timing:
    kind: str  # "combinational" | "sequential"
    delay: int = 0  # for sequential logic
    clk: str = "clk"
    rst: str = "rst"

# ===== Base Property =====
@dataclass
class PropertyIR:
    name: str
    type: str
    timing: Timing

# ===== Specific Property Types =====

@dataclass
class MutualExclusion(PropertyIR):
    signals: List[str]  # e.g., ["gnt0", "gnt1"]

@dataclass
class Implication(PropertyIR):
    antecedent: str
    consequent: str

@dataclass
class Equality(PropertyIR):
    left: str
    right: str

@dataclass
class ResetInvariant(PropertyIR):
    signal: str
    expected_value: str

@dataclass
class HoldProperty(PropertyIR):
    signal: str

@dataclass
class TransitionProperty(PropertyIR):
    condition: str
    next_state: str

@dataclass
class HandshakeProperty(PropertyIR):
    valid: str
    ready: str