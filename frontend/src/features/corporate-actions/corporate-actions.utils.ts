import { CorporateActionRecord, CorporateActionPosition } from '../../app/core/services/corporate-actions.service';

type Num = string | number | null | undefined;

export const RESTRUCTURING_TYPES = new Set(['MERGER', 'DEMERGER']);
const BONUS_TYPES = new Set(['BONUS', 'STOCK_DIVIDEND']);
const SPLIT_TYPES = new Set(['STOCK_SPLIT', 'REVERSE_SPLIT', 'ADR_RATIO_CHANGE']);

export const RECONCILIATION_STATUS_LABELS: Record<string, string> = {
  PENDING: 'Pending review',
  PENDING_USER_APPROVAL: 'Awaiting identity approval',
  ALREADY_RECORDED: 'Already recorded',
  MANUAL_REVIEW: 'Manual review',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  COMPLETED: 'Completed',
  DISMISSED: 'Dismissed',
};

export const POSITION_STATUS_LABELS: Record<string, string> = {
  PENDING: 'To be booked',
  ALREADY_RECORDED: 'Already recorded',
  MANUAL_REVIEW: 'Manual review',
  NOT_ELIGIBLE: 'Not eligible',
  APPLIED: 'Booked',
  REJECTED: 'Rejected',
};

export function toNumber(value: Num): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Quantity with up to 6 decimals and no trailing zeros; '—' when unknown. */
export function formatQuantity(value: Num, signed = false): string {
  const parsed = toNumber(value);
  if (parsed === null) return '—';
  const text = parsed.toLocaleString('en-IN', { maximumFractionDigits: 6 });
  return signed && parsed > 0 ? `+${text}` : text;
}

/** Amount in rupees with 2 decimals for display (the backend keeps 4). */
export function formatAmount(value: Num): string {
  const parsed = toNumber(value);
  if (parsed === null) return '—';
  return `₹${parsed.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function plain(value: Num): string {
  const parsed = toNumber(value);
  return parsed === null ? '' : String(parsed);
}

/** Human-readable statement of the stored entitlement terms. */
export function describeTerms(action: CorporateActionRecord): string {
  const oldUnits = plain(action.old_units);
  const newUnits = plain(action.new_units);
  const type = action.action_type;
  if (type === 'CASH_DIVIDEND') {
    return toNumber(action.cash_per_unit) === null ? 'Dividend per share not available' : `${formatAmount(action.cash_per_unit)} per share`;
  }
  if (!oldUnits || !newUnits) return 'Ratio not available — verify the source';
  if (BONUS_TYPES.has(type)) return `${newUnits} new share(s) for every ${oldUnits} held (${oldUnits}:${newUnits})`;
  if (SPLIT_TYPES.has(type)) return `${oldUnits} share(s) become ${newUnits} (${oldUnits}:${newUnits})`;
  if (type === 'MERGER') return `${newUnits} successor share(s) for every ${oldUnits} held`;
  if (type === 'DEMERGER') return `${newUnits} new-company share(s) for every ${oldUnits} held`;
  return `${oldUnits}:${newUnits}`;
}

export function resultingSecurityLabel(action: CorporateActionRecord): string {
  const name = action.resulting_security_name || action.resulting_symbol || '';
  const isin = action.resulting_isin || '';
  if (!name && !isin) return 'Not available';
  return isin ? `${name || 'Resulting security'} · ${isin}` : name;
}

export function costAllocationLabel(action: CorporateActionRecord): string {
  const ratio = toNumber(action.cost_allocation_ratio);
  if (ratio === null) return action.action_type === 'MERGER' && !toNumber(action.cash_per_unit)
    ? '100% carried to the successor (share-for-share)' : 'Not available';
  return `${(ratio * 100).toLocaleString('en-IN', { maximumFractionDigits: 4 })}% of the original cost`;
}

export function isRestructuring(actionType: string): boolean {
  return RESTRUCTURING_TYPES.has(actionType);
}

export function eligiblePositions(positions: CorporateActionPosition[] | undefined): CorporateActionPosition[] {
  return (positions || []).filter(position => position.status !== 'NOT_ELIGIBLE');
}

export function ineligiblePositions(positions: CorporateActionPosition[] | undefined): CorporateActionPosition[] {
  return (positions || []).filter(position => position.status === 'NOT_ELIGIBLE');
}

/** Number of ledger rows an acceptance would create. */
export function plannedEntryCount(positions: CorporateActionPosition[] | undefined): number {
  return (positions || [])
    .filter(position => position.status === 'PENDING')
    .reduce((total, position) => total + (position.planned_entries?.length || 0), 0);
}

/** Readable message from a DRF error response. */
export function apiErrorMessage(error: any, fallback: string): string {
  const detail = error?.error?.detail ?? error?.error;
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.join(' ');
  if (typeof detail === 'object') {
    return Object.entries(detail)
      .map(([key, value]) => `${key === '__all__' ? '' : `${key}: `}${Array.isArray(value) ? value.join(' ') : value}`)
      .join(' ');
  }
  return fallback;
}

const TEXT_TERM_FIELDS = new Set(['resulting_isin', 'resulting_symbol', 'resulting_security_name']);

/** Only changed term fields. A blank input clears the value: '' for text, null otherwise. */
export function termsPatch(
  original: Record<string, unknown>, edited: Record<string, string | null | undefined>,
): Record<string, string | null> {
  const patch: Record<string, string | null> = {};
  for (const [key, raw] of Object.entries(edited)) {
    let value = raw === undefined || raw === null ? '' : String(raw).trim();
    if (key === 'resulting_isin' || key === 'resulting_symbol') value = value.toUpperCase();
    const before = original[key] === undefined || original[key] === null ? '' : String(original[key]);
    const numericSame = value !== '' && before !== '' && Number(value) === Number(before);
    if (value === before || numericSame) continue;
    patch[key] = value === '' ? (TEXT_TERM_FIELDS.has(key) ? '' : null) : value;
  }
  return patch;
}
