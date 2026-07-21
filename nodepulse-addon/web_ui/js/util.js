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

/**
 * Build a KML string from an array of nodes with GPS fixes.
 * Each node becomes a Placemark with name, description, and coordinates.
 */
export function buildKml(nodes, selfId) {
  const now = new Date().toISOString();
  let kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>NodePulse Mesh Nodes</name>
  <description>Exported ${now} — ${nodes.length} nodes with GPS fix</description>
`;
  for (const n of nodes) {
    if (n.latitude == null || n.longitude == null) continue;
    const name = escapeHtml(n.long_name || n.short_name || n.id);
    const desc = `ID: ${n.id}\\nSNR: ${n.snr ?? 'N/A'} dB\\nHops: ${n.hops_away ?? 'N/A'}\\nLast heard: ${new Date((n.last_heard || 0) * 1000).toISOString()}`;
    const style = n.id === selfId ? '#selfStyle' : '#nodeStyle';
    kml += `  <Placemark>
    <name>${name}</name>
    <description>${desc}</description>
    <styleUrl>${style}</styleUrl>
    <Point><coordinates>${n.longitude},${n.latitude},0</coordinates></Point>
  </Placemark>
`;
  }
  kml += `  <Style id="nodeStyle">
    <IconStyle><scale>0.8</scale></IconStyle>
  </Style>
  <Style id="selfStyle">
    <IconStyle><scale>1.2</scale></IconStyle>
  </Style>
</Document>
</kml>`;
  return kml;
}

/**
 * Build a GPX string from an array of nodes with GPS fixes.
 * Each node is a waypoint (wpt) with name, cmt, and desc.
 */
export function buildGpx(nodes, selfId) {
  const now = new Date().toISOString();
  let gpx = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="NodePulse" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata>
    <name>NodePulse Mesh Nodes</name>
    <time>${now}</time>
  </metadata>
`;
  for (const n of nodes) {
    if (n.latitude == null || n.longitude == null) continue;
    const name = escapeHtml(n.long_name || n.short_name || n.id);
    const desc = `ID: ${escapeHtml(n.id)} SNR: ${n.snr ?? 'N/A'} dB Hops: ${n.hops_away ?? 'N/A'}`;
    const time = n.last_heard ? new Date(n.last_heard * 1000).toISOString() : now;
    gpx += `  <wpt lat="${n.latitude}" lon="${n.longitude}">
    <name>${name}</name>
    <cmt>${desc}</cmt>
    <time>${time}</time>
  </wpt>
`;
  }
  gpx += '</gpx>';
  return gpx;
}

/** Trigger a file download in the browser. */
export function downloadFile(content, filename, mimeType = 'application/xml') {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
