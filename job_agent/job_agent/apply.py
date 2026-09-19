from __future__ import annotations


def approval_prompt(job, cv_path, letter_path):
    print("\nAPPLICATION READY")
    print(f"{job.title} @ {job.company}")
    print(f"Apply URL: {job.apply_url or job.url}")
    print(f"CV: {cv_path}")
    print(f"Cover letter: {letter_path}")
    ans = input("Open browser and proceed to final submission? [y/N] ").strip().lower()
    return ans == "y"


class ApplicationRunner:
    """Browser-assisted application layer. Deliberately never auto-submits without explicit approval."""

    def __init__(self, headless=False):
        self.headless = headless

    def run(self, job, cv_path, letter_path):
        if not approval_prompt(job, cv_path, letter_path):
            return {"status": "pending_user_approval"}
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError("Install .[playwright] and run 'playwright install chromium'") from e
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            page.goto(job.apply_url or job.url, wait_until="domcontentloaded")
            print("Browser opened. Fill/verify fields. The script stops before submission.")
            input("Press Enter only after you have reviewed the application fields...")
            browser.close()
        return {"status": "reviewed_not_submitted"}
