#!/usr/bin/env python3
"""
Simple LokOS state-wise downloader.
Clicks each state, clicks Export Excel, and saves the portal's original download
with a state/year/period filename. Does not parse or modify downloaded contents.

Dependencies:
  ./.venv/bin/python -m pip install playwright
  ./.venv/bin/python -m playwright install chromium

Example:
  export LOKOS_OUTPUT_DIR="/Users/krishnakirankamma/Library/CloudStorage/GoogleDrive-kammakrishnak@gmail.com/My Drive/LokOS_Bank_Linkage/lokos_data"
  ./.venv/bin/python lokos_direct_state_download.py --year 2026-27 --from-month August --to-month August --headed --limit 1
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://banklinkage.lokos.in/UI/Achievement/ProjectWiseAchievement_Newbank.aspx"
DEFAULT_OUT = (
    Path.home()
    / "Library/CloudStorage/GoogleDrive-kammakrishnak@gmail.com/My Drive/"
      "LokOS_Bank_Linkage/lokos_data"
)

YEAR_SEL = "#ContentPlaceHolder1_ddlyear"
FROM_SEL = "#ContentPlaceHolder1_ddlFromMonth"
TO_SEL = "#ContentPlaceHolder1_ddlToMonth"
BANK_SEL = "#ContentPlaceHolder1_ddl_bank_name"
EXPORT_SEL = "#ContentPlaceHolder1_ImageButton3"

def slug(value):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_").upper() or "UNKNOWN"

def unique_path(path):
    if not path.exists():
        return path
    for n in range(2, 10000):
        candidate = path.with_name(f"{path.stem}_rev{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create a unique filename for {path}")

def setup_logging(out):
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(logs / "direct_state_downloads.log", encoding="utf-8"),
        ],
        force=True,
    )

def select_label(page, selector, label):
    loc = page.locator(selector)
    loc.wait_for(state="visible", timeout=30000)
    try:
        loc.select_option(label=label)
    except Exception as exc:
        options = loc.locator("option").all_text_contents()
        raise RuntimeError(
            f"Could not select {label!r} in {selector}. Available options: {options}"
        ) from exc
    page.wait_for_timeout(500)

def save_page_diagnostics(page, out, stage, detail=""):
    """Save evidence of the actual page when the expected portal controls are missing."""
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    diag_dir = Path(out) / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{slug(stage)}_{stamp}"
    html_path = diag_dir / f"{stem}.html"
    screenshot_path = diag_dir / f"{stem}.png"

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        logging.exception("Could not save diagnostic HTML.")

    try:
        page.screenshot(path=str(screenshot_path), full_page=True, timeout=15000)
    except Exception:
        logging.exception("Could not save diagnostic screenshot.")

    try:
        body_preview = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=5000)).strip()[:1500]
    except Exception as exc:
        body_preview = f"<body text unavailable: {exc}>"

    logging.error(
        "PORTAL DIAGNOSTICS stage=%s url=%s title=%r detail=%s body_preview=%r html=%s screenshot=%s",
        stage, page.url, page.title(), detail, body_preview, html_path, screenshot_path,
    )


def open_report(page, year, from_month, to_month, bank, out):
    response = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    logging.info("Portal navigation response status=%s URL=%s", response.status if response else None, page.url)
    try:
        page.locator(YEAR_SEL).wait_for(state="visible", timeout=30000)
    except PlaywrightTimeoutError as exc:
        save_page_diagnostics(page, out, "year_selector_missing", str(exc))
        raise RuntimeError(
            f"Year selector {YEAR_SEL} did not appear. "
            "See output/diagnostics and the PORTAL DIAGNOSTICS log entry for the page response."
        ) from exc
    select_label(page, YEAR_SEL, year)
    select_label(page, FROM_SEL, from_month)
    select_label(page, TO_SEL, to_month)
    if bank:
        select_label(page, BANK_SEL, bank)
    page.wait_for_timeout(1000)
def get_states(page):
    links = page.locator("#StateWise a[id*='_lbDesc_']")
    if links.count() == 0:
        links = page.locator("a[id*='Grid14_15_lbDesc_']")
    states = []
    for i in range(links.count()):
        name = re.sub(r"\s+", " ", links.nth(i).inner_text()).strip()
        if name and name.casefold() not in {x.casefold() for x in states}:
            states.append(name)
    if not states:
        raise RuntimeError("No state links found on the report page.")
    return states

def click_state(page, state, timeout):
    link = page.locator("#StateWise a[id*='_lbDesc_']").filter(
        has_text=re.compile(r"^\s*" + re.escape(state) + r"\s*$", re.I)
    )
    if not link.count():
        link = page.locator("a[id*='Grid14_15_lbDesc_']").filter(
            has_text=re.compile(r"^\s*" + re.escape(state) + r"\s*$", re.I)
        )
    if not link.count():
        raise RuntimeError(f"Could not find state link for {state!r}")
    link.first.click()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(900)

def export_and_save(page, state, year, from_month, to_month, out, timeout):
    export = page.locator(EXPORT_SEL)
    if not export.count():
        export = page.get_by_role("img", name=re.compile("Export Excel", re.I))
    if not export.count():
        raise RuntimeError("Export Excel icon not found after selecting state.")

    with page.expect_download(timeout=timeout * 1000) as download_info:
        export.first.click()
    download = download_info.value
    suggested = download.suggested_filename or "LokOS_report.xls"

    # Preserve the portal's extension when recognizable; legacy exports are often HTML named .xls.
    ext = Path(suggested).suffix.lower()
    if ext not in {".xls", ".xlsx", ".html", ".htm", ".csv"}:
        ext = ".xls"

    period = f"FY{slug(year)}_{slug(from_month)}_to_{slug(to_month)}"
    stem = f"LokOS_{slug(state)}_{period}"
    period_dir = out / "state_reports" / f"FY{slug(year)}" / f"{slug(from_month)}_to_{slug(to_month)}"
    snapshot_dir = out / "snapshots" / f"FY{slug(year)}" / f"{slug(from_month)}_to_{slug(to_month)}"
    period_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    destination = unique_path(period_dir / f"{stem}{ext}")
    download.save_as(destination)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    snapshot = unique_path(snapshot_dir / f"{stem}_{stamp}{ext}")
    shutil.copy2(destination, snapshot)

    size = destination.stat().st_size
    if size == 0:
        destination.unlink(missing_ok=True)
        snapshot.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file is empty; removed the empty output.")

    return {
        "state": state,
        "status": "success",
        "portal_suggested_filename": suggested,
        "saved_file": str(destination),
        "snapshot_file": str(snapshot),
        "size_bytes": size,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "content_validation": "not_parsed; original portal download preserved",
    }

def main():
    ap = argparse.ArgumentParser(description="Directly download and rename LokOS state reports.")
    ap.add_argument("--year", required=True, help="Financial year label, e.g. 2026-27")
    ap.add_argument("--from-month", required=True, help="From month label, e.g. August")
    ap.add_argument("--to-month", required=True, help="To month label, e.g. August")
    ap.add_argument("--bank", default=None, help="Optional bank dropdown label")
    ap.add_argument("--headed", action="store_true", help="Show browser window")
    ap.add_argument("--limit", type=int, default=None, help="Process first N states only")
    ap.add_argument("--timeout", type=int, default=45, help="Download timeout in seconds")
    ap.add_argument("--out", default=os.environ.get("LOKOS_OUTPUT_DIR", str(DEFAULT_OUT)))
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    (out / "logs").mkdir(parents=True, exist_ok=True)
    (out / "metadata").mkdir(parents=True, exist_ok=True)
    setup_logging(out)

    run_id = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    manifest_path = out / "metadata" / (
        f"direct_state_manifest_FY{slug(args.year)}_{slug(args.from_month)}_to_"
        f"{slug(args.to_month)}_{run_id}.json"
    )
    manifest = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "url": URL,
        "year": args.year,
        "from_month": args.from_month,
        "to_month": args.to_month,
        "bank": args.bank,
        "method": "direct portal download; no content parsing",
        "results": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            open_report(page, args.year, args.from_month, args.to_month, args.bank, out)
            states = get_states(page)
            if args.limit is not None:
                states = states[:max(0, args.limit)]
            logging.info("Found %d state links.", len(states))

            for idx, state in enumerate(states, start=1):
                logging.info("[%d/%d] %s", idx, len(states), state)
                try:
                    # Reload and reapply filters before every state so clicks always start from overview.
                    open_report(page, args.year, args.from_month, args.to_month, args.bank, out)
                    click_state(page, state, args.timeout)
                    result = export_and_save(
                        page, state, args.year, args.from_month, args.to_month, out, args.timeout
                    )
                    logging.info("SUCCESS %s -> %s", state, result["saved_file"])
                except Exception as exc:
                    logging.exception("FAILED %s: %s", state, exc)
                    result = {
                        "state": state,
                        "status": "failed",
                        "error": str(exc),
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    }
                manifest["results"].append(result)
                manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
                manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        finally:
            context.close()
            browser.close()

    success = sum(r.get("status") == "success" for r in manifest["results"])
    failures = len(manifest["results"]) - success
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["success_count"] = success
    manifest["failure_count"] = failures
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    logging.info("Finished: %d success, %d failures. Manifest: %s", success, failures, manifest_path)
    return 0 if failures == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
