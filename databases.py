import mysql.connector

def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="productdatabase",
        connection_timeout=5
    )

def ensure_stock_column():
    """Ensures the 'stock' column exists on the products table."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("SHOW COLUMNS FROM products LIKE 'stock'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE products ADD COLUMN stock INT NOT NULL DEFAULT 10")
            db.commit()
            print("[DB Migration] Added 'stock' column with DEFAULT 10 to 'products' table.")
        cursor.close()
        db.close()
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/add stock column: {e}")

def standardize_existing_order_statuses():
    """Standardizes existing order status variations in MySQL to 'Cancelled', 'Completed', 'Pending'."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE LOWER(TRIM(status)) IN ('cancelled', 'canceled', 'cancel')")
        cursor.execute("UPDATE orders SET status = 'Completed' WHERE LOWER(TRIM(status)) IN ('completed', 'delivered')")
        cursor.execute("UPDATE orders SET status = 'Pending' WHERE LOWER(TRIM(status)) IN ('pending')")
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"[DB Warning] Could not standardize statuses: {e}")

def ensure_users_table_and_admin():
    """Ensures the 'users' table exists and seeds the default admin user."""
    try:
        import os
        from auth_utils import hash_password

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(150) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'customer',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()

        # Seed default admin user if none exists
        default_admin_email = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@example.com").strip().lower()
        default_admin_pass = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin123!")

        cursor.execute("SELECT id, role FROM users WHERE LOWER(email) = %s", (default_admin_email,))
        existing_admin = cursor.fetchone()
        if not existing_admin:
            hashed_pw = hash_password(default_admin_pass)
            cursor.execute(
                "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                ("System Admin", default_admin_email, hashed_pw, "admin")
            )
            db.commit()
            print(f"[DB Migration] Default admin user initialized: {default_admin_email}")

        cursor.close()
        db.close()
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create users table: {e}")

def ensure_orders_user_id_column():
    """Ensures the 'user_id' column exists on 'orders' table linked to 'users.id'."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("SHOW COLUMNS FROM orders LIKE 'user_id'")
        if not cursor.fetchone():
            cursor.execute("""
                ALTER TABLE orders
                ADD COLUMN user_id INT NULL AFTER id,
                ADD CONSTRAINT fk_orders_users
                FOREIGN KEY (user_id) REFERENCES users(id)
                ON DELETE SET NULL
            """)
            db.commit()
            print("[DB Migration] Added 'user_id' foreign key column to 'orders' table.")
        cursor.close()
        db.close()
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/add user_id column to orders: {e}")

def ensure_password_resets_table():
    """Ensures the 'password_resets' table exists with columns: id, user_id, token, expires_at, used."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                token VARCHAR(255) NOT NULL UNIQUE,
                expires_at DATETIME NOT NULL,
                used TINYINT(1) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_token (token),
                INDEX idx_user_id (user_id),
                CONSTRAINT fk_password_resets_users
                    FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)
        db.commit()
        cursor.close()
        db.close()
        print("[DB Migration] Verified/created 'password_resets' table.")
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create password_resets table: {e}")

def ensure_email_verifications_table():
    """Ensures the 'email_verifications' table exists with columns: id, user_id, token, expires_at, used."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_verifications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                token VARCHAR(255) NOT NULL UNIQUE,
                expires_at DATETIME NOT NULL,
                used TINYINT(1) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_verify_token (token),
                INDEX idx_verify_user_id (user_id),
                CONSTRAINT fk_email_verifications_users
                    FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)
        # Ensure is_verified column on users table if not exists
        cursor.execute("SHOW COLUMNS FROM users LIKE 'is_verified'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE users ADD COLUMN is_verified TINYINT(1) NOT NULL DEFAULT 0")
        db.commit()
        cursor.close()
        db.close()
        print("[DB Migration] Verified/created 'email_verifications' table and 'is_verified' column.")
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create email_verifications table: {e}")

def ensure_reviews_table():
    """Ensures the 'reviews' table exists with columns: id, product_id, user_id, rating (1-5), comment, created_at."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                user_id INT NOT NULL,
                rating INT NOT NULL,
                comment TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT chk_rating CHECK (rating >= 1 AND rating <= 5),
                INDEX idx_reviews_product_id (product_id),
                INDEX idx_reviews_user_id (user_id),
                CONSTRAINT fk_reviews_products
                    FOREIGN KEY (product_id) REFERENCES products(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_reviews_users
                    FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)
        db.commit()
        cursor.close()
        db.close()
        print("[DB Migration] Verified/created 'reviews' table.")
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create reviews table: {e}")

def ensure_cart_and_order_items_tables():
    """
    Ensures 'cart_items' and 'order_items' tables exist, and ensures
    'total_amount' column exists on 'orders' table.
    """
    try:
        db = get_db_connection()
        cursor = db.cursor()

        # 1. Cart Items Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cart_items (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                product_id INT NOT NULL,
                quantity INT NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_product_cart (user_id, product_id),
                INDEX idx_cart_user (user_id),
                CONSTRAINT fk_cart_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_cart_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
        """)

        # 2. Order Items Table (for multi-item order line-items)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_id INT NOT NULL,
                product_id INT NOT NULL,
                product_name VARCHAR(150) NOT NULL,
                quantity INT NOT NULL,
                unit_price DECIMAL(10, 2) NOT NULL,
                subtotal DECIMAL(10, 2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_order_items_order (order_id),
                INDEX idx_order_items_product (product_id),
                CONSTRAINT fk_order_items_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                CONSTRAINT fk_order_items_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
        """)

        # 3. Ensure 'total_amount' column on 'orders' table
        cursor.execute("SHOW COLUMNS FROM orders LIKE 'total_amount'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE orders ADD COLUMN total_amount DECIMAL(10, 2) NULL AFTER quantity")
            print("[DB Migration] Added 'total_amount' column to 'orders' table.")

        db.commit()
        cursor.close()
        db.close()
        print("[DB Migration] Verified/created 'cart_items' and 'order_items' tables.")
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create cart and order_items tables: {e}")

# Run schema and data normalization on module load
ensure_stock_column()
standardize_existing_order_statuses()
ensure_users_table_and_admin()
ensure_orders_user_id_column()
ensure_password_resets_table()
ensure_email_verifications_table()
ensure_reviews_table()
ensure_cart_and_order_items_tables()





