"""Fill gaps in data/items.json after it changes.

For each item: download a photo if it has none (from imageSrc or the shop
page), and, once per item, check whether the size in `variant` is in stock
on Shopify shops. Prints "changed" when it wrote the file.
"""
import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.insert(0, ".github/scripts")
from apply_issue import DATA, fetch, save_image, shop_details  # noqa: E402


def size_of(variant):
    m = re.search(r"\b(XXS|XS|S|M|L|XL|XXL|\d{1,2}(?:\.\d)?)\b", variant or "", re.I)
    return m.group(1).upper() if m else None


def shopify_stock(url, size):
    u = urlparse(url)
    if "/products/" not in u.path:
        return None
    raw, _ = fetch(f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}.js", 3_000_000)
    d = json.loads(raw.decode("utf-8", "ignore"))
    for v in d.get("variants", []):
        opts = [str(o).strip().upper() for o in (v.get("options") or [])]
        if size in opts or size in [p.strip() for p in str(v.get("title", "")).upper().split("/")]:
            return bool(v.get("available"))
    return None


def main():
    data = json.load(open(DATA))
    changed = False
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for it in data.get("items", []):
        if not it.get("image"):
            src = it.get("imageSrc")
            if not src:
                try:
                    src = shop_details(it["url"]).get("image")
                except Exception as e:  # noqa: BLE001
                    print("details failed:", it.get("name"), e, file=sys.stderr)
            if src:
                try:
                    it["image"] = save_image(src, it["id"])
                    changed = True
                except Exception as e:  # noqa: BLE001
                    print("photo failed:", it.get("name"), e, file=sys.stderr)
        size = size_of(it.get("variant"))
        if size and not it.get("stockCheckedAt"):
            try:
                avail = shopify_stock(it["url"], size)
            except Exception as e:  # noqa: BLE001
                print("stock check failed:", it.get("name"), e, file=sys.stderr)
                avail = None
            if avail is not None:
                it["stockCheckedAt"] = now
                if avail:
                    it["sizeNote"] = f"Size {size} in stock"
                else:
                    it["sizeNote"] = f"Size {size} sold out"
                    it["status"] = "soldout"
                changed = True
    if changed:
        json.dump(data, open(DATA, "w"), indent=2, ensure_ascii=False)
        print("changed")


if __name__ == "__main__":
    main()
