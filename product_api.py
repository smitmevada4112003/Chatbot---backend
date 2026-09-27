from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from databases import get_db_connection
from auth_utils import require_admin

router = APIRouter()


class Product(BaseModel):
    name: str
    price: int
    stock: int = 10


# GET all products
@router.get("/products")
def get_products():
    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("""
        SELECT 
            p.id, 
            p.name, 
            p.price, 
            COALESCE(p.stock, 0) AS stock,
            COALESCE(ROUND(AVG(r.rating), 1), 0.0) AS average_rating,
            COUNT(r.id) AS review_count
        FROM products p
        LEFT JOIN reviews r ON p.id = r.product_id
        GROUP BY p.id, p.name, p.price, p.stock
        ORDER BY p.id ASC
    """)
    rows = cursor.fetchall()

    products = []
    for row in rows:
        products.append({
            "id": row[0],
            "name": row[1],
            "price": row[2],
            "stock": row[3],
            "average_rating": float(row[4]) if row[4] is not None else 0.0,
            "review_count": int(row[5]) if row[5] is not None else 0
        })

    cursor.close()
    db.close()
    return products


# GET one product
@router.get("/products/{product_id}")
def get_product(product_id: int):
    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("""
        SELECT 
            p.id, 
            p.name, 
            p.price, 
            COALESCE(p.stock, 0) AS stock,
            COALESCE(ROUND(AVG(r.rating), 1), 0.0) AS average_rating,
            COUNT(r.id) AS review_count
        FROM products p
        LEFT JOIN reviews r ON p.id = r.product_id
        WHERE p.id = %s
        GROUP BY p.id, p.name, p.price, p.stock
    """, (product_id,))
    row = cursor.fetchone()

    cursor.close()
    db.close()

    if row is None:
        return {"message": "Product not found"}

    return {
        "id": row[0],
        "name": row[1],
        "price": row[2],
        "stock": row[3],
        "average_rating": float(row[4]) if row[4] is not None else 0.0,
        "review_count": int(row[5]) if row[5] is not None else 0
    }


# GET low stock products
@router.get("/products/alerts/low-stock")
def get_low_stock_products(threshold: int = 5):
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute(
        "SELECT id, name, price, COALESCE(stock, 0) as stock FROM products WHERE COALESCE(stock, 0) <= %s ORDER BY stock ASC",
        (threshold,)
    )
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return {
        "threshold": threshold,
        "count": len(rows),
        "low_stock_products": rows
    }


# ADD product (Admin only)
@router.post("/products")
def add_product(product: Product, admin_user: dict = Depends(require_admin)):
    db = get_db_connection()
    cursor = db.cursor()

    stock_val = product.stock if product.stock is not None else 10

    cursor.execute(
        "INSERT INTO products (name, price, stock) VALUES (%s, %s, %s)",
        (product.name, product.price, stock_val)
    )

    db.commit()
    product_id = cursor.lastrowid

    cursor.close()
    db.close()

    return {
        "message": "Product added successfully",
        "product_id": product_id,
        "stock": stock_val
    }


# UPDATE product (Admin only)
@router.put("/products/{product_id}")
def update_product(product_id: int, product: Product, admin_user: dict = Depends(require_admin)):
    db = get_db_connection()
    cursor = db.cursor()

    stock_val = product.stock if product.stock is not None else 10

    cursor.execute(
        """
        UPDATE products
        SET name = %s, price = %s, stock = %s
        WHERE id = %s
        """,
        (product.name, product.price, stock_val, product_id)
    )

    db.commit()
    cursor.close()
    db.close()

    return {
        "message": "Product updated successfully"
    }


# DELETE product (Admin only)
@router.delete("/products/{product_id}")
def delete_product(product_id: int, admin_user: dict = Depends(require_admin)):
    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute(
        "DELETE FROM products WHERE id = %s",
        (product_id,)
    )

    db.commit()
    cursor.close()
    db.close()

    return {
        "message": "Product deleted successfully"
    }


