const script = document.currentScript;
const billId = script.dataset.billId;

const errorEl = document.getElementById("return-error");
const successEl = document.getElementById("return-success");

document.getElementById("submit-return-btn").addEventListener("click", () => {
    errorEl.textContent = "";
    successEl.textContent = "";

    const items = [];
    document.querySelectorAll(".return-qty-input").forEach((input) => {
        const qty = parseInt(input.value, 10);
        if (qty > 0) {
            items.push({ bill_item_id: parseInt(input.dataset.billItemId, 10), quantity: qty });
        }
    });

    if (items.length === 0) {
        errorEl.textContent = "Enter a return quantity for at least one item.";
        return;
    }

    const payload = {
        items,
        reason: document.getElementById("return-reason").value,
    };

    fetch(`/returns/${billId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    })
        .then((res) => res.json().then((body) => ({ status: res.status, body })))
        .then(({ status, body }) => {
            if (status !== 200) {
                errorEl.textContent = body.error || "Return failed.";
                return;
            }
            successEl.textContent = `Return processed. Refund amount: ${body.refund_amount.toFixed(2)}`;
            setTimeout(() => window.location.reload(), 1200);
        })
        .catch(() => {
            errorEl.textContent = "Network error while processing return.";
        });
});
