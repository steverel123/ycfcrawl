# YachtCharterFleet export

Scrapes [superyacht listings](https://www.yachtcharterfleet.com/charter/superyachts-for-charter) and creates one folder per yacht.

## Setup

```bash
pip install -e ".[fetchers]"
```

## Output layout

Each yacht gets a folder under `yachts/`:

```
yachts/
  kismet-54507/
    README.md       # human-readable summary
    metadata.json   # full structured data (listing + detail page)
    images/
      001.jpg
      002.jpg
      ...
```

## Commands

**Test run (1 page, 5 yachts):**

```bash
python scrape_yachtcharterfleet.py --max-pages 1 --max-yachts 5
```

**Full fleet (~4,147 yachts, 65 listing pages):**

```bash
python scrape_yachtcharterfleet.py --delay 0.5 --resume
```

`--resume` skips folders that already have `metadata.json` so you can restart safely.

**JSON index only (no folders):**

```bash
python scrape_yachtcharterfleet.py --json-only -o data/yachtcharterfleet_superyachts.json
```

## Options

| Flag | Description |
|------|-------------|
| `--output-dir` | Root folder (default: `yachts/`) |
| `--json` | Combined index JSON path |
| `--max-pages` | Limit listing pages |
| `--max-yachts` | Limit yachts exported |
| `--max-images` | Cap images per yacht |
| `--delay` | Seconds between requests (default: 0.5) |
| `--no-detail` | Listing data only, skip yacht detail pages |
| `--no-images` | Skip image downloads |
| `--resume` | Skip completed yachts |
