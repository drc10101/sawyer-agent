#!/usr/bin/env python3
"""Start Sawyer Agent test server with correct config."""
from sawyer_harness.web.server import create_app
from sawyer_harness.config import HarnessConfig
from sawyer_harness.paths import UserData

UserData.ensure_dirs()

config = HarnessConfig()
config.memory.path = str(UserData.memory_db)
app = create_app(config)

import uvicorn  # noqa: E402
uvicorn.run(app, host="127.0.0.1", port=8199)