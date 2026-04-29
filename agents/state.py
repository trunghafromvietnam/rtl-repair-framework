##
# Date: Feb 24, 2026
# Author: Ha Tran
# Description: This script is "MEDICAL RECORDS" of the project. All information about fixing process are here.
##

from typing import TypedDict, Annotated, Optional, List
import operator

class AgentState(TypedDict):
    # Inputs
    project_name: str
    raw_spec: str
    buggy_verilog: str
    formal_properties: str
    design_contract: dict

    # Current Work
    current_verilog: str

    # Verifying Results
    verification_status: Optional[str]
    error_log: Optional[str]
    cex_data: Optional[dict]
    cex_analysis: str

    # Tracking
    fix_history: Annotated[List[str], operator.add]
    reviewer_feedback: str
    iteration_count: Annotated[int, operator.add]
