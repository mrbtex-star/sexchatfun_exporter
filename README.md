# SexChatFun Chat Stream Exporter

Exports chat messages from [SexChatFun.com](https://www.sexchatfun.com) chat rooms, with full-size image downloads.

## Quick Start

```bash
# Basic export (auto-launches Chrome, fills guest form)
python main.py --room 1

# With limits and intervals
python main.py --room 1 --limit 200 --interval 5

# JSON + Markdown output
python main.py --room 1 --formats json,md

# Text only (skip images)
python main.py --room 1 --skip-images

# Connect to existing browser (skip guest registration)
python main.py --room 1 --cdp-url http://localhost:9222
```

## Output

```
export/chat_1/2026-11-12/
├── messages.json      # Full message data
├── messages.md        # Human-readable export
├── users.json         # User stats
└── images/            # Downloaded images
```

## Important: Cloudflare Turnstile

When auto-launching Chrome, the script fills the guest form automatically but **you must manually solve the Cloudflare Turnstile captcha** in the browser window. The script waits up to 60 seconds.

## CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--room` | `1` | Room number or path |
| `--output` | `export` | Output directory |
| `--formats` | `json,md` | Comma-separated export formats |
| `--limit` | `0` | Max polling cycles (0=unlimited) |
| `--interval` | `3.0` | Polling interval (seconds) |
| `--cdp-url` | auto | Connect to existing browser |
| `--no-auto-launch` | off | Disable auto-launch |
| `--skip-images` | off | Skip image downloads |
| `--load-older` | off | Load history via "Load More" |
| `--verbose` | off | Verbose logging |
| `--dry-run` | off | Print plan only |
