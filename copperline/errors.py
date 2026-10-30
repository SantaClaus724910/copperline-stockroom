"""Errors that callers can handle separately from storage or programming faults."""


class StockroomError(Exception):
    """Base class for expected operator errors."""


class ValidationError(StockroomError):
    """Malformed or out-of-range input."""


class NotFound(StockroomError):
    """Unknown part or storage location."""


class Conflict(StockroomError):
    """Request contradicts current inventory state or an accepted reference."""
