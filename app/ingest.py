"""Read lead lists from CSV / XLSX / XLS / TXT and figure out which column is which."""
from __future__ import annotations

import csv
import io
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?([A-Za-z0-9][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)+)", re.I)

FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "mac.com",
    "protonmail.com", "proton.me", "gmx.com", "gmx.de", "mail.com", "zoho.com", "yandex.com",
    "yandex.ru", "qq.com", "163.com", "rediffmail.com",
}

JUNK_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "domain.com",
    "yourdomain.com", "email.com", "test.com", "shopify.com", "myshopify.com",
}

EMAIL_COL_KEYS = ("email", "e-mail", "mail", "contact email", "owner email", "address")
DOMAIN_COL_KEYS = ("domain", "url", "website", "web", "site", "store url", "store domain", "homepage", "link")
NAME_COL_KEYS = ("store name", "store", "shop name", "shop", "company", "brand", "business", "name", "title")

# Delimiters we accept inside .txt lines, in preference order.
TXT_SPLIT = re.compile(r"\t|\s*\|\s*|\s*;\s*|\s{2,}|\s*,\s*")


def clean_domain(raw: str) -> str:
    """'https://www.Foo-Bar.com/collections/all?x=1' -> 'foo-bar.com'"""
    if not raw:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)
    s = s.split("@")[-1]
    s = re.split(r"[/?#]", s, 1)[0]
    s = s.split(":")[0]
    s = s.strip().strip(".")
    if s.startswith("www."):
        s = s[4:]
    if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}", s):
        return ""
    if s in JUNK_DOMAINS:
        return ""
    return s


def is_free_mail(domain: str) -> bool:
    return domain.lower() in FREE_MAIL


def looks_like_email(v: str) -> bool:
    return bool(EMAIL_RE.fullmatch(str(v).strip()))


def looks_like_domain(v: str) -> bool:
    v = str(v).strip()
    if not v or " " in v:
        return False
    if looks_like_email(v):
        return False
    return bool(clean_domain(v)) and "." in v


def store_name_from_domain(domain: str) -> str:
    """brown-girl-jane.com -> 'Brown Girl Jane' (rough; the crawler replaces it with the real name)"""
    if not domain:
        return ""
    stem = domain.split(".")[0]
    stem = re.sub(r"[-_]+", " ", stem)
    parts = [p for p in stem.split() if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z ]", "", str(h or "").strip().lower())


def _score_header(header: str, keys: tuple[str, ...]) -> int:
    h = _norm_header(header)
    if not h:
        return 0
    for i, k in enumerate(keys):
        if h == k:
            return 100 - i
    for i, k in enumerate(keys):
        if k in h:
            return 60 - i
    return 0


def _pick_delimiter(text: str) -> str:
    """Beats csv.Sniffer on messy real-world files: pick the delimiter that yields
    the most consistent, multi-column layout across the first few lines."""
    lines = [l for l in text.splitlines()[:12] if l.strip()]
    if not lines:
        return ","
    best, best_score = ",", 0.0
    for delim in [",", ";", "\t", "|"]:
        counts = [len(next(csv.reader([l], delimiter=delim))) for l in lines]
        multi = [c for c in counts if c > 1]
        if not multi:
            continue
        # Most common column count among the lines that actually split.
        mode = max(set(multi), key=multi.count)
        consistency = multi.count(mode) / len(counts)
        score = len(multi) * consistency * min(mode, 8)
        if score > best_score:
            best, best_score = delim, score
    return best


