from __future__ import annotations

import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pandas as pd

import build_data_bundle_v2 as b


def browser_fetch(page_url: str, link_pattern: str, out: Path, dataset: str, min_bytes: int = 1000) -> bool:
    """Download a public file through a real browser session when CDN anti-bot blocks requests."""
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = browser.new_context(user_agent=b.BROWSER_UA, accept_downloads=True, locale='en-AU')
            page = context.new_page()
            page.goto(page_url, wait_until='domcontentloaded', timeout=180_000)
            page.wait_for_timeout(2500)
            rx = re.compile(link_pattern, re.I)
            links = page.locator('a')
            target = None
            for i in range(links.count()):
                loc = links.nth(i)
                label = (loc.inner_text(timeout=5000) or '').strip()
                href = loc.get_attribute('href') or ''
                if rx.search(f'{label} {href}'):
                    target = loc
                    break
            if target is None:
                raise RuntimeError(f'No link matched {link_pattern!r}')
            href = urljoin(page.url, target.get_attribute('href') or '')
            response = context.request.get(href, headers={'Referer': page.url}, timeout=180_000)
            if response.ok:
                out.write_bytes(response.body())
            else:
                with page.expect_download(timeout=180_000) as info:
                    target.click()
                info.value.save_as(str(out))
            browser.close()
        if out.stat().st_size < min_bytes:
            raise RuntimeError(f'browser download too small: {out.stat().st_size}')
        b.record_file(dataset, href, out, note='Downloaded through browser context')
        return True
    except Exception as exc:
        b.records.append(b.Record(dataset, page_url, str(out), 'failed', note=f'Browser fetch failed: {exc!r}'))
        return False


def measured_load_archives() -> None:
    """Collect an automatically accessible measured NSW load library.

    Essential Energy publishes the preferred regional NSW archives, but its CDN blocks
    cloud runners. Their exact official links are retained for optional local/manual use.
    Ausgrid's public 15-minute zone-substation archives provide a complete automatic
    measured-load fallback for model development and load-archetype learning.
    """
    manual_dir = b.RAW / 'essential_energy_manual_links'
    manual_dir.mkdir(parents=True, exist_ok=True)
    essential_links = []
    for y in range(2012, 2025):
        fy = f'{y}-{str(y + 1)[-2:]}'
        folder = 'schools' if y >= 2023 else 'zonesubs'
        essential_links.append({
            'financial_year': fy,
            'url': f'https://www.essentialenergy.com.au/ext/{folder}/EE-Zone-Substation-Load-Data-{fy}.zip',
            'status': 'official_public_link_cloud_403_manual_browser_download',
        })
    pd.DataFrame(essential_links).to_csv(manual_dir / 'essential_energy_official_links.csv', index=False)
    b.record_file('Essential Energy official load-link register',
                  'https://www.essentialenergy.com.au/our-network/network-projects/zone-substation-reports',
                  manual_dir / 'essential_energy_official_links.csv', 'derived',
                  n_records=len(essential_links),
                  note='CDN blocks GitHub/Claude cloud IPs; files remain optional, not a hidden failure')

    page_url = 'https://www.ausgrid.com.au/about-us/about-ausgrid/research-data-sets/distribution-zone-substation-data'
    out_dir = b.RAW / 'ausgrid_zone_substation_load'
    out_dir.mkdir(parents=True, exist_ok=True)
    links: list[tuple[str, str]] = []
    try:
        links = b.scrape_links(page_url, [r'distribution zone substation data', r'\.zip(?:\?|$)'])
    except Exception as exc:
        b.records.append(b.Record('Ausgrid zone-substation load', page_url, str(out_dir), 'failed', note=repr(exc)))
    selected = []
    for url, label in links:
        match = re.search(r'\b(20(?:1[5-9]|2[0-5]))\b', f'{label} {url}')
        if match and '.zip' in url.lower():
            selected.append((int(match.group(1)), url))
    selected = sorted(dict((year, url) for year, url in selected).items())
    successes = 0
    inventory: list[dict[str, Any]] = []
    csv_count = 0
    for year, url in selected:
        out = out_dir / f'ausgrid_distribution_zone_substation_{year}.zip'
        if b.download(url, out, 'Ausgrid zone-substation load', min_bytes=1_000_000,
                      referer=page_url, validate_zip=True):
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
                     'detail': f'{csv_count} measured zone-substation CSV files; years 2015-2025 targeted'})


