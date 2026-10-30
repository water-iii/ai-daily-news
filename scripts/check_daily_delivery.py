"""Skip a scheduled/manual digest run if today's digest already succeeded.

GitHub Actions can miss or delay scheduled events. Multiple cron entries provide
retry opportunities; this check prevents later attempts from sending duplicates.
"""
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import json
import os
import sys

TZ = ZoneInfo("Asia/Shanghai")


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    current_run_id = os.environ["CURRENT_RUN_ID"]
    token = os.environ["GH_TOKEN"]
    today = datetime.now(TZ).date()

    query = urlencode({"branch": "main", "per_page": 100})
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
        f"daily.yml/runs?{query}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    skip = False
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for run in payload.get("workflow_runs", []):
            if str(run.get("id")) == current_run_id:
                continue
            if run.get("conclusion") != "success":
                continue
            if run.get("event") not in {"schedule", "workflow_dispatch"}:
                continue
            started = run.get("run_started_at") or run.get("created_at")
            if not started:
                continue
            run_date = datetime.fromisoformat(
                started.replace("Z", "+00:00")
            ).astimezone(TZ).date()
            if run_date == today:
                skip = True
                print(
                    f"Today's digest already succeeded in run {run['id']}; "
                    "skip this retry to avoid duplicate delivery."
                )
                break
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        # Fail open: if GitHub's read API is temporarily unavailable, attempt
        # delivery rather than silently miss the digest.
        print(
            f"WARNING: Could not verify prior deliveries ({type(exc).__name__}). "
            "Proceeding with this run."
        )

    output_path = os.environ["GITHUB_OUTPUT"]
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"skip={'true' if skip else 'false'}\\n")


if __name__ == "__main__":
    main()
