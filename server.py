#!/usr/bin/env python3
"""horsebin — pastebin whose URLs are three words you can remember on the walk
to another computer. https://xkcd.com/936/

Stdlib only. Listens on HORSEBIN_HOST:HORSEBIN_PORT behind Caddy.
Data: HORSEBIN_DIR/horsebin.db (metadata) + HORSEBIN_DIR/blobs/<id> (content).
"""
import email.parser
import email.policy
import html
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import words as W

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("HORSEBIN_DIR", str(Path.home() / "horsebin")))
HOST = os.environ.get("HORSEBIN_HOST", "127.0.0.1")
PORT = int(os.environ.get("HORSEBIN_PORT", "3050"))
SITE = os.environ.get("HORSEBIN_SITE", "horse.teramips.com")
ABUSE = os.environ.get("HORSEBIN_ABUSE", "horse-abuse@trips.teramips.com")
MAX_BYTES = int(os.environ.get("HORSEBIN_MAX_MB", "25")) * 1024 * 1024
MAX_TOTAL = int(os.environ.get("HORSEBIN_MAX_TOTAL_MB", "2048")) * 1024 * 1024
MAX_PER_IP = int(os.environ.get("HORSEBIN_MAX_PER_IP_MB", "200")) * 1024 * 1024
PREVIEW_BYTES = 1024 * 1024

TTLS = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
DEFAULT_TTL = "15m"
TTL_LABEL = {"5m": "5 minutes", "15m": "15 minutes", "1h": "1 hour", "1d": "1 day"}

MISS_LIMIT, MISS_WINDOW = 20, 600      # wrong guesses per IP per 10 min
NEW_LIMIT, NEW_WINDOW = 30, 600        # pastes per IP per 10 min

INLINE_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
CLI_UA = re.compile(r"^(curl|wget|httpie|python-requests|python-urllib|go-http|powershell|libwww|aria2|fetch)", re.I)

PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: "
            "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
RAW_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"

# ---------------------------------------------------------------- storage

BLOBS = DATA / "blobs"
BLOBS.mkdir(parents=True, exist_ok=True)
DB = sqlite3.connect(DATA / "horsebin.db", check_same_thread=False, isolation_level=None)
DB.execute("PRAGMA journal_mode=WAL")
DB.execute("""CREATE TABLE IF NOT EXISTS paste (
    phrase TEXT PRIMARY KEY, blob TEXT NOT NULL, filename TEXT, mime TEXT,
    size INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
    burn INTEGER NOT NULL DEFAULT 0, views INTEGER NOT NULL DEFAULT 0, ip TEXT)""")
LOCK = threading.Lock()


def q(sql, *args):
    with LOCK:
        return DB.execute(sql, args).fetchall()


def get(phrase):
    rows = q("SELECT phrase, blob, filename, mime, size, created, expires, burn, views "
             "FROM paste WHERE phrase=? AND expires>?", phrase, time.time())
    if not rows:
        return None
    keys = ("phrase", "blob", "filename", "mime", "size", "created", "expires", "burn", "views")
    return dict(zip(keys, rows[0]))


def delete(phrase):
    rows = q("DELETE FROM paste WHERE phrase=? RETURNING blob", phrase)
    for (b,) in rows:
        (BLOBS / b).unlink(missing_ok=True)
    return bool(rows)


def live_count():
    return q("SELECT count(*) FROM paste WHERE expires>?", time.time())[0][0]


def create(data, filename, mime, ttl_key, burn, ip):
    ttl = TTLS[ttl_key]
    n = 4 if ttl >= 86400 else 3
    blob = secrets.token_hex(16)
    (BLOBS / blob).write_bytes(data)
    now = time.time()
    for _ in range(50):
        phrase = "-".join(W.generate(n))
        try:
            q("INSERT INTO paste (phrase, blob, filename, mime, size, created, expires, burn, ip) "
              "VALUES (?,?,?,?,?,?,?,?,?)", phrase, blob, filename, mime, len(data), now, now + ttl, int(burn), ip)
            return phrase
        except sqlite3.IntegrityError:
            # taken: if the holder already expired, evict it and retry this same phrase
            old = q("SELECT expires FROM paste WHERE phrase=?", phrase)
            if old and old[0][0] <= now:
                delete(phrase)
    (BLOBS / blob).unlink(missing_ok=True)
    raise RuntimeError("could not find a free phrase")


