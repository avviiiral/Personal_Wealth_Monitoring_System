import {
  average,
  clean,
  crore,
  escapeHtml,
  fmt,
  histogram,
  joinNames,
  niceEdges,
  pct,
  quartileOf,
  rankOf,
  rebase,
  signedPct,
  topBy,
  trendLabel,
} from './mf-intelligence.utils';

describe('mf-intelligence utils', () => {
  it('ignores null, undefined and non-finite values', () => {
    expect(clean([1, null, undefined, Number.NaN, Infinity, 2])).toEqual([1, 2]);
    expect(average([2, null, 4])).toBe(3);
    expect(average([])).toBeNull();
    expect(average([null, undefined])).toBeNull();
  });

  it('ranks with ties sharing the best position', () => {
    const values = [10, 8, 8, 5, null];
    expect(rankOf(values, 10)).toBe(1);
    expect(rankOf(values, 8)).toBe(2);
    expect(rankOf(values, 5)).toBe(4);
    expect(rankOf(values, 5, false)).toBe(1);
    expect(rankOf(values, null)).toBeNull();
    expect(rankOf([], 3)).toBe(1);
  });

  it('assigns quartiles at the 25/50/75% boundaries', () => {
    const values = [8, 7, 6, 5, 4, 3, 2, 1];
    expect(quartileOf(values, 8)).toBe('Q1');
    expect(quartileOf(values, 7)).toBe('Q1');
    expect(quartileOf(values, 6)).toBe('Q2'); // rank 3 of 8
    expect(quartileOf(values, 5)).toBe('Q2'); // rank 4 = 50% boundary
    expect(quartileOf(values, 4)).toBe('Q3'); // rank 5
    expect(quartileOf(values, 3)).toBe('Q3'); // rank 6 = 75% boundary
    expect(quartileOf(values, 2)).toBe('Q4');
    expect(quartileOf(values, 1)).toBe('Q4');
    expect(quartileOf(values, 1, false)).toBe('Q1');
    expect(quartileOf([], 1)).toBeNull();
    expect(quartileOf(values, null)).toBeNull();
  });

  it('builds histograms that include the top edge', () => {
    expect(histogram([0, 5, 9.9, 10, null], [0, 5, 10])).toEqual([1, 3]);
    expect(histogram([1], [])).toEqual([]);
    expect(histogram([-1, 11], [0, 5, 10])).toEqual([0, 0]);
  });

  it('chooses rounded bin edges covering all values', () => {
    const edges = niceEdges([-7.3, 2.1, 14.8], 8);
    expect(edges[0]).toBeLessThanOrEqual(-7.3);
    expect(edges[edges.length - 1]).toBeGreaterThanOrEqual(14.8);
    expect(niceEdges([])).toEqual([]);
    expect(niceEdges([3, 3]).length).toBeGreaterThan(1);
  });

  it('formats numbers, percentages and crores', () => {
    expect(fmt(1.2345)).toBe('1.23');
    expect(fmt(null)).toBe('—');
    expect(pct(12.5, 1)).toBe('12.5%');
    expect(signedPct(2)).toBe('+2.00%');
    expect(signedPct(-2)).toBe('-2.00%');
    expect(signedPct(0)).toBe('0.00%');
    expect(signedPct(undefined)).toBe('—');
    expect(crore(1234567)).toContain('Cr');
    expect(crore(null)).toBe('—');
  });

  it('escapes fund names placed into takeaway HTML', () => {
    expect(escapeHtml('<b>A & B</b>')).toBe('&lt;b&gt;A &amp; B&lt;/b&gt;');
    expect(escapeHtml(null)).toBe('');
    expect(joinNames(['A', 'B<script>', 'C'])).toBe('<strong>A</strong>, <strong>B&lt;script&gt;</strong> and <strong>C</strong>');
    expect(joinNames([])).toBe('');
  });

  it('picks top items and skips missing scores', () => {
    const items = [{ v: 3 }, { v: null }, { v: 9 }, { v: 1 }];
    expect(topBy(items, (item) => item.v, 2).map((item) => item.v)).toEqual([9, 3]);
    expect(topBy(items, (item) => item.v, 2, false).map((item) => item.v)).toEqual([1, 3]);
    expect(topBy([], () => 1, 3)).toEqual([]);
  });

  it('labels quartile trends', () => {
    expect(trendLabel('Q1', 'Q1', 'Q1').label).toContain('Consistent');
    expect(trendLabel('Q1', 'Q2', 'Q3').label).toContain('Improving');
    expect(trendLabel('Q4', 'Q2', 'Q1').label).toContain('Declining');
    expect(trendLabel('Q2', 'Q1', 'Q2').label).toContain('Stable');
    expect(trendLabel(null, 'Q1', 'Q1').label).toBe('—');
  });

  it('rebases values to 100', () => {
    expect(rebase({ a: 1, b: 3 })).toEqual({ a: 25, b: 75 });
    expect(rebase({ a: 0, b: null })).toEqual({ a: 0, b: 0 });
  });
});
