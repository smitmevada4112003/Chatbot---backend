import time
import threading
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel
from databases import get_db_connection
from auth import get_current_user, get_optional_current_user, require_admin
from websocket_manager import manager, broadcast_new_order, safe_broadcast_new_order
from email_utils import send_low_stock_alert, LOW_STOCK_THRESHOLD

router = APIRouter()

# Thread-safe in-memory cache to prevent duplicate order creation from rapid double-submissions
_orders_lock = threading.Lock()
_recent_orders_cache = {}  # key: (user_id, customer, product, quantity) -> (timestamp, response_dict)


# =========================
# Order Model
# =========================

def normalize_status(status: str) -> str:
    """
    Standardizes any order status variation to exact title-case string:
    'Pending', 'Completed', or 'Cancelled' (capital C, double L).
    """
    s = (status or "").strip().lower()
    if s in ("cancelled", "canceled", "cancel", "cancelling"):
        return "Cancelled"
    elif s in ("completed", "delivered", "complete", "done"):
        return "Completed"
    elif s in ("pending", "processing", "in progress"):
        return "Pending"
    return status.strip().title() if status else "Pending"


class Order(BaseModel):
    customer: Optional[str] = None
    product: str
    quantity: int
    status: str = "Pending"
    user_id: Optional[int] = None


# =========================
# GET ALL ORDERS
# =========================

@router.get("/orders")
def get_orders():

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("SELECT id, user_id, customer, product, quantity, COALESCE(total_amount, 0.0), status FROM orders ORDER BY id DESC")

    rows = cursor.fetchall()

    orders = []

    for row in rows:
        orders.append({
            "id": row[0],
            "user_id": row[1],
            "customer": row[2],
            "product": row[3],
            "quantity": row[4],
            "total_amount": float(row[5] or 0.0),
            "status": row[6]
        })

    cursor.close()
    db.close()

    return orders


# =========================
# GET MY ORDERS (LOGGED-IN CUSTOMER)
# =========================

@router.get("/my-orders")
def get_my_orders(current_user: dict = Depends(get_current_user)):
    """
    Returns only the orders where user_id matches the currently logged-in user.
    Also matches legacy orders if customer name or email matches for seamless migration.
    """
    user_id = current_user.get("id")
    user_email = (current_user.get("email") or "").strip().lower()
    user_name = (current_user.get("name") or "").strip().lower()

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    if user_id:
        cursor.execute(
            """
            SELECT id, user_id, customer, product, quantity, COALESCE(total_amount, 0.0) AS total_amount, status 
            FROM orders 
            WHERE user_id = %s OR (user_id IS NULL AND (LOWER(customer) = %s OR LOWER(customer) = %s))
            ORDER BY id DESC
            """,
            (user_id, user_email, user_name)
        )
    else:
        cursor.execute(
            """
            SELECT id, user_id, customer, product, quantity, COALESCE(total_amount, 0.0) AS total_amount, status 
            FROM orders 
            WHERE LOWER(customer) = %s OR LOWER(customer) = %s
            ORDER BY id DESC
            """,
            (user_email, user_name)
        )

    orders = cursor.fetchall() or []
    for o in orders:
        o["total_amount"] = float(o.get("total_amount") or 0.0)

    # Attach order items if present
    if orders:
        order_ids = [o["id"] for o in orders]
        format_strings = ','.join(['%s'] * len(order_ids))
        try:
            cursor.execute(
                f"""
                SELECT order_id, product_id, product_name, quantity, unit_price, subtotal
                FROM order_items
                WHERE order_id IN ({format_strings})
                """,
                tuple(order_ids)
            )
            item_rows = cursor.fetchall() or []
            items_by_order = {}
            for item in item_rows:
                item["unit_price"] = float(item.get("unit_price") or 0.0)
                item["subtotal"] = float(item.get("subtotal") or 0.0)
                items_by_order.setdefault(item["order_id"], []).append(item)
            for o in orders:
                o["items"] = items_by_order.get(o["id"], [])
        except Exception:
            for o in orders:
                o["items"] = []

    cursor.close()
    db.close()

    return orders


# =========================
# ADD ORDER (NON-BLOCKING FAST RESPONSE)
# =========================

