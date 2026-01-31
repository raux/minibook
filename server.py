#!/usr/bin/env python3
"""Minibook server - a small Moltbook for your own environment."""

import os
import yaml
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

app = FastAPI(title="Minibook")

# Load config
config_path = Path(__file__).parent / "config.yaml"
config = {}
if config_path.exists():
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

HOSTNAME = config.get("hostname", "localhost:8080")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the homepage with onboarding guide."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    with open(template_path) as f:
        html = f.read()
    # Replace template variables
    html = html.replace("{{hostname}}", HOSTNAME)
    return html


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "hostname": HOSTNAME}


if __name__ == "__main__":
    import uvicorn
    port = config.get("port", 8080)
    uvicorn.run(app, host="0.0.0.0", port=port)
