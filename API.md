# TrustDesk client API (v1)

For HR, travel and expense applications that create employees and send requests. Everything is JSON over HTTPS under `/api/v1`.

**Authentication.** Create a token with the **Client application** scope (Settings > API tokens) and send it as `Authorization: Bearer <token>`. A client token can use the endpoints in the first three sections below. It cannot read documents, see the AI advice, decide a request, open reports or use `/mcp`. The signed-in approver (browser) and `full` tokens can do everything.

A client application is trusted to say *which employee* a request is for: TrustDesk does not sign employees in itself. Put your own sign-in in front of it.

```bash
export TD=https://<your-host>
export TOKEN=<client token>
```

## Request types

`GET /api/v1/request-types` returns every type, so a client can build its forms from it.

| Kind | Types |
|---|---|
| leave | `vacation`, `sick_leave`, `parental_leave`, `bereavement_leave`, `unpaid_leave` |
| travel | `business_trip` |
| expense | `expense_travel`, `expense_customer`, `expense_meals`, `expense_medical`, `expense_training`, `expense_equipment`, `expense_home_office`, `expense_other` |
| other | `remote_work`, `overtime`, `training_request`, `equipment_request`, `other` (give a label in `details.custom_type`) |

Each type says whether it needs a date range (`range`), a single date (`single`), optional dates or none, whether `amount` and `currency` are required, and which `details` it takes (`text`, `number`, `bool` or `choice`, with a `required` flag). Extra flat `details` fields are accepted (up to 20, names like `trip_ref`, values text, number or true/false) so you can attach what your own process needs.

## Employees

```bash
curl -X POST $TD/api/v1/employees -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
  "employee_no": "E2001", "name": "Alex Example", "email": "alex@example.com",
  "department": "Sales", "title": "Account Executive", "level": "L3", "country": "US",
  "manager_no": "E2000", "hire_date": "2024-03-18"}'
```

| Call | Result |
|---|---|
| `POST /employees` | `201` with the employee. `409` if the number or e-mail exists. `422` if a field is invalid or `manager_no` is unknown. |
| `GET /employees?q=&status=&limit=&offset=` | `{total, items}`; `q` matches name, e-mail, number and department. |
| `GET /employees/{employee_no}` | The employee, or `404`. |
| `PATCH /employees/{employee_no}` | Send only the fields to change. `status: "inactive"` blocks new requests and keeps history. |

`employee_no` is your identifier (1-40 letters, digits, `.`, `-`, `_`). TrustDesk never sends a name, e-mail address or employee number to the AI provider.

## Requests

```bash
curl -X POST $TD/api/v1/requests -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: alex-summer-2026" -d '{
  "employee_no": "E2001", "type": "vacation", "title": "Summer break",
  "description": "Two weeks at the coast", "start_date": "2026-08-03", "end_date": "2026-08-14"}'

curl -X POST $TD/api/v1/requests -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
  "employee_no": "E2001", "type": "expense_customer", "title": "Dinner with Contoso",
  "description": "Dinner with three buyers to close the renewal", "start_date": "2026-06-18",
  "amount": 450.00, "currency": "USD",
  "details": {"customer": "Contoso", "attendees": 3, "business_purpose": "Renewal", "has_receipt": true}}'
```

| Field | Rules |
|---|---|
| `employee_no`, `type`, `title`, `description` | Required. Title up to 160 characters, description 5 to 4,000. |
| `start_date`, `end_date` | `YYYY-MM-DD`; between 5 years back and 3 years ahead; ranges up to 366 days. For `single` types `start_date` is the date it happened. |
| `amount`, `currency` | Up to 2 decimals, 0 to 10,000,000, with a 3-letter code. Required for expenses. Amounts in different currencies are never added together. |
| `details` | Flat object, see the type's description. |

Bodies larger than 64 KB are refused (`413`). Send an `Idempotency-Key` header (1-80 characters) on creation: repeating the call for the same employee returns the original request with `200` instead of creating a duplicate.

