"""
Azure Function App entry point.

Functions:
    scheduled_scrape  — Timer trigger, runs daily at 12:00 AM IST (18:30 UTC)
    http_trigger      — HTTP trigger, manual test via POST /api/trigger

Local testing:
    func start

HTTP test (PowerShell):
    # Full run (all roles, all firms from STRATEGY env):
    Invoke-RestMethod -Method POST -Uri "http://localhost:7071/api/trigger"

    # Targeted test (one firm, one role):
    Invoke-RestMethod -Method POST -Uri "http://localhost:7071/api/trigger" `
      -ContentType "application/json" `
      -Body '{"strategy":"videsktop","filter":"Jones Day","roles":["paralegal"]}'
"""

import logging
import os
import json

import azure.functions as func

app = func.FunctionApp()


# ── Timer trigger — runs daily at 12:00 AM IST (18:30 UTC) ───────────────────
# Azure Functions CRON format: {second} {minute} {hour} {day} {month} {weekday}
# 18:30 UTC = 00:00 IST (midnight)

@app.timer_trigger(
    schedule="0 30 18 * * *",
    arg_name="timer",
    run_on_startup=False,
)
async def scheduled_scrape(timer: func.TimerRequest) -> None:
    """Scheduled daily run — reads roles from config/roles.json."""
    if timer.past_due:
        logging.info("Timer trigger is past due — running now.")

    logging.info("Scheduled scrape started.")
    from main import run_scraper
    await run_scraper()
    logging.info("Scheduled scrape completed.")


# ── HTTP trigger — manual test ────────────────────────────────────────────────

@app.route(route="trigger", methods=["GET", "POST"])
async def http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    Manual trigger for testing.

    Optional JSON body:
        {
          "strategy": "videsktop",        // default: STRATEGY env var
          "filter":   "Jones Day",        // default: all firms
          "roles":    ["paralegal"]        // default: config/roles.json
        }

    For a quick test, pass a filter to limit to 1 firm so it finishes in ~2 min.
    """
    body = {}
    try:
        body = req.get_json()
    except Exception:
        pass

    strategy    = body.get("strategy") or os.getenv("STRATEGY",    "videsktop")
    site_filter = body.get("filter")   or os.getenv("SITE_FILTER", "all")
    roles       = body.get("roles")    or None  # None = read from roles.json

    logging.info(f"HTTP trigger: strategy={strategy} filter={site_filter} roles={roles}")

    try:
        from main import run_scraper
        summary = await run_scraper(
            strategy    = strategy,
            site_filter = site_filter,
            roles       = roles,
        )
        return func.HttpResponse(
            json.dumps(summary, indent=2),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as e:
        logging.error(f"Scrape failed: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )
