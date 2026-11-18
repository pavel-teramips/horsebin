# HorseBin — horse.teramips.com

Pastebin whose URLs are three words you can remember while walking to another
computer. Solving xkcd one comic at a time: [#936](https://xkcd.com/936/).

- **Words:** EFF short wordlist #2, trimmed to ≤8 letters (`words.txt`, 949 words, ~9.9 bits each).
  Every word has a unique 3-letter prefix, so `fru lad oys` == `fruit-ladder-oyster`. Typos are
  fixed by edit distance; `fruitladderoyster` is segmented. Logic in `words.py`, mirrored in `static/app.js`
  for the live preview.
- **Lifetimes:** 5m / 15m (default) / 1h / 1d. 1-day pastes get 4 words. Optional burn-after-reading
  (browser needs a POST click to open, so link-preview bots don't burn it; HEAD doesn't burn).
- **Limits:** 25 MB per paste, 2 GB total, 30 new pastes / 10 min / IP, 20 wrong guesses / 10 min / IP → lockout.
- **Data:** `~/horsebin/horsebin.db` (SQLite metadata) + `~/horsebin/blobs/`. Expired rows pruned every 30 s.
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

- Buy `staple.horse` → add it to the Caddy site block and set `HORSEBIN_SITE`.
