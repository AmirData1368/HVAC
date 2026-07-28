from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(os.environ.get('BUNDLE_ROOT', 'bundle_v2'))
RAW, META, DERIVED, LOGS = (ROOT / x for x in ('raw', 'metadata', 'derived', 'logs'))
for p in (RAW, META, DERIVED, LOGS):
    p.mkdir(parents=True, exist_ok=True)

TIMEOUT = 180
START_YEAR = int(os.environ.get('START_YEAR', '2015'))
END_YEAR = int(os.environ.get('END_YEAR', '2025'))
SITE_LIMIT = int(os.environ.get('SITE_LIMIT', '20'))
BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36')
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': BROWSER_UA,
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-AU,en;q=0.9'})


@dataclass
class Record:
    dataset: str
    source_url: str
    local_path: str
    status: str
    http_status: int | None = None
    bytes: int | None = None
    sha256: str | None = None
    records: int | None = None
    note: str = ''


records: list[Record] = []
checks: list[dict[str, Any]] = []


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_name(text: str, default: str = 'file') -> str:
    name = Path(urlparse(text).path).name or default
    return re.sub(r'[^A-Za-z0-9._-]+', '_', name)


def save_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')


def record_file(dataset: str, source: str, path: Path, status: str = 'downloaded', *, http_status: int | None = None, n_records: int | None = None, note: str = '') -> None:
    records.append(Record(dataset, source, str(path), status, http_status, path.stat().st_size,
                          file_sha256(path), n_records, note))


def valid_json_payload(obj: Any) -> bool:
    return isinstance(obj, dict) and 'error' not in obj


def download(url: str, out: Path, dataset: str, *, min_bytes: int = 500,
             referer: str | None = None, validate_zip: bool = False,
             overwrite: bool = False) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size >= min_bytes and not overwrite:
        if not validate_zip or zipfile.is_zipfile(out):
            record_file(dataset, url, out, 'cached')
            return True
    headers = {'Referer': referer} if referer else {}
    try:
        with SESSION.get(url, stream=True, timeout=TIMEOUT, allow_redirects=True, headers=headers) as r:
            status = r.status_code
            r.raise_for_status()
            tmp = out.with_suffix(out.suffix + '.part')
            with tmp.open('wb') as f:
                for chunk in r.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)
            if tmp.stat().st_size < min_bytes:
                raise RuntimeError(f'download too small: {tmp.stat().st_size} bytes')
            if validate_zip and not zipfile.is_zipfile(tmp):
                raise RuntimeError('response is not a valid ZIP archive')
            tmp.replace(out)
            record_file(dataset, r.url, out, http_status=status)
            return True
    except Exception as exc:
        records.append(Record(dataset, url, str(out), 'failed', note=repr(exc)))
        return False


def get_soup(url: str) -> BeautifulSoup:
    r = SESSION.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, 'html.parser')


def scrape_links(page_url: str, patterns: Iterable[str]) -> list[tuple[str, str]]:
    soup = get_soup(page_url)
    pats = [re.compile(x, re.I) for x in patterns]
    found: list[tuple[str, str]] = []
    for a in soup.find_all('a', href=True):
        href = urljoin(page_url, a['href'])
        label = a.get_text(' ', strip=True)
        haystack = f'{label} {href}'
        if any(p.search(haystack) for p in pats):
            found.append((href, label))
    return list(dict.fromkeys(found))


def playwright_download_essential(page_url: str, out_dir: Path) -> set[str]:
    successful: set[str] = set()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        records.append(Record('Essential Energy zone-substation load', page_url, str(out_dir),
                              'failed', note=f'Playwright unavailable: {exc!r}'))
        return successful
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = browser.new_context(user_agent=BROWSER_UA, accept_downloads=True, locale='en-AU')
            page = context.new_page()
            page.goto(page_url, wait_until='domcontentloaded', timeout=120_000)
            page.wait_for_timeout(3_000)
            links = page.locator("a[href*='EE-Zone-Substation-Load-Data']")
            for i in range(links.count()):
                locator = links.nth(i)
                href = urljoin(page_url, locator.get_attribute('href') or '')
                if '.zip' not in href.lower():
                    continue
                m = re.search(r'(20\d{2}[-_]\d{2})', href)
                year = m.group(1).replace('_', '-') if m else f'link_{i}'
                out = out_dir / f'EE-Zone-Substation-Load-Data-{year}.zip'
                if out.exists() and zipfile.is_zipfile(out):
                    successful.add(year)
                    record_file('Essential Energy zone-substation load', href, out, 'cached')
                    continue
                try:
                    with page.expect_download(timeout=120_000) as info:
                        locator.click()
                    info.value.save_as(str(out))
                    if not zipfile.is_zipfile(out) or out.stat().st_size < 1000:
                        raise RuntimeError('browser download is not a valid ZIP')
                    successful.add(year)
                    record_file('Essential Energy zone-substation load', href, out,
                                note='Downloaded with Playwright browser session')
                except Exception as exc:
                    records.append(Record('Essential Energy zone-substation load', href, str(out),
                                          'failed', note=f'Playwright click failed: {exc!r}'))
            browser.close()
    except Exception as exc:
        records.append(Record('Essential Energy zone-substation load', page_url, str(out_dir),
                              'failed', note=f'Playwright session failed: {exc!r}'))
    return successful


