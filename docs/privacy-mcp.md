# Privacy policy — MCP section (draft)

Copy this into the public Vepathos privacy policy before step 8. It is not live until
published at an HTTPS URL.

## Vepathos MCP (AI agents)

Vepathos MCP is another channel on the same Vepathos account as the web app and the REST API.
It is not a separate product and does not keep a separate customer database.

### What an agent sends

Optimization tools accept:

- your own stop and vehicle identifiers
- coordinates (latitude and longitude)
- optional weight (kg), volume (m³) and time windows (clock times)

They do **not** accept names, phone numbers, email addresses or free-text street addresses.
Street addresses go through the separate `geocode_addresses` tool, which sends them to
Vepathos Smart Import so they can be turned into coordinates. Do not put personal data in
stop ids.

### What we retain

- Hosted optimization and geocode **results** are kept for **24 hours**, then discarded.
- Usage for billing (stop counts, plan, channel) is stored on the same account ledger as
  the web app. That ledger does not store coordinates or addresses.
- Application logs do not include access tokens, request bodies, coordinates or payloads.

### Who can see the data

The connected AI client (for example Claude) sends the tool arguments. Vepathos processes
them on the MCP adapter and the Core API. The optimizer engine receives coordinates and
constraints, not names or contact details. We do not sell this data.

### Account and payment

Sign-in uses the existing Vepathos identity (Google or email). Paid plan changes, when
enabled, are completed on Stripe. Stripe’s privacy policy applies to payment details.
Vepathos does not collect card numbers on the MCP path.

### Contact

Privacy questions: the contact address already listed in the public Vepathos privacy policy.
MCP product questions: the support contact on [vepathos.com/mcp](https://vepathos.com/mcp)
once that page is published.