def _map_columns(headers: list[str], sample_rows: list[list[str]]) -> dict[str, int]:
    """Return {'email': idx, 'domain': idx, 'store_name': idx}. -1 when unknown."""
    idx = {"email": -1, "domain": -1, "store_name": -1}
    if headers:
        # Score every (field, column) pair, then assign greedily so that no two
        # fields can claim the same column (e.g. a header like "email;domain").
        scored: list[tuple[int, str, int]] = []
        for field, keys in (("email", EMAIL_COL_KEYS), ("domain", DOMAIN_COL_KEYS), ("store_name", NAME_COL_KEYS)):
            for i, h in enumerate(headers):
                sc = _score_header(h, keys)
                if sc > 0:
                    scored.append((sc, field, i))
        scored.sort(key=lambda x: (-x[0], x[2]))
        used_cols: set[int] = set()
        for sc, field, i in scored:
            if idx[field] >= 0 or i in used_cols:
                continue
            idx[field] = i
            used_cols.add(i)

    # Fill gaps by sniffing values (only if the header didn't already claim that column).
    taken = {v for v in idx.values() if v >= 0}
    for row in sample_rows[:25]:
        for i, cell in enumerate(row):
            if i in taken or idx["email"] == i:
                continue
            if idx["email"] < 0 and looks_like_email(cell):
                idx["email"] = i
                taken.add(i)
            elif idx["domain"] < 0 and looks_like_domain(cell):
                idx["domain"] = i
                taken.add(i)
        if idx["email"] >= 0 and idx["domain"] >= 0:
            break

    # No name column claimed? Sniff one: any leftover column holding text that
    # isn't an email or a domain.
    if idx["store_name"] < 0:
        taken = {v for v in idx.values() if v >= 0}
        candidates: dict[int, int] = {}
        for row in sample_rows[:40]:
            for i, cell in enumerate(row):
                if i in taken:
                    continue
                c = str(cell or "").strip()
                if (
                    1 < len(c) <= 48
                    and not EMAIL_RE.search(c)
                    and not looks_like_domain(c)
                    and not re.search(r"[;|,\t]", c)
                    and not c.replace(".", "").isdigit()
                    and re.search(r"[A-Za-z]{2}", c)
                ):
                    candidates[i] = candidates.get(i, 0) + 1
        if candidates:
            best, votes = max(candidates.items(), key=lambda kv: kv[1])
            if votes >= max(1, min(3, len(sample_rows[:40]) // 2)):
                idx["store_name"] = best

    # A "name" column that is really just the domain or the email is useless.
    n = idx["store_name"]
    if n >= 0:
        vals = [r[n] for r in sample_rows if n < len(r)]
        if vals and all(looks_like_domain(v) or looks_like_email(v) for v in vals):
            idx["store_name"] = -1
    return idx


def _row_to_lead(row: list[str], idx: dict[str, int]) -> dict | None:
    def cell(i: int) -> str:
        if i < 0 or i >= len(row):
            return ""
        return str(row[i] or "").strip()

    email = cell(idx["email"])
    if email and not looks_like_email(email):
        m = EMAIL_RE.search(email)
        email = m.group(0) if m else ""
    email = email.lower()

    domain = clean_domain(cell(idx["domain"]))
    name = cell(idx["store_name"])

    # No domain column? Take it from the email if it's a branded address.
    if not domain and email:
        ed = email.split("@")[-1]
        if not is_free_mail(ed):
            domain = clean_domain(ed)

    # No email? Leave blank -- the crawler will try the contact page.
    if not domain and not email:
        return None
    if not name and domain:
        name = store_name_from_domain(domain)

    flags = []
    if not email:
        flags.append("no email yet - crawler will look on the site")
    elif is_free_mail(email.split("@")[-1]):
        flags.append("generic mailbox (gmail/yahoo) - not a brand domain")
    if not domain:
        flags.append("domain guessed from the email address")
    return {"email": email, "domain": domain, "store_name": name, "flags": flags}


def _rows_to_leads(rows: list[list[str]], idx: dict[str, int]) -> list[dict]:
    """Map rows using the detected columns. A row that yields nothing (misaligned
    or a different layout entirely) is classified on its own as a fallback."""
    out = []
    for r in rows:
        cells = [str(c or "") for c in r]
        lead = _row_to_lead(cells, idx)
        if lead is None:
            lead = _classify_line(" , ".join(c.strip() for c in cells if str(c).strip()))
        if lead:
            out.append(lead)
    return out


def parse_csv(text: str) -> list[dict]:
    text = text.lstrip("\ufeff")
    delim = _pick_delimiter(text)
    table = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(str(c or "").strip() for c in r)]
    if not table:
        return []
    headers, rows = None, table
    first = [str(c or "").strip() for c in table[0]]
    if not any(looks_like_email(c) or looks_like_domain(c) for c in first):
        headers, rows = first, table[1:]
    idx = _map_columns(headers or [], rows)
    return _rows_to_leads(rows, idx)


def _classify_line(line: str) -> dict | None:
    """One free-form line -> a lead. Order of fields is irrelevant."""
    parts = [p.strip() for p in TXT_SPLIT.split(line) if p.strip()]
    if not parts:
        return None
    email, domain, name_parts = "", "", []
    for p in parts:
        m = EMAIL_RE.search(p)
        if m and not looks_like_domain(p.replace(m.group(0), "").strip(" ,;|")) and not email:
            email = m.group(0).lower()
            continue
        if not domain and looks_like_domain(p):
            domain = clean_domain(p)
            continue
        # Strip a trailing/leading domain or email out of a mixed token, keep the rest as the name.
        rest = EMAIL_RE.sub(" ", p)
        d = looks_like_domain(rest.strip(" ,;|-"))
        if d:
            if not domain:
                domain = clean_domain(rest)
            continue
        if rest.strip(" ,;|-"):
            name_parts.append(rest.strip(" ,;|-"))
    name = " ".join(name_parts).strip()
    if not email and not domain:
        return None
    if not domain and email and not is_free_mail(email.split("@")[-1]):
        domain = clean_domain(email.split("@")[-1])
    if not name and domain:
        name = store_name_from_domain(domain)
    flags = []
    if not email:
        flags.append("no email yet - crawler will look on the site")
    elif is_free_mail(email.split("@")[-1]):
        flags.append("generic mailbox (gmail/yahoo) - not a brand domain")
    if not domain:
        flags.append("domain guessed from the email address")
    return {"email": email, "domain": domain, "store_name": name, "flags": flags}


def parse_txt(text: str) -> list[dict]:
    lines = [l.strip() for l in text.lstrip("\ufeff").splitlines()]
    lines = [l for l in lines if l and not l.startswith("#")]
    if not lines:
        return []

    rows = []
    for line in lines:
        parts = [p for p in TXT_SPLIT.split(line) if p.strip()]
        if not parts:
            continue
        row = []
        for p in parts:
            m = EMAIL_RE.search(p)
            if m and looks_like_email(m.group(0)) and not looks_like_domain(p):
                row.append(m.group(0).lower())
            else:
                row.append(p)
        rows.append(row)

    first = rows[0] if rows else []
    is_header = bool(first) and not any(looks_like_email(c) or looks_like_domain(c) for c in first)

    if is_header:
        # Declared columns: trust the header row.
        headers = first
        body = rows[1:]
        idx = _map_columns(headers, body)
        return _rows_to_leads(body, idx)

    # No header: classify every line on its own so mixed layouts survive.
    out: list[dict] = []
    for line in lines:
        lead = _classify_line(line)
        if lead:
            out.append(lead)
    return out


def parse_xlsx(data: bytes) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    table: list[list[str]] = []
    for r in ws.iter_rows(values_only=True):
        cells = ["" if c is None else str(c).strip() for c in r]
        if any(cells):
            table.append(cells)
    wb.close()
    if not table:
        return []
    first = table[0]
    headers, rows = (first, table[1:]) if not any(
        looks_like_email(c) or looks_like_domain(c) for c in first
    ) else (None, table)
    idx = _map_columns(headers or [], rows)
    return _rows_to_leads(rows, idx)


def parse_pasted(text: str) -> list[dict]:
    """Free-form paste: one lead per line, any order, any separator."""
    if re.search(r",|\t|;|\|", text) and text.count("\n") < 2:
        return parse_csv(text)
    return parse_txt(text)


def parse_file(filename: str, data: bytes) -> list[dict]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xltx")):
        return parse_xlsx(data)
    if name.endswith(".xls"):
        raise ValueError("Old .xls format isn't supported - re-save it as .xlsx or .csv and try again.")
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Could not read that file as text.")
    if name.endswith(".txt"):
        return parse_txt(text)
    return parse_csv(text)
