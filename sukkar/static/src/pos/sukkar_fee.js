/**
 * The delivery fee, in one place.
 *
 * Two screens set it: the recipient screen puts it on by itself, from the price
 * the area carries, and the fee screen lets the cashier override it. Both do
 * the same three things — find the fee product, put the amount on its line, and
 * record it on the order — so the work lives here rather than being written
 * twice and drifting apart. (29 Sep 2026.)
 */

/** The product the fee is charged on, pointed at in Settings → Sukkar. It is
    fetched if the register has not loaded it yet. */
export async function sukkarFeeProduct(pos) {
    const company = pos.company;
    const productId = company.sukkar_pos_fee_product_id?.id
        || company.raw?.sukkar_pos_fee_product_id;
    if (!productId) {
        return false;
    }
    const loaded = pos.models["product.product"].get(productId);
    if (loaded) {
        return loaded;
    }
    try {
        return (await pos.data.read("product.product", [productId]))[0] || false;
    } catch {
        return false;
    }
}

/**
 * Charge this order `amount` for delivery.
 *
 * An amount of zero takes the line off again, so moving an order from a far
 * area to a near one never leaves the old fee behind. Returns false only when
 * no fee product has been set up yet, so the caller can say so.
 */
export async function sukkarApplyFee(pos, order, amount) {
    const product = await sukkarFeeProduct(pos);
    if (!product) {
        return false;
    }
    const line = order.lines.find((l) => l.product_id?.id === product.id);
    if (!amount) {
        if (line) {
            order.removeOrderline(line);
        }
        order.sukkarSetDelivery({ sukkar_delivery_fee: 0 });
        return true;
    }
    if (line) {
        line.set_unit_price(amount);
    } else {
        await pos.addLineToOrder(
            { product_id: product, price_unit: amount }, order, {});
    }
    order.sukkarSetDelivery({ sukkar_delivery_fee: amount });
    return true;
}
