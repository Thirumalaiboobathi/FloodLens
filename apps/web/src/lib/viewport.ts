// Viewport helpers: snap map bounds to the API's 0.05-degree road tile grid so equal views share one
// cache entry, and debounce requests while the map is moving.

export const TILE_DEG = 0.05;
export const MIN_VECTOR_ZOOM = 11; // below this, facilities and roads are not requested at all
export type Bounds = [west: number, south: number, east: number, north: number];

const r5 = (n: number) => Math.round(n * 1e5) / 1e5;

export function snapBounds(b: Bounds, tile = TILE_DEG): Bounds {
  const [w, s, e, n] = b;
  return [r5(Math.floor(w / tile) * tile), r5(Math.floor(s / tile) * tile),
          r5((Math.floor(e / tile) + 1) * tile), r5((Math.floor(n / tile) + 1) * tile)];
}

export const bboxParam = (b: Bounds) => b.join(",");

/** Cache key for a vector request; identical for every viewport inside the same snapped area. */
export function vectorKey(kind: string, scenario: string, b: Bounds, zoom: number): string {
  return [kind, scenario, bboxParam(snapBounds(b)), zoom < 13 ? "overview" : "full"].join("|");
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number) {
  let t: ReturnType<typeof setTimeout> | undefined;
  const d = (...a: A) => { if (t) clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => { if (t) clearTimeout(t); };
  return d;
}

/** Tiny keyed cache with a size cap (oldest dropped) so panning never grows memory without bound. */
export class LruCache<V> {
  private m = new Map<string, V>();
  constructor(private max = 40) {}
  get(k: string): V | undefined {
    const v = this.m.get(k);
    if (v !== undefined) { this.m.delete(k); this.m.set(k, v); }
    return v;
  }
  set(k: string, v: V) {
    this.m.delete(k); this.m.set(k, v);
    if (this.m.size > this.max) this.m.delete(this.m.keys().next().value as string);
  }
  has(k: string) { return this.m.has(k); }
  get size() { return this.m.size; }
}
