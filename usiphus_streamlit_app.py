"""Optional local-first Streamlit workspace for Usiphus.

The established CLI engines remain the source of truth for curriculum,
evidence governance, assessment validation and release workflows. This file
provides a lightweight learner-facing UI without changing those contracts.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

try:
    import ollama
except ImportError:  # UI can still open in archive-only mode.
    ollama = None

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("USIPHUS_DB_PATH", APP_DIR / "usiphus_learning.db"))
DEFAULT_MODEL = os.environ.get("USIPHUS_OLLAMA_MODEL", "llama3.2")
SPECIALTIES = [
    "Cardiology", "Dermatology", "Emergency Medicine", "Endocrinology",
    "Gastroenterology", "General Surgery", "Hematology & Oncology",
    "Infectious Disease", "Neurology", "Obstetrics & Gynecology",
    "Ophthalmology", "Pediatrics", "Psychiatry", "Pulmonology",
    "Nephrology", "Urology", "Vascular Surgery",
]
RATINGS = ["Again", "Good", "Easy"]

st.set_page_config(
    page_title="Usiphus — Clinical Learning Workspace",
    page_icon="🩺",
    layout="wide",
)

st.markdown(
    """
    <style>
    .stApp { background: #0d1117; color: #c9d1d9; }
    [data-testid="stSidebar"] { background: #161b22; }
    .block-container { max-width: 1500px; padding-top: 1.4rem; }
    h1,h2,h3 { color: #f0f6fc !important; }
    div[data-testid="stMetricValue"] { color: #58a6ff; }
    .stButton button { border-radius: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)


@contextmanager
def db_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                specialty TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                level TEXT NOT NULL DEFAULT 'Medical student',
                vignette TEXT NOT NULL,
                question TEXT NOT NULL DEFAULT '',
                answer_key TEXT NOT NULL DEFAULT '',
                learner_response TEXT NOT NULL DEFAULT '',
                critique TEXT NOT NULL DEFAULT '',
                rating TEXT NOT NULL DEFAULT 'Unrated',
                model TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                specialty TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                front TEXT NOT NULL,
                back TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                UNIQUE(front)
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cases_specialty ON cases(specialty);
            CREATE INDEX IF NOT EXISTS idx_cards_specialty ON cards(specialty);
            CREATE INDEX IF NOT EXISTS idx_chat_case ON chat_messages(case_id, id);
            """
        )


def save_case(specialty: str, topic: str, level: str, model: str,
              vignette: str, question: str, answer_key: str) -> int:
    with db_connection() as conn:
        cur = conn.execute(
            """INSERT INTO cases
            (created_at,specialty,topic,level,vignette,question,answer_key,model)
            VALUES (?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), specialty, topic, level,
             vignette, question, answer_key, model),
        )
        return int(cur.lastrowid)


def update_response(case_id: int, response: str) -> None:
    with db_connection() as conn:
        conn.execute("UPDATE cases SET learner_response=? WHERE id=?",
                     (response, case_id))


def save_critique(case_id: int, response: str, critique: str) -> None:
    with db_connection() as conn:
        conn.execute(
            "UPDATE cases SET learner_response=?, critique=? WHERE id=?",
            (response, critique, case_id),
        )


def set_rating(case_id: int, rating: str) -> None:
    if rating not in RATINGS:
        raise ValueError("Invalid mastery rating")
    with db_connection() as conn:
        conn.execute("UPDATE cases SET rating=? WHERE id=?", (rating, case_id))


def list_cases(search: str = "", specialty: str = "All") -> list[sqlite3.Row]:
    sql = "SELECT * FROM cases WHERE 1=1"
    params: list[Any] = []
    if specialty != "All":
        sql += " AND specialty=?"
        params.append(specialty)
    if search.strip():
        term = f"%{search.strip()}%"
        sql += " AND (topic LIKE ? OR vignette LIKE ? OR question LIKE ? OR learner_response LIKE ?)"
        params.extend([term] * 4)
    sql += " ORDER BY id DESC"
    with db_connection() as conn:
        return list(conn.execute(sql, params).fetchall())


def save_cards(specialty: str, topic: str, cards: list[dict[str, str]]) -> tuple[int, int]:
    added = duplicates = 0
    with db_connection() as conn:
        for card in cards:
            front, back = card.get("front", "").strip(), card.get("back", "").strip()
            if not front or not back:
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO cards
                (created_at,specialty,topic,front,back,tags)
                VALUES (?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), specialty, topic,
                 front, back, card.get("tags", "").strip()),
            )
            if cur.rowcount:
                added += 1
            else:
                duplicates += 1
    return added, duplicates