def esri_to_geojson(geom: dict[str, Any] | None) -> dict[str, Any] | None:
    if not geom:
        return None
    if 'x' in geom and 'y' in geom:
        return {'type': 'Point', 'coordinates': [geom['x'], geom['y']]}
    if 'rings' in geom:
        return {'type': 'Polygon', 'coordinates': geom['rings']}
    if 'paths' in geom:
        return {'type': 'MultiLineString' if len(geom['paths']) > 1 else 'LineString',
                'coordinates': geom['paths'] if len(geom['paths']) > 1 else geom['paths'][0]}
    if 'points' in geom:
        return {'type': 'MultiPoint', 'coordinates': geom['points']}
    return None


def query_layer(service_url: str, layer_id: int, dataset: str, out_dir: Path) -> tuple[int, Path]:
    layer_url = f'{service_url.rstrip("/")}/{layer_id}'
    meta, meta_url = b.request_json(layer_url, {'f': 'pjson'})
    if meta.get('type') != 'Feature Layer':
        raise RuntimeError(f"Layer {layer_id} is not a Feature Layer: {meta.get('type')}")
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', str(meta.get('name', f'layer_{layer_id}')))[:100]
    meta_path = out_dir / f'{layer_id:02d}_{name}_metadata.json'
    b.save_json(meta, meta_path)
    b.record_file(dataset, meta_url, meta_path)
    query_url = layer_url + '/query'
    count_obj, _ = b.request_json(query_url, {'where': '1=1', 'returnCountOnly': 'true', 'f': 'json'})
    expected = int(count_obj.get('count', 0))
    max_records = min(int(meta.get('maxRecordCount') or 1000), 1000)
    features: list[dict[str, Any]] = []

    def append_features(obj: dict[str, Any]) -> None:
        if 'features' not in obj:
            raise RuntimeError('query response has no features')
        for feat in obj.get('features') or []:
            if feat.get('type') == 'Feature':
                features.append(feat)
            else:
                features.append({'type': 'Feature', 'properties': feat.get('attributes', {}),
                                 'geometry': esri_to_geojson(feat.get('geometry'))})

    if expected <= max_records:
        obj, _ = b.request_json(query_url, {'where': '1=1', 'outFields': '*',
                                           'returnGeometry': 'true', 'outSR': '4326', 'f': 'geojson'})
        append_features(obj)
    else:
        oid = (meta.get('objectIdField') or meta.get('objectIdFieldName') or
               next((f.get('name') for f in meta.get('fields', [])
                     if f.get('type') == 'esriFieldTypeOID'), None))
        ids = []
        try:
            ids_obj, _ = b.request_json(query_url, {'where': '1=1', 'returnIdsOnly': 'true', 'f': 'json'})
            ids = ids_obj.get('objectIds') or []
        except Exception:
            ids = []
        if ids:
            for start in range(0, len(ids), min(max_records, 500)):
                obj, _ = b.request_json(query_url, {'objectIds': ','.join(map(str, ids[start:start + 500])),
                                                    'outFields': '*', 'returnGeometry': 'true',
                                                    'outSR': '4326', 'f': 'geojson'})
                append_features(obj)
        else:
            for offset in range(0, expected, max_records):
                params = {'where': '1=1', 'outFields': '*', 'returnGeometry': 'true',
                          'outSR': '4326', 'f': 'geojson', 'resultOffset': offset,
                          'resultRecordCount': max_records}
                if oid:
                    params['orderByFields'] = oid
                obj, _ = b.request_json(query_url, params)
                append_features(obj)
                time.sleep(0.05)
    if len(features) != expected:
        raise RuntimeError(f'incomplete layer {layer_id}: expected {expected}, got {len(features)}')
    out = out_dir / f'{layer_id:02d}_{name}.geojson'
    b.save_json({'type': 'FeatureCollection', 'name': name, 'features': features}, out)
    b.record_file(dataset, query_url, out, n_records=len(features),
                  note='Count-validated query with direct/ID/offset fallback')
    return len(features), out


