import pytest

from app.notifications.delivery import NotificationDeliveryError, require_delivery
from app.tasks import analyze_new, weekly_report


def test_require_delivery_passes_a_sent_result_through():
    result = {"sent": True, "deals": 3}
    assert require_delivery(result, "Tagesbericht") is result


def test_require_delivery_raises_with_an_actionable_message():
    with pytest.raises(NotificationDeliveryError, match="Telegram"):
        require_delivery({"sent": False}, "Wochenreport")


def _run_sync(coro):
    import asyncio

    return asyncio.run(coro)


def test_weekly_report_task_fails_when_telegram_did_not_deliver(monkeypatch):
    # Produktionsbefund 2026-09-18: Wochen- und Tagesbericht meldeten einen
    # Monat lang {'sent': False}, der Heartbeat stand trotzdem auf success —
    # der Task war ja fehlerfrei gelaufen. Kein Wächter und kein Dashboard
    # konnte den toten Benachrichtigungskanal bemerken.
    async def fake_report():
        return {"sent": False, "prices_7d": 1770, "problems": []}

    monkeypatch.setattr(weekly_report, "_report_async", fake_report)
    monkeypatch.setattr(weekly_report, "_run_async", _run_sync)

    with pytest.raises(NotificationDeliveryError):
        weekly_report.send_weekly_report_task()


def test_daily_summary_task_fails_when_telegram_did_not_deliver(monkeypatch):
    async def fake_summary():
        return {"sent": False, "deals": 0, "go_deals": 0}

    monkeypatch.setattr(analyze_new, "_send_summary_async", fake_summary)
    monkeypatch.setattr(analyze_new, "_run_async", _run_sync)

    with pytest.raises(NotificationDeliveryError):
        analyze_new.send_daily_summary_task()


def test_delivered_report_still_succeeds(monkeypatch):
    async def fake_report():
        return {"sent": True, "prices_7d": 1770, "problems": []}

    monkeypatch.setattr(weekly_report, "_report_async", fake_report)
    monkeypatch.setattr(weekly_report, "_run_async", _run_sync)

    assert weekly_report.send_weekly_report_task()["sent"] is True