def list_cards(search: str = "", specialty: str = "All") -> list[sqlite3.Row]:
    sql = "SELECT * FROM cards WHERE 1=1"
    params: list[Any] = []
    if specialty != "All":
        sql += " AND specialty=?"
        params.append(specialty)
    if search.strip():
        term = f"%{search.strip()}%"
        sql += " AND (front LIKE ? OR back LIKE ? OR tags LIKE ? OR topic LIKE ?)"
        params.extend([term] * 4)
    sql += " ORDER BY id DESC"
    with db_connection() as conn:
        return list(conn.execute(sql, params).fetchall())


def chat_history(case_id: int) -> list[dict[str, str]]:
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT role,content FROM chat_messages WHERE case_id=? ORDER BY id",
            (case_id,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def add_chat_message(case_id: int, role: str, content: str) -> None:
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO chat_messages(case_id,role,content,created_at) VALUES(?,?,?,?)",
            (case_id, role, content, datetime.now(timezone.utc).isoformat()),
        )


@st.cache_data(ttl=15)
def ollama_models() -> list[str]:
    if ollama is None:
        return []
    response = ollama.list()
    models = response.get("models", []) if isinstance(response, dict) else getattr(response, "models", [])
    names = []
    for item in models:
        if isinstance(item, dict):
            names.append(item.get("model") or item.get("name", ""))
        else:
            names.append(getattr(item, "model", None) or getattr(item, "name", ""))
    return sorted(n for n in names if n)


def call_model(model: str, prompt: str, system: str | None = None) -> str:
    if ollama is None:
        raise RuntimeError("Install the optional Ollama Python package: pip install ollama")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    result = ollama.chat(model=model, messages=messages)
    return result["message"]["content"].strip()


def extract_json(text: str) -> Any:
    """Parse strict JSON, allowing a single surrounding Markdown code fence."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return json.loads(cleaned)


def make_case(model: str, specialty: str, topic: str, level: str) -> dict[str, str]:
    prompt = f"""
Create one original, fictional educational case for {specialty}.
Topic: {topic or 'Choose a representative high-yield topic'}.
Learner level: {level}.
Return ONLY a JSON object with string keys:
vignette, question, answer_key.
The vignette should include relevant history, examination and plausible data.
The question should test one learning objective. The answer_key should explain
the best answer and briefly address important alternatives. Avoid patient
identifiers. State jurisdiction-dependent recommendations as such. Do not
invent citations or claim that generated material is guideline-verified.
"""
    raw = call_model(
        model, prompt,
        "You are an educational clinical tutor. Create synthetic teaching material, not patient-specific advice.",
    )
    data = extract_json(raw)
    required = ("vignette", "question", "answer_key")
    if not isinstance(data, dict) or any(not str(data.get(k, "")).strip() for k in required):
        raise ValueError("Model output did not contain the required case fields.")
    return {k: str(data[k]).strip() for k in required}


def critique_response(model: str, case: sqlite3.Row, learner_response: str) -> str:
    prompt = f"""
Assess this learner's clinical reasoning as a formative educational exercise.

Specialty: {case['specialty']}
Topic: {case['topic']}
Case: {case['vignette']}
Question: {case['question']}
Learner response: {learner_response}

Use these headings:
1. What was correct
2. Important omissions or errors
3. Prioritized differential and discriminating features
4. Investigations and rationale
5. Initial management and safety-critical actions
6. Suggested model answer
7. Three learning objectives for revision

Be explicit about uncertainty and local guideline variation. Do not claim
that this feedback establishes clinical competence. Flag unsafe reasoning.
"""
    return call_model(
        model, prompt,
        "You are a careful clinical educator. Distinguish educational feedback from clinical advice.",
    )


def generate_cards(model: str, specialty: str, topic: str, count: int) -> list[dict[str, str]]:
    prompt = f"""
