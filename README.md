# Billing / POS System

A small point-of-sale system for a single retail counter: manage products, ring up a sale, print a receipt, and check sales reports. Built with Flask, raw `sqlite3` (no ORM), and server-rendered Jinja2 templates.

## Stack

- Flask (routing, templating, JSON checkout endpoint)
- `sqlite3` standard library module — hand-written SQL, no ORM
- Jinja2 templates, plain CSS, a small amount of vanilla JS for the cart-building screen

## Running it

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

python scripts/seed.py          # wipes/creates instance/pos.db, adds sample products
python app.py                   # http://127.0.0.1:5050
```

`scripts/seed.py` calls `init_db()`, which drops and recreates all tables from `schema.sql` — running it again always gives you a clean, known database.

## Schema

**products**
| column | notes |
|---|---|
| id | PK |
| name | |
| sku | unique |
| unit_price | REAL |
| tax_rate_percent | REAL, per-product |
| stock_quantity | INTEGER |

**bills** — one row per finalized sale, holding the computed totals (`subtotal`, `discount_amount`, `taxable_amount`, `tax_amount`, `grand_total`) plus `bill_number`, `created_at`, `payment_method`.

**bill_items** — one row per line item. Product name, SKU, unit price and tax rate are copied onto the row at sale time (`unit_price_at_sale`, `tax_rate_at_sale`) instead of joined live from `products`, so a receipt for a bill from last month still shows the price and tax rate that actually applied that day, even if the product's price or tax rate has since changed.

## The billing math

This is the part of the app worth being able to explain line by line.

For each cart line: `line_subtotal = quantity * unit_price`.

```
subtotal          = sum of all line_subtotal
discount_amount    = subtotal * discount% / 100        (percent discount)
                    = min(discount_flat_value, subtotal) (flat discount)
taxable_amount     = subtotal - discount_amount
```

**Tax is computed per line, at that line's own product's tax rate, not as one flat rate over the whole cart.** A cart with a 5%-taxed loaf of bread and an 18%-taxed pair of earphones has two different correct tax amounts on those two lines; summing them is the only way to get the right total. If a discount is active, it's applied proportionally to each line first (`discount_ratio = discount_amount / subtotal`, applied to every line so a 10%-off sale takes 10% off every line, not just the first one), tax is then computed on that discounted line amount, and the line's own `line_tax` and `line_total` are stored on `bill_items` next to it — so a receipt can be reconstructed accurately without recomputing from `products`.

```
tax_amount   = sum of each line's (line_subtotal after discount) * tax_rate_percent / 100
grand_total  = taxable_amount + tax_amount
```

All money math rounds to 2 decimal places at each step (per line, then again at the totals) to avoid float drift accumulating across many line items — this matches how a real receipt rounds each printed number rather than carrying full float precision to the end and rounding once.

### Worked example (this is what `python scripts/seed.py` + a real checkout in this repo actually produced)

Cart: 3x Bread (₹45.00, 5% tax), 2x Notebook (₹55.00, 12% tax), 1x Earphones (₹899.00, 18% tax), no discount.

```
line 1: 3 * 45.00  = 135.00, tax = 135.00 * 0.05 = 6.75,   line_total = 141.75
line 2: 2 * 55.00  = 110.00, tax = 110.00 * 0.12 = 13.20,  line_total = 123.20
line 3: 1 * 899.00 = 899.00, tax = 899.00 * 0.18 = 161.82, line_total = 1060.82

