"""Explicitly migrated PostgreSQL execution and outbox storage."""

from .execution import ExecutionOperations
from .outbox import OutboxOperations


class PostgresStore(ExecutionOperations, OutboxOperations):
    """Owned async durable storage; migration is an explicit operator action.

    Example::

        store = await PostgresStore.open(dsn)
        try:
            await store.migrate()  # Operator-controlled deployment step.
            accepted = await store.accept(submission)
        finally:
            await store.aclose()
    """


__all__ = ["PostgresStore"]