@router.post("/orders")
def add_order(
    order: Order,
    background_tasks: BackgroundTasks,
    current_user: Optional[dict] = Depends(get_optional_current_user)
):
    # Automatically resolve user_id and customer name from JWT token or payload
    user_id = current_user.get("id") if current_user else order.user_id
    customer_name = (order.customer or "").strip()

    if not customer_name and current_user:
        customer_name = (current_user.get("name") or current_user.get("email") or "").strip()

    if not customer_name:
        return {"status": "error", "message": "Customer name is required"}
    if not order.product or not order.product.strip():
        return {"status": "error", "message": "Product name is required"}
    if order.quantity <= 0:
        return {"status": "error", "message": "Quantity must be greater than 0"}

    # Deduplication check for rapid double-submission (within 3.0 seconds)
    dedup_key = (
        user_id,
        customer_name.strip().lower(),
        order.product.strip().lower(),
        int(order.quantity)
    )
    now = time.time()
    with _orders_lock:
        # Prune old cache entries (> 10s)
        stale_keys = [k for k, (ts, _) in _recent_orders_cache.items() if now - ts > 10.0]
        for k in stale_keys:
            del _recent_orders_cache[k]

        if dedup_key in _recent_orders_cache:
            prev_time, cached_response = _recent_orders_cache[dedup_key]
            if now - prev_time < 3.0:
                print(f"[Duplicate Order Suppressed] Rapid duplicate order for customer '{customer_name}' - '{order.product}' (qty {order.quantity}) received within {now - prev_time:.2f}s. Returning existing order #{cached_response.get('order_id')}.")
                return cached_response

    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # If user_id is not yet set, attempt lookup in users table using customer_name
        if user_id is None and customer_name:
            cursor.execute(
                "SELECT id FROM users WHERE LOWER(email) = %s OR LOWER(name) = %s LIMIT 1",
                (customer_name.lower(), customer_name.lower())
            )
            u_match = cursor.fetchone()
            if u_match:
                user_id = u_match["id"]

        # 1. Check product catalog and available stock
        cursor.execute(
            "SELECT id, name, price, COALESCE(stock, 0) as stock FROM products WHERE LOWER(name) LIKE %s LIMIT 1",
            (f"%{order.product.strip().lower()}%",)
        )
        product_row = cursor.fetchone()

        if not product_row:
            cursor.close()
            return {"status": "error", "message": f"Product '{order.product}' not found in catalog"}

        product_id = product_row["id"]
        product_name = product_row["name"]
        current_stock = int(product_row.get("stock") or 0)
        unit_price = float(product_row.get("price") or 0.0)
        total_amount = round(unit_price * order.quantity, 2)

        if current_stock < order.quantity:
            cursor.close()
            return {
                "status": "error",
                "message": f"Insufficient stock for '{product_name}'. Only {current_stock} available, requested {order.quantity}."
            }

        t_total_start = time.perf_counter()

        # 2. Deduct product stock
        t_stock_start = time.perf_counter()
        new_stock = current_stock - order.quantity
        cursor.execute(
            "UPDATE products SET stock = %s WHERE id = %s",
            (new_stock, product_id)
        )
        t_stock_ms = (time.perf_counter() - t_stock_start) * 1000

        # 3. Insert order with user_id and total_amount
        t_db_start = time.perf_counter()
        cursor.execute(
            """
            INSERT INTO orders (user_id, customer, product, quantity, total_amount, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, customer_name, product_name, order.quantity, total_amount, normalize_status(order.status or "Pending"))
        )
        db.commit()
        order_id = cursor.lastrowid
        cursor.close()
        t_db_ms = (time.perf_counter() - t_db_start) * 1000
    except Exception as db_err:
        if db:
            try:
                db.rollback()
            except Exception:
                pass
        print(f"[Database Error in add_order]: {db_err}")
        return {"status": "error", "message": f"Database error: {str(db_err)}"}
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass

    # 4. Asynchronously queue low-stock email in background task (never blocks HTTP response, isolated failure)
    alert_info = None
    try:
        if new_stock <= LOW_STOCK_THRESHOLD:
            condition = "Out of Stock" if new_stock <= 0 else "Low Stock"
            background_tasks.add_task(
                send_low_stock_alert,
                product_name=product_name,
                product_id=product_id,
                current_stock=new_stock,
                status=condition,
                order_id=order_id
            )
            alert_info = f"Low stock alert queued for {product_name} ({new_stock} left)."
    except Exception as email_err:
        print(f"[Email Alert Queue Error]: {email_err}")

    # 5. Asynchronously queue WebSocket broadcast in background task (never blocks HTTP response, isolated failure)
    try:
        unit_p = float(product_row["price"] or 0) if product_row else 0.0
        ws_order_payload = {
            "id": order_id,
            "user_id": user_id,
            "customer": customer_name,
            "product": product_name,
            "quantity": order.quantity,
            "total_amount": round(unit_p * order.quantity, 2),
            "status": normalize_status(order.status or "Pending")
        }
        background_tasks.add_task(safe_broadcast_new_order, ws_order_payload)
    except Exception as ws_err:
        print(f"[WebSocket Broadcast Queue Warning] orders_api: {ws_err}")

    t_total_ms = (time.perf_counter() - t_total_start) * 1000

    try:
        print(
            f"\n[Fast Order Creation - Order #{order_id}]\n"
            f"  |-- Stock Update:        {t_stock_ms:7.2f} ms\n"
            f"  |-- DB Insert & Commit:  {t_db_ms:7.2f} ms\n"
            f"  |-- Background Email:    Queued (non-blocking)\n"
            f"  |-- WebSocket Broadcast: Queued (non-blocking)\n"
            f"  +-- HTTP Response Time:  {t_total_ms:7.2f} ms (Immediate)\n"
        )
    except Exception:
        pass

    response_payload = {
        "status": "success",
        "message": "Order added successfully",
        "order_id": order_id,
        "user_id": user_id,
        "customer": customer_name,
        "product": product_name,
        "quantity": order.quantity,
        "remaining_stock": new_stock,
        "alert": alert_info
    }

    # Cache successful order for deduplication window
    with _orders_lock:
        _recent_orders_cache[dedup_key] = (time.time(), response_payload)

    # Return response immediately
    return response_payload



# =========================
# GET ORDERS SUMMARY
# =========================

@router.get("/orders/summary")
def get_orders_summary(admin_user: dict = Depends(require_admin)):

    db = get_db_connection()
    cursor = db.cursor()

    # 1. Total number of orders
    cursor.execute("SELECT COUNT(*) FROM orders")
    total_orders_row = cursor.fetchone()
    total_orders = int(total_orders_row[0]) if total_orders_row and total_orders_row[0] is not None else 0

    # 2. Total number of products
    cursor.execute("SELECT COUNT(*) FROM products")
    total_products_row = cursor.fetchone()
    total_products = int(total_products_row[0]) if total_products_row and total_products_row[0] is not None else 0

    # 3. Count of orders by status (case-insensitive SQL grouping + Python normalization)
    cursor.execute("SELECT LOWER(TRIM(status)), COUNT(*) FROM orders GROUP BY LOWER(TRIM(status))")
    status_rows = cursor.fetchall()

    status_counts = {
        "Pending": 0,
        "Completed": 0,
        "Cancelled": 0
    }

    for row in status_rows:
        raw_status = row[0] or ""
        count = int(row[1] or 0)
        norm_key = normalize_status(raw_status)
        if norm_key in status_counts:
            status_counts[norm_key] += count
        else:
            status_counts[norm_key] = count

    # 4. Total revenue (sum of price * quantity for all orders, joined with products table)
    cursor.execute("""
        SELECT COALESCE(SUM(o.quantity * p.price), 0)
        FROM orders o
        JOIN products p ON LOWER(TRIM(o.product)) = LOWER(TRIM(p.name))
    """)
    rev_row = cursor.fetchone()
    total_revenue = int(rev_row[0]) if rev_row and rev_row[0] is not None else 0

    cursor.close()
    db.close()

    return {
        "total_orders": total_orders,
        "total_products": total_products,
        "status_counts": status_counts,
        "total_revenue": total_revenue
    }


# =========================
# GET ONE ORDER
# =========================

@router.get("/orders/{order_id}")
def get_order(order_id: int, admin_user: dict = Depends(require_admin)):

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute(
        "SELECT id, user_id, customer, product, quantity, COALESCE(total_amount, 0.0), status FROM orders WHERE id = %s",
        (order_id,)
    )

    row = cursor.fetchone()

    cursor.close()
    db.close()

    if row is None:
        return {
            "message": "Order not found"
        }

    return {
        "id": row[0],
        "user_id": row[1],
        "customer": row[2],
        "product": row[3],
        "quantity": row[4],
        "total_amount": float(row[5] or 0.0),
        "status": row[6]
    }


# =========================
# UPDATE ORDER
# =========================

@router.put("/orders/{order_id}")
def update_order(order_id: int, order: Order, admin_user: dict = Depends(require_admin)):

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    clean_status = normalize_status(order.status)
    cursor.execute(
        "SELECT id, price FROM products WHERE LOWER(name) LIKE %s LIMIT 1",
        (f"%{order.product.strip().lower()}%",)
    )
    p_row = cursor.fetchone()
    u_price = float(p_row["price"] or 0.0) if p_row else 0.0
    tot_amt = round(u_price * order.quantity, 2)

    cursor.execute(
        """
        UPDATE orders
        SET customer = %s,
            product = %s,
            quantity = %s,
            total_amount = %s,
            status = %s
        WHERE id = %s
        """,
        (
            order.customer,
            order.product,
            order.quantity,
            tot_amt,
            clean_status,
            order_id
        )
    )

    db.commit()
    cursor.close()
    db.close()

    try:
        manager.broadcast_sync({
            "type": "ORDER_STATUS_UPDATED",
            "data": {
                "id": order_id,
                "status": clean_status,
                "customer": order.customer,
                "product": order.product,
                "quantity": order.quantity,
                "total_amount": tot_amt
            }
        })
    except Exception:
        pass

    return {
        "message": "Order updated successfully",
        "status": clean_status
    }


# =========================
# DELETE ORDER
# =========================

@router.delete("/orders/{order_id}")
def delete_order(order_id: int, admin_user: dict = Depends(require_admin)):

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute(
        "DELETE FROM orders WHERE id = %s",
        (order_id,)
    )

    db.commit()

    cursor.close()
    db.close()

    try:
        manager.broadcast_sync({
            "type": "ORDER_DELETED",
            "data": {
                "id": order_id
            }
        })
    except Exception:
        pass

    return {
        "message": "Order deleted successfully"
    }


# =========================
# QUICK STATUS UPDATE
# =========================

class OrderStatusUpdate(BaseModel):
    status: str


@router.patch("/orders/{order_id}/status")
def update_order_status(order_id: int, payload: OrderStatusUpdate, admin_user: dict = Depends(require_admin)):
    clean_status = normalize_status(payload.status)

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Check previous status to restore stock if newly Cancelled
    cursor.execute("SELECT product, quantity, status FROM orders WHERE id = %s", (order_id,))
    existing = cursor.fetchone()

    if existing and clean_status == "Cancelled" and (existing.get("status") or "").lower() not in ("cancelled", "canceled"):
        cursor.execute(
            "UPDATE products SET stock = stock + %s WHERE LOWER(name) = %s",
            (existing["quantity"], (existing["product"] or "").lower())
        )

    cursor.execute(
        "UPDATE orders SET status = %s WHERE id = %s",
        (clean_status, order_id)
    )

    db.commit()
    cursor.close()
    db.close()

    try:
        manager.broadcast_sync({
            "type": "ORDER_STATUS_UPDATED",
            "data": {
                "id": order_id,
                "status": clean_status
            }
        })
    except Exception:
        pass

    return {
        "message": "Status updated successfully",
        "order_id": order_id,
        "status": clean_status
    }


# =========================
# CANCEL ORDER ENDPOINT
# =========================

@router.patch("/orders/{order_id}/cancel")
@router.post("/orders/{order_id}/cancel")
def cancel_order_endpoint(order_id: int, admin_user: dict = Depends(require_admin)):
    """Cancels an order and always sets status to exact string 'Cancelled'."""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT product, quantity, status FROM orders WHERE id = %s", (order_id,))
    existing = cursor.fetchone()

    if not existing:
        cursor.close()
        db.close()
        return {"status": "error", "message": f"Order #{order_id} not found"}

    if (existing.get("status") or "").lower() in ("cancelled", "canceled"):
        cursor.close()
        db.close()
        return {"status": "already_cancelled", "message": f"Order #{order_id} is already Cancelled"}

    # Restore stock
    cursor.execute(
        "UPDATE products SET stock = stock + %s WHERE LOWER(name) = %s",
        (existing["quantity"], (existing["product"] or "").lower())
    )

    # Always set exact string "Cancelled"
    cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE id = %s", (order_id,))
    db.commit()
    cursor.close()
    db.close()

    try:
        manager.broadcast_sync({
            "type": "ORDER_STATUS_UPDATED",
            "data": {
                "id": order_id,
                "status": "Cancelled"
            }
        })
    except Exception:
        pass

    return {
        "status": "success",
        "message": f"Order #{order_id} has been Cancelled and inventory restored.",
        "order_id": order_id,
        "order_status": "Cancelled"
    }


# =========================
# BULK DELETE ORDERS
# =========================

class BulkDeleteRequest(BaseModel):
    order_ids: list[int]


@router.post("/orders/bulk-delete")
def bulk_delete_orders(payload: BulkDeleteRequest, admin_user: dict = Depends(require_admin)):
    if not payload.order_ids:
        return {"message": "No orders specified", "deleted_count": 0}

    db = get_db_connection()
    cursor = db.cursor()

    format_strings = ','.join(['%s'] * len(payload.order_ids))
    cursor.execute(f"DELETE FROM orders WHERE id IN ({format_strings})", tuple(payload.order_ids))

    deleted_count = cursor.rowcount
    db.commit()

    cursor.close()
    db.close()

    try:
        manager.broadcast_sync({
            "type": "ORDER_DELETED",
            "data": {
                "ids": payload.order_ids
            }
        })
    except Exception:
        pass

    return {
        "message": f"Successfully deleted {deleted_count} orders",
        "deleted_count": deleted_count
    }