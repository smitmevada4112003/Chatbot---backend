import asyncio
import threading
from typing import List, Optional
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState


class ConnectionManager:
    """
    Manages active WebSocket connections for real-time live events.
    Supports non-blocking async and synchronous background broadcast calls across FastAPI routes and tools.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    async def connect(self, websocket: WebSocket):
        """Accepts and stores an active client WebSocket connection."""
        await websocket.accept()
        with self._lock:
            if websocket not in self.active_connections:
                self.active_connections.append(websocket)
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        print(f"[WebSocket] Client connected. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Removes a disconnected client."""
        with self._lock:
            while websocket in self.active_connections:
                self.active_connections.remove(websocket)
        print(f"[WebSocket] Client disconnected. Total active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """
        Broadcasts a JSON message to all currently connected clients.
        Each client send is wrapped in a strict 2-second timeout so a dead/stalled
        connection can NEVER hang the broadcast or server.
        """
        with self._lock:
            current_conns = list(self.active_connections)

        dead_connections = []
        for connection in current_conns:
            try:
                if connection.client_state != WebSocketState.CONNECTED:
                    dead_connections.append(connection)
                    continue
                # 2-second per-connection send timeout to prevent hanging on slow/dead clients
                await asyncio.wait_for(connection.send_json(message), timeout=2.0)
            except Exception:
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)

    def broadcast_sync(self, message: dict):
        """
        Thread-safe and synchronous-friendly broadcast helper.
        Can be called safely from regular def routes, background threads, or chatbot tools.
        Wrapped in comprehensive try/except so failures never bubble up.
        """
        try:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None

            if current_loop and current_loop.is_running():
                current_loop.create_task(self.broadcast(message))
            elif self.loop and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)
            else:
                # Dispatch in a daemon thread so it never blocks synchronous callers
                threading.Thread(target=self._run_async_broadcast, args=(message,), daemon=True).start()
        except Exception as e:
            print(f"[WebSocket Warning] Failed to broadcast message: {e}")

    def _run_async_broadcast(self, message: dict):
        """Helper to run broadcast coroutine safely in standalone background thread."""
        try:
            asyncio.run(self.broadcast(message))
        except Exception as e:
            print(f"[WebSocket Background Thread Warning]: {e}")

    def broadcast_new_order(self, order_data: dict):
        """
        Broadcasts a new order notification to all connected Admin Dashboard clients.
        Sends a JSON message with type 'NEW_ORDER' and order details.
        """
        self.broadcast_sync({
            "type": "NEW_ORDER",
            "data": order_data
        })


manager = ConnectionManager()


def broadcast_new_order(order_data: dict):
    """
    Convenience function to broadcast a new order payload to all active WebSocket clients.
    Can be safely called from sync routes, background tasks, or LangChain tools.
    """
    try:
        manager.broadcast_new_order(order_data)
    except Exception as e:
        print(f"[WebSocket Broadcast Error Caught]: {e}")


def safe_broadcast_new_order(order_data: dict):
    """Alias for broadcast_new_order with explicit top-level exception insulation."""
    try:
        broadcast_new_order(order_data)
    except Exception as e:
        print(f"[safe_broadcast_new_order] Suppressed error: {e}")


def broadcast_in_background(message: dict):
    """Spawns broadcast in a daemon thread so it never blocks the caller."""
    try:
        threading.Thread(target=manager.broadcast_sync, args=(message,), daemon=True).start()
    except Exception as e:
        print(f"[broadcast_in_background] Thread error: {e}")


