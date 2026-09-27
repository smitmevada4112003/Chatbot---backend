from datetime import datetime
from typing import Optional, Dict, List
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent import agent
from auth import get_optional_current_user

router = APIRouter()

# ==============================================================================
# IN-MEMORY CACHE FOR TEMPORARY CONVERSATION HISTORY
# ==============================================================================
# IMPORTANT ARCHITECTURAL NOTE:
# - This conversation history is temporary, session-scoped data stored in RAM.
# - It is deliberately NOT written to MySQL. Only permanent business data
#   (products, orders, stock) is stored in the MySQL database.
# - VOLATILE MEMORY WARNING:
#   1. Server Restarts: This in-memory dictionary resets if FastAPI restarts.
#   2. Multi-Worker / Multi-Instance: In-memory state is not shared across
#      separate worker processes or container instances without a distributed
#      store like Redis.
#   3. For this small-scale application, this volatile dictionary is acceptable
#      and lightweight.
# ==============================================================================
session_store: Dict[str, List[dict]] = {}

# Sliding window: Trim history to last 12 messages (within the 10-15 message requirement)
# to avoid excessive token consumption and unbounded memory growth.
MAX_HISTORY_MESSAGES = 12


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default_session"
    user_name: Optional[str] = None
    user_email: Optional[str] = None


@router.post("/chat")
def chat_with_bot(
    request: ChatRequest,
    current_user: Optional[dict] = Depends(get_optional_current_user)
):
    """
    Handles conversational interactions with session memory.
    Automatically detects logged-in user via JWT token and provides identity context to agent.
    """
    session_id = (request.session_id or "default_session").strip()

    # Fallback to payload identity if token was not sent
    if not current_user and (request.user_email or request.user_name):
        current_user = {
            "name": request.user_name,
            "email": request.user_email or "",
            "id": None,
            "role": "customer"
        }

    # Step 1, 2 & 3: Check cache, retrieve existing or initialize empty list
    if session_id in session_store:
        history = session_store[session_id]
    else:
        history = []
        session_store[session_id] = history

    # Ensure pre-existing history is trimmed before appending the new user message
    if len(history) >= MAX_HISTORY_MESSAGES:
        history = history[-(MAX_HISTORY_MESSAGES - 1):]

    # Step 4: Append the new user message
    user_message = {
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now().isoformat()
    }
    history.append(user_message)

    # Convert to LangChain message tuples: [("user", content), ("assistant", content), ...]
    messages_to_send = [(m["role"], m["content"]) for m in history]

    # If current_user is authenticated, prepend identity context so agent never asks for user's name
    if current_user:
        display_name = current_user.get("name") or current_user.get("email").split("@")[0].title()
        user_id = current_user.get("id")
        user_email = current_user.get("email")
        auth_context_str = (
            f"[AUTHENTICATED USER CONTEXT]\n"
            f"Active Customer: {display_name}\n"
            f"Email: {user_email}\n"
            f"User ID: {user_id}\n\n"
            f"DIRECTIVES:\n"
            f"1. You ALREADY know this customer's identity. NEVER ask 'What is your name?' or ask for their name/email.\n"
            f"2. When placing an order, call place_order(customer_name='{display_name}', product_name=..., quantity=..., user_id={user_id}).\n"
            f"3. When this user asks to view, track, or check their orders, immediately call get_orders_by_customer(customer_identifier='{user_id if user_id else display_name}') without asking who they are.\n"
            f"4. For shopping cart operations, pass user_id={user_id} to add_to_cart, view_cart, and checkout_cart."
        )
        messages_to_send = [("system", auth_context_str)] + messages_to_send

    try:
        # Step 5: Pass the full message history to agent.invoke()
        result = agent.invoke({"messages": messages_to_send})

        last_message = result["messages"][-1]
        reply_content = last_message.content
        if isinstance(reply_content, list):
            reply_text = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p)
                for p in reply_content
            )
        else:
            reply_text = str(reply_content)

        # Step 6: Append the assistant's reply to the history
        assistant_message = {
            "role": "assistant",
            "content": reply_text,
            "timestamp": datetime.now().isoformat()
        }
        history.append(assistant_message)

        # Step 7: Trim to last 10-15 messages (12) and save back to in-memory cache
        if len(history) > MAX_HISTORY_MESSAGES:
            history = history[-MAX_HISTORY_MESSAGES:]

        session_store[session_id] = history

        return {
            "reply": reply_text,
            "session_id": session_id,
            "history_count": len(history)
        }

    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            reply_text = "⚠️ API Quota exceeded. Please try again in a few moments."
        else:
            reply_text = f"⚠️ An error occurred: {error_msg}"

        history.append({
            "role": "assistant",
            "content": reply_text,
            "timestamp": datetime.now().isoformat()
        })

        if len(history) > MAX_HISTORY_MESSAGES:
            history = history[-MAX_HISTORY_MESSAGES:]

        session_store[session_id] = history

        return {
            "reply": reply_text,
            "session_id": session_id,
            "history_count": len(history)
        }


@router.get("/chat/history/{session_id}")
def get_session_history(session_id: str):
    """Retrieve the in-memory conversation history for a given session_id."""
    session_id = session_id.strip()
    history = session_store.get(session_id, [])
    return {
        "session_id": session_id,
        "max_limit": MAX_HISTORY_MESSAGES,
        "total_messages": len(history),
        "history": history
    }


@router.delete("/chat/history/{session_id}")
def clear_session_history(session_id: str):
    """Clear in-memory conversation history for a given session_id."""
    session_id = session_id.strip()
    if session_id in session_store:
        session_store[session_id] = []
    return {
        "status": "success",
        "message": f"History for session '{session_id}' has been cleared.",
        "session_id": session_id
    }


@router.post("/chat/reset")
def reset_chat_session(request: ChatRequest):
    """Reset the session history in the in-memory cache."""
    session_id = (request.session_id or "default_session").strip()
    if session_id in session_store:
        session_store[session_id] = []
    return {
        "status": "success",
        "message": f"Session '{session_id}' reset successfully.",
        "session_id": session_id
    }