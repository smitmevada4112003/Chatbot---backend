import sys
import asyncio
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from websocket_manager import manager

from product_api import router as product_router
from orders_api import router as order_router
from agent_api import router as agent_router
from dashboard_api import router as dashboard_router
from auth_api import router as auth_router
from reviews_api import router as review_router
from cart_api import router as cart_router


app = FastAPI()


# =======================================================
# CORS CONFIGURATION
# Allows requests from dev servers and all origins
# =======================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# =======================================================
# API ROUTES
# =======================================================

app.include_router(auth_router)
app.include_router(product_router)
app.include_router(order_router)
app.include_router(agent_router)
app.include_router(dashboard_router)
app.include_router(review_router)
app.include_router(cart_router)




# =======================================================
# HEALTH CHECK
# =======================================================

@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "message": "FastAPI connected to MySQL successfully"
    }


# =======================================================
# WEBSOCKET REAL-TIME NOTIFICATIONS
# =======================================================

@app.websocket("/ws/admin-notifications")
@app.websocket("/ws/orders")
async def websocket_orders(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        manager.disconnect(websocket)


# =======================================================
# SERVE FRONTEND (Single Server - http://localhost:8000)
# =======================================================

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if not FRONTEND_DIST.exists():
    FRONTEND_DIST = Path(__file__).resolve().parent.parent / "Frontend" / "dist"

if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def serve_root():
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}")
    def serve_frontend_spa(full_path: str):
        file_path = FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def home():
        return {
            "message": "FastAPI is running. Build frontend by running 'npm run build' inside the frontend folder."
        }