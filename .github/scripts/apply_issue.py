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
    if "type" in out and out["type"] not in TYPES:
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


def shop_image(page_url):
    raw, _ = fetch(page_url, 3_000_000)
    text = raw.decode("utf-8", "ignore")
    pats = [r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)']
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            return urljoin(page_url, html.unescape(m.group(1)))
    return None


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
        item_id = secrets.token_hex(5)
        it = {"id": item_id, "url": fields["url"], "brand": fields.get("brand", ""),
              "name": fields.get("name", ""), "type": fields.get("type", "Other"),
              "variant": fields.get("variant", ""), "usualPrice": fields.get("usualPrice"),
              "maxPrice": fields.get("maxPrice"), "priority": fields.get("priority", 2),
              "notes": fields.get("notes", ""), "image": None, "status": "unchecked",
              "salePrice": None, "checkedAt": None, "scanNote": "",
              "addedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        items.append(it)
    else:
        reply("Titles need to start with Add:, Edit: or Remove:.")
        return

    photo_msg = ""
    src = attached_image(body)
    if not src and not it.get("image"):
        try:
            src = shop_image(it["url"])
        except Exception as e:  # noqa: BLE001
            print("shop page fetch failed:", e, file=sys.stderr)
    if src:
        try:
            it["image"] = save_image(src, it["id"])
        except Exception as e:  # noqa: BLE001
            print("image save failed:", e, file=sys.stderr)
            photo_msg = " I couldn't get a photo, so it shows the brand initial for now."
    elif not it.get("image"):
        photo_msg = " The shop didn't share a photo, so it shows the brand initial for now. Edit it and drag in a photo to add one."

    json.dump(data, open(DATA, "w"), indent=2, ensure_ascii=False)
    verb = "Added" if action == "add" else "Updated"
    reply(f"{verb} **{it.get('brand')} {it.get('name')}**. It'll show on your site in a minute or two.{photo_msg}")


if __name__ == "__main__":
    main()
