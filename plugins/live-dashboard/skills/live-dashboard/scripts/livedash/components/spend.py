from __future__ import annotations

from datetime import timedelta

from livedash import Context, Line, Series, component


@component("aws-spend", "Cloud spend per day", question="What did the account spend each day, against its target?", reads=["aws ce get-cost-and-usage"], every="15m", timeout="60s")
def aws_spend(ctx: Context, *, profile: str, days: int = 14, target_per_day: float | None = None, target_label: str = "target") -> Series:
    """Cost Explorer's unblended cost per UTC day for the account behind `profile`, over the last `days` full days, as
    bars; a day over `target_per_day` reads warn. Today is left out, since Cost Explorer fills it in over the next day."""
    today = ctx.now.date()
    start = today - timedelta(days=days)
    found = ctx.json(["aws", "--profile", profile, "ce", "get-cost-and-usage", "--time-period", f"Start={start},End={today}", "--granularity", "DAILY", "--metrics", "UnblendedCost", "--output", "json"])
    points = []
    for day in found["ResultsByTime"]:
        amount = round(float(day["Total"]["UnblendedCost"]["Amount"]))
        over = target_per_day is not None and amount > target_per_day
        points.append([day["TimePeriod"]["Start"], amount, {"tone": "warn" if over else "ok", "label": f"${amount:,} on {day['TimePeriod']['Start']}"}])
    return Series([Line("spend", points, "bar")], unit="$", thresholds={target_label: target_per_day} if target_per_day is not None else {}, note="Cost Explorer lags about a day, so today is left out.")