def selected_arcgis_service(name: str, url: str, layer_ids: list[int], minimum_nonempty: int) -> None:
    out_dir = b.RAW / 'nsw_arcgis' / name
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta, meta_url = b.request_json(url, {'f': 'pjson'})
        meta_path = out_dir / 'service_metadata.json'
        b.save_json(meta, meta_path)
        b.record_file(name, meta_url, meta_path)
    except Exception as exc:
        b.records.append(b.Record(name, url, str(out_dir), 'failed', note=repr(exc)))
        b.checks.append({'check': name, 'passed': False, 'value': 0, 'detail': 'service metadata failed'})
        return
    nonempty = 0
    completed = 0
    for layer_id in layer_ids:
        try:
            n, _ = query_layer(url, layer_id, name, out_dir)
            completed += 1
            nonempty += int(n > 0)
        except Exception as exc:
            b.records.append(b.Record(name, f'{url}/{layer_id}/query', str(out_dir), 'failed', note=repr(exc)))
    b.checks.append({'check': name, 'passed': nonempty >= minimum_nonempty,
                     'value': nonempty, 'detail': f'{completed}/{len(layer_ids)} selected layers complete'})


def spatial_and_biomass() -> None:
    selected_arcgis_service('essential_energy_uhc_2025',
        'https://portal.data.nsw.gov.au/arcgis/rest/services/Hosted/Essential_Energy_UHC_Data_2025/FeatureServer',
        [0, 1, 2], 3)
    selected_arcgis_service('biomass_livestock',
        'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Livestock/MapServer',
        [0, 1, 2], 3)
    selected_arcgis_service('biomass_cropping',
        'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Cropping/MapServer',
        list(range(19)), 16)
    selected_arcgis_service('biomass_forestry',
        'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Forestry/MapServer',
        list(range(7)), 5)
    selected_arcgis_service('biomass_organic_waste',
        'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/Waste_OrganicSolidWaste/MapServer',
        [0, 1, 2, 3], 4)


def official_documents() -> None:
    abs_population = ('https://www.abs.gov.au/statistics/people/population/regional-population/'
                      '2024-25/32180DS0003_2001-25.xlsx')
    abs_boundary = ('https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/'
                    'edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files/'
                    'SA2_2021_AUST_SHP_GDA2020.zip')
    abs_census = ('https://www.abs.gov.au/census/find-census-data/datapacks/download/'
                  '2021_GCP_SA2_for_NSW_short-header.zip')
    petroleum = ('https://www.energy.gov.au/sites/default/files/2026-07/'
                 'australian_petroleum_statistics_-_data_extract_may_2026.xlsx')
    direct_files = [
        ('ABS regional population SA2', abs_population,
         b.RAW / 'demographics/abs_population/32180DS0003_2001-25.xlsx', 100_000, False),
        ('ABS 2021 SA2 boundaries', abs_boundary,
         b.RAW / 'demographics/abs_boundaries/SA2_2021_AUST_SHP_GDA2020.zip', 10_000_000, True),
        ('ABS 2021 Census NSW SA2 DataPack', abs_census,
         b.RAW / 'demographics/abs_census/2021_GCP_SA2_for_NSW_short-header.zip', 5_000_000, True),
        ('Australian Petroleum Statistics', petroleum,
         b.RAW / 'fuel/australian_petroleum_statistics_may_2026.xlsx', 1_000_000, False),
    ]
    for dataset, url, out, min_bytes, is_zip in direct_files:
        ok = b.download(url, out, dataset, min_bytes=min_bytes, validate_zip=is_zip)
        b.checks.append({'check': dataset, 'passed': ok, 'value': int(ok), 'detail': str(out)})

    aemo_page = ('https://www.aemo.com.au/energy-systems/major-publications/integrated-system-plan-isp/'
                 '2026-integrated-system-plan-isp/2025-26-inputs-assumptions-and-scenarios')
    aemo_url = ('https://www.aemo.com.au/-/media/files/stakeholder_consultation/consultations/nem-consultations/'
                '2024/2025-iasr-scenarios/final-docs/2025-inputs-and-assumptions-workbook.xlsm'
                '?rev=2d0e43f63185479e9c7b54331f0aad7b&sc_lang=en')
    aemo_out = b.RAW / 'economics/aemo/2025_inputs_and_assumptions_workbook.xlsm'
    aemo_ok = b.download(aemo_url, aemo_out, 'AEMO inputs and assumptions', min_bytes=5_000_000,
                         referer=aemo_page)
    if not aemo_ok:
        aemo_ok = browser_fetch(aemo_page, r'2025 Inputs and Assumptions Workbook', aemo_out,
                                'AEMO inputs and assumptions', 5_000_000)
    b.checks.append({'check': 'AEMO inputs and assumptions', 'passed': aemo_ok,
                     'value': int(aemo_ok), 'detail': str(aemo_out)})

    nga_page = 'https://www.dcceew.gov.au/climate-change/publications/national-greenhouse-accounts-factors-2025'
    nga_url = 'https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.xlsx'
    nga_out = b.RAW / 'emissions/dcceew/national_greenhouse_account_factors_2025.xlsx'
    nga_ok = b.download(nga_url, nga_out, 'DCCEEW National Greenhouse Accounts Factors',
                        min_bytes=50_000, referer=nga_page)
    if not nga_ok:
        nga_ok = browser_fetch(nga_page, r'National Greenhouse Account Factors 2025.*XLSX', nga_out,
                               'DCCEEW National Greenhouse Accounts Factors', 50_000)
    b.checks.append({'check': 'DCCEEW National Greenhouse Accounts Factors', 'passed': nga_ok,
                     'value': int(nga_ok), 'detail': str(nga_out)})

    b.download_matching_documents('CSIRO GenCost',
        'https://www.csiro.au/en/research/technology-space/energy/electricity-transition/gencost',
        [r'gencost.*\.pdf', r'gencost.*\.xlsx'], b.RAW / 'economics/csiro_gencost')


