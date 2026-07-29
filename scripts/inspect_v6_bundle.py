from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    try:
        return pd.read_csv(path, low_memory=False, **kwargs)
    except UnicodeDecodeError:
        return pd.read_csv(path, low_memory=False, encoding='latin-1', **kwargs)


def zip_inventory(path: Path) -> dict[str, Any]:
    result = {'path': str(path), 'valid_zip': False, 'members': 0, 'uncompressed_bytes': 0}
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            result['valid_zip'] = bad is None
            result['bad_member'] = bad
            result['members'] = len(z.infolist())
            result['uncompressed_bytes'] = sum(x.file_size for x in z.infolist())
            result['sample_members'] = [x.filename for x in z.infolist()[:20]]
    except Exception as exc:
        result['error'] = repr(exc)
    return result


def parquet_summary(path: Path) -> dict[str, Any]:
    try:
        df = pd.read_parquet(path)
        result: dict[str, Any] = {
            'path': str(path),
            'rows': len(df),
            'columns': list(df.columns),
            'missing_fraction_max': float(df.isna().mean().max()) if len(df) else None,
        }
        if 'YEAR' in df.columns:
            result['year_min'] = int(pd.to_numeric(df['YEAR'], errors='coerce').min())
            result['year_max'] = int(pd.to_numeric(df['YEAR'], errors='coerce').max())
        return result
    except Exception as exc:
        return {'path': str(path), 'error': repr(exc)}