Generate exactly {count} concise medical revision flashcards.
Specialty: {specialty}. Topic: {topic or 'high-yield core topics'}.
Return ONLY a JSON array of objects with keys front, back, tags.
Each card must test one fact or clinical association. Front and back should
normally be under 250 characters. Prefer precise qualifiers over vague rules.
Do not invent references or include patient-identifiable information.
"""
    data = extract_json(call_model(
        model, prompt,
        "You create draft educational flashcards. Clinical content requires faculty verification.",
    ))
    if not isinstance(data, list) or len(data) != count:
        raise ValueError(f"Expected exactly {count} cards in a JSON array.")
    result = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Each flashcard must be a JSON object.")
        front, back = str(item.get("front", "")).strip(), str(item.get("back", "")).strip()
        if not front or not back:
            raise ValueError("A generated card is missing its front or back.")
        result.append({
            "front": front,
            "back": back,
            "tags": str(item.get("tags", f"{specialty} {topic}")).strip(),
        })
    return result


def markdown_archive(rows: list[sqlite3.Row]) -> str:
    lines = ["# Usiphus Learning Archive", ""]
    for row in rows:
        lines += [
            f"## Case {row['id']} — {row['specialty']} / {row['topic']}",
            f"*Created: {row['created_at']} · Mastery: {row['rating']}*",
            "", row["vignette"], "", f"**Question:** {row['question']}", "",
            "### Learner response", row["learner_response"] or "Not recorded.", "",
            "### Formative feedback", row["critique"] or "Not recorded.", "",
            "### Answer key", row["answer_key"], "", "---", "",
        ]
    return "\n".join(lines)


def main() -> None:
    init_db()
    with st.sidebar:
        st.title("🩺 Usiphus")
        st.caption("Local-first clinical learning workspace")
        page = st.radio(
            "Workspace",
            ["Clinical Cases", "Flashcard Studio", "Card Bank", "Case Archive", "About"],
        )
        st.divider()
        st.subheader("Local AI")
        models = ollama_models() if ollama is not None else []
        if models:
            preferred = DEFAULT_MODEL if DEFAULT_MODEL in models else models[0]
            model = st.selectbox("Ollama model", models, index=models.index(preferred))
            st.success("Ollama is reachable")
        else:
            model = DEFAULT_MODEL
            st.warning("Ollama unavailable — archive and export remain usable")
        st.caption(f"Database: {DB_PATH.name}")

    if page == "Clinical Cases":
        st.title("Clinical case workspace")
        st.caption("Generate a fictional case, reason independently, then request formative feedback.")
        with st.form("case_generator"):
            c1, c2 = st.columns(2)
            with c1:
                specialty = st.selectbox("Specialty", SPECIALTIES)
                topic = st.text_input("Topic or curriculum objective")
            with c2:
                level = st.selectbox("Learner level", ["Medical student", "Intern", "Junior resident", "Senior resident"])
            generate = st.form_submit_button("Generate case", type="primary", disabled=not bool(ollama and models))
        if generate:
            try:
                with st.spinner("Generating a fictional teaching case…"):
                    case = make_case(model, specialty, topic, level)
                    case_id = save_case(specialty, topic, level, model, **case)
                st.session_state["active_case_id"] = case_id
                st.success(f"Case #{case_id} created.")
            except Exception as exc:
                st.error(f"Case generation failed: {exc}")

        active_id = st.session_state.get("active_case_id")
        if active_id:
            with db_connection() as conn:
                case = conn.execute("SELECT * FROM cases WHERE id=?", (active_id,)).fetchone()
            if case:
                st.subheader(f"{case['specialty']} — {case['topic'] or 'Clinical case'}")
                st.markdown(case["vignette"])
                st.info(case["question"])
                response = st.text_area(
                    "Your differential diagnosis, reasoning and plan",
                    value=case["learner_response"],
                    height=220,
                    key=f"response_{active_id}",
                )
                if st.button("Save draft"):
                    update_response(active_id, response)
                    st.success("Draft saved.")
                if st.button("Submit for formative feedback", type="primary",
                             disabled=not bool(ollama and models)):
                    if not response.strip():
                        st.warning("Enter your reasoning first.")
                    else:
                        try:
                            with st.spinner("Reviewing your reasoning…"):
                                feedback = critique_response(model, case, response)
                            save_critique(active_id, response, feedback)
                            st.session_state[f"feedback_{active_id}"] = feedback
                        except Exception as exc:
                            st.error(f"Feedback failed: {exc}")
                feedback = st.session_state.get(f"feedback_{active_id}") or case["critique"]
                if feedback:
                    st.subheader("Formative feedback")
                    st.markdown(feedback)
                with st.expander("Reveal model answer"):
                    st.markdown(case["answer_key"])
                st.caption("Generated clinical content and feedback are drafts. Verify against current local guidance and qualified faculty review.")

    elif page == "Flashcard Studio":
        st.title("Flashcard studio")
        st.caption("Create draft cards from a specialty/topic and review them before saving.")
        with st.form("card_generator"):
            specialty = st.selectbox("Specialty", SPECIALTIES, key="card_specialty")
            topic = st.text_input("Topic", key="card_topic")
            count = st.slider("Number of cards", 5, 30, 10)
            generate = st.form_submit_button("Generate cards", type="primary", disabled=not bool(ollama and models))
        if generate:
            try:
                with st.spinner("Drafting cards…"):
                    st.session_state["draft_cards"] = generate_cards(model, specialty, topic, count)
                    st.session_state["draft_specialty"] = specialty
                    st.session_state["draft_topic"] = topic
            except Exception as exc:
                st.error(f"Card generation failed: {exc}")
        if st.session_state.get("draft_cards"):
            edited = st.data_editor(
                st.session_state["draft_cards"], num_rows="dynamic",
                column_config={
                    "front": st.column_config.TextColumn("Front", required=True),
                    "back": st.column_config.TextColumn("Back", required=True),
                    "tags": st.column_config.TextColumn("Tags"),
                },
                use_container_width=True,
            )
            if st.button("Save reviewed cards"):
                added, duplicates = save_cards(
                    st.session_state["draft_specialty"],
                    st.session_state["draft_topic"],
                    edited.to_dict(orient="records"),
                )
                st.success(f"Saved {added}; skipped {duplicates} duplicate(s).")

    elif page == "Card Bank":
        st.title("Card bank")
        q = st.text_input("Search cards")
        spec = st.selectbox("Specialty", ["All"] + SPECIALTIES)
        cards = list_cards(q, spec)
        st.write(f"{len(cards)} card(s)")
        if cards:
            for card in cards:
                with st.expander(f"{card['front']}  ·  {card['specialty']}"):
                    st.markdown(card["back"])
                    st.caption(card["tags"])
            tsv = "\n".join(
                "\t".join([c["front"], c["back"], c["tags"]]) for c in cards
            )
            st.download_button("Export Anki TSV", tsv, "usiphus_cards.tsv", "text/tab-separated-values")

    elif page == "Case Archive":
        st.title("Case archive")
        q = st.text_input("Search cases")
        spec = st.selectbox("Specialty", ["All"] + SPECIALTIES)
        rows = list_cases(q, spec)
        st.write(f"{len(rows)} case(s)")
        if rows:
            st.download_button("Export Markdown archive", markdown_archive(rows),
                               "usiphus_case_archive.md", "text/markdown")
            for row in rows:
                with st.expander(f"#{row['id']} · {row['specialty']} · {row['topic'] or 'General'} · {row['rating']}"):
                    st.markdown(row["vignette"])
                    st.markdown(f"**Question:** {row['question']}")
                    st.markdown("**Learner response**")
                    st.markdown(row["learner_response"] or "Not recorded.")
                    st.markdown("**Feedback**")
                    st.markdown(row["critique"] or "Not recorded.")
                    st.markdown("**Answer key**")
                    st.markdown(row["answer_key"])
                    cols = st.columns(3)
                    for col, rating in zip(cols, RATINGS):
                        if col.button(rating, key=f"rating_{row['id']}_{rating}"):
                            set_rating(row["id"], rating)
                            st.rerun()

    else:
        st.title("About Usiphus")
        st.markdown(
            """
            This optional interface sits alongside the Usiphus command-line
            engines. It is intended for local study workflows, not clinical
            decision support or a hosted learning-management system.

            **Current UI features**
            - Local Ollama case generation and formative reasoning feedback
            - SQLite case archive and search
            - Draft flashcard generation, editing, duplicate protection and TSV export
            - Per-case chat storage schema reserved for future tutor-chat integration

            **Not included in this UI release**
            - Automatic crawling/downloading of AnkiWeb or DoctorDE content
            - Automated approval of clinical content or evidence
            - Cloud accounts, multi-user permissions or remote database hosting
            - Clinical guideline verification

            Use the repository's resource-discovery and evidence-governance
            engines for metadata discovery and review workflows. Public search
            links do not grant permission to copy or redistribute third-party
            decks, questions, images or article text.
            """
        )


if __name__ == "__main__":
    main()
