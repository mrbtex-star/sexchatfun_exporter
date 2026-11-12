#!/usr/bin/env python3
"""Chat stream exporter for SexChatFun.com.

Captures messages from a public chat room, downloads full-size images,
and exports to JSON and Markdown with live reporting.

Fully standalone: auto-launches Chrome/Edge/Brave with remote debugging enabled.
Can also connect to an existing browser via CDP (--cdp-url) or reuse one already
connected via Hermes (/browser connect).

Usage:
    python main.py --room 1
    python main.py --room 1 --limit 200 --interval 5 --formats json,md --output export --verbose
    python main.py --room 1 --dry-run
    python main.py --room 1 --no-auto-launch --cdp-url http://localhost:9222
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import socket
import string
import sys
import textwrap
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

SITE_URL = "https://www.sexchatfun.com"
ROOM_PATH = "/chat/1"
CHAT_SELECTOR = '[class*="list"]'


class ChatExporter:
    """Capture messages from SexChatFun chat and download images."""

    def __init__(
        self,
        room: str = ROOM_PATH,
        output_dir: str = "export",
        formats: list[str] | None = None,
        limit: int = 0,
        interval: float = 3.0,
        timeout: int = 60,
        verbose: bool = False,
        dry_run: bool = False,
        cdp_url: str = "",
        cdp_port: int = 0,
        chat_selector: str = CHAT_SELECTOR,
        load_older: bool = False,
        skip_images: bool = False,
        auto_launch: bool = True,
    ) -> None:
        self.room = room
        self.output_dir = Path(output_dir) / room.lstrip("/").replace("/", "_")
        self.date_dir = datetime.now().strftime("%Y-%m-%d")
        self.output_dir = self.output_dir / self.date_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.img_dir = self.output_dir / "images"
        self.img_dir.mkdir(exist_ok=True)
        self.formats = formats or ["json", "md"]
        self.limit = limit
        self.interval = interval
        self.timeout_ms = timeout * 1000
        self.dry_run = dry_run
        self.verbose = verbose
        self.cdp_url = cdp_url
        self.cdp_port = cdp_port
        self.chat_selector = chat_selector
        self.load_older = load_older
        self.skip_images = skip_images
        self.auto_launch = auto_launch

        # Browser state
        self._pw: Any = None
        self._browser: Any = None
        self._chrome_proc: Any = None  # subprocess for auto-launched Chrome
        self._chrome_tmpdir: str = ""  # temp dir for auto-launched Chrome profile

        self.messages: list[dict[str, Any]] = []
        self.seen_ids: set[str] = set()
        self.downloaded_hashes: set[str] = set()
        self.downloaded_message_ids: set[str] = set()
        self.captured_count = 0
        self.new_count = 0
        self.total_downloaded = 0
        self.total_size_bytes = 0
        self.total_seen_images = 0
        self.running = True

    # ---- logging ----

    def log(self, msg: str, force: bool = False) -> None:
        if self.verbose or force:
            print(f"[{datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)

    # ---- message extraction JS ----

    EXTRACT_JS = """
    (function() {
        var rows = document.querySelectorAll('[class*="row_xicnu"]');
        var results = [];
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var body = row.querySelector('[class*="body_xicnu"]');
            if (!body) continue;

            var username = '';
            var timeStr = '';
            var rank = '';
            var quotedName = '';
            var reactions = '';
            var imageUrls = [];
            var messageText = '';

            var nameEl = body.querySelector('[class*="name_xicnu"]');
            if (nameEl) username = nameEl.textContent.trim();

            var timeEl = body.querySelector('[class*="time_xicnu"]');
            if (timeEl) timeStr = timeEl.textContent.trim();

            var rankEl = body.querySelector('[class*="rank_xicnu"]');
            if (rankEl) rank = rankEl.textContent.trim();

            var quoteContainer = body.querySelector('[class*="quote_xicnu"]:not([class*="Thumbs"]):not([class*="Name"])');
            if (quoteContainer) {
                var quoteNameSpan = quoteContainer.querySelector('[class*="quoteName_xicnu"]');
                if (quoteNameSpan) quotedName = quoteNameSpan.textContent.trim();
            }

            var reactionsEl = body.querySelector('[class*="actions_xicnu"]');
            if (reactionsEl) reactions = reactionsEl.textContent.trim();

            var textEl = body.querySelector('[class*="text_xicnu"]');
            if (textEl) messageText = textEl.textContent.trim();

            var imgs = body.querySelectorAll('img');
            for (var j = 0; j < imgs.length; j++) {
                var src = (imgs[j].src || '').trim();
                if (src && src.includes('uploads/thumbs') && !imgs[j].className.includes('avatar')) {
                    var w = imgs[j].width || 0;
                    if (w <= 44 && imgs[j].className && imgs[j].className.includes('quote')) continue;
                    imageUrls.push(src);
                }
            }

            var idStr = username + '|||' + timeStr + '|||' + messageText + '|||' + quotedName;
            if (imageUrls.length > 0) idStr += '|||' + imageUrls[0];
            var h = 0;
            for (var k = 0; k < idStr.length; k++) {
                h = ((h << 5) - h) + idStr.charCodeAt(k);
                h = h & h;
            }

            results.push({
                index: i,
                username: username,
                time: timeStr,
                rank: rank,
                quotedName: quotedName,
                reactions: reactions,
                message: messageText,
                images: imageUrls,
                id: 'sf_' + h.toString(36)
            });
        }
        return results;
    })()
    """

    def extract_messages(self, page: Any) -> list[dict[str, Any]]:
        """Extract all messages from DOM via JS evaluation."""
        try:
            raw_msgs = page.evaluate(self.EXTRACT_JS)
            return raw_msgs or []
        except Exception:  # noqa: BLE001
            return []

    def capture_messages(self, page: Any) -> int:
        """Extract only new messages. Returns count added."""
        msgs = self.extract_messages(page)
        added = 0
        for m in msgs:
            if m.get("id") and m["id"] not in self.seen_ids:
                self.seen_ids.add(m["id"])
                self.messages.append(m)
                added += 1
                if m.get("images"):
                    for img_url in m["images"]:
                        self.total_seen_images += 1
        if added:
            print(f"    +{added} new message(s) (total: {len(self.messages)})", flush=True)
        return added

    # ---- image download ----

    def save_image(self, page: Any, url: str, username: str = "unknown") -> str:
        """Download an image via browser fetch to use session cookies."""
        if self.dry_run:
            self.total_downloaded += 1
            return ""

        # Convert thumb URL to full-size URL
        full_url = url.replace("/uploads/thumbs/", "/uploads/")
        ext = ".jpg"
        low = full_url.lower()
        for e in [".png", ".jpeg", ".webp", ".gif"]:
            if e in low:
                ext = e
                break

        safe_user = re.sub(r"[^\w\-]", "_", username)[:30]
        h = hashlib.md5(full_url.encode()).hexdigest()[:12]
        dest = self.img_dir / f"{safe_user}_{h}{ext}"

        if dest.exists() and h in self.downloaded_hashes:
            return str(dest)

        try:
            # Download via browser's fetch to use session cookies
            data = page.evaluate(
                "((url) => fetch(url).then(r => r.blob()).then(b => b.arrayBuffer()).then(a => Array.from(new Uint8Array(a))))",
                full_url,
            )
            if isinstance(data, dict) and "error" in data:
                raise Exception(data["error"])
            if isinstance(data, (int, float)):
                raise Exception("Download returned non-bytes")
            raw_bytes = bytes(data)
            dest.write_bytes(raw_bytes)
            self.downloaded_hashes.add(h)
            self.total_downloaded += 1
            self.total_size_bytes += len(data)

            size_str = self._format_size(len(data))
            total_size_str = self._format_size(self.total_size_bytes)
            print(
                f"  [SAVED] {username}/{dest.name}  ({size_str})  "
                f"[{self.total_downloaded}/{self.total_seen_images}]  "
                f"Total: {total_size_str}",
                flush=True,
            )
            return str(dest)
        except Exception:  # noqa: BLE001
            self.log(f"  Failed to download: {full_url}", force=True)
            return ""

    def download_new_images(self, page: Any) -> int:
        """Download images for messages that haven't been processed yet."""
        if self.skip_images:
            return 0
        count = 0
        for m in self.messages:
            mid = m["id"]
            if not mid or mid in self.downloaded_message_ids:
                continue
            for img_url in m.get("images", []):
                if img_url:
                    uname = m.get("username", "unknown")
                    self.save_image(page, img_url, uname)
            if m.get("images"):
                self.downloaded_message_ids.add(mid)
                count += 1
        return count

    @staticmethod
    def _format_size(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / (1024 * 1024):.1f} MB"

    # ---- exports ----

    def export_json(self) -> None:
        if "json" not in self.formats:
            return
        path = self.output_dir / "messages.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.messages, f, indent=2, ensure_ascii=False)

    def export_markdown(self) -> None:
        if "md" not in self.formats:
            return
        path = self.output_dir / "messages.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write("# SexChatFun Chat Export\n\n")
            f.write(f"**Room:** `{self.room}`\n")
            f.write(f"**Exported:** {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write(f"**Messages:** {len(self.messages)}\n")
            f.write(f"**Images downloaded:** {self.total_downloaded}\n\n")
            f.write("---\n\n")
            for m in self.messages:
                f.write(f"### @{m.get('username', '?')}\n\n")
                time_val = m.get("time", "")
                f.write(f"**{time_val}**")
                if m.get("rank"):
                    f.write(f" · {m['rank']}")
                f.write("\n\n")
                if m.get("quotedName"):
                    f.write(f"> *Replying to {m['quotedName']}*\n\n")
                if m.get("message"):
                    f.write(f"{m['message']}\n\n")
                if m.get("reactions"):
                    f.write(f"**Reactions:** {m['reactions']}\n\n")
                if m.get("images"):
                    f.write(f"**Images:** {len(m['images'])} photo(s) in `images/` folder\n\n")
                f.write("---\n\n")

    def export_users(self) -> None:
        users: dict[str, dict[str, Any]] = {}
        for m in self.messages:
            u = m.get("username", "")
            if not u:
                continue
            if u not in users:
                users[u] = {
                    "username": u,
                    "count": 0,
                    "first": m.get("time", ""),
                    "rank": m.get("rank", ""),
                    "last": m.get("time", ""),
                    "has_images": False,
                }
            users[u]["count"] += 1
            users[u]["last"] = m.get("time", "")
            if m.get("images"):
                users[u]["has_images"] = True
        path = self.output_dir / "users.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(users.values()), f, indent=2, ensure_ascii=False)

    # ---- browser setup ----

    def _find_chrome_exe(self) -> str:
        """Find a Chrome/Edge/Brave executable on the system."""
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft Edge\Application\msedge.exe",
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return ""

    def _launch_chrome(self) -> tuple:
        """Launch Chrome with CDP in background. Returns (process, cdp_url) or (None, '')."""
        chrome_exe = self._find_chrome_exe()
        if not chrome_exe:
            return None, ""

        port = self.cdp_port if self.cdp_port else 0

        import subprocess
        import random

        try:
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            # Pick a random port if none specified
            if port == 0:
                port = random.randint(9200, 9999)

            self.log(f"Launching Chrome on port {port} ...")

            # Create a temp profile directory for CDP
            self._chrome_tmpdir = os.path.join(
                os.environ.get("TMPDIR", os.environ.get("TEMP", "/tmp")),
                f"sexchatfun_chrome_{port}_{int(time.time())}"
            )
            os.makedirs(self._chrome_tmpdir, exist_ok=True)

            proc = subprocess.Popen(
                [chrome_exe, f"--remote-debugging-port={port}", "--no-first-run",
                 "--no-default-browser-check", f"--user-data-dir={self._chrome_tmpdir}"],
                creationflags=creation_flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Wait for CDP port to accept connections
            for attempt in range(30):
                try:
                    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                    sock.close()
                    self.log(f"Chrome launched on port {port} (PID {proc.pid})")
                    return proc, f"http://127.0.0.1:{port}"
                except (OSError, ConnectionRefusedError):
                    time.sleep(1)

            self.log("Chrome launched but CDP port not ready", force=True)
            proc.terminate()
            return None, ""

        except Exception as exc:  # noqa: BLE001
            self.log(f"Failed to launch Chrome: {exc}", force=True)
            return None, ""

    def _get_cdp_url(self) -> str:
        """Find a working CDP URL. Checks explicit URL, then common ports, then launches Chrome."""
        if self.cdp_url:
            return self.cdp_url
        for port in [9222, 9223, 9221, 49152]:
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                sock.close()
                return f"http://127.0.0.1:{port}"
            except (OSError, ConnectionRefusedError):
                continue
        # No existing browser — launch one
        if self.auto_launch:
            proc, url = self._launch_chrome()
            if proc:
                self._chrome_proc = proc  # Save to kill later
        return url

    def _connect_browser(self) -> Any:
        """Connect to a running Chrome via CDP (or launch one)."""
        cdp_url = self._get_cdp_url()
        if not cdp_url:
            return None
        self.log(f"Connecting to CDP at {cdp_url} ...")
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp(cdp_url)
            self.log("Connected!")
            self._pw = pw
            self._browser = browser
            return browser
        except Exception as exc:  # noqa: BLE001
            self.log(f"CDP connection failed: {exc}", force=True)
            return None

    def _cleanup(self) -> None:
        """Kill the auto-launched Chrome and close Playwright."""
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        try:
            if self._chrome_proc:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
                self.log("Closed auto-launched Chrome")
        except Exception:
            pass
        # Remove temp profile dir
        if self._chrome_tmpdir and os.path.exists(self._chrome_tmpdir):
            try:
                import shutil
                shutil.rmtree(self._chrome_tmpdir, ignore_errors=True)
            except Exception:
                pass

    # ---- main loop ----

    def run(self) -> None:
        self._print_header()

        if self.dry_run:
            print("\n  [DRY RUN — no browser, no downloads]\n")
        else:
            if not HAS_PLAYWRIGHT:
                print(
                    "ERROR: playwright is required.\n"
                    "Install with: pip install playwright",
                    file=sys.stderr,
                )
                sys.exit(1)

            browser = self._connect_browser()
            if not browser:
                if self.auto_launch:
                    print(
                        "\nERROR: Could not find or launch a Chrome/Edge/Brave browser.\n"
                        "Make sure Chrome/Edge is installed.\n",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "\nERROR: No Chrome/Chromium/Brave browser found on localhost.\n"
                        "Either:\n"
                        "  1. Start Chrome with --remote-debugging-port=9222, or\n"
                        "  2. Connect the browser via '/browser connect' in Hermes,\n"
                        "  3. Specify --cdp-url 'http://host:port', or\n"
                        "  4. Remove --no-auto-launch (default: auto-launch is ON).\n",
                        file=sys.stderr,
                    )
                sys.exit(1)

            try:
                context = browser.contexts[0]
                page = None
                for p in context.pages:
                    if "chat/" in p.url:
                        page = p
                        break
                if not page:
                    page = context.new_page()
                    self._navigate(page)

                self._initial_capture(page)

                if self.load_older:
                    print("  Loading older messages via 'Load More' button...\n")
                    self._load_older_messages(page)

                print(f"\n  Polling every {self.interval:.1f}s (Ctrl+C to stop).\n")

                while self.running:
                    time.sleep(self.interval)
                    self.capture_messages(page)
                    self.download_new_images(page)
                    self.export_json()
                    self.new_count += 1

                    if self.limit and self.new_count >= self.limit:
                        print(f"\n  Limit reached ({self.limit}).\n")
                        break

            except KeyboardInterrupt:
                print("\n  Interrupted.\n")
            except Exception as exc:  # noqa: BLE001
                self.log(f"Error: {exc}", force=True)
            finally:
                self._cleanup()

        self._final_export_all()

    def _load_older_messages(self, page: Any, max_loads: int = 5) -> None:
        """Click the 'Load older messages' button to load history."""
        for attempt in range(max_loads):
            load_btn = page.query_selector('[class*="loadMore"]')
            if not load_btn:
                break
            try:
                display = load_btn.evaluate("el => el.style.display")
                if display == "none":
                    break
            except Exception:
                break
            load_btn.click()
            time.sleep(2)
            total_rows = page.evaluate(
                "document.querySelectorAll('[class*=row_xicnu]').length"
            )
            print(f"  Loaded: {total_rows} total messages visible")

    def _accept_age_verification(self, page: Any) -> bool:
        """Click the 'Yes, I am 18 or older' age-verification button.
        Returns True if the button was found and clicked."""
        try:
            # Wait up to 5 seconds for the age overlay to appear
            btn = page.locator("text=Yes, I am 18 or older").first
            btn.wait_for(state="visible", timeout=5000)
            btn.click()
            self.log("Age verification accepted")
            return True
        except Exception as e:
            self.log(f"Age verification button not found: {e}", force=True)
            return False

    def _click_guest(self, page: Any) -> bool:
        """Click the 'Continue as guest' button to open the registration modal.
        Returns True if the modal opened."""
        try:
            btn = page.locator("text=Continue as guest").first
            btn.wait_for(state="visible", timeout=3000)
            btn.click()
            time.sleep(2)  # Wait for modal to appear
            modal = page.query_selector("._modal_zwuga_13")
            if modal:
                self.log("Guest modal opened")
                return True
            else:
                self.log("Modal not found after guest click", force=True)
        except Exception as e:
            self.log(f"Guest click error: {e}", force=True)
        return False

    def _fill_guest_form(self, page: Any) -> bool:
        """Fill and submit the guest registration form.

        Returns True if successfully submitted and chat content visible.
        Returns False if Turnstile captcha blocked it.
        """
        from playwright.sync_api import TimeoutError as PwTimeout

        # Wait for the modal and inputs to be visible
        try:
            username_input = page.locator('._modal_zwuga_13 input[type="text"]').first
            username_input.wait_for(state="visible", timeout=5000)
        except PwTimeout:
            self.log("Username input not visible in guest modal", force=True)
            return False

        from guest_names import generate_username
        username = generate_username()

        # Fill username
        try:
            username_input.fill(username)
            self.log(f"Filled username: {username}")
        except Exception as e:
            self.log(f"Username fill error: {e}", force=True)
            return False

        # Select gender (Female = 8)
        try:
            page.locator('._modal_zwuga_13 select').first.select_option(value="8")
            self.log("Selected gender")
        except Exception as e:
            self.log(f"Gender select error: {e}", force=True)

        # Fill date of birth
        try:
            page.locator('._modal_zwuga_13 input[type="date"]').first.fill("1990-06-15")
            self.log("Filled date of birth")
        except Exception as e:
            self.log(f"DOB fill error: {e}", force=True)

        # Try submitting
        try:
            submit_btn = page.locator('._modal_zwuga_13 button[type="submit"]').first
            submit_btn.click(force=True)
            time.sleep(5)
        except Exception as e:
            self.log(f"Submit error: {e}", force=True)

        # Check result
        errors = page.evaluate("""
            () => Array.from(document.querySelectorAll('[class*=error]'))
                   .map(el => el.textContent.trim())
        """)

        has_chat = page.evaluate("document.body.innerText.includes('row_xicnu')")

        if errors and any("captcha" in e.lower() for e in errors):
            self.log("Turnstile captcha required — user must solve manually", force=True)
            return False
        elif errors:
            self.log(f"Form error: {errors}", force=True)
            return False
        elif has_chat:
            self.log("Guest registration successful!")
            return True
        else:
            # Check if still on /login
            if "login" in page.url.lower():
                self.log("Still on login page — Turnstile blocked", force=True)
                return False
            time.sleep(1)
            return self._fill_guest_form(page)

    def _wait_for_guest_login(self, page: Any, timeout: int = 60) -> bool:
        """Wait for the user to solve the Turnstile captcha manually.

        The browser window stays open. Returns True when chat loads.
        Handles page navigation that happens after captcha solve.
        """
        from playwright.sync_api import TimeoutError as PwTimeout

        print(f"\n  {'='*50}")
        print(f"  TURNSTILE CAPTCHA REQUIRED")
        print(f"  Please solve the captcha in the browser window.")
        print(f"  After solving, the chat will load automatically.")
        print(f"  Timeout: {timeout}s (Ctrl+C to exit)")
        print(f"  {'='*50}\n")

        start = time.time()
        while time.time() - start < timeout:
            time.sleep(2)

            try:
                current_url = page.url.lower()
            except Exception:
                # Page was navigated away, get a fresh page reference
                self.log("Page navigated — waiting for new page...")
                try:
                    page = page.main_frame.page
                    time.sleep(2)
                except Exception:
                    continue

            # If still on /login page with modal, keep waiting
            if "login" in current_url:
                try:
                    has_modal = page.evaluate("!!document.querySelector('._modal_zwuga_13')")
                    if has_modal:
                        remaining = max(0, int(timeout - (time.time() - start)))
                        print(f"  Waiting for captcha... ({remaining}s remaining)")
                        continue
                except Exception:
                    # Page navigated while checking — fall through to chat load check
                    pass

            # Captcha solved — page left /login. Go to /chat/1 and wait for chat.
            if "login" not in current_url:
                self.log("Captcha solved — loading chat...")
                try:
                    page.goto(SITE_URL + self.room, wait_until="networkidle", timeout=self.timeout_ms)
                    time.sleep(3)
                    has_chat = page.evaluate("document.body.innerText.includes('row_xicnu')")
                    rows = page.query_selector_all(self.chat_selector)
                    if has_chat or rows:
                        self.log(f"Chat loaded after {int(time.time() - start)}s!")
                        return True
                except PwTimeout:
                    self.log("Chat load timed out, trying again...", force=True)
                    time.sleep(5)
                    try:
                        has_chat = page.evaluate("document.body.innerText.includes('row_xicnu')")
                        rows = page.query_selector_all(self.chat_selector)
                        if has_chat or rows:
                            self.log(f"Chat loaded after retry!")
                            return True
                    except Exception:
                        pass

        self.log("Timed out waiting for captcha", force=True)
        return False

    def _print_header(self) -> None:
        print("=" * 60)
        print(f"  Chat Stream Exporter — {SITE_URL}")
        print("=" * 60)
        print(f"  Room:     {self.room}")
        print(f"  Output:   {self.output_dir}")
        print(f"  Limit:    {self.limit or 'unlimited'}")
        print(f"  Interval: {self.interval}s")
        print(f"  Load older: {'yes' if self.load_older else 'no'}")
        print(f"  Formats:  {', '.join(self.formats)}")
        if self.auto_launch:
            print(f"  Mode:     auto-launch (solve captcha manually)")
        elif self.cdp_url:
            print(f"  Mode:     connect to {self.cdp_url}")
        print("=" * 60)

    def _navigate(self, page: Any) -> None:
        root = SITE_URL + "/"
        print(f"\n  Establishing session at {root} ...")
        page.goto(root, wait_until="domcontentloaded", timeout=self.timeout_ms)

        # Accept age verification
        if self._accept_age_verification(page):
            time.sleep(2)  # Wait for redirect to /login
            self.log("Navigating to /login page...")

            # Click guest to open registration modal
            if self._click_guest(page):
                self.log("Guest modal opened, filling form...")
                if not self._fill_guest_form(page):
                    # Wait for user to solve Turnstile captcha
                    self._wait_for_guest_login(page)
            else:
                self.log("Guest button not found, trying /chat/1 directly...", force=True)
        else:
            self.log("Age verification already accepted or not needed", force=True)

        url = SITE_URL + self.room
        print(f"  Navigating to {url} ...")
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        time.sleep(2)

        print("  Waiting for chat container ...")
        try:
            page.wait_for_selector(self.chat_selector, timeout=15000)
        except Exception:
            self.log("Chat container not found, proceeding anyway...", force=True)
        print("  Chat ready.")

    def _initial_capture(self, page: Any) -> None:
        initial = self.capture_messages(page)
        if initial == 0:
            time.sleep(5)
            initial = self.capture_messages(page)
        print(f"\n  Initial snapshot: {initial} messages\n")
        self.captured_count += initial
        self.download_new_images(page)
        self.export_json()

    def _final_export_all(self) -> None:
        print("\n" + "=" * 60)
        print("  Finalising exports ...")
        print("=" * 60)
        if self.messages:
            self.export_json()
            self.export_markdown()
            self.export_users()
            print(f"\n  Output: {self.output_dir}/")
        print("\n" + "=" * 60)
        print(f"  DONE — {self.captured_count} messages captured")
        print(f"  {self.total_downloaded} images saved ({self._format_size(self.total_size_bytes)})")
        print("=" * 60)


def main() -> None:
    ap = argparse.ArgumentParser(description="SexChatFun chat stream exporter")
    ap.add_argument("--room", default="1", help="Room number or path")
    ap.add_argument("--output", default="export", help="Output directory")
    ap.add_argument("--formats", default="json,md", help="Export formats (comma-separated)")
    ap.add_argument("--limit", type=int, default=0, help="Max polling cycles (0=unlimited)")
    ap.add_argument("--interval", type=float, default=3.0, help="Polling interval in seconds")
    ap.add_argument("--timeout", type=int, default=60, help="Page load timeout in seconds")
    ap.add_argument(
        "--cdp-url", default="", help="CDP URL (default: auto-detect localhost:9222, then auto-launch)"
    )
    ap.add_argument(
        "--cdp-port", type=int, default=0,
        help="CDP port for auto-launched browser (default: random free port)"
    )
    ap.add_argument(
        "--no-auto-launch", action="store_true",
        help="Do not auto-launch Chrome; fail if no browser is running on localhost"
    )
    ap.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    ap.add_argument("--dry-run", action="store_true", help="Print plan only, no browser/download")
    ap.add_argument("--chat-selector", default=CHAT_SELECTOR, help="CSS selector for chat container")
    ap.add_argument(
        "--load-older", action="store_true", help="Load older messages via 'Load More' button"
    )
    ap.add_argument(
        "--skip-images", action="store_true", help="Skip image downloads (export text only)"
    )
    args = ap.parse_args()

    raw_room = args.room or "1"
    if raw_room.startswith("/chat"):
        room = raw_room
    elif "/" in raw_room:
        room = f"/{raw_room}"
    else:
        room = f"/chat/{raw_room}"

    exporter = ChatExporter(
        room=room,
        output_dir=args.output,
        formats=[x.strip() for x in args.formats.split(",")],
        limit=args.limit,
        interval=args.interval,
        timeout=args.timeout,
        verbose=args.verbose,
        dry_run=args.dry_run,
        cdp_url=args.cdp_url,
        cdp_port=args.cdp_port,
        chat_selector=args.chat_selector,
        load_older=args.load_older,
        skip_images=args.skip_images,
        auto_launch=not args.no_auto_launch,
    )
    exporter.run()


if __name__ == "__main__":
    main()
