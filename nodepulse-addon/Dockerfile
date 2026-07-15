ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install system-level packages needed for cryptography pure-Python wheels
# and for the meshtastic library's serial/bluetooth optional deps (optional here).
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev

# Copy the requirements file first to leverage Docker's layer cache.
# If requirements.txt hasn't changed, this layer won't be rebuilt.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy the entire addon source into the container
WORKDIR /app
COPY . /app/

# The main entry point for the addon
CMD ["python", "-m", "app.main"]
