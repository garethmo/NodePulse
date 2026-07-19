/**
 * NodePulse Web UI — Shared utility helpers.
 *
 * `app.js` and `map.js` both need HTML-escaping and haversine distance
 * formatting. These were previously copy-pasted into each module; this shared
 * module is the single source of truth so the two never drift apart.
 */

/** Escape HTML special chars to prevent XSS in rendered / popup content. */
export function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Great-circle distance (km) between two lat/lon points (haversine). */
export function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/** Human-friendly distance string from a kilometres value. */
export function formatDistance(km) {
  if (km == null || Number.isNaN(km)) return 'N/A';
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(2)} km`;
}