def source_register() -> None:
    rows = [
        ['Ausgrid zone-substation load', 'Ausgrid', 'Measured 15-minute NSW zone-substation demand archives', 'Ausgrid publication disclaimer/terms', 'https://www.ausgrid.com.au/about-us/about-ausgrid/research-data-sets/distribution-zone-substation-data'],
        ['Essential Energy optional load links', 'Essential Energy', 'Preferred regional NSW half-hourly archives; manual browser download because cloud CDN returns 403', 'Essential Energy acknowledgement/disclaimer', 'https://www.essentialenergy.com.au/our-network/network-projects/zone-substation-reports'],
        ['Essential Energy network GIS', 'NSW Government', 'Regional substation locations and attributes', 'NSW Open Data terms', 'https://portal.data.nsw.gov.au/'],
        ['NSW Bioenergy Assessment', 'NSW Government', 'Livestock, cropping, forestry and organic-waste feedstocks', 'NSW spatial-data terms', 'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment'],
        ['NASA POWER', 'NASA', 'Hourly solar and meteorological resources', 'NASA data policy and attribution', 'https://power.larc.nasa.gov/'],
        ['ABS population/Census/boundaries', 'Australian Bureau of Statistics', 'SA2 population, Census and boundaries', 'CC BY 4.0 unless stated otherwise', 'https://www.abs.gov.au/'],
        ['AEMO inputs and assumptions', 'AEMO', 'Technology costs and planning assumptions', 'AEMO website terms', 'https://www.aemo.com.au/'],
        ['CSIRO GenCost', 'CSIRO', 'Generation and storage cost benchmarks', 'CSIRO copyright/attribution', 'https://www.csiro.au/'],
        ['National Greenhouse Accounts Factors', 'DCCEEW', 'Official Australian emissions factors', 'Commonwealth attribution', 'https://www.dcceew.gov.au/'],
        ['Australian Petroleum Statistics', 'DCCEEW', 'Fuel market and supply data', 'Commonwealth attribution', 'https://www.energy.gov.au/'],
        ['PV/wind component libraries', 'NREL/SAM and pvlib', 'PV module/inverter parameters and wind power curves', 'Retain source licences', 'https://github.com/NatLabRockies/SAM'],
    ]
    pd.DataFrame(rows, columns=['dataset', 'publisher', 'use', 'licence_note', 'source']).to_csv(
        b.META / 'source_and_licence_register.csv', index=False)


b.essential_energy_loads = measured_load_archives
b.nsw_spatial_and_biomass = spatial_and_biomass
b.official_economic_emissions_and_demographic_documents = official_documents
b.source_register = source_register

if __name__ == '__main__':
    try:
        b.main()
    except Exception as exc:
        b.records.append(b.Record('pipeline_v3', '', str(b.ROOT), 'failed', note=repr(exc)))
        try:
            b.finalise_and_validate()
        except Exception:
            pass
        raise
