class FinOpsError(Exception):
    """Tool-level error with a message written to be read by an LLM agent.

    Raise this (not raw SDK exceptions) from provider methods so tool callers
    get an actionable message instead of a stack trace.
    """
