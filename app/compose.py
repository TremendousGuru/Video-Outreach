"""Turn a fact sheet into a personalized email. AI when a key is set, templates otherwise."""
from __future__ import annotations

import json
import random
import re
import textwrap

import httpx

SYSTEM_PROMPT = """You write cold outreach emails for an independent creator who makes short product videos for small e-commerce brands.

Voice: how a real person types a quick message. Short sentences. Concrete nouns. No marketing voice.

Hard rules:
- Never invent facts. Only use details from the FACTS block. If something isn't there, don't mention it.
- Never say you bought, own, use or have tried the product. You're writing as a stranger who looked at their site.
- Never use placeholders like [Name], [Company], {product} or "insert X".
- No markdown, no bullets, no links, no emoji, no em dashes.
- Banned openers/phrases: "I hope this email finds you well", "I came across your website", "I wanted to reach out", "I am reaching out", "in today's fast-paced", "as a business owner", "I noticed that you", "elevate", "seamless", "leverage".
- Don't compliment vaguely ("amazing brand", "great products"). Compliments must point at a specific, checkable detail.
- Plain text email body, 70-120 words, plus a sign-off."""


def build_facts_block(facts: dict) -> str:
    """Compact, human-readable fact sheet for the prompt."""
    f = facts or {}
    lines: list[str] = []
    add = lambda k, v: lines.append(f"{k}: {v}") if v else None

    add("Store name", f.get("store_name"))
    add("Domain", f.get("domain"))
    add("Platform", f.get("platform"))
    add("Their own tagline", f.get("tagline"))
    add("Based in", f.get("location"))
    add("Founded", f.get("founded_year"))
    add("Founder", f.get("founder_name"))
    if f.get("top_tags"):
        add("Product tags", ", ".join(f["top_tags"][:8]))
    if f.get("product_types"):
        add("Product categories", ", ".join(f["product_types"]))
    if f.get("collections"):
        add("Collections", ", ".join(f["collections"][:6]))
    if f.get("price_range"):
        add("Price range", f["price_range"])
    if f.get("strong_signals"):
        add("Brand signals you may reference", "; ".join(f["strong_signals"]))
    elif f.get("signal_phrases"):
        add("Brand signals (background only, don't quote these back)", "; ".join(f["signal_phrases"]))
    if f.get("shipping_facts"):
        add("Shipping/returns", "; ".join(f["shipping_facts"]))
    if f.get("socials"):
        add("Socials", ", ".join(f"{k}: @{v}" for k, v in f["socials"].items()))

    prods = (f.get("products") or [])[:8]
    if prods:
        lines.append("Products (pick ONE of these to reference, by its exact title):")
        for p in prods:
            bits = [display_product(p.get("title", ""))]
            if p.get("price"):
                bits.append(f"({p.get('price')})")
            if p.get("type"):
                bits.append(f"[{p.get('type')}]")
            lines.append("  - " + " ".join(b for b in bits if b))

    if f.get("about_snippet"):
        lines.append("About page excerpt (verbatim, do not quote at length):")
        lines.append(textwrap.shorten(f["about_snippet"], width=600, placeholder=" ..."))
    elif f.get("home_snippet"):
        lines.append("Homepage excerpt:")
        lines.append(textwrap.shorten(f["home_snippet"], width=400, placeholder=" ..."))

    add("Privacy policy found", "yes" if f.get("privacy_policy_found") else "no")
    return "\n".join(lines)


