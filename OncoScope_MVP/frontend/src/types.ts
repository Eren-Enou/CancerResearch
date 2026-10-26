export type Entity = 'sources' | 'events' | 'labs' | 'treatments' | 'studies' | 'measurements';
export type VerificationStatus = 'entered' | 'reviewed' | 'verified';
export type Provenance = 'transcribed' | 'extracted' | 'user_observation';
/** Where in its source document an observation is written. Offsets index the extracted text layer of that page. */
export type Evidence = {
  source_id: number; provenance: Provenance; anchor_page: number | null; anchor_quote: string | null;
  anchor_start: number | null; anchor_end: number | null; anchor_region: string | null; extraction_id?: number | null;
};
export type RecordMeta = { id: number; uid: string; revision: number; verification_status: VerificationStatus; entered_at: string; updated_at: string | null };

export type Patient = { id: number; uid: string; label: string; is_synthetic: number };
export type Source = RecordMeta & {
  label: string; kind: string; source_date: string | null; note: string; origin: string;
  document_sha256: string | null; document_filename: string | null; document_media_type: string | null; document_size: number | null;
};
export type Event = RecordMeta & Evidence & { date: string; kind: string; title: string; detail: string; source_id: number };
export type Lab = RecordMeta & Evidence & { date: string; test: string; value: number; unit: string; source_id: number };
export type Treatment = RecordMeta & Evidence & { date: string; regimen: string; cycle: number; notes: string; source_id: number };
export type Study = RecordMeta & Evidence & { date: string; modality: string; study_label: string; report_text: string; source_id: number };
export type Measurement = RecordMeta & Evidence & { study_id: number; lesion_key: string; site: string; long_axis_mm: number; short_axis_mm: number | null; notes: string; source_id: number };
export type AnyRecord = Source | Event | Lab | Treatment | Study | Measurement;

export type TimelineEntry = Omit<Evidence, 'extraction_id'> & { id: number; uid: string; entity: Entity; kind: string; date: string; label: string; revision: number; verification_status: VerificationStatus };
export type Counts = Record<Entity, number>;
export type LabChange = { from_id: number; to_id: number; from_date: string; to_date: string; difference: number; unit: string };
export type LabSeries = { test: string; unit: string | null; available_units: string[]; points: Lab[]; excluded: Lab[]; changes?: LabChange[]; note: string };
export type AuditEntry = { id: number; entity_type: string; entity_id: number; patient_id: number; action: string; detail_json: string; reason: string | null; recorded_at: string; entry_hash: string };
export type AuditVerification = { valid: boolean; entries_checked: number; first_invalid_id: number | null };
export type ComparisonRow = {
  lesion_key: string; site: string; site_differs: boolean; baseline_site: string | null; followup_site: string | null;
  baseline_mm: number | null; followup_mm: number | null; difference_mm: number | null; status: string;
  baseline_measurement_id: number | null; followup_measurement_id: number | null;
  baseline_source_id: number | null; followup_source_id: number | null;
};
export type Comparison = { baseline: Study; followup: Study; interpretation: string; measurements: ComparisonRow[] };

export type DocumentText = { source_id: number; document_sha256: string; media_type: string; filename: string; extractor: string | null;
  pages: { page: number; text: string }[]; has_text_layer: boolean };
export type Issue = { code: string; message: string };
export type ExtractedFields = { test?: string; value?: number | null; unit?: string | null; date?: string | null; date_basis?: string; unit_as_written?: string; qualifier_note?: string };
export type Extraction = {
  id: number; uid: string; patient_id: number; source_id: number; document_sha256: string; parser: string; parser_version: string;
  page: number; start: number; end: number; original_text: string; proposed: ExtractedFields; issues: Issue[];
  status: 'pending' | 'accepted' | 'rejected'; accepted: ExtractedFields | null; decision_reason: string | null; decided_at: string | null;
  record_entity: string | null; record_id: number | null; created_at: string;
};
export type Finding = { id: string; kind: string; severity: 'conflict' | 'attention' | 'info'; message: string;
  records: { entity: string; id: number; source_id: number | null }[]; source_ids: number[] };
/** A request to show a document, optionally at the passage or region an observation cites. */
export type DocFocus = { sourceId: number; recordKey?: string };
/** Prefill handed from the document viewer to the entry form for source-anchored transcription. */
export type AnchorDraft = { kind: string; source_id: number; anchor_page: number | null; anchor_quote: string | null;
  anchor_start: number | null; anchor_end: number | null; anchor_region: string | null };
