from typing import Optional
from langchain_core.tools import tool
from databases import get_db_connection
from email_utils import send_low_stock_alert, LOW_STOCK_THRESHOLD
from websocket_manager import manager, broadcast_new_order


# ---------------- PRODUCT TOOLS ----------------

@tool
def get_products():
    """Get all products with their id, name, price, and current stock level."""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, name, CAST(price AS FLOAT) as price, COALESCE(stock, 0) as stock FROM products")
        products = cursor.fetchall()
        cursor.close()
        db.close()
        return products
    except Exception as e:
        return f"Error retrieving products from database: {e}"


@tool
def get_product_by_id(product_id: int):
    """Get a single product's details (name, price, stock) by its product ID."""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, name, CAST(price AS FLOAT) as price, COALESCE(stock, 0) as stock FROM products WHERE id = %s",
            (product_id,)
        )
        row = cursor.fetchone()
        cursor.close()
        db.close()
        if row is None:
            return f"Product with ID {product_id} not found."
        return row
    except Exception as e:
        return f"Error retrieving product: {e}"


@tool
def get_product_by_name(name: str):
    """Get details of a specific product (including price and stock) by its name."""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, name, CAST(price AS FLOAT) as price, COALESCE(stock, 0) as stock FROM products WHERE LOWER(name) LIKE %s",
            (f"%{name.lower()}%",)
        )
        matched = cursor.fetchall()
        cursor.close()
        db.close()
        return matched if matched else f"No product found matching '{name}'"
    except Exception as e:
        return f"Error searching product: {e}"


@tool
def get_low_stock_products(threshold: int = LOW_STOCK_THRESHOLD):
    """Check and list all products that have low stock (equal to or below the given threshold, default 5)."""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, name, CAST(price AS FLOAT) as price, COALESCE(stock, 0) as stock FROM products WHERE COALESCE(stock, 0) <= %s ORDER BY stock ASC",
            (threshold,)
        )
        matched = cursor.fetchall()
        cursor.close()
        db.close()
        if not matched:
            return f"All products have adequate stock (above {threshold} units)."
        return matched
    except Exception as e:
        return f"Error checking low stock products: {e}"


# ---------------- ORDER TOOLS ----------------

@tool
def get_orders():
    """Get all orders with customer name, product name, quantity and status."""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, customer, product, quantity, status FROM orders")
        orders = cursor.fetchall()
        cursor.close()
        db.close()
        return orders
    except Exception as e:
        return f"Error retrieving orders from database: {e}"


@tool
def get_order_by_id(order_id: int):
    """Get status/details of a specific order by its order ID."""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, customer, product, quantity, status FROM orders WHERE id = %s",
            (order_id,)
        )
        row = cursor.fetchone()
        cursor.close()
        db.close()
        if row is None:
            return f"Order with ID {order_id} not found."
        return row
    except Exception as e:
        return f"Error retrieving order: {e}"


@tool
def get_orders_by_customer(customer_identifier: str):
    """
    Get all orders placed by a specific customer.
    Accepts customer name, email address, or numeric user_id as customer_identifier.
    """
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        raw_ident = str(customer_identifier).strip()

        if raw_ident.isdigit():
            cursor.execute(
                """
                SELECT id, user_id, customer, product, quantity, status 
                FROM orders 
                WHERE user_id = %s OR LOWER(customer) LIKE %s 
                ORDER BY id DESC
                """,
                (int(raw_ident), f"%{raw_ident.lower()}%")
            )
        else:
            clean_ident = raw_ident.lower()
            cursor.execute(
                """
                SELECT o.id, o.user_id, o.customer, o.product, o.quantity, o.status 
                FROM orders o
                LEFT JOIN users u ON o.user_id = u.id
                WHERE LOWER(o.customer) LIKE %s 
                   OR LOWER(COALESCE(u.email, '')) = %s 
                   OR LOWER(COALESCE(u.name, '')) LIKE %s
                ORDER BY o.id DESC
                """,
                (f"%{clean_ident}%", clean_ident, f"%{clean_ident}%")
            )

        matched = cursor.fetchall()
        cursor.close()
        db.close()
        return matched if matched else f"No orders found for customer '{customer_identifier}'"
    except Exception as e:
        return f"Error searching customer orders: {e}"


