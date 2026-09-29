from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field

from databases import get_db_connection
from auth import get_current_user
from email_utils import send_low_stock_alert, LOW_STOCK_THRESHOLD
from websocket_manager import broadcast_new_order, safe_broadcast_new_order

router = APIRouter(prefix="/cart", tags=["Cart"])


class CartItemAdd(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=0)


class CheckoutRequest(BaseModel):
    customer_name: Optional[str] = None
    shipping_address: Optional[str] = None


@router.get("")
def get_cart(current_user: dict = Depends(get_current_user)):
    """
    Retrieves all items in the logged-in customer's active shopping cart,
    including individual pricing, quantities, available stock, and cart totals.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID could not be identified.")

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        query = """
            SELECT 
                c.id,
                c.product_id,
                p.name AS product_name,
                CAST(p.price AS FLOAT) AS price,
                COALESCE(p.stock, 0) AS stock,
                c.quantity,
                ROUND(c.quantity * CAST(p.price AS DECIMAL(10, 2)), 2) AS subtotal,
                c.updated_at
            FROM cart_items c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = %s
            ORDER BY c.updated_at DESC
        """
        cursor.execute(query, (user_id,))
        rows = cursor.fetchall() or []

        for r in rows:
            r["item_id"] = r["id"]

        total_items = sum(r["quantity"] for r in rows)
        total_price = round(sum(float(r["subtotal"] or 0) for r in rows), 2)

        return {
            "items": rows,
            "total_items": total_items,
            "total_amount": total_price,
            "total_price": total_price
        }
    finally:
        cursor.close()
        db.close()


@router.post("")
@router.post("/items")
def add_to_cart(item: CartItemAdd, current_user: dict = Depends(get_current_user)):
    """
    Adds a product to the customer's cart, or increments quantity if already present.
    Validates catalog existence and remaining stock.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID could not be identified.")

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        # 1. Check product existence and current stock
        cursor.execute(
            "SELECT id, name, CAST(price AS FLOAT) as price, COALESCE(stock, 0) as stock FROM products WHERE id = %s",
            (item.product_id,)
        )
        product = cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product with ID {item.product_id} not found.")

        current_stock = product["stock"]
        if current_stock <= 0:
            raise HTTPException(status_code=400, detail=f"'{product['name']}' is out of stock.")

        # 2. Check if product already exists in user's cart
        cursor.execute(
            "SELECT id, quantity FROM cart_items WHERE user_id = %s AND product_id = %s",
            (user_id, item.product_id)
        )
        existing = cursor.fetchone()

        new_quantity = item.quantity
        if existing:
            new_quantity += existing["quantity"]

        if new_quantity > current_stock:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot add {item.quantity} more. Only {current_stock} available in stock (you already have {existing['quantity'] if existing else 0} in cart)."
            )

        if existing:
            cursor.execute(
                "UPDATE cart_items SET quantity = %s WHERE id = %s",
                (new_quantity, existing["id"])
            )
            item_id = existing["id"]
        else:
            cursor.execute(
                "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (%s, %s, %s)",
                (user_id, item.product_id, new_quantity)
            )
            item_id = cursor.lastrowid

        db.commit()

        return {
            "status": "success",
            "message": f"Added '{product['name']}' to cart.",
            "item_id": item_id,
            "product_id": item.product_id,
            "quantity": new_quantity
        }
    finally:
        cursor.close()
        db.close()


