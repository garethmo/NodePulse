ARG BUILD_ARCH=amd64
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base-python:3.12-alpine3.19

# Build toolchain (gcc + headers) is ONLY required when pip must compile a
# package from source. On amd64/aarch64 all of our deps (cryptography,
# protobuf, etc.) ship prebuilt musllinux wheels, so installing gcc there is
# pure waste. Only the 32-bit / x86-32 arches can lack wheels and need it.
# Skipping it on amd64 noticeably speeds up every add-on build.
RUN case "${BUILD_ARCH}" in \
      armhf|armv7|i386) apk add --no-cache gcc musl-dev libffi-dev openssl-dev ;; \
      *) echo "Build toolchain not required for ${BUILD_ARCH} (wheels available)" ;; \
    esac

# Copy the requirements file first to leverage Docker's layer cache.
# If requirements.txt hasn't changed, this layer won't be rebuilt.
COPY requirements.txt /tmp/requirements.txt
# --prefer-binary guarantees we never fall back to a slow source build.
RUN pip install --no-cache-dir --prefer-binary -r /tmp/requirements.txt

# Enable detailed logging for s6-overlay and bashio to debug startup issues
ENV S6_VERBOSITY=3
ENV BASHIO_LOG_LEVEL=debug

# Copy the entire addon source into the container
WORKDIR /app
COPY . /app

# Run the addon as the container's main process (CMD). Combined with
# `init: false` in config.json this is the canonical Home Assistant add-on
# pattern: s6-overlay supervises /run.sh directly. Do NOT hand-roll an
# s6-rc.d service here — that collides with HA's own service wiring.
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