@tool
def place_order(customer_name: str, product_name: str, quantity: int, user_id: Optional[int] = None):
    """
    Place a new order in the database for a customer.
    Checks product existence and available stock, deducts stock, triggers a low-stock email alert if stock falls <= 5, and records the order.
    Requires customer_name, product_name, and quantity.
    Optionally accepts user_id to link order to the registered user account.
    Newly placed orders are always initialized with the exact status 'Pending'
    (from the three exact allowed status values: "Pending", "Completed", "Cancelled").
    """
    try:
        clean_customer = str(customer_name or "").strip()
        clean_product = str(product_name or "").strip()

        if not clean_customer:
            return "Failed to place order: customer name is required."
        if not clean_product:
            return "Failed to place order: product name is required."
        if not isinstance(quantity, int) or quantity <= 0:
            return "Failed to place order: quantity must be a positive integer."

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Resolve user_id if not explicitly provided, checking users table
        resolved_user_id = user_id
        if resolved_user_id is None and clean_customer:
            cursor.execute(
                "SELECT id FROM users WHERE LOWER(email) = %s OR LOWER(name) = %s LIMIT 1",
                (clean_customer.lower(), clean_customer.lower())
            )
            matched_user = cursor.fetchone()
            if matched_user:
                resolved_user_id = matched_user["id"]

        # 1. Look up product in catalog to verify existence, unit price, and current stock
        cursor.execute(
            "SELECT id, name, CAST(price AS FLOAT) as price, COALESCE(stock, 0) as stock FROM products WHERE LOWER(name) LIKE %s LIMIT 1",
            (f"%{clean_product.lower()}%",)
        )
        prod = cursor.fetchone()

        if not prod:
            cursor.close()
            db.close()
            return f"Failed to place order: product '{product_name}' does not exist in the store catalog."

        exact_product_name = prod["name"]
        prod_id = prod["id"]
        unit_price = prod["price"]
        current_stock = prod["stock"]

        # 2. Check stock availability
        if current_stock < quantity:
            cursor.close()
            db.close()
            if current_stock <= 0:
                return f"Sorry, '{exact_product_name}' is currently out of stock."
            return f"Insufficient stock for '{exact_product_name}'. Only {current_stock} unit(s) available, but {quantity} were requested."

        # 3. Deduct stock
        remaining_stock = current_stock - quantity
        cursor.execute(
            "UPDATE products SET stock = %s WHERE id = %s",
            (remaining_stock, prod_id)
        )

        # 4. Insert new order into orders table with user_id
        cursor.execute(
            "INSERT INTO orders (user_id, customer, product, quantity, status) VALUES (%s, %s, %s, %s, %s)",
            (resolved_user_id, clean_customer, exact_product_name, quantity, "Pending")
        )
        db.commit()
        order_id = cursor.lastrowid
        cursor.close()
        db.close()

        # 5. Check if stock reached low-stock threshold (e.g. <= 5) or 0
        alert_info = ""
        if remaining_stock <= LOW_STOCK_THRESHOLD:
            condition = "Out of Stock" if remaining_stock <= 0 else "Low Stock"
            alert_res = send_low_stock_alert(
                product_name=exact_product_name,
                product_id=prod_id,
                current_stock=remaining_stock,
                status=condition
            )
            alert_info = f" [Alert Triggered: {condition} ({remaining_stock} left)]."

        total_price_num = round(unit_price * quantity, 2) if unit_price is not None else 0.0
        total_price_str = f" Total: ₹{total_price_num:,.2f}." if unit_price is not None else ""

        # 6. Broadcast real-time WebSocket notification to open Admin Dashboard sessions asynchronously
        try:
            broadcast_new_order({
                "id": order_id,
                "user_id": resolved_user_id,
                "customer": clean_customer,
                "product": exact_product_name,
                "quantity": quantity,
                "total_amount": total_price_num,
                "status": "Pending"
            })
        except Exception as ws_err:
            print(f"[WebSocket Broadcast Warning] place_order tool: {ws_err}")
        return {
            "status": "success",
            "order_id": order_id,
            "user_id": resolved_user_id,
            "customer": clean_customer,
            "product": exact_product_name,
            "quantity": quantity,
            "remaining_stock": remaining_stock,
            "order_status": "Pending",
            "message": f"Order #{order_id} placed successfully for {clean_customer} ({quantity}x {exact_product_name}).{total_price_str} Remaining stock: {remaining_stock}.{alert_info} Status is 'Pending'."
        }
    except Exception as e:
        return f"Error placing order in database: {e}"


