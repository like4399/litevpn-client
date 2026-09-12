# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/* \
  && useradd --system --home-dir /config --uid 1000 litevpn

COPY docker/mihomo /usr/local/bin/mihomo
COPY docker/entrypoint.sh /entrypoint.sh
COPY pack /usr/share/litevpn/pack
COPY docker/ui /usr/share/litevpn/ui

RUN chmod 755 /usr/local/bin/mihomo /entrypoint.sh \
  && mkdir -p /config/litevpn \
  && chown -R litevpn:litevpn /config

ENV HOME=/config \
    XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/usr/share \
    LITEVPN_BIN_DIR=/usr/local/bin \
    LITEVPN_UI_DIR=/usr/share/litevpn/ui \
    LITEVPN_LISTEN_HOST=0.0.0.0 \
    LITEVPN_BIND_ADDRESS=0.0.0.0 \
    SAFE_PATHS=/usr/share/litevpn:/config \
    PYTHONUNBUFFERED=1

USER litevpn
EXPOSE 9090 7890
ENTRYPOINT ["/entrypoint.sh"]
