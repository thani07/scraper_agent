"""
HTTP API — trigger the scraper and check run status.

Routes:
    GET  /health            — liveness check
    POST /trigger           — start a scrape run (background)
    GET  /status/{run_id}   — get status of a run
    GET  /runs              — list all runs (most recent first)

Start locally:
    uvicorn api:app --host 0.0.0.0 --port 8000 --reload

Test trigger:
    curl -X POST http://localhost:8000/trigger
    curl -X POST http://localhost:8000/trigger \
         -H "Content-Type: application/json" \
         -d '{"strategy": "videsktop", "filter": "Jones Day"}'
"""

import logging
logging.disable(logging.CRITICAL)

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# In-memory run registry (resets on container restart — fine for testing)
_runs: dict[str, dict] = {}

app = FastAPI(title="HR Salary Scraper API", version="1.0.0")


# ── Request / Response models ──────────────────────────────────────────────────

class TriggerRequest(BaseModel):
    strategy:    Optional[str] = None   # videsktop | all | workday | ... (default from .env)
    filter:      Optional[str] = None   # firm name substring, or "all"
    roles:       Optional[list[str]] = None  # override roles.json for this run


class TriggerResponse(BaseModel):
    run_id:     str
    status:     str
    started_at: str
    message:    str


# ── Background job ─────────────────────────────────────────────────────────────

async def _execute_run(run_id: str, strategy: str, site_filter: str, roles: list[str]):
    """Run the full scrape job and update _runs registry."""
    from app.models import SiteConfig
    from app.scraper import scrape_site, generate_search_terms
    from app.storage import CosmosStorage, LocalStorage
    from main import load_sites, run_batch

    _runs[run_id]["status"] = "running"

    try:
        sites = load_sites(strategy, site_filter)
        if not sites:
            _runs[run_id]["status"]  = "failed"
            _runs[run_id]["error"]   = f"No sites found for strategy='{strategy}' filter='{site_filter}'"
            return

        storage  = CosmosStorage() if os.getenv("STORAGE", "local") == "cosmos" else LocalStorage()
        await storage.connect()

        total_jobs = 0
        concurrency = int(os.getenv("CONCURRENCY", "5"))
        output_file = os.getenv("OUTPUT_FILE", "output.txt")

        # Write run header to output file
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"Run ID  : {run_id}\n")
            f.write(f"Started : {_runs[run_id]['started_at']}\n")
            f.write(f"Roles   : {', '.join(roles)}\n")
            f.write(f"Firms   : {len(sites)}\n")
            f.write("=" * 70 + "\n")

        for role in roles:
            search_terms = await generate_search_terms(role)
            results = await run_batch(sites, role, concurrency, output_file, search_terms)
            await storage.save_batch(results)
            jobs = sum(1 for r in results if r.status == "success")
            total_jobs += jobs
            _runs[run_id]["roles_done"].append({
                "role":       role,
                "jobs_found": jobs,
                "firms_run":  len(sites),
            })

        await storage.close()

        _runs[run_id]["status"]       = "completed"
        _runs[run_id]["total_jobs"]   = total_jobs
        _runs[run_id]["finished_at"]  = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        _runs[run_id]["status"] = "failed"
        _runs[run_id]["error"]  = str(e)[:500]
        _runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/trigger", response_model=TriggerResponse)
async def trigger(background_tasks: BackgroundTasks, req: TriggerRequest = TriggerRequest()):
    """
    Start a scrape run in the background.
    Returns immediately with a run_id — poll /status/{run_id} for progress.
    """
    from main import load_roles

    strategy    = req.strategy    or os.getenv("STRATEGY",    "videsktop")
    site_filter = req.filter      or os.getenv("SITE_FILTER", "all")
    roles       = req.roles       or load_roles()

    run_id     = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    _runs[run_id] = {
        "run_id":      run_id,
        "status":      "queued",
        "started_at":  started_at,
        "finished_at": None,
        "strategy":    strategy,
        "filter":      site_filter,
        "roles":       roles,
        "roles_done":  [],
        "total_jobs":  0,
        "error":       None,
    }

    background_tasks.add_task(_execute_run, run_id, strategy, site_filter, roles)

    return TriggerResponse(
        run_id     = run_id,
        status     = "queued",
        started_at = started_at,
        message    = f"Run started for {len(roles)} role(s) across {strategy} firms. Poll /status/{run_id} for progress.",
    )


@app.get("/status/{run_id}")
async def status(run_id: str):
    """Get the current status of a run."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return _runs[run_id]


@app.get("/runs")
async def list_runs():
    """List all runs, most recent first."""
    runs = sorted(_runs.values(), key=lambda r: r["started_at"], reverse=True)
    return {"total": len(runs), "runs": runs}