@router.put("/{item_id}")
@router.put("/items/{item_id}")
def update_cart_item(
    item_id: int,
    item: CartItemUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Updates the quantity of a specific item in the cart.
    Accepts cart_items.id or product_id. If quantity is 0, removes the item.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID could not be identified.")

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        # Locate item by cart_items.id or by product_id
        cursor.execute(
            """
            SELECT c.id, c.product_id, c.quantity, p.name AS product_name, COALESCE(p.stock, 0) AS stock
            FROM cart_items c
            JOIN products p ON c.product_id = p.id
            WHERE (c.id = %s OR c.product_id = %s) AND c.user_id = %s
            LIMIT 1
            """,
            (item_id, item_id, user_id)
        )
        cart_item = cursor.fetchone()
        if not cart_item:
            raise HTTPException(status_code=404, detail="Cart item not found.")

        target_cart_id = cart_item["id"]

        if item.quantity <= 0:
            cursor.execute("DELETE FROM cart_items WHERE id = %s", (target_cart_id,))
            db.commit()
            return {"status": "success", "message": "Item removed from cart.", "item_id": target_cart_id, "quantity": 0}

        if item.quantity > cart_item["stock"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot set quantity to {item.quantity}. Only {cart_item['stock']} units available in stock."
            )

        cursor.execute(
            "UPDATE cart_items SET quantity = %s WHERE id = %s",
            (item.quantity, target_cart_id)
        )
        db.commit()

        return {
            "status": "success",
            "message": f"Updated quantity for '{cart_item['product_name']}' to {item.quantity}.",
            "item_id": target_cart_id,
            "product_id": cart_item["product_id"],
            "quantity": item.quantity
        }
    finally:
        cursor.close()
        db.close()


@router.delete("/{item_id}")
@router.delete("/items/{item_id}")
def remove_from_cart(item_id: int, current_user: dict = Depends(get_current_user)):
    """
    Removes a specific item from the customer's cart (accepts cart_items.id or product_id).
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID could not be identified.")

    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute(
            "DELETE FROM cart_items WHERE (id = %s OR product_id = %s) AND user_id = %s",
            (item_id, item_id, user_id)
        )
        db.commit()
        return {"status": "success", "message": "Item removed from cart.", "item_id": item_id}
    finally:
        cursor.close()
        db.close()


@router.delete("")
def clear_cart(current_user: dict = Depends(get_current_user)):
    """
    Removes all items from the customer's shopping cart.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID could not be identified.")

    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM cart_items WHERE user_id = %s", (user_id,))
        db.commit()
        return {"status": "success", "message": "Cart cleared successfully."}
    finally:
        cursor.close()
        db.close()


@router.post("/checkout")
def checkout_cart(
    background_tasks: BackgroundTasks,
    payload: Optional[CheckoutRequest] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Checks out all items in the customer's cart in a single transaction:
    1. Validates stock for all items.
    2. Deducts inventory.
    3. Triggers low stock email alerts if necessary in the background (non-blocking).
    4. Creates an order record in `orders` (with total amount and multi-item summary).
    5. Inserts line items into `order_items`.
    6. Clears the customer's cart.
    7. Returns HTTP response immediately without delay.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID could not be identified.")

    customer_name = (payload and payload.customer_name) or current_user.get("name") or current_user.get("email") or "Valued Customer"

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        # 1. Fetch all items in user's cart with product details
        cursor.execute("""
            SELECT 
                c.id AS cart_item_id,
                c.product_id,
                c.quantity,
                p.name AS product_name,
                CAST(p.price AS FLOAT) AS price,
                COALESCE(p.stock, 0) AS stock
            FROM cart_items c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = %s
        """, (user_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            raise HTTPException(status_code=400, detail="Your shopping cart is empty.")

        # 2. Validate stock availability for all items
        for item in cart_items:
            if item["stock"] < item["quantity"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for '{item['product_name']}'. Requested: {item['quantity']}, Available: {item['stock']}."
                )

        # 3. Calculate totals & item summaries
        total_quantity = sum(item["quantity"] for item in cart_items)
        total_amount = round(sum(item["quantity"] * item["price"] for item in cart_items), 2)

        # Generate a descriptive product summary string for backward compatibility with orders table
        summary_parts = [f"{item['product_name']} (x{item['quantity']})" for item in cart_items]
        product_summary = ", ".join(summary_parts)
        if len(product_summary) > 250:
            product_summary = f"{len(cart_items)} Items: " + ", ".join(summary_parts)[:230] + "..."

        # 4. Insert into orders table
        cursor.execute("""
            INSERT INTO orders (user_id, customer, product, quantity, total_amount, status)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, customer_name, product_summary, total_quantity, total_amount, "Pending"))
        order_id = cursor.lastrowid

        # 5. Insert line items into order_items & deduct stock
        low_stock_alerts_to_send = []
        order_line_items = []

        for item in cart_items:
            subtotal = round(item["quantity"] * item["price"], 2)
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, subtotal)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (order_id, item["product_id"], item["product_name"], item["quantity"], item["price"], subtotal))

            # Deduct stock
            remaining_stock = item["stock"] - item["quantity"]
            cursor.execute("UPDATE products SET stock = %s WHERE id = %s", (remaining_stock, item["product_id"]))

            order_line_items.append({
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "quantity": item["quantity"],
                "unit_price": item["price"],
                "subtotal": subtotal
            })

            if remaining_stock <= LOW_STOCK_THRESHOLD:
                low_stock_alerts_to_send.append({
                    "name": item["product_name"],
                    "id": item["product_id"],
                    "stock": remaining_stock,
                    "status": "Out of Stock" if remaining_stock <= 0 else "Low Stock"
                })

        # 6. Clear customer's cart
        cursor.execute("DELETE FROM cart_items WHERE user_id = %s", (user_id,))

        # Commit entire transaction
        db.commit()

        # 7. Fire low-stock email alerts in background tasks (never delays HTTP response; deduplicated per product)
        seen_alert_ids = set()
        for alert in low_stock_alerts_to_send:
            if alert["id"] not in seen_alert_ids:
                seen_alert_ids.add(alert["id"])
                background_tasks.add_task(
                    send_low_stock_alert,
                    product_name=alert["name"],
                    product_id=alert["id"],
                    current_stock=alert["stock"],
                    status=alert["status"],
                    order_id=order_id
                )

        # 8. Broadcast real-time order creation to Admin Dashboards in background
        try:
            ws_order_payload = {
                "id": order_id,
                "user_id": user_id,
                "customer": customer_name,
                "product": product_summary,
                "quantity": total_quantity,
                "total_amount": round(total_amount, 2),
                "status": "Pending"
            }
            background_tasks.add_task(safe_broadcast_new_order, ws_order_payload)
        except Exception as ws_err:
            print(f"[WebSocket Broadcast Warning] cart_api checkout: {ws_err}")

        return {
            "status": "success",
            "message": "Order placed successfully! Your cart has been checked out.",
            "order_id": order_id,
            "customer": customer_name,
            "total_items": total_quantity,
            "total_amount": total_amount,
            "order_status": "Pending",
            "items": order_line_items
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Checkout failed: {str(e)}")
    finally:
        cursor.close()
        db.close()