@tool
def cancel_order(order_id: int):
    """
    Cancel an existing order by its order ID.
    Always updates the status to the exact string 'Cancelled' (capital C, double L),
    and restores the product stock in the database.
    Note: The only allowed status value for cancellation is the exact string 'Cancelled'.
    """
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, customer, product, quantity, status FROM orders WHERE id = %s", (order_id,))
        order = cursor.fetchone()
        if not order:
            cursor.close()
            db.close()
            return f"Order with ID #{order_id} not found."

        current_status = (order["status"] or "").strip()
        if current_status.lower() in ("cancelled", "canceled"):
            cursor.close()
            db.close()
            return f"Order #{order_id} is already Cancelled."

        # Always store exact string "Cancelled" (capital C, double L)
        cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE id = %s", (order_id,))

        # Restore product stock
        cursor.execute(
            "UPDATE products SET stock = stock + %s WHERE LOWER(name) = %s",
            (order["quantity"], order["product"].lower())
        )
        db.commit()
        cursor.close()
        db.close()

        # Broadcast order cancellation to Admin Dashboards
        try:
            manager.broadcast_sync({
                "type": "ORDER_STATUS_UPDATED",
                "data": {
                    "id": order_id,
                    "status": "Cancelled",
                    "customer": order.get("customer"),
                    "product": order.get("product"),
                    "quantity": order.get("quantity")
                }
            })
        except Exception as ws_err:
            print(f"[WebSocket Broadcast Warning] cancel_order tool: {ws_err}")

        return {
            "status": "success",
            "order_id": order_id,
            "order_status": "Cancelled",
            "message": f"Order #{order_id} for {order['customer']} ({order['quantity']}x {order['product']}) has been successfully Cancelled. Restocked {order['quantity']} unit(s)."
        }
    except Exception as e:
        return f"Error cancelling order #{order_id}: {e}"


@tool
def update_order_status(order_id: int, new_status: str):
    """
    Update the status of an existing order by its order ID.

    ALLOWED STATUS VALUES:
    The assistant (LLM) must ONLY ever pass one of the exact allowed status values:
    - "Pending"
    - "Completed"
    - "Cancelled" (capital C, double L)

    DO NOT generate or pass lowercase (e.g. 'cancelled', 'completed', 'pending'),
    alternative spellings (e.g. 'canceled'), or other status names (e.g. 'delivered',
    'shipped', 'processing'). Only use: "Pending", "Completed", or "Cancelled".
    """
    try:
        s = (new_status or "").strip().lower()
        if s in ("cancelled", "canceled", "cancel", "cancelling"):
            normalized = "Cancelled"
        elif s in ("completed", "delivered", "done"):
            normalized = "Completed"
        elif s in ("pending", "processing", "in progress"):
            normalized = "Pending"
        else:
            normalized = new_status.strip().title()

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, customer, product, quantity, status FROM orders WHERE id = %s", (order_id,))
        order = cursor.fetchone()
        if not order:
            cursor.close()
            db.close()
            return f"Order #{order_id} not found."

        prev_status = (order["status"] or "").strip().lower()
        # If transitioning to Cancelled and wasn't cancelled before, restore stock
        if normalized == "Cancelled" and prev_status not in ("cancelled", "canceled"):
            cursor.execute(
                "UPDATE products SET stock = stock + %s WHERE LOWER(name) = %s",
                (order["quantity"], order["product"].lower())
            )

        cursor.execute("UPDATE orders SET status = %s WHERE id = %s", (normalized, order_id))
        db.commit()
        cursor.close()
        db.close()

        # Broadcast status update to Admin Dashboards
        try:
            manager.broadcast_sync({
                "type": "ORDER_STATUS_UPDATED",
                "data": {
                    "id": order_id,
                    "status": normalized,
                    "customer": order.get("customer"),
                    "product": order.get("product"),
                    "quantity": order.get("quantity")
                }
            })
        except Exception as ws_err:
            print(f"[WebSocket Broadcast Warning] update_order_status tool: {ws_err}")

        return {
            "status": "success",
            "order_id": order_id,
            "order_status": normalized,
            "message": f"Order #{order_id} status updated to '{normalized}'."
        }
    except Exception as e:
        return f"Error updating order status: {e}"


