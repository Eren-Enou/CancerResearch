import { useEffect, useState } from 'react';
import { request } from './api';
import { AddRecord, CreateCase } from './components/AddRecord';
import { DocumentViewer } from './components/DocumentViewer';
import { EvidenceView, locationText } from './components/EvidenceView';
import { HistoryView } from './components/HistoryView';
import { ImportDocument } from './components/ImportDocument';
import { ReviewQueue } from './components/ReviewQueue';
import { LabChart } from './components/LabChart';
import { AuditView, SourceReference, SourcesView } from './components/Panels';
import { RecordTools, StatusBadge } from './components/RecordTools';
import { formatDifference, formatNumber, provenanceLabel } from './records';
import type { AnchorDraft, DocFocus, Evidence, Extraction, AnyRecord, Comparison, Counts, Entity, Event, Lab, LabSeries, Measurement, Patient, Source, Study, TimelineEntry, Treatment } from './types';

type View = 'Overview' | 'History' | 'Timeline' | 'Laboratory' | 'Imaging' | 'Documents' | 'Review' | 'Evidence' | 'Sources' | 'Audit' | 'Add record';
const views: View[] = ['Overview', 'History', 'Timeline', 'Laboratory', 'Imaging', 'Documents', 'Review', 'Evidence', 'Sources', 'Audit', 'Add record'];
const icons = ['◫', '◷', '≡', '⌁', '▧', '▯', '✓', '⚑', '▤', '⎘', '+'];

/** The selected case and view live in the URL hash (e.g. #case=3&view=History) so a reload keeps the user's place. */
function readHash(): { view: View; caseId: number | null } {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const view = views.find(v => v === params.get('view')) ?? 'Overview';
  const caseId = Number(params.get('case'));
  return { view, caseId: Number.isInteger(caseId) && caseId > 0 ? caseId : null };
}

function ProvenanceBadge({ record }: { record: Pick<Evidence, 'provenance'> }) {
  return <span className={`badge prov-${record.provenance}`}>{provenanceLabel[record.provenance]}</span>;
}

function Metric({ label, value, sub }: { label: string; value: number; sub: string }) {
  return <div className="metric"><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="muted tiny">{sub}</div></div>;
}

