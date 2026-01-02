from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")

def get_current_week_start(now: datetime | None = None) -> datetime:
    """
    Compute the current week start datetime using
    Friday 21:31 IST as the week boundary.

    Returns:
        timezone-aware datetime in IST
    """
    if now is None:
        now = datetime.now(IST)
    else:
        try:
            now = now.astimezone(IST)
        except Exception:
            now = datetime.now(IST)

    # weekday(): Monday=0 ... Sunday=6
    # Friday = 4
    days_since_friday = (now.weekday() - 4) % 7

    candidate = now - timedelta(days=days_since_friday)
    candidate = candidate.replace(
        hour=21,
        minute=31,
        second=0,
        microsecond=0,
    )

    # If candidate is in the future, go back a week
    if now < candidate:
        candidate -= timedelta(days=7)

    return candidate
