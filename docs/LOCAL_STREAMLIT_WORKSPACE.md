# Optional Local Streamlit Learning Workspace

The repository's established command-line engines remain the authoritative workflows for curriculum contracts, evidence governance, resource discovery, assessment validation and governed releases. `usiphus_streamlit_app.py` adds an optional learner-facing local UI; it does not replace or silently modify those engines.

## Setup

Requirements: Python 3.11+, a local Ollama installation, and a model with enough memory for your machine.

```bash
python -m venv .venv
# Activate the environment, then:
python -m pip install -r requirements-ui.txt
ollama pull llama3.2
ollama serve
streamlit run usiphus_streamlit_app.py
```

The app can also open without the Ollama Python package/server. In that mode, generation and AI feedback are disabled while local archive and export features remain available.

## Features

- Generate fictional educational cases with a locally running Ollama model.
- Save learner reasoning and request structured formative feedback.
- Keep case records in a local SQLite database with foreign keys and WAL mode.
- Generate editable draft flashcards, prevent duplicate fronts and export Anki-compatible TSV.
- Search and export the case archive as Markdown.
- Rate archived cases as Again, Good or Easy.

## Configuration

- `USIPHUS_OLLAMA_MODEL`: preferred default model (default: `llama3.2`).
- `USIPHUS_DB_PATH`: SQLite database path (default: `usiphus_learning.db` beside the app).

Keep the database on a local, access-controlled disk. Back it up before upgrades or manual edits. Do not store patient-identifiable data or real clinical records in this study app.

## Important boundaries

Generated cases, answer keys, flashcards and feedback are unreviewed educational drafts. They are not clinical decision support, verified guidelines or proof of learner competence. Qualified specialty faculty must review material before teaching or distribution. Local protocols, jurisdiction, date-sensitive recommendations and drug doses require independent verification.

The app does not scrape AnkiWeb, DoctorDE, Cureus or other third-party sites. Use `usiphus_resource_discovery.py` for permitted public metadata discovery and link-outs. A public search result is not permission to copy or redistribute its content. No article bodies, deck contents or proprietary exam questions are downloaded by this UI.

## Current scope and limitations

This is an optional single-user local interface. It does not provide authentication, multi-user access controls, remote synchronization, automated curriculum coverage analysis, voice transcription, PDF export, or direct external-source ingestion. Use the existing CLI engines for governed curriculum/evidence/assessment/release operations. The chat-message table is reserved for a later tutor-chat feature and is not yet exposed in the UI.