def essential_energy_loads() -> None:
    dataset = 'Essential Energy zone-substation load'
    page_url = 'https://www.essentialenergy.com.au/our-network/network-projects/zone-substation-reports'
    out_dir = RAW / 'essential_energy_load'
    out_dir.mkdir(parents=True, exist_ok=True)
    successful: set[str] = set()
    discovered: list[tuple[str, str]] = []
    try:
        SESSION.get('https://www.essentialenergy.com.au/', timeout=TIMEOUT)
        discovered = scrape_links(page_url, [r'EE-Zone-Substation-Load-Data-20\d{2}[-_]\d{2}\.zip'])
    except Exception as exc:
        records.append(Record(dataset, page_url, str(out_dir), 'failed',
                              note=f'HTML discovery failed: {exc!r}'))
    candidates = [u for u, _ in discovered]
    for y in range(2012, 2025):
        fy = f'{y}-{str(y + 1)[-2:]}'
        candidates.extend([
            f'https://www.essentialenergy.com.au/ext/zonesubs/EE-Zone-Substation-Load-Data-{fy}.zip',
            f'https://www.essentialenergy.com.au/ext/schools/EE-Zone-Substation-Load-Data-{fy}.zip'])
    for url in dict.fromkeys(candidates):
        m = re.search(r'(20\d{2}[-_]\d{2})', url)
        year = m.group(1).replace('_', '-') if m else safe_name(url)
        if year in successful:
            continue
        out = out_dir / f'EE-Zone-Substation-Load-Data-{year}.zip'
        if download(url, out, dataset, min_bytes=1000, referer=page_url, validate_zip=True):
            successful.add(year)
    if len(successful) < 10:
        successful.update(playwright_download_essential(page_url, out_dir))
    inventory: list[dict[str, Any]] = []
    extracted_csvs = 0
    for z in sorted(out_dir.glob('*.zip')):
        if not zipfile.is_zipfile(z):
            continue
        extract_dir = out_dir / 'extracted' / z.stem
        with zipfile.ZipFile(z) as arc:
            arc.extractall(extract_dir)
            for member in arc.namelist():
                p = extract_dir / member
                inventory.append({'archive': z.name, 'member': member,
                                  'bytes': p.stat().st_size if p.is_file() else None})
                if p.suffix.lower() == '.csv' and p.is_file() and p.stat().st_size > 100:
                    extracted_csvs += 1
    pd.DataFrame(inventory).to_csv(META / 'essential_energy_archive_inventory.csv', index=False)
    checks.append({'check': 'essential_energy_load_archives',
                   'passed': len(successful) >= 10 and extracted_csvs >= 100,
                   'value': len(successful), 'detail': f'{extracted_csvs} extracted CSV files'})


