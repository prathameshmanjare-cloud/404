from __future__ import annotations


class ModelError(Exception):
    """A provider call failed. retryable says whether trying again can help."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None,
                 retryable: bool = True) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.retryable = retryable


class ModelUnavailable(Exception):
    """Every model for a role failed (after retries, breaker and fallback)."""


class CassetteMissing(Exception):
    """Replay mode found no recorded response. A setup problem, never treated as an outage."""


class InvalidOutput(ModelUnavailable):
    """A model answered, but not with usable output (for example Python that does not parse, twice)."""