subtotal    = 135.00 + 110.00 + 899.00 = 1144.00
tax_amount  = 6.75 + 13.20 + 161.82    = 181.77
grand_total = 1144.00 + 181.77         = 1325.77
```

The app's own `bills` row for that sale: `subtotal=1144.0, tax_amount=181.77, grand_total=1325.77`. Matches exactly.

A second sale with a 10% discount applied (4x Milk ₹60/5%, 2x Pens ₹80/12%, 3x USB Cable ₹149/18%): `subtotal=847.00`, `discount_amount=84.70`, `taxable_amount=762.30`, `tax_amount=100.49`, `grand_total=862.79` — also verified against the app's stored row, exact match.

## The atomic-sale rule

When a bill is finalized, every requested line's `quantity` is checked against that product's current `stock_quantity` **before anything is written to the database**. If any single line requests more than is in stock, the entire checkout is rejected with an error naming the offending product, and no bill, no bill_items, and no stock changes are written for *any* line in that cart — including the lines that would have been valid on their own.

This matters because a POS silently fulfilling half a sale (charging for 5 items when only 3 items across two products were actually in stock) is worse than just rejecting the sale outright: the cashier would have to manually work out and reverse a partial transaction at the till instead of just re-checking stock and trying again. Checking happens up front in a validation pass over the whole cart, and only after every line passes does the code open a transaction, insert the bill and its line items, and decrement `stock_quantity` for each product — so there's never a window where a bill exists with mismatched stock.

## Features

- Product catalogue (add/edit, per-product tax rate and stock)
- New sale screen: pick a product + quantity, build a cart client-side, remove lines before checkout, apply an optional flat or percent discount, choose a payment method
- Checkout: server recomputes and validates everything (never trusts the client's displayed totals), decrements stock, generates a bill number (`INV-YYYYMMDD-NNN`)
- Receipt view: printable HTML page (browser print / "save as PDF" works fine — no PDF library used)
- Returns/refunds: full or partial return against any past bill, restocks the returned quantity and refunds proportionally to what was actually charged
- Sales history: list of all past bills, with a link to process a return against each
- Reports: revenue + bill count for a chosen day, all-time totals, top-5 products by quantity sold and by revenue

## Returns/refunds

A return is processed against a specific bill's line items (`/returns/<bill_id>`), not against the bill as a whole — you can return 2 of the 5 units on a line and leave the other 3 sold. Each return is its own row in a `returns`/`return_items` table; the original `bill`/`bill_items` rows are never mutated, so a receipt always shows exactly what was charged at sale time.

**Refund amount comes from the bill item's own `line_total`, not from re-deriving a price.** `line_total` was already computed at checkout with that line's proportional share of any cart-wide discount and its own tax rate baked in (see the billing math above), so `refund = line_total * returned_qty / original_qty` automatically refunds at the price the customer actually paid — a return on a bill with a 10% discount refunds 10% less than the same return would on a full-price bill, without the returns code needing to know anything about discounts itself.

Validation before any write: the bill item must belong to the bill being returned against, the requested quantity must be positive, and it can't exceed `original_qty - already_returned` (summed live from prior `return_items` rows) — so a line can be returned across multiple partial returns but never refunded twice for the same unit. A valid return inserts the `returns`/`return_items` rows and restocks each product's `stock_quantity` inside a single transaction, same atomicity pattern as checkout.

## Testing

`compute_totals` (per-line tax, proportional discounts, rounding), the product/checkout routes (duplicate SKU, negative price, insufficient stock, atomic-sale rollback), and returns (full/partial refund amount, discount-proportional refund, restock, double-return and over-return rejection) are covered with pytest, run against a temporary SQLite file so it never touches `instance/pos.db`.

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Limitations (honestly)

- Single till / single user — no login, no cashier accounts, no concurrent-register handling beyond SQLite's own transaction locking
- Returns have no reason-code enforcement or approval workflow (a free-text reason is optional, not required or validated), no refund-to-original-payment-method tracking, and no returns line on the Reports page yet
- No cash drawer, printer, or barcode scanner hardware integration; "receipt" is an HTML page, not a real print job or PDF
- No multi-currency, no per-customer accounts, no loyalty/coupons beyond the one discount applied at checkout
- Discount is cart-wide only, not itemized per product
- Dev Flask server only (`app.run(debug=True)`) — not configured for production deployment