# ---------------- REVIEW TOOLS ----------------

@tool
def get_product_reviews(product_name: str):
    """
    Get customer reviews, average star rating, total review count, and sample feedback comments for a product by name.
    Use this tool whenever a customer asks things like:
    - 'What do people think of the iPhone 15?'
    - 'What is the rating of laptop?'
    - 'Are there any reviews for product X?'
    - 'What do customers say about Y?'
    """
    try:
        query_name = (product_name or "").strip()
        if not query_name:
            return "Please provide a valid product name to check reviews."

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # 1. Search for matching product by name
        cursor.execute(
            "SELECT id, name, CAST(price AS FLOAT) as price, COALESCE(stock, 0) as stock FROM products WHERE LOWER(name) LIKE %s",
            (f"%{query_name.lower()}%",)
        )
        products = cursor.fetchall()
        if not products:
            cursor.close()
            db.close()
            return f"No product found matching '{query_name}' to check reviews."

        product = products[0]
        product_id = product["id"]
        actual_name = product["name"]

        # 2. Compute average rating and count
        cursor.execute(
            """
            SELECT 
                COALESCE(ROUND(AVG(rating), 1), 0.0) AS average_rating,
                COUNT(id) AS review_count
            FROM reviews
            WHERE product_id = %s
            """,
            (product_id,)
        )
        summary = cursor.fetchone() or {"average_rating": 0.0, "review_count": 0}
        avg_rating = float(summary["average_rating"])
        review_count = int(summary["review_count"])

        if review_count == 0:
            cursor.close()
            db.close()
            return {
                "product_id": product_id,
                "product_name": actual_name,
                "average_rating": 0.0,
                "review_count": 0,
                "sample_comments": [],
                "message": f"'{actual_name}' currently has no customer reviews (Rating: 0.0/5 based on 0 reviews)."
            }

        # 3. Retrieve sample comments (up to 3 latest reviews)
        cursor.execute(
            """
            SELECT 
                r.rating,
                r.comment,
                COALESCE(u.name, 'Customer') AS reviewer_name,
                r.created_at
            FROM reviews r
            LEFT JOIN users u ON r.user_id = u.id
            WHERE r.product_id = %s
            ORDER BY r.created_at DESC
            LIMIT 3
            """,
            (product_id,)
        )
        review_rows = cursor.fetchall()

        cursor.close()
        db.close()

        sample_comments = []
        for r in review_rows:
            sample_comments.append({
                "rating": r["rating"],
                "reviewer": r["reviewer_name"],
                "comment": r["comment"] or "(No written comment)"
            })

        return {
            "product_id": product_id,
            "product_name": actual_name,
            "average_rating": avg_rating,
            "review_count": review_count,
            "sample_comments": sample_comments
        }
    except Exception as e:
        return f"Error retrieving product reviews: {e}"


# ---------------- CART & MULTI-ITEM TOOLS ----------------

