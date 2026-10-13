# Nile Bites restaurant data

`nile_bites.json` is a **fictional** restaurant invented for this research
instrument. Nile Bites does not exist, the menu is not a real menu, and no fact
in this file describes any actual business. It exists so the agent's answers can
be checked against a single deterministic source instead of against the
researcher's memory.

## What is in it

| Key | Contents |
| --- | --- |
| `meta` | Marks the data as fictional, including a `research_valid: false` flag |
| `categories` | 8 menu categories |
| `customization_groups` | 6 option groups, with single-select and price rules |
| `menu` | 40 items, each with English and Egyptian Arabic names, price, allergens, prep time and an availability flag |
| `unavailable_reasons` | Why specific items are unavailable, with the alternatives to offer instead |
| `delivery` | 6 zones with fees, minimums and ETAs, plus the general free-delivery threshold |
| `business_hours` | Opening hours for all seven days |
| `promotions` | 5 promotions, none stackable |
| `order_rules` | Limits the engine enforces, e.g. max lines per order |

Prices are whole EGP. Every item carries both an English and an Egyptian Arabic
name, because the reference resolver has to match what a bilingual speaker
actually says rather than only what the menu says in one language.

## Rules the engine reads from here

The data is not decoration; the deterministic layer reads it, so a change here
changes what the agent is allowed to claim.

- **Promotions are not stackable.** At most one applies per order, and the
  winner is the first one in file order whose conditions are all met.
- **Promotions can be gated on a day**, on a minimum subtotal, on a minimum
  quantity of an item, on the delivery zone, or on `requires_mention`, which
  means the customer has to state something first. `PROM-03` is the gated one:
  it only applies once the order carries a student ID, so the agent has to ask.
  A requirement with no implementation fails closed and never grants the
  promotion.
- **Availability is data, not a guess.** Three items are unavailable, each with
  a reason and named alternatives, which is what makes the refusal tasks
  checkable.
- **Delivery is limited to the listed zones.** Anything else is pickup only, and
  the correct behaviour is to refuse delivery and offer pickup.

## Editing it

This is development data for a fictional business, so editing it is fine. Two
things to keep in mind:

1. The expected totals in `../tasks/tasks.yaml` are replayed against this file
   by `python scripts/verify_task_totals.py` and by the test suite. Change a
   price here and those expectations will disagree, which is the intended
   alarm, not a bug to silence.
2. The weekday matters. Every promotion except `PROM-02` and `PROM-05` is tied
   to a day, so a task that asserts an exact total has to pin its weekday or
   the expectation breaks on a different day.
