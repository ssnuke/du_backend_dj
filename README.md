# Django App - API Documentation

## Table of Contents
- [Models](#models)
- [API Endpoints](#api-endpoints)
  - [GET Endpoints](#get-endpoints)
  - [POST Endpoints](#post-endpoints)
  - [PUT Endpoints](#put-endpoints)
  - [DELETE Endpoints](#delete-endpoints)

---

## Models

### TeamRole (Enum)
| Value | Description |
|-------|-------------|
| `ADMIN` | Full access |
| `CTC` | Full access (like admin) |
| `LDC` | Can manage teams (created or member as LDC), view subtree |
| `LS` | Can add data for team members |
| `GC` | View own data only |
| `IR` | View own data only |

### AccessLevel (Constants)
| Level | Role |
|-------|------|
| 1 | ADMIN |
| 2 | CTC |
| 3 | LDC |
| 4 | LS |
| 5 | GC |
| 6 | IR |

### InfoResponse (Enum)
| Value |
|-------|
| `A` |
| `B` |
| `C` |

---

### IrId
Whitelist of allowed IR IDs.

| Field | Type | Constraints |
|-------|------|-------------|
| `ir_id` | CharField | Primary Key, max_length=18 |

---

### Ir (Individual Representative)
Main user model with hierarchy support.

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `ir_id` | CharField | Primary Key, max_length=18 | - |
| `ir_name` | CharField | max_length=45 | - |
| `ir_email` | EmailField | - | - |
| `ir_access_level` | PositiveSmallIntegerField | 1-6 | 6 (IR) |
| `ir_password` | CharField | max_length=256, hashed | - |
| `status` | BooleanField | - | True |
| `parent_ir` | ForeignKey(self) | nullable | null |
| `hierarchy_path` | CharField | max_length=500, indexed | "/" |
| `hierarchy_level` | PositiveIntegerField | - | 0 |
| `plan_count` | IntegerField | - | 0 |
| `dr_count` | IntegerField | - | 0 |
| `info_count` | IntegerField | - | 0 |
| `name_list` | IntegerField | - | 0 |
| `uv_count` | IntegerField | - | 0 |
| `weekly_info_target` | IntegerField | - | 0 |
| `weekly_plan_target` | IntegerField | - | 0 |
| `weekly_uv_target` | IntegerField | nullable | null |
| `started_date` | DateField | auto_now_add | - |

---

### Team

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `id` | AutoField | Primary Key | - |
| `name` | CharField | max_length=100 | - |
| `created_by` | ForeignKey(Ir) | nullable | null |
| `weekly_info_done` | IntegerField | - | 0 |
| `weekly_plan_done` | IntegerField | - | 0 |
| `weekly_info_target` | IntegerField | - | 0 |
| `weekly_plan_target` | IntegerField | - | 0 |
| `created_at` | DateTimeField | auto_now_add | - |

---

### TeamMember

| Field | Type | Constraints |
|-------|------|-------------|
| `id` | AutoField | Primary Key |
| `team` | ForeignKey(Team) | CASCADE |
| `ir` | ForeignKey(Ir) | CASCADE |
| `role` | CharField | max_length=5, choices=TeamRole |

**Unique Constraint:** `(team, ir)`

---

### InfoDetail

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `id` | AutoField | Primary Key | - |
| `ir` | ForeignKey(Ir) | CASCADE | - |
| `info_date` | DateTimeField | - | timezone.now |
| `response` | CharField | max_length=1, choices=InfoResponse | - |
| `comments` | TextField | nullable | null |
| `info_name` | CharField | max_length=100 | - |

---

### PlanDetail

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `id` | AutoField | Primary Key | - |
| `ir` | ForeignKey(Ir) | CASCADE | - |
| `plan_date` | DateTimeField | - | timezone.now |
| `plan_name` | CharField | max_length=255, nullable | null |
| `comments` | TextField | nullable | null |

---

### TeamWeek

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `id` | AutoField | Primary Key | - |
| `team` | ForeignKey(Team) | CASCADE | - |
| `week_start` | DateTimeField | - | - |
| `weekly_info_done` | IntegerField | - | 0 |
| `weekly_plan_done` | IntegerField | - | 0 |
| `created_at` | DateTimeField | auto_now_add | - |

---

### WeeklyTarget

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `id` | AutoField | Primary Key | - |
| `week_number` | PositiveSmallIntegerField | 1-52 | - |
| `year` | PositiveIntegerField | - | - |
| `week_start` | DateTimeField | - | - |
| `week_end` | DateTimeField | - | - |
| `ir` | ForeignKey(Ir) | nullable, CASCADE | null |
| `ir_weekly_info_target` | IntegerField | nullable | null |
| `ir_weekly_plan_target` | IntegerField | nullable | null |
| `ir_weekly_uv_target` | IntegerField | nullable | null |
| `team` | ForeignKey(Team) | nullable, CASCADE | null |
| `team_weekly_info_target` | IntegerField | nullable | null |
| `team_weekly_plan_target` | IntegerField | nullable | null |
| `team_weekly_uv_target` | IntegerField | nullable | null |
| `created_at` | DateTimeField | auto_now_add | - |
| `updated_at` | DateTimeField | auto_now | - |

**Unique Constraints:** 
- `(week_number, year, ir)`
- `(week_number, year, team)`

---

## API Endpoints

Base URL: `/api/`

---

## GET Endpoints

### Get All IR IDs (Whitelist)
```
GET /api/get_all_ir/
```

**Response:**
```json
[
  { "ir_id": "IR123456789012345" }
]
```

---

### Get Single IR
```
GET /api/ir/{ir_id}/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `requester_ir_id` | string | No | IR ID for role-based permission check |

**Response:**
```json
{
  "ir_id": "IR123456789012345",
  "ir_name": "John Doe",
  "ir_email": "john@example.com",
  "ir_access_level": 6,
  "status": true,
  "hierarchy_level": 1,
  "parent_ir_id": "IR000000000000001"
}
```

---

### Get All Registered IRs
```
GET /api/irs/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `requester_ir_id` | string | No | Filter based on requester's role-based visibility |

**Response:**
```json
{
  "data": [
    {
      "ir_id": "IR123456789012345",
      "ir_name": "John Doe",
      "hierarchy_level": 1,
      "parent_ir_id": "IR000000000000001"
    }
  ],
  "count": 1
}
```

---

### Get All Teams
```
GET /api/teams/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `requester_ir_id` | string | No | Filter based on requester's role-based visibility |

**Response:**
```json
[
  {
    "id": 1,
    "name": "Team Alpha",
    "created_by": "IR123456789012345",
    "weekly_info_done": 10,
    "weekly_plan_done": 5,
    "weekly_info_target": 15,
    "weekly_plan_target": 10
  }
]
```

---

### Get LDCs
```
GET /api/ldcs/
```

**Response:**
```json
[
  {
    "ir_id": "IR123456789012345",
    "ir_name": "John Doe"
  }
]
```

---

### Get Teams by LDC
```
GET /api/teams_by_ldc/{ldc_id}/
```

**Response:**
```json
[
  {
    "id": 1,
    "name": "Team Alpha"
  }
]
```

---

### Get Team Members
```
GET /api/team_members/{team_id}/
```

**Response:**
```json
[
  {
    "ir_id": "IR123456789012345",
    "ir_name": "John Doe",
    "role": "LDC"
  }
]
```

---

### Get Info Details
```
GET /api/info_details/{ir_id}/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `start_date` | date | No | Filter from date (YYYY-MM-DD) |
| `end_date` | date | No | Filter to date (YYYY-MM-DD) |

**Response:**
```json
[
  {
    "id": 1,
    "ir": "IR123456789012345",
    "info_date": "2026-01-06T10:00:00Z",
    "response": "A",
    "comments": "Good meeting",
    "info_name": "Client ABC"
  }
]
```

---

### Get Plan Details
```
GET /api/plan_details/{ir_id}/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `start_date` | date | No | Filter from date (YYYY-MM-DD) |
| `end_date` | date | No | Filter to date (YYYY-MM-DD) |

**Response:**
```json
[
  {
    "id": 1,
    "ir": "IR123456789012345",
    "plan_date": "2026-01-06T10:00:00Z",
    "plan_name": "Meeting Plan",
    "comments": "Follow-up required"
  }
]
```

---

### Get UV Count
```
GET /api/uv_count/{ir_id}/
```

**Response:**
```json
{
  "ir_id": "IR123456789012345",
  "uv_count": 5
}
```

---

### Get Targets Dashboard
```
GET /api/targets_dashboard/{ir_id}/
```

**Response:**
```json
{
  "ir_id": "IR123456789012345",
  "weekly_info_target": 10,
  "weekly_plan_target": 5,
  "weekly_uv_target": 3,
  "info_done": 8,
  "plan_done": 4,
  "uv_done": 2
}
```

---

### Get Targets
```
GET /api/get_targets/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ir_id` | string | No | Get targets for specific IR |
| `team_id` | int | No | Get targets for specific team |
| `week_number` | int | No | Specific week number |
| `year` | int | No | Specific year |

**Response:**
```json
{
  "ir_weekly_info_target": 10,
  "ir_weekly_plan_target": 5,
  "team_weekly_info_target": 20,
  "team_weekly_plan_target": 15
}
```

---

### Get Teams by IR
```
GET /api/teams_by_ir/{ir_id}/
```

**Response:**
```json
[
  {
    "id": 1,
    "name": "Team Alpha",
    "role": "LDC"
  }
]
```

---

### Get Team Info Total
```
GET /api/team_info_total/{team_id}/
```

**Response:**
```json
{
  "team_id": 1,
  "total_info": 25
}
```

---

### Get Team UV Total
```
GET /api/team_uv_total/{team_id}/
```

**Response:**
```json
{
  "team_id": 1,
  "total_uv": 10
}
```

---

### Get Visible Teams (Hierarchy-based)
```
GET /api/visible_teams/{ir_id}/
```

**Response:**
```json
[
  {
    "id": 1,
    "name": "Team Alpha"
  }
]
```

---

### Get Downline Data (Hierarchy-based)
```
GET /api/downline_data/{ir_id}/
```

**Response:**
```json
{
  "downlines": [
    {
      "ir_id": "IR123456789012345",
      "ir_name": "John Doe",
      "hierarchy_level": 2
    }
  ],
  "count": 1
}
```

---

### Get Direct Downlines (Hierarchy-based)
```
GET /api/direct_downlines/{ir_id}/
```

**Response:**
```json
[
  {
    "ir_id": "IR123456789012345",
    "ir_name": "John Doe"
  }
]
```

---

### Get Hierarchy Tree (Hierarchy-based)
```
GET /api/hierarchy_tree/{ir_id}/
```

**Response:**
```json
{
  "ir_id": "IR000000000000001",
  "ir_name": "Root User",
  "children": [
    {
      "ir_id": "IR123456789012345",
      "ir_name": "John Doe",
      "children": []
    }
  ]
}
```

---

## POST Endpoints

### Add IR ID (Whitelist)
```
POST /api/add_ir_id/
```

**Request Body:**
```json
[
  { "ir_id": "IR123456789012345" }
]
```
*Or single object:*
```json
{ "ir_id": "IR123456789012345" }
```

**Response (201):**
```json
{
  "message": "IrId(s) added",
  "ir_ids": ["IR123456789012345"]
}
```

**Errors (400):**
```json
{
  "errors": [
    { "index": 0, "ir_id": "IR123456789012345", "error": "IR ID already exists" }
  ]
}
```

---

### Register New IR
```
POST /api/register_new_ir/
```

**Request Body:**
```json
{
  "ir_id": "IR123456789012345",
  "ir_name": "John Doe",
  "ir_email": "john@example.com",
  "ir_password": "secret123",
  "ir_access_level": 6,
  "parent_ir_id": "IR000000000000001"
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `ir_id` | string | Yes | - |
| `ir_name` | string | Yes | - |
| `ir_email` | string | Yes | - |
| `ir_password` | string | No | "secret" |
| `ir_access_level` | int | No | 5 |
| `parent_ir_id` | string | No | null |

**Response (201):**
```json
{
  "message": "IR(s) registered successfully",
  "ir_ids": ["IR123456789012345"]
}
```

**Errors (400):**
```json
{
  "errors": [
    { "index": 0, "ir_id": "IR123456789012345", "error": "Already registered" }
  ]
}
```

---

### Bulk Register from Excel
```
POST /api/bulk_register_from_excel/
```

**Request:** `multipart/form-data`

| Field | Type | Required |
|-------|------|----------|
| `file` | File (xlsx) | Yes |

**Response (201):**
```json
{
  "message": "Bulk registration successful",
  "created_count": 10
}
```

---

### IR Login
```
POST /api/login/
```

**Request Body:**
```json
{
  "ir_id": "IR123456789012345",
  "password": "secret123"
}
```

**Response (200):**
```json
{
  "message": "Login successful",
  "ir_id": "IR123456789012345",
  "ir_name": "John Doe",
  "ir_access_level": 6
}
```

**Errors (401):**
```json
{
  "detail": "Invalid credentials"
}
```

---

### Create Team
```
POST /api/create_team/
```

**Request Body:**
```json
{
  "name": "Team Alpha",
  "created_by_ir_id": "IR123456789012345",
  "weekly_info_target": 10,
  "weekly_plan_target": 5
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `name` | string | Yes | - |
| `created_by_ir_id` | string | No | null |
| `weekly_info_target` | int | No | 0 |
| `weekly_plan_target` | int | No | 0 |

**Response (201):**
```json
{
  "message": "Team created",
  "team_id": 1,
  "name": "Team Alpha"
}
```

---

### Add IR to Team
```
POST /api/add_ir_to_team/
```

**Request Body:**
```json
{
  "team_id": 1,
  "ir_id": "IR123456789012345",
  "role": "LDC",
  "requester_ir_id": "IR000000000000001"
}
```

| Field | Type | Required |
|-------|------|----------|
| `team_id` | int | Yes |
| `ir_id` | string | Yes |
| `role` | string | Yes |
| `requester_ir_id` | string | No |

**Response (201):**
```json
{
  "message": "IR added to team",
  "team_id": 1,
  "ir_id": "IR123456789012345"
}
```

---

### Add Info Detail
```
POST /api/add_info_detail/{ir_id}/
```

**Request Body:**
```json
{
  "info_name": "Client ABC",
  "response": "A",
  "comments": "Good meeting",
  "info_date": "2026-01-06T10:00:00Z",
  "requester_ir_id": "IR000000000000001"
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `info_name` | string | Yes | - |
| `response` | string | Yes | - |
| `comments` | string | No | null |
| `info_date` | datetime | No | now |
| `requester_ir_id` | string | No | - |

**Response (201):**
```json
{
  "message": "Info detail added",
  "info_id": 1,
  "ir_id": "IR123456789012345"
}
```

---

### Add Plan Detail
```
POST /api/add_plan_detail/{ir_id}/
```

**Request Body:**
```json
{
  "plan_name": "Meeting Plan",
  "comments": "Follow-up required",
  "plan_date": "2026-01-06T10:00:00Z",
  "requester_ir_id": "IR000000000000001"
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `plan_name` | string | No | null |
| `comments` | string | No | null |
| `plan_date` | datetime | No | now |
| `requester_ir_id` | string | No | - |

**Response (201):**
```json
{
  "message": "Plan detail added",
  "plan_id": 1,
  "ir_id": "IR123456789012345"
}
```

---

### Add UV
```
POST /api/add_uv/{ir_id}/
```

**Request Body:**
```json
{
  "count": 1,
  "requester_ir_id": "IR000000000000001"
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `count` | int | No | 1 |
| `requester_ir_id` | string | No | - |

**Response (200):**
```json
{
  "message": "UV count updated",
  "ir_id": "IR123456789012345",
  "new_uv_count": 6
}
```

---

### Set Targets
```
POST /api/set_targets/
```

**Request Body:**
```json
{
  "ir_id": "IR123456789012345",
  "team_id": 1,
  "weekly_info_target": 10,
  "weekly_plan_target": 5,
  "weekly_uv_target": 3,
  "week_number": 1,
  "year": 2026
}
```

| Field | Type | Required |
|-------|------|----------|
| `ir_id` | string | No |
| `team_id` | int | No |
| `weekly_info_target` | int | No |
| `weekly_plan_target` | int | No |
| `weekly_uv_target` | int | No |
| `week_number` | int | No |
| `year` | int | No |

**Response (200):**
```json
{
  "message": "Targets set successfully"
}
```

---

### Password Reset
```
POST /api/password_reset/
```

**Request Body:**
```json
{
  "ir_id": "IR123456789012345",
  "new_password": "newpassword123"
}
```

**Response (200):**
```json
{
  "message": "Password reset successful",
  "ir_id": "IR123456789012345"
}
```

---

### Change IR Access Level
```
POST /api/change_access_level/
```

**Request Body:**
```json
{
  "ir_id": "IR123456789012345",
  "new_access_level": 3,
  "acting_ir_id": "IR000000000000001"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ir_id` | string | Yes | Target IR |
| `new_access_level` | int | Yes | 1-6 |
| `acting_ir_id` | string | Yes | Must be ADMIN/CTC |

**Response (200):**
```json
{
  "message": "Access level changed",
  "ir_id": "IR123456789012345",
  "new_access_level": 3
}
```

---

### Reset Database
```
POST /api/reset_database/
```

**⚠️ WARNING: This deletes ALL data from all tables!**

**Response (200):**
```json
{
  "status": "success",
  "message": "Database has been reset successfully"
}
```

---

## PUT Endpoints

### Update IR Details
```
PUT /api/update_ir/{ir_id}/
```

**Request Body:**
```json
{
  "acting_ir_id": "IR000000000000001",
  "ir_name": "John Smith",
  "ir_access_level": 4,
  "password": "newpassword123",
  "weekly_info_target": 15,
  "weekly_plan_target": 10,
  "weekly_uv_target": 5
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `acting_ir_id` | string | Yes | Required for authorization |
| `ir_name` | string | No | Update name |
| `ir_access_level` | int | No | Only ADMIN can change (1-6) |
| `password` | string | No | Update password |
| `weekly_info_target` | int | No | Update target |
| `weekly_plan_target` | int | No | Update target |
| `weekly_uv_target` | int | No | Only for CTC/LDC |

**Response (200):**
```json
{
  "message": "IR details updated successfully",
  "ir_id": "IR123456789012345",
  "updated_fields": {
    "ir_name": "John Smith",
    "weekly_info_target": 15
  }
}
```

**Errors (403):**
```json
{
  "detail": "Not authorized to update other IR's details. Only ADMIN/CTC can do this."
}
```

---

### Update Info Detail
```
PUT /api/update_info_detail/{info_id}/
```

**Request Body:**
```json
{
  "info_name": "Updated Client Name",
  "response": "B",
  "comments": "Updated comments",
  "requester_ir_id": "IR000000000000001"
}
```

| Field | Type | Required |
|-------|------|----------|
| `info_name` | string | No |
| `response` | string | No |
| `comments` | string | No |
| `requester_ir_id` | string | No |

**Response (200):**
```json
{
  "message": "Info detail updated",
  "info_id": 1
}
```

---

### Update Plan Detail
```
PUT /api/update_plan_detail/{plan_id}/
```

**Request Body:**
```json
{
  "plan_name": "Updated Plan Name",
  "comments": "Updated comments",
  "requester_ir_id": "IR000000000000001"
}
```

| Field | Type | Required |
|-------|------|----------|
| `plan_name` | string | No |
| `comments` | string | No |
| `requester_ir_id` | string | No |

**Response (200):**
```json
{
  "message": "Plan detail updated",
  "plan_id": 1
}
```

---

### Update Team Name
```
PUT /api/update_team_name/{team_id}/
```

**Request Body:**
```json
{
  "name": "New Team Name",
  "requester_ir_id": "IR000000000000001"
}
```

| Field | Type | Required |
|-------|------|----------|
| `name` | string | Yes |
| `requester_ir_id` | string | No |

**Response (200):**
```json
{
  "message": "Team name updated",
  "team_id": 1,
  "name": "New Team Name"
}
```

---

## DELETE Endpoints

### Delete Team
```
DELETE /api/delete_team/{team_id}/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `requester_ir_id` | string | No | For role-based authorization |

**Response (200):**
```json
{
  "message": "Team with ID 1 and its members have been deleted"
}
```

**Errors (403):**
```json
{
  "detail": "Not authorized to delete this team"
}
```

---

### Remove IR from Team
```
DELETE /api/remove_ir_from_team/{team_id}/{ir_id}/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `requester_ir_id` | string | No | For role-based authorization |

**Response (200):**
```json
{
  "message": "IR 'IR123456789012345' removed from team 1"
}
```

**Errors (404):**
```json
{
  "detail": "IR not found in team"
}
```

---

### Delete Info Detail
```
DELETE /api/delete_info_detail/{info_id}/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `requester_ir_id` | string | No | For role-based authorization |

**Response (200):**
```json
{
  "message": "Info detail deleted",
  "info_id": 1
}
```

---

### Delete Plan Detail
```
DELETE /api/delete_plan_detail/{plan_id}/
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `requester_ir_id` | string | No | For role-based authorization |

**Response (200):**
```json
{
  "message": "Plan detail deleted",
  "plan_id": 1
}
```

---

## Health Check

### Health Check
```
GET /api/health/
```

**Response (200):**
```json
{
  "status": "healthy"
}
```

---

## Role-Based Access Control Summary

| Role | Level | View IRs | View Teams | Edit Teams | Add Data For |
|------|-------|----------|------------|------------|--------------|
| ADMIN | 1 | All | All | All | All |
| CTC | 2 | All | All | All | All |
| LDC | 3 | Subtree | Subtree + Member | Own Created + LDC Member Of | Own Team Members |
| LS | 4 | Team Members | Member Of | None | Team Members |
| GC | 5 | Self Only | None | None | Self Only |
| IR | 6 | Self Only | None | None | Self Only |

**Note:** LDCs can now edit (add members, update, delete) teams that they either created OR teams where they are added as an LDC member. This allows LDCs added to a team to manage that team collaboratively.

---

## Error Response Format

All error responses follow this format:

```json
{
  "detail": "Error message description"
}
```

Or for validation errors:
```json
{
  "errors": [
    {
      "index": 0,
      "field": "ir_id",
      "error": "Error description"
    }
  ]
}
```

---

## HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request - Invalid input |
| 401 | Unauthorized - Invalid credentials |
| 403 | Forbidden - Insufficient permissions |
| 404 | Not Found - Resource doesn't exist |
| 500 | Internal Server Error |
