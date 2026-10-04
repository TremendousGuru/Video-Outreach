"""Crawl a storefront and boil it down to a fact sheet.

Strategy for Shopify stores (the common case):
  1. /meta.json      -> real shop name, city, country, currency, tagline
  2. /products.json  -> real product titles, prices, types, tags
  3. homepage + /pages/about + privacy/shipping/returns -> story, signals, socials
Anything not on Shopify still works: we fall back to plain HTML parsing of
homepage + about + privacy.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

ABOUT_HINTS = [
    "about-us", "about", "our-story", "ourstory", "story", "who-we-are", "who-we-are-1",
    "our-mission", "mission", "meet-the-founder", "founder", "the-brand", "our-brand",
]
POLICY_HINTS = ["privacy", "terms", "legal"]
SHIPPING_HINTS = ["shipping", "returns", "refund", "faq", "contact", "sustainab", "care"]

FOUNDER_PATTERNS = [
    r"(?:co-?founded|founded|started|created|launched|built)\s+(?:it\s+)?(?:in\s+\d{4}\s+)?by\s+([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){0,2})",
    r"(?:founder|co-?founder|owner|ceo)\s*,?\s*([A-Z][a-zA-Z'\-]+\s+[A-Z][a-zA-Z'\-]+)",
    r"\bmy name is\s+([A-Z][a-zA-Z'\-]+)",
    r"\b(?:hi|hey|hello)[,!]?\s+i'?m\s+([A-Z][a-zA-Z'\-]+)",
    r"\bmeet\s+([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)?)",
]
NAME_STOPWORDS = {
    "the", "our", "we", "us", "my", "me", "it", "this", "that", "a", "an", "in", "at",
    "shopify", "shop", "store", "brand", "company", "team", "and", "for", "with", "her", "him",
    "them", "they", "you", "your", "all", "one", "first", "then", "now", "here",
}

# Signals safe to build a spoken hook on ("the fact that X"). Weak/ambiguous ones
# are still collected as facts but never quoted back at the store owner.
HOOK_SIGNALS = {
    "handmade", "small-batch", "family-run", "woman-owned", "Black-owned", "Latina-owned",
    "veteran-owned", "minority-owned", "B Corp", "carbon-neutral", "fair trade",
    "made in the USA", "made in the UK", "made in Canada", "made to order",
    "ships worldwide", "organic", "sustainability-led",
}

SIGNAL_RULES: list[tuple[str, str, str]] = [
    (r"hand[\s-]?made|hand[\s-]?crafted", "handmade", "everything is made by hand"),
    (r"small[\s-]?batch", "small-batch", "you work in small batches"),
    (r"family[\s-]?(owned|run|operated)", "family-run", "it's a family-run operation"),
    (r"woman[\s-]?owned|female[\s-]?founded|women[\s-]?owned", "woman-owned", "it's woman-owned"),
    (r"black[\s-]?owned", "Black-owned", "it's Black-owned"),
    (r"latin[ao][\s-]?owned|latina-owned", "Latina-owned", "it's Latina-owned"),
    (r"veteran[\s-]?owned", "veteran-owned", "it's veteran-owned"),
    (r"minority[\s-]?owned", "minority-owned", "it's minority-owned"),
    (r"sustainab|eco[\s-]?friendly|plastic[\s-]?free|zero[\s-]?waste", "sustainability-led", "sustainability is central to how you make things"),
    (r"b[\s-]?corp", "B Corp", "you're B Corp certified"),
    (r"carbon[\s-]?neutral|climate[\s-]?neutral", "carbon-neutral", "you're carbon neutral"),
    (r"organic", "organic", "you work with certified organic materials"),
    (r"fair[\s-]?trade", "fair trade", "you source fair trade"),
    (r"made in (?:the )?(?:usa|u\.s\.a|america)", "made in the USA", "it's made in the USA"),
    (r"made in (?:the )?uk", "made in the UK", "it's made in the UK"),
    (r"made in (?:the )?canada", "made in Canada", "it's made in Canada"),
    (r"made to order|made[\s-]?to[\s-]?order", "made to order", "you make to order"),
    (r"refill|refillable", "refillable", "the packaging is refillable"),
    (r"custom|personaliz|personaliz", "customizable", "you let people customize"),
    (r"subscription", "subscription", "you run a subscription"),
    (r"gift[\s-]?(?:set|box|guide)", "gifting", "gifting is a big use case"),
    (r"worldwide shipping|ships worldwide|international shipping|we ship worldwide", "ships worldwide", "you ship worldwide"),
    (r"wholesale", "wholesale", "you do wholesale"),
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_JUNK_RE = re.compile(
    r"(\.png|\.jpg|\.jpeg|\.gif|\.webp|\.svg|sentry|wixpress|example\.com|@2x|@3x|"
    r"shopify\.com|myshopify\.com|noreply|no-reply|donotreply|u003|cdn\.|schema\.org|w3\.org|"
    r"cloudflare|godaddy|\.js$|\.css$)", re.I
)
SKIP_PRODUCT_RE = re.compile(
    r"gift\s?card|e-?gift|sample|tester|shipping protection|route|donation|tip\b|"
    r"^\W*$|insurance|warranty|digital download|gift wrap|add[- ]?on", re.I
)
TRACKING_QUERY_RE = re.compile(r"^(utm_|ref|variant|fbclid|gclid|srsltid|_pos|_sid|_ss)", re.I)

SOCIAL_PATTERNS = {
    "instagram": r"instagram\.com/([A-Za-z0-9_.]{2,40})",
    "tiktok": r"tiktok\.com/@?([A-Za-z0-9_.]{2,40})",
    "facebook": r"facebook\.com/([A-Za-z0-9_.\-]{2,60})",
    "x": r"(?:twitter|x)\.com/([A-Za-z0-9_]{2,20})",
    "youtube": r"youtube\.com/(?:@|c/|channel/|user/)([A-Za-z0-9_.\-]{2,60})",
    "pinterest": r"pinterest\.com/([A-Za-z0-9_.\-]{2,60})",
    "etsy": r"etsy\.com/shop/([A-Za-z0-9_.\-]{2,60})",
}
SOCIAL_SKIP = {"sharer", "share", "intent", "home", "p", "explore", "reel", "watch", "hashtag", "widgets"}


# ----------------------------------------------------------------- helpers

def clean_domain(raw: str) -> str:
    from .ingest import clean_domain as _cd
    return _cd(raw)


def clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = s.replace("\u00a0", " ").replace("\u2019", "'")
    return s


def money(v, currency: str = "") -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    sym = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "C$", "AUD": "A$", "NGN": "₦", "INR": "₹"}.get(
        (currency or "").upper(), ""
    )
    if f == int(f):
        return f"{sym}{int(f)}"
    return f"{sym}{f:.2f}"


def soupify(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def strip_tags(soup: BeautifulSoup) -> BeautifulSoup:
    s = soup.__copy__()
    for tag in s(["script", "style", "noscript", "svg", "iframe", "template", "form", "input"]):
        tag.decompose()
    return s


NAV_JUNK_RE = re.compile(
    r"(shop all|shop now|subscribe\s*&?\s*save|save up to \d|add to cart|choose your|sign in|my account|"
    r"view all|view cart|continue shopping|free shipping on orders|back to |^\s*menu\s*$|"
    r"cookie|accept all|privacy preferences|powered by shopify|all rights reserved|"
    r"^\s*(home|shop|about|contact|faq|blog|search|cart|collections?)\s*$)", re.I
)


def is_prose(t: str) -> bool:
    """Reject mega-menu rubble: prose has lowercase function words and sentence punctuation."""
    words = t.split()
    if len(words) < 7:
        return False
    if NAV_JUNK_RE.search(t):
        return False
    caps = sum(1 for w in words if w.isupper() and len(w) > 1)
    if caps / len(words) > 0.3:
        return False
    lower = sum(1 for w in words if w.islower())
    if lower / len(words) < 0.35:
        return False
    if len(re.findall(r"\d", t)) > len(words) * 0.45:
        return False
    if not t.endswith((".", "!", "?", '"', "'", "\u2019", "\u201d")) and not re.search(r"[.!?]\s", t):
        return False
    # Word-salad check: real sentences repeat common articles/pronouns.
    if not re.search(r"\b(the|a|an|we|our|you|your|it|is|are|was|to|and|of|in|for|with)\b", t, re.I):
        return False
    return True


def paragraphs(
    soup: BeautifulSoup, min_len: int = 28, limit: int = 120,
    exclude: set[str] | None = None, prose_only: bool = True,
) -> list[str]:
    s = strip_tags(soup)
    out, seen = [], set()
    for el in s.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
        t = clean_text(el.get_text(" "))
        if len(t) < min_len or len(t) > 500:
            continue
        key = t.lower()[:70]
        if key in seen:
            continue
        seen.add(key)
        if prose_only and not is_prose(t):
            continue
        if exclude and t.lower()[:60] in exclude:
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


def meta_desc(soup: BeautifulSoup) -> str:
    for sel in (
        'meta[name="description"]', 'meta[property="og:description"]', 'meta[name="twitter:description"]',
    ):
        el = soup.select_one(sel)
        if el and el.get("content"):
            return clean_text(el["content"])
    return ""


def find_socials(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for platform, pat in SOCIAL_PATTERNS.items():
        for m in re.finditer(pat, html, re.I):
            handle = m.group(1).strip(".").lower()
            if handle in SOCIAL_SKIP or len(handle) < 2:
                continue
            out[platform] = handle
            break
    return out


def find_emails(html: str, host: str = "") -> list[str]:
    found: list[str] = []
    for m in EMAIL_RE.finditer(html or ""):
        e = m.group(0).lower().strip(".")
        if EMAIL_JUNK_RE.search(e) or len(e) > 60:
            continue
        if e not in found:
            found.append(e)
    host_root = host.replace("www.", "")
    stem = host_root.split(".")[0] if host_root else ""
    # Brand addresses first, then anything else.
    found.sort(key=lambda e: (0 if stem and stem[:6] in e else 1, len(e)))
    return found[:5]


def is_internal_host(host: str) -> tuple[bool, str]:
    """Block the crawler from being pointed at private/internal addresses.

    On a public host, a CSV row containing "169.254.169.254" (cloud metadata) or
    an internal service name would otherwise make the server fetch its own
    network on the uploader's behalf. Returns (blocked, reason).
    """
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return True, "empty host"
    if h in {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}:
        return True, "internal hostname"
    if h.endswith((".local", ".internal", ".localhost")):
        return True, "internal hostname"

    import ipaddress
    import socket

    def blocked_ip(ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return bool(
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        )

    if blocked_ip(h):
        return True, f"{h} is a private address"

    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        # Can't resolve - let the fetch fail naturally with its own error.
        return False, ""
    for info in infos:
        addr = info[4][0]
        if blocked_ip(addr):
            return True, f"{h} resolves to a private address ({addr})"
    return False, ""


def detect_shopify(html: str, meta: dict | None) -> bool:
    if meta and meta.get("myshopify_domain"):
        return True
    h = (html or "").lower()
    return any(k in h for k in ("cdn.shopify.com", "shopify.theme", "window.shopify", "shopify-section"))


def page_label(kind: str, url: str) -> str:
    """Readable page names so the Sources tab shows *what* was read, not just 'policy page'."""
    u = url.lower()
    for frag, label in (
        ("privacy", "privacy policy"), ("terms", "terms of service"), ("refund", "refund policy"),
        ("shipping", "shipping policy"), ("contact", "contact page"), ("faq", "FAQ page"),
        ("sustainab", "sustainability page"), ("about", "about page"), ("story", "about page"),
        ("mission", "about page"), ("care", "product care page"), ("returns", "returns policy"),
    ):
        if frag in u:
            return label
    return {"about": "about page", "privacy": "privacy policy", "privacy_alt": "privacy policy",
            "extras": "policy/contact page"}.get(kind, kind)


def link_score(href: str, text: str, hints: list[str]) -> float:
    h = (href or "").lower()
    t = (text or "").lower()
    if any(x in h for x in ("/account", "/cart", "/checkout", "?page=", "mailto:", "tel:", "#")):
        return 0
    score = 0.0
    for i, hint in enumerate(hints):
        weight = len(hints) - i
        if hint in h:
            score += 6 + weight
        if hint in t:
            score += 5 + weight
    if re.search(r"/pages/[a-z0-9\-]+$", h) or re.search(r"/policies/[a-z0-9\-]+$", h):
        score += 1
    return score


def canonical(url: str) -> str:
    p = urlparse(url)
    q = "&".join(
        kv for kv in (p.query or "").split("&")
        if kv and not TRACKING_QUERY_RE.match(kv.split("=")[0])
    )
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{p.netloc}{path}" + (f"?{q}" if q else "")


# ----------------------------------------------------------------- fetching

class Fetcher:
    def __init__(self, client: httpx.AsyncClient, delay: float = 0.5, respect_robots: bool = True):
        self.client = client
        self.delay = max(0.0, delay)
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    async def _polite(self, url: str) -> None:
        host = urlparse(url).netloc
        elapsed = time.monotonic() - self._last_hit.get(host, 0)
        if elapsed < self.delay:
            await asyncio.sleep(self.delay - elapsed)
        self._last_hit[host] = time.monotonic()

    async def robots_for(self, url: str) -> RobotFileParser | None:
        host = urlparse(url).netloc
        if host in self._robots:
            return self._robots[host]
        rp: RobotFileParser | None = None
        try:
            r = await self.client.get(f"{urlparse(url).scheme}://{host}/robots.txt", timeout=10)
            if r.status_code == 200:
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
        except Exception:
            rp = None
        self._robots[host] = rp
        return rp

    async def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = await self.robots_for(url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(UA, url)
        except Exception:
            return True

    async def _request(self, url: str, headers: dict | None = None) -> httpx.Response | None:
        """GET with one patient retry on rate-limiting / transient server errors."""
        for attempt in range(2):
            try:
                r = await self.client.get(url, timeout=22, headers=headers)
            except httpx.HTTPError:
                if attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                return None
            if r.status_code in (429, 503) and attempt == 0:
                wait = 2.0
                ra = r.headers.get("retry-after")
                if ra:
                    try:
                        wait = min(8.0, max(1.0, float(ra)))
                    except ValueError:
                        pass
                await asyncio.sleep(wait)
                self._last_hit[urlparse(url).netloc] = time.monotonic()
                continue
            return r
        return None

    async def get(self, url: str, allow_html: bool = True) -> tuple[int, str, str]:
        if not await self.allowed(url):
            return 0, "", url
        await self._polite(url)
        r = await self._request(url)
        if r is None:
            return -1, "request failed after retry (timeout or network error)", url
        ctype = (r.headers.get("content-type") or "").lower()
        if r.status_code == 200 and allow_html and "html" not in ctype and "text" not in ctype and ctype:
            return r.status_code, "", str(r.url)
        if r.status_code in (429, 503):
            return r.status_code, "", str(r.url)
        return r.status_code, r.text if r.status_code == 200 else "", str(r.url)

    async def get_json(self, url: str):
        if not await self.allowed(url):
            return None, 0
        await self._polite(url)
        r = await self._request(url, headers={**HEADERS, "Accept": "application/json"})
        if r is None:
            return None, -1
        if r.status_code != 200:
            return None, r.status_code
        try:
            return r.json(), 200
        except Exception:
            return None, 200


# ----------------------------------------------------------------- extraction

def extract_products(payload: list[dict], max_products: int = 25) -> list[dict]:
    out = []
    for p in payload or []:
        title = clean_text(p.get("title"))
        if not title or len(title) > 90 or SKIP_PRODUCT_RE.search(title):
            continue
        price = ""
        for v in p.get("variants") or []:
            price = v.get("price")
            if price:
                break
        tags = [clean_text(t).lower() for t in (p.get("tags") or []) if clean_text(t)]
        out.append({
            "title": title,
            "price": price,
            "type": clean_text(p.get("product_type")) or (tags[0] if tags else ""),
            "vendor": clean_text(p.get("vendor")),
            "tags": tags[:6],
            "image": bool((p.get("images") or [])),
        })
    # Prefer stuff that actually looks like a hero product: priced, imaged, informative title.
    out.sort(key=lambda x: (x["image"], bool(x["price"]), len(x["title"])))
    return out[:max_products]


def rank_products(products: list[dict]) -> list[dict]:
    def score(p: dict) -> float:
        s = 0.0
        s += 3 if p.get("image") else 0
        try:
            s += min(float(p.get("price") or 0) / 25.0, 4)
        except (TypeError, ValueError):
            pass
        if 12 <= len(p.get("title", "")) <= 45:
            s += 1.5
        if p.get("type"):
            s += 1
        title = p.get("title", "")
        if re.search(r"\b(set|bundle|collection|kit|pack|trio|duo)\b", title, re.I):
            s -= 1.5
        # Collab products ("Brand x Brand") are rarely the store's hero item.
        if re.search(r"\s[xX]\s", title):
            s -= 3.0
        if title.count("\u2122") >= 1 and re.search(r"\s[xX]\s", title):
            s -= 1.0
        return s

    return sorted(products, key=score, reverse=True)


def find_founder(text: str) -> str:
    for pat in FOUNDER_PATTERNS:
        for m in re.finditer(pat, text or ""):
            cand = clean_text(m.group(1)).strip(" .,'")
            words = cand.split()
            if not words or len(words) > 3:
                continue
            if words[0].lower() in NAME_STOPWORDS:
                continue
            if not words[0][:1].isupper():
                continue
            return " ".join(w for w in words if w.lower() not in {"and", "the", "of"})
    return ""


def find_year(text: str) -> str:
    m = re.search(r"(?:since|est\.?|established|founded in|founded)\D{0,12}((?:19|20)\d{2})", text or "", re.I)
    return m.group(1) if m else ""


def find_signals(text: str) -> list[str]:
    t = (text or "").lower()
    out = []
    for pat, label, phrase in SIGNAL_RULES:
        if re.search(pat, t) and label not in out:
            out.append(label)
    return out[:8]


def signal_phrases(signals: list[str]) -> list[str]:
    return [phrase for _, label, phrase in SIGNAL_RULES if label in (signals or [])][:5]


def strong_signal_phrases(signals: list[str]) -> list[str]:
    """Only the ones that read naturally in a sentence to the owner."""
    return [phrase for _, label, phrase in SIGNAL_RULES if label in HOOK_SIGNALS and label in (signals or [])]


def looks_password_protected(html: str) -> bool:
    """Careful: lock apps inject 'password' CSS everywhere. Only trust titles/page markup."""
    head = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)[:40000]
    if re.search(r"<title>[^<]{0,80}password", head, re.I):
        return True
    if re.search(r"password_protected|opening soon|store will open|enter using password|/password\?redirect", head, re.I):
        return True
    return False


def find_shipping_facts(text: str) -> list[str]:
    t = clean_text(text)
    facts = []
    m = re.search(r"free (?:standard )?shipping (?:on orders? )?(?:over|above|from)\s*([$£€₦₹]?\s?\d+(?:\.\d{2})?)", t, re.I)
    if m:
        facts.append(f"free shipping over {m.group(1).replace(' ', '')}")
    elif re.search(r"free shipping", t, re.I):
        facts.append("free shipping")
    m = re.search(r"\b(\d{2,3})[\s-]?day (?:returns?|money[\s-]?back)", t, re.I)
    if m:
        facts.append(f"{m.group(1)}-day returns")
    if re.search(r"worldwide shipping|ships worldwide|ship worldwide", t, re.I):
        facts.append("ships worldwide")
    if re.search(r"same[\s-]day (?:dispatch|shipping)|next[\s-]day (?:dispatch|shipping)", t, re.I):
        facts.append("fast dispatch")
    if re.search(r"24[\s/-]?7 (?:support|customer)", t, re.I):
        facts.append("24/7 support")
    return facts


def jsonld_org(soup: BeautifulSoup) -> dict:
    out: dict = {}
    for el in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(el.string or "{}")
        except Exception:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            if node.get("@type") in ("Organization", "OnlineStore", "WebSite", "Store", "Brand"):
                out.setdefault("name", clean_text(node.get("name")))
                out.setdefault("description", clean_text(node.get("description")))
                same = node.get("sameAs")
                if isinstance(same, list):
                    out["sameAs"] = [str(x) for x in same][:8]
    return {k: v for k, v in out.items() if v}


# ----------------------------------------------------------------- main entry

async def crawl_store(fetcher: Fetcher, lead: dict, settings: dict) -> dict:
    """Returns {facts, email_found, store_name_found, pages, note, error}"""
    domain = clean_domain(lead.get("domain") or "")
    result: dict = {
        "facts": {}, "email_found": "", "store_name_found": "", "pages": [],
        "ok": False, "error": "", "note": "", "flags": [],
    }
    if not domain:
        result["error"] = "no usable domain"
        return result

    blocked, why = await asyncio.to_thread(is_internal_host, domain)
    if blocked:
        result["error"] = f"refusing to crawl: {why}"
        return result

    max_pages = int(settings.get("max_pages", 5) or 5)
    max_products = int(settings.get("max_products", 25) or 25)

    origin = ""
    home_html = ""
    home_status = 0
    for candidate in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        status, html, final = await fetcher.get(candidate)
        if status == 200 and html:
            origin = f"{urlparse(final).scheme}://{urlparse(final).netloc}"
            home_html = html
            home_status = status
            break
        home_status = status
        if status == 0:  # robots blocked
            break

    if not origin:
        if home_status == 0:
            result["error"] = "robots.txt disallows crawling"
        elif home_status == -1:
            result["error"] = "site not reachable (domain doesn't resolve, or it's down)"
        elif home_status in (429, 503):
            result["error"] = f"site is rate-limiting us (HTTP {home_status}) - raise the delay and retry"
        elif home_status in (401, 403):
            result["error"] = f"site is blocking automated visits (HTTP {home_status})"
        elif home_status == 404:
            result["error"] = "no site at that domain (HTTP 404)"
        else:
            result["error"] = f"homepage unreachable (HTTP {home_status})"
        return result

    result["pages"].append({"label": "homepage", "url": origin, "status": 200})
    home_soup = soupify(home_html)
    facts: dict = {"domain": domain, "url": origin}

    # --- Shopify structured data (fast, exact, no guessing)
    meta, meta_status = await fetcher.get_json(f"{origin}/meta.json")
    shopify = detect_shopify(home_html, meta)
    facts["platform"] = "shopify" if shopify else "unknown"

    if isinstance(meta, dict) and meta.get("name"):
        facts["store_name"] = clean_text(meta.get("name"))
        result["store_name_found"] = facts["store_name"]
        facts["tagline"] = clean_text(meta.get("description"))
        city = clean_text(meta.get("city"))
        region = clean_text(meta.get("province"))
        country = clean_text(meta.get("country"))
        facts["location"] = ", ".join(x for x in (city, region if region != city else "", country) if x)
        facts["currency"] = clean_text(meta.get("currency"))
        if meta.get("ships_to_countries"):
            facts["ships_to"] = [clean_text(c) for c in meta["ships_to_countries"]][:12]
        if meta.get("country"):
            facts["country"] = clean_text(meta["country"])

    products: list[dict] = []
    if shopify:
        payload, pstatus = await fetcher.get_json(f"{origin}/products.json?limit=100")
        if isinstance(payload, dict):
            products = extract_products(payload.get("products") or [], max_products)
            result["pages"].append({
                "label": "products.json", "url": f"{origin}/products.json",
                "status": 200, "note": f"{len(products)} products",
            })
        collections, cstatus = await fetcher.get_json(f"{origin}/collections.json?limit=24")
        if isinstance(collections, dict):
            raw = [clean_text(c.get("title")) for c in (collections.get("collections") or [])]
            names = [
                n for n in raw
                if n and 3 < len(n) < 34
                and not re.search(r"all products|home ?page|front ?page|backup|landing|featured|test|"
                                  r"import|draft|new collection|untitled|\d{5,}|[-_]{2}|"
                                  r"\bsale\b|discount|clearance|gift ?card|wholesale|bundle|"
                                  r"^[\d\s.,%-]+$", n, re.I)
                and re.search(r"[a-zA-Z]{3}", n)
            ]
            facts["collections"] = list(dict.fromkeys(names))[:12]

    # --- HTML-only fallback for product discovery
    if not products:
        seen_titles = set()
        for sel in ("a.product-card__title", ".product-item__title", ".card__heading", "a[href*='/products/']",
                    ".grid-product__title", "h2.card-title", ".product__title"):
            for el in home_soup.select(sel)[:40]:
                t = clean_text(el.get_text(" "))
                if 6 <= len(t) <= 80 and t.lower() not in seen_titles:
                    seen_titles.add(t.lower())
                    products.append({"title": t, "price": "", "type": "", "vendor": "", "tags": [], "image": False})
            if len(products) >= 8:
                break
        products = products[:max_products]

    # --- Homepage text
    home_desc = meta_desc(home_soup)
    title_tag = clean_text(home_soup.title.get_text()) if home_soup.title else ""
    facts["page_title"] = title_tag
    facts["tagline"] = facts.get("tagline") or home_desc
    home_paras = paragraphs(home_soup, limit=60, prose_only=True)
    home_exclude = {p.lower()[:60] for p in home_paras}
    if not facts.get("store_name"):
        guess = ""
        ld = jsonld_org(home_soup)
        if ld.get("name"):
            guess = ld["name"]
        elif title_tag:
            guess = clean_text(re.split(r"[|\-–—:•]", title_tag)[0])[:60]
        if guess:
            facts["store_name"] = guess
            result["store_name_found"] = guess

    # --- Discover + fetch about / privacy / shipping
    links: dict[str, tuple[float, str]] = {}
    for a in home_soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        absu = canonical(urljoin(origin, href))
        if urlparse(absu).netloc.replace("www.", "") != urlparse(origin).netloc.replace("www.", ""):
            continue
        text = clean_text(a.get_text(" "))
        for kind, hints in (("about", ABOUT_HINTS), ("privacy", POLICY_HINTS), ("extras", SHIPPING_HINTS)):
            sc = link_score(absu, text, hints)
            if sc <= 0:
                continue
            if kind == "privacy" and "privacy" not in absu.lower() and "privacy" not in text.lower():
                continue
            best = links.get(kind)
            if not best or sc > best[0]:
                links[kind] = (sc, absu)

    defaults = []
    if shopify:
        defaults = [
            ("about", f"{origin}/pages/about"),
            ("privacy", f"{origin}/policies/privacy-policy"),
        ]
    else:
        defaults = [("about", f"{origin}/about"), ("privacy", f"{origin}/privacy-policy")]

    wanted: list[tuple[str, str]] = []
    for kind, dl in defaults:
        got = links.get(kind)
        wanted.append((kind, got[1] if got else dl))
    wanted.append(("privacy_alt", f"{origin}/pages/privacy-policy" if shopify else f"{origin}/privacy"))
    wanted.append(("extras", f"{origin}/policies/shipping-policy" if shopify else f"{origin}/shipping-policy"))
    wanted.append(("extras", f"{origin}/pages/contact" if shopify else f"{origin}/contact"))

    fetched_pages: dict[str, dict] = {}
    texts: list[str] = []
    about_paragraphs: list[str] = []
    all_html = [home_html]
    about_url_used = ""
    attempts = 0
    for kind, url in wanted:
        if attempts >= max_pages:
            break
        if url in fetched_pages or url == origin:
            continue
        status, html, final = await fetcher.get(url)
        attempts += 1
        if status != 200 or not html:
            fetched_pages[url] = {"kind": kind, "status": status, "html": ""}
            continue
        fetched_pages[url] = {"kind": kind, "status": 200, "html": html}
        all_html.append(html)
        s = soupify(html)
        # Strip the mega-menu boilerplate we already saw on the homepage.
        paras = paragraphs(s, min_len=35, limit=70, exclude=home_exclude)
        texts.extend(paras)
        if kind == "about":
            about_paragraphs = paras
            about_url_used = str(final)
        result["pages"].append({"label": page_label(kind, str(final)), "url": str(final), "status": 200})

    # The /pages/about slug is often a shell page. If we got no real prose, try
    # other common story slugs before giving up on the story material.
    if not about_paragraphs and attempts < max_pages:
        alts = [l for l in (links.get("about")[1] if links.get("about") else "", ) if l]
        alts += [f"{origin}/pages/{s}" for s in ("our-story", "about-us", "story", "our-mission", "who-we-are")]
        for alt in dict.fromkeys(alts):
            if attempts >= max_pages or alt in fetched_pages:
                break
            status, html, final = await fetcher.get(alt)
            attempts += 1
            if status != 200 or not html:
                continue
            fetched_pages[alt] = {"kind": "about", "status": 200, "html": html}
            all_html.append(html)
            paras = paragraphs(soupify(html), min_len=35, limit=70, exclude=home_exclude)
            if paras:
                about_paragraphs = paras
                about_url_used = str(final)
                texts.extend(paras)
                result["pages"].append({"label": "about page", "url": str(final), "status": 200})
                break

    deduped, seen_urls, seen_labels = [], set(), set()
    for pg in result["pages"]:
        if pg.get("url") in seen_urls:
            continue
        # Two different URLs often render the same policy page; keep one per label.
        if pg.get("label") in seen_labels:
            continue
        seen_urls.add(pg.get("url"))
        seen_labels.add(pg.get("label"))
        deduped.append(pg)
    result["pages"] = deduped
    facts["pages_crawled"] = deduped
    facts["privacy_policy_found"] = any(
        v["status"] == 200 and v["kind"] in ("privacy", "privacy_alt") for v in fetched_pages.values()
    )
    facts["about_found"] = bool(about_paragraphs)
    facts["about_url"] = about_url_used
    facts["extras_found"] = [v["kind"] for v in fetched_pages.values() if v["status"] == 200]

    # --- Founder / story / signals
    story_text = "\n".join(about_paragraphs) or "\n".join(texts) or "\n".join(home_paras)
    founder = find_founder(story_text) or find_founder("\n".join(home_paras))
    if founder:
        facts["founder_name"] = founder
        first = founder.split()[0]
        facts["founder_first_name"] = first
    year = find_year(story_text) or find_year("\n".join(home_paras))
    if year:
        facts["founded_year"] = year

    joined = "\n".join(texts + home_paras)
    facts["signals"] = find_signals(joined)
    facts["shipping_facts"] = find_shipping_facts(joined)
    facts["about_snippet"] = " ".join(about_paragraphs[:3])[:700] if about_paragraphs else ""
    facts["home_snippet"] = " ".join(home_paras[:4])[:500]
    facts["socials"] = find_socials(" ".join(all_html))

    products = rank_products(products)
    facts["products"] = products
    if products:
        prices = []
        for p in products:
            try:
                prices.append(float(p["price"]))
            except (TypeError, ValueError):
                pass
        if prices:
            facts["price_min"] = money(min(prices), facts.get("currency", ""))
            facts["price_max"] = money(max(prices), facts.get("currency", ""))
            facts["price_range"] = (
                facts["price_min"] if facts["price_min"] == facts["price_max"]
                else f"{facts['price_min']}-{facts['price_max']}"
            )
        titles_lower = {p["title"].lower() for p in products}
        types = sorted({
            p["type"] for p in products
            if p.get("type") and p["type"].lower() not in titles_lower and len(p["type"]) > 2
        })
        facts["product_types"] = [
            t for t in types
            if not re.search(r"single|one-off|default|::|=>|[0-9a-f]{16}", t, re.I) and len(t) <= 30
        ][:8]
        tags: dict[str, int] = {}
        for p in products:
            for t in p.get("tags") or []:
                tags[t] = tags.get(t, 0) + 1
        facts["top_tags"] = [t for t, _ in sorted(tags.items(), key=lambda kv: -kv[1])[:10]]

    # --- Email discovery when the sheet had none
    if not lead.get("email") and settings.get("find_missing_emails", True):
        found = find_emails(" ".join(all_html), domain)
        if found:
            result["email_found"] = found[0]
            facts["emails_seen"] = found
            result["note"] = f"found {found[0]} on the site"

    # --- Sanity flags
    flags = list(lead.get("flags") or [])
    if looks_password_protected(home_html):
        flags.append("site looks password-protected / coming soon")
    if not products:
        flags.append("no products found - store may be inactive or a non-Shopify / app-only site")
    if not facts.get("about_found"):
        flags.append("no real about page prose found")
    if facts.get("platform") != "shopify":
        flags.append("not detected as Shopify - crawled as a generic site")
    result["flags"] = flags

    result["facts"] = facts
    result["ok"] = True
    bits = [f"{len(products)} products" if products else "no products"]
    if facts.get("about_found"):
        bits.append("about page")
    if facts.get("privacy_policy_found"):
        bits.append("privacy policy")
    if founder:
        bits.append(f"founder: {founder}")
    result["note"] = result["note"] or ", ".join(bits)
    return result
