# Guidance for Codex working on OncoScope

You are developing a **local-first, source-linked oncology record-organizer**. Do not optimize for impressive-looking AI features until the record timeline is reproducible and reliable.

1. Read `README.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/ROADMAP.md` first.
2. Never infer actual health details or clinical findings. Use synthetic, unmistakably fictional datasets in all tests and commits.
3. Do not expose backend over a public interface, introduce external analytics, remote storage, or call an LLM with patient data.
4. Preserve original source fields, date, units and origin; audit all updates. Add tests before changing calculations.
5. Distinguish arithmetic observation differences from a medical interpretation. No disease staging, automated RECIST classifications, response predictions, treatment recommendations, or simulated "personalized" outcomes.
6. Prefer existing open-source medical imaging infrastructure over building a renderer. DICOM integration is not in this phase.
7. Every user-visible value should be verifiable against a synthetic fixture or stored record/source.
8. Local commands: `cd backend; python -m pytest -q`, `cd frontend; npm install; npm run build`.
9. Prioritize milestone M2 from `docs/ROADMAP.md`. Show tests/verification and enumerate known limitations in your final report.
10. No real patient records should be used until milestone M3 has passed an explicit security/privacy review.
