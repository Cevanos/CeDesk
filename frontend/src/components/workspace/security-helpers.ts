/** One IP or CIDR per line (commas and spaces also split); blanks and duplicates dropped. */
export function parseIpList(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).filter(Boolean))];
}

/** A whole number within [min, max], or null. */
export function parseIntIn(s: string, min: number, max: number): number | null {
  const t = s.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= min && n <= max ? n : null;
}
