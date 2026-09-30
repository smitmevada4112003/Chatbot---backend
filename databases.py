import os
from dotenv import load_dotenv
import mysql.connector

import mysql.connector.pooling

load_dotenv()

import threading

_connection_pool = None
_pool_lock = threading.Lock()

def get_db_pool():
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                host = os.getenv("DB_HOST", "altaria.proxy.rlwy.net")
                port = int(os.getenv("DB_PORT", "33460"))
                user = os.getenv("DB_USER", "root")
                password = os.getenv("DB_PASSWORD", "zzahymaTnAFOjklYreKaTyctFvhrvvXk")
                database = os.getenv("DB_NAME", "railway")

                try:
                    _connection_pool = mysql.connector.pooling.MySQLConnectionPool(
                        pool_name="app_db_pool",
                        pool_size=3,
                        pool_reset_session=False,
                        host=host,
                        port=port,
                        user=user,
                        password=password,
                        database=database,
                        connection_timeout=15,
                        use_pure=True
                    )
                except Exception as e:
                    print(f"[DB Warning] Could not initialize connection pool: {e}")
                    _connection_pool = None
    return _connection_pool

def get_db_connection():
    pool = get_db_pool()
    if pool:
        try:
            cnx = pool.get_connection()
            try:
                cnx.autocommit = True
            except Exception:
                pass
            if not cnx.is_connected():
                try:
                    cnx.ping(reconnect=True, attempts=1, delay=0.1)
                except Exception:
                    pass
            return cnx
        except Exception:
            pass

    # Direct fallback if pool is empty or unavailable
    host = os.getenv("DB_HOST", "altaria.proxy.rlwy.net")
    port = int(os.getenv("DB_PORT", "33460"))
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "zzahymaTnAFOjklYreKaTyctFvhrvvXk")
    database = os.getenv("DB_NAME", "railway")

    cnx = mysql.connector.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        connection_timeout=15,
        use_pure=True
    )
    try:
        cnx.autocommit = True
    except Exception:
        pass
    return cnx

def ensure_stock_column(db=None):
    """Ensures the 'stock' column exists on the products table."""
    should_close = False
    try:
        if db is None:
            db = get_db_connection()
            should_close = True
        cursor = db.cursor()
        cursor.execute("SHOW COLUMNS FROM products LIKE 'stock'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE products ADD COLUMN stock INT NOT NULL DEFAULT 10")
            db.commit()
            print("[DB Migration] Added 'stock' column with DEFAULT 10 to 'products' table.")
        cursor.close()
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/add stock column: {e}")
    finally:
        if should_close and db:
            db.close()

def standardize_existing_order_statuses(db=None):
    """Standardizes existing order status variations in MySQL to 'Cancelled', 'Completed', 'Pending'."""
    should_close = False
    try:
        if db is None:
            db = get_db_connection()
            should_close = True
        cursor = db.cursor()
        cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE LOWER(TRIM(status)) IN ('cancelled', 'canceled', 'cancel')")
        cursor.execute("UPDATE orders SET status = 'Completed' WHERE LOWER(TRIM(status)) IN ('completed', 'delivered')")
        cursor.execute("UPDATE orders SET status = 'Pending' WHERE LOWER(TRIM(status)) IN ('pending')")
        db.commit()
        cursor.close()
    except Exception as e:
        print(f"[DB Warning] Could not standardize statuses: {e}")
    finally:
        if should_close and db:
            db.close()

def ensure_users_table_and_admin(db=None):
    """Ensures the 'users' table exists and seeds the default admin user."""
    should_close = False
    try:
        import os
        from auth_utils import hash_password

        if db is None:
            db = get_db_connection()
            should_close = True
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
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create users table: {e}")
    finally:
        if should_close and db:
            db.close()

def ensure_orders_user_id_column(db=None):
    """Ensures the 'user_id' column exists on 'orders' table linked to 'users.id'."""
    should_close = False
    try:
        if db is None:
            db = get_db_connection()
            should_close = True
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
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/add user_id column to orders: {e}")
    finally:
        if should_close and db:
            db.close()

def ensure_password_resets_table(db=None):
    """Ensures the 'password_resets' table exists with columns: id, user_id, token, expires_at, used."""
    should_close = False
    try:
        if db is None:
            db = get_db_connection()
            should_close = True
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
        print("[DB Migration] Verified/created 'password_resets' table.")
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create password_resets table: {e}")
    finally:
        if should_close and db:
            db.close()

def ensure_email_verifications_table(db=None):
    """Ensures the 'email_verifications' table exists with columns: id, user_id, token, expires_at, used."""
    should_close = False
    try:
        if db is None:
            db = get_db_connection()
            should_close = True
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
        print("[DB Migration] Verified/created 'email_verifications' table and 'is_verified' column.")
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create email_verifications table: {e}")
    finally:
        if should_close and db:
            db.close()

def ensure_reviews_table(db=None):
    """Ensures the 'reviews' table exists with columns: id, product_id, user_id, rating (1-5), comment, created_at."""
    should_close = False
    try:
        if db is None:
            db = get_db_connection()
            should_close = True
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
        print("[DB Migration] Verified/created 'reviews' table.")
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create reviews table: {e}")
    finally:
        if should_close and db:
            db.close()

def ensure_cart_and_order_items_tables(db=None):
    """
    Ensures 'cart_items' and 'order_items' tables exist, and ensures
    'total_amount' column exists on 'orders' table.
    """
    should_close = False
    try:
        if db is None:
            db = get_db_connection()
            should_close = True
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
        print("[DB Migration] Verified/created 'cart_items' and 'order_items' tables.")
    except Exception as e:
        print(f"[DB Migration Warning] Could not verify/create cart and order_items tables: {e}")
    finally:
        if should_close and db:
            db.close()

_migrations_run = False

def run_startup_migrations():
    global _migrations_run
    if _migrations_run:
        return
    try:
        db = get_db_connection()
        ensure_stock_column(db)
        standardize_existing_order_statuses(db)
        ensure_users_table_and_admin(db)
        ensure_orders_user_id_column(db)
        ensure_password_resets_table(db)
        ensure_email_verifications_table(db)
        ensure_reviews_table(db)
        ensure_cart_and_order_items_tables(db)
        db.close()
        _migrations_run = True
    except Exception as e:
        print(f"[DB Warning] Could not run startup migrations: {e}")

# Note: Migrations have already run and tables exist in database.
# Do not run synchronously on import to avoid blocking application startup.
# run_startup_migrations()





