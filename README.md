# HorseBin

A pastebin whose URLs are three words you can remember while walking to another computer.
Solving xkcd one comic at a time: [#936](https://xkcd.com/936/).

Live at **[staple.horse](https://staple.horse)**.

1. Paste text or drop a file.
2. You get 3 words, like `fruit ladder oyster`.
3. On the other computer, open the site and type them. The first 3 letters of each word are enough.

```
curl -T notes.txt staple.horse                 # upload, prints the words
echo hi | curl --data-binary @- staple.horse
curl -T x.png "staple.horse/x.png?ttl=1h&burn=1"
curl staple.horse/fru-lad-oys                  # download
```

## How it works

- **Words:** the [EFF short wordlist #2](https://www.eff.org/dice), trimmed to words of 8 letters or fewer
  (`words.txt`, 949 words, about 9.9 bits each). Every word has a unique 3-letter prefix, so `fru lad oys`
  is `fruit-ladder-oyster`. Typos are fixed by edit distance, and `fruitladderoyster` is split back into words.
  The logic is in `words.py`, mirrored in `static/app.js` for the live preview as you type.
- **Lifetimes:** 1 hour or 1 week (default) get 3 words (about 30 bits); forever gets 4 (about 40 bits).
  Expired pastes are never served again (410 Gone) and words are never reused, so old words can't open
  someone else's newer paste. Optional burn-after-reading: a browser needs a click to open it, so link-preview
  bots don't burn it.
- **Guessing:** 3 words is a small space, so it is protected by rate limits, not secrecy: 20 wrong guesses per IP
  per 10 minutes, then a lockout.
- **Limits:** 25 MB per paste, 200 MB live per IP, 2 GB total, 30 new pastes per IP per 10 minutes.
  When stored data reaches 1.5 GB, an upload triggers compaction, which deletes the content of expired pastes.
- **Demo:** `fruit-ladder-oyster`, the example on the home page, opens a built-in demo paste and is never handed out.

## Run it

Python 3 standard library only, no dependencies. SQLite for metadata, files on disk for content.

```
HORSEBIN_DIR=/tmp/hb HORSEBIN_PORT=3059 python3 server.py    # http://127.0.0.1:3059
```

To serve it publicly, put it behind a reverse proxy that sets `X-Forwarded-For` (rate limits are per client IP).
`setup.sh` installs a systemd service plus a [Caddy](https://caddyserver.com) site:

```
sudo DOMAINS="paste.example.com" ABUSE="abuse@example.com" sh setup.sh
journalctl -u horsebin -f      # new and deleted pastes (never content)
```

Settings are environment variables: `HORSEBIN_DIR`, `HORSEBIN_HOST`, `HORSEBIN_PORT`, `HORSEBIN_SITE`
(host name printed in links), `HORSEBIN_ABUSE`, `HORSEBIN_MAX_MB`, `HORSEBIN_MAX_TOTAL_MB`,
`HORSEBIN_MAX_PER_IP_MB`, `HORSEBIN_COMPACT_MB`.

## License

MIT, see `LICENSE`. The word list is from the Electronic Frontier Foundation,
[CC BY 3.0 US](https://creativecommons.org/licenses/by/3.0/us/).
