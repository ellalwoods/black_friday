"""Apply an Add / Edit / Remove issue to data/items.json.

Reads the issue from GITHUB_EVENT_PATH, updates the wishlist, saves a product
photo into images/, and writes a short reply for the issue to /tmp/reply.md.
"""
import html
import io
import json
import os
import re
import secrets
import sys
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

DATA = "data/items.json"
IMAGES = "images"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
TYPES = ["Tops", "Knitwear", "Dresses", "Skirts", "Pants & jeans", "Outerwear",
         "Tailoring", "Activewear", "Swim", "Shoes", "Bags", "Jewellery",
         "Accessories", "Beauty", "Home", "Other"]
PRIO = {"only if cheap": 1, "want": 2, "must have": 3}
FIELDS = {"id": "id", "link": "url", "brand": "brand", "item": "name",
          "type": "type", "size/colour": "variant", "size/color": "variant",
          "usual price": "usualPrice", "max price": "maxPrice",
          "priority": "priority", "notes": "notes"}


def reply(msg):
    with open("/tmp/reply.md", "w") as f:
        f.write(msg)


def money(v):
    v = re.sub(r"[^0-9.]", "", v or "")
    try:
        return round(float(v), 2) if v else None
    except ValueError:
        return None


def parse(body):
    out = {}
    for line in (body or "").splitlines():
        m = re.match(r"^\s*([A-Za-z/ ]+?)\s*:\s*(.*?)\s*$", line)
        if not m:
            continue
        key = FIELDS.get(m.group(1).strip().lower())
        if key and key not in out:
            out[key] = m.group(2)
    if "usualPrice" in out:
        out["usualPrice"] = money(out["usualPrice"])
    if "maxPrice" in out:
        out["maxPrice"] = money(out["maxPrice"])
    if "priority" in out:
        out["priority"] = PRIO.get(out["priority"].strip().lower(), 2)
    if "type" in out and out["type"] and out["type"] not in TYPES:
        out["type"] = "Other"
    for k in ("brand", "name", "variant", "notes"):
        if k in out:
            out[k] = out[k][:200]
    return out


def attached_image(body):
    m = re.search(r"!\[[^\]]*\]\((https://[^)\s]+)\)", body or "")
    if m:
        return m.group(1)
    m = re.search(r'<img[^>]+src="(https://[^"]+)"', body or "")
    return m.group(1) if m else None


def fetch(url, limit=15_000_000):
    headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-AU,en;q=0.9"}
    host = urlparse(url).hostname or ""
    if host == "github.com" or host.endswith(".githubusercontent.com"):
        tok = os.environ.get("GITHUB_TOKEN")
        if tok and host == "github.com":
            headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read(limit), r.headers.get("Content-Type", "")


TYPE_WORDS = [
    ("Dresses", ["dress", "gown", "slip"]),
    ("Skirts", ["skirt", "skort"]),
    ("Pants & jeans", ["jean", "denim", "pant", "trouser", "short", "legging", "culotte"]),
    ("Knitwear", ["knit", "cardigan", "sweater", "jumper", "pullover"]),
    ("Outerwear", ["coat", "jacket", "trench", "puffer", "blazer", "parka", "gilet"]),
    ("Tailoring", ["suit", "waistcoat"]),
    ("Swim", ["swim", "bikini", "one-piece", "one piece"]),
    ("Activewear", ["sports bra", "active", "running", "yoga", "tight"]),
    ("Shoes", ["shoe", "boot", "sneaker", "trainer", "heel", "sandal", "loafer", "flat", "mule", "pump", "slide"]),
    ("Bags", ["bag", "tote", "clutch", "backpack", "purse", "wallet"]),
    ("Jewellery", ["earring", "necklace", "ring", "bracelet", "pendant", "jewel", "anklet"]),
    ("Beauty", ["serum", "lipstick", "mascara", "fragrance", "perfume", "parfum", "moisturiser", "moisturizer", "cleanser", "skincare", "makeup", "foundation", "cream"]),
    ("Home", ["candle", "linen sheet", "towel", "vase", "throw", "cushion", "duvet", "glassware"]),
    ("Accessories", ["hat", "cap", "scarf", "belt", "sunglass", "glove", "hair clip", "watch"]),
    ("Tops", ["top", "shirt", "tee", "t-shirt", "blouse", "tank", "cami", "bodysuit", "polo", "singlet", "hoodie", "sweatshirt"]),
]


def guess_type(*texts):
    t = " ".join(x for x in texts if x).lower()
    for typ, words in TYPE_WORDS:
        if any(re.search(r"\b" + re.escape(w), t) for w in words):
            return typ
    return None


