import {
  apiErrorMessage,
  costAllocationLabel,
  describeTerms,
  eligiblePositions,
  formatAmount,
  formatQuantity,
  ineligiblePositions,
  plannedEntryCount,
  resultingSecurityLabel,
  termsPatch,
} from './corporate-actions.utils';

const action = (overrides: Record<string, unknown> = {}) => ({
  id: 1, action_type: 'STOCK_SPLIT', security_name: 'ABC Ltd', isin: 'INE000ABC011', symbol: 'ABC',
  exchange: 'NSE', ex_date: '2025-06-15', record_date: null, payment_date: null,
  old_units: '1', new_units: '2', cash_per_unit: null, source: 'NSE', source_reference: '',
  status: 'PENDING', notes: '', quantity_factor: null, ...overrides,
}) as any;

const position = (status: string, entries = 1) => ({ status, planned_entries: Array(entries).fill({}) }) as any;

describe('corporate-actions utils', () => {
  it('describes each entitlement convention without guessing missing terms', () => {
    expect(describeTerms(action({ action_type: 'BONUS', old_units: '1', new_units: '2' })))
      .toBe('2 new share(s) for every 1 held (1:2)');
    expect(describeTerms(action({ action_type: 'REVERSE_SPLIT', old_units: '10', new_units: '1' })))
      .toBe('10 share(s) become 1 (10:1)');
    expect(describeTerms(action({ action_type: 'CASH_DIVIDEND', cash_per_unit: '2.5' }))).toContain('2.50 per share');
    expect(describeTerms(action({ action_type: 'CASH_DIVIDEND', cash_per_unit: null }))).toBe('Dividend per share not available');
    expect(describeTerms(action({ old_units: null }))).toBe('Ratio not available — verify the source');
  });

  it('formats quantities and amounts, signing adjustments', () => {
    expect(formatQuantity('-900')).toBe('-900');
    expect(formatQuantity('200', true)).toBe('+200');
    expect(formatQuantity('0.500000')).toBe('0.5');
    expect(formatQuantity(null)).toBe('—');
    expect(formatQuantity('')).toBe('—');
    expect(formatAmount('2500.0000')).toBe('₹2,500.00');
    expect(formatAmount(undefined)).toBe('—');
  });

  it('labels resulting securities and cost allocation for restructurings', () => {
    expect(resultingSecurityLabel(action({ resulting_security_name: 'XYZ Ltd', resulting_isin: 'INE000XYZ022' })))
      .toBe('XYZ Ltd · INE000XYZ022');
    expect(resultingSecurityLabel(action())).toBe('Not available');
    expect(costAllocationLabel(action({ action_type: 'DEMERGER', cost_allocation_ratio: '0.25' }))).toBe('25% of the original cost');
    expect(costAllocationLabel(action({ action_type: 'DEMERGER' }))).toBe('Not available');
    expect(costAllocationLabel(action({ action_type: 'MERGER' }))).toContain('100% carried');
    expect(costAllocationLabel(action({ action_type: 'MERGER', cash_per_unit: '10' }))).toBe('Not available');
  });

  it('separates eligible positions and counts only the rows acceptance will create', () => {
    const rows = [position('PENDING', 2), position('ALREADY_RECORDED'), position('NOT_ELIGIBLE'), position('MANUAL_REVIEW')];
    expect(eligiblePositions(rows).length).toBe(3);
    expect(ineligiblePositions(rows).length).toBe(1);
    expect(plannedEntryCount(rows)).toBe(2);
    expect(plannedEntryCount(undefined)).toBe(0);
    expect(eligiblePositions(undefined)).toEqual([]);
  });

  it('reads DRF error shapes', () => {
    expect(apiErrorMessage({ error: { detail: 'Stale proposal.' } }, 'x')).toBe('Stale proposal.');
    expect(apiErrorMessage({ error: { detail: ['A.', 'B.'] } }, 'x')).toBe('A. B.');
    expect(apiErrorMessage({ error: { detail: { cost_allocation_ratio: ['Bad.'] } } }, 'x')).toBe('cost_allocation_ratio: Bad.');
    expect(apiErrorMessage({}, 'fallback')).toBe('fallback');
    expect(apiErrorMessage(null, 'fallback')).toBe('fallback');
  });

  it('sends only changed terms and clears blanks with the right empty value', () => {
    const original = { old_units: '1.00000000', new_units: '2', cost_allocation_ratio: null, resulting_isin: 'INE000XYZ022', record_date: null };
    expect(termsPatch(original, { old_units: '1', new_units: '2', cost_allocation_ratio: '', resulting_isin: 'INE000XYZ022' })).toEqual({});
    expect(termsPatch(original, { cost_allocation_ratio: '0.25', record_date: '2025-06-10' }))
      .toEqual({ cost_allocation_ratio: '0.25', record_date: '2025-06-10' });
    expect(termsPatch(original, { new_units: '' })).toEqual({ new_units: null });
    expect(termsPatch(original, { resulting_isin: '' })).toEqual({ resulting_isin: '' });
    expect(termsPatch(original, { resulting_isin: ' ine000new033 ' })).toEqual({ resulting_isin: 'INE000NEW033' });
  });
});
