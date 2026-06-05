"""
Azure Function App entry point.

Functions:
    scheduled_scrape  — Timer trigger, runs daily at 12:00 AM IST (18:30 UTC)
    http_trigger      — POST /api/trigger  — starts a run, blocks until done
    stop_trigger      — POST /api/stop     — abort a running crawl gracefully
    status_trigger    — GET  /api/status   — check current run progress

Local testing:
    func start

Trigger a run (PowerShell):
    Invoke-RestMethod -Method POST -Uri "http://localhost:7071/api/trigger" `
      -ContentType "application/json" `
      -Body '{"strategy":"videsktop","filter":"Jones Day","roles":["paralegal"]}'

Stop a running crawl:
    Invoke-RestMethod -Method POST -Uri "http://localhost:7071/api/stop"

Check status:
    Invoke-RestMethod -Uri "http://localhost:7071/api/status"
"""

import logging
import os
import json
import asyncio
from datetime import datetime, timezone

import azure.functions as func

app = func.FunctionApp()

# ── In-memory run state ────────────────────────────────────────────────────────
_run_state: dict = {
    "status":      "idle",      # idle | running | aborted | completed | failed
    "started_at":  None,
    "finished_at": None,
    "strategy":    None,
    "filter":      None,
    "roles":       [],
    "progress": {
        "current_role":       None,
        "role_index":         0,
        "total_roles":        0,
        "current_firm":       None,
        "firms_done":         0,
        "firms_total":        0,
        "jobs_found_so_far":  0,
    },
    "summary":     None,
    "error":       None,
}

# ── Stop flag — set by /api/stop, checked before each new firm starts ──────────
_stop_requested: bool = False


# ── Timer trigger — daily at 12:00 AM IST (18:30 UTC) ─────────────────────────

@app.timer_trigger(
    schedule="0 30 18 * * *",
    arg_name="timer",
    run_on_startup=False,
)
async def scheduled_scrape(timer: func.TimerRequest) -> None:
    """Scheduled daily run — reads roles from analyses container."""
    global _stop_requested
    _stop_requested = False
    if timer.past_due:
        logging.info("Timer is past due — running now.")
    logging.info("Scheduled scrape started.")
    await _execute_scrape(
        strategy     = os.getenv("STRATEGY",    "all"),
        site_filter  = os.getenv("SITE_FILTER", "all"),
        roles        = None,
        firms_config = os.getenv("FIRMS_CONFIG") or None,
    )
    logging.info("Scheduled scrape completed.")


# ── HTTP trigger — manual start ────────────────────────────────────────────────

@app.route(route="trigger", methods=["GET", "POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    Start a scrape run. Blocks until the run completes (or is aborted).

    Optional JSON body:
        { "strategy": "videsktop", "filter": "Jones Day", "roles": ["paralegal"] }
    """
    global _run_state, _stop_requested

    if _run_state["status"] == "running":
        return func.HttpResponse(
            json.dumps({
                "status":  "already_running",
                "message": "A scrape is already in progress. Call POST /api/stop to abort it.",
                "progress": _run_state["progress"],
            }),
            status_code=409,
            mimetype="application/json",
        )

    body = {}
    try:
        body = req.get_json()
    except Exception:
        pass

    strategy     = body.get("strategy") or os.getenv("STRATEGY",    "all")
    site_filter  = body.get("filter")   or os.getenv("SITE_FILTER", "all")
    roles        = body.get("roles")    or None
    firms_config = body.get("firms_config") or os.getenv("FIRMS_CONFIG") or None

    # Reset stop flag and run state
    _stop_requested = False
    _run_state.update({
        "status":      "running",
        "started_at":  datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "strategy":    strategy,
        "filter":      site_filter,
        "roles":       roles or [],
        "progress": {
            "current_role":      None,
            "role_index":        0,
            "total_roles":       0,
            "current_firm":      None,
            "firms_done":        0,
            "firms_total":       0,
            "jobs_found_so_far": 0,
        },
        "summary":     None,
        "error":       None,
    })

    logging.info(f"Scrape started: strategy={strategy} filter={site_filter} roles={roles} firms_config={firms_config}")
    await _execute_scrape(strategy, site_filter, roles, firms_config)

    status_code = 200 if _run_state["status"] in ("completed", "aborted") else 500
    return func.HttpResponse(
        json.dumps({"run": _run_state}),
        status_code=status_code,
        mimetype="application/json",
    )


# ── Stop trigger — abort a running crawl ──────────────────────────────────────

@app.route(route="stop", methods=["GET", "POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def stop_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    Gracefully abort a running crawl.

    Already-running firms finish their current role, then the crawl stops.
    All jobs collected so far are saved to Cosmos DB before exiting.
    """
    global _stop_requested, _run_state

    if _run_state["status"] != "running":
        return func.HttpResponse(
            json.dumps({
                "status":  "not_running",
                "message": f"No crawl is running. Current status: {_run_state['status']}",
            }),
            status_code=200,
            mimetype="application/json",
        )

    _stop_requested = True
    progress = _run_state["progress"]

    return func.HttpResponse(
        json.dumps({
            "status":  "stop_requested",
            "message": (
                "Stop signal sent. Active firms will finish their current role, "
                "then the crawl will abort. All jobs collected so far will be saved to DB."
            ),
            "firms_done":        progress["firms_done"],
            "firms_total":       progress["firms_total"],
            "firms_remaining":   progress["firms_total"] - progress["firms_done"],
            "jobs_found_so_far": progress["jobs_found_so_far"],
            "current_firm":      progress["current_firm"],
        }),
        status_code=200,
        mimetype="application/json",
    )


# ── Status endpoint ────────────────────────────────────────────────────────────

@app.route(route="status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def status_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """Return current run state including live progress."""
    return func.HttpResponse(
        json.dumps(_run_state, indent=2),
        status_code=200,
        mimetype="application/json",
    )


# ── Core scrape runner ─────────────────────────────────────────────────────────

async def _execute_scrape(strategy: str, site_filter: str, roles, firms_config: str | None = None):
    """Run the scraper and update _run_state on completion or abort."""
    global _run_state, _stop_requested

    def on_progress(
        current_role=None,
        role_index=None,
        total_roles=None,
        current_firm=None,
        firms_done=None,
        firms_total=None,
        jobs_found_so_far=None,
    ):
        p = _run_state["progress"]
        if current_role      is not None: p["current_role"]      = current_role
        if role_index        is not None: p["role_index"]        = role_index
        if total_roles       is not None: p["total_roles"]       = total_roles
        if current_firm      is not None: p["current_firm"]      = current_firm
        if firms_done        is not None: p["firms_done"]        = firms_done
        if firms_total       is not None: p["firms_total"]       = firms_total
        if jobs_found_so_far is not None: p["jobs_found_so_far"] = jobs_found_so_far

    def stop_check() -> bool:
        return _stop_requested

    try:
        from main import run_scraper
        summary = await run_scraper(
            strategy     = strategy,
            site_filter  = site_filter,
            roles        = roles,
            on_progress  = on_progress,
            firms_config = firms_config,
            stop_check   = stop_check,
        )

        final_status = "aborted" if summary.get("aborted") else "completed"
        _run_state.update({
            "status":      final_status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "summary":     summary,
        })
        logging.info(
            f"Scrape {final_status}: "
            f"{summary.get('total_jobs', 0)} jobs, "
            f"{summary.get('firms_done', 0)}/{summary.get('firms_total', 0)} firms."
        )

    except Exception as e:
        _run_state.update({
            "status":      "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error":       str(e)[:500],
        })
        logging.error(f"Scrape failed: {e}")
