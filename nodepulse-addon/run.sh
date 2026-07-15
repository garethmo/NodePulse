#!/usr/bin/with-contenv bashio
# ==============================================================================
# Home Assistant Add-on: NodePulse
# ==============================================================================
#
# The shebang MUST be `#!/usr/bin/with-contenv bashio` (NOT `#!/command/with-contenv
# bashio`). In current s6-overlay v3 base images `/command/with-contenv` is a
# symlink to `s6-overlay-suexec`, which aborts with:
#   "s6-overlay-suexec: fatal: can only run as pid 1"
# `/usr/bin/with-contenv` is the correct helper and loads the bashio functions.

bashio::log.info "Starting NodePulse..."
cd /app
exec python3 -m app.main
