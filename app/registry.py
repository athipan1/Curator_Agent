from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.models import SkillCreateRequest, SkillDetail, SkillRecord
from app.validator import SafeSkillValidator


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
                    validation_errors TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_hash ON skills(code_hash)")

    def register(self, request: SkillCreateRequest) -> SkillDetail:
        validation = self.validator.validate(request.code)
        skill_id = str(uuid.uuid4())
        now = _utc_now().isoformat()
        code_hash = hashlib.sha256(request.code.encode("utf-8")).hexdigest()
        validation_status = "validated" if validation.approved else "rejected"
        validation_errors = validation.errors + [f"warning: {item}" for item in validation.warnings]

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO skills (
                    skill_id, name, description, code, code_hash, tags, market_context,
                    input_schema, output_schema, source_agent, validation_status,
                    validation_errors, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _json_dump(validation_errors),
                    now,
                    now,
                ),
            )

        return self.get(skill_id)

    def list(self, *, tag: str | None = None, validation_status: str | None = None) -> list[SkillRecord]:
        query = "SELECT * FROM skills"
        params: list[str] = []
        filters: list[str] = []
        if validation_status:
            filters.append("validation_status = ?")
            params.append(validation_status)
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

    def search(self, query: str) -> list[SkillRecord]:
        lowered = query.lower().strip()
        if not lowered:
            return self.list()
        candidates = self.list()
        return [
            skill
            for skill in candidates
            if lowered in skill.name.lower()
            or lowered in skill.description.lower()
            or any(lowered in tag.lower() for tag in skill.tags)
            or lowered in json.dumps(skill.market_context, ensure_ascii=False).lower()
        ]

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
            validation_errors=_json_load(row["validation_errors"]) or [],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @classmethod
    def _to_detail(cls, row: sqlite3.Row) -> SkillDetail:
        record = cls._to_record(row)
        return SkillDetail(**record.model_dump(), code=row["code"])
