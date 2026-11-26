import { CorporateActionRecord, CorporateActionPosition } from '../../app/core/services/corporate-actions.service';

type Num = string | number | null | undefined;

export const RESTRUCTURING_TYPES = new Set(['MERGER', 'DEMERGER']);
export const TENDER_TYPES = new Set(['BUYBACK', 'TENDER_OFFER']);
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
  if (TENDER_TYPES.has(type)) {
    if (action.buyback_method === 'OPEN_MARKET') return 'Open-market buyback (no tender)';
    const word = type === 'BUYBACK' ? 'Buyback' : 'Offer';
    return toNumber(action.cash_per_unit) === null ? `${word} price not available` : `${word} at ${formatAmount(action.cash_per_unit)} per share`;
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

export function isTender(actionType: string): boolean {
  return TENDER_TYPES.has(actionType);
}

const TERM_NAMES: Record<string, string> = {
  old_units: 'old units', new_units: 'new units', cash_per_unit: 'price per share',
  cost_allocation_ratio: 'cost split', resulting_security_name: 'new company',
  resulting_isin: 'new company ISIN', resulting_symbol: 'new company symbol',
};

/** "new company: XYZ Ltd (NSE announcement #12 (2025-03-01))" lines for terms read automatically. */
export function announcementTermLines(action: CorporateActionRecord): string[] {
  const filled = action.terms_from_announcements || {};
  return Object.entries(filled).map(([field, item]) => {
    const value = field === 'cost_allocation_ratio' && toNumber(item?.value) !== null
      ? `${ratioToPercent(item.value)}% to the new company` : String(item?.value ?? '');
    return `${TERM_NAMES[field] || field}: ${value}${item?.source ? ` (${item.source})` : ''}`;
  });
}

/** 0.0468 -> "4.68"; '' when blank. */
export function ratioToPercent(value: Num): string {
  const parsed = toNumber(value);
  if (parsed === null) return '';
  return String(Number((parsed * 100).toFixed(6)));
}

/** "4.68" -> "0.0468"; '' when blank; null when not a percentage between 0 and 100. */
export function percentToRatio(value: string | null | undefined): string | null {
  const text = String(value ?? '').trim();
  if (!text) return '';
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed >= 100) return null;
  return String(Number((parsed / 100).toFixed(8)));
}

/** Positions that need the accepted share count before a buyback can be booked. */
export function quantityPositions(positions: CorporateActionPosition[] | undefined): CorporateActionPosition[] {
  return (positions || []).filter(position => position.status === 'PENDING' && position.needs_accepted_quantity);
}

/**
 * Accepted buyback quantities for the API, or an error message.
 * Every holding needs a whole number from 0 to its eligible quantity, and at least one must be above 0.
 */
export function acceptedQuantitiesPayload(
  positions: CorporateActionPosition[] | undefined, entered: Record<number, string | number | null | undefined>,
): {quantities: Record<number, number>} | {error: string} {
  const rows = quantityPositions(positions);
  const quantities: Record<number, number> = {};
  for (const position of rows) {
    const raw = entered[position.id];
    const label = `${position.family_name || 'Holding'} / ${position.portfolio || '—'}`;
    if (raw === undefined || raw === null || String(raw).trim() === '') {
      return {error: `Enter the shares accepted for ${label} (0 if it did not tender).`};
    }
    const value = Number(String(raw).trim());
    if (!Number.isInteger(value) || value < 0) return {error: `Shares accepted for ${label} must be a whole number.`};
    const eligible = toNumber(position.eligible_quantity) ?? 0;
    if (value > eligible) return {error: `${label} held ${formatQuantity(eligible)} shares; it cannot have more accepted.`};
    quantities[position.id] = value;
  }
  if (rows.length && Object.values(quantities).every(value => value === 0)) {
    return {error: 'Every quantity is 0. Use "Did not tender" instead; nothing will be booked.'};
  }
  return {quantities};
}

export function eligiblePositions(positions: CorporateActionPosition[] | undefined): CorporateActionPosition[] {
  return (positions || []).filter(position => position.status !== 'NOT_ELIGIBLE');
}

export function ineligiblePositions(positions: CorporateActionPosition[] | undefined): CorporateActionPosition[] {
  return (positions || []).filter(position => position.status === 'NOT_ELIGIBLE');
}

/** Number of ledger rows an acceptance would create (buyback holdings count once each). */
export function plannedEntryCount(positions: CorporateActionPosition[] | undefined): number {
  return (positions || [])
    .filter(position => position.status === 'PENDING')
    .reduce((total, position) => total + (position.needs_accepted_quantity ? 1 : position.planned_entries?.length || 0), 0);
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
