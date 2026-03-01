# Carrier Contract Commitment Awareness — Design

**Date:** 2026-03-02
**Status:** Approved
**Module:** `mml_freight`

---

## Problem

MML commits to volume and rate agreements with freight carriers (e.g., 20 TEU/year with DSV at $2,500/TEU). The current tender system selects quotes purely on market rates with no awareness of these contractual obligations. This means:

- No tracking of how much committed volume has been consumed
- No visibility into whether the contracted rate is beating or trailing the market
- No mechanism to preferentially route bookings to contracted carriers to honour commitments

---

## Solution

Add a `freight.carrier.contract` model to `mml_freight`, a `contract_aware` selection mode on `freight.tender`, opportunity cost fields on `freight.tender.quote`, and utilization tracking on `freight.booking`.

---

## Data Model

### `freight.carrier.contract` (new)

| Field | Type | Notes |
|---|---|---|
| `name` | Char | e.g. "DSV 2026 FCL Agreement" |
| `carrier_id` | Many2one `delivery.carrier` | Required |
| `date_start` | Date | Contract period start |
| `date_end` | Date | Contract period end |
| `commitment_unit` | Selection | `teu` / `weight_kg` / `shipment_count` |
| `committed_quantity` | Float | e.g. 20.0 |
| `contracted_rate` | Monetary | Rate per unit |
| `contracted_rate_currency_id` | Many2one `res.currency` | |
| `notes` | Text | |

**Computed fields:**

| Field | Computed from |
|---|---|
| `utilized_quantity` | Sum of `unit_quantity` on confirmed `freight.booking` records for this carrier within the contract period |
| `remaining_quantity` | `committed_quantity − utilized_quantity` |
| `utilization_pct` | `utilized / committed × 100` |
| `is_active` | `date_start ≤ today ≤ date_end` |

A contract record **is** the activation switch. No separate on/off toggle. If no active contract exists for a carrier, that carrier competes on market rates as normal. Expired or future-dated contracts are ignored.

### `freight.booking` extensions

| Field | Type | Notes |
|---|---|---|
| `contract_id` | Many2one `freight.carrier.contract` | Set at booking time if an active contract applies |
| `unit_quantity` | Float | Units consumed against the contract |
| `unit_type` | Selection | Mirrors `commitment_unit` from contract |

**Unit quantity population rules (at booking time):**
- **Sea FCL (`teu`)** — derived from tender package lines; 20ft container = 1 TEU, 40ft = 2 TEU
- **Air (`weight_kg`)** — `chargeable_weight_kg` from the tender
- **Road / express (`shipment_count`)** — 1 per booking

---

## Quote Enhancement

New computed fields on `freight.tender.quote`:

| Field | Type | Notes |
|---|---|---|
| `contract_id` | Many2one (computed) | Active contract for this carrier, if any |
| `is_contract_carrier` | Boolean (computed) | True when active contract with remaining commitment > 0 |
| `contract_remaining_qty` | Float (computed) | Remaining commitment at quote time |
| `contracted_rate_total_nzd` | Float (computed) | `contracted_rate × unit_quantity` converted to NZD |
| `opportunity_cost_nzd` | Float (computed) | `contracted_rate_total_nzd − total_rate_nzd` |

**Opportunity cost sign convention:**
- **Positive** → contract costs more than market (we are overpaying vs spot)
- **Negative** → contract is cheaper than market (the deal is paying off)

---

## `contract_aware` Selection Mode

Added to `SELECTION_MODES` on `freight.tender` alongside `cheapest`, `fastest`, `best_value`, `manual`.

### Decision tree in `action_auto_select`:

```
1. Collect all received quotes.

2. Identify contract candidates:
   quotes where is_contract_carrier = True
   (active contract + remaining_quantity > 0)

3. No contract candidates:
   → Select cheapest market quote.
   → Post chatter warning:
     "No contract commitment remaining — selected cheapest market rate.
      [Carrier]: [N] remaining of [M] committed."

4. One contract candidate:
   → Select it.

5. Multiple contract candidates:
   → Select lowest contracted_rate_total_nzd
     (fill best-value contract first).

6. Post-selection (all cases where contract carrier selected):
   → Compute opportunity_cost_nzd vs cheapest market quote.
   → If opportunity_cost_nzd > 0:
       Post chatter alert:
       "Contract carrier selected ([Carrier] [Mode]).
        Opportunity cost vs cheapest market: +$X NZD.
        Contract utilisation: Y of Z [unit] (N%). Review if deviation warranted."
       Set tender.has_opportunity_cost_alert = True
   → If opportunity_cost_nzd ≤ 0:
       Post chatter:
       "Contract carrier selected ([Carrier] [Mode]).
        Contract rate beats market by $X NZD.
        Contract utilisation: Y of Z [unit] (N%)."
```

No auto-override threshold. If the market is cheaper, the system flags it — a human decides whether to deviate from the contract. Threshold-based auto-override is deferred pending scale review.

### New fields on `freight.tender`:

| Field | Type | Notes |
|---|---|---|
| `has_opportunity_cost_alert` | Boolean | Set when selected contract quote costs more than cheapest market |
| `opportunity_cost_nzd` | Float | Delta amount, stored for reporting |

---

## Utilization Tracking

Utilization is computed live from `freight.booking` records — no separate tracking table.

```
utilized_quantity = SUM(booking.unit_quantity)
WHERE booking.contract_id = this_contract
AND booking.state IN ('confirmed', 'in_transit', 'delivered')
```

Cancellations are automatically excluded. Retroactive and always accurate.

**Weekly cron — commitment pace alert:**
Scan active contracts where `utilization_pct < 50` and `days_remaining < 90`. Post a chatter message on the contract record:
> "DSV 2026 FCL: 6 of 20 TEU used, 88 days remaining. At current pace you will fall short of committed volume."

---

## UI

### Carrier form (`delivery.carrier`)
- New **Contracts** tab: tree of all contracts for that carrier (name, period, committed, utilized, remaining, status).

### `freight.carrier.contract` form view
- Header: name, carrier, period, status badge (Active / Upcoming / Expired)
- Left: commitment terms (unit, quantity, contracted rate, currency)
- Right: live utilization progress bar (`Y / Z [unit] — N%`), remaining, pace indicator
- Bottom: linked bookings list

### `freight.carrier.contract` list view
Menu: `MML Operations → Freight → Carrier Contracts`

Columns: carrier, period, committed, utilized, remaining, utilization %, opportunity cost YTD

Color coding:
- Green: utilization < 80%
- Amber: 80–100%
- Red: > 100% (over-committed)

### Tender form — quotes tab
Additional columns: `CONTRACT` badge, `contract_remaining_qty`, `opportunity_cost_nzd`

Banner on tender form when `has_opportunity_cost_alert = True`:
> "Opportunity cost alert: contract carrier selected above market rate. See quotes for detail."

---

## Scope Boundaries

- **In scope:** `freight.carrier.contract` model, booking unit tracking, `contract_aware` selection mode, opportunity cost on quotes, utilization cron alert, UI views
- **Out of scope (deferred):** Auto-override threshold, penalty/shortfall tracking, multi-contract blending (split a tender across two carriers to fill two commitments), contract renewal workflow
- **Multi-mode:** Model supports `teu`, `weight_kg`, `shipment_count` from day one; current implementation focus is sea FCL (`teu`)
