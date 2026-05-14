Cognitive
===========

An ambient intelligence layer that protects your attention by detecting cognitive overload in real-time and automatically deflecting interruptions.

![Cognitive Architecture](docs/architecture.png)

## What It Does

Cognitive observes your behavioral signals (keystroke patterns, window switches, scroll velocity, mouse dynamics), predicts your cognitive load via on-device ML, and intervenes only when necessary:

- **Holds notifications** when you're overloaded
- **Auto-drafts responses** to meeting requests and scheduling questions
- **Tracks focus time** and helps protect deep work blocks
- **Adapts to you** over 24-48 hours of use

All processing happens locally on your device. No data leaves your machine.

## Architecture

```
┌──────────────────────────────────────────────────┐
│                    USER'S DEVICE                  │
│                                                   │
│  ┌───────────────┐   ┌───────────────┐           │
│  │ Browser Ext.  │   │ Desktop Agent │           │
│  │ (Manifest V3) │   │   (Python)    │           │
│  │               │   │               │           │
│  │ • Signals     │   │ • App focus   │           │
│  │ • Notif block │   │ • Idle detect │           │
│  │ • Draft inject│   │ • Bridge      │           │
│  └───────┬───────┘   └───────┬───────┘           │
│          │                   │                    │
│          └───────────┬───────┘                    │
│                      │                            │
│              ┌───────▼───────┐                     │
│              │  FastAPI      │                     │
│              │  Local Server │                     │
│              │  :8000        │                     │
│              │               │                     │
│              │ • CLS Engine  │                     │
│              │ • Interventions│                    │
│              │ • Decision AI  │                    │
│              │ • SQLite DB   │                     │
│              └───────┬───────┘                     │
│                      │                            │
│              ┌───────▼───────┐                     │
│              │  TFLite Model │                     │
│              │  (on-device)  │                     │
│              └───────────────┘                     │
└──────────────────────────────────────────────────┘
```

## Quick Start (Development Mode)

### Prerequisites

- Python 3.10+
- Node.js 18+ (for extension development)
- Google Chrome or Chromium
- (Optional) macOS or Windows for desktop agent

### 1. Clone and Setup

```bash
git clone <repo-url>
cd Cognitive
```

### 2. Set Up the Server

```bash
cd cognitive-server
python -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

pip install -r requirements.txt
python main.py
```

The server starts at `http://127.0.0.1:8000`.

### 3. Verify the API

```bash
# Health check
curl http://127.0.0.1:8000/health

# Check load (no data yet — returns learning state)
curl http://127.0.0.1:8000/api/v1/load/current

# Send a test signal
curl -X POST http://127.0.0.1:8000/api/v1/signals \
  -H "Content-Type: application/json" \
  -d '{
    "signals": [{
      "session_id": "test-001",
      "timestamp": "2026-05-14T10:30:00Z",
      "kpm": 45.0,
      "switch_rate": 3.0,
      "scroll_velocity": 500.0,
      "scroll_delta": 200.0,
      "mouse_entropy": 0.6,
      "idle_ratio": 0.05,
      "tab_count": 12,
      "domain_switches": 3,
      "time_of_day": 0.3
    }]
  }'

# Get load score (should compute from signal)
curl http://127.0.0.1:8000/api/v1/load/current

# Test decision proxy
curl -X POST http://127.0.0.1:8000/api/v1/decisions/schedule \
  -H "Content-Type: application/json" \
  -d '{
    "proposed_slots": [
      "2026-05-14T14:00:00Z",
      "2026-05-14T16:00:00Z"
    ],
    "duration_minutes": 30,
    "context": "Sprint planning"
  }'
```

### 4. Run Tests

```bash
cd cognitive-server
python -m pytest tests/ -v
```

### 5. Load the Browser Extension

1. Open Chrome → `chrome://extensions`
2. Enable "Developer mode" (top right)
3. Click "Load unpacked"
4. Select `cognitive-extension/`
5. The green/gray badge should appear in your toolbar

### 6. Desktop Agent (macOS)

```bash
cd cognitive-desktop
chmod +x build/build.sh
./build/build.sh
./dist/cognitive-agent
```

## API Reference

### `POST /api/v1/signals`
Ingest behavioral signal batch.

```json
{
  "signals": [{
    "session_id": "uuid",
    "timestamp": "2026-05-14T10:30:00Z",
    "kpm": 45.2,
    "switch_rate": 3.1,
    "scroll_velocity": 500.0,
    "scroll_delta": 200.0,
    "mouse_entropy": 0.45,
    "idle_ratio": 0.12,
    "tab_count": 12,
    "domain_switches": 2,
    "time_of_day": 0.5
  }]
}
```

### `GET /api/v1/load/current`
Returns current Cognitive Load Score and state.

### `GET /api/v1/load/history?hours=2`
Returns CLS trend data for visualization.

### `GET /api/v1/interventions/active`
Returns held notifications and active intervention recommendations.

### `POST /api/v1/decisions/schedule`
Submit proposed meeting times for intelligent scheduling.

## Extension Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+U` | Release all held notifications |
| `Ctrl+Shift+C` | Open decision panel |
| `Ctrl+Shift+P` | Pause/resume monitoring |

## Privacy

- **All data stays local.** Zero cloud transmission.
- **No keystroke logging.** Only aggregate statistics (KPM, intervals).
- **No screen capture.** Only domain-level URL tracking.
- **Open source.** Core is public for audit.

See PRD §10 for full privacy architecture.

## Development Roadmap

| Phase | Days | Focus |
|-------|------|-------|
| 0 | 1 | Setup, scaffolding, infrastructure |
| 1 | 2-4 | Signal capture pipeline, badge indicator |
| 2 | 5-7 | ML inference, CLS computation, trend |
| 3 | 8-10 | Notification deflection, urgency bypass |
| 4 | 11-12 | Decision proxy, scheduling AI |
| 5 | 13-14 | Personalization, polish, settings |
| 6 | 15-17 | Testing, demo prep, submission |

## Project Structure

```
Cognitive/
├── cognitive-server/          # Python FastAPI backend
│   ├── api/                   # REST endpoints
│   ├── db/                    # SQLite store & schema
│   ├── ml/                    # ML inference & feature engineering
│   ├── interventions/         # Intervention engine, draft generator
│   ├── config/                # Configuration
│   └── tests/                 # Unit tests
├── cognitive-extension/       # Chrome/Firefox extension
│   ├── interceptors/          # Gmail, Slack, Calendar integrations
│   ├── lib/                   # Utilities, signal aggregator
│   └── icons/                 # Extension icons
├── cognitive-desktop/         # Desktop agent (Python)
│   ├── build/                 # PyInstaller build scripts
│   └── native_messaging.py    # Chrome native messaging bridge
├── plan.md                    # Implementation plan
├── README.md                  # This file
└── .gitignore
```

## Contributing

This is a hackathon project (Design4Future). During the build sprint, all changes should follow the plan.md roadmap. After the hackathon, see the PRD for the full product roadmap.

## License

Open-source — see LICENSE file.