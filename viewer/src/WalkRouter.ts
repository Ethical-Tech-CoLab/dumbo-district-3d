/**
 * Pedestrian routing over the district walk network.
 *
 * Two jobs, both for the tour player:
 *   1. Route a leg that arrives without a path, so a tour authored as nothing but a list of stops
 *      still walks along real streets instead of straight through warehouses.
 *   2. Snap an arbitrary position onto the network, so the walking camera stays on ground a person
 *      could actually stand on.
 *
 * The graph is the OSM footway and street network built by scripts/build_district_assets.py.
 */

export interface WalkNetwork {
  nodes: [number, number][];
  edges: Array<{ a: number; b: number; len: number; kind: string; name?: string | null; stairs?: boolean }>;
  attribution: string;
}

interface Adjacency {
  to: number;
  cost: number;
  name?: string | null;
  stairs: boolean;
}

export interface RouteOptions {
  avoidStairs?: boolean;
  /** Multiplier applied to street edges, biasing routes onto dedicated footways. */
  streetPenalty?: number;
}

export class WalkRouter {
  private readonly nodes: [number, number][];
  private readonly adjacency: Adjacency[][];
  /** Uniform grid over node positions, so nearest-node lookup is not O(n) per query. */
  private readonly cellSize = 40;
  private readonly grid = new Map<string, number[]>();

  constructor(network: WalkNetwork, options: RouteOptions = {}) {
    this.nodes = network.nodes;
    this.adjacency = network.nodes.map(() => []);

    const streetPenalty = options.streetPenalty ?? 1.25;
    for (const edge of network.edges) {
      if (options.avoidStairs && edge.stairs) continue;
      const weight = edge.kind === 'footway' ? 1 : streetPenalty;
      const cost = edge.len * weight;
      this.adjacency[edge.a]?.push({ to: edge.b, cost, name: edge.name, stairs: !!edge.stairs });
      this.adjacency[edge.b]?.push({ to: edge.a, cost, name: edge.name, stairs: !!edge.stairs });
    }

    network.nodes.forEach((node, index) => {
      const key = this.cellKey(node[0], node[1]);
      const bucket = this.grid.get(key);
      if (bucket) bucket.push(index);
      else this.grid.set(key, [index]);
    });
  }

  private cellKey(x: number, y: number): string {
    return `${Math.floor(x / this.cellSize)},${Math.floor(y / this.cellSize)}`;
  }

  /** Index of the network node nearest to a scene position, searching outward by grid ring. */
  nearestNode(x: number, y: number): number {
    let best = -1;
    let bestDistance = Infinity;
    for (let ring = 0; ring <= 8; ring++) {
      const cx = Math.floor(x / this.cellSize);
      const cy = Math.floor(y / this.cellSize);
      for (let dx = -ring; dx <= ring; dx++) {
        for (let dy = -ring; dy <= ring; dy++) {
          if (ring > 0 && Math.abs(dx) !== ring && Math.abs(dy) !== ring) continue;
          for (const index of this.grid.get(`${cx + dx},${cy + dy}`) ?? []) {
            const node = this.nodes[index];
            const distance = (node[0] - x) ** 2 + (node[1] - y) ** 2;
            if (distance < bestDistance) {
              bestDistance = distance;
              best = index;
            }
          }
        }
      }
      if (best >= 0 && ring >= 1) break;
    }
    return best;
  }

  snap(x: number, y: number): [number, number] | null {
    const index = this.nearestNode(x, y);
    return index >= 0 ? this.nodes[index] : null;
  }

  /**
   * A* between two scene positions. Returns scene-space waypoints including the exact endpoints, so
   * a stop that sits off the network (a viewpoint at the end of a pier, say) is still reached.
   */
  route(from: [number, number, number], to: [number, number, number]): [number, number, number][] | null {
    const start = this.nearestNode(from[0], from[1]);
    const goal = this.nearestNode(to[0], to[1]);
    if (start < 0 || goal < 0) return null;
    if (start === goal) return [from, to];

    const heuristic = (index: number): number => {
      const node = this.nodes[index];
      const target = this.nodes[goal];
      return Math.hypot(node[0] - target[0], node[1] - target[1]);
    };

    const cameFrom = new Map<number, number>();
    const gScore = new Map<number, number>([[start, 0]]);
    // Small graph (a few thousand nodes), so a sorted-array frontier beats the complexity of a heap.
    const open: Array<{ index: number; f: number }> = [{ index: start, f: heuristic(start) }];
    const closed = new Set<number>();

    while (open.length) {
      open.sort((a, b) => a.f - b.f);
      const current = open.shift()!;
      if (current.index === goal) break;
      if (closed.has(current.index)) continue;
      closed.add(current.index);

      for (const edge of this.adjacency[current.index] ?? []) {
        if (closed.has(edge.to)) continue;
        const tentative = (gScore.get(current.index) ?? Infinity) + edge.cost;
        if (tentative >= (gScore.get(edge.to) ?? Infinity)) continue;
        cameFrom.set(edge.to, current.index);
        gScore.set(edge.to, tentative);
        open.push({ index: edge.to, f: tentative + heuristic(edge.to) });
      }
    }

    if (!cameFrom.has(goal)) return null;

    const path: number[] = [goal];
    let cursor = goal;
    while (cursor !== start) {
      const previous = cameFrom.get(cursor);
      if (previous === undefined) return null;
      cursor = previous;
      path.push(cursor);
    }
    path.reverse();

    const waypoints: [number, number, number][] = [from];
    for (const index of path) {
      const node = this.nodes[index];
      waypoints.push([node[0], node[1], 0]);
    }
    waypoints.push(to);
    return waypoints;
  }
}
