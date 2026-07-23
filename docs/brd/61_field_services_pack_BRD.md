# BRD 61 — `field-services-ops` capability pack

**Deliverable type:** Capability Pack (BRD 23) · **Publisher:** Datacern · **Initial version:** 0.1.0 (draft)
**Status:** draft — 2026-07-23 · not started
**Owner:** platform · **Related:** BRD 60 (external agent governance — hard dependency), BRD 23 (pack service), BRD 53 (custom agents), BRD 19 (notification), BRD 08 (case-service). Reference implementation pattern: `26_healthcare_provider_rcm_pack_BRD.md`.

---

## 1. Overview

**Purpose.** Back-office workflow execution for **residential field-services SMBs** (HVAC, plumbing, electrical, garage door, roofing): job intake → booking → dispatch → confirmation → invoicing → AR follow-up. Sold **B2B2B through voice-agent partners** — the partner's voice AI owns the phone call; Datacern is the governed execution layer that turns the call's intent into completed, audited, multi-system work. Sits ABOVE the SMB's existing FSM system of record (ServiceTitan, Housecall Pro, Jobber) — reads via native APIs, writes through governed tool adapters.

**Why this vertical.** First non-regulated pack, and the first consumer of the BRD 60 external-agent ingress — it proves the "governed decision layer for other people's agents" thesis with a paying partner channel. Market evidence (verified 2026-07, adversarially fact-checked; see `docs/research/` when landed): admin burden is quantified (quoting consumes daily time for 37% of home-service owners, admin/paperwork 31%, invoicing 28%, scheduling 27% — Jobber 2026 survey n=1,050, corroborated by EverCommerce + Housecall Pro); speed-to-lead gap (>70% of customers expect same-day response, only ~20% of pros reply within an hour, HVAC 11%); labor shortage as forcing function (86% of trades pros cite lack of qualified candidates; ~2.1M unfilled trades roles projected by 2030 — Housecall Pro / AGC / JLL). At the named-company level, eight metro-Atlanta firms were verified actively hiring $16–26/hr dispatcher/CSR roles whose posted duties (answer calls, book jobs, coordinate technician schedules, manage work orders) are exactly this pack's workflow surface. **Do not use** the widely-circulated missed-call ROI stats (ServiceTitan 10–15x phone-revenue multiple, Signpost $75K/yr loss, 85% no-callback) — all failed adversarial verification.

**Business value.** For the SMB: intake-to-dispatch coordination without adding dispatcher headcount; faster speed-to-lead; fewer dropped follow-ups (quotes, invoices, callbacks). For the partner: a workflow/execution layer their voice agent lacks, with an audit chain they can show the business owner ("every action the AI took, who it acted for, proof"). For Datacern: per-tenant/per-workflow revenue through the partner, plus the live proof-point for BRD 60.

