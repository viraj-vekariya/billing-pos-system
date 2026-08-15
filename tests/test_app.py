import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module
import app as app_module
from app import compute_totals


@pytest.fixture
def client():
    fd, path = tempfile.mkstemp()
    db_module.DB_PATH = path
    db_module.init_db()
    app_module.app.config["TESTING"] = True

    with app_module.app.test_client() as client:
        yield client

    os.close(fd)
    os.unlink(path)


def add_product(client, name="Widget", sku="WID-1", unit_price=10.0,
                 tax_rate_percent=18.0, stock_quantity=50):
    return client.post("/products/new", data={
        "name": name,
        "sku": sku,
        "unit_price": str(unit_price),
        "tax_rate_percent": str(tax_rate_percent),
        "stock_quantity": str(stock_quantity),
    }, follow_redirects=True)


def get_product_id_by_sku(sku):
    conn = db_module.get_db()
    row = conn.execute("SELECT id FROM products WHERE sku = ?", (sku,)).fetchone()
    conn.close()
    return row["id"]


def checkout(client, items, discount_type="none", discount_value=0, payment_method="cash"):
    return client.post(
        "/checkout",
        data=json.dumps({
            "items": items,
            "discount_type": discount_type,
            "discount_value": discount_value,
            "payment_method": payment_method,
        }),
        content_type="application/json",
    )


class TestComputeTotals:
    def test_single_line_no_discount(self):
        lines = [{"quantity": 2, "unit_price": 10.0, "tax_rate_percent": 18.0}]
        totals = compute_totals(lines, "none", 0)
        assert totals["subtotal"] == 20.0
        assert totals["discount_amount"] == 0.0
        assert totals["taxable_amount"] == 20.0
        assert totals["tax_amount"] == 3.6
        assert totals["grand_total"] == 23.6

    def test_percent_discount_applied_before_tax(self):
        lines = [{"quantity": 1, "unit_price": 100.0, "tax_rate_percent": 10.0}]
        totals = compute_totals(lines, "percent", 10)
        assert totals["discount_amount"] == 10.0
        assert totals["taxable_amount"] == 90.0
        assert totals["tax_amount"] == 9.0
        assert totals["grand_total"] == 99.0

    def test_flat_discount_capped_at_subtotal(self):
        lines = [{"quantity": 1, "unit_price": 20.0, "tax_rate_percent": 0.0}]
        totals = compute_totals(lines, "flat", 500)
        assert totals["discount_amount"] == 20.0
        assert totals["taxable_amount"] == 0.0
        assert totals["grand_total"] == 0.0

    def test_per_line_tax_rates_are_not_flattened(self):
        lines = [
            {"quantity": 1, "unit_price": 100.0, "tax_rate_percent": 0.0},
            {"quantity": 1, "unit_price": 100.0, "tax_rate_percent": 20.0},
        ]
        totals = compute_totals(lines, "none", 0)
        assert totals["subtotal"] == 200.0
        assert totals["tax_amount"] == 20.0
        assert lines[0]["line_tax"] == 0.0
        assert lines[1]["line_tax"] == 20.0

    def test_discount_distributed_proportionally_across_lines(self):
        lines = [
            {"quantity": 1, "unit_price": 100.0, "tax_rate_percent": 10.0},
            {"quantity": 1, "unit_price": 300.0, "tax_rate_percent": 10.0},
        ]
        totals = compute_totals(lines, "percent", 50)
        assert totals["discount_amount"] == 200.0
        assert totals["tax_amount"] == pytest.approx(20.0, abs=0.01)
        assert totals["grand_total"] == pytest.approx(220.0, abs=0.01)

    def test_empty_cart_has_zero_totals(self):
        totals = compute_totals([], "none", 0)
        assert totals == {
            "subtotal": 0.0,
            "discount_amount": 0.0,
            "taxable_amount": 0.0,
            "tax_amount": 0.0,
            "grand_total": 0.0,
        }


class TestProductRoutes:
    def test_new_product_persists(self, client):
        add_product(client, name="Notebook", sku="NB-1")
        response = client.get("/products")
        assert b"Notebook" in response.data

    def test_duplicate_sku_rejected(self, client):
        add_product(client, sku="DUP-1")
        response = add_product(client, sku="DUP-1")
        assert b"already exists" in response.data

    def test_negative_price_rejected(self, client):
        response = add_product(client, sku="NEG-1", unit_price=-5)
        assert b"cannot be negative" in response.data


class TestCheckout:
    def test_empty_cart_rejected(self, client):
        response = checkout(client, [])
        assert response.status_code == 400

    def test_unknown_product_rejected(self, client):
        response = checkout(client, [{"product_id": 9999, "quantity": 1}])
        assert response.status_code == 400

    def test_insufficient_stock_rejected(self, client):
        add_product(client, sku="LOW-1", stock_quantity=2)
        product_id = get_product_id_by_sku("LOW-1")
        response = checkout(client, [{"product_id": product_id, "quantity": 5}])
        assert response.status_code == 409

    def test_successful_checkout_decrements_stock_and_creates_bill(self, client):
        add_product(client, sku="SALE-1", unit_price=50.0, tax_rate_percent=10.0, stock_quantity=10)
        product_id = get_product_id_by_sku("SALE-1")

        response = checkout(client, [{"product_id": product_id, "quantity": 3}])
        assert response.status_code == 200
        bill_id = response.get_json()["bill_id"]

        conn = db_module.get_db()
        product = conn.execute("SELECT stock_quantity FROM products WHERE id = ?", (product_id,)).fetchone()
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        items = conn.execute("SELECT * FROM bill_items WHERE bill_id = ?", (bill_id,)).fetchall()
        conn.close()

        assert product["stock_quantity"] == 7
        assert bill["grand_total"] == pytest.approx(165.0, abs=0.01)
        assert len(items) == 1
        assert items[0]["quantity"] == 3

    def test_failed_checkout_leaves_stock_untouched(self, client):
        add_product(client, sku="ATOMIC-1", stock_quantity=5)
        add_product(client, sku="ATOMIC-2", stock_quantity=1)
        p1 = get_product_id_by_sku("ATOMIC-1")
        p2 = get_product_id_by_sku("ATOMIC-2")

        response = checkout(client, [
            {"product_id": p1, "quantity": 3},
            {"product_id": p2, "quantity": 10},
        ])
        assert response.status_code == 409

        conn = db_module.get_db()
        stock = conn.execute("SELECT stock_quantity FROM products WHERE id = ?", (p1,)).fetchone()
        conn.close()
        assert stock["stock_quantity"] == 5
