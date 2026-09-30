from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from databases import get_db_connection
from auth import get_current_user

router = APIRouter(tags=["Reviews"])


# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class ReviewCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    comment: Optional[str] = Field(None, description="Optional text review")


# ==============================================================================
# 1. POST /products/{product_id}/reviews (Protected)
# ==============================================================================

@router.post("/products/{product_id}/reviews", status_code=status.HTTP_201_CREATED)
def create_or_update_review(
    product_id: int,
    payload: ReviewCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Submits a review for a product.
    Requires authentication via Bearer token (get_current_user).
    If the user has already reviewed the product, overwrites/updates their existing review.
    """
    rating = payload.rating
    comment = (payload.comment or "").strip() or None

    if rating < 1 or rating > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rating must be between 1 and 5."
        )

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # 1. Verify product exists
        cursor.execute("SELECT id, name FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product #{product_id} not found."
            )

        # 2. Get user_id from token payload or database
        user_id = current_user.get("id")
        user_email = (current_user.get("email") or current_user.get("sub") or "").strip().lower()

        if not user_id:
            cursor.execute("SELECT id FROM users WHERE LOWER(email) = %s", (user_email,))
            u = cursor.fetchone()
            if not u:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User account not found."
                )
            user_id = u["id"]

        # 3. Check if user already reviewed this product
        cursor.execute(
            "SELECT id, rating, comment FROM reviews WHERE product_id = %s AND user_id = %s",
            (product_id, user_id)
        )
        existing_review = cursor.fetchone()

        if existing_review:
            # Overwrite/update existing review
            cursor.execute(
                """
                UPDATE reviews 
                SET rating = %s, comment = %s, created_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (rating, comment, existing_review["id"])
            )
            db.commit()
            review_id = existing_review["id"]
            action = "updated"
            message = "Your existing review for this product has been updated."
        else:
            # Insert new review
            cursor.execute(
                """
                INSERT INTO reviews (product_id, user_id, rating, comment)
                VALUES (%s, %s, %s, %s)
                """,
                (product_id, user_id, rating, comment)
            )
            db.commit()
            review_id = cursor.lastrowid
            action = "created"
            message = "Review submitted successfully."

        # Fetch updated product summary stats
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
        from cache_utils import products_cache
        products_cache.invalidate()

        return {
            "status": "success",
            "action": action,
            "message": message,
            "review": {
                "id": review_id,
                "product_id": product_id,
                "product_name": product["name"],
                "user_id": user_id,
                "user_name": current_user.get("name") or user_email.split("@")[0],
                "rating": rating,
                "comment": comment
            },
            "product_stats": {
                "average_rating": float(stats["average_rating"]),
                "review_count": int(stats["review_count"])
            }
        }
    finally:
        cursor.close()
        db.close()


# ==============================================================================
# 2. GET /products/{product_id}/reviews (Public)
# ==============================================================================

@router.get("/products/{product_id}/reviews")
def get_product_reviews(product_id: int):
    """
    Public route: Returns all reviews for a product, including average rating and review count.
    """
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # 1. Verify product exists
        cursor.execute("SELECT id, name, price FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product #{product_id} not found."
            )

        # 2. Calculate average rating and total review count
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

        # 3. Retrieve all individual reviews joined with user info
        cursor.execute(
            """
            SELECT 
                r.id,
                r.product_id,
                r.user_id,
                COALESCE(u.name, 'Customer') AS user_name,
                u.email AS user_email,
                r.rating,
                r.comment,
                r.created_at
            FROM reviews r
            LEFT JOIN users u ON r.user_id = u.id
            WHERE r.product_id = %s
            ORDER BY r.created_at DESC
            """,
            (product_id,)
        )
        reviews_rows = cursor.fetchall()

        formatted_reviews = []
        for row in reviews_rows:
            formatted_reviews.append({
                "id": row["id"],
                "product_id": row["product_id"],
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "user_email": row["user_email"],
                "rating": row["rating"],
                "comment": row["comment"],
                "created_at": str(row["created_at"]) if row["created_at"] else None
            })

        return {
            "product_id": product["id"],
            "product_name": product["name"],
            "average_rating": float(summary["average_rating"]),
            "review_count": int(summary["review_count"]),
            "reviews": formatted_reviews
        }
    finally:
        cursor.close()
        db.close()
