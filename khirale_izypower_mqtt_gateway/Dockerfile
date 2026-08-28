FROM ghcr.io/home-assistant/base:3.24

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="addon" \
  io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3 py3-paho-mqtt

COPY run.sh /run.sh
COPY decoder.py /decoder.py
RUN chmod 755 /run.sh

CMD ["/run.sh"]

