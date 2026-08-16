from app.notifications.telegram_bot import format_weekly_report


def test_report_flags_dead_pipeline_on_zero_prices():
    text = format_weekly_report(
        {"prices_7d": 0, "offers_7d": 0, "go_7d": 0, "watchlist_active": 5, "problems": []}
    )
    assert "⚠️" in text


def test_report_contains_core_numbers_and_problem_names():
    text = format_weekly_report(
        {
            "prices_7d": 123,
            "offers_7d": 17,
            "go_7d": 4,
            "watchlist_active": 30,
            "problems": ["app.tasks.scrape_daily.scrape_all_watched_sets"],
        }
    )
    for token in ("123", "17", "4", "30", "scrape_all_watched_sets"):
        assert token in text
