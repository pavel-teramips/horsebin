# HorseBin — horse.teramips.com

Pastebin whose URLs are three words you can remember while walking to another
computer. Solving xkcd one comic at a time: [#936](https://xkcd.com/936/).

- **Words:** EFF short wordlist #2, trimmed to ≤8 letters (`words.txt`, 949 words, ~9.9 bits each).
  Every word has a unique 3-letter prefix, so `fru lad oys` == `fruit-ladder-oyster`. Typos are
  fixed by edit distance; `fruitladderoyster` is segmented. Logic in `words.py`, mirrored in `static/app.js`
  for the live preview.
- **Lifetimes:** 1h / 1w (default) — 3 words; forever — 4 words. Every row stores its expiry (NULL = forever).
  Expired rows are kept (data too) until auto-compaction (below), but are never served (410 Gone), and
  phrases are never reused. Burn/delete destroy the blob at once and leave a tombstone row (`deleted` set).
  Optional burn-after-reading
  (browser needs a POST click to open, so link-preview bots don't burn it; HEAD doesn't burn).
- **Limits:** 25 MB per paste, 200 MB live per IP, 2 GB total, 30 new pastes / 10 min / IP, 20 wrong guesses / 10 min / IP → lockout.
- **Data:** `~/horsebin/horsebin.db` (SQLite metadata) + `~/horsebin/blobs/`. Only orphan blobs are pruned; when stored data reaches 1.5 GB (`HORSEBIN_COMPACT_MB`)
  the content of all expired pastes is purged (rows kept as tombstones, `purged` set; logged as `compact:`).
- **Demo:** `fruit-ladder-oyster` (the example on the home page) always opens a built-in demo paste; never generated.
- **Abuse:** anyone holding the words can "delete / report abuse"; deletions are logged to the journal.

## Run

    sudo sh ~/dev/horsebin/setup.sh        # systemd horsebin.service on 127.0.0.1:3050 + Caddy vhost
    journalctl -u horsebin -f              # new/deleted pastes (no content)

Dev: `HORSEBIN_DIR=/tmp/hb HORSEBIN_PORT=3059 python3 server.py`

## CLI

    curl -T notes.txt horse.teramips.com
    echo hi | curl --data-binary @- horse.teramips.com
    curl -T x.png "horse.teramips.com/x.png?ttl=1h&burn=1"
    curl horse.teramips.com/fru-lad-oys

## Later

- Domains: `staple.horse` (primary, Cloudflare Registrar, DNS-only A → 188.120.135.68) + `horse.teramips.com`.