def _num(v):
    try:
        n = float(str(v).replace(",", "").strip())
        return round(n, 2) if n > 0 else None
    except (TypeError, ValueError):
        return None


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _img(v):
    v = _first(v)
    if isinstance(v, dict):
        v = v.get("url") or v.get("contentUrl")
    return v if isinstance(v, str) else None


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _meta(text, *names):
    for n in names:
        for p in (r'<meta[^>]+(?:property|name|itemprop)=["\']' + re.escape(n) + r'["\'][^>]*content=["\']([^"\']*)',
                  r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name|itemprop)=["\']' + re.escape(n) + r'["\']'):
            m = re.search(p, text, re.I)
            if m and m.group(1).strip():
                return html.unescape(m.group(1).strip())
    return None


def shopify(page_url):
    u = urlparse(page_url)
    if "/products/" not in u.path:
        return {}
    raw, _ = fetch(f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}.js", 3_000_000)
    d = json.loads(raw.decode("utf-8", "ignore"))
    out = {"name": d.get("title"), "brand": d.get("vendor"), "category": d.get("product_type")}
    price, compare = d.get("price"), d.get("compare_at_price")
    if price:
        out["price"] = round(price / 100, 2)
    if compare and price and compare > price:
        out["was"] = round(compare / 100, 2)
    img = d.get("featured_image") or _first(d.get("images"))
    if isinstance(img, str):
        out["image"] = ("https:" + img) if img.startswith("//") else img
    return {k: v for k, v in out.items() if v}


def shop_details(page_url):
    """Best-effort product details from a shop page. Missing keys mean unknown."""
    out = {}
    try:
        out.update(shopify(page_url))
    except Exception as e:  # noqa: BLE001
        print("shopify lookup failed:", e, file=sys.stderr)
    try:
        raw, _ = fetch(page_url, 4_000_000)
    except Exception as e:  # noqa: BLE001
        print("shop page fetch failed:", e, file=sys.stderr)
        return out
    text = raw.decode("utf-8", "ignore")
    found = {}
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', text, re.S | re.I):
        try:
            data = json.loads(html.unescape(block.strip()))
        except ValueError:
            continue
        for o in _walk(data):
            t = o.get("@type")
            t = t if isinstance(t, list) else [t]
            if "Product" not in t and "ProductGroup" not in t:
                continue
            found.setdefault("name", o.get("name"))
            b = _first(o.get("brand"))
            found.setdefault("brand", b.get("name") if isinstance(b, dict) else b)
            found.setdefault("image", _img(o.get("image")))
            found.setdefault("category", o.get("category") if isinstance(o.get("category"), str) else None)
            offers = o.get("offers") or (o.get("hasVariant") and [_first(o.get("hasVariant")).get("offers")])
            for off in _walk(offers or []):
                p = _num(off.get("price") or off.get("lowPrice"))
                if p and "price" not in found:
                    found["price"] = p
                spec = off.get("priceSpecification")
                for ps in _walk(spec or []):
                    if "StrikethroughPrice" in str(ps.get("priceType", "")) or "ListPrice" in str(ps.get("priceType", "")):
                        w = _num(ps.get("price"))
                        if w:
                            found.setdefault("was", w)
            break
    meta = {
        "name": _meta(text, "og:title", "twitter:title"),
        "brand": _meta(text, "product:brand", "og:brand", "brand") or _meta(text, "og:site_name"),
        "image": _meta(text, "og:image:secure_url", "og:image", "twitter:image"),
        "price": _num(_meta(text, "product:price:amount", "og:price:amount", "price")),
        "was": _num(_meta(text, "product:original_price:amount")),
    }
    for src in (found, meta):
        for k, v in src.items():
            if v and not out.get(k):
                out[k] = v
    if out.get("image"):
        out["image"] = urljoin(page_url, out["image"])
    if out.get("name"):
        name = html.unescape(str(out["name"])).strip()
        brand = str(out.get("brand") or "")
        parts = re.split(r"\s+[|\u2013\u2014-]\s+", name)
        if len(parts) > 1:
            name = parts[0] if not brand or brand.lower() not in parts[0].lower() else max(parts, key=len)
        if brand and name.lower().startswith(brand.lower() + " "):
            name = name[len(brand) + 1:]
        out["name"] = name[:200]
    if out.get("brand"):
        out["brand"] = html.unescape(str(out["brand"])).strip()[:100]
    for k in ("brand", "name"):
        v = out.get(k)
        if isinstance(v, str) and v.isupper() and len(v) > 3:
            out[k] = v.title()
    return out


