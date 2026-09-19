"""Process-local synchronous event bus with SQLite outbox for cross-process delivery.

Emitted events are persisted into ``hub_events`` (outbox pattern) in the same
transaction as the business change, then dispatched synchronously in-process.
The scheduler process's ``drain_events`` task redelivers events whose dispatch
has not been confirmed, providing at-least-once cross-process delivery with a
≤30s latency bound (identical to today's webhook/pipeline polling semantics).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hub_server.kernel.protocol import EventBinding, EventEnvelope

_LOGGER = logging.getLogger(__name__)


class EventBus:
    """In-process synchronous event bus backed by a SQLite outbox.

    ``emit`` never blocks on subscribers and never rolls back the business
    transaction: handler errors are logged, business code is unaffected.
    Cross-process handlers receive events via the scheduler's ``drain`` sweep.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._bindings: dict[str, list[EventBinding]] = {}

    def subscribe(self, binding: EventBinding, plugin: str) -> None:
        self._bindings.setdefault(binding.event_type, []).append(binding)

    def emit(
        self,
        event_type: str,
        payload: dict[str, object],
        *,
        session: Session | None = None,
        persist: bool = True,
    ) -> None:
        """Emit one event; persist to the outbox and dispatch in-process."""
        envelope = EventEnvelope(event_type=event_type, payload=payload)
        if persist:
            self._persist_outbox(envelope, session)
        self._dispatch(envelope)

    def drain(self, session: Session) -> int:
        """Redeliver persisted events whose dispatch has not been confirmed."""
        from hub_server.models import HubEvent

        pending = session.scalars(
            select(HubEvent).where(HubEvent.dispatched_at.is_(None)).limit(100)
        ).all()
        for row in pending:
            envelope = EventEnvelope(
                event_type=row.event_type, payload=row.payload_json or {}
            )
            self._dispatch(envelope)
            row.dispatched_at = datetime.now(UTC)
        session.commit()
        return len(pending)

    def _persist_outbox(
        self, envelope: EventEnvelope, session: Session | None
    ) -> None:
        from hub_server.models import HubEvent

        row = HubEvent(
            event_type=envelope.event_type, payload_json=envelope.payload
        )
        if session is not None:
            session.add(row)
        else:
            with self._session_factory() as s:
                s.add(row)
                s.commit()

    def _dispatch(self, envelope: EventEnvelope) -> None:
        for binding in self._bindings.get(envelope.event_type, ()):
            try:
                binding.handler(envelope)
            except Exception:
                _LOGGER.exception(
                    "event handler for %s failed; business flow continues",
                    envelope.event_type,
                )
