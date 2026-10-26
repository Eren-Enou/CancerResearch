import type { AnyRecord, Entity } from './types';

export type FieldSpec = {
  name: string; label: string;
  type?: 'text' | 'date' | 'number' | 'textarea' | 'select' | 'source' | 'study';
  options?: string[]; optional?: boolean;
};

export const sourceTypes = ['manual', 'lab', 'radiology', 'oncology-note', 'treatment', 'other'];
export const eventTypes = ['consultation', 'diagnosis', 'symptom', 'procedure', 'plan', 'other'];
export const modalities = ['CT', 'MRI', 'PET', 'US', 'other'];
/** Exact spellings the API accepts for registered tests. Other tests accept any well-formed unit. */
export const knownLabUnits: Record<string, string[]> = { 'CA-125': ['U/mL', 'kU/L'] };

export const entityLabel: Record<Entity, string> = {
  sources: 'source', events: 'event', labs: 'lab', treatments: 'treatment', studies: 'study', measurements: 'measurement',
};

const source: FieldSpec = { name: 'source_id', label: 'Original source', type: 'source' };
export const editableFields: Record<Entity, FieldSpec[]> = {
  sources: [
    { name: 'label', label: 'Source label' }, { name: 'kind', label: 'Document type', type: 'select', options: sourceTypes },
    { name: 'source_date', label: 'Document date', type: 'date', optional: true },
    { name: 'origin', label: 'Origin (fictional issuer)', optional: true }, { name: 'note', label: 'Notes', type: 'textarea', optional: true },
  ],
  events: [
    { name: 'date', label: 'Date', type: 'date' }, { name: 'kind', label: 'Type', type: 'select', options: eventTypes },
    { name: 'title', label: 'Title' }, { name: 'detail', label: 'Details', type: 'textarea', optional: true }, source,
  ],
  labs: [
    { name: 'date', label: 'Date', type: 'date' }, { name: 'test', label: 'Test' },
    { name: 'value', label: 'Value', type: 'number' }, { name: 'unit', label: 'Unit (as written in source)' }, source,
  ],
  treatments: [
    { name: 'date', label: 'Date', type: 'date' }, { name: 'regimen', label: 'Regimen' },
    { name: 'cycle', label: 'Cycle', type: 'number' }, { name: 'notes', label: 'Notes', type: 'textarea', optional: true }, source,
  ],
  studies: [
    { name: 'date', label: 'Date', type: 'date' }, { name: 'modality', label: 'Modality', type: 'select', options: modalities },
    { name: 'study_label', label: 'Study label' }, { name: 'report_text', label: 'Report text (verbatim)', type: 'textarea', optional: true }, source,
  ],
  measurements: [
    { name: 'study_id', label: 'Study', type: 'study' }, { name: 'lesion_key', label: 'Lesion key' }, { name: 'site', label: 'Documented location' },
    { name: 'long_axis_mm', label: 'Long axis (mm)', type: 'number' },
    { name: 'short_axis_mm', label: 'Short axis (mm)', type: 'number', optional: true },
    { name: 'notes', label: 'Notes', type: 'textarea', optional: true }, source,
  ],
};

const nullable = new Set(['source_date', 'short_axis_mm']);

export function toDraft(entity: Entity, record: AnyRecord): Record<string, string> {
  const values = record as unknown as Record<string, unknown>;
  return Object.fromEntries(editableFields[entity].map(f => [f.name, values[f.name] === null || values[f.name] === undefined ? '' : String(values[f.name])]));
}

/** Only fields whose value actually differs are sent, converted to the API's types. */
export function changedFields(entity: Entity, record: AnyRecord, draft: Record<string, string>): Record<string, string | number | null> {
  const original = toDraft(entity, record);
  const changes: Record<string, string | number | null> = {};
  for (const f of editableFields[entity]) {
    const next = draft[f.name] ?? '';
    if (next.trim() === original[f.name].trim()) continue;
    if (next.trim() === '' && nullable.has(f.name)) changes[f.name] = null;
    else if (f.type === 'number' || f.type === 'source' || f.type === 'study') changes[f.name] = Number(next);
    else changes[f.name] = next;
  }
  return changes;
}

/** Plain decimal text without exponent, matching the backend's report formatting. */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (Number.isInteger(value)) return String(value);
  const text = String(value);
  return /e/i.test(text) ? value.toFixed(20).replace(/0+$/, '').replace(/\.$/, '') : text;
}

export function formatDifference(value: number | null): string {
  if (value === null) return '—';
  return `${value > 0 ? '+' : ''}${formatNumber(value)}`;
}
