"""Sawyer Dashboard package — FastAPI web interface for cluster status."""

from sawyer.dashboard.server import app, serve

__all__ = ["app", "serve"]