def build_user_prompt(digest: str, settings: dict, store_hint: str, subject_hint: str) -> str:
    sender = settings.get("sender_name") or "the sender"
    company = settings.get("sender_company") or ""
    offer = settings.get("offer") or "makes short product videos for Shopify stores"
    video_line = settings.get("video_line") or "I already put together a short video for one of your products"
    cta = settings.get("cta") or "Want me to send it over?"
    tone = settings.get("tone") or "friendly, direct"
    subject_style = settings.get("subject_style") or "mix"

    optout = ""
    if settings.get("optout", True):
        optout = f'\n- End with this exact sentence on its own line after the sign-off: "{settings.get("optout_line")}"'

    company_line = f"\n- Sign off as {sender}{(' from ' + company) if company else ''}." if sender else ""

    return f"""FACTS about the store (this is all you know):
{digest}

WRITE ONE EMAIL.

Structure:
1. Greeting. Use the founder's first name only if a founder name is listed above; otherwise "Hi {store_hint} team," or "Hi there,".
2. One or two sentences that prove a human actually looked at their site: name a specific product, or a specific detail from their about page, their categories, or a brand signal. Be plain about it.
3. One sentence of who you are and what you do ({offer}).
4. One sentence framing the video: {video_line}. You have NOT sent it and there is NO link.
5. CTA: {cta}
6. Sign-off. Tone: {tone}.{company_line}{optout}

SUBJECT LINES: give exactly 3 options.
- Option 1: a short lowercase curiosity question about the store being active, in the spirit of "{subject_hint}". Keep it plain and a little blunt, e.g. "is <store> still running".
- Option 2: specific to the product or niche you referenced, also lowercase, under 7 words.
- Option 3: a plain spoken sentence-case question about the video, under 7 words.
Style note: {subject_style}.

Return ONLY valid JSON, no code fences:
{{"subjects": ["...", "...", "..."], "body": "...", "hook": "the exact detail you referenced", "angle": "3-6 word label"}}"""


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


async def compose_ai(facts: dict, settings: dict, store_hint: str = "the store") -> dict:
    """OpenAI-compatible chat completion. Raises on failure so the caller can fall back."""
    base = (settings.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    key = (settings.get("api_key") or "").strip()
    model = settings.get("model") or "gpt-4o-mini"
    if not key:
        raise RuntimeError("no API key")

    subject_hint = settings.get("subject_hint") or "is this store active"
    payload = {
        "model": model,
        "temperature": 0.85,
        "max_tokens": 800,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(build_facts_block(facts), settings, store_hint, subject_hint)},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
        if r.status_code >= 400 and "response_format" in r.text:
            r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("error", {}).get("message", "")
            except Exception:
                detail = r.text[:200]
            raise RuntimeError(f"API {r.status_code}: {detail or r.text[:160]}")

    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("unexpected API response shape")

    parsed = _parse_json(content)
    subjects = [re.sub(r"\s+", " ", str(s)).strip().strip('"') for s in (parsed.get("subjects") or []) if str(s).strip()]
    body = (parsed.get("body") or "").strip()
    if not body:
        raise RuntimeError("model returned no body")
    subjects = [s for s in subjects if 3 <= len(s) <= 90][:3]
    if not subjects:
        subjects = template_subjects(facts, settings)
    return {
        "subjects": subjects,
        "body": body,
        "hook": parsed.get("hook", ""),
        "angle": parsed.get("angle", ""),
        "engine": f"ai:{model}",
    }


# ----------------------------------------------------------------- templates

def display_product(title: str, limit: int = 34) -> str:
    """"Men's Tree Skippers - Sesame (White Sole)" -> "Men's Tree Skippers" when it's too long."""
    t = (title or "").strip()
    if len(t) <= limit:
        return t
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)          # drop trailing "(White Sole)"
    t = re.sub(r"\s+[\u2013\u2014-]\s+.*$", "", t)   # drop " - Colourway"
    if len(t) > limit:
        t = t[:limit].rstrip(" ,;:-")
    return t or title.strip()


def short_store_name(facts: dict, max_words: int = 3, max_chars: int = 26) -> str:
    """'BROWN GIRL Jane Fine Fragrance' -> 'BROWN GIRL Jane' so subject lines stay short."""
    name = (facts.get("store_name") or facts.get("domain") or "your store").strip()
    name = re.sub(r"\s+(inc|llc|ltd|limited|co)\.?$", "", name, flags=re.I)
    name = re.sub(r"\s*[|·—–-]\s+.*$", "", name)  # strip "Store | Tagline" artefacts
    words = name.split()
    if len(words) > max_words:
        name = " ".join(words[:max_words])
    if len(name) > max_chars:
        name = " ".join(words[:2])
    return name.strip() or "your store"


