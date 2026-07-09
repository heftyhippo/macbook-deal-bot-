"""
sources.py - fetches listings from Mercari JP, Yahoo! Auctions JP and Rakuma
(the marketplaces that supply the bulk of Buyee's and ZenMarket's second-hand
stock), plus eBay US live listings and an eBay-UK sold-listings price helper.
"""
from __future__ import annotations

import asyncio
import re
import statistics
import time
import unicodedata
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from pricing import Listing, find_cycle_count

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class FetchError(Exception):
    """Raised when every fetch attempt failed. Keeps the last HTTP status and
    response body so --debug can save whatever the site sent back."""
    def __init__(self, msg: str, status=None, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.body = body or ""


_HTTP_BACKEND: Optional[str] = None
_SESSIONS: dict = {}      # impersonation profile -> curl_cffi Session
_WARMED: set = set()      # (profile, warmup_url) pairs already visited

# real-browser TLS/HTTP2 fingerprints to rotate through when a site says 403
IMPERSONATE_PROFILES = ["chrome", "safari", "chrome_android", "safari_ios"]


def http_backend() -> str:
    """'curl_cffi' (stealthy, recommended) or 'requests' (easily blocked)."""
    global _HTTP_BACKEND
    if _HTTP_BACKEND is None:
        try:
            import curl_cffi  # noqa: F401
            _HTTP_BACKEND = "curl_cffi"
        except ImportError:
            _HTTP_BACKEND = "requests"
    return _HTTP_BACKEND


# Accept-Language per target market - sending Japanese-preferred headers to
# eBay US (and vice versa) is both a bot tell and a recipe for odd variants.
_LANG_HEADERS = {
    "ja": "ja,en-GB;q=0.9,en;q=0.8",
    "en-US": "en-US,en;q=0.9",
    "en-GB": "en-GB,en;q=0.9",
}


def _http_get(url: str, referer: str = "", warmup: str = "", lang: str = "ja") -> str:
    """GET a page looking like a real browser.

    With curl_cffi installed: rotates through several real-browser
    fingerprints, keeps cookies in a session, and (optionally) visits a
    'warmup' page first like a human would - this is what gets past
    Yahoo's bot-blocking. NOTE: we deliberately do NOT set our own
    User-Agent here; each impersonation profile supplies a matching one,
    and a mismatched UA is an instant bot giveaway.
    """
    base_headers = {
        "Accept-Language": _LANG_HEADERS.get(lang, lang),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if referer:
        base_headers["Referer"] = referer

    if http_backend() == "curl_cffi":
        from curl_cffi import requests as creq
        last_status, last_body, last_err = None, "", None
        for prof in IMPERSONATE_PROFILES:
            try:
                sess = _SESSIONS.get(prof)
                if sess is None:
                    sess = creq.Session(impersonate=prof)
                    _SESSIONS[prof] = sess
                if warmup and (prof, warmup) not in _WARMED:
                    try:
                        sess.get(warmup, headers=base_headers, timeout=20)
                    except Exception:
                        pass  # warmup is best-effort
                    _WARMED.add((prof, warmup))
                    time.sleep(0.8)
                r = sess.get(url, headers=base_headers, timeout=25)
                if r.status_code == 200 and r.text:
                    return r.text
                last_status, last_body = r.status_code, r.text or ""
                # blocked with this fingerprint - try the next one
                time.sleep(1.2)
            except Exception as e:
                last_err = e
                time.sleep(1.2)
        msg = (f"HTTP {last_status}" if last_status else f"{last_err}")
        raise FetchError(
            f"{msg} after trying {len(IMPERSONATE_PROFILES)} browser fingerprints",
            status=last_status, body=last_body)

    # ---- plain-requests fallback (curl_cffi not installed) ----
    import requests
    headers = dict(base_headers)
    headers["User-Agent"] = UA
    r = requests.get(url, headers=headers, timeout=25)
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code} (plain requests - install "
                         f"curl_cffi for stealth mode)",
                         status=r.status_code, body=r.text or "")
    return r.text


# ============================================================================
# MERCARI JP  (official-app API via the `mercapi` library)
# ============================================================================

def _run_async(factory):
    """Run an async coroutine factory to completion in its OWN thread + event
    loop. Plain asyncio.run() breaks once Playwright's sync browser (used for
    Buyee) has claimed the main thread's async machinery."""
    import threading
    box: dict = {}

    def runner():
        try:
            box["v"] = asyncio.run(factory())
        except BaseException as e:   # propagate to caller
            box["e"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box.get("v")


async def _mercari_search_async(queries, conditions, pages, min_price) -> list[Listing]:
    from mercapi import Mercapi
    from mercapi.requests.search import SearchRequestData

    m = Mercapi()
    out: dict[str, Listing] = {}
    for q in queries:
        try:
            res = await m.search(
                q,
                item_conditions=list(conditions),
                status=[SearchRequestData.Status.STATUS_ON_SALE],
                sort_by=SearchRequestData.SortBy.SORT_CREATED_TIME,
                price_min=int(min_price),
            )
        except Exception as e:
            print(f"  [mercari] search '{q}' failed: {e}")
            continue
        page = 0
        while True:
            for it in res.items:
                if it.real_price is None:
                    continue
                # condition id -> (label, tier): 1-2 are unused (resale-safe),
                # 3 = "no visible scratches or dirt" (practically-new tier),
                # 4 = "some scratches/dirt" (best-value section only)
                cond, grade = {
                    1: ("新品、未使用", "resale"),
                    2: ("未使用に近い", "resale"),
                    3: ("目立った傷や汚れなし", "personal"),
                    4: ("やや傷や汚れあり", "good"),
                }.get(it.item_condition_id,
                      (f"condition {it.item_condition_id}", "good"))
                out[it.id_] = Listing(
                    item_id=it.id_,
                    source="mercari",
                    title=it.name,
                    price=int(it.real_price),
                    is_auction=False,
                    condition=cond,
                    grade=grade,
                )
            page += 1
            if page >= pages:
                break
            try:
                if not res.meta.next_page_token:
                    break
                await asyncio.sleep(1.2)
                res = await res.next_page()
            except Exception:
                break
        await asyncio.sleep(1.2)
    return list(out.values())


def scan_mercari(cfg) -> list[Listing]:
    s = cfg["scan"]
    conds = list(s["mercari_conditions"])
    if not cfg.get("personal", {}).get("enabled", True):
        conds = [c for c in conds if int(c) != 3]
    if not cfg.get("value", {}).get("enabled", True):
        conds = [c for c in conds if int(c) != 4]
    try:
        return _run_async(lambda: _mercari_search_async(
            s["queries"], conds, s["mercari_pages"], s["min_price_jpy"]))
    except Exception as e:
        print(f"  [mercari] scan failed entirely: {e}")
        return []


def fetch_mercari_cycles(item_id: str) -> Optional[int]:
    """Open the full Mercari listing and look for a battery-cycle count."""
    async def _go():
        from mercapi import Mercapi
        m = Mercapi()
        item = await m.item(item_id)
        return find_cycle_count((item.description or "") if item else "")
    try:
        return _run_async(_go)
    except Exception:
        return None


# ============================================================================
# YAHOO! AUCTIONS JP  (HTML search-results scraping)
# ============================================================================

# ---------------------------------------------------------------------------
# Buyee is protected by an AWS WAF *JavaScript challenge* (HTTP 202 + a
# challenge.js page). Plain HTTP clients cannot execute JavaScript, so they
# can never earn the required token. Solution: solve the challenge once in a
# real (invisible) Chromium via Playwright, then hand the earned token cookie
# to the fast curl_cffi layer. If the token handoff isn't accepted, we simply
# fetch every Buyee page through the browser (slower but bulletproof).
# ---------------------------------------------------------------------------

_BUYEE = {"cookie_header": "", "ua": "", "browser_only": False}
_PW = {"pw": None, "browser": None, "ctx": None, "page": None}

_WAF_MARKERS = ("awswaf", "challenge-container", "gokuProps")


def _looks_like_waf(html: str) -> bool:
    head = html[:4000]
    return any(m in head for m in _WAF_MARKERS)


_DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/126.0.0.0 Safari/537.36")


def _browser_page():
    """Start (once) and return a headless-Chromium page via Playwright,
    masked so it doesn't announce itself as a robot: headless Chromium's
    default UA contains 'HeadlessChrome' and navigator.webdriver=true, and
    Buyee's edge server 403s exactly that."""
    if _PW["page"] is not None:
        return _PW["page"]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise FetchError(
            "Playwright is not installed, and Buyee's bot-check needs a real "
            "browser. One-time fix, two commands:\n"
            "            python3 -m pip install playwright\n"
            "            python3 -m playwright install chromium")
    _PW["pw"] = sync_playwright().start()
    _PW["browser"] = _PW["pw"].chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    _PW["ctx"] = _PW["browser"].new_context(
        user_agent=_DESKTOP_UA,
        locale="ja-JP",
        viewport={"width": 1366, "height": 900},
        extra_http_headers={"Accept-Language": "ja,en-GB;q=0.9,en;q=0.8"},
    )
    _PW["ctx"].add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    _PW["page"] = _PW["ctx"].new_page()
    import atexit
    atexit.register(_browser_close)
    # warm up like a human: land on the homepage first (also earns the WAF
    # token there, before we ask for anything search-shaped)
    try:
        _PW["page"].goto("https://buyee.jp/?lang=en",
                         wait_until="domcontentloaded", timeout=30000)
        _PW["page"].wait_for_timeout(2500)
    except Exception:
        pass
    return _PW["page"]


def _browser_close():
    for key in ("browser", "pw"):
        try:
            if _PW[key]:
                (_PW[key].close() if key == "browser" else _PW[key].stop())
        except Exception:
            pass
        _PW[key] = None
    _PW["ctx"] = _PW["page"] = None


_BLOCK_RE = re.compile(r"<title>\s*(403 Forbidden|Access Denied|Forbidden)", re.I)


def _looks_hard_blocked(html: str) -> bool:
    return len(html) < 2500 and bool(_BLOCK_RE.search(html))


def _browser_fetch(url: str, timeout_ms: int = 35000) -> str:
    """Load a Buyee URL in headless Chromium. The AWS WAF challenge runs its
    JavaScript and reloads the page by itself - we just poll until the
    content stops looking like a challenge."""
    page = _browser_page()
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    deadline = time.time() + timeout_ms / 1000
    html = page.content()
    while _looks_like_waf(html) and time.time() < deadline:
        page.wait_for_timeout(1500)   # challenge solves + auto-reloads
        html = page.content()
    if _looks_like_waf(html):
        raise FetchError("Buyee's bot-check did not clear in the browser "
                         "(it may have escalated to a CAPTCHA)", body=html)
    if _looks_hard_blocked(html):
        raise FetchError("Buyee's server refused the browser outright "
                         "(403 page)", status=403, body=html)
    return html


def _adopt_browser_cookies():
    """Copy the browser's earned buyee.jp cookies (incl. the WAF token) into
    a Cookie header the fast HTTP layer can reuse."""
    try:
        cookies = [c for c in _PW["ctx"].cookies()
                   if "buyee.jp" in (c.get("domain") or "")]
        _BUYEE["cookie_header"] = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies)
        _BUYEE["ua"] = _PW["page"].evaluate("navigator.userAgent")
    except Exception:
        pass


def _buyee_http_attempt(url: str) -> tuple:
    """One fast HTTP try. Returns (status_code, text)."""
    headers = {
        "Accept-Language": "ja,en-GB;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://buyee.jp/",
    }
    if _BUYEE["cookie_header"]:
        headers["Cookie"] = _BUYEE["cookie_header"]
    if _BUYEE["ua"]:
        headers["User-Agent"] = _BUYEE["ua"]
    if http_backend() == "curl_cffi":
        from curl_cffi import requests as creq
        sess = _SESSIONS.get("buyee-http")
        if sess is None:
            sess = creq.Session(impersonate="chrome")
            _SESSIONS["buyee-http"] = sess
        r = sess.get(url, headers=headers, timeout=25)
        return r.status_code, r.text or ""
    import requests
    headers.setdefault("User-Agent", UA)
    r = requests.get(url, headers=headers, timeout=25)
    return r.status_code, r.text or ""


def _buyee_get(url: str) -> str:
    """Fetch a Buyee page: fast HTTP when our WAF token is accepted,
    headless browser whenever it isn't."""
    if not _BUYEE["browser_only"]:
        try:
            status, text = _buyee_http_attempt(url)
            if status == 200 and text and not _looks_like_waf(text):
                return text
        except Exception:
            pass
        # token missing/expired/rejected -> earn one in the browser
    html = _browser_fetch(url)
    _adopt_browser_cookies()
    if not _BUYEE["cookie_header"]:
        _BUYEE["browser_only"] = True   # cookies unreadable; stay in browser
    time.sleep(0.6)
    return html


def scan_yahoo(cfg, debug: bool = False) -> list[Listing]:
    """Yahoo! Auctions listings - fetched VIA BUYEE.

    Yahoo! JAPAN has geo-blocked all visitors from the UK/EEA since April
    2022, so auctions.yahoo.co.jp cannot be reached from a UK connection at
    all. Buyee exists precisely to give overseas buyers access to Yahoo
    Auctions, so we search Buyee's mirror of it instead.
    """
    s = cfg["scan"]
    out: dict[str, Listing] = {}
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("  [yahoo/buyee] WARNING: Playwright is not installed. Buyee's "
              "bot-check needs a real browser, so Yahoo results will fail.\n"
              "                One-time fix, two commands:\n"
              "                python3 -m pip install playwright\n"
              "                python3 -m playwright install chromium")
    extra = s.get("buyee_extra_params", "translationType=98&istatus=2")
    # Buyee's own condition filter can't be relied on, so each query is
    # searched with the words Japanese sellers reliably put in titles:
    # 未使用 (unused) / 新品 (brand new) for the resale tier, plus 美品
    # (beautiful condition - catches 極美品/超美品 too) for practically-new.
    words = ["未使用", "新品"]
    if cfg.get("personal", {}).get("enabled", True):
        words.append("美品")
    variants = [f"{q} {w}" for q in s["queries"] for w in words]
    for i, q in enumerate(variants):
        url = f"https://buyee.jp/item/search/query/{quote(q)}?{extra}"
        try:
            html = _buyee_get(url)
        except FetchError as e:
            print(f"  [yahoo/buyee] search '{q}' failed: {e}")
            if debug and e.body:
                with open("debug_buyee_blocked.html", "w", encoding="utf-8") as f:
                    f.write(e.body)
                print("  [yahoo/buyee] saved Buyee's block/error page to "
                      "debug_buyee_blocked.html - send it to Claude for a fix.")
            continue
        except Exception as e:
            print(f"  [yahoo/buyee] search '{q}' failed: {e}")
            continue
        if debug:
            fn = f"debug_buyee_{re.sub(r'[^A-Za-z0-9]+','_',q)}{i}.html"
            with open(fn, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  [yahoo/buyee] saved raw page to {fn}")

        soup = BeautifulSoup(html, "html.parser")
        # Tolerant parsing: any link to a Yahoo-auction item page is a result.
        anchors = soup.select("a[href*='/item/yahoo/auction/'], "
                              "a[href*='/jdirectitems/auction/']")
        found_here = 0
        for a in anchors:
            mid = BUYEE_YAHOO_ID_RE.search(a.get("href", ""))
            if not mid:
                continue
            item_id = mid.group(1)
            if item_id in out:
                continue
            card, text = _climb_to_card(a)
            title = _anchor_title(a, card)
            if not title:
                continue
            if _card_is_sold(card, text):
                continue

            # Buyee may ignore the condition filter, so grade from the title
            # itself (Japanese sellers reliably put condition words there).
            blob = title + " " + text
            grade = _jp_grade(blob)
            if grade is None:
                continue
            # a plain 中古 (used) with no unused wording is only acceptable
            # in the practically-new tier, where 美品 etc. vouched for it
            if grade == "resale" and "中古" in blob and "未使用" not in blob:
                continue

            price, is_auction = _pick_price(text)
            if price is None or price < int(s["min_price_jpy"]):
                continue
            out[item_id] = Listing(
                item_id=item_id,
                source="yahoo",
                title=title,
                price=price,
                is_auction=is_auction,
                condition="未使用" if grade == "resale" else "美品 (used, like new)",
                grade=grade,
            )
            found_here += 1
        if not anchors:
            print(f"  [yahoo/buyee] 0 items parsed for '{q}' - Buyee may have "
                  f"changed its page layout. Run with --debug and send the "
                  f"debug_buyee_*.html file to Claude.")
        elif debug:
            print(f"  [yahoo/buyee] '{q}': {len(anchors)} cards on page, "
                  f"{found_here} passed the new/unused title check")
        time.sleep(1.5)
    return list(out.values())


# Buyee has rebranded Yahoo! Auctions as "JDirectItems Auction" for overseas
# users - accept item links under either name (IDs are the same).
BUYEE_YAHOO_ID_RE = re.compile(
    r"/item/(?:yahoo|jdirectitems)/auction/([a-z]?\d{6,13})", re.I)
# Accept BOTH "198,000円" / "198,000 yen" and "¥198,000" / "￥198,000".
BUYEE_PRICE_RE = re.compile(
    r"[¥￥]\s*([0-9][\d,]{2,})|([0-9][\d,]{2,})\s*(?:円|yen|JPY)", re.I)


def _first_price(text: str) -> Optional[int]:
    m = BUYEE_PRICE_RE.search(text)
    if not m:
        return None
    g = m.group(1) or m.group(2)
    return int(g.replace(",", ""))


_UNUSED_HINT_RE = re.compile(
    r"新品|未使用|未開封|デッドストック|unused|brand\s*new|new\s*in\s*box|sealed",
    re.I)

# "same as new" phrasings describe USED machines even though they contain
# 新品 - they must be checked BEFORE the unused hint.
_JP_SAME_AS_NEW_RE = re.compile(r"新品同様|新品級|ほぼ新品")
# top-cosmetic-grade markers JP sellers put on lightly used, wear-free units
_JP_LIKE_NEW_RE = re.compile(r"極美品|超美品|美品|使用回数少|使用頻度少|使用少|数回使用|使用感なし|使用感無し")


def _jp_grade(blob: str) -> Optional[str]:
    """Classify a JP listing blob: 'resale' (new/unused), 'personal'
    (practically new - zero visible wear), or None (neither)."""
    if _JP_SAME_AS_NEW_RE.search(blob):
        return "personal"
    if _UNUSED_HINT_RE.search(blob):
        return "resale"
    if _JP_LIKE_NEW_RE.search(blob):
        return "personal"
    return None

# Buyee marks sold/ended items with an overlay CLASS (e.g. "soldOut" on the
# card's thumbnail), not with text - so the card's markup must be checked,
# not just its flattened text.
_SOLD_CLASS_RE = re.compile(r"sold[-_]?out", re.I)
_SOLD_TEXT_RE = re.compile(r"SOLD\s*OUT|売り切れ|売切れ|入札期間終了|オークション終了", re.I)


def _card_is_sold(card, text: str) -> bool:
    try:
        for el in card.find_all(class_=True):
            if any(_SOLD_CLASS_RE.search(c) for c in el.get("class", [])):
                return True
    except Exception:
        pass
    return bool(_SOLD_TEXT_RE.search(text))


def _climb_to_card(a) -> tuple:
    """Walk up from an item link until the surrounding element contains a yen
    price - that element is the listing card. Layout-agnostic on purpose."""
    card = a
    for _ in range(5):
        if card.parent is None:
            break
        card = card.parent
        text = card.get_text(" ", strip=True)
        if BUYEE_PRICE_RE.search(text):
            return card, text
    return card, card.get_text(" ", strip=True)


def _anchor_title(a, card) -> str:
    title = (a.get("title") or "").strip()
    if not title:
        img = a.select_one("img[alt]") or card.select_one("img[alt]")
        if img:
            title = (img.get("alt") or "").strip()
    if not title:
        title = a.get_text(" ", strip=True)
    # drop obvious non-titles like a bare "Bid"/"Buy" button label
    return title if len(title) >= 8 else ""


def _pick_price(card_text: str) -> tuple:
    """Return (price, is_auction). Prefer the Buy-It-Now figure (即決 /
    'Buy It Now') if the card shows one, since that's a price you can
    actually pay; otherwise the current bid (price may rise)."""
    bin_m = re.search(
        r"(?:即決|Buy\s*It\s*Now)[^0-9¥￥]{0,20}(?:[¥￥]\s*)?([0-9][\d,]{2,})\s*(?:円|yen|JPY)?",
        card_text, re.I)
    if bin_m:
        return int(bin_m.group(1).replace(",", "")), False
    p = _first_price(card_text)
    if p is not None:
        return p, True
    return None, True


def fetch_yahoo_cycles(item_id: str) -> Optional[int]:
    """Battery-cycle lookup for a Yahoo auction - read from its BUYEE item
    page (which shows the seller's full description), because
    auctions.yahoo.co.jp itself is geo-blocked in the UK/EEA."""
    try:
        html = _buyee_get(
            f"https://buyee.jp/item/yahoo/auction/{item_id}?translationType=98")
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return find_cycle_count(text)
    except Exception:
        return None


# ============================================================================
# RAKUTEN RAKUMA  (2nd-biggest JP flea market, via Buyee - same browser path)
# ============================================================================

# Rakuma item links on Buyee look like /rakuma/item/<id> or /item/rakuma/<id>
# (ids are 32-char hex strings). Require 12+ id characters so path words like
# /rakuma/search can never be mistaken for an item id.
BUYEE_RAKUMA_HREF_RE = re.compile(r"/(?:item/)?rakuma/(?:item/)?([A-Za-z0-9_-]{12,})", re.I)


def scan_rakuma(cfg, debug: bool = False) -> list[Listing]:
    """Rakuten Rakuma listings - fetched VIA BUYEE, exactly like Yahoo.

    Rakuma has no clean public API (unlike Mercari), and Buyee proxies it for
    overseas buyers, so we search Buyee's Rakuma vertical with the same
    headless-browser fetcher that clears Buyee's bot-check.
    """
    s = cfg["scan"]
    out: dict[str, Listing] = {}
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("  [rakuma/buyee] WARNING: Playwright is not installed - Rakuma "
              "needs the browser path. Install it (see the Yahoo note above).")
    extra = s.get("rakuma_extra_params", "status=on_sale")
    words = ["未使用", "新品"]
    if cfg.get("personal", {}).get("enabled", True):
        words.append("美品")
    variants = [f"{q} {w}" for q in s["queries"] for w in words]
    for i, q in enumerate(variants):
        url = f"https://buyee.jp/rakuma/search?keyword={quote(q)}&{extra}"
        try:
            html = _buyee_get(url)
        except FetchError as e:
            print(f"  [rakuma/buyee] search '{q}' failed: {e}")
            if debug and e.body:
                with open("debug_rakuma_blocked.html", "w", encoding="utf-8") as f:
                    f.write(e.body)
                print("  [rakuma/buyee] saved Buyee's block/error page to "
                      "debug_rakuma_blocked.html - send it to Claude for a fix.")
            continue
        except Exception as e:
            print(f"  [rakuma/buyee] search '{q}' failed: {e}")
            continue
        if debug:
            fn = f"debug_rakuma_{re.sub(r'[^A-Za-z0-9]+','_',q)}{i}.html"
            with open(fn, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  [rakuma/buyee] saved raw page to {fn}")

        soup = BeautifulSoup(html, "html.parser")
        anchors = [a for a in soup.select("a[href*='rakuma']")
                   if BUYEE_RAKUMA_HREF_RE.search(a.get("href", ""))]
        found_here = 0
        for a in anchors:
            href = a.get("href", "")
            m = BUYEE_RAKUMA_HREF_RE.search(href)
            if not m:
                continue
            item_id = m.group(1)
            if item_id in out:
                continue
            card, text = _climb_to_card(a)
            title = _anchor_title(a, card)
            if not title:
                continue
            if _card_is_sold(card, text):
                continue
            blob = title + " " + text
            grade = _jp_grade(blob)
            if grade is None:
                continue
            if grade == "resale" and "中古" in blob and "未使用" not in blob:
                continue
            # Rakuma is fixed-price (not auctions), so take the card's yen price.
            price, _ = _pick_price(text)
            if price is None or price < int(s["min_price_jpy"]):
                continue
            out[item_id] = Listing(
                item_id=item_id,
                source="rakuma",
                title=title,
                price=price,
                is_auction=False,
                condition="未使用" if grade == "resale" else "美品 (used, like new)",
                buyee_path=href,          # exact link we found = always valid
                grade=grade,
            )
            found_here += 1
        if not anchors:
            print(f"  [rakuma/buyee] 0 items parsed for '{q}' - Buyee's Rakuma "
                  f"layout may have changed. Run with --debug and send the "
                  f"debug_rakuma_*.html file to Claude.")
        elif debug:
            print(f"  [rakuma/buyee] '{q}': {len(anchors)} cards on page, "
                  f"{found_here} passed the new/unused title check")
        time.sleep(1.5)
    return list(out.values())


def fetch_rakuma_cycles(item_id: str) -> Optional[int]:
    """Battery-cycle lookup for a Rakuma item via its Buyee item page."""
    try:
        html = _buyee_get(f"https://buyee.jp/item/rakuma/{item_id}")
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return find_cycle_count(text)
    except Exception:
        return None


# ============================================================================
# EBAY US  -  live Brand New / Open Box listings (the biggest US resale market)
# ============================================================================
#
# Why eBay US and not Swappa / Mercari US / OfferUp: those three all sit
# behind hard bot-walls (Cloudflare / DataDome captchas) that block even a
# real headless browser, while eBay serves its search results as parseable
# HTML to a warmed-up session. eBay US also happens to have by far the
# largest inventory of new/open-box MacBook Pros, and is the only one a UK
# buyer can purchase from directly (eBay International Shipping / sellers
# who post worldwide) instead of renting a US parcel-forwarder address.

# Real listing ids are 9-15 digits ("Shop on eBay" promo cards use 16).
EBAY_LISTING_ID_RE = re.compile(r"^\d{9,15}$")
EBAY_ITM_HREF_RE = re.compile(r"/itm/(\d{9,15})\b")
EBAY_US_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
EBAY_PRICE_RES = {
    "USD": EBAY_US_PRICE_RE,
    "GBP": re.compile(r"£\s*([\d,]+(?:\.\d{2})?)"),
}
# any supported price symbol - used only to detect "this element has a price"
EBAY_ANY_PRICE_RE = re.compile(r"[\$£]\s*[\d,]{3,}")


def _first_price_in(text: str, currency: str = "USD") -> Optional[float]:
    """First real price in a card's text, skipping financing offers like
    '$76/mo with Affirm' (checked on the full match, not by regex lookahead,
    which would backtrack '$95/mo' into a bogus '$9')."""
    rx = EBAY_PRICE_RES.get(currency, EBAY_US_PRICE_RE)
    for m in rx.finditer(text):
        if re.match(r"\s*/", text[m.end():m.end() + 4]):
            continue
        return float(m.group(1).replace(",", ""))
    return None


def _first_usd_price(text: str) -> Optional[float]:
    return _first_price_in(text, "USD")
# eBay's own condition wording as it appears in result cards.
EBAY_COND_RE = re.compile(r"\b(Brand New|Open Box|New \(Other\)|New other)\b", re.I)


def _ebay_result_cards(soup) -> list:
    """(item_id, card_element) pairs from a search page.

    eBay serves several page layouts (desktop 2025+, older desktop, mobile)
    depending on which browser fingerprint the request wore. The modern
    layout marks each result <li data-listingid=...>; for anything else,
    fall back to climbing up from /itm/ links until a $ price appears -
    the same layout-agnostic trick the Buyee parser uses."""
    pairs = []
    for card in soup.select("li[data-listingid]"):
        pairs.append(((card.get("data-listingid") or "").strip(), card))
    if pairs:
        return pairs
    seen = set()
    for a in soup.select("a[href*='/itm/']"):
        m = EBAY_ITM_HREF_RE.search(a.get("href", ""))
        if not m or m.group(1) in seen:
            continue
        card = a
        for _ in range(8):
            if card.parent is None:
                break
            card = card.parent
            if EBAY_ANY_PRICE_RE.search(card.get_text(" ", strip=True)):
                break
        seen.add(m.group(1))
        pairs.append((m.group(1), card))
    return pairs


# title claims that justify treating an eBay "Used" listing as practically
# new; the cycle-count limit is enforced separately on top of this
EBAY_LIKE_NEW_RE = re.compile(
    r"like\s*new|mint|pristine|excellent\s*cond|flawless|barely\s*used|"
    r"lightly\s*used|hardly\s*used|light\s*use|as\s*new|"
    r"\b\d{1,3}\s*(?:battery\s*)?cycles?\b|cycle\s*count",
    re.I)


# one parser, two eBay sites - the page structure is identical
_EBAY_SITES = {
    "ebay_us": {"domain": "www.ebay.com", "currency": "USD", "lang": "en-US",
                "keyboard": "US", "min_key": "min_price_usd", "min_default": 400},
    "ebay_uk": {"domain": "www.ebay.co.uk", "currency": "GBP", "lang": "en-GB",
                "keyboard": "UK", "min_key": "min_price_gbp", "min_default": 350},
}
_EBAY_NEW_PARAMS = "LH_ItemCondition=1000%7C1500&LH_BIN=1&LH_PrefLoc=1&_sop=10&_ipg=60"
_EBAY_USED_PARAMS = "LH_ItemCondition=3000&LH_BIN=1&LH_PrefLoc=1&_sop=10&_ipg=60"


def scan_ebay_us(cfg, debug: bool = False) -> list[Listing]:
    return _scan_ebay(cfg, "ebay_us", debug)


def scan_ebay_uk(cfg, debug: bool = False) -> list[Listing]:
    """eBay UK - domestic listings compete in every tier with no import
    costs, UK keyboard and UK returns; an underpriced UK listing is the
    best deal of all."""
    return _scan_ebay(cfg, "ebay_uk", debug)


def _scan_ebay(cfg, source: str, debug: bool) -> list[Listing]:
    """eBay search results in two passes:
    pass 1 (new)  - condition Brand New (1000) + Open box (1500) -> resale;
    pass 2 (used) - condition Used (3000): like-new titles -> personal tier,
                    the rest -> "good" (value section only).
    Domestically located, Buy-It-Now, newest first; parsed tolerantly."""
    s = cfg["scan"]
    site = _EBAY_SITES[source]
    min_price = int(s.get(site["min_key"], site["min_default"]))
    out: dict[str, Listing] = {}
    _ebay_pass(cfg, source, s.get(f"{source}_extra_params", _EBAY_NEW_PARAMS),
               min_price, "new", out, debug)
    if (cfg.get("personal", {}).get("enabled", True)
            or cfg.get("value", {}).get("enabled", True)):
        _ebay_pass(cfg, source,
                   s.get(f"{source}_personal_extra_params", _EBAY_USED_PARAMS),
                   min_price, "used", out, debug)
    return list(out.values())


def _ebay_pass(cfg, source: str, extra: str, min_price: int, mode: str,
               out: dict, debug: bool) -> None:
    site = _EBAY_SITES[source]
    domain, currency = site["domain"], site["currency"]
    home = f"https://{domain}/"
    personal_on = cfg.get("personal", {}).get("enabled", True)
    value_on = cfg.get("value", {}).get("enabled", True)
    for q in cfg["scan"]["queries"]:
        url = (f"https://{domain}/sch/i.html?_nkw=" + quote(q)
               + "&_udlo=" + str(min_price) + "&" + extra)
        cards, html, fetch_failed = [], "", False
        # eBay sometimes answers a burst of requests with a card-less
        # interstitial page; one retry after a pause clears it.
        for attempt in (1, 2):
            try:
                html = _http_get(url, referer=home, warmup=home,
                                 lang=site["lang"])
            except Exception as e:
                print(f"  [{source}] search '{q}' failed: {e}")
                if debug and isinstance(e, FetchError) and e.body:
                    with open(f"debug_{source}_blocked.html", "w", encoding="utf-8") as f:
                        f.write(e.body)
                    print(f"  [{source}] saved eBay's block/error page to "
                          f"debug_{source}_blocked.html - send it to Claude for a fix.")
                fetch_failed = True
                break
            # When a query has few exact hits, eBay pads the page with a
            # "Results matching fewer words" section - don't parse that part.
            cut = re.search(r"Results matching fewer words", html, re.I)
            if cut:
                html = html[:cut.start()]
            cards = _ebay_result_cards(BeautifulSoup(html, "html.parser"))
            if cards or attempt == 2:
                break
            time.sleep(4.0)
        if fetch_failed:
            continue
        if debug:
            fn = f"debug_{source}_{mode}_{re.sub(r'[^A-Za-z0-9]+', '_', q)}.html"
            with open(fn, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  [{source}] saved raw page to {fn}")
        found_here = 0
        for item_id, card in cards:
            if not EBAY_LISTING_ID_RE.match(item_id) or item_id in out:
                continue
            text = card.get_text(" ", strip=True)
            img = card.select_one("img[alt]")
            title = (img.get("alt") or "").strip() if img else ""
            if not title:
                a = card.select_one("a[href*='/itm/']")
                title = a.get_text(" ", strip=True) if a else ""
            if len(title) < 8 or title.lower().startswith("shop on ebay"):
                continue
            if mode == "used":
                # like-new title claims -> practically-new tier;
                # everything else -> "good" (best-value section only)
                grade = "personal" if EBAY_LIKE_NEW_RE.search(title) else "good"
                if grade == "personal" and not personal_on:
                    grade = "good"
                if grade == "good" and not value_on:
                    continue
            else:
                grade = "resale"
            price = _first_price_in(text, currency)
            if price is None or price < min_price:
                continue
            low = text.lower()
            # pickup-only listings can't be posted to the buyer
            if (("local pickup" in low or "collection in person" in low)
                    and "shipping" not in low and "postage" not in low):
                continue
            cond_m = EBAY_COND_RE.search(text)
            if mode == "used":
                condition = ("Used (seller: like new)" if grade == "personal"
                             else "Used")
            else:
                condition = cond_m.group(1) if cond_m else "New / Open box"
            out[item_id] = Listing(
                item_id=item_id,
                source=source,
                title=title,
                price=price,
                is_auction=bool(re.search(r"\b\d+\s*bids?\b", low)),
                condition=condition,
                currency=currency,
                url=f"https://{domain}/itm/{item_id}",
                grade=grade,
            )
            # market default layouts: US = ANSI, UK = ISO-GB
            out[item_id].keyboard = site["keyboard"]
            if "best offer" in low:
                out[item_id].best_offer = True
            found_here += 1
        if not cards:
            print(f"  [{source}] 0 result cards for '{q}' ({mode} pass) - "
                  f"eBay may have changed its page layout. Run with --debug "
                  f"and send the debug_{source}_*.html file to Claude.")
        elif debug:
            print(f"  [{source}] '{q}' ({mode}): {len(cards)} cards on page, "
                  f"{found_here} usable")
        time.sleep(1.5)


def fetch_ebay_us_cycles(item_id: str) -> Optional[int]:
    return _fetch_ebay_cycles(item_id, "ebay_us")


def fetch_ebay_uk_cycles(item_id: str) -> Optional[int]:
    return _fetch_ebay_cycles(item_id, "ebay_uk")


def _fetch_ebay_cycles(item_id: str, source: str) -> Optional[int]:
    """Battery-cycle lookup from the full eBay item page (sellers of
    lightly-used units usually state it in the description)."""
    site = _EBAY_SITES[source]
    home = f"https://{site['domain']}/"
    try:
        html = _http_get(f"{home}itm/{item_id}", referer=home, warmup=home,
                         lang=site["lang"])
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return find_cycle_count(text)
    except Exception:
        return None


# ============================================================================
# SWAPPA  -  US tech marketplace, New / Mint condition listings
# ============================================================================
#
# Swappa sits behind a Cloudflare wall that blocks plain HTTP clients AND a
# vanilla headless browser. What does get through (tested July 2026): a real
# installed Google Chrome, patched to hide its automation strings (the
# `patchright` library), running NON-headless with a persistent profile. The
# earned cf_clearance cookie lives in that profile (.swappa_chrome_profile/),
# so later scans sail straight past the challenge. The Chrome window is
# parked off-screen; you'll briefly see a Chrome icon in the Dock / taskbar
# while Swappa is being scanned - that's normal.
#
# This only works from a residential connection with Chrome installed - on a
# cloud runner (GitHub Actions) it will fail gracefully and be skipped.

SWAPPA_PROFILE_DIR = ".swappa_chrome_profile"

# model tiles worth scanning: 14"/16" from 2023 on (M2 Pro generation +)
SWAPPA_YEAR_RE = re.compile(r"20\d\d")
SWAPPA_SIZE_RE = re.compile(r"-(1[346])(?:-|$)")
SWAPPA_LISTING_RE = re.compile(r"/listing/view/([A-Za-z0-9]+)")
SWAPPA_CHALLENGE = "Just a moment"

_SWAPPA_Q = None   # job queue owned by the dedicated Swappa browser thread


def _swappa_worker(q):
    """Owns the stealth Chrome and serves fetch jobs.

    Lives on its OWN thread because patchright's sync API refuses to start
    on a thread where Playwright (Buyee's browser) is already running.

    The window is sent straight to the Dock (minimized via Chrome's DevTools
    protocol - macOS won't keep a window fully off-screen), pages load fine
    while minimized, and a "release" job closes Chrome entirely at the end
    of every scan. The Cloudflare cookie survives in the profile on disk, so
    the next scan reopens without a new challenge."""
    pw = ctx = page = cdp = None

    def close_browser():
        nonlocal pw, ctx, page, cdp
        for closer in (ctx, pw):
            try:
                if closer is not None:
                    (closer.close() if closer is ctx else closer.stop())
            except Exception:
                pass
        pw = ctx = page = cdp = None

    def set_window(state):
        try:
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds",
                     {"windowId": wid, "bounds": {"windowState": state}})
        except Exception:
            pass

    def launch():
        nonlocal pw, ctx, page, cdp
        from patchright.sync_api import sync_playwright
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            SWAPPA_PROFILE_DIR, channel="chrome", headless=False,
            no_viewport=True, args=["--window-position=-2400,-2400"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            cdp = ctx.new_cdp_session(page)
        except Exception:
            cdp = None
        set_window("minimized")   # straight to the Dock - nothing on screen

    while True:
        job = q.get()
        if job is None or job[0] == "close":
            close_browser()
            break
        if job[0] == "release":
            close_browser()
            job[1].set()
            continue
        _, url, timeout_s, box, done = job
        try:
            if page is None:
                launch()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            deadline = time.time() + timeout_s
            restore_at = time.time() + timeout_s / 2
            restored = False
            html = page.content()
            while time.time() < deadline and SWAPPA_CHALLENGE in html:
                if not restored and time.time() >= restore_at:
                    # a fresh challenge that won't clear minimized gets a
                    # real window for the remaining seconds (rare)
                    set_window("normal")
                    restored = True
                page.wait_for_timeout(1500)
                html = page.content()
            if restored:
                set_window("minimized")
            if SWAPPA_CHALLENGE in html:
                raise FetchError("Swappa's Cloudflare challenge did not clear "
                                 "(it may have escalated for this connection)",
                                 body=html)
            box["v"] = html
        except BaseException as e:
            box["e"] = e
        finally:
            done.set()


def _swappa_fetch(url: str, timeout_s: int = 45) -> str:
    """Load a Swappa URL via the browser thread, waiting out Cloudflare's
    challenge if it appears (first ever run takes ~10-20s; afterwards the
    profile's cf_clearance cookie skips it)."""
    global _SWAPPA_Q
    import atexit
    import queue as _queue
    import threading
    if _SWAPPA_Q is None:
        try:
            import patchright  # noqa: F401
        except ImportError:
            raise FetchError(
                "patchright is not installed - Swappa's Cloudflare wall needs it.\n"
                "            One-time fix:  python3 -m pip install patchright\n"
                "            (Google Chrome itself must also be installed.)")
        _SWAPPA_Q = _queue.Queue()
        threading.Thread(target=_swappa_worker, args=(_SWAPPA_Q,),
                         daemon=True).start()
        atexit.register(lambda: _SWAPPA_Q and _SWAPPA_Q.put(("close",)))
    box, done = {}, threading.Event()
    _SWAPPA_Q.put(("fetch", url, timeout_s, box, done))
    if not done.wait(timeout=timeout_s + 60):
        raise FetchError("Swappa fetch timed out (browser thread stuck)")
    if "e" in box:
        raise box["e"]
    return box["v"]


def swappa_release() -> None:
    """Close the Swappa Chrome (if open). Called at the end of every scan so
    no browser is left running between scans - the Cloudflare cookie lives
    in the profile on disk, so the next scan reopens challenge-free."""
    if _SWAPPA_Q is None:
        return
    import threading
    done = threading.Event()
    _SWAPPA_Q.put(("release", done))
    done.wait(timeout=15)


def scan_swappa(cfg, debug: bool = False) -> list[Listing]:
    """Swappa listings for every 14/16-inch MacBook Pro model from 2023 on,
    kept only in the conditions you accept (default New + Mint). Each listing
    card states its chip / RAM / storage, so matching is exact."""
    s = cfg["scan"]
    keep = {str(c).strip().lower()
            for c in s.get("swappa_conditions", ["New", "Mint", "Good"])}
    # Swappa "New" = sealed (resale tier); "Mint" = used but flawless (the
    # practically-new personal tier); "Good" = light wear (value section only)
    if not cfg.get("personal", {}).get("enabled", True):
        keep.discard("mint")
    if not cfg.get("value", {}).get("enabled", True):
        keep.discard("good")
    min_usd = int(s.get("min_price_usd", 400))
    out: dict[str, Listing] = {}
    # the index page occasionally arrives half-rendered - retry it once
    models, idx = [], ""
    for attempt in (1, 2):
        try:
            idx = _swappa_fetch("https://swappa.com/buy/macbooks/macbook-pro")
        except Exception as e:
            print(f"  [swappa] could not reach Swappa: {e}")
            if debug and isinstance(e, FetchError) and e.body:
                with open("debug_swappa_blocked.html", "w", encoding="utf-8") as f:
                    f.write(e.body)
                print("  [swappa] saved the block page to debug_swappa_blocked.html")
            return []
        soup = BeautifulSoup(idx, "html.parser")
        for tile in soup.select("div.card_product"):
            sku = tile.select_one("meta[itemprop=sku]")
            link = tile.select_one("a[href^='/listings/']")
            if not sku or not link:
                continue
            sku = sku.get("content", "")
            ym, sm = SWAPPA_YEAR_RE.search(sku), SWAPPA_SIZE_RE.search(sku)
            if not ym or not sm:
                continue
            if int(ym.group(0)) >= 2023 and int(sm.group(1)) in (14, 16):
                models.append((sku, int(sm.group(1)), link["href"]))
        if models or attempt == 2:
            break
        time.sleep(5.0)
    if not models:
        if debug and idx:
            with open("debug_swappa_index.html", "w", encoding="utf-8") as f:
                f.write(idx)
        print("  [swappa] no model tiles parsed (after a retry) - Swappa may "
              "have changed its page layout. Run with --debug and send "
              "debug_swappa_*.html to Claude.")
        return []
    for sku, size, href in models:
        try:
            html = _swappa_fetch("https://swappa.com" + href)
        except Exception as e:
            print(f"  [swappa] '{sku}' failed: {e}")
            continue
        if debug:
            fn = f"debug_swappa_{re.sub(r'[^A-Za-z0-9]+', '_', sku)}.html"
            with open(fn, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  [swappa] saved raw page to {fn}")
        cards = BeautifulSoup(html, "html.parser").select("div.xui_card_listing")
        found_here = 0
        for card in cards:
            a = card.select_one("a[href^='/listing/view/']")
            if not a:
                continue
            m = SWAPPA_LISTING_RE.search(a["href"])
            if not m or m.group(1) in out:
                continue
            item_id = m.group(1)
            price = (_first_usd_price(a.get_text(" ", strip=True))
                     or _first_usd_price(card.get_text(" ", strip=True)))
            if price is None or price < min_usd:
                continue
            attrs = [x.get_text(" ", strip=True) for x in card.select("span.attr")]
            cond = next((x for x in attrs if "condition" in x.lower()), "")
            cond_word = cond.lower().replace("condition", "").strip()
            if cond_word not in keep:
                continue
            chip = next((x for x in attrs
                         if re.match(r"Apple\s+M\d", x, re.I)), "")
            gbtb = [x for x in attrs if re.fullmatch(r"\d+\s*(?:GB|TB)", x)]
            # synthetic but complete title - the normal spec parser reads it
            title = " ".join(filter(None, [
                f"MacBook Pro {size}", chip.replace("Apple", "").strip(),
                *gbtb, "-", cond_word.title(), "(Swappa)"]))
            out[item_id] = Listing(
                item_id=item_id,
                source="swappa",
                title=title,
                price=price,
                condition=cond_word.title(),
                currency="USD",
                url=f"https://swappa.com/listing/view/{item_id}",
                grade={"new": "resale", "mint": "personal"}.get(cond_word, "good"),
            )
            out[item_id].keyboard = "US"   # US-market Macs are ANSI layout
            found_here += 1
        if debug:
            print(f"  [swappa] '{sku}': {len(cards)} cards, "
                  f"{found_here} in accepted condition")
        time.sleep(1.0)
    return list(out.values())


def fetch_swappa_cycles(item_id: str) -> Optional[int]:
    """Battery-cycle lookup from the full Swappa listing page (sellers often
    state cycle count in the description or a photo caption)."""
    try:
        html = _swappa_fetch(f"https://swappa.com/listing/view/{item_id}")
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return find_cycle_count(text)
    except Exception:
        return None


# ============================================================================
# EBAY UK  -  median of recent SOLD prices for new / open-box-unused units
# ============================================================================

def ebay_uk_sold_median(query: str, debug: bool = False,
                        conditions: str = "1000%7C1500") -> tuple[Optional[float], int]:
    """Returns (median GBP, sample size) from recent eBay UK sold listings,
    UK located. `conditions` is eBay's LH_ItemCondition filter: the default
    "1000|1500" = New + Open box; pass "3000" for Used (this is what feeds
    the condition-aware fair-value maths)."""
    url = (
        "https://www.ebay.co.uk/sch/i.html?_nkw=" + quote(query)
        + "&LH_Sold=1&LH_Complete=1&LH_ItemCondition=" + conditions
        + "&LH_PrefLoc=1&_ipg=120"
    )
    try:
        html = _http_get(url, referer="https://www.ebay.co.uk/", lang="en-GB")
    except Exception as e:
        print(f"  [ebay] fetch failed for '{query}': {e}")
        return None, 0
    if debug:
        fn = f"debug_ebay_{re.sub(r'[^A-Za-z0-9]+','_',query)[:40]}.html"
        with open(fn, "w", encoding="utf-8") as f:
            f.write(html)
    soup = BeautifulSoup(html, "html.parser")
    prices: list[float] = []
    # eBay's 2025+ layout uses .s-card__price; older layouts .s-item__price.
    nodes = soup.select(".s-item__price, .s-card__price")
    # drop struck-through "was" prices so they don't skew the median
    nodes = [n for n in nodes
             if "strikethrough" not in " ".join(n.get("class") or [])]
    if not nodes:
        nodes = soup.select("[class*='item__price'], [class*='card__price']")
    texts = [n.get_text(" ", strip=True) for n in nodes]
    if not texts:  # newer eBay layouts: fall back to regexing the whole page
        texts = re.findall(r"£[\d,]+\.\d{2}", html)
    for t in texts:
        t = unicodedata.normalize("NFKC", t)
        if " to " in t.lower():
            continue
        m = re.search(r"£\s*([\d,]+(?:\.\d{2})?)", t)
        if m:
            prices.append(float(m.group(1).replace(",", "")))
    # keep plausible laptop prices, trim outliers with the IQR rule
    prices = [p for p in prices if 300 <= p <= 8000]
    if len(prices) < 5:
        return None, len(prices)
    prices.sort()
    q1 = prices[len(prices) // 4]
    q3 = prices[(len(prices) * 3) // 4]
    iqr = q3 - q1
    kept = [p for p in prices if q1 - 1.5 * iqr <= p <= q3 + 1.5 * iqr]
    if not kept:
        kept = prices
    return round(statistics.median(kept), 0), len(kept)
