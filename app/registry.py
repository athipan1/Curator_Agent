from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models import SkillCreateRequest, SkillDetail, SkillRecord
from app.validator import SafeSkillValidator


APPROVAL_DRAFT = "draft"
APPROVAL_APPROVED = "approved"
APPROVAL_DEPRECATED = "deprecated"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(value: str):
    return json.loads(value) if value else None


class SkillRegistry:
    def __init__(self, db_path: str = "./curator_skills.sqlite3"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.validator = SafeSkillValidator()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    code TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    market_context TEXT NOT NULL,
                    input_schema TEXT NOT NULL,
                    output_schema TEXT NOT NULL,
                    source_agent TEXT,
                    validation_status TEXT NOT NULL,
                    approval_status TEXT NOT NULL DEFAULT 'draft',
                    validation_errors TEXT NOT NULL,
                    lifecycle_notes TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "approval_status", "TEXT NOT NULL DEFAULT 'draft'")
            self._ensure_column(conn, "lifecycle_notes", "TEXT NOT NULL DEFAULT '[]'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_hash ON skills(code_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_approval ON skills(approval_status)")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, column_name: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(skills)").fetchall()}
        if column_name not in columns:
            conn.execute(f"ALTER TABLE skills ADD COLUMN {column_name} {ddl}")

    def register(self, request: SkillCreateRequest) -> SkillDetail:
        validation = self.validator.validate(request.code)
        skill_id = request.skill_id or str(uuid.uuid4())
        now = _utc_now().isoformat()
        code_hash = hashlib.sha256(request.code.encode("utf-8")).hexdigest()
        validation_status = "validated" if validation.approved else "rejected"
        approval_status = APPROVAL_DRAFT
        validation_errors = validation.errors + [f"warning: {item}" for item in validation.warnings]

        with self._connect() as conn:
            existing = conn.execute("SELECT skill_id FROM skills WHERE skill_id = ?", (skill_id,)).fetchone()
            if existing is not None:
                return self.get(skill_id)
            conn.execute(
                """
                INSERT INTO skills (
                    skill_id, name, description, code, code_hash, tags, market_context,
                    input_schema, output_schema, source_agent, validation_status,
                    approval_status, validation_errors, lifecycle_notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    skill_id,
                    request.name,
                    request.description,
                    request.code,
                    code_hash,
                    _json_dump(request.tags),
                    _json_dump(request.market_context),
                    _json_dump(request.input_schema),
                    _json_dump(request.output_schema),
                    request.source_agent,
                    validation_status,
                    approval_status,
                    _json_dump(validation_errors),
                    _json_dump([]),
                    now,
                    now,
                ),
            )

        return self.get(skill_id)

    def list(
        self,
        *,
        tag: str | None = None,
        validation_status: str | None = None,
        approval_status: str | None = None,
    ) -> list[SkillRecord]:
        query = "SELECT * FROM skills"
        params: list[str] = []
        filters: list[str] = []
        if validation_status:
            filters.append("validation_status = ?")
            params.append(validation_status)
        if approval_status:
            filters.append("approval_status = ?")
            params.append(approval_status)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY created_at DESC"

        with self._connect() as conn:
            rows = [self._to_record(row) for row in conn.execute(query, params).fetchall()]

        if tag:
            normalized_tag = tag.lower().strip()
            rows = [row for row in rows if normalized_tag in {item.lower() for item in row.tags}]
        return rows

    def get(self, skill_id: str) -> SkillDetail:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM skills WHERE skill_id = ?", (skill_id,)).fetchone()
        if row is None:
            raise KeyError(skill_id)
        return self._to_detail(row)

    def search(
        self,
        query: str,
        *,
        approval_status: str | None = None,
    ) -> list[SkillRecord]:
        lowered = query.lower().strip()
        candidates = self.list(approval_status=approval_status)
        if not lowered:
            return candidates
        return [
            skill
            for skill in candidates
            if lowered in skill.name.lower()
            or lowered in skill.description.lower()
            or any(lowered in tag.lower() for tag in skill.tags)
            or lowered in json.dumps(skill.market_context, ensure_ascii=False).lower()
        ]

    def approve(
        self,
        skill_id: str,
        *,
        approved_by: str | None = None,
        reason: str | None = None,
    ) -> SkillDetail:
        skill = self.get(skill_id)
        if skill.validation_status != "validated":
            raise ValueError("only_validated_skills_can_be_approved")
        return self._transition(
            skill_id,
            approval_status=APPROVAL_APPROVED,
            note=self._lifecycle_note("approved", actor=approved_by, reason=reason),
        )

    def deprecate(
        self,
        skill_id: str,
        *,
        approved_by: str | None = None,
        reason: str | None = None,
    ) -> SkillDetail:
        self.get(skill_id)
        return self._transition(
            skill_id,
            approval_status=APPROVAL_DEPRECATED,
            note=self._lifecycle_note("deprecated", actor=approved_by, reason=reason),
        )

    def _transition(self, skill_id: str, *, approval_status: str, note: dict) -> SkillDetail:
        current = self.get(skill_id)
        notes = [*current.lifecycle_notes, note]
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE skills
                SET approval_status = ?, lifecycle_notes = ?, updated_at = ?
                WHERE skill_id = ?
                """,
                (approval_status, _json_dump(notes), now, skill_id),
            )
        return self.get(skill_id)

    @staticmethod
    def _lifecycle_note(action: str, *, actor: str | None, reason: str | None) -> dict:
        return {
            "action": action,
            "actor": actor or "system",
            "reason": reason,
            "timestamp": _utc_now().isoformat(),
        }

    @staticmethod
    def _to_record(row: sqlite3.Row) -> SkillRecord:
        return SkillRecord(
            skill_id=row["skill_id"],
            name=row["name"],
            description=row["description"],
            code_hash=row["code_hash"],
            tags=_json_load(row["tags"]) or [],
            market_context=_json_load(row["market_context"]) or {},
            input_schema=_json_load(row["input_schema"]) or {},
            output_schema=_json_load(row["output_schema"]) or {},
            source_agent=row["source_agent"],
            validation_status=row["validation_status"],
            approval_status=row["approval_status"],
            validation_errors=_json_load(row["validation_errors"]) or [],
            lifecycle_notes=_json_load(row["lifecycle_notes"]) or [],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @classmethod
    def _to_detail(cls, row: sqlite3.Row) -> SkillDetail:
        record = cls._to_record(row)
        return SkillDetail(**record.model_dump(), code=row["code"])
