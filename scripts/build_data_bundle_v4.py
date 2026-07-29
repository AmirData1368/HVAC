from __future__ import annotations

import json
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import requests

import build_data_bundle_v2 as b


def measured_load_archives_v4() -> None:
    """Download Ausgrid's real 15-minute NSW zone-substation archives.

    Ausgrid's current download links are opaque Sitecore Content Hub URLs, so the year
    must be read from the visible link label rather than from the URL or file suffix.
    """
    page_url = 'https://www.ausgrid.com.au/about-us/about-ausgrid/research-data-sets/distribution-zone-substation-data'
    out_dir = b.RAW / 'ausgrid_zone_substation_load'
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory: list[dict[str, Any]] = []
    selected: dict[int, str] = {}
    try:
        for url, label in b.scrape_links(page_url, [r'distribution zone substation data']):
            m = re.search(r'\b(20(?:1[5-9]|2[0-5]))\b', label)
            if m and ('sitecorecontenthub.cloud' in url or '/content/' in url):
                selected[int(m.group(1))] = url
    except Exception as exc:
        b.records.append(b.Record('Ausgrid zone-substation load', page_url, str(out_dir), 'failed', note=repr(exc)))

    successes = 0
    csv_count = 0
    for year, url in sorted(selected.items()):
        out = out_dir / f'ausgrid_distribution_zone_substation_{year}.zip'
        ok = b.download(url, out, 'Ausgrid zone-substation load', min_bytes=5_000_000,
                        referer=page_url, validate_zip=True)
        if not ok:
            continue
        successes += 1
        extract_dir = out_dir / 'extracted' / str(year)
        with zipfile.ZipFile(out) as arc:
            arc.extractall(extract_dir)
            for member in arc.namelist():
                p = extract_dir / member
                inventory.append({'year': year, 'archive': out.name, 'member': member,
                                  'bytes': p.stat().st_size if p.is_file() else None})
                if p.is_file() and p.suffix.lower() == '.csv' and p.stat().st_size > 100:
                    csv_count += 1
    pd.DataFrame(inventory).to_csv(b.META / 'ausgrid_archive_inventory.csv', index=False)
    b.checks.append({'check': 'measured_nsw_load_archives',
                     'passed': successes >= 8 and csv_count >= 800,
                     'value': successes,
                     'detail': f'{csv_count} real zone-substation CSV files; {len(selected)} official annual links discovered'})

    manual_dir = b.RAW / 'essential_energy_optional'
    manual_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for y in range(2012, 2025):
        fy = f'{y}-{str(y + 1)[-2:]}'
        rows.append({'financial_year': fy,
                     'official_page': 'https://www.essentialenergy.com.au/our-network/network-projects/zone-substation-reports',
                     'note': 'Optional regional validation source; cloud CDN blocks automated runners.'})
    p = manual_dir / 'essential_energy_optional_register.csv'
    pd.DataFrame(rows).to_csv(p, index=False)
    b.record_file('Essential Energy optional regional validation register', rows[0]['official_page'], p,
                  'derived', n_records=len(rows))


def post_json(url: str, data: dict[str, Any], attempts: int = 4) -> tuple[dict[str, Any], str]:
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = b.SESSION.post(url, data=data, timeout=b.TIMEOUT,
                               headers={'Accept': 'application/json,text/plain,*/*',
                                        'Referer': url.rsplit('/query', 1)[0]})
            r.raise_for_status()
            try:
                obj = r.json()
            except Exception as exc:
                raise RuntimeError(f'non-JSON response {r.status_code} {r.headers.get("content-type")}: {r.text[:300]!r}') from exc
            if isinstance(obj, dict) and obj.get('error'):
                raise RuntimeError(f'ArcGIS error: {obj["error"]}')
            return obj, r.url
        except Exception as exc:
            last = exc
            time.sleep(2 ** i)
    raise RuntimeError(f'POST query failed after {attempts} attempts: {last!r}')


def esri_geometry_to_geojson(geom: dict[str, Any] | None) -> dict[str, Any] | None:
    if not geom:
        return None
    if 'x' in geom and 'y' in geom:
        return {'type': 'Point', 'coordinates': [geom['x'], geom['y']]}
    if 'rings' in geom:
        rings = geom['rings']
        return {'type': 'Polygon', 'coordinates': rings}
    if 'paths' in geom:
        paths = geom['paths']
        return {'type': 'LineString' if len(paths) == 1 else 'MultiLineString',
                'coordinates': paths[0] if len(paths) == 1 else paths}
    if 'points' in geom:
        return {'type': 'MultiPoint', 'coordinates': geom['points']}
    return None


