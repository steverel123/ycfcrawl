#!/usr/bin/env python3
"""
Scrape YachtCharterFleet superyacht listings and export per-yacht folders
with images and documentation.

Source: https://www.yachtcharterfleet.com/charter/superyachts-for-charter
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

BASE_URL = "https://www.yachtcharterfleet.com"
LISTING_URL = f"{BASE_URL}/charter/superyachts-for-charter"


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def _element_text(element) -> str | None:
    if not element:
        return None
    return _clean_text(str(element.get_all_text(strip=True)))


def _first(parent, selector: str):
    return parent.css(selector).first


def _parse_price(card) -> dict:
    price_el = _first(card, ".price-container .price") or _first(card, ".price")
    if not price_el:
        return {"display": None, "amount_usd": None, "poa": False}

    poa = "poa" in (price_el.attrib.get("class") or "")
    if poa:
        return {"display": "POA", "amount_usd": None, "poa": True}

    actual = _first(price_el, ".actual-price")
    amount = _element_text(actual)
    per_week = _first(price_el, ".perWeek")
    suffix = _element_text(per_week) or "p/week"
    display = f"from {amount} {suffix}" if amount else _element_text(price_el)

    amount_usd = None
    if amount:
        digits = re.sub(r"[^\d]", "", amount)
        amount_usd = int(digits) if digits else None

    return {"display": display, "amount_usd": amount_usd, "poa": False}


def _is_yacht_image_url(url: str) -> bool:
    if not url.startswith("http"):
        return False
    if "image.yachtcharterfleet.com" not in url:
        return False
    if "/areaguide/" in url or "facebook.com" in url:
        return False
    return "/vessel/" in url


def _add_image_url(urls: list[str], url: str | None) -> None:
    if url and _is_yacht_image_url(url) and url not in urls:
        urls.append(url)


def _collect_listing_images(container) -> list[str]:
    urls: list[str] = []
    for img in container.css("img"):
        for attr in ("src", "data-interlazy-l", "data-interlazy-s"):
            _add_image_url(urls, img.attrib.get(attr))
    return urls


def _image_resource_key(url: str) -> str | None:
    match = re.search(r"/vessel/(?:resource|key/feature)/(\d+)", url)
    return match.group(1) if match else None


def _image_size_score(url: str) -> int:
    match = re.search(r"/w(\d+)/h(\d+)/", url)
    if match:
        return int(match.group(1)) * int(match.group(2))
    return 0


def _dedupe_images_by_resource(urls: list[str]) -> list[str]:
    best: dict[str, str] = {}
    for url in urls:
        key = _image_resource_key(url) or url
        if key not in best or _image_size_score(url) > _image_size_score(best[key]):
            best[key] = url
    return list(best.values())


def _collect_detail_images(page) -> list[str]:
    urls: list[str] = []

    for link in page.css("a.lightbox[href], a.yachtAddPhotos[href]"):
        _add_image_url(urls, link.attrib.get("href"))

    overview = page.css("#overview_image").first
    if overview:
        _add_image_url(urls, overview.attrib.get("src"))

    return _dedupe_images_by_resource(urls)


def parse_yacht_card(card) -> dict | None:
    data_id = card.attrib.get("data-id", "")
    match = re.search(r"vessel_(\d+)", data_id)
    yacht_id = match.group(1) if match else None

    name_el = (
        _first(card, ".js_compare_name")
        or _first(card, "h4.name a")
        or _first(card, "h3.name a")
        or _first(card, "h3 a.name")
    )
    name = _element_text(name_el)
    if not name:
        return None

    href = name_el.attrib.get("href") if name_el else None
    if not href:
        link = _first(card, "a[href*='/luxury-charter-yacht-']")
        href = link.attrib.get("href") if link else None

    if href and not yacht_id:
        id_match = re.search(r"/luxury-charter-yacht-(\d+)/", href)
        yacht_id = id_match.group(1) if id_match else None

    guests = _element_text(_first(card, ".guests span") or _first(card, ".guests"))
    cabins = _element_text(_first(card, ".cabins"))
    length = _element_text(_first(card, ".js_compare_length"))
    builder = _element_text(_first(card, ".js_compare_builder"))
    year_built = _element_text(_first(card, "p.length"))

    slug = None
    if href:
        slug_match = re.search(r"/luxury-charter-yacht-\d+/([^/]+)\.htm", href)
        slug = slug_match.group(1) if slug_match else None

    return {
        "id": yacht_id,
        "name": name,
        "slug": slug,
        "url": urljoin(BASE_URL, href) if href else None,
        "length": length,
        "builder": builder,
        "year_built": year_built,
        "guests": int(guests) if guests and guests.isdigit() else guests,
        "cabins": int(cabins) if cabins and cabins.isdigit() else cabins,
        "price": _parse_price(card),
        "listing_images": _collect_listing_images(card),
    }


def get_max_listing_page(page) -> int:
    match = re.search(r"\bof\s+(\d+)\b", str(page.text))
    if match:
        return int(match.group(1))

    max_page = 1
    for link in page.css("ul.search-results-pagination a[data-page]"):
        value = link.attrib.get("data-page")
        if value and value.isdigit():
            max_page = max(max_page, int(value))
    return max_page


def parse_listing_page(page) -> list[dict]:
    yachts: list[dict] = []
    seen_ids: set[str] = set()

    for card in page.css(".jsYachtSearchResult"):
        record = parse_yacht_card(card)
        if not record or not record.get("id"):
            continue
        if record["id"] in seen_ids:
            continue
        seen_ids.add(record["id"])
        yachts.append(record)

    return yachts


def _collapse_dup(value: str | None) -> str | None:
    """Collapse values rendered twice (e.g. 'Sunseeker Sunseeker' -> 'Sunseeker')."""
    text = _clean_text(value)
    if not text:
        return None
    words = text.split(" ")
    n = len(words)
    if n % 2 == 0 and n >= 2:
        half = n // 2
        if words[:half] == words[half:]:
            return " ".join(words[:half])
    return text


def _parse_meters(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"([\d.]+)\s*m", value)
    return float(match.group(1)) if match else None


def _parse_first_int(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d[\d,]*)", value)
    return int(match.group(1).replace(",", "")) if match else None


def _slug_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def parse_spec_table(page) -> dict:
    """Extract the label/value rows from the yacht specification table."""
    specs: dict[str, str] = {}
    for tbl in page.css("table.minimal-style"):
        for tr in tbl.css("tr"):
            title = tr.css(".title").first
            if not title:
                continue
            label = _clean_text(str(title.get_all_text(strip=True)))
            cells = tr.css("td")
            if not label or len(cells) < 2:
                continue
            value = _collapse_dup(str(cells[-1].get_all_text(strip=True)))
            if value and label not in specs:
                specs[label] = value
    return specs


def normalize_specs(specs: dict) -> dict:
    """Produce normalized numeric/text fields from the raw spec table."""
    lower = {k.lower(): v for k, v in specs.items()}

    def pick(*names: str) -> str | None:
        for name in names:
            if name.lower() in lower:
                return lower[name.lower()]
        return None

    norm: dict = {}
    length = pick("Length")
    if length:
        norm["length_m"] = _parse_meters(length)
        norm["length_display"] = length
    beam = pick("Beam")
    if beam:
        norm["beam_m"] = _parse_meters(beam)
    draft = pick("Draft")
    if draft:
        norm["draft_m"] = _parse_meters(draft)
    tonnage = pick("Gross Tonnage", "Tonnage")
    if tonnage:
        norm["gross_tonnage"] = _parse_first_int(tonnage)
    cruise = pick("Cruising Speed")
    if cruise:
        norm["cruising_speed_knots"] = _parse_first_int(cruise)
    top = pick("Top Speed", "Max Speed", "Maximum Speed")
    if top:
        norm["top_speed_knots"] = _parse_first_int(top)
    built = pick("Built", "Year Built", "Year")
    if built:
        years = re.findall(r"\b(?:19|20)\d{2}\b", built)
        if years:
            norm["year_built"] = int(years[0])
            if "refit" in built.lower() and len(years) > 1:
                norm["refit_year"] = int(years[-1])
    for field, *names in (
        ("builder", "Builder"),
        ("model", "Model"),
        ("naval_architect", "Naval Architect"),
        ("exterior_designer", "Exterior Designer"),
        ("interior_designer", "Interior Design", "Interior Designer"),
        ("flag", "Flag"),
    ):
        value = pick(*names)
        if value:
            norm[field] = value
    return norm


def parse_amenities(page) -> list[str]:
    out: list[str] = []
    for li in page.css(".amenitiesList li .list-inner"):
        text = _clean_text(str(li.get_all_text(strip=True)))
        if text and text not in out:
            out.append(text)
    return out


def parse_toys(page) -> dict:
    """Map each tender/toy tab (Recreation, Tenders, Diving, Wellness, ...) to its text items."""
    container = page.css(".tenders-toys-tabs").first
    if not container:
        return {}

    titles: dict[str, str] = {}
    for anchor in container.css(".tabs-title a"):
        href = anchor.attrib.get("href", "")
        title = _clean_text(str(anchor.get_all_text(strip=True)))
        if href.startswith("#") and title:
            titles[href[1:]] = title

    result: dict[str, list[str]] = {}
    for panel in container.css(".tabs-panel"):
        pid = panel.attrib.get("id", "")
        title = titles.get(pid) or pid or "items"
        items: list[str] = []
        for li in panel.css(".toys-list li .list-inner, ul li .list-inner"):
            text = _clean_text(str(li.get_all_text(strip=True)))
            if text and text not in items:
                items.append(text)
        if items:
            result[_slug_key(title)] = items
    return result


def parse_narrative(page) -> dict:
    """Capture the long-form description and its h4 subsections."""
    block = page.css(".read-more").first
    if not block:
        return {}
    full = _clean_text(str(block.get_all_text(strip=True)))
    sections: dict[str, list[str]] = {}
    current = "overview"
    for child in block.css("h4, p"):
        text = _clean_text(str(child.get_all_text(strip=True)))
        if not text:
            continue
        if getattr(child, "tag", None) == "h4":
            current = _slug_key(text)
        else:
            sections.setdefault(current, []).append(text)
    merged = {k: " ".join(v) for k, v in sections.items() if v}
    return {"full_description": full, "sections": merged}


def parse_json_ld_vehicle(page) -> dict | None:
    for script in page.css('script[type="application/ld+json"]'):
        raw = _element_text(script)
        if not raw or '"@type"' not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict) and data.get("@type") == "Vehicle":
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "Vehicle":
                    return item
    return None


def enrich_from_detail_page(page, yacht: dict) -> dict:
    enriched = dict(yacht)
    vehicle = parse_json_ld_vehicle(page)
    detail_images = _collect_detail_images(page)

    if vehicle:
        enriched["schema"] = vehicle
        enriched["description"] = vehicle.get("description")
        if vehicle.get("name"):
            enriched["name"] = vehicle["name"]
        if vehicle.get("manufacturer"):
            enriched["builder"] = vehicle["manufacturer"]
        if vehicle.get("productionDate"):
            enriched["year_built"] = str(vehicle["productionDate"])
        schema_images = vehicle.get("image") or []
        if isinstance(schema_images, str):
            schema_images = [schema_images]
        for url in schema_images:
            _add_image_url(detail_images, url)

        offers = vehicle.get("offers") or {}
        if isinstance(offers, dict) and offers.get("price"):
            try:
                enriched["price"] = {
                    "display": f"${int(offers['price']):,} {offers.get('priceCurrency', 'USD')}",
                    "amount_usd": int(offers["price"]),
                    "poa": False,
                    "currency": offers.get("priceCurrency", "USD"),
                }
            except (TypeError, ValueError):
                pass

    specs = parse_spec_table(page)
    if specs:
        enriched["specifications"] = specs
        normalized = normalize_specs(specs)
        enriched["specs_normalized"] = normalized
        if normalized.get("length_display") and not enriched.get("length"):
            enriched["length"] = normalized["length_display"]
        if normalized.get("builder") and not enriched.get("builder"):
            enriched["builder"] = normalized["builder"]
        if normalized.get("year_built") and not enriched.get("year_built"):
            enriched["year_built"] = str(normalized["year_built"])

    amenities = parse_amenities(page)
    if amenities:
        enriched["amenities"] = amenities

    toys = parse_toys(page)
    if toys:
        enriched["toys"] = toys

    narrative = parse_narrative(page)
    if narrative:
        if narrative.get("full_description"):
            enriched["full_description"] = narrative["full_description"]
            if not enriched.get("description"):
                enriched["description"] = narrative["full_description"]
        if narrative.get("sections"):
            enriched["description_sections"] = narrative["sections"]

    meta_desc = page.css('meta[name="description"]').first
    if meta_desc and meta_desc.attrib.get("content"):
        enriched["meta_description"] = meta_desc.attrib["content"]

    og_image = page.css('meta[property="og:image"]').first
    if og_image:
        _add_image_url(detail_images, og_image.attrib.get("content"))

    if not detail_images:
        detail_images = list(enriched.get("listing_images") or [])

    enriched["images"] = detail_images
    return enriched


def yacht_folder_name(yacht: dict) -> str:
    slug = yacht.get("slug") or "yacht"
    yacht_id = yacht.get("id") or "unknown"
    safe_slug = re.sub(r"[^\w\-]+", "-", slug.lower()).strip("-")
    return f"{safe_slug}-{yacht_id}"


def image_filename(url: str, index: int) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    return f"{index:03d}{ext}"


def download_images(
    fetcher, urls: list[str], images_dir: Path, delay: float, max_images: int | None
) -> list[dict]:
    images_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []

    if max_images:
        urls = urls[:max_images]

    for index, url in enumerate(urls, start=1):
        filename = image_filename(url, index)
        dest = images_dir / filename
        if dest.exists() and dest.stat().st_size > 0:
            saved.append({"source_url": url, "file": str(dest.relative_to(dest.parent.parent))})
            continue

        try:
            response = fetcher.get(url, impersonate="chrome")
            if response.status >= 400:
                print(f"  skip image HTTP {response.status}: {url}", file=sys.stderr)
                continue
            dest.write_bytes(response.body)
            saved.append({"source_url": url, "file": str(dest.relative_to(dest.parent.parent))})
        except Exception as exc:
            print(f"  failed image {url}: {exc}", file=sys.stderr)

        if delay:
            time.sleep(delay)

    return saved


def write_readme(yacht_dir: Path, yacht: dict, saved_images: list[dict]) -> None:
    price = yacht.get("price") or {}
    price_line = price.get("display") or ("POA" if price.get("poa") else "N/A")

    lines = [
        f"# {yacht.get('name', 'Yacht')}",
        "",
        f"Charter listing: [{yacht.get('url')}]({yacht.get('url')})",
        "",
        "## Overview",
        "",
        f"- **ID:** {yacht.get('id')}",
        f"- **Length:** {yacht.get('length') or 'N/A'}",
        f"- **Builder:** {yacht.get('builder') or 'N/A'}",
        f"- **Year built:** {yacht.get('year_built') or 'N/A'}",
        f"- **Guests:** {yacht.get('guests') or 'N/A'}",
        f"- **Cabins:** {yacht.get('cabins') or 'N/A'}",
        f"- **Weekly price:** {price_line}",
        "",
    ]

    specs = yacht.get("specifications") or {}
    if specs:
        lines.extend(["## Specifications", "", "| Spec | Value |", "| --- | --- |"])
        for label, value in specs.items():
            lines.append(f"| {label} | {value} |")
        lines.append("")

    description = yacht.get("full_description") or yacht.get("description") or yacht.get("meta_description")
    if description:
        lines.extend(["## Description", "", description, ""])

    amenities = yacht.get("amenities") or []
    if amenities:
        lines.extend(["## Amenities & Entertainment", ""])
        lines.extend(f"- {item}" for item in amenities)
        lines.append("")

    toys = yacht.get("toys") or {}
    if toys:
        lines.extend(["## Toys, Tenders & Recreation", ""])
        for category, items in toys.items():
            heading = category.replace("_", " ").title()
            lines.append(f"### {heading}")
            lines.append("")
            lines.extend(f"- {item}" for item in items)
            lines.append("")

    if saved_images:
        lines.extend(["## Images", ""])
        for img in saved_images:
            lines.append(f"- `{img['file']}`")
        lines.append("")

    lines.extend(
        [
            "## Source",
            "",
            f"Data from [YachtCharterFleet]({LISTING_URL}).",
            "",
        ]
    )

    (yacht_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def export_yacht_folder(
    fetcher,
    yacht: dict,
    output_dir: Path,
    *,
    enrich_detail: bool,
    download: bool,
    delay: float,
    resume: bool,
    max_images: int | None,
) -> Path:
    folder = output_dir / yacht_folder_name(yacht)
    metadata_path = folder / "metadata.json"

    if resume and metadata_path.exists():
        return folder

    folder.mkdir(parents=True, exist_ok=True)
    data = dict(yacht)

    if enrich_detail and yacht.get("url"):
        print(f"  detail: {yacht['url']}", file=sys.stderr)
        detail_page = fetcher.get(yacht["url"], impersonate="chrome")
        if detail_page.status < 400:
            data = enrich_from_detail_page(detail_page, data)
        if delay:
            time.sleep(delay)
    else:
        data["images"] = data.get("listing_images", [])

    saved_images: list[dict] = []
    if download:
        image_urls = data.get("images") or data.get("listing_images") or []
        saved_images = download_images(
            fetcher, image_urls, folder / "images", delay / 2, max_images
        )

    data["downloaded_images"] = saved_images
    metadata_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    write_readme(folder, data, saved_images)
    return folder


def scrape_listings(
    fetcher,
    *,
    max_pages: int | None = None,
    delay: float = 0.5,
) -> list[dict]:
    all_yachts: dict[str, dict] = {}
    first = fetcher.get(LISTING_URL, impersonate="chrome")
    if first.status >= 400:
        raise RuntimeError(f"HTTP {first.status} for {LISTING_URL}")

    total_pages = get_max_listing_page(first)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    for page_num in range(1, total_pages + 1):
        url = LISTING_URL if page_num == 1 else f"{LISTING_URL}?page={page_num}"
        print(f"Listing page {page_num}/{total_pages}: {url}", file=sys.stderr)

        response = first if page_num == 1 else fetcher.get(url, impersonate="chrome")
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} for {url}")

        for yacht in parse_listing_page(response):
            all_yachts[yacht["id"]] = yacht

        print(f"  -> {len(all_yachts)} unique yachts so far", file=sys.stderr)
        if page_num < total_pages and delay:
            time.sleep(delay)

    return sorted(all_yachts.values(), key=lambda y: y.get("name") or "")


def run_export(
    *,
    output_dir: Path,
    json_output: Path | None,
    max_pages: int | None,
    max_yachts: int | None,
    delay: float,
    enrich_detail: bool,
    download_images: bool,
    resume: bool,
    max_images: int | None,
    workers: int = 1,
) -> dict:
    from scrapling.fetchers import Fetcher

    yachts = scrape_listings(Fetcher, max_pages=max_pages, delay=delay)
    if max_yachts:
        yachts = yachts[:max_yachts]

    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(yachts)
    print(f"Exporting {total} yachts to {output_dir} with {workers} worker(s)", file=sys.stderr)

    counter = {"done": 0}
    lock = threading.Lock()

    def process(yacht: dict) -> None:
        try:
            export_yacht_folder(
                Fetcher,
                yacht,
                output_dir,
                enrich_detail=enrich_detail,
                download=download_images,
                delay=delay,
                resume=resume,
                max_images=max_images,
            )
        except Exception as exc:  # keep the run going if one yacht fails
            print(f"  ERROR {yacht.get('name')} ({yacht.get('id')}): {exc}", file=sys.stderr)
        finally:
            with lock:
                counter["done"] += 1
                print(f"[{counter['done']}/{total}] {yacht.get('name')}", file=sys.stderr)

    if workers and workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(process, yachts))
    else:
        for yacht in yachts:
            process(yacht)

    result = {
        "source": LISTING_URL,
        "total_yachts": len(yachts),
        "output_dir": str(output_dir),
        "yachts": yachts,
    }

    if json_output:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return result


def main() -> None:
    try:
        from scrapling.fetchers import Fetcher  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Install fetchers: pip install 'scrapling[fetchers]' or pip install -e '.[fetchers]'"
        ) from exc

    parser = argparse.ArgumentParser(
        description="Scrape YachtCharterFleet and export per-yacht folders with images and docs"
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("yachts"),
        help="Root directory for per-yacht folders (default: yachts/)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("data/yachtcharterfleet_superyachts.json"),
        help="Also write combined JSON index",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="Limit listing pages")
    parser.add_argument("--max-yachts", type=int, default=None, help="Limit yachts exported")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between HTTP requests")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of yachts to export in parallel (default: 1)",
    )
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Skip fetching individual yacht pages (listing data only)",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip downloading images",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip yachts that already have metadata.json",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Max images to download per yacht",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only write combined JSON, no per-yacht folders",
    )
    args = parser.parse_args()

    if args.json_only:
        from scrapling.fetchers import Fetcher

        yachts = scrape_listings(Fetcher, max_pages=args.max_pages, delay=args.delay)
        result = {
            "source": LISTING_URL,
            "total_yachts": len(yachts),
            "yachts": yachts,
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {result['total_yachts']} yachts to {args.json}")
        return

    result = run_export(
        output_dir=args.output_dir,
        json_output=args.json,
        max_pages=args.max_pages,
        max_yachts=args.max_yachts,
        delay=args.delay,
        enrich_detail=not args.no_detail,
        download_images=not args.no_images,
        resume=args.resume,
        max_images=args.max_images,
        workers=args.workers,
    )
    print(f"Exported {result['total_yachts']} yachts to {args.output_dir}")
    if args.json:
        print(f"Index JSON: {args.json}")


if __name__ == "__main__":
    main()
