"""oracle — agentic AI root-cause diagnosis for FCV Bot Gauntlet findings.

See README.md for usage and AGENT.md for how/when an agent should invoke this.
"""

from .oracle import (
    diagnose,
    check_budget,
    spend_summary,
    estimate_cost,
    format_budget_report,
    BudgetStatus,
    DiagnosisResult,
    OracleError,
    OracleConfirmationRequired,
    OracleBudgetExceeded,
)

__all__ = [
    "diagnose",
    "check_budget",
    "spend_summary",
    "estimate_cost",
    "format_budget_report",
    "BudgetStatus",
    "DiagnosisResult",
    "OracleError",
    "OracleConfirmationRequired",
    "OracleBudgetExceeded",
]