def save_image(url, item_id):
    from PIL import Image
    raw, _ = fetch(url)
    im = Image.open(io.BytesIO(raw))
    im = im.convert("RGB")
    im.thumbnail((1100, 1100))
    path = f"{IMAGES}/{item_id}.jpg"
    im.save(path, "JPEG", quality=84, optimize=True)
    return path


def main():
    ev = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    issue = ev["issue"]
    title, body = issue.get("title") or "", issue.get("body") or ""
    action = title.split(":", 1)[0].strip().lower()

    data = json.load(open(DATA))
    items = data.setdefault("items", [])
    fields = parse(body)
    by_id = {i["id"]: i for i in items}

    if action == "remove":
        it = by_id.get(fields.get("id", "").strip())
        if not it:
            reply("I couldn't find that piece, so nothing changed.")
            return
        items.remove(it)
        img = it.get("image")
        if img and img.startswith(IMAGES + "/") and os.path.exists(img):
            os.remove(img)
        json.dump(data, open(DATA, "w"), indent=2, ensure_ascii=False)
        reply(f"Removed **{it.get('brand')} {it.get('name')}** from your edit.")
        return

    if action == "edit":
        it = by_id.get(fields.get("id", "").strip())
        if not it:
            reply("I couldn't find that piece to edit. Try adding it again instead.")
            return
        old_url = it.get("url")
        for k, v in fields.items():
            if k != "id":
                it[k] = v
        if it.get("url") != old_url:
            it.update(status="unchecked", salePrice=None, scanNote="")
    elif action == "add":
        if not re.match(r"^https?://", fields.get("url", "")):
            reply("This needs a product link starting with https:// before I can add it.")
            return
        it = {"id": secrets.token_hex(5), "url": fields["url"], "brand": fields.get("brand", ""),
              "name": fields.get("name", ""), "type": fields.get("type", ""),
              "variant": fields.get("variant", ""), "usualPrice": fields.get("usualPrice"),
              "maxPrice": fields.get("maxPrice"), "priority": fields.get("priority", 2),
              "notes": fields.get("notes", ""), "image": None, "status": "unchecked",
              "salePrice": None, "checkedAt": None, "scanNote": "",
              "addedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        items.append(it)
    else:
        reply("Titles need to start with Add:, Edit: or Remove:.")
        return

    # Fill in whatever was left blank from the shop's own page.
    attached = attached_image(body)
    needs = (not it.get("brand") or not it.get("name") or not it.get("usualPrice")
             or not it.get("type") or (not attached and not it.get("image")))
    info, filled = {}, []
    if needs:
        info = shop_details(it["url"])
        if not it.get("brand") and info.get("brand"):
            it["brand"] = info["brand"]; filled.append("brand")
        if not it.get("name") and info.get("name"):
            it["name"] = info["name"]; filled.append("name")
        if not it.get("usualPrice"):
            p = info.get("was") or info.get("price")
            if p:
                it["usualPrice"] = p; filled.append("price")
        if not it.get("type"):
            g = guess_type(it.get("name"), info.get("category"))
            if g:
                it["type"] = g; filled.append("type")
    if action == "add" and info.get("was") and info.get("price") and info["price"] < info["was"]:
        it.update(status="sale", salePrice=info["price"], scanNote="Already on sale when added",
                  checkedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    missing = [lbl for key, lbl in (("brand", "brand"), ("name", "item name"), ("usualPrice", "price"))
               if not it.get(key)]
    if not it.get("brand"):
        host = (urlparse(it["url"]).hostname or "").removeprefix("www.").split(".")[0]
        it["brand"] = host.replace("-", " ").title() or "Unknown"
    if not it.get("name"):
        it["name"] = "Untitled piece"
    if not it.get("type"):
        it["type"] = "Other"

    photo_msg = ""
    src = attached or (None if it.get("image") else info.get("image"))
    if src:
        try:
            it["image"] = save_image(src, it["id"])
            if not attached:
                filled.append("photo")
        except Exception as e:  # noqa: BLE001
            print("image save failed:", e, file=sys.stderr)
            photo_msg = " I couldn't download a photo, so it shows the brand initial for now."
    elif not it.get("image"):
        photo_msg = " The shop didn't share a photo, so it shows the brand initial for now. Use Edit and drag in a photo to add one."

    note = ""
    if filled:
        note += " Filled in from the shop: " + ", ".join(filled) + "."
    if needs and missing:
        note += " The shop page didn't give me the " + ", ".join(missing) + ", so use Edit on the site if you want to add it."
    json.dump(data, open(DATA, "w"), indent=2, ensure_ascii=False)
    verb = "Added" if action == "add" else "Updated"
    reply(f"{verb} **{it.get('brand')} {it.get('name')}**. It'll show on your site in a minute or two.{note}{photo_msg}")


if __name__ == "__main__":
    main()