@tool
def add_to_cart(
    product_name: str,
    quantity: int = 1,
    user_id: Optional[int] = None,
    customer_identifier: Optional[str] = None
):
    """
    Add a product to the customer's shopping cart.
    Accepts product_name, quantity (defaults to 1), and user_id or customer_identifier (email/name).
    Validates catalog existence and remaining stock, then updates or inserts the cart item.
    """
    try:
        clean_product = str(product_name or "").strip()
        if not clean_product:
            return "Failed to add to cart: Product name is required."
        if not isinstance(quantity, int) or quantity <= 0:
            return "Failed to add to cart: Quantity must be a positive integer."

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # 1. Resolve user_id
        resolved_user_id = user_id
        if resolved_user_id is None and customer_identifier:
            cursor.execute(
                "SELECT id, name, email FROM users WHERE LOWER(email) = %s OR LOWER(name) = %s LIMIT 1",
                (str(customer_identifier).strip().lower(), str(customer_identifier).strip().lower())
            )
            u_match = cursor.fetchone()
            if u_match:
                resolved_user_id = u_match["id"]

        if resolved_user_id is None:
            cursor.close()
            db.close()
            return "Failed to add to cart: Please sign in or provide your account email to use the shopping cart."

        # 2. Look up product
        cursor.execute(
            "SELECT id, name, CAST(price AS FLOAT) as price, COALESCE(stock, 0) as stock FROM products WHERE LOWER(name) LIKE %s LIMIT 1",
            (f"%{clean_product.lower()}%",)
        )
        prod = cursor.fetchone()
        if not prod:
            cursor.close()
            db.close()
            return f"Failed to add to cart: Product '{product_name}' was not found in the catalog."

        prod_id = prod["id"]
        prod_name = prod["name"]
        prod_price = prod["price"]
        current_stock = prod["stock"]

        if current_stock <= 0:
            cursor.close()
            db.close()
            return f"Sorry, '{prod_name}' is currently out of stock."

        # 3. Check existing cart item
        cursor.execute(
            "SELECT id, quantity FROM cart_items WHERE user_id = %s AND product_id = %s",
            (resolved_user_id, prod_id)
        )
        existing = cursor.fetchone()
        new_quantity = quantity + (existing["quantity"] if existing else 0)

        if new_quantity > current_stock:
            cursor.close()
            db.close()
            already_in_cart = existing["quantity"] if existing else 0
            return (
                f"Cannot add {quantity} unit(s) of '{prod_name}'. Only {current_stock} in stock "
                f"(you currently have {already_in_cart} in your cart)."
            )

        if existing:
            cursor.execute(
                "UPDATE cart_items SET quantity = %s WHERE id = %s",
                (new_quantity, existing["id"])
            )
        else:
            cursor.execute(
                "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (%s, %s, %s)",
                (resolved_user_id, prod_id, new_quantity)
            )

        db.commit()
        cursor.close()
        db.close()

        item_subtotal = round(new_quantity * prod_price, 2)
        return (
            f"Successfully added {quantity} unit(s) of '{prod_name}' to your cart! "
            f"You now have {new_quantity} in your cart (Subtotal: ${item_subtotal:.2f}). "
            f"You can ask to 'view cart' or 'checkout' anytime."
        )
    except Exception as e:
        return f"Error adding item to cart: {e}"


@tool
def view_cart(
    user_id: Optional[int] = None,
    customer_identifier: Optional[str] = None
):
    """
    View all products currently in the customer's shopping cart, including
    quantities, unit prices, item subtotals, and the overall cart total.
    """
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        resolved_user_id = user_id
        if resolved_user_id is None and customer_identifier:
            cursor.execute(
                "SELECT id, name, email FROM users WHERE LOWER(email) = %s OR LOWER(name) = %s LIMIT 1",
                (str(customer_identifier).strip().lower(), str(customer_identifier).strip().lower())
            )
            u_match = cursor.fetchone()
            if u_match:
                resolved_user_id = u_match["id"]

        if resolved_user_id is None:
            cursor.close()
            db.close()
            return "Please sign in or provide your registered email/name so I can find your shopping cart."

        cursor.execute(
            """
            SELECT 
                c.id,
                p.name AS product_name,
                CAST(p.price AS FLOAT) AS price,
                c.quantity,
                ROUND(c.quantity * CAST(p.price AS DECIMAL(10, 2)), 2) AS subtotal
            FROM cart_items c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = %s
            ORDER BY c.created_at ASC
            """,
            (resolved_user_id,)
        )
        items = cursor.fetchall() or []
        cursor.close()
        db.close()

        if not items:
            return "Your shopping cart is currently empty. Would you like to browse products?"

        total_items = sum(i["quantity"] for i in items)
        total_price = sum(float(i["subtotal"]) for i in items)

        lines = ["🛒 **Your Shopping Cart:**"]
        for idx, item in enumerate(items, 1):
            lines.append(
                f"{idx}. **{item['product_name']}** — Qty: {item['quantity']} × ${item['price']:.2f} = ${float(item['subtotal']):.2f}"
            )
        lines.append(f"\n**Total Items:** {total_items}")
        lines.append(f"**Total Cart Price:** ${total_price:.2f}")
        lines.append("Say 'checkout' whenever you're ready to place this order!")

        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving cart: {e}"


