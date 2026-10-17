import { CircleMarker, MapContainer, Popup, TileLayer } from "react-leaflet";
import type { Visualization } from "../schema";

export function WellMap({ spec }: { spec: Visualization }) {
  if (!spec.markers.length) {
    return (
      <p className="muted text-sm">
        {spec.empty_reason || "Location data is unavailable. Coordinates are never inferred from well names."}
      </p>
    );
  }
  const center: [number, number] = [spec.markers[0].lat, spec.markers[0].lon];
  return (
    <MapContainer center={center} zoom={8} scrollWheelZoom={false}>
      <TileLayer attribution="&copy; OpenStreetMap" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      {spec.markers.map((marker) => (
        <CircleMarker
          key={`${marker.record_id}-${marker.lat}-${marker.lon}`}
          center={[marker.lat, marker.lon]}
          radius={8}
          pathOptions={{ color: "#0f766e", fillColor: "#0d9488", fillOpacity: 0.85 }}
        >
          <Popup>
            <div className="text-sm">
              <div className="font-medium">{marker.label}</div>
              {marker.record_id && <div>{marker.record_id}</div>}
              {marker.detail && <div className="text-slate-600">{marker.detail}</div>}
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
