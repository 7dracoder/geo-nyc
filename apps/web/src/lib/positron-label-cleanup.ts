import type { Map as MaplibreMap } from "maplibre-gl";

/**
 * Hides Carto Positron symbol layers that show regional / water / settlement names.
 * Those labels sit under the five-borough mask and read as broken (“New York”, bays, etc.).
 * Road names (`roadname_*`) and `housenumber` stay visible. POI symbols are hidden
 * so park/stadium captions are not clipped at the mask edge.
 */
export function hidePositronPlaceAndWaterLabels(map: MaplibreMap): void {
  if (!map.isStyleLoaded()) return;
  const style = map.getStyle();
  if (!style?.layers) return;
  for (const layer of style.layers) {
    if (layer.type !== "symbol") continue;
    const { id } = layer;
    if (
      id.startsWith("watername_") ||
      id === "waterway_label" ||
      id.startsWith("place_") ||
      id.startsWith("poi_")
    ) {
      if (!map.getLayer(id)) continue;
      try {
        map.setLayoutProperty(id, "visibility", "none");
      } catch {
        /* layer may not support layout visibility */
      }
    }
  }
}
