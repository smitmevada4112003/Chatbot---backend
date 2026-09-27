import asyncio
from typing import List, Optional
from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """
    Manages active WebSocket connections for real-time live events.
    Supports both async and synchronous broadcast calls across FastAPI routes and tools.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket):
        """Accepts and stores an active client WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        print(f"[WebSocket] Client connected. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Removes a disconnected client."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"[WebSocket] Client disconnected. Total active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcasts a JSON message to all currently connected clients."""
        dead_connections = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)

    def broadcast_sync(self, message: dict):
        """
        Thread-safe and synchronous-friendly broadcast helper.
        Can be called safely from regular def routes, background threads, or chatbot tools.
        """
        try:
            # Check if there is an active running loop in the current thread
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None

            if current_loop and current_loop.is_running():
                current_loop.create_task(self.broadcast(message))
            elif self.loop and self.loop.is_running():
                # Dispatched from a worker thread (e.g. FastAPI threadpool or LangChain agent)
                asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)
            else:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(self.broadcast(message))
                    else:
                        loop.run_until_complete(self.broadcast(message))
                except RuntimeError:
                    asyncio.run(self.broadcast(message))
        except Exception as e:
            print(f"[WebSocket Warning] Failed to broadcast message: {e}")

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
    manager.broadcast_new_order(order_data)