def prune():
    rows = q("DELETE FROM paste WHERE expires<=? RETURNING blob", time.time())
    for (b,) in rows:
        (BLOBS / b).unlink(missing_ok=True)
    known = {b for (b,) in q("SELECT blob FROM paste")}
    for f in BLOBS.iterdir():  # orphans from crashes
        if f.name not in known and time.time() - f.stat().st_mtime > 300:
            f.unlink(missing_ok=True)


def ip_bytes(ip):
    return q("SELECT coalesce(sum(size),0) FROM paste WHERE ip=? AND expires>?", ip, time.time())[0][0]


def total_bytes():
    return q("SELECT coalesce(sum(size),0) FROM paste")[0][0]


def pruner():
    while True:
        try:
            prune()
        except Exception as e:
            print("prune failed:", e, flush=True)
        time.sleep(30)

# ---------------------------------------------------------------- rate limiting

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def _bucket(kind, ip, window):
    d = _hits[(kind, ip)]
    now = time.time()
    while d and d[0] < now - window:
        d.popleft()
    return d


def limited(kind, ip):
    limit, window = (MISS_LIMIT, MISS_WINDOW) if kind == "miss" else (NEW_LIMIT, NEW_WINDOW)
    with _hits_lock:
        return len(_bucket(kind, ip, window)) >= limit


def hit(kind, ip):
    window = MISS_WINDOW if kind == "miss" else NEW_WINDOW
    with _hits_lock:
        _bucket(kind, ip, window).append(time.time())


def years_to_brute_force():
    """How long one IP at our rate limit needs to stumble on *any* live paste."""
    space = len(W.WORDS) ** 3
    per_year = MISS_LIMIT * (365 * 86400 / MISS_WINDOW)
    return space / (max(1, live_count()) * per_year)

# ---------------------------------------------------------------- html

E = html.escape


def fmt_left(expires):
    s = max(0, int(expires - time.time()))
    if s >= 3600:
        return f"{s // 3600}h {s % 3600 // 60}m"
    if s >= 60:
        return f"{s // 60}m {s % 60}s"
    return f"{s}s"


def fmt_dur(s):
    s = round(s)
    return next((f"{s // n} {u}{'s' if s // n != 1 else ''}" for n, u in ((86400, "day"), (3600, "hour"), (60, "minute")) if s >= n), f"{s} seconds")


def fmt_size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def page(title, body, status=200):
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{E(title)}</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%90%B4%3C/text%3E%3C/svg%3E">
<link rel="stylesheet" href="/static/style.css">
<script src="/static/app.js" defer></script>
</head><body>
<header><a href="/" class="logo">🐴 HorseBin</a><a class="tag" href="https://xkcd.com/936/">correct horse battery <s>staple</s> paste</a></header>
<main>{body}</main>
<footer>Solving <a href="https://xkcd.com/">xkcd</a> one comic at a time · <a href="https://xkcd.com/936/">#936</a> ✓ · <a href="/about">about</a> · <a href="/terms">terms</a></footer>
</body></html>"""
    return status, "text/html; charset=utf-8", doc.encode()


def words_html(words, big=True):
    chips = "".join(f'<span class="w"><b>{E(w[:3])}</b>{E(w[3:])}</span>' for w in words)
    return f'<div class="words{" big" if big else ""}">{chips}</div>'


def home():
    ttl_opts = "".join(
        f'<option value="{k}"{" selected" if k == DEFAULT_TTL else ""}>{TTL_LABEL[k]}{" (4 words)" if TTLS[k] >= 86400 else ""}</option>'
        for k in TTLS)
    return page("HorseBin", f"""
<section class="how">
  <h1>Move stuff to another computer by remembering 3 words.</h1>
  <ol class="steps">
    <li><span class="n">1</span><span><b>Paste</b> text or drop a file below</span></li>
    <li><span class="n">2</span><span>You get <b>3 words</b>, like <code>fruit ladder oyster</code></span></li>
    <li><span class="n">3</span><span>On the other computer, open <b>{SITE}</b> and <b>type them</b></span></li>
  </ol>
