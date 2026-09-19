"""Make a notification that did not arrive count as a failed task.

The Telegram helpers return False instead of raising when a message could not
be sent — missing credentials, a rejected token, an unknown chat. The Celery
tasks passed that False along in their result and still finished normally, so
the heartbeat recorded "success". For a month in 2026 the weekly report and the
daily summary reached nobody while the pipeline-health watchdog showed green:
the one channel meant to report a dead pipeline was itself dead, silently.

A task whose whole purpose is to deliver a message has failed if the message
did not arrive. Raising turns that into a heartbeat error, which the watchdog
evaluates and the dashboard shows — the only signal that still works when
Telegram does not.
"""


class NotificationDeliveryError(RuntimeError):
    """A notification task ran but its message did not reach the recipient."""


def require_delivery(result: dict, what: str) -> dict:
    """Return the task result, or raise if its message was not delivered."""
    if not result.get("sent"):
        raise NotificationDeliveryError(
            f"{what} nicht zugestellt — Telegram-Zugangsdaten in den Settings pruefen "
            "(Bot-Token gueltig? Chat-ID numerisch? Bot einmal angeschrieben?)"
        )
    return result
