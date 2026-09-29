import mysql.connector

conn = mysql.connector.connect(
    host="altaria.proxy.rlwy.net",
    port=33460,
    user="root",
    password="EuxngIbRGSrwRNYFmjjyEPRfwFKLpbPS",
    database="railway"
)

cursor = conn.cursor()

cursor.execute("SHOW TABLES;")
tables = cursor.fetchall()
print("Tables in Railway database:")
for t in tables:
    print(" -", t[0])

cursor.execute("SELECT COUNT(*) FROM products;")
print("\nTotal products:", cursor.fetchone()[0])

cursor.execute("SELECT COUNT(*) FROM orders;")
print("Total orders:", cursor.fetchone()[0])

cursor.close()
conn.close()