import mysql.connector
import re

# Read the exported SQL file
with open(r"C:\Users\SMIT\Downloads\productdatabase.sql", "r", encoding="utf-8") as f:
    sql_script = f.read()

# Remove comment lines (phpMyAdmin adds -- comments and /*! ... */ hints)
lines = sql_script.split("\n")
cleaned_lines = []
for line in lines:
    stripped = line.strip()
    if stripped.startswith("--") or stripped.startswith("/*") or stripped == "":
        continue
    cleaned_lines.append(line)

cleaned_script = "\n".join(cleaned_lines)

# Connect to Railway MySQL
conn = mysql.connector.connect(
    host="altaria.proxy.rlwy.net",
    port=33460,
    user="root",
    password="EuxngIbRGSrwRNYFmjjyEPRfwFKLpbPS",
    database="railway"
)

cursor = conn.cursor()

# Disable foreign key checks temporarily to avoid order-dependency issues
cursor.execute("SET FOREIGN_KEY_CHECKS=0;")

# Split into statements and execute one by one
statements = cleaned_script.split(";")

success_count = 0
error_count = 0

for statement in statements:
    statement = statement.strip()
    if statement:
        try:
            cursor.execute(statement)
            success_count += 1
        except Exception as e:
            error_count += 1
            print(f"❌ Error: {e}")
            print(f"   Statement was: {statement[:100]}...")

cursor.execute("SET FOREIGN_KEY_CHECKS=1;")

conn.commit()
cursor.close()
conn.close()

print(f"\n✅ Done! {success_count} statements succeeded, {error_count} failed.")