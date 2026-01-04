from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")

def get_saturday_friday_week_info(now: datetime | None = None) -> tuple[int, int, datetime, datetime]:
    """
    Calculate week number (1-52), year, week start (Saturday), and week end (Friday)
    for Saturday-Friday weekly cycles.
    
    Returns:
        tuple: (week_number, year, week_start, week_end)
    """
    if now is None:
        now = datetime.now(IST)
    else:
        try:
            now = now.astimezone(IST)
        except Exception:
            now = datetime.now(IST)
    
    # Find the Saturday that starts the current week
    # weekday(): Monday=0, Tuesday=1, ..., Saturday=5, Sunday=6
    days_since_saturday = (now.weekday() + 2) % 7  # Convert to Saturday=0 system
    week_start = (now - timedelta(days=days_since_saturday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    
    # Week end is Friday of the same week
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    
    # Calculate week number based on the year
    year = week_start.year
    
    # Find the first Saturday of the year
    jan_1 = datetime(year, 1, 1, tzinfo=IST)
    days_to_first_saturday = (5 - jan_1.weekday()) % 7  # Saturday is 5 in Monday=0 system
    first_saturday = jan_1 + timedelta(days=days_to_first_saturday)
    
    # Calculate week number (1-52)
    days_diff = (week_start - first_saturday).days
    week_number = max(1, (days_diff // 7) + 1)
    
    # Ensure week number doesn't exceed 52
    week_number = min(52, week_number)
    
    return week_number, year, week_start, week_end

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
