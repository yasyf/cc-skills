export function merge<T>(...maps: (Record<string, T> | undefined)[]): Record<string, T> {
  const out: Record<string, T> = Object.create(null);
  for (const map of maps) {
    if (!map) continue;
    for (const [key, val] of Object.entries(map)) out[key] = val;
  }
  return out;
}

export function groupBy<T>(items: T[], key: (item: T) => string): Record<string, T[]> {
  const out: Record<string, T[]> = Object.create(null);
  for (const item of items) {
    const k = key(item);
    (out[k] ??= []).push(item);
  }
  return out;
}
