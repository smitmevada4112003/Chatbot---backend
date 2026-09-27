from databases import get_db_connection

try:
    db = get_db_connection()
    print("[SUCCESS] Connection to MySQL successful!")
    db.close()
except Exception as e:
    print("[ERROR] Connection failed:", e)