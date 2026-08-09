from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime
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
    conn.commit()
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


if __name__ == "__main__":
    app.run(debug=True, port=5050)
