from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")

# Define Year 2026 Week 1 Start: January 2, 2026, 9:31 PM IST
YEAR_2026_WEEK_1_START = datetime(2026, 1, 2, 21, 31, 0, 0, tzinfo=IST)


def get_week_info_friday_to_friday(now: datetime | None = None, week_number: int | None = None, year: int | None = None) -> tuple[int, int, datetime, datetime]:
    """
    Calculate week number (1-52), year, week start, and week end
    for Friday 9:31 PM IST to next Friday 9:30 PM IST weekly cycles.
    
    Week 1 of 2026 starts: Jan 2, 2026 9:31 PM IST
    Week 1 of 2026 ends: Jan 9, 2026 9:30 PM IST
    
    For other years, Week 1 starts on the first Friday of January at 9:31 PM.
    
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
        # Get the anchor date (Week 1 start) for the requested year
        if year == 2026:
            first_week_start = YEAR_2026_WEEK_1_START
        else:
            # For other years, Week 1 starts on first Friday of January at 9:31 PM
            jan_1 = datetime(year, 1, 1, tzinfo=IST)
            # Friday = 4 in weekday() (Monday=0, ..., Friday=4, Saturday=5, Sunday=6)
            days_to_first_friday = (4 - jan_1.weekday()) % 7
            if days_to_first_friday == 0:  # Jan 1 is a Friday
                days_to_first_friday = 0
            first_week_start = jan_1 + timedelta(days=days_to_first_friday)
            first_week_start = first_week_start.replace(hour=21, minute=31, second=0, microsecond=0)
        
        # Calculate the start of the requested week
        # Week 1 starts at first_week_start, Week 2 at first_week_start + 7 days, etc.
        week_start = first_week_start + timedelta(weeks=(week_number - 1))
        
        # Week ends next Friday at 9:30 PM (6 days, 23 hours, 59 minutes later)
        # From 9:31 PM Friday to 9:30 PM next Friday = 6 days + 23 hours + 59 minutes
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        
        return week_number, year, week_start, week_end
    
    # Calculate current week based on current time
    # Determine the year first
    current_year = now.year
    
    # Get Week 1 start for current year
    if current_year == 2026:
        first_week_start = YEAR_2026_WEEK_1_START
    else:
        jan_1 = datetime(current_year, 1, 1, tzinfo=IST)
        days_to_first_friday = (4 - jan_1.weekday()) % 7
        if days_to_first_friday == 0:
            days_to_first_friday = 0
        first_week_start = jan_1 + timedelta(days=days_to_first_friday)
        first_week_start = first_week_start.replace(hour=21, minute=31, second=0, microsecond=0)
    
    # Check if we're before Week 1 of current year
    if now < first_week_start:
        # We're in the previous year's last week
        current_year -= 1
        if current_year == 2026:
            first_week_start = YEAR_2026_WEEK_1_START
        else:
            jan_1 = datetime(current_year, 1, 1, tzinfo=IST)
            days_to_first_friday = (4 - jan_1.weekday()) % 7
            if days_to_first_friday == 0:
                days_to_first_friday = 0
            first_week_start = jan_1 + timedelta(days=days_to_first_friday)
            first_week_start = first_week_start.replace(hour=21, minute=31, second=0, microsecond=0)
    
    # Calculate how many complete weeks have passed since first_week_start
    time_diff = now - first_week_start
    weeks_passed = int(time_diff.total_seconds() // (7 * 24 * 60 * 60))
    
    # Week number (1-52)
    week_number = min(weeks_passed + 1, 52)
    
    # Calculate actual week start and end
    week_start = first_week_start + timedelta(weeks=weeks_passed)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    
    return week_number, current_year, week_start, week_end


def get_week_info_monday_to_sunday(now: datetime | None = None, week_number: int | None = None, year: int | None = None) -> tuple[int, int, datetime, datetime]:
    """
    Calculate week start (Sunday 00:00 AM) and week end (Saturday 23:59:59) IST for a given week.
    Week numbers are based on the Friday-to-Friday weekly system for consistency.
    
    This function is used for Plans, which need Sunday-to-Saturday boundaries.
    The week numbering matches get_week_info_friday_to_friday() for consistency.
    
    For Week 1 of 2026:
    - Friday-Friday (Infos): Jan 2, 2026 9:31 PM to Jan 9, 2026 9:30 PM
    - Sunday-Saturday (Plans): Dec 28, 2025 to Jan 3, 2026 (the Sunday-Saturday range before the Friday week start)
    
    Logic: For a given Friday-Friday week, find the Sunday that comes BEFORE the Friday start,
    and the Saturday that follows that Sunday (6 days later).
    
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
    
    # First, get the Friday-Friday week info to determine the week number and Friday start
    if week_number is not None and year is not None:
        friday_week_num, friday_year, friday_start, friday_end = get_week_info_friday_to_friday(
            week_number=week_number, year=year
        )
    else:
        friday_week_num, friday_year, friday_start, friday_end = get_week_info_friday_to_friday(now)
    
    # Now find the Sunday-Saturday range that comes BEFORE the Friday start time
    # friday_start is a Friday at 9:31 PM
    # We need to find the Sunday before this Friday
    
    # Friday is weekday() = 4, Sunday is weekday() = 6
    # Days back from Friday to previous Sunday: (4 - 6) % 7 = -2 % 7 = 5 days
    # But we want the Sunday before, so: (4 + 2) % 7 = 6, but that doesn't work
    # Actually: Friday (4) back to Sunday (6 of previous week) = 5 days back
    
    # From Friday, go back 5 days to get to Sunday
    days_to_sunday = (friday_start.weekday() + 2) % 7  # This gives us days back to Sunday
    if days_to_sunday == 0:  # If Friday's weekday + 2 is divisible by 7
        days_to_sunday = 7
    
    # Actually simpler: Friday is weekday 4, Sunday (previous week) is 5 days before
    # Friday -> Thursday (1) -> Wednesday (2) -> Tuesday (3) -> Monday (4) -> Sunday (5)
    week_start = friday_start - timedelta(days=5)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Saturday is 6 days after Sunday
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    
    return friday_week_num, friday_year, week_start, week_end


def get_current_week_start(now: datetime | None = None) -> datetime:
    """
    Compute the current week start datetime using
    Friday 9:31 PM IST as the week boundary.
    
    Week starts at Friday 9:31 PM and ends next Friday 9:30 PM.

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
    
    # Get the current Friday-Friday week info
    week_number, year, week_start, week_end = get_week_info_friday_to_friday(now)
    
    return week_start


# Legacy function for backwards compatibility
def get_saturday_friday_week_info(now: datetime | None = None) -> tuple[int, int, datetime, datetime]:
    """
    DEPRECATED: Use get_week_info_friday_to_friday() instead.
    
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