@tool
def checkout_cart(
    user_id: Optional[int] = None,
    customer_name: Optional[str] = None
):
    """
    Check out the customer's entire shopping cart as a single order.
    Validates stock, deducts product inventory, records order line-items,
    triggers low-stock alerts if applicable, and clears the cart.
    """
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        resolved_user_id = user_id
        resolved_customer = str(customer_name or "").strip()

        if resolved_user_id is None and resolved_customer:
            cursor.execute(
                "SELECT id, name, email FROM users WHERE LOWER(email) = %s OR LOWER(name) = %s LIMIT 1",
                (resolved_customer.lower(), resolved_customer.lower())
            )
            u_match = cursor.fetchone()
            if u_match:
                resolved_user_id = u_match["id"]
                if not resolved_customer:
                    resolved_customer = u_match.get("name") or u_match.get("email")

        if resolved_user_id is None:
            cursor.close()
            db.close()
            return "Please sign in or provide your customer name/email to check out your shopping cart."

        # Fetch cart items
        cursor.execute(
            """
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
            """,
            (resolved_user_id,)
        )
        cart_items = cursor.fetchall() or []

        if not cart_items:
            cursor.close()
            db.close()
            return "Your shopping cart is empty! There is nothing to check out."

        # Validate stock
        for item in cart_items:
            if item["stock"] < item["quantity"]:
                cursor.close()
                db.close()
                return (
                    f"Checkout could not be completed: Insufficient stock for '{item['product_name']}'. "
                    f"Requested: {item['quantity']}, Available: {item['stock']}."
                )

        if not resolved_customer:
            resolved_customer = f"Customer #{resolved_user_id}"

        total_quantity = sum(item["quantity"] for item in cart_items)
        total_amount = round(sum(item["quantity"] * item["price"] for item in cart_items), 2)
        summary_str = ", ".join([f"{item['product_name']} (x{item['quantity']})" for item in cart_items])
        if len(summary_str) > 250:
            summary_str = f"{len(cart_items)} Items: " + summary_str[:230] + "..."

        # Insert order
        cursor.execute(
            """
            INSERT INTO orders (user_id, customer, product, quantity, total_amount, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (resolved_user_id, resolved_customer, summary_str, total_quantity, total_amount, "Pending")
        )
        order_id = cursor.lastrowid

        alerts_to_send = []
        # Insert line items & deduct stock
        for item in cart_items:
            subtotal = round(item["quantity"] * item["price"], 2)
            cursor.execute(
                """
                INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, subtotal)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (order_id, item["product_id"], item["product_name"], item["quantity"], item["price"], subtotal)
            )

            new_stock = item["stock"] - item["quantity"]
            cursor.execute("UPDATE products SET stock = %s WHERE id = %s", (new_stock, item["product_id"]))

            if new_stock <= LOW_STOCK_THRESHOLD:
                alerts_to_send.append({
                    "name": item["product_name"],
                    "id": item["product_id"],
                    "stock": new_stock,
                    "status": "Out of Stock" if new_stock <= 0 else "Low Stock"
                })

        # Clear cart
        cursor.execute("DELETE FROM cart_items WHERE user_id = %s", (resolved_user_id,))
        db.commit()
        cursor.close()
        db.close()

        # Trigger background email alerts
        for alert in alerts_to_send:
            try:
                send_low_stock_alert(
                    product_name=alert["name"],
                    product_id=alert["id"],
                    current_stock=alert["stock"],
                    status=alert["status"]
                )
            except Exception:
                pass

        # Broadcast real-time order creation from cart checkout asynchronously
        try:
            broadcast_new_order({
                "id": order_id,
                "user_id": resolved_user_id,
                "customer": resolved_customer,
                "product": summary_str,
                "quantity": total_quantity,
                "total_amount": round(total_amount, 2),
                "status": "Pending"
            })
        except Exception as ws_err:
            print(f"[WebSocket Broadcast Warning] checkout_cart tool: {ws_err}")

        return (
            f"🎉 **Checkout Successful!**\n"
            f"- **Order ID:** #{order_id}\n"
            f"- **Customer:** {resolved_customer}\n"
            f"- **Items Ordered:** {summary_str}\n"
            f"- **Total Amount:** ${total_amount:.2f}\n"
            f"- **Status:** Pending\n\n"
            f"Your shopping cart has been cleared. Thank you for your purchase!"
        )
    except Exception as e:
        return f"Error during checkout: {e}"