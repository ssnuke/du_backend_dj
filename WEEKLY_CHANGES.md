# Weekly Date Range Calculation Updates

## Summary of Changes

Updated the Django backend to calculate weekly data with different date ranges for Plans and Infos:

1. **Plans for the week**: Monday 00:00 AM to Sunday 23:59:59 IST
2. **Infos for the week**: Friday 9:30 PM IST to Next Friday 11:45 PM IST

## Files Modified

### 1. `/backend/core/utils/dates.py`
- **Updated**: `get_week_info_friday_to_friday()` 
  - Changed time boundary from Friday 9:31 PM to Friday 9:30 PM (start)
  - Changed end time from Friday 9:30 PM to Friday 11:45 PM (end)
  - Now correctly: Friday 9:30 PM → Next Friday 11:45 PM
  
- **Added**: `get_week_info_monday_to_sunday()`
  - New function for calculating Plans weekly data
  - Monday 00:00 AM → Sunday 23:59:59 IST
  - Uses same week numbering as Friday-Friday system for consistency
  - Maps Friday-Friday week to Monday-Sunday boundaries

### 2. `/backend/core/views/get.py`
- **Updated imports**: Added `get_week_info_monday_to_sunday` 

- **Updated `GetTeamMembers` class**:
  - Now uses `get_week_info_friday_to_friday()` for info counts
  - Now uses `get_week_info_monday_to_sunday()` for plan counts
  - Applies correct time boundaries for each data type

- **Updated `GetInfoDetails` class**:
  - Uses `get_week_info_friday_to_friday()` for filtering info records

- **Updated `GetPlanDetails` class**:
  - Changed from Friday-Friday to Monday-Sunday boundaries
  - Uses `get_week_info_monday_to_sunday()` for filtering plan records

- **Updated `GetTeamInfoTotal` class**:
  - Uses `get_week_info_friday_to_friday()` for info aggregation
  - Uses `get_week_info_monday_to_sunday()` for plan aggregation
  - Applies correct time boundaries for each data type

- **Updated `GetVisibleTeams` class**:
  - Uses `get_week_info_friday_to_friday()` for info calculations
  - Uses `get_week_info_monday_to_sunday()` for plan calculations

### 3. `/backend/core/views/post.py`
- **Updated imports**: Added `get_week_info_monday_to_sunday`

## API Behavior Changes

### For INFO Queries (e.g., GET /api/info_details/{ir_id}/)
- When filtering by week: Data is fetched from Friday 9:30 PM IST to Next Friday 11:45 PM IST
- Week boundaries now correctly reflect the Infos weekly cycle

### For PLAN Queries (e.g., GET /api/plan_details/{ir_id}/)
- When filtering by week: Data is fetched from Monday 00:00 AM to Sunday 23:59:59 IST
- Week boundaries now correctly reflect the Plans weekly cycle

### For TEAM MEMBER DATA (e.g., GET /api/team_members/{team_id}/)
- Info counts: Friday 9:30 PM to Next Friday 11:45 PM
- Plan counts: Monday 00:00 AM to Sunday 23:59:59 IST
- Weekly targets and achievement calculations now use correct boundaries

### For TEAM TOTALS (e.g., GET /api/team_info_total/{team_id}/)
- Info totals: Friday 9:30 PM to Next Friday 11:45 PM
- Plan totals: Monday 00:00 AM to Sunday 23:59:59 IST

## Week Numbering
- Both functions use the same week numbering system (based on Friday-Friday boundaries)
- Week numbers remain consistent (1-52) across both data types
- This ensures that when filtering by week number, the "week" is well-defined even though the data boundaries differ

## Testing Recommendations
1. Create a test record for Plans on Monday morning and verify it's counted
2. Create a test record for Infos on Friday at 9:30 PM and verify it's included
3. Verify that records after Sunday 23:59:59 for Plans are NOT included
4. Verify that records after Friday 11:45 PM for Infos are NOT included in the same week
5. Test cross-year week boundaries to ensure year calculations remain correct