def main(bundle_archive: str, checksum_file: str, output_dir: str) -> None:
    bundle_archive_path = Path(bundle_archive)
    checksum_path = Path(checksum_file)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    extract_root = out / 'extracted'
    extract_root.mkdir(exist_ok=True)

    expected_sha = checksum_path.read_text(encoding='utf-8').strip().split()[0]
    actual_sha = sha256(bundle_archive_path)
    if actual_sha != expected_sha:
        raise RuntimeError(f'Bundle checksum mismatch: expected {expected_sha}, got {actual_sha}')

    with tarfile.open(bundle_archive_path, 'r:gz') as tar:
        tar.extractall(extract_root)

    candidates = [p for p in extract_root.rglob('bundle_v6') if p.is_dir()]
    if candidates:
        root = candidates[0]
    else:
        dirs = [p for p in extract_root.iterdir() if p.is_dir()]
        root = dirs[0] if len(dirs) == 1 else extract_root

    files = [p for p in root.rglob('*') if p.is_file()]
    inventory_rows: list[dict[str, Any]] = []
    ext_counts: Counter[str] = Counter()
    ext_sizes: Counter[str] = Counter()
    directory_sizes: defaultdict[str, int] = defaultdict(int)
    directory_counts: Counter[str] = Counter()

    for path in files:
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        ext = path.suffix.lower() or '[no_extension]'
        top = rel.split('/', 1)[0]
        ext_counts[ext] += 1
        ext_sizes[ext] += size
        directory_sizes[top] += size
        directory_counts[top] += 1
        inventory_rows.append({
            'path': rel,
            'size_bytes': size,
            'extension': ext,
            'sha256': sha256(path),
        })

    inventory_df = pd.DataFrame(inventory_rows).sort_values('path')
    inventory_df.to_csv(out / 'file_inventory.csv', index=False)

    metadata_dir = root / 'metadata'
    logs_dir = root / 'logs'
    summary_json = metadata_dir / 'bundle_summary.json'
    validation_csv = metadata_dir / 'core_validation_report.csv'
    manifest_csv = metadata_dir / 'download_manifest.csv'

    bundle_summary = json.loads(summary_json.read_text(encoding='utf-8')) if summary_json.exists() else {}
    validation = safe_read_csv(validation_csv) if validation_csv.exists() else pd.DataFrame()
    manifest = safe_read_csv(manifest_csv) if manifest_csv.exists() else pd.DataFrame()
    failures = safe_read_csv(logs_dir / 'failed_downloads.csv') if (logs_dir / 'failed_downloads.csv').exists() else pd.DataFrame()

    validation.to_csv(out / 'core_validation_report.csv', index=False)
    if not failures.empty:
        failures.to_csv(out / 'failed_downloads.csv', index=False)

    # Key dataset checks.
    key: dict[str, Any] = {}

    ausgrid_dir = root / 'raw' / 'ausgrid_zone_substation_load'
    ausgrid_archives = sorted(ausgrid_dir.glob('*.zip'))
    ausgrid_csvs = sorted((ausgrid_dir / 'extracted').rglob('*.csv')) if (ausgrid_dir / 'extracted').exists() else []
    key['ausgrid_load'] = {
        'archive_count': len(ausgrid_archives),
        'extracted_csv_count': len(ausgrid_csvs),
        'archive_years': sorted({p.stem[-4:] for p in ausgrid_archives if p.stem[-4:].isdigit()}),
        'archive_zip_checks': [zip_inventory(p) for p in ausgrid_archives],
        'sample_csv_headers': {},
        'scientific_use_note': (
            'Ausgrid data are measured NSW zone-substation loads but are not Essential Energy '
            'regional-community measurements. Use them for demand archetypes, transfer learning, '
            'or transparent real-data-derived profiles unless the paper scope is changed to the '
            'Ausgrid service territory.'
        ),
    }
    for p in ausgrid_csvs[:10]:
        try:
            df = safe_read_csv(p, nrows=5)
            key['ausgrid_load']['sample_csv_headers'][p.relative_to(root).as_posix()] = list(df.columns)
        except Exception as exc:
            key['ausgrid_load']['sample_csv_headers'][p.relative_to(root).as_posix()] = {'error': repr(exc)}

    nasa_dir = root / 'raw' / 'nasa_power_hourly'
    nasa_annual = sorted((nasa_dir / 'annual').glob('*.csv')) if (nasa_dir / 'annual').exists() else []
    nasa_combined = sorted((nasa_dir / 'combined').glob('*.parquet')) if (nasa_dir / 'combined').exists() else []
    nasa_summaries = [parquet_summary(p) for p in nasa_combined]
    key['nasa_power'] = {
        'annual_csv_count': len(nasa_annual),
        'combined_parquet_count': len(nasa_combined),
        'combined_files': nasa_summaries,
        'all_files_readable': all('error' not in x for x in nasa_summaries),
    }

    biomass_root = root / 'raw' / 'nsw_biomass_attributes'
    biomass: dict[str, Any] = {}
    if biomass_root.exists():
        for group in sorted(p for p in biomass_root.iterdir() if p.is_dir()):
            csvs = sorted(group.glob('*_attributes.csv'))
            rows = 0
            schemas: dict[str, list[str]] = {}
            errors: list[str] = []
            for p in csvs:
                try:
                    df = safe_read_csv(p)
                    rows += len(df)
                    schemas[p.name] = list(df.columns)
                except Exception as exc:
                    errors.append(f'{p.name}: {exc!r}')
            biomass[group.name] = {
                'attribute_file_count': len(csvs),
                'total_rows': rows,
                'schemas': schemas,
                'errors': errors,
            }
    key['biomass'] = biomass

    gis_root = root / 'raw' / 'nsw_arcgis' / 'essential_energy_uhc_2025'
    gis_files = sorted(gis_root.glob('*.geojson')) if gis_root.exists() else []
    gis_summary = []
    for p in gis_files:
        try:
            obj = json.loads(p.read_text(encoding='utf-8'))
            gis_summary.append({'file': p.name, 'features': len(obj.get('features', []))})
        except Exception as exc:
            gis_summary.append({'file': p.name, 'error': repr(exc)})
    key['essential_energy_gis'] = {'geojson_files': gis_summary}

    abs_files = sorted((root / 'raw' / 'demographics').rglob('*')) if (root / 'raw' / 'demographics').exists() else []
    abs_files = [p for p in abs_files if p.is_file()]
    key['abs'] = {
        'files': [{'path': p.relative_to(root).as_posix(), 'size_bytes': p.stat().st_size,
                   'zip': zip_inventory(p) if p.suffix.lower() == '.zip' else None}
                  for p in abs_files]
    }

    component_dir = root / 'raw' / 'component_databases'
    component_files = sorted(component_dir.glob('*')) if component_dir.exists() else []
    component_summary = []
    for p in component_files:
        item: dict[str, Any] = {'file': p.name, 'size_bytes': p.stat().st_size}
        if p.suffix.lower() == '.csv':
            try:
                df = safe_read_csv(p)
                item.update({'rows': len(df), 'columns': len(df.columns), 'column_names': list(df.columns)[:30]})
            except Exception as exc:
                item['error'] = repr(exc)
        component_summary.append(item)
    key['component_databases'] = component_summary

    economics_files = sorted((root / 'raw' / 'economics').rglob('*')) if (root / 'raw' / 'economics').exists() else []
    emissions_files = sorted((root / 'raw' / 'emissions').rglob('*')) if (root / 'raw' / 'emissions').exists() else []
    key['economics_and_emissions'] = {
        'economics_files': [p.relative_to(root).as_posix() for p in economics_files if p.is_file()],
        'emissions_files': [p.relative_to(root).as_posix() for p in emissions_files if p.is_file()],
    }

    derived_sites = root / 'derived' / 'candidate_sites.csv'
    if derived_sites.exists():
        sites = safe_read_csv(derived_sites)
        key['candidate_sites'] = {
            'count': len(sites),
            'columns': list(sites.columns),
            'records': sites.to_dict(orient='records'),
        }

    critical_patterns = {
        'measured_load': len(ausgrid_archives) >= 8 and len(ausgrid_csvs) >= 800,
        'weather_20_sites': len(nasa_combined) == 20 and all('error' not in x for x in nasa_summaries),
        'biomass_livestock': biomass.get('biomass_livestock', {}).get('attribute_file_count', 0) >= 3,
        'biomass_cropping': biomass.get('biomass_cropping', {}).get('attribute_file_count', 0) >= 16,
        'biomass_forestry': biomass.get('biomass_forestry', {}).get('attribute_file_count', 0) >= 5,
        'biomass_waste': biomass.get('biomass_organic_waste', {}).get('attribute_file_count', 0) >= 4,
        'regional_network_gis': len(gis_files) >= 3,
        'abs_population': any('abs_population' in p.as_posix() for p in abs_files),
        'abs_boundaries': any('abs_boundaries' in p.as_posix() for p in abs_files),
        'abs_census': any('abs_census' in p.as_posix() for p in abs_files),
        'component_databases': len(component_files) >= 4,
        'cost_benchmark': any('gencost' in p.as_posix().lower() for p in economics_files if p.is_file()),
        'emission_factors': any('model_inputs.csv' in p.name for p in emissions_files if p.is_file()),
    }

    core_validation_passed = bool(len(validation)) and bool(validation['passed'].astype(bool).all()) if 'passed' in validation.columns else False
    operational_ready = bool(bundle_summary.get('ready_for_modelling')) and core_validation_passed and all(critical_patterns.values())

    # Scientific gaps that remain even when the download bundle is operationally complete.
    scientific_gaps = [
        {
            'id': 'LOAD_GEOGRAPHY_ALIGNMENT',
            'severity': 'high',
            'status': 'open',
            'issue': 'Measured load archives are from Ausgrid, while candidate regional sites and GIS are from Essential Energy.',
            'resolution': 'Either obtain Essential Energy load archives manually, re-scope the detailed study to Ausgrid territory, or explicitly model regional demand as real-data-derived archetypes transferred/scaled from Ausgrid.'
        },
        {
            'id': 'BATTERY_TECHNO_ECONOMIC_PARAMETERS',
            'severity': 'high',
            'status': 'open',
            'issue': 'The bundle does not yet contain a structured battery degradation, calendar ageing, cycle ageing, replacement and power/energy cost table.',
            'resolution': 'Add a sourced model-input table from CSIRO/AEMO and peer-reviewed battery ageing literature.'
        },
        {
            'id': 'BIOENERGY_CONVERSION_PARAMETERS',
            'severity': 'high',
            'status': 'open',
            'issue': 'Biomass quantities are present, but feedstock-specific collection fraction, moisture, lower heating value, conversion efficiency, minimum load, ramping, storage loss, and generator cost inputs are not yet fully structured.',
            'resolution': 'Create a source-traceable feedstock and conversion parameter workbook before optimisation.'
        },
        {
            'id': 'LOCAL_FUEL_AND_LOGISTICS_COSTS',
            'severity': 'medium',
            'status': 'open',
            'issue': 'A complete model-ready diesel price and biomass collection/haulage cost series is not confirmed.',
            'resolution': 'Add Australian Petroleum Statistics or another official price series and a sourced transport-cost model.'
        },
        {
            'id': 'VALIDATION_GENERATION_DATA',
            'severity': 'medium',
            'status': 'open',
            'issue': 'NASA POWER provides meteorological inputs but no site-specific measured PV/wind generation benchmark for physical-model validation.',
            'resolution': 'Add public measured generation data or clearly limit validation to cross-source meteorological checks and published component benchmarks.'
        },
        {
            'id': 'RESILIENCE_EVENT_INPUTS',
            'severity': 'medium',
            'status': 'open',
            'issue': 'Outage durations, critical-load fractions, failure rates and repair times are not yet sourced as model inputs.',
            'resolution': 'Add a transparent scenario table or official reliability statistics before resilience optimisation.'
        },
    ]

    report = {
        'bundle_archive': str(bundle_archive_path),
        'bundle_sha256_expected': expected_sha,
        'bundle_sha256_actual': actual_sha,
        'checksum_verified': actual_sha == expected_sha,
        'bundle_root': str(root),
        'file_count': len(files),
        'extracted_size_bytes': sum(p.stat().st_size for p in files),
        'bundle_summary': bundle_summary,
        'core_validation_passed': core_validation_passed,
        'critical_presence_checks': critical_patterns,
        'operationally_ready_for_pipeline_development': operational_ready,
        'scientifically_complete_for_final_article_modelling': False,
        'scientific_gaps': scientific_gaps,
        'extension_counts': dict(ext_counts),
        'extension_sizes_bytes': dict(ext_sizes),
        'top_level_directory_counts': dict(directory_counts),
        'top_level_directory_sizes_bytes': dict(directory_sizes),
        'manifest_status_counts': manifest['status'].value_counts(dropna=False).to_dict() if 'status' in manifest.columns else {},
        'failed_download_count': len(failures),
        'failed_download_datasets': failures['dataset'].value_counts().to_dict() if not failures.empty and 'dataset' in failures.columns else {},
        'key_datasets': key,
    }
    (out / 'qa_report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')

    lines = [
        '# Regional NSW Microgrid Data Bundle v6 — Independent QA',
        '',
        f"- Checksum verified: **{report['checksum_verified']}**",
        f"- Extracted files: **{report['file_count']:,}**",
        f"- Extracted size: **{report['extracted_size_bytes'] / (1024**3):.3f} GiB**",
        f"- All internal core checks passed: **{core_validation_passed}**",
        f"- Operationally ready for pipeline development: **{operational_ready}**",
        f"- Scientifically complete for final article modelling: **False**",
        '',
        '## Critical presence checks',
        '',
    ]
    for name, passed in critical_patterns.items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} — `{name}`")
    lines += ['', '## Key counts', '']
    lines.append(f"- Ausgrid annual archives: {len(ausgrid_archives)}")
    lines.append(f"- Ausgrid extracted load CSVs: {len(ausgrid_csvs)}")
    lines.append(f"- NASA annual files: {len(nasa_annual)}")
    lines.append(f"- NASA combined site Parquet files: {len(nasa_combined)}")
    for group, item in biomass.items():
        lines.append(f"- {group}: {item['attribute_file_count']} files, {item['total_rows']} rows")
    lines += ['', '## Scientific gaps still open', '']
    for gap in scientific_gaps:
        lines.append(f"### {gap['id']} — {gap['severity'].upper()}")
        lines.append(gap['issue'])
        lines.append('')
        lines.append(f"Resolution: {gap['resolution']}")
        lines.append('')
    lines += [
        '## Decision',
        '',
        'The bundle is technically valid and sufficient to begin data engineering, exploratory analysis, clustering and forecasting pipeline development. It is **not yet sufficient to lock the final optimisation model or make final article claims** until the high-severity scientific input gaps above are resolved.',
    ]
    (out / 'QA_SUMMARY.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('Usage: inspect_v6_bundle.py BUNDLE_TAR_GZ CHECKSUM_FILE OUTPUT_DIR')
    main(sys.argv[1], sys.argv[2], sys.argv[3])