export default function App() {
  const [view, setView] = useState<View>(() => readHash().view);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [patientId, setPatientId] = useState<number | null>(() => readHash().caseId);
  const [sources, setSources] = useState<Source[]>([]);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [series, setSeries] = useState<LabSeries | null>(null);
  const [treatments, setTreatments] = useState<Treatment[]>([]);
  const [studies, setStudies] = useState<Study[]>([]);
  const [measurements, setMeasurements] = useState<Measurement[]>([]);
  const [counts, setCounts] = useState<Counts | null>(null);
  const [baselineId, setBaselineId] = useState('');
  const [followupId, setFollowupId] = useState('');
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [comparisonError, setComparisonError] = useState('');
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [docFocus, setDocFocus] = useState<DocFocus | null>(null);
  const [anchorDraft, setAnchorDraft] = useState<AnchorDraft | null>(null);
  const [pendingReviews, setPendingReviews] = useState(0);
  const reload = () => setRefresh(x => x + 1);
  useEffect(() => {
    const onHash = () => { const h = readHash(); setView(h.view); if (h.caseId) setPatientId(h.caseId); };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => {
    const hash = `#case=${patientId ?? ''}&view=${encodeURIComponent(view)}`;
    if (window.location.hash !== hash) window.history.replaceState(null, '', hash);
  }, [patientId, view]);
  const patient = patients.find(p => p.id === patientId);
  const bySource = (id: number) => <SourceReference id={id} sources={sources} />;

  useEffect(() => {
    request<Patient[]>('/patients').then(all => {
      setPatients(all); setLoaded(true);
      setPatientId(p => (p !== null && all.some(x => x.id === p)) ? p : all[0]?.id ?? null);
    }).catch(e => setError(`Could not reach local API: ${e.message}`));
  }, [refresh]);

  useEffect(() => {
    if (patientId === null) return;
    Promise.all([
      request<{ counts: Counts }>(`/patients/${patientId}/dashboard`),
      request<Source[]>(`/patients/${patientId}/sources`),
      request<TimelineEntry[]>(`/patients/${patientId}/timeline`),
      request<Event[]>(`/patients/${patientId}/events`),
      request<Lab[]>(`/patients/${patientId}/labs`),
      request<Treatment[]>(`/patients/${patientId}/treatments`),
      request<Study[]>(`/patients/${patientId}/studies`),
      request<Measurement[]>(`/patients/${patientId}/measurements`),
      request<LabSeries>(`/patients/${patientId}/labs/series?test=CA-125`),
      request<Extraction[]>(`/patients/${patientId}/extractions?status=pending`),
    ]).then(([dash, src, time, ev, lab, tx, scans, mm, ser, pending]) => {
      setCounts(dash.counts); setSources(src); setTimeline(time); setEvents(ev); setLabs(lab); setTreatments(tx);
      setStudies(scans); setMeasurements(mm); setSeries(ser); setPendingReviews(pending.length); setError('');
    }).catch(e => setError(e.message));
  }, [patientId, refresh]);

  // Default the comparison to the earliest and latest study once studies change in number.
  useEffect(() => {
    const ordered = [...studies].sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id);
    if (ordered.length > 1) {
      setBaselineId(b => ordered.some(s => String(s.id) === b) ? b : String(ordered[0].id));
      setFollowupId(f => ordered.some(s => String(s.id) === f) ? f : String(ordered[ordered.length - 1].id));
    } else { setBaselineId(''); setFollowupId(''); }
  }, [studies]);

  useEffect(() => {
    setComparison(null); setComparisonError('');
    if (!patientId || !baselineId || !followupId) return;
    if (baselineId === followupId) { setComparisonError('Select two different studies.'); return; }
    request<Comparison>(`/patients/${patientId}/compare?baseline_id=${baselineId}&followup_id=${followupId}`)
      .then(setComparison).catch(e => setComparisonError(e.message));
  }, [patientId, baselineId, followupId, refresh]);

  const records: Record<Entity, AnyRecord[]> = { sources, events, labs, treatments, studies, measurements };
  const lookup = (entity: Entity, id: number) => records[entity].find(r => r.id === id);
  const citations: Record<number, { entity: Entity; record: AnyRecord }[]> = {};
  for (const entity of ['events', 'labs', 'treatments', 'studies', 'measurements'] as Entity[]) {
    for (const record of records[entity] as (AnyRecord & { source_id: number })[]) (citations[record.source_id] ??= []).push({ entity, record });
  }
  const tools = (entity: Entity, record: AnyRecord | undefined) => record &&
    <RecordTools key={`${entity}-${record.id}-${record.revision}-${record.verification_status}`} entity={entity} record={record} sources={sources} studies={studies} onSaved={reload} />;
  const studyById = (id: number) => studies.find(s => s.id === id);
  const openDocument = (sourceId: number, recordKey?: string) => { setDocFocus({ sourceId, recordKey }); setView('Documents'); };
  // Every observation links to its supporting document, at the cited passage or region when one was recorded.
  const evidenceLink = (entity: Entity, r: Evidence & { id: number }) => {
    const src = sources.find(s => s.id === r.source_id);
    if (!src?.document_sha256 || r.provenance === 'user_observation') return null;
    return <button type="button" className="text-button evidence-link" aria-label={`Show evidence for ${entity} #${r.id}`} onClick={() => openDocument(r.source_id, `${entity}-${r.id}`)}>
      📄 {locationText(r)}</button>;
  };
  const docSources = sources.filter(s => s.document_sha256);
  const viewerSource = sources.find(s => s.id === docFocus?.sourceId) ?? docSources[0];
  const timelineRow = (t: TimelineEntry, withTools: boolean) => <div className="timeline-row" key={`${t.entity}-${t.id}`} data-testid="timeline-row" data-entity={t.entity} data-id={t.id}>
    <span className="timeline-dot" />
    <div className="grow"><span className="date-label">{t.date} · {t.kind.toUpperCase()} #{t.id}</span> <StatusBadge record={t} /> <ProvenanceBadge record={t} />
      <div className="row-title">{t.label}</div><div className="muted tiny">{bySource(t.source_id)} {evidenceLink(t.entity, t)}</div>
      {withTools && tools(t.entity, lookup(t.entity, t.id))}
    </div></div>;
  const exportBase = patientId ? `/api/patients/${patientId}` : '';

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">◎</div><div><strong>ONCOSCOPE</strong><span>LOCAL RESEARCH WORKSPACE</span></div></div>
      <p className="nav-label">WORKSPACE</p>
      <nav aria-label="Main navigation">{views.map((v, i) => <button className={`nav-item ${view === v ? 'active' : ''}`} aria-current={view === v ? 'page' : undefined} key={v} onClick={() => { setView(v); setAnchorDraft(null); }}><span className="nav-icon" aria-hidden="true">{icons[i]}</span>{v}{v === 'Review' && pendingReviews > 0 && <span className="nav-count" aria-hidden="true">{pendingReviews}</span>}</button>)}</nav>
      <div className="sidebar-footer"><span className="local-dot" /> Local-only prototype<p>Phase 1 · M2.6 · v0.2.6<br />No automated interpretation</p></div>
    </aside>
    <main className="main">
      <header className="topbar"><div><div className="eyebrow">CLINICAL RECORDS / {view.toUpperCase()}</div><h1>{view === 'Overview' ? 'Patient overview' : view === 'History' ? 'Case history' : view}</h1></div>
        <div className="top-actions"><span className="status-label">● Local data</span>
          {patient && <><a href={`${exportBase}/report.md`} target="_blank" rel="noreferrer" className="export-link">Markdown report ↗</a>
            <a href={`${exportBase}/export.json`} className="export-link">Export JSON</a></>}</div></header>
      <div className="body-content">
        {error && <div className="error-banner" role="alert">{error}. Start the FastAPI server at 127.0.0.1:8000.</div>}
        {loaded && !patient && <section className="panel empty"><h2>No case yet</h2><p className="muted">This local database is empty. Create a fictional case to begin entering synthetic sources and observations.</p>
          <CreateCase onCreated={id => { setPatientId(id); reload(); setView('Add record'); }} /></section>}
        {patient && <>
          <div className="patient-strip"><div><span className="strip-label">CURRENT RECORD</span><strong>{patient.label}</strong><span className="strip-description">ID #{patient.id} · Synthetic dataset</span></div>
            {patients.length > 1 && <label className="case-picker">Case<select value={patient.id} onChange={e => setPatientId(Number(e.target.value))}>{patients.map(p => <option key={p.id} value={p.id}>#{p.id} · {p.label}</option>)}</select></label>}
            <span className="synthetic-tag">SYNTHETIC DATA — NOT A REAL PATIENT</span></div>
          {view === 'Overview' && <>
            <div className="metric-grid"><Metric label="Clinical events" value={counts?.events ?? 0} sub="Chronological entries" /><Metric label="Laboratory results" value={counts?.labs ?? 0} sub="Source-linked readings" /><Metric label="Treatment entries" value={counts?.treatments ?? 0} sub="Documented cycles" /><Metric label="Imaging studies" value={counts?.studies ?? 0} sub="Reports and metadata" /></div>
            <section className="panel chart-panel"><div className="section-heading"><div><div className="eyebrow">LABORATORY TREND</div><h2>CA-125 measurements</h2></div><span className="small-pill">{series?.unit ?? 'U/mL'}</span></div><LabChart series={series} /></section>
            <div className="two-col"><section className="panel"><div className="section-heading"><h2>Recent timeline</h2><button className="text-button" onClick={() => setView('Timeline')}>All events →</button></div><div className="timeline-list">{timeline.slice(0, 6).map(t => timelineRow(t, false))}</div></section>
              <section className="panel"><div className="section-heading"><h2>Record integrity</h2></div><div className="integrity-line"><span>Source documents</span><strong>{counts?.sources ?? 0}</strong></div><div className="integrity-line"><span>Sources with attached file</span><strong>{sources.filter(s => s.document_sha256).length}</strong></div><div className="integrity-line"><span>Recorded measurements</span><strong>{counts?.measurements ?? 0}</strong></div><div className="integrity-line"><span>Dataset type</span><strong>Synthetic</strong></div><div className="integrity-note"><strong>Important limitation</strong><p>Observed changes are descriptive only. This software does not calculate stage, assign RECIST response, diagnose disease, or recommend treatment.</p></div></section></div>
          </>}
          {view === 'Timeline' && <section className="panel"><div className="section-heading"><div><h2>Longitudinal medical timeline</h2><p className="muted">Events, therapy administrations, laboratory observations, studies and lesion measurements (dated by their study) on one axis. Use Correct / History on any entry.</p></div></div>
            {timeline.length ? <div className="timeline-list full">{timeline.map(t => timelineRow(t, true))}</div> : <p className="muted">No records yet.</p>}</section>}
          {view === 'Laboratory' && <><section className="panel chart-panel"><div className="section-heading"><div><h2>CA-125 over time</h2><p className="muted">Units and values are preserved exactly as entered.</p></div></div><LabChart series={series} /></section>
            <section className="panel"><h2>All laboratory results</h2><div className="table-scroll"><table><thead><tr><th>Lab</th><th>Date</th><th>Test</th><th>Value</th><th>Unit</th><th>State</th><th>Source</th><th>Action</th></tr></thead><tbody>{labs.map(l => <tr key={l.id} data-testid="lab-row" data-id={l.id}><td>#{l.id}</td><td>{l.date}</td><td>{l.test}</td><td className="numeric">{formatNumber(l.value)}</td><td>{l.unit}</td><td><StatusBadge record={l} /></td><td>{bySource(l.source_id)} <ProvenanceBadge record={l} /> {evidenceLink('labs', l)}</td><td>{tools('labs', l)}</td></tr>)}</tbody></table></div></section>
            <section className="panel"><h2>Documented treatments</h2><div className="table-scroll"><table><thead><tr><th>#</th><th>Date</th><th>Regimen</th><th>Cycle</th><th>State</th><th>Source</th><th>Action</th></tr></thead><tbody>{treatments.map(t => <tr key={t.id}><td>#{t.id}</td><td>{t.date}</td><td>{t.regimen}</td><td>{t.cycle}</td><td><StatusBadge record={t} /></td><td>{bySource(t.source_id)} {evidenceLink('treatments', t)}</td><td>{tools('treatments', t)}</td></tr>)}</tbody></table></div></section></>}
          {view === 'Imaging' && <><section className="panel"><div className="section-heading"><div><h2>Cross-study measurement comparison</h2><p className="muted">Matches documented lesion keys. Difference = later − earlier long axis. Does not register CT slices or classify response.</p></div></div>
            <div className="comparison-controls"><label>Earlier study<select aria-label="Earlier study" value={baselineId} onChange={e => setBaselineId(e.target.value)}>{studies.map(s => <option key={s.id} value={s.id}>#{s.id} · {s.date} · {s.study_label}</option>)}</select></label><label>Later study<select aria-label="Later study" value={followupId} onChange={e => setFollowupId(e.target.value)}>{studies.map(s => <option key={s.id} value={s.id}>#{s.id} · {s.date} · {s.study_label}</option>)}</select></label></div>
            {comparison ? <><div className="table-scroll"><table><thead><tr><th>Lesion key</th><th>Site</th><th>Earlier (mm)</th><th>Later (mm)</th><th>Difference (mm)</th><th>Records</th></tr></thead><tbody>{comparison.measurements.map(m => <tr key={m.lesion_key} data-testid="comparison-row" data-key={m.lesion_key}>
              <td>{m.lesion_key}</td><td>{m.site}{m.site_differs && <div className="unit-warning tiny">Site differs: “{m.baseline_site}” vs “{m.followup_site}”. Check the key.</div>}</td>
              <td className="numeric">{formatNumber(m.baseline_mm)}</td><td className="numeric">{formatNumber(m.followup_mm)}</td>
              <td className="numeric" data-testid="difference">{m.status === 'matched' ? formatDifference(m.difference_mm) : <span className="muted">{m.status}</span>}</td>
              <td className="tiny">{m.baseline_measurement_id && `#${m.baseline_measurement_id} (src #${m.baseline_source_id})`} {m.followup_measurement_id && `→ #${m.followup_measurement_id} (src #${m.followup_source_id})`}</td></tr>)}</tbody></table></div>
              {!comparison.measurements.length && <p className="muted">Neither study has recorded measurements.</p>}
              <p className="caption">{comparison.interpretation} Consult the original radiology report for limitations.</p></>
              : <p className="muted">{comparisonError || (studies.length < 2 ? 'Enter two imaging studies to compare recorded measurements.' : 'Loading…')}</p>}</section>
            <section className="panel"><h2>Radiology reports and study metadata</h2>{studies.map(s => <article className="study" key={s.id}><div className="study-top"><strong>#{s.id} · {s.study_label}</strong><span className="date-label">{s.date} · {s.modality} <StatusBadge record={s} /></span></div><p>{s.report_text || 'No report text entered.'}</p><div className="tiny muted">{bySource(s.source_id)} {evidenceLink('studies', s)}</div>{tools('studies', s)}</article>)}</section>
            <section className="panel"><h2>Recorded lesion measurements</h2><div className="table-scroll"><table><thead><tr><th>#</th><th>Study</th><th>Lesion key</th><th>Location</th><th>Long (mm)</th><th>Short (mm)</th><th>State</th><th>Source</th><th>Action</th></tr></thead><tbody>{measurements.map(m => <tr key={m.id} data-testid="measurement-row" data-id={m.id}><td>#{m.id}</td><td>#{m.study_id} · {studyById(m.study_id)?.date}</td><td>{m.lesion_key}</td><td>{m.site}</td><td className="numeric">{formatNumber(m.long_axis_mm)}</td><td className="numeric">{formatNumber(m.short_axis_mm)}</td><td><StatusBadge record={m} /></td><td>{bySource(m.source_id)} {evidenceLink('measurements', m)}</td><td>{tools('measurements', m)}</td></tr>)}</tbody></table></div></section>
          </>}
          {view === 'Documents' && <div className="documents-layout">
            <nav className="panel doc-list" aria-label="Documents"><h2>Documents</h2>
              <ImportDocument patientId={patient.id} sources={sources} onImported={id => { setDocFocus({ sourceId: id }); reload(); }} />
              {docSources.length ? docSources.map(s => <button key={s.id} type="button" className={`doc-item ${viewerSource?.id === s.id ? 'active' : ''}`} onClick={() => setDocFocus({ sourceId: s.id })}>
                <strong>#{s.id} · {s.label}</strong><span className="tiny muted">{s.document_filename} · {timeline.filter(t => t.source_id === s.id).length} record(s)</span></button>)
                : <p className="muted">No documents attached yet. Attach fictional files in Sources.</p>}</nav>
            {viewerSource && <DocumentViewer key={viewerSource.id} source={viewerSource} observations={timeline.filter(t => t.source_id === viewerSource.id)}
              focus={docFocus?.sourceId === viewerSource.id ? docFocus : null}
              onTranscribe={d => { setAnchorDraft(d); setView('Add record'); }} onExtracted={() => reload()} />}
          </div>}
          {view === 'History' && <HistoryView patientId={patient.id} refresh={refresh} timeline={timeline} series={series} treatments={treatments}
            sources={sources} onOpenDocument={openDocument} onReview={() => setView('Review')} />}
          {view === 'Review' && <ReviewQueue patientId={patient.id} sources={sources} refresh={refresh} onDecided={reload} onOpenDocument={openDocument} />}
          {view === 'Evidence' && <EvidenceView patientId={patient.id} refresh={refresh} timeline={timeline} sources={sources} series={series} onOpenDocument={openDocument} />}
          {view === 'Sources' && <SourcesView sources={sources} studies={studies} onSaved={reload} citations={citations} />}
          {view === 'Audit' && <AuditView patientId={patient.id} refresh={refresh} />}
          {view === 'Add record' && <><AddRecord patientId={patient.id} sources={sources} studies={studies} onSaved={reload} anchor={anchorDraft} />
            <section className="panel"><h2>Exports (synthetic cases only)</h2><p className="muted">Read-only. Source document bytes are not included; their SHA-256 hashes are.</p>
              <div className="export-row"><a className="export-link" href={`${exportBase}/report.md`} target="_blank" rel="noreferrer">Markdown report</a><a className="export-link" href={`${exportBase}/export.json`}>Full JSON</a>
                {(['sources', 'events', 'labs', 'treatments', 'studies', 'measurements', 'audit_log'] as const).map(e => <a key={e} className="export-link" href={`${exportBase}/export/${e}.csv`}>{e}.csv</a>)}</div>
              <details className="new-case"><summary>Create another fictional case</summary><CreateCase onCreated={id => { setPatientId(id); reload(); }} /></details></section></>}
        </>}
        <footer className="footer">OncoScope 0.2.6 · Local-first prototype · Not for clinical decisions · Verify every observation against original documentation</footer>
      </div>
    </main>
  </div>;
}
