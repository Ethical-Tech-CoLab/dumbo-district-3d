import type { AssetMetadata } from '@d3d/contracts';
import { CONFIDENCE_COLORS, CONFIDENCE_LABELS } from '@d3d/contracts';
import CollapsiblePanel from './CollapsiblePanel';

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
  ground_elevation_basis: 'Ground elevation from',
};

const UNITS: Record<string, string> = {
  height_roof_m: ' m',
  ground_elevation_m: ' m',
  roof_elevation_m: ' m',
  footprint_area_m2: ' m²',
  lot_area_m2: ' m²',
};

/** Appearance a building took from a photograph rather than from its class and age. */
export interface FacadeEvidence {
  color: string;
  observed_grade?: string;
  colour_source?: string;
  attribution_text?: string;
}

interface Props {
  metadata: AssetMetadata | null;
  evidence?: FacadeEvidence | null;
  onClose?: () => void;
}

export default function MetadataPanel({ metadata, evidence, onClose }: Props) {
  if (!metadata) {
    return (
      <section className="panel">
        <h2>Inspect</h2>
        <p className="muted">
          Click a building to read its record. Every value shown comes from a registered source and
          carries a confidence grade.
        </p>
        <p className="muted small">
          Double-click anywhere in the scene to walk there.
        </p>      </section>
    );
  }

  const attributes = Object.entries(metadata.attributes ?? {}).filter(
    ([, value]) => value !== null && value !== '',
  );

  const summary = (
    <div className="metadata-summary">
      <span
        className="chip small"
        style={{ background: CONFIDENCE_COLORS[metadata.confidence] }}
        title={CONFIDENCE_LABELS[metadata.confidence]}
      >
        {metadata.confidence}
      </span>
      <span className="metadata-summary-title">{metadata.display_name}</span>
    </div>
  );

  return (
    <CollapsiblePanel
      // Keyed by asset, so opening a new building always shows its details rather than inheriting
      // the collapsed state of the last one.
      key={metadata.asset_id}
      storageKey="metadata"
      className="panel metadata-panel"
      summary={summary}
      actions={
        onClose ? (
          <button className="icon-button" onClick={onClose} title="Clear selection">
            ✕
          </button>
        ) : undefined
      }
    >
      <p className="muted small confidence-note">{CONFIDENCE_LABELS[metadata.confidence]}</p>

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

      {evidence ? (
        <>
          <h3>Photographic evidence</h3>
          <p className="small">
            This building&rsquo;s appearance is taken from a photograph rather than inferred from its
            class and age, and is held against the procedural pass.
          </p>
          <dl className="kv">
            <div>
              <dt>Grade</dt>
              <dd>{evidence.observed_grade ?? '—'}</dd>
            </div>
            <div>
              <dt>Colour</dt>
              <dd>
                <span className="swatch" style={{ background: evidence.color }} />
                <span className="mono small"> {evidence.color}</span>
              </dd>
            </div>
            {evidence.colour_source ? (
              <div>
                <dt>Measured</dt>
                <dd className="small">
                  {evidence.colour_source === 'measured_from_this_photograph'
                    ? 'from this photograph'
                    : 'from the district corpus'}
                </dd>
              </div>
            ) : null}
          </dl>
          {evidence.attribution_text ? (
            <p className="small muted">{evidence.attribution_text}</p>
          ) : null}
        </>
      ) : null}
    </CollapsiblePanel>
  );
}
