export type Entity = 'sources' | 'events' | 'labs' | 'treatments' | 'studies' | 'measurements';
export type VerificationStatus = 'entered' | 'reviewed' | 'verified';
export type RecordMeta = { id: number; uid: string; revision: number; verification_status: VerificationStatus; entered_at: string; updated_at: string | null };

export type Patient = { id: number; uid: string; label: string; is_synthetic: number };
export type Source = RecordMeta & {
  label: string; kind: string; source_date: string | null; note: string; origin: string;
  document_sha256: string | null; document_filename: string | null; document_media_type: string | null; document_size: number | null;
};
export type Event = RecordMeta & { date: string; kind: string; title: string; detail: string; source_id: number };
export type Lab = RecordMeta & { date: string; test: string; value: number; unit: string; source_id: number };
export type Treatment = RecordMeta & { date: string; regimen: string; cycle: number; notes: string; source_id: number };
export type Study = RecordMeta & { date: string; modality: string; study_label: string; report_text: string; source_id: number };
export type Measurement = RecordMeta & { study_id: number; lesion_key: string; site: string; long_axis_mm: number; short_axis_mm: number | null; notes: string; source_id: number };
export type AnyRecord = Source | Event | Lab | Treatment | Study | Measurement;

export type TimelineEntry = { id: number; uid: string; entity: Entity; kind: string; date: string; label: string; source_id: number; revision: number; verification_status: VerificationStatus };
export type Counts = Record<Entity, number>;
export type LabSeries = { test: string; unit: string | null; available_units: string[]; points: Lab[]; excluded: Lab[]; note: string };
export type AuditEntry = { id: number; entity_type: string; entity_id: number; patient_id: number; action: string; detail_json: string; reason: string | null; recorded_at: string; entry_hash: string };
export type AuditVerification = { valid: boolean; entries_checked: number; first_invalid_id: number | null };
export type ComparisonRow = {
  lesion_key: string; site: string; site_differs: boolean; baseline_site: string | null; followup_site: string | null;
  baseline_mm: number | null; followup_mm: number | null; difference_mm: number | null; status: string;
  baseline_measurement_id: number | null; followup_measurement_id: number | null;
  baseline_source_id: number | null; followup_source_id: number | null;
};
export type Comparison = { baseline: Study; followup: Study; interpretation: string; measurements: ComparisonRow[] };
