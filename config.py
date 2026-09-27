import os
from dotenv import load_dotenv

load_dotenv()

PRODUCT_API = "http://127.0.0.1:8000/products"
ORDER_API = "http://127.0.0.1:8000/orders"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")