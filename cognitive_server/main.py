"""
Cognitive Server — FastAPI Application Entry Point
Phase 0: Health endpoint | Phase 1: Signal ingestion | Phase 2+: Full intelligence
"""

import os
import sys
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cognitive_server.api import signals, load, interventions, decisions, settings, personalization
from cognitive_server.db import sqlite_store
from cognitive_server.interventions.engine import evaluate_interventions

logger = logging.getLogger("cognitive.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB, start intervention evaluation loop. Shutdown: close connections."""
    db_path = os.environ.get("COGNITIVE_DB_PATH", "cognitive.db")
    await sqlite_store.initialize(db_path)
    print(f"[cognitive] Database initialized at {db_path}")

    # Start background intervention evaluation loop (every 30 seconds)
    intervention_task = asyncio.create_task(_intervention_loop())
    print("[cognitive] Intervention evaluation loop started")

    yield

    # Shutdown
    intervention_task.cancel()
    try:
        await intervention_task
    except asyncio.CancelledError:
        pass
    await sqlite_store.close()
    print("[cognitive] Database connection closed")


async def _intervention_loop():
    """Periodically evaluate intervention rules (CLS thresholds, smart release, urgency bypass)."""
    while True:
        try:
            await asyncio.sleep(30)  # Evaluate every 30 seconds
            result = await evaluate_interventions()
            if result.get("action") == "evaluated" and result.get("released_this_cycle", 0) > 0:
                logger.info(f"Intervention: released {result['released_this_cycle']} notifications")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Intervention loop error: {e}")


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
app.include_router(settings.router, prefix="/api/v1")
app.include_router(personalization.router, prefix="/api/v1")


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