def template_subjects(facts: dict, settings: dict, rng: random.Random | None = None) -> list[str]:
    rng = rng or random.Random()
    f = facts or {}
    name = (f.get("store_name") or f.get("domain") or "your store").strip()
    short = short_store_name(f)
    prods = f.get("products") or []
    product = display_product(prods[0]["title"]) if prods else ""
    hint = (settings.get("subject_hint") or "is this store active").strip()

    # 1) the blunt "is this store active" style
    if hint and hint.lower().startswith("is"):
        s1 = re.sub(r"\bthis store\b", short, hint, flags=re.I)
        if s1.lower() == hint.lower():
            s1 = f"{hint} ({short})"
    else:
        s1 = f"is {short} still active"
    out = [s1.replace("  ", " ").strip()]

    # 2) product-specific, product title kept verbatim
    if product:
        out.append(f"quick question about {product[:46].strip()}")
    elif f.get("product_types"):
        out.append(f"quick question about your {f['product_types'][0][:32].lower()}")
    else:
        out.append(f"one question about {short}")

    # 3) the video, plainly
    out.append(rng.choice([
        f"made a video for {short}",
        f"a video for {short}?",
        f"video idea for {short}",
    ]))

    seen, final = set(), []
    for s in out:
        s = re.sub(r"\s+", " ", s).strip().strip('"')
        if len(s) > 78:
            s = s[:75].rstrip(" ,;:-") + "..."
        k = s.lower()
        if k not in seen:
            seen.add(k)
            final.append(s[0].lower() + s[1:] if len(s) > 3 else s)
    return final[:3]


