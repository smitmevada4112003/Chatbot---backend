import os
from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from prompt import system_message
from tools import (
    get_products, get_product_by_id, get_product_by_name,
    get_low_stock_products,
    get_orders, get_order_by_id, get_orders_by_customer,
    place_order, cancel_order, update_order_status,
    get_product_reviews,
    add_to_cart, view_cart, checkout_cart
)

load_dotenv()

from config import GEMINI_API_KEY, GROQ_API_KEY

# Tech Stack Constraint: Gemini (ChatGoogleGenerativeAI) with Groq fallback
if GEMINI_API_KEY:
    from langchain_google_genai import ChatGoogleGenerativeAI
    llm = ChatGoogleGenerativeAI(
        model="gemini-1.5-flash",
        temperature=0,
        google_api_key=GEMINI_API_KEY
    )
elif GROQ_API_KEY:
    from langchain_groq import ChatGroq
    llm = ChatGroq(
        model="openai/gpt-oss-120b",
        temperature=0,
        api_key=GROQ_API_KEY
    )
else:
    from langchain_google_genai import ChatGoogleGenerativeAI
    llm = ChatGoogleGenerativeAI(
        model="gemini-1.5-flash",
        temperature=0
    )

tools = [
    get_products, get_product_by_id, get_product_by_name,
    get_low_stock_products,
    get_orders, get_order_by_id, get_orders_by_customer,
    place_order, cancel_order, update_order_status,
    get_product_reviews,
    add_to_cart, view_cart, checkout_cart
]

# Create LangGraph ReAct agent accepting message history lists
agent = create_react_agent(
    model=llm,
    tools=tools,
    prompt=system_message
)