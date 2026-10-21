import { CLIENTS } from "/static/clients.js";
import { $, copy, h } from "/static/ui.js";

let ready = false;

function render() {
  const c = CLIENTS.find((x) => x.id === $("client").value) || CLIENTS[0];
  const url = location.origin + "/mcp";
  $("client-where").textContent = c.where;
  $("client-snippet").textContent = c.snippet(url);
  $("mcp-url").textContent = url;
  $("client-api-snippet").textContent = CLIENT_API(location.origin);
}

const CLIENT_API = (url) => `export TRUSTDESK_URL=${url}
export TRUSTDESK_CLIENT_TOKEN=<token with the Client application scope>

# 1. what can be requested, and which fields each type takes
curl -s "$TRUSTDESK_URL/api/v1/request-types" -H "Authorization: Bearer $TRUSTDESK_CLIENT_TOKEN"

# 2. create an employee
curl -s -X POST "$TRUSTDESK_URL/api/v1/employees" -H "Authorization: Bearer $TRUSTDESK_CLIENT_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"employee_no":"E2001","name":"Alex Example","email":"alex@example.com","department":"Sales","level":"L3"}'

# 3. send a request (repeat-safe with Idempotency-Key)
curl -s -X POST "$TRUSTDESK_URL/api/v1/requests" -H "Authorization: Bearer $TRUSTDESK_CLIENT_TOKEN" \\
  -H "Content-Type: application/json" -H "Idempotency-Key: alex-2026-summer" \\
  -d '{"employee_no":"E2001","type":"vacation","title":"Summer break","description":"Two weeks off","start_date":"2026-08-03","end_date":"2026-08-14"}'

# 4. follow it: status moves submitted, pending_review, then approved, rejected or needs_info
curl -s "$TRUSTDESK_URL/api/v1/requests/REQ-000001" -H "Authorization: Bearer $TRUSTDESK_CLIENT_TOKEN"`;

export function initConnect() {
  if (!ready) {
    ready = true;
    for (const c of CLIENTS) $("client").append(h("option", { value: c.id }, c.name));
    $("client").addEventListener("change", render);
    $("copy-snippet").addEventListener("click", () => copy($("client-snippet").textContent));
    $("copy-client-api").addEventListener("click", () => copy($("client-api-snippet").textContent));
  }
  render();
}
