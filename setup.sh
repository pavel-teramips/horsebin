#!/bin/sh
# Installs HorseBin on big-ku: system unit running ~/dev/horsebin/server.py on 127.0.0.1:3050
# + public Caddy vhost horse.teramips.com (wildcard DNS already covers it).
# Run: sudo sh ~/dev/horsebin/setup.sh
set -eu

install -d -o pavelr -g pavelr -m 0700 /home/pavelr/horsebin

cat > /etc/systemd/system/horsebin.service <<'EOF'
[Unit]
Description=HorseBin: pastebin with 3-word URLs (horse.teramips.com) — xkcd 936
After=network-online.target

[Service]
User=pavelr
WorkingDirectory=/home/pavelr/dev/horsebin
ExecStart=/usr/bin/python3 /home/pavelr/dev/horsebin/server.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/pavelr/horsebin
PrivateTmp=yes
Environment=HORSEBIN_DIR=/home/pavelr/horsebin
Environment=HORSEBIN_PORT=3050
Environment=HORSEBIN_SITE=horse.teramips.com
Environment=HORSEBIN_MAX_MB=25
Environment=HORSEBIN_MAX_TOTAL_MB=2048
Environment=HORSEBIN_MAX_PER_IP_MB=200

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/caddy/sites-enabled/horsebin.caddy <<'EOF'
# horse.teramips.com — HorseBin, public pastebin with 3-word URLs (xkcd 936). Deliberately no auth.
# ~/dev/horsebin/server.py as horsebin.service on 127.0.0.1:3050. Setup: sudo sh ~/dev/horsebin/setup.sh
horse.teramips.com {
    encode gzip zstd
    request_body {
        max_size 27MB
    }
    reverse_proxy 127.0.0.1:3050

    header X-Robots-Tag "noindex, nofollow"

    log {
        output file /var/log/caddy/horsebin.log {
            roll_size 10mb
            roll_keep 5
        }
    }
}
EOF

systemctl daemon-reload
systemctl enable --now horsebin.service
systemctl restart horsebin.service
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
sleep 2
curl -s -o /dev/null -w 'horsebin local: %{http_code}\n' http://127.0.0.1:3050/
curl -s -o /dev/null -w 'horsebin public: %{http_code}\n' https://horse.teramips.com/ || echo "public check failed (cert may still be issuing; retry in a minute)"