</section>
<div class="duo">
<section class="card send">
  <h2>📤 Send</h2>
  <form action="/new" method="post" enctype="multipart/form-data" id="newform">
    <textarea name="text" id="text" placeholder="Paste text here — or drop a file / Ctrl+V a screenshot anywhere on the page"></textarea>
    <div class="row">
      <button class="primary">Get my 3 words</button>
      <label class="file"><input type="file" name="file" id="file"><span id="filename">📎 attach a file</span></label>
    </div>
    <details class="opts"><summary>options</summary>
      <div class="row">
        <label>gone after <select name="ttl">{ttl_opts}</select></label>
        <label class="chk"><input type="checkbox" name="burn" value="1"> burn after reading</label>
      </div>
    </details>
  </form>
</section>
<section class="card recv">
  <h2>📥 Receive</h2>
  <form action="/go" method="get" class="go" autocomplete="off">
    <input name="q" id="q" placeholder="type the 3 words" spellcheck="false" autocapitalize="off">
    <button>Open</button>
  </form>
  <div id="qhint" class="hint">Just the first 3 letters of each word work too: <code>fru lad oys</code></div>
</section>
</div>
<p class="muted center why">Why words? <a href="https://xkcd.com/936/" title="xkcd #936 “Password Strength”: humans remember words, not xK9fQ2rT">xkcd #936</a> ·
Terminal: <code>curl -T file.txt {SITE}</code></p>
""")


def about():
    bits3 = W.BITS_PER_WORD * 3
    bits4 = W.BITS_PER_WORD * 4
    return page("About HorseBin", f"""