| Call | Result |
|---|---|
| `POST /requests` | `201`, status `submitted`. AI pre-validation starts in the background. |
| `GET /requests/{ref}` | `{...request, events}`. `ref` is `REQ-000123` or the number. |
| `GET /requests?employee_no=&status=&type=&kind=&from=&to=&q=&limit=&offset=` | `{total, items}` (limit up to 200). `status=open` means submitted, pending review or waiting for information. |
| `PUT /requests/{ref}` | Answer an approver's question: send changed fields and an optional `note`. Only while the status is `needs_info`; the request is validated again. Otherwise `409`. |
| `POST /requests/{ref}/withdraw` | Optional `{ "note": "..." }`. Only an open request; otherwise `409`. |

### Status

`submitted` (being pre-validated) → `pending_review` (waiting for a person) → `approved` or `rejected`. `needs_info` means the approver asked the employee something (read `decision_note`), and `withdrawn` means the employee cancelled it. Poll `GET /requests/{ref}`, or list with `status=approved`. When decided, `decided_at`, `decided_by` and `decision_note` are set. A client never sees the AI recommendation.

## Approver endpoints (browser session or `full` token)

| Call | Purpose |
|---|---|
| `GET /requests/{ref}` | Adds `validation`: recommendation (`approve`, `reject`, `needs_info`, `manual`), compliance, summary, reasons, citations with quotes (and whether each quote was found word for word), the passages the model saw, automatic checks and warnings. |
| `POST /requests/{ref}/decision` | `{ "action": "approve" \| "reject" \| "request_info", "note": "..." }`. A note is required to reject or ask for information. Deciding against the AI advice needs a note of 10+ characters and is recorded as an override. `409` if the request is already decided or still being validated. |
| `POST /requests/{ref}/validate` | Run the pre-validation again (`202`). Starts the request's automatic-retry count over. |
| `POST /requests/validate-pending` | Validate every request still waiting for it, including those skipped because no account was configured and those whose validation failed (`202`); it starts their automatic-retry count over. |
| `GET /requests/counts` | Counts by status and how many open requests the AI flagged. |
| `GET /reports`, `GET /reports/{name}?from=&to=&format=json\|csv` | Reports: `overview`, `requests`, `compliance`, `out_of_compliance`, `overrides`, `expenses`, `leave`, `aging`, `absences`. CSV neutralises cells that a spreadsheet would treat as formulas. |
| `GET/PUT /validation/settings` | Which provider account validates and how fast waiting requests are caught up. `PUT { "account": "env" \| "<id>" \| "none" \| null, "backlog_per_minute": 0-60 \| null, "max_attempts": 0-10 \| null }`: `none` switches validation off, `null` forgets the saved value (the environment account, else the only account with a chat model, is the default; `backlog_per_minute` falls back to `VALIDATE_BACKLOG_PER_MINUTE`, `max_attempts` to `VALIDATE_MAX_ATTEMPTS`, default 3). `max_attempts` is how many times in all a validation that failed is attempted automatically (after 5, 15, then 60 minutes; `0` never retries; a failure the provider gives for that very request, HTTP 400/413/422, is never retried). A field you leave out is unchanged. `GET` returns the saved `account`, the `effective` one, its `source` (`saved` or `default`) and `backlog` (`waiting`; `retrying` = failed and will be retried, `gave_up` = failed and will not be, counted over open requests; `per_minute`, `default`, `max`; `max_attempts`, `max_attempts_saved`, `max_attempts_default`, `max_attempts_max`, `backoff_minutes`). |
| `POST /demo` | Load the fictional Acme policies, employees and requests (idempotent). |

## Errors

`401` no or bad credentials, `403` the token's scope does not allow it, `404` not found, `409` the request is in the wrong state or a unique value exists, `413` body too large, `422` the input is invalid (the message says which field), `429` too many failed attempts, `503` the database is waking up (retry in a few seconds).