**In scope.** Job intake execution from partner voice-agent intents, booking against FSM availability, dispatch/schedule proposals, appointment confirmations + reminders + "on-my-way" comms, quote follow-up sequences, invoice/AR follow-up (first-party dunning), FSM connectors (ServiceTitan, Housecall Pro, Jobber), owner-facing KPI dashboards, TCPA/quiet-hours comms guardrails, the risk-tiered auto-execute policy for external intents (the pack's one Core-adjacent change, specced in §3.8 and delivered under BRD 60).

**Out of scope.** Telephony/speech/conversation (the partner owns the voice layer — Datacern never answers a phone); technician routing optimization with GIS/traffic (v2); payments processing and refund execution (propose-only, never autonomous — see BR-2); marketing/lead-gen campaigns; commercial/construction field services (different workflows); payroll/HR.

## 2. Actors & user stories

**Personas:** Business Owner (SMB), Dispatcher/CSR (the human the pack augments), Technician, Office Manager (AR), Partner Integration Engineer (voice-agent company), Tenant Admin, Datacern Operator.

- **US-1** As a homeowner calling after hours, the partner's voice agent takes my AC-repair request; by the time I hang up, a real job exists in the company's FSM with a confirmed time window and I have an SMS confirmation — no human touched it.
- **US-2** As a Dispatcher, I open my board and see the overnight AI-booked jobs flagged `via_agent: <partner-bot>` with the call summary attached; I can re-slot any of them, and my manual bookings work exactly as before.
- **US-3** As a Dispatcher, when the schedule is over-committed the Dispatch Optimizer proposes a re-shuffle (who moves, why, customer impact); I approve it in the inbox and the confirmations go out automatically.
- **US-4** As an Office Manager, unpaid invoices get a polite, escalating follow-up sequence (day 3 SMS → day 10 email + payment link → day 20 owner-review case) without me tracking any of it; a customer reply pauses the sequence and opens a case for me.
- **US-5** As a Business Owner, I see speed-to-lead, booking rate, schedule utilization, and AR aging on one dashboard — and a ledger of every action the AI took on my behalf.
- **US-6** As a Business Owner, the AI can book jobs and send confirmations on its own, but anything touching money (refund, discount, cancellation fee waiver) lands in my approval inbox — and I chose where that line sits.
- **US-7** As a Partner Integration Engineer, I onboard a new SMB tenant with a self-service credential (BRD 60 WS2), point my voice agent at the propose/read surface, and pass certification (§9 AC-2) without Datacern staff in the loop.
- **US-8** As a homeowner who received a quote 4 days ago, I get one follow-up SMS; when I reply "yes", the job books itself into the next open slot and the quote converts in the FSM.
- **US-9** As a Technician, when I'm running late the status communicator notifies my next customer with a revised window before they call the office.

## 3. Functional requirements

### 3.1 Pack manifest (FSO-FR-001)

Standard pack.yaml v1 per BRD 23 §PKG-FR-001..007. Categories: `field-services, home-services, dispatch, scheduling, ar, partner-channel`. Regulatory: `tcpa, state_quiet_hours, first_party_collections, pii_consumer`. Clouds: aws, azure, gcp.

### 3.2 Ontology (FSO-FR-010)

`Customer`, `ServiceLocation` (property; a Customer has 1..n), `Job` (work order — the central entity, mirrors the FSM's job), `Appointment` (time window + assigned Technician), `Technician` (skills, service area, shift), `Estimate` (quote, with line items + expiry), `Invoice`, `Payment` (read-only mirror), `Interaction` (call/SMS/email touchpoint; partner voice-agent calls land here with transcript-summary + recording URL), `ServiceAgreement` (membership/maintenance plan), `FollowUpSequence` (state machine instance for quote/AR follow-ups).

Consumer-PII fields (name, address, phone, email) tagged `pii: true` — ai-gateway redaction boundary applies (no HIPAA; standard consumer-PII posture). **The FSM remains the system of record for Customer/Job/Appointment/Estimate/Invoice — Datacern holds a synced mirror + Datacern-native `Interaction` and `FollowUpSequence`.**

### 3.3 Semantic model — Field-services KPI catalog (FSO-FR-020)

| Measure | Definition |
|---|---|
| `speed_to_lead_p50` | median seconds from first inbound Interaction to booked Appointment or human handoff |
| `booking_rate` | count(Interactions → booked Job) / count(booking-intent Interactions) — 30d |
| `after_hours_capture` | count(booked Jobs from Interactions outside business hours) / count(after-hours booking-intent Interactions) |
| `schedule_utilization` | booked technician-hours / available technician-hours, per day + tech |
| `quote_conversion_rate` | count(Estimates → Jobs) / count(Estimates sent) — 60d, split by followed-up vs not |
| `days_to_invoice` | median days from Job completion to Invoice sent |
| `ar_aging` | open Invoice $ bucketed 0–30/31–60/61–90/90+ |
| `sequence_recovery_$` | sum(Invoice payments landing while a FollowUpSequence was active) |
| `ai_action_share` | count(auto-executed writes via_agent) / count(all writes) — the owner-trust metric |

### 3.4 Agents (FSO-FR-030..070) — 5, mixed-mode (see §3.8)

1. **Intake & Booking Agent (FSO-FR-030)** — the consumer of BRD 60 external intents. Graph: `receive_intent → dedupe_check → customer_match_or_create → availability_lookup → slot_selection → job_create → confirm_comms`. Tools: `fsm.customer.search`, `fsm.customer.create`, `fsm.availability.get`, `fsm.job.create`, `fsm.appointment.book`, `comms.sms.send`, `comms.email.send`. Triggered by `POST /external/v1/intents` from the partner's voice agent (booking-class intents) or by an inbound web-form/SMS Interaction. Emergency-class jobs (no water / no heat in winter / gas smell keywords) skip slot optimization, book the first slot, and page the on-call human.
2. **Dispatch & Schedule Optimizer (FSO-FR-040)** — proposal-mode. Twice-daily + on-trigger (cancellation, emergency insert): scores the day's board (skills match, drive proximity by zone, window risk) and proposes re-shuffles as a single reviewable diff. Never moves a job autonomously.
3. **Quote Follow-Up Agent (FSO-FR-050)** — runs `FollowUpSequence` per open Estimate: configurable cadence (default: day 2 SMS, day 5 email, stop). A customer reply routes to intent detection — "yes" → hands to Intake & Booking Agent; anything else → case for the Dispatcher. Hard cap: 3 touches per Estimate, ever.
4. **AR Follow-Up Agent (FSO-FR-060)** — first-party dunning on open Invoices: day 3 SMS with payment link, day 10 email, day 20 → `ar_escalation` case for the owner (never threatens, never engages third-party-collections language; see BR-6). Payment detected via FSM webhook/poll → sequence closes with a thank-you.
5. **Job Status Communicator (FSO-FR-070)** — deterministic + LLM-drafted copy: appointment reminders (T-24h, T-1h), on-my-way with revised ETA on technician delay, post-job review request (single touch, only after Invoice paid).

### 3.5 Connectors (FSO-FR-080)

**Read (3 FSM + 2 comms):** ServiceTitan API v2 (customers, jobs, appointments, technicians, estimates, invoices, payments), Housecall Pro API, Jobber GraphQL API; Twilio inbound-SMS webhook; SendGrid/SES inbound-email.
**Write adapters (per FSM, governed tools):** `customer.create`, `job.create`, `appointment.book/reschedule`, `estimate.update_status`, `invoice.send_reminder` — each registered in tool-plane with a risk tier (§3.8). No write adapter for payments or refunds in v1 (BR-2).
One FSM connector is **required at install**; the pack materializes only the selected FSM's toolset (per-tenant config).

### 3.6 Partner ingress (FSO-FR-090)

The partner's voice agent is a **registered external agent** (BRD 53 `origin=external`, BRD 60 WS2 credential): reads (availability, customer lookup) via the MCP gateway read-toolset scope; writes ONLY as intents via `POST /external/v1/intents`. The pack ships (a) an intent schema catalog for the vertical — `book_job.v1`, `reschedule.v1`, `cancel_request.v1`, `quote_accept.v1`, `callback_request.v1` — and (b) a partner certification suite (§9 AC-2). Every partner action carries `via_agent={partner_bot}` distinct from `actor` (the SMB tenant's service account or the on-behalf-of end customer) in the WORM chain.

### 3.7 KPI dashboards (FSO-FR-100)

Owner Overview (speed-to-lead, booking rate, `ai_action_share`) · Schedule Board Health · Quote Pipeline · AR Aging & Recovery · AI Action Ledger (every via_agent write, filterable, exportable). All Semantic-service compile.

### 3.8 Risk-tiered auto-execute for external intents (FSO-FR-110) — **the governance delta**

BRD 60 WS1 deliberately forces ALL external intents to pending proposals. That stance is correct for regulated SoR writes and wrong for "book Mrs. Johnson's AC repair" — no SMB owner will four-eyes every booking. This pack requires a **tenant-configurable auto-execute policy for external intents, allow-listed per tool + tier**, delivered as a BRD 60 workstream (proposed: WS6), not as pack-local code:

- Policy lives on the tenant (admin-set, default **empty** — propose-only remains the platform default; the pack install *offers* the vertical's recommended policy, the owner opts in).
- Auto-execute is grantable ONLY to tools tiered `write-low` (new tier: reversible, non-monetary — booking, reschedule ≥24h out, confirmation comms). `write-proposal` tier (refunds, discounts, fee waivers, same-day cancellation, anything monetary) can NEVER be auto-granted; attempting it fails config validation with `AUTO_EXECUTE_TIER_FORBIDDEN`.
- Auto-executed writes still ride the full pipeline: toolset allow-list, server-derived effect, rate limits, kill switch, `ai.tool_invoked.v1` + `ai.proposal.v1` (status `auto_executed`) WORM emits with `via_agent`. Auto-execute changes WHO approves (policy vs human), never WHAT is recorded.
- Per-tool daily auto-execute rate cap (default 50/day/tenant) — a runaway partner bot degrades to propose-only and alerts, it does not flood the FSM.

## 4. Domain model & data

Standard materialization via BRD 23 §PKG-FR-030 into: semantic-service (1 model, §3.3), chart-service (5 dashboards), case-service (4 schemas: `booking_exception` · `dispatch_review` · `ar_escalation` · `customer_reply_triage`), rbac-service (6 role seeds: `owner`, `dispatcher`, `office_manager`, `technician`, `partner_integration`, `tenant_admin`), guardrail-service (comms + auto-execute policies), agent-runtime (5 agent recipes), ingestion-service (FSM connector templates), tool-plane (~18 MCP tools across 3 FSM adapters), notification-service (SMS/email templates), bff-graphql (display_labels).

### Display labels (selected)

```yaml
locale: en
keys:
  job.singular:                "Job"
  booking_exception.singular:  "Booking exception"
  dispatch_review.singular:    "Schedule change"
  dispatch_review.action.approve: "Apply schedule"
  ar_escalation.singular:      "Overdue invoice"
  agent.intake_booking.name:   "Booking Agent"
  agent.dispatch_optimizer.name: "Dispatch Optimizer"
  agent.quote_followup.name:   "Quote Follow-Up"
  agent.ar_followup.name:      "Invoice Follow-Up"
  agent.status_comms.name:     "Status Updates"
entity_templates:
  customer: "Customer {name}"
  job:      "Job {job_number}"
```

## 5. Events

Emitted via installed components (no new topics): `case.created/resolved` per schema; `ai.proposal.v1` (incl. `auto_executed` status per §3.8); `ai.tool_invoked.v1`; `ai.token_usage.v1` with `decision_urn = job.urn`; `pack.install_completed`. Consumed: FSM webhooks (job/appointment/invoice/payment change → mirror sync + sequence triggers); `dataset.schema_changed` on connector-owned datasets.

## 6. Business rules & edge cases (FSO-BR-*)

- **BR-1** **The FSM is the system of record.** Datacern never holds a job state the FSM doesn't; every write goes through the FSM adapter with `Idempotency-Key`; on FSM write failure the intent becomes a `booking_exception` case — never a silent retry loop, never a Datacern-only "shadow booking".
- **BR-2** **Nothing monetary executes autonomously.** Refunds, discounts, fee waivers, payment-plan changes: proposal-mode, four-eyes, forever. Shipping a pack version that tiers a monetary tool `write-low` fails publish with `FSO_AUTONOMOUS_MONEY_FORBIDDEN` (mirrors BRD 26 §BR-1).
- **BR-3** **TCPA + quiet hours.** No outbound SMS/call-initiation outside 8am–9pm in the *customer's* timezone (state-configurable stricter windows); every SMS sequence honors STOP instantly and permanently; consent basis (existing-business-relationship) recorded per Interaction. Quiet-hours violations are blocked at the comms tool, not left to agent prompts.
- **BR-4** **Double-booking defense.** `dedupe_check` matches inbound intents on (phone, address, problem-class, 48h window); a probable duplicate becomes a `booking_exception` case, not a second job.
- **BR-5** **Emergency escape hatch.** Emergency-class intents (configurable keyword/class list) always ALSO page a human (owner/on-call) even when auto-booked — the AI books the slot, it never becomes the only party aware of a gas leak.
- **BR-6** **First-party collections only.** The AR agent is the business reminding its own customer: no third-party-collector representations, no threats, no credit-bureau language, tone-locked templates. Sequences stop permanently on dispute ("I already paid", "this is wrong") → `ar_escalation` case.
- **BR-7** **Reply always wins.** Any inbound customer reply pauses every active sequence for that customer until a human or the intake agent resolves the reply's intent.
- **BR-8** **Partner blast-radius.** Per-partner AND per-tenant rate caps at the ingress; a partner credential compromise is bounded by tenant scoping (a partner token for tenant A can never propose into tenant B) + the §3.8 daily caps + the existing kill switch per agent.
- **BR-9** **Reschedule boundary.** Auto-reschedule allowed only ≥24h before the window; inside 24h it is a proposal to the Dispatcher (customers hate same-day AI shuffles).
- **BR-10** **Owner visibility floor.** `ai_action_share` and the AI Action Ledger cannot be disabled — the trust product IS the audit trail; a partner cannot white-label it away.

## 7. Dependencies

Datacern Core (BRDs 01–23). **Hard:** BRD 60 WS1 (landed — external-intent ingress), WS2 (external credential — must land before partner self-service; manual token minting acceptable for pilot #1 only), the §3.8 auto-execute policy (proposed BRD 60 WS6, incl. the new `write-low` tier in tool-plane). **Soft:** BRD 60 WS4 (guardrail lift) before GA. External: ServiceTitan/Housecall Pro/Jobber APIs + sandbox accounts; Twilio; SendGrid/SES; one committed voice-agent partner with a sandbox bot.

## 8. NFRs (deltas from master)

| Metric | Target |
|---|---|
| Intent → booked job (auto-execute path), p95 | ≤ 15s |
| Intent → pending proposal (propose path), p95 | ≤ 5s |
| Availability read via gateway, p95 | ≤ 2s (voice agent is mid-call) |
| FSM mirror sync lag, p95 | ≤ 60s |
| Quiet-hours comms violations | 0 |
| Double-bookings created by the pack | 0 (exceptions become cases) |
| Speed-to-lead p50 (post-install, month 3) | ≤ 5 min on partner-handled channels |
| After-hours capture (post-install, month 3) | ≥ 60% |
| Partner onboarding of a new SMB tenant | ≤ 1 day, no Datacern staff |

## 9. Acceptance criteria

- **AC-1** Fresh install of `field-services-ops@0.1.0` with the Jobber connector materializes all components; 5 agents register in `mode: shadow`; `pack.install_completed` fires; ServiceTitan/Housecall toolsets are NOT materialized (per-tenant FSM selection works).
- **AC-2** **Partner certification (the demo):** a sandbox voice-agent posts `book_job.v1` for a new customer → customer + job + appointment exist in the Jobber sandbox, confirmation SMS rendered (sandbox), WORM chain shows `ai.proposal.v1(auto_executed)` with `via_agent=<partner-bot>` distinct from `actor`, and the AI Action Ledger displays the action — end-to-end ≤ 15s.
- **AC-3** Same intent with tenant auto-execute policy EMPTY → pending proposal in `/inbox`; Dispatcher approves → job lands in Jobber; nothing executed before approval.
- **AC-4** A `quote_accept.v1` intent referencing a monetary adjustment tool → `GUARDRAIL_VIOLATION`; tenant admin attempting to add a monetary tool to the auto-execute allow-list → `AUTO_EXECUTE_TIER_FORBIDDEN`.
- **AC-5** AR sequence on a sandbox overdue invoice: day-3 SMS and day-10 email render on schedule (clock-warped test); simulated customer reply "I already paid this" pauses the sequence and opens `ar_escalation`; simulated STOP halts all comms to that number permanently.
- **AC-6** Comms tool refuses an SMS at 9:30pm customer-local time (quiet hours) regardless of agent instruction; the refusal is audited.
- **AC-7** Duplicate booking intent (same phone/address/problem within 48h) → `booking_exception` case, no second job in the FSM.
- **AC-8** Partner rate-cap breach (51st auto-execute of the day) → degrade to propose-only + operator alert; no FSM write beyond the cap.
- **AC-9** Kill switch on the partner agent principal → in-flight and subsequent intents refused at the gateway; recovery is admin-explicit.
- **AC-10** **Pack installs cleanly on unmodified Core + BRD 60 WS1/WS2/WS6** (falsifiability test per `DATACERN_CORE_CAPABILITIES.md` §6 Test 1 — the pack carries no Core patches; if it needs one, the BRD 60 workstream was scoped wrong).

## 10. Out of scope / future

GIS/traffic-aware routing optimization; payments/refund execution (revisit only with a processor partnership + dedicated risk review); marketing campaigns and lead-gen; commercial field services; multi-FSM single-tenant (one FSM per tenant in v1); Datacern-as-FSM for greenfield SMBs (explicitly rejected for v1 — the wedge is executing into the incumbent SoR, not replacing it); additional verticals on the same skeleton (property-management maintenance intake is the natural second — same intents, different SoR connectors).
