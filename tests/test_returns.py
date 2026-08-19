import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module
import app as app_module


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


def add_product(client, name="Widget", sku="WID-1", unit_price=100.0,
                 tax_rate_percent=10.0, stock_quantity=50):
    client.post("/products/new", data={
        "name": name,
        "sku": sku,
        "unit_price": str(unit_price),
        "tax_rate_percent": str(tax_rate_percent),
        "stock_quantity": str(stock_quantity),
    }, follow_redirects=True)
    conn = db_module.get_db()
    pid = conn.execute("SELECT id FROM products WHERE sku = ?", (sku,)).fetchone()["id"]
    conn.close()
    return pid


def checkout(client, items, discount_type="none", discount_value=0, payment_method="cash"):
    r = client.post(
        "/checkout",
        data=json.dumps({
            "items": items,
            "discount_type": discount_type,
            "discount_value": discount_value,
            "payment_method": payment_method,
        }),
        content_type="application/json",
    )
    return r.get_json()["bill_id"]


def bill_item_for(bill_id, product_id=None):
    conn = db_module.get_db()
    if product_id is None:
        row = conn.execute(
            "SELECT * FROM bill_items WHERE bill_id = ? ORDER BY id", (bill_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM bill_items WHERE bill_id = ? AND product_id = ?",
            (bill_id, product_id),
        ).fetchone()
    conn.close()
    return row


def do_return(client, bill_id, items, reason=None):
    return client.post(
        f"/returns/{bill_id}",
        data=json.dumps({"items": items, "reason": reason}),
        content_type="application/json",
    )


def stock_of(product_id):
    conn = db_module.get_db()
    qty = conn.execute(
        "SELECT stock_quantity FROM products WHERE id = ?", (product_id,)
    ).fetchone()["stock_quantity"]
    conn.close()
    return qty


class TestFullReturn:
    def test_full_return_refunds_entire_line_total(self, client):
        pid = add_product(client, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])
        item = bill_item_for(bill_id)

        r = do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 5}])
        body = r.get_json()

        assert r.status_code == 200
        assert body["refund_amount"] == item["line_total"]

    def test_full_return_restocks_all_units(self, client):
        pid = add_product(client, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])
        item = bill_item_for(bill_id)

        do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 5}])

        assert stock_of(pid) == 50  # 50 - 5 sold + 5 returned


class TestPartialReturn:
    def test_partial_return_refunds_proportionally(self, client):
        pid = add_product(client, unit_price=100, tax_rate_percent=10, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])
        item = bill_item_for(bill_id)
        # no discount: line_total = 5 * 100 * 1.10 = 550, per-unit = 110
        assert item["line_total"] == 550.0

        r = do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 2}])
        assert r.get_json()["refund_amount"] == 220.0  # 2 * 110

    def test_partial_return_restocks_only_returned_quantity(self, client):
        pid = add_product(client, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])
        item = bill_item_for(bill_id)

        do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 2}])

        assert stock_of(pid) == 47  # 50 - 5 + 2


class TestDiscountProportionality:
    def test_refund_reflects_discounted_price_not_list_price(self, client):
        pid = add_product(client, unit_price=100, tax_rate_percent=10, stock_quantity=50)
        bill_id = checkout(
            client, [{"product_id": pid, "quantity": 5}],
            discount_type="percent", discount_value=10,
        )
        item = bill_item_for(bill_id)
        # subtotal=500, discount=50, taxable=450, tax=45, line_total=495, per-unit=99
        assert item["line_total"] == 495.0

        r = do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 2}])
        refund = r.get_json()["refund_amount"]

        assert refund == 198.0  # 2 * 99, NOT 2 * 110 (undiscounted list price)
        assert refund < 220.0  # sanity: strictly less than the undiscounted equivalent


class TestReturnValidation:
    def test_rejects_return_exceeding_remaining_returnable_quantity(self, client):
        pid = add_product(client, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])
        item = bill_item_for(bill_id)

        do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 3}])
        r = do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 3}])  # only 2 left

        assert r.status_code == 409
        assert "only 2 remaining" in r.get_json()["error"]

    def test_rejects_double_return_of_full_quantity(self, client):
        pid = add_product(client, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])
        item = bill_item_for(bill_id)

        do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 5}])
        r = do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 1}])

        assert r.status_code == 409

    def test_rejects_zero_or_negative_quantity(self, client):
        pid = add_product(client, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])
        item = bill_item_for(bill_id)

        r = do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 0}])
        assert r.status_code == 400

    def test_rejects_empty_items_list(self, client):
        pid = add_product(client, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])

        r = do_return(client, bill_id, [])
        assert r.status_code == 400

    def test_rejects_return_against_nonexistent_bill(self, client):
        r = do_return(client, 999, [{"bill_item_id": 1, "quantity": 1}])
        assert r.status_code == 404

    def test_rejects_bill_item_not_belonging_to_bill(self, client):
        pid = add_product(client, stock_quantity=50)
        bill_id_1 = checkout(client, [{"product_id": pid, "quantity": 5}])
        bill_id_2 = checkout(client, [{"product_id": pid, "quantity": 5}])
        item_from_bill_1 = bill_item_for(bill_id_1)

        r = do_return(client, bill_id_2, [{"bill_item_id": item_from_bill_1["id"], "quantity": 1}])
        assert r.status_code == 400


class TestSequentialPartialReturns:
    def test_two_partial_returns_sum_to_full_refund(self, client):
        pid = add_product(client, unit_price=100, tax_rate_percent=10, stock_quantity=50)
        bill_id = checkout(client, [{"product_id": pid, "quantity": 5}])
        item = bill_item_for(bill_id)

        r1 = do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 2}])
        r2 = do_return(client, bill_id, [{"bill_item_id": item["id"], "quantity": 3}])

        total_refunded = r1.get_json()["refund_amount"] + r2.get_json()["refund_amount"]
        assert total_refunded == item["line_total"]
        assert stock_of(pid) == 50
