"""
Cognitive Server - Settings API
Endpoints: GET/POST /api/v1/settings/*
Manages user preferences: intervention toggles, whitelist, schedule.
"""

from fastapi import APIRouter, HTTPException

from cognitive_server.db import sqlite_store

router = APIRouter(tags=["settings"])

# In-memory settings store (persisted to DB in future)
_settings = {
    "intervention_toggles": {
        "browser": True,
        "gmail": True,
        "slack": True,
        "calendar": True,
    },
    "whitelist": {
        "senders": [],
        "domains": [],
    },
    "schedule": {
        "work_start": 8,
        "work_end": 18,
        "release_interval": 90,
    },
}


@router.get("/settings/intervention-toggles",
            summary="Get intervention toggles",
            description="Returns which intervention categories are enabled.")
async def get_intervention_toggles():
    return _settings["intervention_toggles"]


@router.post("/settings/intervention-toggles",
             summary="Update intervention toggles",
             description="Enable/disable intervention categories.")
async def update_intervention_toggles(payload: dict):
    try:
        for key in ("browser", "gmail", "slack", "calendar"):
            if key in payload:
                _settings["intervention_toggles"][key] = bool(payload[key])
        return {"status": "updated", "toggles": _settings["intervention_toggles"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/whitelist",
            summary="Get urgency whitelist",
            description="Returns whitelisted senders and domains that always bypass deflection.")
async def get_whitelist():
    return _settings["whitelist"]


@router.post("/settings/whitelist",
             summary="Add to whitelist",
             description="Add a sender email or domain to the urgency whitelist.")
async def add_to_whitelist(payload: dict):
    try:
        entry_type = payload.get("type", "")
        value = payload.get("value", "").strip()

        if not value:
            raise ValueError("Value is required")

        if entry_type == "sender":
            if value not in _settings["whitelist"]["senders"]:
                _settings["whitelist"]["senders"].append(value)
        elif entry_type == "domain":
            if value not in _settings["whitelist"]["domains"]:
                _settings["whitelist"]["domains"].append(value)
        else:
            raise ValueError(f"Unknown type: {entry_type}")

        return {"status": "added", "type": entry_type, "value": value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/settings/whitelist/{entry_type}/{value}",
               summary="Remove from whitelist",
               description="Remove a sender or domain from the urgency whitelist.")
async def remove_from_whitelist(entry_type: str, value: str):
    try:
        if entry_type == "sender":
            if value in _settings["whitelist"]["senders"]:
                _settings["whitelist"]["senders"].remove(value)
                return {"status": "removed", "type": "sender", "value": value}
        elif entry_type == "domain":
            if value in _settings["whitelist"]["domains"]:
                _settings["whitelist"]["domains"].remove(value)
                return {"status": "removed", "type": "domain", "value": value}
        raise HTTPException(status_code=404, detail=f"{entry_type}/{value} not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/schedule",
            summary="Get schedule settings",
            description="Returns work hours and release interval preferences.")
async def get_schedule():
    return _settings["schedule"]


@router.post("/settings/schedule",
             summary="Update schedule settings",
             description="Update work hours and notification release interval.")
async def update_schedule(payload: dict):
    try:
        for key in ("work_start", "work_end", "release_interval"):
            if key in payload:
                val = int(payload[key])
                if key == "work_start" and not (0 <= val <= 23):
                    raise ValueError("work_start must be 0-23")
                if key == "work_end" and not (0 <= val <= 23):
                    raise ValueError("work_end must be 0-23")
                if key == "release_interval" and not (30 <= val <= 180):
                    raise ValueError("release_interval must be 30-180")
                _settings["schedule"][key] = val
        return {"status": "updated", "schedule": _settings["schedule"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
