class NonRetryableError(Exception):
    """Trade failure that retrying cannot fix (empty balance, missing agent key…)."""
