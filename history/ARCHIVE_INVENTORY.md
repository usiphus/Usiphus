# Historical archive inventory

The active standalone Usiphus sequence is V7 → V8 → V9 at the repository root. This history folder restores the standalone V6 script and preserves earlier source snapshots, roadmaps, and supplied archive artifacts.

## Version provenance
- V1–V2: the source package manifest reports that no individually labelled V1/V2 archive or source tree was found. Generic early archives and pasted source-text files are preserved as unclassified.
- V3: an explicitly labelled V3.0 source snapshot and original ZIP are included.
- V4–V8: roadmap files are included. The V1–V8 combined package contains a separate `current_v8/` source snapshot.
- V5 implementation snapshot: source, tests, schemas, documentation, and CI workflow are included.
- V6: standalone script included at `versions/usiphus_v6.py`.
- V7–V9: current standalone implementations are at repository root.

## Preserved original artifacts
`original-artifacts/` contains the supplied ZIP, PATCH, and BUNDLE distributions for the V1–V8 combined package, V3.0, V5 implementation, V5 guideline update, V5–V6 feature work, V4–V8 roadmaps, V7 pre-V8, and V8 roadmap audit. Byte-identical duplicate uploads are included once.

## Safety and accuracy
Roadmaps document plans and status at their own snapshot date; they do not prove that every listed feature was implemented. The V6 script's own documentation states that it performs structural/educational validation, not medical factuality or clinical safety review. Clinical content requires qualified review against current authoritative sources.
