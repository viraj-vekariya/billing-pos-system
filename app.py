from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, date
import sqlite3

from db import get_db

app = Flask(__name__)
app.secret_key = "dev-secret-key-not-for-production"


def generate_bill_number(conn):
    today_str = datetime.now().strftime("%Y%m%d")
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM bills WHERE bill_number LIKE ?", (f"INV-{today_str}-%",)
    ).fetchone()
    seq = row["c"] + 1
    return f"INV-{today_str}-{seq:03d}"


@app.route("/")
def index():
    return redirect(url_for("new_sale"))


# ---------- Products ----------

@app.route("/products")
def products():
    conn = get_db()
    rows = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
    conn.close()
    return render_template("products.html", products=rows)


@app.route("/products/new", methods=["GET", "POST"])
def new_product():
    if request.method == "POST":
        name = request.form["name"].strip()
        sku = request.form["sku"].strip()
        unit_price = float(request.form["unit_price"])
        tax_rate_percent = float(request.form["tax_rate_percent"])
        stock_quantity = int(request.form["stock_quantity"])

        if not name or not sku:
            flash("Name and SKU are required.")
            return redirect(url_for("new_product"))
        if unit_price < 0 or tax_rate_percent < 0 or stock_quantity < 0:
            flash("Price, tax rate and stock cannot be negative.")
            return redirect(url_for("new_product"))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO products (name, sku, unit_price, tax_rate_percent, stock_quantity) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, sku, unit_price, tax_rate_percent, stock_quantity),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            flash(f"SKU '{sku}' already exists.")
            conn.close()
            return redirect(url_for("new_product"))
        conn.close()
        return redirect(url_for("products"))

    return render_template("new_product.html")


@app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def edit_product(product_id):
    conn = get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        unit_price = float(request.form["unit_price"])
        tax_rate_percent = float(request.form["tax_rate_percent"])
        stock_quantity = int(request.form["stock_quantity"])
        conn.execute(
            "UPDATE products SET name = ?, unit_price = ?, tax_rate_percent = ?, stock_quantity = ? "
            "WHERE id = ?",
            (name, unit_price, tax_rate_percent, stock_quantity, product_id),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("products"))

    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    if product is None:
        flash("Product not found.")
        return redirect(url_for("products"))
    return render_template("edit_product.html", product=product)


# ---------- New sale / cart / checkout ----------

@app.route("/sale")
def new_sale():
    conn = get_db()
    rows = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
    conn.close()
    return render_template("new_sale.html", products=rows)


@app.route("/api/products/lookup")
def api_product_lookup():
    """Used by the cart JS to fetch live price/tax/stock for a product id."""
    product_id = request.args.get("id", type=int)
    conn = get_db()
    p = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    if p is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(p))


def compute_totals(cart_lines, discount_type, discount_value):
    """cart_lines: list of dicts with quantity, unit_price, tax_rate_percent.

    Tax is computed per line at that line's own product's tax rate, then summed -
    a single flat rate across the whole subtotal would be wrong whenever products
    in the same cart have different tax rates.
    """
    subtotal = 0.0
    for line in cart_lines:
        line["line_subtotal"] = round(line["quantity"] * line["unit_price"], 2)
        subtotal += line["line_subtotal"]
    subtotal = round(subtotal, 2)

    if discount_type == "percent":
        discount_amount = round(subtotal * discount_value / 100, 2)
    elif discount_type == "flat":
        discount_amount = round(min(discount_value, subtotal), 2)
    else:
        discount_amount = 0.0

    taxable_amount = round(subtotal - discount_amount, 2)
    # discount is applied proportionally to each line before tax, so each
    # line's tax reflects the discounted price it actually sold at
    discount_ratio = (discount_amount / subtotal) if subtotal > 0 else 0.0

    tax_amount = 0.0
    for line in cart_lines:
        line_taxable = round(line["line_subtotal"] * (1 - discount_ratio), 2)
        line["line_tax"] = round(line_taxable * line["tax_rate_percent"] / 100, 2)
        line["line_total"] = round(line_taxable + line["line_tax"], 2)
        tax_amount += line["line_tax"]
    tax_amount = round(tax_amount, 2)

    grand_total = round(taxable_amount + tax_amount, 2)
    return {
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "taxable_amount": taxable_amount,
        "tax_amount": tax_amount,
        "grand_total": grand_total,
    }