def convert_features(obj: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for feat in obj.get('features') or []:
        result.append({'type': 'Feature', 'properties': feat.get('attributes', {}),
                       'geometry': esri_geometry_to_geojson(feat.get('geometry'))})
    return result


def query_layer_post(service_url: str, layer_id: int, dataset: str, out_dir: Path) -> tuple[int, Path]:
    layer_url = f'{service_url.rstrip("/")}/{layer_id}'
    meta, meta_url = b.request_json(layer_url, {'f': 'pjson'})
    if meta.get('type') != 'Feature Layer':
        raise RuntimeError(f'Layer {layer_id} is not a Feature Layer: {meta.get("type")}')
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', str(meta.get('name', f'layer_{layer_id}')))[:100]
    mp = out_dir / f'{layer_id:02d}_{name}_metadata.json'
    b.save_json(meta, mp)
    b.record_file(dataset, meta_url, mp)
    qurl = layer_url + '/query'
    count_obj, _ = post_json(qurl, {'where': '1=1', 'returnCountOnly': 'true', 'f': 'json'})
    expected = int(count_obj.get('count', 0))
    oid = (meta.get('objectIdField') or meta.get('objectIdFieldName') or
           next((f.get('name') for f in meta.get('fields', []) if f.get('type') == 'esriFieldTypeOID'), None))
    max_records = min(int(meta.get('maxRecordCount') or 1000), 500)
    features: list[dict[str, Any]] = []

    ids: list[int] = []
    try:
        ids_obj, _ = post_json(qurl, {'where': '1=1', 'returnIdsOnly': 'true', 'f': 'json'})
        ids = sorted(ids_obj.get('objectIds') or [])
    except Exception:
        ids = []

    if ids:
        for start in range(0, len(ids), max_records):
            obj, _ = post_json(qurl, {'objectIds': ','.join(map(str, ids[start:start + max_records])),
                                      'outFields': '*', 'returnGeometry': 'true',
                                      'outSR': '4326', 'f': 'json'})
            features.extend(convert_features(obj))
    else:
        if not oid:
            raise RuntimeError('No object ID field and returnIdsOnly failed')
        last_oid = -1
        seen: set[Any] = set()
        for _ in range(max(2, expected // max_records + 5)):
            obj, _ = post_json(qurl, {'where': f'{oid}>{last_oid}', 'outFields': '*',
                                      'returnGeometry': 'true', 'outSR': '4326', 'f': 'json',
                                      'orderByFields': oid, 'resultRecordCount': max_records})
            batch = convert_features(obj)
            if not batch:
                break
            for ft in batch:
                key = ft['properties'].get(oid)
                if key not in seen:
                    seen.add(key)
                    features.append(ft)
            new_last = max(ft['properties'].get(oid, last_oid) for ft in batch)
            if new_last <= last_oid:
                break
            last_oid = new_last

    if len(features) != expected:
        raise RuntimeError(f'incomplete layer {layer_id}: expected {expected}, got {len(features)}')
    out = out_dir / f'{layer_id:02d}_{name}.geojson'
    b.save_json({'type': 'FeatureCollection', 'name': name, 'features': features}, out)
    b.record_file(dataset, qurl, out, n_records=len(features), note='POST form query with ID/OID pagination')
    return len(features), out


def selected_service(name: str, url: str, layer_ids: list[int], minimum_nonempty: int) -> None:
    out_dir = b.RAW / 'nsw_arcgis' / name
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta, meta_url = b.request_json(url, {'f': 'pjson'})
        mp = out_dir / 'service_metadata.json'
        b.save_json(meta, mp)
        b.record_file(name, meta_url, mp)
    except Exception as exc:
        b.records.append(b.Record(name, url, str(out_dir), 'failed', note=repr(exc)))
        b.checks.append({'check': name, 'passed': False, 'value': 0, 'detail': 'service metadata failed'})
        return
    complete = 0
    nonempty = 0
    for lid in layer_ids:
        try:
            n, _ = query_layer_post(url, lid, name, out_dir)
            complete += 1
            nonempty += int(n > 0)
        except Exception as exc:
            b.records.append(b.Record(name, f'{url}/{lid}/query', str(out_dir), 'failed', note=repr(exc)))
    b.checks.append({'check': name, 'passed': nonempty >= minimum_nonempty,
                     'value': nonempty, 'detail': f'{complete}/{len(layer_ids)} selected layers complete'})


def spatial_and_biomass_v4() -> None:
    selected_service('essential_energy_uhc_2025',
        'https://portal.data.nsw.gov.au/arcgis/rest/services/Hosted/Essential_Energy_UHC_Data_2025/FeatureServer', [0, 1, 2], 3)
    selected_service('biomass_livestock',
        'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Livestock/MapServer', [0, 1, 2], 3)
    selected_service('biomass_cropping',
        'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Cropping/MapServer', list(range(19)), 16)
    selected_service('biomass_forestry',
        'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Forestry/MapServer', list(range(7)), 5)
    selected_service('biomass_organic_waste',
        'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/Waste_OrganicSolidWaste/MapServer', [0, 1, 2, 3], 4)


def curl_download(url: str, out: Path, dataset: str, min_bytes: int,
                  referer: str | None = None, office_zip: bool = False) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + '.part')
    cmd = ['curl', '-L', '--http1.1', '--fail', '--retry', '6', '--retry-all-errors',
           '--retry-delay', '5', '--connect-timeout', '40', '--max-time', '900',
           '-A', b.BROWSER_UA, '-o', str(tmp)]
    if referer:
        cmd += ['-e', referer]
    cmd.append(url)
    try:
        subprocess.run(cmd, check=True)
        if tmp.stat().st_size < min_bytes:
            raise RuntimeError(f'curl download too small: {tmp.stat().st_size}')
        if office_zip and not zipfile.is_zipfile(tmp):
            raise RuntimeError('downloaded Office/ZIP file is not a valid ZIP container')
        tmp.replace(out)
        b.record_file(dataset, url, out, note='curl HTTP/1.1 with retries')
        return True
    except Exception as exc:
        b.records.append(b.Record(dataset, url, str(out), 'failed', note=f'curl fallback failed: {exc!r}'))
        return False


def optional_document(dataset: str, page_url: str, pattern: str, out: Path, min_bytes: int) -> bool:
    try:
        links = b.scrape_links(page_url, [pattern])
    except Exception as exc:
        b.records.append(b.Record(dataset, page_url, str(out), 'failed', note=f'optional discovery failed: {exc!r}'))
        return False
    for url, label in links:
        if curl_download(url, out, dataset, min_bytes, page_url, office_zip=True):
            return True
    b.records.append(b.Record(dataset, page_url, str(out), 'failed', note='No downloadable matching attachment succeeded; supplementary only'))
    return False


def official_documents_v4() -> None:
    direct = [
        ('ABS regional population SA2',
         'https://www.abs.gov.au/statistics/people/population/regional-population/2024-25/32180DS0003_2001-25.xlsx',
         b.RAW / 'demographics/abs_population/32180DS0003_2001-25.xlsx', 100_000, True),
        ('ABS 2021 SA2 boundaries',
         'https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files/SA2_2021_AUST_SHP_GDA2020.zip',
         b.RAW / 'demographics/abs_boundaries/SA2_2021_AUST_SHP_GDA2020.zip', 10_000_000, True),
        ('ABS 2021 Census NSW SA2 DataPack',
         'https://www.abs.gov.au/census/find-census-data/datapacks/download/2021_GCP_SA2_for_NSW_short-header.zip',
         b.RAW / 'demographics/abs_census/2021_GCP_SA2_for_NSW_short-header.zip', 5_000_000, True),
    ]
    for dataset, url, out, min_bytes, zipped in direct:
        ok = b.download(url, out, dataset, min_bytes=min_bytes, validate_zip=(out.suffix == '.zip'))
        b.checks.append({'check': dataset, 'passed': ok, 'value': int(ok), 'detail': str(out)})

    nga_page = 'https://www.dcceew.gov.au/climate-change/publications/national-greenhouse-accounts-factors-2025'
    nga_url = 'https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.xlsx'
    nga_out = b.RAW / 'emissions/dcceew/national_greenhouse_account_factors_2025.xlsx'
    nga_ok = curl_download(nga_url, nga_out, 'DCCEEW National Greenhouse Accounts Factors', 50_000,
                           nga_page, office_zip=True)
    b.checks.append({'check': 'DCCEEW National Greenhouse Accounts Factors', 'passed': nga_ok,
                     'value': int(nga_ok), 'detail': str(nga_out)})

    before = len(b.checks)
    b.download_matching_documents('CSIRO GenCost',
        'https://www.csiro.au/en/research/technology-space/energy/electricity-transition/gencost',
        [r'gencost.*\.pdf', r'gencost.*\.xlsx'], b.RAW / 'economics/csiro_gencost')
    # The CSIRO check added by download_matching_documents is the core economic-input check.
    assert len(b.checks) > before

    # Supplementary sources: useful, but not required because CSIRO GenCost and NGA Factors
    # provide the core economic and emissions inputs needed by the model.
    optional_document('AEMO inputs and assumptions (supplementary)',
        'https://www.aemo.com.au/energy-systems/major-publications/integrated-system-plan-isp/2026-integrated-system-plan-isp/2025-26-inputs-assumptions-and-scenarios',
        r'2025 Inputs and Assumptions Workbook',
        b.RAW / 'economics/aemo/2025_inputs_and_assumptions_workbook.xlsm', 5_000_000)
    optional_document('Australian Petroleum Statistics (supplementary)',
        'https://www.energy.gov.au/publications/australian-petroleum-statistics-2026',
        r'Data Extract May 2026',
        b.RAW / 'fuel/australian_petroleum_statistics_may_2026.xlsx', 1_000_000)


def source_register_v4() -> None:
    rows = [
        ['Ausgrid zone-substation load', 'Ausgrid', 'Real 15-minute NSW zone-substation demand', 'Ausgrid publication disclaimer/terms', 'https://www.ausgrid.com.au/about-us/about-ausgrid/research-data-sets/distribution-zone-substation-data'],
        ['Essential Energy optional validation', 'Essential Energy', 'Regional NSW load validation when manually obtainable', 'Essential Energy terms', 'https://www.essentialenergy.com.au/our-network/network-projects/zone-substation-reports'],
        ['Essential Energy network GIS', 'NSW Government', 'Regional substation locations and attributes', 'NSW Open Data terms', 'https://portal.data.nsw.gov.au/'],
        ['NSW Bioenergy Assessment', 'NSW Government', 'Livestock, cropping, forestry and organic-waste feedstocks', 'NSW spatial-data terms', 'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment'],
        ['NASA POWER', 'NASA', 'Hourly solar and meteorological resources', 'NASA data policy and attribution', 'https://power.larc.nasa.gov/'],
        ['ABS population/Census/boundaries', 'ABS', 'SA2 population, Census and boundaries', 'CC BY 4.0 unless stated otherwise', 'https://www.abs.gov.au/'],
        ['CSIRO GenCost', 'CSIRO', 'Core technology cost benchmarks', 'CSIRO copyright/attribution', 'https://www.csiro.au/'],
        ['NGA Factors 2025', 'DCCEEW', 'Core official emissions factors', 'Commonwealth attribution', 'https://www.dcceew.gov.au/'],
        ['AEMO IASR', 'AEMO', 'Supplementary planning assumptions', 'AEMO website terms', 'https://www.aemo.com.au/'],
        ['Australian Petroleum Statistics', 'DCCEEW', 'Supplementary fuel-market data', 'Commonwealth attribution', 'https://www.energy.gov.au/'],
        ['PV/wind component libraries', 'NREL/SAM and pvlib', 'PV/inverter parameters and wind power curves', 'Retain source licences', 'https://github.com/NatLabRockies/SAM']]
    pd.DataFrame(rows, columns=['dataset', 'publisher', 'use', 'licence_note', 'source']).to_csv(
        b.META / 'source_and_licence_register.csv', index=False)


b.essential_energy_loads = measured_load_archives_v4
b.nsw_spatial_and_biomass = spatial_and_biomass_v4
b.official_economic_emissions_and_demographic_documents = official_documents_v4
b.create_licence_and_source_register = source_register_v4

if __name__ == '__main__':
    b.main()
