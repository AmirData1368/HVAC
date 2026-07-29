from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

OUT = Path("site_selection/geometry")
OUT.mkdir(parents=True, exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
})

LAYERS = {
    "nsw_biomass_merged_sa2_regions": (
        "https://spatial.industry.nsw.gov.au/arcgis/rest/services/"
        "Bioenergy_Assessment/BiomassTool_Cropping/MapServer/0"
    ),
    "nsw_forestry_management_areas": (
        "https://spatial.industry.nsw.gov.au/arcgis/rest/services/"
        "Bioenergy_Assessment/BiomassTool_Forestry/MapServer/0"
    ),
}


def call(url: str, params: dict[str, Any], attempts: int = 8) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(attempts):
        for method in ("post", "get"):
            try:
                kwargs = {
                    "timeout": 240,
                    "headers": {"Referer": url.rsplit("/query", 1)[0]},
                }
                response = (
                    SESSION.post(url, data=params, **kwargs)
                    if method == "post" else SESSION.get(url, params=params, **kwargs)
                )
                response.raise_for_status()
                text = response.text.lstrip()
                if not text.startswith("{"):
                    raise RuntimeError(f"non-JSON response: {response.status_code} {text[:150]!r}")
                obj = response.json()
                if obj.get("error"):
                    raise RuntimeError(f"ArcGIS error: {obj['error']}")
                return obj
            except Exception as exc:
                last = exc
        time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"request failed after {attempts} attempts: {last!r}")


def object_ids(layer_url: str) -> list[int]:
    query_url = layer_url.rstrip("/") + "/query"
    try:
        obj = call(query_url, {"where": "1=1", "returnIdsOnly": "true", "f": "json"})
        ids = obj.get("objectIds") or []
        if ids:
            return sorted(int(x) for x in ids)
    except Exception:
        pass
    ids, offset = [], 0
    while True:
        obj = call(query_url, {
            "where": "1=1", "outFields": "OBJECTID", "returnGeometry": "false",
            "orderByFields": "OBJECTID", "resultOffset": offset,
            "resultRecordCount": 200, "f": "json",
        })
        rows = obj.get("features") or []
        if not rows:
            break
        ids.extend(int(x["attributes"]["OBJECTID"]) for x in rows)
        if len(rows) < 200:
            break
        offset += len(rows)
    if not ids:
        raise RuntimeError(f"could not discover object IDs for {layer_url}")
    return sorted(set(ids))


def fetch_features(name: str, layer_url: str, ids: list[int]) -> list[dict[str, Any]]:
    query_url = layer_url.rstrip("/") + "/query"
    output: list[dict[str, Any]] = []
    for start in range(0, len(ids), 5):
        batch = ids[start:start + 5]
        print(f"{name}: requesting OBJECTID values {batch}", flush=True)
        params = {
            "objectIds": ",".join(map(str, batch)),
            "outFields": "OBJECTID,REGION_NAME,REGION_TYPE",
            "returnGeometry": "true",
            "outSR": "4326",
            "maxAllowableOffset": "0.0005",
            "geometryPrecision": "5",
            "f": "geojson",
        }
        try:
            obj = call(query_url, params)
            features = obj.get("features") or []
            if len(features) != len(batch):
                raise RuntimeError(f"batch returned {len(features)} of {len(batch)} features")
            output.extend(features)
        except Exception as batch_error:
            print(f"{name}: batch failed ({batch_error!r}); retrying individually", flush=True)
            for oid in batch:
                params["objectIds"] = str(oid)
                try:
                    obj = call(query_url, params)
                except Exception as exc:
                    raise RuntimeError(f"{name}: geometry query failed for OBJECTID {oid}: {exc!r}") from exc
                features = obj.get("features") or []
                if len(features) != 1:
                    raise RuntimeError(f"{name}: OBJECTID {oid} returned {len(features)} features")
                output.extend(features)
        time.sleep(0.15)
    return output


def validate_feature_collection(name: str, fc: dict[str, Any], expected_ids: list[int]) -> dict[str, Any]:
    features = fc.get("features") or []
    ids, names, invalid = [], [], []
    for feature in features:
        props = feature.get("properties") or {}
        geom = feature.get("geometry")
        ids.append(int(props.get("OBJECTID")))
        names.append(str(props.get("REGION_NAME") or "").strip())
        if not geom or geom.get("type") not in {"Polygon", "MultiPolygon"}:
            invalid.append(props.get("OBJECTID"))
    if sorted(ids) != sorted(expected_ids):
        raise RuntimeError(f"{name}: object-ID mismatch")
    if any(not x for x in names):
        raise RuntimeError(f"{name}: blank REGION_NAME values")
    if invalid:
        raise RuntimeError(f"{name}: invalid geometries for OBJECTID values {invalid[:10]}")
    return {
        "name": name, "feature_count": len(features), "unique_region_names": len(set(names)),
        "minimum_object_id": min(ids), "maximum_object_id": max(ids), "source": LAYERS[name],
        "geometry_format": "GeoJSON EPSG:4326 simplified to 0.0005 degrees",
    }


def main() -> None:
    reports = []
    for name, layer_url in LAYERS.items():
        ids = object_ids(layer_url)
        print(f"{name}: discovered {len(ids)} object IDs", flush=True)
        features = fetch_features(name, layer_url, ids)
        fc = {
            "type": "FeatureCollection", "name": name,
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": features,
        }
        reports.append(validate_feature_collection(name, fc, ids))
        (OUT / f"{name}.geojson").write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    report = {
        "status": "PASS", "layers": reports,
        "scientific_use": (
            "Merged SA2 geometry supports area-weighted 25/50/100 km biomass catchments. "
            "The area-weighting assumes uniform distribution inside each published source region and must be sensitivity-tested."
        ),
    }
    (OUT / "geometry_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
