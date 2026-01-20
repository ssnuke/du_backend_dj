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
    Friday 21:30 IST (9:30 PM) as the week boundary.
    
    Week starts at Friday 9:30 PM and ends next Friday 9:29:59 PM.

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
        minute=30,
        second=0,
        microsecond=0,
    )

    # If we haven't reached Friday 9:30 PM yet, go back to previous Friday
    if now < candidate:
        candidate -= timedelta(days=7)

    return candidate


def get_week_info_friday_to_friday(now: datetime | None = None, week_number: int | None = None, year: int | None = None) -> tuple[int, int, datetime, datetime]:
    """
    Calculate week number (1-52), year, week start, and week end
    for Friday 9:30 PM IST to next Friday 11:45 PM IST weekly cycles.
    
    If week_number and year are provided, returns that specific week's bounds.
    Otherwise, calculates for current time.
    
    Args:
        now: Current datetime (optional, defaults to now in IST)
        week_number: Specific week number to calculate (1-52, optional)
        year: Year for the specific week (optional)
    
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
    
    # If specific week requested, calculate bounds for that week
    if week_number is not None and year is not None:
        # Find first Friday of the year at 9:30 PM
        jan_1 = datetime(year, 1, 1, tzinfo=IST)
        # Friday = 4 in weekday()
        days_to_first_friday = (4 - jan_1.weekday()) % 7
        first_friday = jan_1 + timedelta(days=days_to_first_friday)
        first_friday = first_friday.replace(hour=21, minute=30, second=0, microsecond=0)
        
        # Calculate the start of the requested week
        # Week 1 starts at first_friday, Week 2 at first_friday + 7 days, etc.
        week_start = first_friday + timedelta(weeks=(week_number - 1))
        # Next Friday 11:45:59 PM (7 days + 2 hours + 15 minutes + 59 seconds)
        week_end = week_start + timedelta(days=7, hours=2, minutes=15, seconds=59)
        
        return week_number, year, week_start, week_end
    
    # Calculate current week
    # Find the most recent Friday 9:30 PM
    # weekday(): Monday=0, Tuesday=1, ..., Friday=4, Saturday=5, Sunday=6
    days_since_friday = (now.weekday() - 4) % 7
    
    # Get to last Friday
    last_friday = now - timedelta(days=days_since_friday)
    last_friday = last_friday.replace(hour=21, minute=30, second=0, microsecond=0)
    
    # If current time is before Friday 9:30 PM this week, go back one more week
    if now < last_friday:
        last_friday -= timedelta(days=7)
    
    week_start = last_friday
    # Next Friday 11:45:59 PM (7 days + 2 hours + 15 minutes + 59 seconds from start)
    week_end = week_start + timedelta(days=7, hours=2, minutes=15, seconds=59)
    
    # Determine year (use the year when the week starts)
    year = week_start.year
    
    # Calculate week number: weeks since first Friday 9:30 PM of the year
    jan_1 = datetime(year, 1, 1, tzinfo=IST)
    days_to_first_friday = (4 - jan_1.weekday()) % 7
    
    first_friday = jan_1 + timedelta(days=days_to_first_friday)
    first_friday = first_friday.replace(hour=21, minute=30, second=0, microsecond=0)
    
    # Calculate week number
    days_diff = (week_start - first_friday).days
    week_number = (days_diff // 7) + 1
    
    # Ensure week number is between 1 and 52
    week_number = max(1, min(52, week_number))
    
    return week_number, year, week_start, week_end


def get_week_info_monday_to_sunday(now: datetime | None = None, week_number: int | None = None, year: int | None = None) -> tuple[int, int, datetime, datetime]:
    """
    Calculate week start (Monday 00:00 AM) and week end (Sunday 23:59:59) IST for a given week.
    Week numbers are based on the Friday-to-Friday weekly system for consistency.
    
    This function is used for Plans, which need Monday-to-Sunday boundaries.
    The week numbering matches get_week_info_friday_to_friday() for consistency.
    
    If week_number and year are provided, returns that specific week's bounds.
    Otherwise, calculates for current time.
    
    Args:
        now: Current datetime (optional, defaults to now in IST)
        week_number: Specific week number to calculate (1-52, optional)
        year: Year for the specific week (optional)
    
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
    
    # First, get the Friday-Friday week info to determine the week number
    if week_number is not None and year is not None:
        # Calculate the Friday-Friday bounds for this week (for week number consistency)
        friday_week_num, friday_year, friday_start, friday_end = get_week_info_friday_to_friday(
            week_number=week_number, year=year
        )
        week_number = friday_week_num
        year = friday_year
    else:
        # Get current week's Friday-Friday bounds
        week_number, year, friday_start, friday_end = get_week_info_friday_to_friday(now)
    
    # Find the previous Sunday before the Friday week start
    week_end = friday_start - timedelta(days=5)
    week_end = week_end.replace(hour=23, minute=59, second=59, microsecond=0)
    week_start = week_end - timedelta(days=6)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    return week_number, year, week_start, week_end