<section class="card prose">
<h2>Why</h2>
<p>You're on one computer. You need a thing on the other computer, across the room.
Pastebins give you <code>pastebin.com/xK9fQ2rT</code>, which you will forget by the time you stand up.
<a href="https://xkcd.com/936/">xkcd #936</a> pointed out in 2011 that humans remember <i>words</i>, not line noise.
So HorseBin gives you three words.</p>
<h2>How safe is it?</h2>
<p>Three words from a list of {len(W.WORDS)} is about <b>{bits3:.0f} bits</b> — terrible for a password,
fine for something that lives 15 minutes behind a rate limit of {MISS_LIMIT} wrong guesses per 10 minutes.
At that rate a guesser needs about <b>{years_to_brute_force():,.0f} years</b> to hit anything right now.
Pastes that live a day get a fourth word ({bits4:.0f} bits).</p>
<p>It is still a public pastebin: don't put secrets here you wouldn't write on a whiteboard.
Want it gone right after it's read? Tick <i>burn after reading</i>.</p>
<h2>Typing</h2>
<p>Every word is uniquely identified by its first three letters (that's the
<a href="https://www.eff.org/deeplinks/2016/07/new-wordlists-random-passphrases">EFF short wordlist #2</a>), so
<code>fru lad oys</code>, <code>Fruit.Ladder.Oyster</code>, <code>fruitladderoyster</code> and
<code>frut ladr oystr</code> all work.</p>
<h2>Terminal</h2>
<pre>curl -T notes.txt {SITE}                  # upload, prints the words
echo hello | curl --data-binary @- {SITE}
curl -T x.png "{SITE}/x.png?ttl=1h&amp;burn=1"
curl {SITE}/fruit-ladder-oyster          # download (raw)</pre>
</section>""")


def terms():
    mb = MAX_BYTES // 1024 // 1024
    return page("HorseBin terms", f"""
<section class="card prose">
<h2>Terms of use</h2>
<p>Short version: HorseBin is a free toy for moving text between your own computers. Be nice, don't be evil,
expect nothing.</p>

<h2>What it is</h2>
<ul>
  <li>Pastes are <b>public to anyone who has the words</b>. Three words is a small space (about 30 bits);
  it is protected by rate limits and short lifetimes, not by secrecy.</li>
  <li>Pastes are <b>not encrypted</b> on the server. They travel over HTTPS but are stored as-is.</li>
  <li>Burned pastes, and pastes deleted with <i>delete / report abuse</i>, are destroyed immediately.
  No backups are kept.</li>
  <li>Limits: {mb} MB per paste, plus per-IP rate and storage limits. They can change without notice.</li>
</ul>

<h2>Don't use it for</h2>
<ul>
  <li>Anything illegal where you are or where this server is.</li>
  <li>Malware, phishing, spam, credential dumps, personal data about other people, or content you don't have the
  right to share.</li>
  <li>Passwords, keys and other secrets. Seriously.</li>
  <li>Hosting files for the public, hotlinking, or as a CDN. It's a short-term clipboard, not a file host.</li>
</ul>
<p>We may delete any paste, block any IP, or shut the whole thing down, at any time, without notice or reason.</p>

<h2>Privacy</h2>
<p>We log the IP address, time, size and filename of each paste, plus standard web server access logs
(which include the words in the URL). Logs are used to run the service and fight abuse, are kept for a limited
time, and are not sold or shared except where the law requires it. No cookies, no analytics, no ads.</p>

<h2>No warranty</h2>
<p>HorseBin is provided <b>as is</b>, with no warranty of any kind. It may lose your paste, show it to someone else,
or be down when you need it. You use it at your own risk, and to the extent the law allows, its operator is not
liable for anything that results.</p>

<h2>Abuse &amp; takedown</h2>
<p>If you have the words, press <i>delete / report abuse</i> on the paste — it's gone immediately.
Otherwise write to <a href="mailto:{E(ABUSE)}">{E(ABUSE)}</a>.</p>

<p class="muted">Using HorseBin means you accept these terms. They may change; the version on this page is the one that applies.</p>
</section>""")


def view_page(p, new=False):
    words = p["phrase"].split("-")
    url = f"https://{SITE}/{p['phrase']}"
    head = ""
    if new:
        head = f"""
<section class="card made">
  <div class="muted">Your words:</div>
  {words_html(words)}
  <p class="mnemonic">{E(W.mnemonic(words))}</p>
  <div class="row center">
    <code class="url" id="url">{E(url)}</code>
    <button class="copy" data-copy="#url">copy link</button>
  </div>
  <p class="muted">{W.BITS_PER_WORD * len(words):.0f} bits of entropy. Plenty for {E(fmt_dur(p['expires'] - p['created']))}, Randall.</p>
</section>"""
    meta = (f'<div class="meta">gone in <span class="left" data-expires="{p["expires"]:.0f}">{fmt_left(p["expires"])}</span>'
            f'{" · 🔥 burns after reading" if p["burn"] else ""} · {fmt_size(p["size"])}'
            f'{" · " + E(p["filename"]) if p["filename"] else ""}</div>')
    ph = quote(p["phrase"])
    report = (f'<form method="post" action="/{ph}/delete" class="del">'
              f'<button class="link" data-confirm="Delete this paste for everyone?">delete / report abuse</button></form>')
    if p["burn"]:
        body = f"""
<section class="card">
  {"" if new else words_html(words, big=False)}
  {meta}
  <p>This paste burns after reading. Opening it deletes it.</p>
  <form method="post" action="/{ph}/open"><button class="primary">🔥 Open it</button></form>
  {report}
</section>"""
        return page(" ".join(words), head + body)
    content = ""
    blob = (BLOBS / p["blob"])
    text = _as_text(blob, p)
    if p["mime"] in INLINE_IMAGES:
        content = f'<img class="preview" src="/{ph}/raw" alt="">'
    elif text is not None:
        content = f'<div class="row right"><button class="copy" data-copy="#paste">copy text</button></div><pre id="paste">{E(text)}</pre>'
    else:
        content = '<p class="muted">Binary file, no preview.</p>'
    body = f"""
<section class="card">
  {"" if new else words_html(words, big=False)}
  {meta}
  {content}
  <div class="row"><a class="btn" href="/{ph}/dl">⬇ download</a><a class="btn" href="/{ph}/raw">raw</a>{report}</div>
</section>"""
    return page(" ".join(words), head + body)


def _as_text(blob, p):
    try:
        return _decode(blob.read_bytes(), p) if p["size"] <= PREVIEW_BYTES else None
    except FileNotFoundError:
        return None


def not_found(typed, resolved):
    hint = ""
    if resolved:
        hint = f"<p>We read that as {words_html(resolved, big=False)} but there's nothing there. Expired, burned, or misremembered.</p>"
    else:
        hint = "<p>Couldn't make three words out of that. First three letters of each word are enough.</p>"
    return page("No horse here", f"""
<section class="card center">
  <div class="huge">🐴❓</div>
  <h2>No horse here.</h2>
  {hint}
  <form action="/go" method="get" class="go" autocomplete="off">
    <input name="q" value="{E(typed)}" spellcheck="false" autocapitalize="off"><button>Try again</button>
  </form>
</section>""", 404)


def xkcd_page():
    return page("Nice try", """
<section class="card center">
  <div class="huge">🐴🔋📎</div>
  <h2>Nice try.</h2>
  <p>That one's taken. By everyone.</p>
  <p class="muted"><a href="https://xkcd.com/936/">xkcd #936</a>, 2011. It's in every cracking dictionary now.</p>
</section>""", 404)


def locked_page():
    return page("Whoa there", f"""
<section class="card center">
  <div class="huge">🐎💨</div>
  <h2>Whoa there.</h2>
  <p>You've guessed {MISS_LIMIT} times. At this rate, finding anything takes about
  <b>{years_to_brute_force():,.0f} years</b>.</p>
  <p class="muted">Try again in 10 minutes.</p>
</section>""", 429)

# ---------------------------------------------------------------- http


class Handler(BaseHTTPRequestHandler):
    server_version = "horsebin"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass  # Caddy keeps the access log

    # ---- helpers
    @property
    def ip(self):
        xff = self.headers.get("X-Forwarded-For")
        return xff.split(",")[-1].strip() if xff else self.client_address[0]

    @property
    def cli(self):
        return bool(CLI_UA.match(self.headers.get("User-Agent", "")))

    def send(self, status, ctype, body, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", PAGE_CSP)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def text(self, status, s):
        self.send(status, "text/plain; charset=utf-8", s.encode())

    def redirect(self, loc, status=303):
        self.send(status, "text/plain; charset=utf-8", b"", {"Location": loc})

    def read_body(self):
        te = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in te:
            out = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return bytes(out)
                if len(out) + size > MAX_BYTES + 65536:
                    raise ValueError("too big")
                out += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BYTES + 65536:
            raise ValueError("too big")
        return self.rfile.read(n)

    # ---- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlsplit(self.path)
        path, qs = u.path, parse_qs(u.query, keep_blank_values=True)
        if path == "/":
            if self.cli:
                return self.text(200, usage())
            return self.send_page(home())
        if path == "/about":
            return self.send_page(about())
        if path == "/terms":
            return self.send_page(terms())
        if path.startswith("/static/"):
            return self.static(path[8:])
        if path == "/go":
            typed = (qs.get("q") or [""])[0]
            return self.lookup(typed, "")
        if path in ("/robots.txt",):
            return self.text(200, "User-agent: *\nDisallow: /\n")
        if path == "/favicon.ico":
            return self.send(204, "image/x-icon", b"")
        m = re.fullmatch(r"/([^/]+)(/raw|/dl)?/?", path)
        if m:
            return self.lookup(m.group(1), m.group(2) or "", new="new" in qs)
        self.send_page(not_found(path, None))

    def do_POST(self):
        u = urlsplit(self.path)
        path, qs = u.path, parse_qs(u.query, keep_blank_values=True)
        if path in ("/", "/new"):
            return self.new(qs, from_form=(path == "/new"))
        m = re.fullmatch(r"/([a-z-]+)/(open|delete)", path)
        if m:
            words = W.resolve(m.group(1))
            phrase = "-".join(words) if words else ""
            p = get(phrase) if phrase else None
            if not p:
                return self.send_page(not_found(m.group(1), words))
            if m.group(2) == "delete":
                delete(phrase)
                print(f"deleted {phrase} by {self.ip}", flush=True)
                return self.send_page(page("Deleted", '<section class="card center"><div class="huge">🐴🧹</div><h2>Gone.</h2><p><a href="/">make another</a></p></section>'))
            return self.serve(p, "", burn_now=True)
        self.text(404, "nope\n")

    def do_PUT(self):
        u = urlsplit(self.path)
        name = os.path.basename(u.path.strip("/")) or None
        return self.new(parse_qs(u.query), filename=name)

    def send_page(self, t):
        self.send(*t)

    def static(self, name):
        if name not in ("app.js", "style.css", "words.txt"):
            return self.text(404, "nope\n")
        f = HERE / ("words.txt" if name == "words.txt" else f"static/{name}")
        ctype = {"js": "text/javascript", "css": "text/css", "txt": "text/plain"}[name.rsplit(".", 1)[1]]
        self.send(200, ctype + "; charset=utf-8", f.read_bytes(), {"Cache-Control": "public, max-age=300"})

    # ---- read
    def lookup(self, typed, suffix, new=False):
        ip = self.ip
        if W.is_xkcd(typed):
            return self.send_page(xkcd_page()) if not self.cli else self.text(404, "Nice try. That one's taken. By everyone.\n")
        if limited("miss", ip):
            return self.send_page(locked_page()) if not self.cli else self.text(429, f"Whoa there. Try again in 10 minutes.\n")
        words = W.resolve(typed)
        phrase = "-".join(words) if words else None
        p = get(phrase) if phrase else None
        if not p:
            hit("miss", ip)
            if self.cli:
                return self.text(404, "No horse here.\n")
            return self.send_page(not_found(typed, words))
        canonical = f"/{phrase}{suffix}"
        if urlsplit(self.path).path.rstrip("/") != canonical:
            return self.redirect(canonical + ("?new" if new else ""), 302)
        if suffix or self.cli:
            if self.command == "HEAD" and p["burn"]:
                return self.send(200, "application/octet-stream", b"")  # don't burn on a peek
            return self.serve(p, suffix or "/raw", burn_now=bool(p["burn"]))
        self.send_page(view_page(p, new=new))

    def serve(self, p, suffix, burn_now=False):
        blob = BLOBS / p["blob"]
        try:
            data = blob.read_bytes()
        except FileNotFoundError:
            return self.send_page(not_found(p["phrase"], None))
        q("UPDATE paste SET views=views+1 WHERE phrase=?", p["phrase"])
        if burn_now:
            delete(p["phrase"])
        if burn_now and suffix == "" and not self.cli:
            # browser "open" of a burn paste: show it once on a page
            text = _decode(data, p)
            if text is not None:
                words = p["phrase"].split("-")
                return self.send_page(page(" ".join(words), f"""
<section class="card">
  {words_html(words, big=False)}
  <div class="meta">🔥 burned. This is the only time you'll see it.</div>
  <div class="row right"><button class="copy" data-copy="#paste">copy text</button></div>
  <pre id="paste">{E(text)}</pre>
</section>"""))
            suffix = "/dl"
        name = p["filename"] or ("paste.txt" if _decode(data, p) is not None else "paste.bin")
        mime = p["mime"] or ("text/plain; charset=utf-8" if name == "paste.txt" else "application/octet-stream")
        hdr = {"Content-Security-Policy": RAW_CSP}
        if suffix == "/dl":
            hdr["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
            ctype = "application/octet-stream"
        elif mime in INLINE_IMAGES:
            ctype = mime
        elif _decode(data, p) is not None:
            ctype = "text/plain; charset=utf-8"
        else:
            hdr["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
            ctype = "application/octet-stream"
        self.send(200, ctype, data, hdr)

    # ---- write
    def new(self, qs, from_form=False, filename=None):
        ip = self.ip
        if limited("new", ip):
            return self.reply_err(429, "Slow down: too many pastes from you. Try again in a few minutes.", from_form)
        try:
            body = self.read_body()
        except ValueError:
            return self.reply_err(413, f"Too big. Max {MAX_BYTES // 1024 // 1024} MB.", from_form)
        ctype = self.headers.get("Content-Type", "")
        ttl = (qs.get("ttl") or [DEFAULT_TTL])[0]
        burn = (qs.get("burn") or [""])[0] in ("1", "true", "yes", "on")
        mime = None
        if ctype.startswith("multipart/form-data"):
            fields = parse_multipart(ctype, body)
            ttl = fields.get("ttl", (None, ttl.encode()))[1].decode(errors="replace") or ttl
            burn = burn or fields.get("burn", (None, b""))[1] in (b"1", b"on", b"true")
            fname, data = None, b""
            for key, (fn, val) in fields.items():
                if fn is not None and val:  # first non-empty file field wins (curl -F f=@x, or the form's file)
                    fname, data = fn, val
                    break
            if not data:
                data = fields.get("text", (None, b""))[1]
                if not data:  # curl -F 'x=some text'
                    data = next((v for k, (fn, v) in fields.items() if k not in ("ttl", "burn") and v), b"")
            filename = os.path.basename(fname) if fname else None
        else:
            data = body
            if from_form or ctype.startswith("application/x-www-form-urlencoded"):
                f = parse_qs(body.decode(errors="replace"), keep_blank_values=True)
                if "text" in f:  # a real urlencoded form; curl --data-binary also claims this type
                    data = f["text"][0].encode()
                    ttl = (f.get("ttl") or [ttl])[0]
                    burn = burn or (f.get("burn") or [""])[0] in ("1", "on")
        data = data.replace(b"\r\n", b"\n") if filename is None and _utf8(data) else data
        if not data.strip():
            return self.reply_err(400, "Nothing to paste. It's empty.", from_form)
        if len(data) > MAX_BYTES:
            return self.reply_err(413, f"Too big. Max {MAX_BYTES // 1024 // 1024} MB.", from_form)
        if ip_bytes(ip) + len(data) > MAX_PER_IP:
            return self.reply_err(429, f"Your stall is full: you already have {fmt_size(ip_bytes(ip))} of live pastes "
                                       f"(max {MAX_PER_IP // 1024 // 1024} MB). Wait for some to expire.", from_form)
        if total_bytes() + len(data) > MAX_TOTAL:
            return self.reply_err(507, "The stable is full. Try again later.", from_form)
        if ttl not in TTLS:
            ttl = DEFAULT_TTL
        if filename:
            filename = re.sub(r"[\x00-\x1f/\\]", "_", filename)[:200]
            mime = mimetypes.guess_type(filename)[0]
        hit("new", ip)
        phrase = create(data, filename, mime, ttl, burn, ip)
        print(f"new {phrase} {len(data)}B ttl={ttl} burn={int(burn)} file={filename!r} ip={ip}", flush=True)
        words = phrase.split("-")
        if self.cli or not from_form:
            return self.text(200,
                             f"https://{SITE}/{phrase}\n\n   {'  '.join(w.upper() for w in words)}\n\n"
                             f"   {W.mnemonic(words)}\n   gone in {TTL_LABEL[ttl]}{', burns after reading' if burn else ''}.\n")
        self.redirect(f"/{phrase}?new")

    def reply_err(self, status, msg, from_form):
        if from_form and not self.cli:
            return self.send_page(page("Hmm", f'<section class="card center"><div class="huge">🐴</div><h2>{E(msg)}</h2><p><a href="/">back</a></p></section>', status))
        self.text(status, msg + "\n")


def _utf8(b):
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _decode(data, p):
    if len(data) > PREVIEW_BYTES:
        return None
    if p["mime"] and p["filename"] and not (p["mime"].startswith("text/") or p["mime"] in ("application/json", "application/xml", "application/javascript")):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def parse_multipart(ctype, body):
    """-> {field: (filename or None, bytes)}"""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    out = {}
    if not msg.is_multipart():
        return out
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name or name in out:
            continue
        out[name] = (part.get_filename(), part.get_payload(decode=True) or b"")
    return out


def usage():
    return f"""HorseBin — pastes with URLs you can remember. https://xkcd.com/936/

  curl -T notes.txt {SITE}                   upload a file
  echo hello | curl --data-binary @- {SITE}  upload text
  curl -T x.png "{SITE}/x.png?ttl=1h&burn=1" ttl: 5m 15m 1h 1d (1d = 4 words); burn after reading
  curl {SITE}/fruit-ladder-oyster           download (first 3 letters of each word are enough)
"""


if __name__ == "__main__":
    threading.Thread(target=pruner, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"horsebin on {HOST}:{PORT}, data {DATA}", flush=True)
    srv.serve_forever()