@app.route("/checkout", methods=["POST"])
def checkout():
    data = request.get_json()
    items = data.get("items", [])
    discount_type = data.get("discount_type", "none")
    discount_value = float(data.get("discount_value", 0) or 0)
    payment_method = data.get("payment_method", "cash")

    if not items:
        return jsonify({"error": "Cart is empty."}), 400

    conn = get_db()
    cart_lines = []
    for item in items:
        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (item["product_id"],)
        ).fetchone()
        if product is None:
            conn.close()
            return jsonify({"error": f"Product {item['product_id']} not found."}), 400
        quantity = int(item["quantity"])
        if quantity <= 0:
            conn.close()
            return jsonify({"error": "Quantity must be positive."}), 400
        # reject the whole sale on the first insufficient-stock line found -
        # nothing gets written to the DB until every line has been checked,
        # so a sale never partially commits stock changes
        if quantity > product["stock_quantity"]:
            conn.close()
            return jsonify({
                "error": f"Insufficient stock for {product['name']}: "
                         f"requested {quantity}, available {product['stock_quantity']}."
            }), 409
        cart_lines.append({
            "product_id": product["id"],
            "product_name": product["name"],
            "sku": product["sku"],
            "quantity": quantity,
            "unit_price": product["unit_price"],
            "tax_rate_percent": product["tax_rate_percent"],
        })

    totals = compute_totals(cart_lines, discount_type, discount_value)

    bill_number = generate_bill_number(conn)
    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO bills (bill_number, created_at, subtotal, discount_type, discount_value, "
            "discount_amount, taxable_amount, tax_amount, grand_total, payment_method) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bill_number, datetime.now().isoformat(timespec="seconds"), totals["subtotal"],
             discount_type, discount_value, totals["discount_amount"], totals["taxable_amount"],
             totals["tax_amount"], totals["grand_total"], payment_method),
        )
        bill_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        for line in cart_lines:
            conn.execute(
                "INSERT INTO bill_items (bill_id, product_id, product_name, sku, quantity, "
                "unit_price_at_sale, tax_rate_at_sale, line_subtotal, line_tax, line_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (bill_id, line["product_id"], line["product_name"], line["sku"], line["quantity"],
                 line["unit_price"], line["tax_rate_percent"], line["line_subtotal"],
                 line["line_tax"], line["line_total"]),
            )
            conn.execute(
                "UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?",
                (line["quantity"], line["product_id"]),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": f"Checkout failed: {e}"}), 500

    conn.close()
    return jsonify({"bill_id": bill_id})


@app.route("/receipt/<int:bill_id>")
def receipt(bill_id):
    conn = get_db()
    bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if bill is None:
        conn.close()
        flash("Bill not found.")
        return redirect(url_for("new_sale"))
    items = conn.execute(
        "SELECT * FROM bill_items WHERE bill_id = ? ORDER BY id", (bill_id,)
    ).fetchall()
    conn.close()
    return render_template("receipt.html", bill=bill, items=items)


# ---------- Returns / refunds ----------

def already_returned_quantity(conn, bill_item_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS qty FROM return_items WHERE bill_item_id = ?",
        (bill_item_id,),
    ).fetchone()
    return row["qty"]


def returnable_bill_items(conn, bill_id):
    """Bill items for this bill annotated with how much of each is still returnable."""
    items = conn.execute(
        "SELECT * FROM bill_items WHERE bill_id = ? ORDER BY id", (bill_id,)
    ).fetchall()
    result = []
    for item in items:
        returned = already_returned_quantity(conn, item["id"])
        result.append({
            "bill_item": item,
            "already_returned": returned,
            "returnable_qty": item["quantity"] - returned,
        })
    return result


@app.route("/returns/<int:bill_id>", methods=["GET"])
def new_return(bill_id):
    conn = get_db()
    bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if bill is None:
        conn.close()
        flash("Bill not found.")
        return redirect(url_for("history"))
    lines = returnable_bill_items(conn, bill_id)
    conn.close()
    return render_template("new_return.html", bill=bill, lines=lines)


@app.route("/returns/<int:bill_id>", methods=["POST"])
def process_return(bill_id):
    """Process a full or partial return against an existing bill.

    Refund per unit is derived from the bill_item's own line_total / quantity -
    that figure already has the bill's discount and tax baked in proportionally
    (see compute_totals), so refunding off it automatically refunds at the price
    the customer actually paid rather than at the undiscounted list price.
    """
    data = request.get_json()
    requested_lines = data.get("items", [])
    reason = (data.get("reason") or "").strip() or None

    if not requested_lines:
        return jsonify({"error": "No items selected to return."}), 400

    conn = get_db()
    bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if bill is None:
        conn.close()
        return jsonify({"error": "Bill not found."}), 404

    return_lines = []
    for req in requested_lines:
        bill_item_id = int(req["bill_item_id"])
        quantity = int(req["quantity"])
        if quantity <= 0:
            conn.close()
            return jsonify({"error": "Return quantity must be positive."}), 400

        bill_item = conn.execute(
            "SELECT * FROM bill_items WHERE id = ? AND bill_id = ?", (bill_item_id, bill_id)
        ).fetchone()
        if bill_item is None:
            conn.close()
            return jsonify({"error": f"Bill item {bill_item_id} not found on this bill."}), 400

        returned_so_far = already_returned_quantity(conn, bill_item_id)
        returnable = bill_item["quantity"] - returned_so_far
        if quantity > returnable:
            conn.close()
            return jsonify({
                "error": f"Cannot return {quantity} of '{bill_item['product_name']}': "
                         f"only {returnable} remaining returnable "
                         f"(purchased {bill_item['quantity']}, already returned {returned_so_far})."
            }), 409

        refund = round(bill_item["line_total"] * quantity / bill_item["quantity"], 2)
        return_lines.append({
            "bill_item_id": bill_item_id,
            "product_id": bill_item["product_id"],
            "quantity": quantity,
            "refund_amount": refund,
        })

    total_refund = round(sum(line["refund_amount"] for line in return_lines), 2)

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO returns (bill_id, created_at, reason, refund_amount) VALUES (?, ?, ?, ?)",
            (bill_id, datetime.now().isoformat(timespec="seconds"), reason, total_refund),
        )
        return_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        for line in return_lines:
            conn.execute(
                "INSERT INTO return_items (return_id, bill_item_id, product_id, quantity, refund_amount) "
                "VALUES (?, ?, ?, ?, ?)",
                (return_id, line["bill_item_id"], line["product_id"], line["quantity"],
                 line["refund_amount"]),
            )
            conn.execute(
                "UPDATE products SET stock_quantity = stock_quantity + ? WHERE id = ?",
                (line["quantity"], line["product_id"]),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": f"Return failed: {e}"}), 500

    conn.close()
    return jsonify({"return_id": return_id, "refund_amount": total_refund})


@app.route("/history")
def history():
    conn = get_db()
    bills = conn.execute("SELECT * FROM bills ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("history.html", bills=bills)


@app.route("/reports")
def reports():
    conn = get_db()

    report_date = request.args.get("date", date.today().isoformat())

    day_row = conn.execute(
        "SELECT COUNT(*) AS bill_count, COALESCE(SUM(grand_total), 0) AS revenue "
        "FROM bills WHERE substr(created_at, 1, 10) = ?",
        (report_date,),
    ).fetchone()

    top_by_qty = conn.execute(
        "SELECT product_name, sku, SUM(quantity) AS total_qty, SUM(line_total) AS total_revenue "
        "FROM bill_items GROUP BY product_id ORDER BY total_qty DESC LIMIT 5"
    ).fetchall()

    top_by_revenue = conn.execute(
        "SELECT product_name, sku, SUM(quantity) AS total_qty, SUM(line_total) AS total_revenue "
        "FROM bill_items GROUP BY product_id ORDER BY total_revenue DESC LIMIT 5"
    ).fetchall()

    overall = conn.execute(
        "SELECT COUNT(*) AS bill_count, COALESCE(SUM(grand_total), 0) AS revenue FROM bills"
    ).fetchone()

    conn.close()
    return render_template(
        "reports.html",
        report_date=report_date,
        day_row=day_row,
        top_by_qty=top_by_qty,
        top_by_revenue=top_by_revenue,
        overall=overall,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5050)