def request_json(url: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    r = SESSION.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    obj = r.json()
    if not valid_json_payload(obj):
        raise RuntimeError(f'ArcGIS/API error: {obj.get("error") if isinstance(obj, dict) else "invalid JSON"}')
    return obj, r.url


def esri_geometry_to_geojson(geom: dict[str, Any] | None) -> dict[str, Any] | None:
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


def arcgis_layer_complete(service_url: str, layer_id: int, dataset: str,
                           out_dir: Path) -> tuple[int, Path]:
    layer_url = f'{service_url.rstrip("/")}/{layer_id}'
    meta, meta_url = request_json(layer_url, {'f': 'pjson'})
    layer_label = re.sub(r'[^A-Za-z0-9._-]+', '_', str(meta.get('name', dataset)))[:100]
    meta_path = out_dir / f'{layer_id:02d}_{layer_label}_metadata.json'
    save_json(meta, meta_path)
    record_file(dataset, meta_url, meta_path)
    oid_field = (meta.get('objectIdField') or meta.get('objectIdFieldName') or
                 next((f.get('name') for f in meta.get('fields', [])
                       if f.get('type') == 'esriFieldTypeOID'), None))
    ids_obj, ids_url = request_json(layer_url + '/query',
                                    {'where': '1=1', 'returnIdsOnly': 'true', 'f': 'json'})
    ids = ids_obj.get('objectIds') or []
    expected = len(ids)
    all_features: list[dict[str, Any]] = []
    if expected:
        batch_size = min(int(meta.get('maxRecordCount') or 500), 500)
        for start in range(0, expected, batch_size):
            batch = ids[start:start + batch_size]
            params = {'objectIds': ','.join(map(str, batch)), 'outFields': '*',
                      'returnGeometry': 'true', 'outSR': '4326', 'f': 'geojson'}
            try:
                obj, _ = request_json(layer_url + '/query', params)
                features = obj.get('features')
                if features is None:
                    raise RuntimeError('GeoJSON response has no features')
                all_features.extend(features)
            except Exception:
                params['f'] = 'json'
                obj, _ = request_json(layer_url + '/query', params)
                for feat in obj.get('features') or []:
                    all_features.append({'type': 'Feature',
                                         'properties': feat.get('attributes', {}),
                                         'geometry': esri_geometry_to_geojson(feat.get('geometry'))})
            time.sleep(0.05)
    out = out_dir / f'{layer_id:02d}_{layer_label}.geojson'
    save_json({'type': 'FeatureCollection', 'name': layer_label,
               'features': all_features}, out)
    if len(all_features) != expected:
        raise RuntimeError(f'incomplete ArcGIS layer {layer_id}: expected {expected}, collected {len(all_features)}')
    record_file(dataset, ids_url, out, n_records=len(all_features),
                note=f'Complete object-ID pagination; OID={oid_field}')
    return len(all_features), out


def arcgis_service(service_url: str, name: str) -> dict[int, tuple[int, Path]]:
    out_dir = RAW / 'nsw_arcgis' / name
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[int, tuple[int, Path]] = {}
    try:
        meta, url = request_json(service_url, {'f': 'pjson'})
        service_meta = out_dir / 'service_metadata.json'
        save_json(meta, service_meta)
        record_file(name, url, service_meta)
        for layer in list(meta.get('layers', [])) + list(meta.get('tables', [])):
            lid = int(layer['id'])
            try:
                result[lid] = arcgis_layer_complete(service_url, lid, name, out_dir)
            except Exception as exc:
                records.append(Record(name, f'{service_url}/{lid}/query', str(out_dir),
                                      'failed', note=repr(exc)))
    except Exception as exc:
        records.append(Record(name, service_url, str(out_dir), 'failed', note=repr(exc)))
    return result


def nsw_spatial_and_biomass() -> None:
    services = {
        'essential_energy_uhc_2025': 'https://portal.data.nsw.gov.au/arcgis/rest/services/Hosted/Essential_Energy_UHC_Data_2025/FeatureServer',
        'biomass_livestock': 'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Livestock/MapServer',
        'biomass_cropping': 'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Cropping/MapServer',
        'biomass_forestry': 'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Forestry/MapServer',
        'biomass_organic_waste': 'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/Waste_OrganicSolidWaste/MapServer'}
    for name, url in services.items():
        result = arcgis_service(url, name)
        nonempty = sum(1 for n, _ in result.values() if n > 0)
        checks.append({'check': name, 'passed': bool(result) and nonempty > 0,
                       'value': len(result), 'detail': f'{nonempty} non-empty complete layers'})


def candidate_sites_from_gis(limit: int = SITE_LIMIT) -> pd.DataFrame:
    base = RAW / 'nsw_arcgis' / 'essential_energy_uhc_2025'
    rows: list[dict[str, Any]] = []
    for f in sorted(base.glob('*.geojson')):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            for ft in data.get('features', []):
                geom = ft.get('geometry') or {}
                if geom.get('type') != 'Point':
                    continue
                coords = geom.get('coordinates') or []
                if len(coords) < 2:
                    continue
                props = ft.get('properties') or {}
                site = props.get('substation') or props.get('Substation') or props.get('name') or 'site'
                rows.append({'site_name': str(site), 'longitude': float(coords[0]),
                             'latitude': float(coords[1]), 'source_file': f.name})
        except Exception:
            continue
    df = pd.DataFrame(rows).drop_duplicates(subset=['site_name', 'latitude', 'longitude']) if rows else pd.DataFrame()
    if df.empty:
        raise RuntimeError('No point substations found in complete Essential Energy GIS data')
    df = df[df.longitude.between(140, 154) & df.latitude.between(-38, -27)].copy()
    df = df.sort_values(['longitude', 'latitude']).reset_index(drop=True)
    if len(df) > limit:
        positions = [round(i * (len(df) - 1) / max(limit - 1, 1)) for i in range(limit)]
        df = df.iloc[positions].drop_duplicates().reset_index(drop=True)
    df.to_csv(DERIVED / 'candidate_sites.csv', index=False)
    checks.append({'check': 'candidate_sites', 'passed': len(df) >= min(10, limit),
                   'value': len(df), 'detail': 'Derived only from complete live GIS data; no hard-coded fallback'})
    return df


def read_nasa_csv(path: Path) -> pd.DataFrame:
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    end_header = next((i for i, line in enumerate(lines) if '-END HEADER-' in line), None)
    if end_header is None:
        raise RuntimeError('NASA CSV header terminator not found')
    return pd.read_csv(path, skiprows=end_header + 1)


def nasa_power_weather() -> None:
    sites = candidate_sites_from_gis(SITE_LIMIT)
    out_dir = RAW / 'nasa_power_hourly'
    out_dir.mkdir(parents=True, exist_ok=True)
    parameters = ['ALLSKY_SFC_SW_DWN', 'ALLSKY_SFC_SW_DNI', 'ALLSKY_SFC_SW_DIFF',
                  'T2M', 'T2MDEW', 'RH2M', 'PS', 'PRECTOTCORR',
                  'WS10M', 'WD10M', 'WS50M', 'WD50M']
    endpoint = 'https://power.larc.nasa.gov/api/temporal/hourly/point'
    complete_sites = 0
    for _, row in sites.iterrows():
        slug = re.sub(r'[^A-Za-z0-9_-]+', '_', str(row.site_name))[:50]
        site_frames: list[pd.DataFrame] = []
        site_ok = True
        for year in range(START_YEAR, END_YEAR + 1):
            out = out_dir / 'annual' / f'{slug}_{row.latitude:.4f}_{row.longitude:.4f}_{year}.csv'
            query = {'parameters': ','.join(parameters), 'community': 'RE',
                     'longitude': row.longitude, 'latitude': row.latitude,
                     'start': f'{year}0101', 'end': f'{year}1231',
                     'format': 'CSV', 'time-standard': 'UTC'}
            prepared = requests.Request('GET', endpoint, params=query).prepare().url
            if not download(prepared, out, 'NASA POWER hourly weather', min_bytes=10_000):
                site_ok = False
                continue
            try:
                frame = read_nasa_csv(out)
                if len(frame) < 8700 or 'YEAR' not in frame.columns:
                    raise RuntimeError(f'unexpected row count/schema: {len(frame)}')
                site_frames.append(frame)
            except Exception as exc:
                site_ok = False
                records.append(Record('NASA POWER validation', prepared or endpoint, str(out),
                                      'failed', note=repr(exc)))
            time.sleep(0.25)
        if site_ok and len(site_frames) == END_YEAR - START_YEAR + 1:
            combined = pd.concat(site_frames, ignore_index=True)
            combined_path = out_dir / 'combined' / f'{slug}_{row.latitude:.4f}_{row.longitude:.4f}_{START_YEAR}_{END_YEAR}.parquet'
            combined_path.parent.mkdir(parents=True, exist_ok=True)
            combined.to_parquet(combined_path, index=False)
            record_file('NASA POWER combined hourly weather', endpoint, combined_path,
                        'derived', n_records=len(combined))
            complete_sites += 1
    checks.append({'check': 'nasa_power_complete_sites',
                   'passed': complete_sites == len(sites), 'value': complete_sites,
                   'detail': f'{len(sites)} sites requested for {START_YEAR}-{END_YEAR}'})


def pv_and_wind_component_databases() -> None:
    import pvlib
    out_dir = RAW / 'component_databases'
    out_dir.mkdir(parents=True, exist_ok=True)
    for dbname in ('CECMod', 'CECInverter', 'SandiaMod'):
        try:
            df = pvlib.pvsystem.retrieve_sam(dbname)
            out = out_dir / f'{dbname}.csv'
            df.to_csv(out)
            record_file(f'PVLib {dbname}', 'pvlib bundled SAM database', out,
                        'exported', n_records=df.shape[1])
        except Exception as exc:
            records.append(Record(f'PVLib {dbname}', 'pvlib bundled SAM database',
                                  str(out_dir), 'failed', note=repr(exc)))
    wind_url = 'https://raw.githubusercontent.com/NatLabRockies/SAM/develop/deploy/libraries/Wind%20Turbines.csv'
    wind_ok = download(wind_url, out_dir / 'NREL_SAM_Wind_Turbines.csv',
                       'NREL SAM wind turbine library', min_bytes=5000)
    checks.append({'check': 'component_databases',
                   'passed': wind_ok and all((out_dir / f'{x}.csv').exists()
                                             for x in ('CECMod', 'CECInverter', 'SandiaMod')),
                   'value': 4, 'detail': 'PV modules, inverters and wind-turbine power curves'})


def download_matching_documents(dataset: str, page: str, patterns: list[str],
                                out_dir: Path, *, require: int = 1) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    successes = 0
    try:
        for i, (url, label) in enumerate(scrape_links(page, patterns)):
            ext = Path(urlparse(url).path).suffix.lower()
            if ext not in {'.xlsx', '.xlsm', '.xls', '.pdf', '.docx', '.csv', '.zip', '.gpkg'}:
                continue
            out = out_dir / safe_name(url, f'file_{i}{ext}')
            if download(url, out, dataset, min_bytes=1000, referer=page,
                        validate_zip=(ext == '.zip')):
                successes += 1
    except Exception as exc:
        records.append(Record(dataset, page, str(out_dir), 'failed', note=repr(exc)))
    checks.append({'check': dataset, 'passed': successes >= require,
                   'value': successes, 'detail': f'minimum required={require}'})
    return successes


def official_economic_emissions_and_demographic_documents() -> None:
    direct = [
        ('AEMO inputs and assumptions',
         'https://www.aemo.com.au/-/media/files/stakeholder_consultation/consultations/nem-consultations/2024/2025-iasr-scenarios/final-docs/2025-inputs-and-assumptions-workbook.xlsm?rev=2d0e43f63185479e9c7b54331f0aad7b&sc_lang=en',
         RAW / 'economics/aemo/2025_inputs_and_assumptions_workbook.xlsm', 1_000_000),
        ('DCCEEW National Greenhouse Accounts Factors',
         'https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.xlsx',
         RAW / 'emissions/dcceew/national_greenhouse_accounts_factors_2025.xlsx', 50_000)]
    for dataset, url, out, min_bytes in direct:
        ok = download(url, out, dataset, min_bytes=min_bytes)
        checks.append({'check': dataset, 'passed': ok, 'value': int(ok), 'detail': str(out)})
    download_matching_documents('CSIRO GenCost',
        'https://www.csiro.au/en/research/technology-space/energy/electricity-transition/gencost',
        [r'gencost.*\.pdf', r'gencost.*\.xlsx'], RAW / 'economics/csiro_gencost')
    download_matching_documents('ABS regional population SA2',
        'https://www.abs.gov.au/statistics/people/population/regional-population/2024-25',
        [r'Population estimates.*SA2.*2001.*2025', r'Population estimates and components.*SA2.*2024.*2025'],
        RAW / 'demographics/abs_population')
    download_matching_documents('ABS 2021 SA2 boundaries',
        'https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files',
        [r'Statistical Area Level 2.*2021.*Shapefile', r'SA2_2021_AUST_GDA2020.*\.zip'],
        RAW / 'demographics/abs_boundaries')
    download_matching_documents('ABS 2021 Census NSW SA2 DataPack',
        'https://www.abs.gov.au/census/find-census-data/datapacks',
        [r'2021 General Community Profile.*Statistical Area 2.*New South Wales',
         r'2021_GCP_SA2.*NSW.*\.zip'], RAW / 'demographics/abs_census')
    download_matching_documents('Australian Petroleum Statistics',
        'https://www.energy.gov.au/publications/australian-petroleum-statistics-2026',
        [r'Data Extract.*2026.*\.xlsx', r'Australian Petroleum Statistics.*Data Extract.*\.xlsx'],
        RAW / 'fuel/australian_petroleum_statistics')


def source_register() -> None:
    rows = [
        ['Essential Energy zone-substation load', 'Essential Energy', 'Measured half-hourly active/reactive load', 'Public data; retain conditions of use', 'https://www.essentialenergy.com.au/our-network/network-projects/zone-substation-reports'],
        ['Essential Energy network GIS', 'NSW Government', 'Substation location and network attributes', 'NSW Open Data terms', 'https://portal.data.nsw.gov.au/'],
        ['NSW Bioenergy Assessment', 'NSW Government', 'Livestock, cropping, forestry and organic-waste feedstocks', 'NSW spatial-data terms', 'https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment'],
        ['NASA POWER', 'NASA', 'Hourly solar and meteorological resources', 'NASA data policy and attribution', 'https://power.larc.nasa.gov/'],
        ['ABS population/Census/boundaries', 'Australian Bureau of Statistics', 'SA2 population, demographic and boundary data', 'CC BY 4.0 unless stated otherwise', 'https://www.abs.gov.au/'],
        ['AEMO inputs and assumptions', 'AEMO', 'Technology costs and planning assumptions', 'AEMO website terms', 'https://www.aemo.com.au/'],
        ['CSIRO GenCost', 'CSIRO', 'Generation and storage cost benchmarks', 'CSIRO copyright/attribution', 'https://www.csiro.au/'],
        ['National Greenhouse Accounts Factors', 'DCCEEW', 'Official Australian emissions factors', 'Commonwealth attribution', 'https://www.dcceew.gov.au/'],
        ['Australian Petroleum Statistics', 'DCCEEW', 'Fuel supply and market statistics', 'Commonwealth attribution', 'https://www.energy.gov.au/'],
        ['PV/wind component libraries', 'NREL/SAM and pvlib', 'PV module/inverter parameters and wind power curves', 'Retain source licences', 'https://github.com/NatLabRockies/SAM']]
    pd.DataFrame(rows, columns=['dataset', 'publisher', 'use', 'licence_note', 'source']).to_csv(
        META / 'source_and_licence_register.csv', index=False)


def finalise_and_validate() -> None:
    source_register()
    df = pd.DataFrame([asdict(r) for r in records])
    df.to_csv(META / 'download_manifest.csv', index=False)
    save_json([asdict(r) for r in records], META / 'download_manifest.json')
    check_df = pd.DataFrame(checks)
    check_df.to_csv(META / 'core_validation_report.csv', index=False)
    save_json(checks, META / 'core_validation_report.json')
    failures = df[df.status == 'failed'] if not df.empty else pd.DataFrame()
    if not failures.empty:
        failures.to_csv(LOGS / 'failed_downloads.csv', index=False)
    required_failed = check_df[~check_df.passed.astype(bool)] if not check_df.empty else pd.DataFrame([{'check': 'no_checks', 'passed': False}])
    summary = {'generated_utc': pd.Timestamp.utcnow().isoformat(),
               'record_count': len(df),
               'successful': int(df.status.isin(['downloaded', 'cached', 'exported', 'derived']).sum()) if not df.empty else 0,
               'failed_attempts': int((df.status == 'failed').sum()) if not df.empty else 0,
               'core_checks': len(check_df),
               'core_checks_passed': int(check_df.passed.astype(bool).sum()) if not check_df.empty else 0,
               'core_checks_failed': len(required_failed),
               'bundle_bytes': sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file()),
               'ready_for_modelling': required_failed.empty}
    save_json(summary, META / 'bundle_summary.json')
    (ROOT / 'README.md').write_text(
        '# Regional NSW Microgrid Data Bundle v2\n\n'
        f"Generated: {summary['generated_utc']}\n\n"
        f"Ready for modelling: **{summary['ready_for_modelling']}**\n\n"
        'Do not begin modelling unless `metadata/bundle_summary.json` reports '
        '`ready_for_modelling: true` and every row in `metadata/core_validation_report.csv` passes.\n',
        encoding='utf-8')
    if not required_failed.empty:
        print('\nCORE VALIDATION FAILED:\n', required_failed.to_string(index=False), file=sys.stderr)
        raise SystemExit(2)


def main() -> None:
    essential_energy_loads()
    nsw_spatial_and_biomass()
    nasa_power_weather()
    pv_and_wind_component_databases()
    official_economic_emissions_and_demographic_documents()
    finalise_and_validate()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        records.append(Record('pipeline', '', str(ROOT), 'failed', note=repr(exc)))
        try:
            finalise_and_validate()
        except Exception:
            pass
        raise
