#!/usr/bin/env python
"""
Test script to verify the weekly date range calculations.

Requirements:
1. Plans for the week: Monday to next Sunday
2. Infos for the week: Friday 9:30 PM IST to Next Friday 11:45 PM IST
"""

from datetime import datetime, timedelta
import pytz
from backend.core.utils.dates import (
    get_week_info_friday_to_friday,
    get_week_info_monday_to_sunday
)

IST = pytz.timezone('Asia/Kolkata')

def test_friday_to_friday():
    """Test Friday 9:30 PM to Next Friday 11:45 PM (for Infos)"""
    print("=" * 80)
    print("Testing Friday 9:30 PM to Next Friday 11:45 PM (for Infos)")
    print("=" * 80)
    
    # Test with a specific date
    test_date = datetime(2026, 1, 14, 10, 0, 0, tzinfo=IST)  # Wednesday
    week_number, year, week_start, week_end = get_week_info_friday_to_friday(test_date)
    
    print(f"Test Date: {test_date.strftime('%A, %B %d, %Y %H:%M:%S %Z')}")
    print(f"Week Number: {week_number}")
    print(f"Year: {year}")
    print(f"Week Start: {week_start.strftime('%A, %B %d, %Y %H:%M:%S %Z')}")
    print(f"Week End: {week_end.strftime('%A, %B %d, %Y %H:%M:%S %Z')}")
    
    # Verify start is Friday 9:30 PM
    assert week_start.weekday() == 4, f"Start should be Friday, got {week_start.strftime('%A')}"
    assert week_start.hour == 21 and week_start.minute == 30, f"Start should be 21:30, got {week_start.hour}:{week_start.minute}"
    
    # Verify end is next Friday 11:45:59 PM
    assert week_end.weekday() == 4, f"End should be Friday, got {week_end.strftime('%A')}"
    assert week_end.hour == 23 and week_end.minute == 45, f"End should be 23:45, got {week_end.hour}:{week_end.minute}"
    
    # Verify it's 7 days apart
    time_diff = week_end - week_start
    assert time_diff.days == 7, f"Should be 7 days apart, got {time_diff.days}"
    
    print("✓ All assertions passed for Friday-to-Friday!")
    print()

def test_monday_to_sunday():
    """Test Monday 00:00 AM to Sunday 23:59:59 (for Plans)"""
    print("=" * 80)
    print("Testing Monday 00:00 AM to Sunday 23:59:59 (for Plans)")
    print("=" * 80)
    
    # Test with a specific date
    test_date = datetime(2026, 1, 14, 10, 0, 0, tzinfo=IST)  # Wednesday
    week_number, year, week_start, week_end = get_week_info_monday_to_sunday(test_date)
    
    print(f"Test Date: {test_date.strftime('%A, %B %d, %Y %H:%M:%S %Z')}")
    print(f"Week Number: {week_number}")
    print(f"Year: {year}")
    print(f"Week Start: {week_start.strftime('%A, %B %d, %Y %H:%M:%S %Z')}")
    print(f"Week End: {week_end.strftime('%A, %B %d, %Y %H:%M:%S %Z')}")
    
    # Verify start is Monday 00:00 AM
    assert week_start.weekday() == 0, f"Start should be Monday, got {week_start.strftime('%A')}"
    assert week_start.hour == 0 and week_start.minute == 0, f"Start should be 00:00, got {week_start.hour}:{week_start.minute}"
    
    # Verify end is Sunday 23:59:59
    assert week_end.weekday() == 6, f"End should be Sunday, got {week_end.strftime('%A')}"
    assert week_end.hour == 23 and week_end.minute == 59 and week_end.second == 59, f"End should be 23:59:59, got {week_end.hour}:{week_end.minute}:{week_end.second}"
    
    # Verify it's 7 days apart
    time_diff = week_end - week_start
    assert time_diff.days == 6, f"Should be ~7 days apart (6 full days + partial), got {time_diff.days}"
    
    print("✓ All assertions passed for Monday-to-Sunday!")
    print()

if __name__ == "__main__":
    try:
        test_friday_to_friday()
        test_monday_to_sunday()
        print("\n" + "=" * 80)
        print("✓ All tests passed!")
        print("=" * 80)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
