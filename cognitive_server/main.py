"""
Cognitive Server — FastAPI Application Entry Point
Phase 0: Health endpoint | Phase 1: Signal ingestion | Phase 2+: Full intelligence
"""

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cognitive_server.api import signals, load, interventions, decisions
from cognitive_server.db import sqlite_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB. Shutdown: close connections."""
    db_path = os.environ.get("COGNITIVE_DB_PATH", "cognitive.db")
    await sqlite_store.initialize(db_path)
    print(f"[cognitive] Database initialized at {db_path}")
    yield
    await sqlite_store.close()
    print("[cognitive] Database connection closed")


app = FastAPI(
    title="Cognitive \u2014 Ambient Intelligence API",
    description="Real-time cognitive load detection and interruption deflection.",
    version="1.0.0",
    lifespan=lifespan,
)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Local-only; extension and localhost
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes ---
app.include_router(signals.router, prefix="/api/v1")
app.include_router(load.router, prefix="/api/v1")
app.include_router(interventions.router, prefix="/api/v1")
app.include_router(decisions.router, prefix="/api/v1")


@app.get("/")
async def root():
    return JSONResponse(content={"status": "ok", "service": "cognitive-server"})


@app.get("/health")
async def health():
    return JSONResponse(content={"status": "healthy"})


# --- Run with uvicorn ---
# uvicorn main:app --host 127.0.0.1 --port 8000 --reload
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )