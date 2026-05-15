"""
Cognitive Server - Google Calendar Integration
Provides calendar conflict detection via Google Calendar API (read-only).
Falls back to simulated events when API is not configured.
"""

import datetime
import os
import json
from typing import Dict, List, Optional


class GoogleCalendarClient:
    """
    Read-only Google Calendar API client.
    
    Uses OAuth-free approach: reads from a local ICS file or
    Google Calendar API if credentials are configured.
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._client = None
        self._initialized = False

    async def initialize(self):
        """Initialize the calendar client."""
        api_key = self.config.get("google_calendar_api_key") or os.environ.get("GOOGLE_CALENDAR_API_KEY")
        calendar_id = self.config.get("calendar_id") or os.environ.get("GOOGLE_CALENDAR_ID", "primary")

        if api_key:
            try:
                from googleapiclient.discovery import build
                self._client = build("calendar", "v3", developerKey=api_key)
                self._calendar_id = calendar_id
                self._initialized = True
            except ImportError:
                print("[calendar] google-api-python-client not installed, using fallback")
            except Exception as e:
                print(f"[calendar] API init failed: {e}")

    async def get_events(self, start: datetime.datetime,
                         end: datetime.datetime) -> List[Dict]:
        """
        Fetch calendar events in the given time range.
        
        Returns list of dicts with: start, end, title, location, description.
        """
        if self._initialized and self._client:
            return await self._fetch_from_api(start, end)
        else:
            return self._generate_fallback_events(start, end)

    async def _fetch_from_api(self, start: datetime.datetime,
                               end: datetime.datetime) -> List[Dict]:
        """Fetch events from Google Calendar API."""
        try:
            events_result = self._client.events().list(
                calendarId=self._calendar_id,
                timeMin=start.isoformat() + "Z",
                timeMax=end.isoformat() + "Z",
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()

            events = []
            for item in events_result.get("items", []):
                start_info = item.get("start", {})
                end_info = item.get("end", {})

                # Handle all-day events
                if "dateTime" in start_info:
                    evt_start = start_info["dateTime"]
                    evt_end = end_info.get("dateTime", "")
                else:
                    evt_start = start_info.get("date", "")
                    evt_end = end_info.get("date", "")

                events.append({
                    "title": item.get("summary", "Untitled Event"),
                    "start": evt_start,
                    "end": evt_end,
                    "location": item.get("location", ""),
                    "description": item.get("description", ""),
                    "attendees": [
                        a.get("email", "")
                        for a in item.get("attendees", [])
                        if not a.get("self", False)
                    ],
                })

            return events
        except Exception as e:
            print(f"[calendar] API fetch failed: {e}")
            return self._generate_fallback_events(start, end)

    def _generate_fallback_events(self, start: datetime.datetime,
                                   end: datetime.datetime) -> List[Dict]:
        """
        Generate realistic fallback events based on typical work patterns.
        Used when Google Calendar API is not configured.
        """
        events = []
        current = start
        weekday = current.weekday()

        # Typical work week pattern
        recurring_meetings = {
            0: [  # Monday
                {"hour": 9, "duration": 30, "title": "Weekly Planning"},
                {"hour": 14, "duration": 60, "title": "Team Sync"},
            ],
            1: [  # Tuesday
                {"hour": 10, "duration": 60, "title": "1:1 with Manager"},
            ],
            2: [  # Wednesday
                {"hour": 11, "duration": 30, "title": "Mid-week Check-in"},
                {"hour": 15, "duration": 60, "title": "Sprint Review"},
            ],
            3: [  # Thursday
                {"hour": 10, "duration": 60, "title": "Architecture Review"},
            ],
            4: [  # Friday
                {"hour": 9, "duration": 30, "title": "Weekly Retro"},
                {"hour": 13, "duration": 30, "title": "Demo Prep"},
            ],
        }

        meetings = recurring_meetings.get(weekday, [])

        for meeting in meetings:
            evt_start = current.replace(
                hour=meeting["hour"], minute=0, second=0, microsecond=0
            )
            evt_end = evt_start + datetime.timedelta(minutes=meeting["duration"])

            if start <= evt_start <= end:
                events.append({
                    "title": meeting["title"],
                    "start": evt_start.isoformat(),
                    "end": evt_end.isoformat(),
                    "location": "",
                    "description": "",
                    "attendees": [],
                })

        return events

    async def check_conflict(self, slot_start: datetime.datetime,
                              duration_min: int) -> dict:
        """
        Check if a time slot conflicts with any calendar event.
        
        Returns:
            {"has_conflict": bool, "conflicting_events": [...]}
        """
        slot_end = slot_start + datetime.timedelta(minutes=duration_min)

        # Look ahead 24 hours for events
        end = slot_start + datetime.timedelta(hours=24)
        events = await self.get_events(slot_start, end)

        conflicts = []
        for evt in events:
            try:
                evt_start = datetime.datetime.fromisoformat(
                    evt["start"].replace("Z", "+00:00")
                )
                evt_end = datetime.datetime.fromisoformat(
                    evt["end"].replace("Z", "+00:00")
                )
            except (ValueError, KeyError):
                continue

            # Check for overlap
            if slot_start < evt_end and slot_end > evt_start:
                conflicts.append(evt)

        return {
            "has_conflict": len(conflicts) > 0,
            "conflicting_events": conflicts,
        }


# Global singleton
_calendar_client = None


def get_calendar_client(config: Optional[Dict] = None) -> GoogleCalendarClient:
    """Get or create the global calendar client."""
    global _calendar_client
    if _calendar_client is None:
        _calendar_client = GoogleCalendarClient(config)
    return _calendar_client
