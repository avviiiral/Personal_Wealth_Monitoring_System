/**
 * Pure helpers for the Mutual Fund Intelligence page: ranking, quartiles,
 * formatting and the plain-language takeaways. Every statistic the page
 * shows is either sent by the backend or derived here from those values;
 * nothing is hard-coded.
 */

export type Quartile = 'Q1' | 'Q2' | 'Q3' | 'Q4';

export function isNum(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function clean(values: Array<number | null | undefined>): number[] {
  return values.filter(isNum);
}

export function average(values: Array<number | null | undefined>): number | null {
  const numbers = clean(values);
  return numbers.length ? numbers.reduce((sum, value) => sum + value, 0) / numbers.length : null;
}

/** 1-based rank of ``value`` among ``values`` (1 = best). Ties share the best rank. */
export function rankOf(values: Array<number | null | undefined>, value: number | null | undefined, higherIsBetter = true): number | null {
  if (!isNum(value)) return null;
  const numbers = clean(values);
  const better = numbers.filter((other) => (higherIsBetter ? other > value : other < value)).length;
  return better + 1;
}

export function quartileOf(values: Array<number | null | undefined>, value: number | null | undefined, higherIsBetter = true): Quartile | null {
  const rank = rankOf(values, value, higherIsBetter);
  const count = clean(values).length;
  if (rank === null || !count) return null;
  if (rank <= count / 4) return 'Q1';
  if (rank <= count / 2) return 'Q2';
  if (rank <= (3 * count) / 4) return 'Q3';
  return 'Q4';
}

export function histogram(values: Array<number | null | undefined>, edges: number[]): number[] {
  const counts = new Array(Math.max(edges.length - 1, 0)).fill(0);
  for (const value of clean(values)) {
    for (let index = 0; index < edges.length - 1; index++) {
      const last = index === edges.length - 2;
      if (value >= edges[index] && (value < edges[index + 1] || (last && value <= edges[index + 1]))) {
        counts[index]++;
        break;
      }
    }
  }
  return counts;
}

/** Evenly spaced, rounded bin edges covering every value. */
export function niceEdges(values: Array<number | null | undefined>, bins = 8): number[] {
  const numbers = clean(values);
  if (!numbers.length) return [];
  let min = Math.min(...numbers);
  let max = Math.max(...numbers);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const rawStep = (max - min) / bins;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= rawStep) ?? rawStep;
  const start = Math.floor(min / step) * step;
  const edges: number[] = [];
  for (let edge = start; edge < max + step / 2 && edges.length < 40; edge += step) {
    edges.push(Number(edge.toFixed(6)));
  }
  if (edges[edges.length - 1] < max) edges.push(Number((edges[edges.length - 1] + step).toFixed(6)));
  return edges;
}

export function fmt(value: number | null | undefined, digits = 2): string {
  return isNum(value) ? value.toFixed(digits) : '—';
}

export function pct(value: number | null | undefined, digits = 2): string {
  return isNum(value) ? `${value.toFixed(digits)}%` : '—';
}

export function signedPct(value: number | null | undefined, digits = 2): string {
  return isNum(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}%` : '—';
}

export function crore(value: number | null | undefined): string {
  if (!isNum(value)) return '—';
  return `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`;
}

export function escapeHtml(value: string | null | undefined): string {
  return (value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char] as string);
}

export function topBy<T>(items: T[], score: (item: T) => number | null | undefined, count: number, descending = true): T[] {
  return items
    .filter((item) => isNum(score(item)))
    .sort((a, b) => (descending ? (score(b) as number) - (score(a) as number) : (score(a) as number) - (score(b) as number)))
    .slice(0, count);
}

export function joinNames(names: string[]): string {
  const safe = names.map((name) => `<strong>${escapeHtml(name)}</strong>`);
  if (safe.length <= 1) return safe.join('');
  return `${safe.slice(0, -1).join(', ')} and ${safe[safe.length - 1]}`;
}

export interface SummaryPill {
  label: string;
  tone?: 'good' | 'bad' | 'accent' | '';
}

export interface Summary {
  html: string;
  pills: SummaryPill[];
}

export function trendLabel(q3m: Quartile | null, q6m: Quartile | null, q1y: Quartile | null): { label: string; tone: string } {
  if (!q3m || !q6m || !q1y) return { label: '—', tone: 'muted' };
  const order: Record<Quartile, number> = { Q1: 1, Q2: 2, Q3: 3, Q4: 4 };
  if (q3m === 'Q1' && q6m === 'Q1' && q1y === 'Q1') return { label: '★ Consistent', tone: 'accent' };
  if (order[q3m] < order[q1y]) return { label: '▲ Improving', tone: 'good' };
  if (order[q3m] > order[q1y]) return { label: '▼ Declining', tone: 'bad' };
  return { label: '→ Stable', tone: 'muted' };
}

/** Rebase a list of values so they sum to 100 (ignores nulls). */
export function rebase(values: Record<string, number | null | undefined>): Record<string, number> {
  const total = clean(Object.values(values)).reduce((sum, value) => sum + value, 0);
  const result: Record<string, number> = {};
  for (const [key, value] of Object.entries(values)) {
    result[key] = total && isNum(value) ? (value / total) * 100 : 0;
  }
  return result;
}

export const SCORE_LABELS: Record<string, string> = {
  returns: 'Returns',
  risk_adjusted: 'Risk-adjusted (Sortino)',
  consistency: 'Consistency (rolling P25)',
  downside: 'Downside protection',
  cost: 'Cost (TER)',
};

export const METRIC_LABELS: Record<string, string> = {
  trailing_cagr: 'CAGR',
  amfi_volatility: 'Volatility',
  beta: 'Beta',
  alpha: 'Alpha',
  correlation: 'Correlation',
  sharpe_ratio: 'Sharpe',
  sortino_ratio: 'Sortino',
  information_ratio: 'Information ratio',
  upside_capture: 'Upside capture',
  downside_capture: 'Downside capture',
  trailing_max_drawdown: 'Max drawdown',
  rolling_return_p50: 'Rolling return (median)',
  rolling_return_sd: 'Rolling return SD',
  trailing_xirr: 'SIP XIRR',
};

/** Metrics where a lower number is the better outcome. */
export const LOWER_IS_BETTER = new Set(['amfi_volatility', 'downside_capture', 'rolling_return_sd', 'ter', 'beta']);