def template_body(facts: dict, settings: dict, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    f = facts or {}
    store = (f.get("store_name") or f.get("domain") or "your store").strip()
    short = short_store_name(f)
    sender = (settings.get("sender_name") or "").strip()
    company = (settings.get("sender_company") or "").strip()

    prods = f.get("products") or []
    hero = prods[0] if prods else {}
    product = display_product(hero.get("title", ""))
    price = hero.get("price", "")
    currency = f.get("currency", "")
    if price:
        try:
            sym = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "C$", "AUD": "A$", "NGN": "₦", "INR": "₹"}.get(
                currency.upper(), ""
            )
            pv = float(price)
            price_txt = f"{sym}{int(pv) if pv == int(pv) else round(pv, 2)}"
        except (TypeError, ValueError):
            price_txt = ""
    else:
        price_txt = ""

    # Greeting
    founder_first = f.get("founder_first_name")
    if founder_first:
        greeting = rng.choice([f"Hi {founder_first},", f"Hey {founder_first},"])
    else:
        greeting = rng.choice([f"Hi {short} team,", f"Hi {short} team,", f"Hi there,", f"Hey {short} team,"])

    # Hook: the line that proves someone actually looked at the site
    hooks: list[str] = []
    # Only talk about price when it's clearly an entry-level item.
    price_is_entry = False
    if price_txt:
        try:
            pv = float(price)
            mx = 0.0
            for p in prods:
                try:
                    mx = max(mx, float(p.get("price") or 0))
                except (TypeError, ValueError):
                    pass
            price_is_entry = mx > 0 and pv <= min(120.0, mx * 0.7)
        except (TypeError, ValueError):
            price_is_entry = False
    if product and price_txt and price_is_entry:
        hooks.append(f"I ended up on {short} and {product} stopped me. {price_txt} for that is a sharp price point.")
        hooks.append(f"Was on {short} earlier and {product} is the one I kept coming back to.")
    elif product:
        hooks.append(f"I was on {short} earlier and {product} is the one that stuck with me.")
        hooks.append(f"{product} on your site is the product I kept looking at.")
    if f.get("collections"):
        hooks.append(f"Your {f['collections'][0].lower()} range is the bit I'd point at first.")
    # Story-based hooks only when we have real about-page prose and a signal that
    # reads naturally after "the fact that ...".
    strong = strong_signal_phrases_safe(f)
    if f.get("about_snippet") and strong:
        hooks.append(f"Read your about page. The fact that {strong[0]} is what stuck with me.")
        if len(strong) > 1:
            hooks.append(f"The fact that {strong[1]} is the part of your about page I'd lead with.")
    if f.get("product_types") and not f.get("collections"):
        t = f["product_types"][0].lower()
        hooks.append(f"Your {t} range is the thing I'd point at first if someone asked what {short} is about.")
    if not hooks:
        if f.get("location"):
            hooks.append(f"Had a look through {short} earlier. Saw you're based in {f['location'].split(',')[0]}.")
        else:
            hooks.append(f"Had a look through {short} this morning.")

    # Middle: who I am + the video tease
    offer = (settings.get("offer") or "I make short product videos for Shopify stores").rstrip(".")
    video_line = (settings.get("video_line") or "I already put together a short video for one of your products").rstrip(".")
    target = f" for {product}" if product else ""
    mids = [
        f"{offer}. I already put together a short one{target} to show you what I mean, it's about 20 seconds.",
        f"{offer}. I put together a short video{target} before emailing you, no strings.",
        f"{offer}. I made a quick example{target} so you can see the style rather than take my word for it.",
    ]
    if video_line and not any(video_line.lower()[:28] in m.lower() for m in mids):
        mids.append(video_line + f"{target}.")

    # CTA
    cta = (settings.get("cta") or "Want me to send it over?").strip()
    ctas = [cta, "Want me to send it over?", "Should I send it through?", "Worth a look?"]
    ctas = [c if c.endswith(("?", ".", "!")) else c + "?" for c in dict.fromkeys(ctas)]

    chosen_hook = rng.choice(hooks)
    parts = [greeting, chosen_hook, rng.choice(mids), rng.choice(ctas)]

    sign = sender or ""
    if company and sign:
        sign = f"{sign}\n{company}"
    elif company:
        sign = company
    if sign:
        parts.append(sign)

    optouts = [
        "Not interested? Just reply \"no\" and I won't follow up.",
        "If this isn't your thing, reply \"no\" and I'll leave you alone.",
        "Reply \"no\" if you'd rather not hear from me.",
    ]
    if settings.get("optout", True):
        line = (settings.get("optout_line") or rng.choice(optouts)).strip()
        if line:
            parts.append(line)

    body = "\n\n".join(p for p in parts if p)
    return {
        "subjects": template_subjects(f, settings, rng),
        "body": body,
        "hook": chosen_hook[:160],
        "angle": "template",
        "engine": "template",
    }


def strong_signal_phrases_safe(facts: dict) -> list[str]:
    """Phrases that read naturally after "the fact that ...". No crawler import needed."""
    phrases = facts.get("strong_signals") or []
    if phrases:
        return phrases
    # Fallback if the crawler didn't tag them: only trust unambiguous labels.
    safe = {
        "handmade", "small-batch", "family-run", "woman-owned", "Black-owned", "Latina-owned",
        "veteran-owned", "minority-owned", "B Corp", "carbon-neutral", "fair trade",
        "made in the USA", "made in the UK", "made in Canada", "made to order", "ships worldwide",
    }
    return [s for s in (facts.get("signals") or []) if s in safe]


def local_subject_for(facts: dict, settings: dict) -> str:
    return (template_subjects(facts, settings) or ["Quick question"])[0]


async def compose(facts: dict, settings: dict, store_hint: str = "the store") -> dict:
    """AI if possible, template if not. Always returns something usable."""
    if settings.get("use_ai", True) and (settings.get("api_key") or "").strip():
        try:
            return await compose_ai(facts, settings, store_hint)
        except Exception as e:  # noqa: BLE001 - any failure falls back to templates
            out = template_body(facts, settings)
            out["engine"] = "template"
            out["fallback_reason"] = str(e)[:200]
            return out
    return template_body(facts, settings)
