let cart = [];

const cartBody = document.getElementById("cart-body");
const addError = document.getElementById("add-error");
const checkoutError = document.getElementById("checkout-error");

document.getElementById("add-item-btn").addEventListener("click", () => {
    addError.textContent = "";
    const select = document.getElementById("product-select");
    const option = select.options[select.selectedIndex];
    const qty = parseInt(document.getElementById("qty-input").value, 10);

    if (!option.value) {
        addError.textContent = "Pick a product first.";
        return;
    }
    if (!qty || qty < 1) {
        addError.textContent = "Quantity must be at least 1.";
        return;
    }

    const productId = parseInt(option.value, 10);
    const stock = parseInt(option.dataset.stock, 10);
    const existing = cart.find((l) => l.product_id === productId);
    const alreadyInCart = existing ? existing.quantity : 0;

    if (alreadyInCart + qty > stock) {
        addError.textContent = `Only ${stock} in stock (already have ${alreadyInCart} in cart).`;
        return;
    }

    if (existing) {
        existing.quantity += qty;
    } else {
        cart.push({
            product_id: productId,
            name: option.dataset.name,
            unit_price: parseFloat(option.dataset.price),
            tax_rate_percent: parseFloat(option.dataset.tax),
            quantity: qty,
        });
    }
    renderCart();
});

function removeLine(productId) {
    cart = cart.filter((l) => l.product_id !== productId);
    renderCart();
}

function computeTotals() {
    const discountType = document.getElementById("discount-type").value;
    const discountValue = parseFloat(document.getElementById("discount-value").value) || 0;

    let subtotal = 0;
    cart.forEach((l) => {
        l.line_subtotal = l.quantity * l.unit_price;
        subtotal += l.line_subtotal;
    });

    let discountAmount = 0;
    if (discountType === "percent") {
        discountAmount = subtotal * discountValue / 100;
    } else if (discountType === "flat") {
        discountAmount = Math.min(discountValue, subtotal);
    }
    const discountRatio = subtotal > 0 ? discountAmount / subtotal : 0;

    let taxAmount = 0;
    cart.forEach((l) => {
        const lineTaxable = l.line_subtotal * (1 - discountRatio);
        l.line_tax = lineTaxable * l.tax_rate_percent / 100;
        l.line_total = lineTaxable + l.line_tax;
        taxAmount += l.line_tax;
    });

    const taxableAmount = subtotal - discountAmount;
    const grandTotal = taxableAmount + taxAmount;

    return { subtotal, discountAmount, taxAmount, grandTotal };
}

function renderCart() {
    cartBody.innerHTML = "";
    const totals = computeTotals();

    cart.forEach((l) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${l.name}</td>
            <td>${l.quantity}</td>
            <td>${l.unit_price.toFixed(2)}</td>
            <td>${l.tax_rate_percent}</td>
            <td>${l.line_total.toFixed(2)}</td>
            <td><button type="button" class="link-btn" data-id="${l.product_id}">remove</button></td>
        `;
        cartBody.appendChild(tr);
    });
    cartBody.querySelectorAll(".link-btn").forEach((btn) => {
        btn.addEventListener("click", () => removeLine(parseInt(btn.dataset.id, 10)));
    });

    document.getElementById("t-subtotal").textContent = totals.subtotal.toFixed(2);
    document.getElementById("t-discount").textContent = totals.discountAmount.toFixed(2);
    document.getElementById("t-tax").textContent = totals.taxAmount.toFixed(2);
    document.getElementById("t-grand").textContent = totals.grandTotal.toFixed(2);
}

document.getElementById("discount-type").addEventListener("change", renderCart);
document.getElementById("discount-value").addEventListener("input", renderCart);

document.getElementById("checkout-btn").addEventListener("click", () => {
    checkoutError.textContent = "";
    if (cart.length === 0) {
        checkoutError.textContent = "Cart is empty.";
        return;
    }

    const payload = {
        items: cart.map((l) => ({ product_id: l.product_id, quantity: l.quantity })),
        discount_type: document.getElementById("discount-type").value,
        discount_value: parseFloat(document.getElementById("discount-value").value) || 0,
        payment_method: document.getElementById("payment-method").value,
    };

    fetch("/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    })
        .then((res) => res.json().then((body) => ({ status: res.status, body })))
        .then(({ status, body }) => {
            if (status !== 200) {
                checkoutError.textContent = body.error || "Checkout failed.";
                return;
            }
            window.location.href = `/receipt/${body.bill_id}`;
        })
        .catch(() => {
            checkoutError.textContent = "Network error during checkout.";
        });
});

renderCart();
