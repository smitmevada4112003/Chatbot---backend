from fastapi import APIRouter, Depends, HTTPException
from databases import get_db_connection
from auth import require_admin

from cache_utils import dashboard_cache

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats")
def get_dashboard_stats(admin_user: dict = Depends(require_admin)):
    """
    Computes summary metrics, order status breakdowns,
    estimated revenue, and top products.
    """
    cached = dashboard_cache.get("stats")
    if cached is not None:
        return cached

    try:
        db = get_db_connection()
        cursor = db.cursor()

        # Product count
        cursor.execute("SELECT COUNT(*) FROM products")
        product_count_row = cursor.fetchone()
        total_products = product_count_row[0] if product_count_row else 0

        # Low stock count (<= 5)
        cursor.execute("SELECT COUNT(*) FROM products WHERE COALESCE(stock, 0) <= 5")
        low_stock_row = cursor.fetchone()
        low_stock_count = low_stock_row[0] if low_stock_row else 0

        # Orders count and quantity
        cursor.execute("SELECT COUNT(*), COALESCE(SUM(quantity), 0) FROM orders")
        order_row = cursor.fetchone()
        total_orders = order_row[0] if order_row else 0
        total_items_ordered = int(order_row[1]) if order_row and order_row[1] is not None else 0

        # Status breakdown (case-insensitive SQL grouping + normalized statuses)
        cursor.execute("SELECT LOWER(TRIM(status)), COUNT(*) FROM orders GROUP BY LOWER(TRIM(status))")
        status_rows = cursor.fetchall()
        status_counts = {"Pending": 0, "Completed": 0, "Cancelled": 0}
        for row in status_rows:
            raw_s = (row[0] or "").strip().lower()
            count = int(row[1] or 0)
            if raw_s in ("cancelled", "canceled", "cancel", "cancelling"):
                status_counts["Cancelled"] += count
            elif raw_s in ("completed", "delivered", "done"):
                status_counts["Completed"] += count
            elif raw_s in ("pending", "processing", "in progress"):
                status_counts["Pending"] += count
            elif row[0]:
                status_counts[row[0].strip().title()] = count

        # Fast revenue calculation from total_amount
        cursor.execute("SELECT COALESCE(SUM(total_amount), 0) FROM orders")
        rev_row = cursor.fetchone()
        estimated_revenue = float(rev_row[0]) if rev_row and rev_row[0] is not None else 0.0

        # Top products by quantity ordered
        cursor.execute("""
            SELECT product, SUM(quantity) as total_qty, COUNT(*) as orders_count
            FROM orders
            GROUP BY product
            ORDER BY total_qty DESC
            LIMIT 5
        """)
        top_product_rows = cursor.fetchall()
        top_products = [
            {"product": r[0], "total_quantity": int(r[1]), "orders_count": int(r[2])}
            for r in top_product_rows
        ]

        # Recent orders
        cursor.execute("SELECT id, customer, product, quantity, status FROM orders ORDER BY id DESC LIMIT 5")
        recent_order_rows = cursor.fetchall()
        recent_orders = [
            {"id": r[0], "customer": r[1], "product": r[2], "quantity": r[3], "status": r[4]}
            for r in recent_order_rows
        ]

        cursor.close()
        db.close()

        result = {
            "status": "success",
            "total_products": total_products,
            "low_stock_count": low_stock_count,
            "total_orders": total_orders,
            "total_items_ordered": total_items_ordered,
            "estimated_revenue": estimated_revenue,
            "status_counts": status_counts,
            "top_products": top_products,
            "recent_orders": recent_orders
        }
        dashboard_cache.set("stats", result, ttl=4.0)
        return result
    except HTTPException:
        raise
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "total_products": 0,
            "total_orders": 0,
            "total_items_ordered": 0,
            "estimated_revenue": 0.0,
            "status_counts": {},
            "top_products": [],
            "recent_orders": []
        }
