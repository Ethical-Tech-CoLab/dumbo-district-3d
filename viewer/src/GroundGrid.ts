/**
 * Sampler for the interpolated ground height grid.
 *
 * The grid is built from building base elevations, which DSRC-003 defines as authoritative NAVD88
 * ground samples. It is graded C and carries DOQ-003: the samples are real, the surface between
 * them is inferred. It exists because DUMBO climbs about 23 m from the waterfront, so a flat scene
 * would put the walking camera metres underground for most of the district.
 */

export interface GroundGridDocument {
  origin_xy_m: [number, number];
  cell_m: number;
  cols: number;
  rows: number;
  min_m: number;
  max_m: number;
  vertical_datum: string;
  confidence: string;
  heights: number[][];
  /** 1 where the cell is land, 0 where it is water. Absent means all land. */
  land?: number[][];
}

export class GroundGrid {
  readonly doc: GroundGridDocument;

  constructor(doc: GroundGridDocument) {
    this.doc = doc;
  }

  /** Bilinear sample of ground height, in scene meters, clamped at the grid edge. */
  heightAt(x: number, y: number): number {
    const { origin_xy_m, cell_m, cols, rows, heights } = this.doc;
    const fx = (x - origin_xy_m[0]) / cell_m;
    const fy = (y - origin_xy_m[1]) / cell_m;

    const c0 = Math.max(0, Math.min(cols - 1, Math.floor(fx)));
    const r0 = Math.max(0, Math.min(rows - 1, Math.floor(fy)));
    const c1 = Math.min(cols - 1, c0 + 1);
    const r1 = Math.min(rows - 1, r0 + 1);

    const tx = Math.max(0, Math.min(1, fx - c0));
    const ty = Math.max(0, Math.min(1, fy - r0));

    const h00 = heights[r0][c0];
    const h10 = heights[r0][c1];
    const h01 = heights[r1][c0];
    const h11 = heights[r1][c1];

    return (
      h00 * (1 - tx) * (1 - ty) +
      h10 * tx * (1 - ty) +
      h01 * (1 - tx) * ty +
      h11 * tx * ty
    );
  }

  /** Whether a grid cell is land. Water cells are omitted from the terrain mesh. */
  isLand(col: number, row: number): boolean {
    const land = this.doc.land;
    if (!land) return true;
    return (land[row]?.[col] ?? 0) === 1;
  }
}
