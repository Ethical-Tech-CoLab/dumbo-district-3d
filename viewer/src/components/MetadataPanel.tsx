import type { AssetMetadata } from '@d3d/contracts';
import { CONFIDENCE_COLORS, CONFIDENCE_LABELS } from '@d3d/contracts';

const LABELS: Record<string, string> = {
  address: 'Address',
  owner: 'Owner',
  building_class: 'Building class',
  land_use: 'Land use',
  num_floors: 'Floors',
  residential_units: 'Residential units',
  total_units: 'Total units',
  year_built: 'Year built',
  construction_year: 'Construction year',
  height_roof_m: 'Roof height',
  ground_elevation_m: 'Ground elevation (NAVD88)',
  roof_elevation_m: 'Roof elevation (NAVD88)',
  footprint_area_m2: 'Footprint area',
  lot_area_m2: 'Lot area',
  zoning: 'Zoning',
  bin: 'BIN',
  bbl: 'BBL',
  geom_source: 'Geometry capture',
  height_basis: 'Height basis',
};

const UNITS: Record<string, string> = {
  height_roof_m: ' m',
  ground_elevation_m: ' m',
  roof_elevation_m: ' m',
  footprint_area_m2: ' m²',
  lot_area_m2: ' m²',
};

export default function MetadataPanel({ metadata }: { metadata: AssetMetadata | null }) {
  if (!metadata) {
    return (
      <section className="panel">
        <h2>Inspect</h2>
        <p className="muted">
          Click a building to read its record. Every value shown comes from a registered source and
          carries a confidence grade.
        </p>
      </section>
    );
  }

  const attributes = Object.entries(metadata.attributes ?? {}).filter(
    ([, value]) => value !== null && value !== '',
  );

  return (
    <section className="panel">
      <h2>{metadata.display_name}</h2>

      <div className="confidence-row">
        <span className="chip" style={{ background: CONFIDENCE_COLORS[metadata.confidence] }}>
          {metadata.confidence}
        </span>
        <span className="muted small">{CONFIDENCE_LABELS[metadata.confidence]}</span>
      </div>

      <dl className="kv">
        {attributes.map(([key, value]) => (
          <div key={key}>
            <dt>{LABELS[key] ?? key}</dt>
            <dd>
              {typeof value === 'number' ? value.toLocaleString() : String(value)}
              {UNITS[key] ?? ''}
            </dd>
          </div>
        ))}
      </dl>

      <h3>Provenance</h3>
      <dl className="kv">
        <div>
          <dt>Asset URN</dt>
          <dd className="mono small">{metadata.asset_id}</dd>
        </div>
        <div>
          <dt>Source basis</dt>
          <dd>{metadata.source_basis.join(', ')}</dd>
        </div>
        {metadata.source_refs && (
          <div>
            <dt>Sources</dt>
            <dd className="mono small">{metadata.source_refs.join(', ')}</dd>
          </div>
        )}
        {metadata.control_refs?.length ? (
          <div>
            <dt>Controls consumed</dt>
            <dd className="mono small">{metadata.control_refs.join(', ')}</dd>
          </div>
        ) : null}
        {metadata.open_questions?.length ? (
          <div>
            <dt>Open questions</dt>
            <dd className="mono small warn">{metadata.open_questions.join(', ')}</dd>
          </div>
        ) : null}
        <div>
          <dt>Review status</dt>
          <dd>{metadata.review_status}</dd>
        </div>
      </dl>
    </section>
  );
}
