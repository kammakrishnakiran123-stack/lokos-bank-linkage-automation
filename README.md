# LokOS Bank Linkage Automation — deployment starter

This repository starter runs the existing direct-download script in GitHub Actions. The workflow is **manual-only** on purpose: validate state discovery and downloaded reports before enabling a recurring all-India run.

## First cloud test

1. Create a **private** GitHub repository named `lokos-bank-linkage-automation`.
2. Upload the files in this package, including `.github/workflows/lokos-manual-test.yml`.
3. Open **Actions → LokOS State Report Download (manual validation) → Run workflow**.
4. Keep `limit` at `1` for the first run. Download the artifact and inspect the `.xls` file.
5. Investigate why the portal reports only one state link before using a larger limit or scheduling recurring runs.

## Important current limitation

The workflow stores its output as a GitHub Actions artifact for validation; it does **not yet upload reports to Google Drive**. To enable Drive delivery, create a Google Cloud service account, enable Google Drive API, share the destination Drive folder with the service account email, and add the service-account JSON and Drive folder ID as GitHub Actions secrets. Then implement/test the Drive upload step before enabling a monthly schedule.

Do not commit credentials, cookies, passwords, tokens, or downloaded reports to this repository.
