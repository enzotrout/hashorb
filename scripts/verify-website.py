"""Verify that the published HashOrb homepage reflects the checked-out site source."""

from __future__ import annotations

import html
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://hashorb.com/"
SOURCE = Path("site/index.html")
RELEASE_LINK = re.compile(
    r'<a\b[^>]*href="([^"]+)"[^>]*>\s*(Download v[^<]+ Alpha)\s*</a>',
    re.IGNORECASE,
)
EYEBROW = re.compile(r'<div class="eyebrow">(.+?)</div>', re.DOTALL)


def release_content(page: str) -> tuple[str, str, str]:
    badge = EYEBROW.search(page)
    link = RELEASE_LINK.search(page)
    if badge is None or link is None:
        raise ValueError("Release badge or alpha download link is missing")
    label = html.unescape(re.sub(r"<[^>]*>", "", badge.group(1))).strip()
    label = " ".join(label.split())
    return label, html.unescape(link.group(1)), html.unescape(link.group(2)).strip()


def main() -> int:
    expected = release_content(SOURCE.read_text(encoding="utf-8"))
    attempts = max(1, min(12, int(os.environ.get("VERIFY_ATTEMPTS", "1"))))
    headers = {"User-Agent": "HashOrb-deployment-verifier/1.0", "Cache-Control": "no-cache"}
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(URL + "?verify=" + str(int(time.time())), headers=headers)
            with urllib.request.urlopen(request, timeout=15) as response:
                status = response.status
                final_url = response.url
                body = response.read(2_000_000).decode("utf-8")
            if status != 200 or not final_url.startswith("https://hashorb.com/"):
                raise ValueError(f"Unexpected HTTP response: status={status}, url={final_url}")
            found = release_content(body)
            if found != expected:
                raise ValueError(f"Website release details differ: expected={expected!r}, found={found!r}")
            print(f"PASS: {URL} serves the expected release badge and download link")
            return 0
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"Attempt {attempt}/{attempts}: {exc}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(20)
    print("FAIL: Published website does not match current site/index.html release content", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
