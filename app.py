from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3

from db import get_db

app = Flask(__name__)
app.secret_key = "dev-secret-key-not-for-production"


@app.route("/")
def index():
    return redirect(url_for("products"))


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


if __name__ == "__main__":
    app.run(debug=True, port=5050)
