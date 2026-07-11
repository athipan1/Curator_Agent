from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models import (
    SkillCreateRequest,
    SkillDetail,
    SkillRecord,
    SkillVersionCreateRequest,
)
from app.validator import SafeSkillValidator


APPROVAL_DRAFT = "draft"
APPROVAL_APPROVED = "approved"
APPROVAL_DEPRECATED = "deprecated"

DEPLOYMENT_CANDIDATE = "candidate"
DEPLOYMENT_SHADOW = "shadow"
DEPLOYMENT_CHALLENGER = "challenger"
DEPLOYMENT_CHAMPION = "champion"
DEPLOYMENT_DEGRADED = "degraded"
DEPLOYMENT_QUARANTINED = "quarantined"
DEPLOYMENT_RETIRED = "retired"
DEPLOYMENT_STAGES = {
    DEPLOYMENT_CANDIDATE,
    DEPLOYMENT_SHADOW,
    DEPLOYMENT_CHALLENGER,
    DEPLOYMENT_CHAMPION,
    DEPLOYMENT_DEGRADED,
    DEPLOYMENT_QUARANTINED,
    DEPLOYMENT_RETIRED,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(value: str):
    return json.loads(value) if value else None


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


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
                    skill_family_id TEXT,
                    version TEXT NOT NULL DEFAULT '1.0.0',
                    parent_skill_id TEXT,
                    deployment_stage TEXT NOT NULL DEFAULT 'candidate',
                    immutable INTEGER NOT NULL DEFAULT 0,
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
            self._ensure_column(conn, "skill_family_id", "TEXT")
            self._ensure_column(conn, "version", "TEXT NOT NULL DEFAULT '1.0.0'")
            self._ensure_column(conn, "parent_skill_id", "TEXT")
            self._ensure_column(conn, "deployment_stage", "TEXT NOT NULL DEFAULT 'candidate'")
            self._ensure_column(conn, "immutable", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "approval_status", "TEXT NOT NULL DEFAULT 'draft'")
            self._ensure_column(conn, "lifecycle_notes", "TEXT NOT NULL DEFAULT '[]'")
            conn.execute(
                "UPDATE skills SET skill_family_id = skill_id "
                "WHERE skill_family_id IS NULL OR skill_family_id = ''"
            )
            conn.execute(
                "UPDATE skills SET immutable = 1 WHERE approval_status IN (?, ?)",
                (APPROVAL_APPROVED, APPROVAL_DEPRECATED),
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_hash ON skills(code_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_approval ON skills(approval_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_family ON skills(skill_family_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_stage ON skills(deployment_stage)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_family_version "
                "ON skills(skill_family_id, version)"
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, column_name: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(skills)").fetchall()}
        if column_name not in columns:
            conn.execute(f"ALTER TABLE skills ADD COLUMN {column_name} {ddl}")

    def register(self, request: SkillCreateRequest) -> SkillDetail:
        validation = self.validator.validate(request.code)
        skill_id = request.skill_id or str(uuid.uuid4())
        family_id = request.skill_family_id or skill_id
        now = _utc_now().isoformat()
        code_hash = _code_hash(request.code)
        validation_status = "validated" if validation.approved else "rejected"
        validation_errors = validation.errors + [f"warning: {item}" for item in validation.warnings]

        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM skills WHERE skill_id = ?", (skill_id,)).fetchone()
            if existing is not None:
                if existing["code_hash"] != code_hash:
                    raise ValueError("immutable_skill_id_code_hash_mismatch")
                return self._to_detail(existing)
            duplicate_version = conn.execute(
                "SELECT skill_id, code_hash FROM skills WHERE skill_family_id = ? AND version = ?",
                (family_id, request.version),
            ).fetchone()
            if duplicate_version is not None:
                if duplicate_version["code_hash"] != code_hash:
                    raise ValueError("skill_family_version_already_exists_with_different_code")
                return self.get(duplicate_version["skill_id"])
            if request.parent_skill_id:
                parent = conn.execute(
                    "SELECT skill_family_id FROM skills WHERE skill_id = ?",
                    (request.parent_skill_id,),
                ).fetchone()
                if parent is None:
                    raise ValueError("parent_skill_not_found")
                if parent["skill_family_id"] != family_id:
                    raise ValueError("parent_skill_family_mismatch")
            conn.execute(
                """
                INSERT INTO skills (
                    skill_id, skill_family_id, version, parent_skill_id,
                    deployment_stage, immutable, name, description, code, code_hash,
                    tags, market_context, input_schema, output_schema, source_agent,
                    validation_status, approval_status, validation_errors,
                    lifecycle_notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    skill_id,
                    family_id,
                    request.version,
                    request.parent_skill_id,
                    request.deployment_stage,
                    0,
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
                    APPROVAL_DRAFT,
                    _json_dump(validation_errors),
                    _json_dump([]),
                    now,
                    now,
                ),
            )
        return self.get(skill_id)

    def create_version(
        self,
        parent_skill_id: str,
        request: SkillVersionCreateRequest,
    ) -> SkillDetail:
        parent = self.get(parent_skill_id)
        return self.register(
            SkillCreateRequest(
                skill_family_id=parent.skill_family_id,
                version=request.version,
                parent_skill_id=parent.skill_id,
                deployment_stage=DEPLOYMENT_CANDIDATE,
                name=parent.name,
                description=request.description or parent.description,
                code=request.code,
                tags=parent.tags if request.tags is None else request.tags,
                market_context=(
                    parent.market_context
                    if request.market_context is None
                    else request.market_context
                ),
                input_schema=(
                    parent.input_schema
                    if request.input_schema is None
                    else request.input_schema
                ),
                output_schema=(
                    parent.output_schema
                    if request.output_schema is None
                    else request.output_schema
                ),
                source_agent=request.source_agent or parent.source_agent,
            )
        )

    def list(
        self,
        *,
        tag: str | None = None,
        validation_status: str | None = None,
        approval_status: str | None = None,
        skill_family_id: str | None = None,
        deployment_stage: str | None = None,
    ) -> list[SkillRecord]:
        query = "SELECT * FROM skills"
        params: list[str] = []
        filters: list[str] = []
        for column, value in (
            ("validation_status", validation_status),
            ("approval_status", approval_status),
            ("skill_family_id", skill_family_id),
            ("deployment_stage", deployment_stage),
        ):
            if value:
                filters.append(f"{column} = ?")
                params.append(value)
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

    def search(self, query: str, *, approval_status: str | None = None) -> list[SkillRecord]:
        lowered = query.lower().strip()
        candidates = self.list(approval_status=approval_status)
        if not lowered:
            return candidates
        return [
            skill
            for skill in candidates
            if lowered in skill.name.lower()
            or lowered in skill.description.lower()
            or lowered in skill.version.lower()
            or lowered in skill.skill_family_id.lower()
            or any(lowered in tag.lower() for tag in skill.tags)
            or lowered in json.dumps(skill.market_context, ensure_ascii=False).lower()
        ]

    def approve(self, skill_id: str, *, approved_by: str | None = None, reason: str | None = None) -> SkillDetail:
        skill = self.get(skill_id)
        if skill.validation_status != "validated":
            raise ValueError("only_validated_skills_can_be_approved")
        return self._transition(
            skill_id,
            approval_status=APPROVAL_APPROVED,
            immutable=True,
            note=self._lifecycle_note("approved", actor=approved_by, reason=reason),
        )

    def deprecate(self, skill_id: str, *, approved_by: str | None = None, reason: str | None = None) -> SkillDetail:
        self.get(skill_id)
        return self._transition(
            skill_id,
            approval_status=APPROVAL_DEPRECATED,
            deployment_stage=DEPLOYMENT_RETIRED,
            immutable=True,
            note=self._lifecycle_note("deprecated", actor=approved_by, reason=reason),
        )

    def promote(
        self,
        skill_id: str,
        *,
        deployment_stage: str,
        approved_by: str | None = None,
        reason: str | None = None,
    ) -> SkillDetail:
        if deployment_stage not in DEPLOYMENT_STAGES:
            raise ValueError("invalid_deployment_stage")
        skill = self.get(skill_id)
        if deployment_stage not in {DEPLOYMENT_CANDIDATE, DEPLOYMENT_RETIRED}:
            if skill.validation_status != "validated" or skill.approval_status != APPROVAL_APPROVED:
                raise ValueError("only_validated_approved_skills_can_be_deployed")
        with self._connect() as conn:
            if deployment_stage == DEPLOYMENT_CHAMPION:
                current = conn.execute(
                    "SELECT skill_id FROM skills WHERE skill_family_id = ? "
                    "AND deployment_stage = ? AND skill_id != ?",
                    (skill.skill_family_id, DEPLOYMENT_CHAMPION, skill_id),
                ).fetchall()
                for row in current:
                    self._transition(
                        row["skill_id"],
                        deployment_stage=DEPLOYMENT_RETIRED,
                        note=self._lifecycle_note(
                            "champion_replaced",
                            actor=approved_by,
                            reason=f"replaced_by={skill_id}; {reason or ''}".strip(),
                        ),
                    )
        return self._transition(
            skill_id,
            deployment_stage=deployment_stage,
            immutable=skill.immutable or deployment_stage != DEPLOYMENT_CANDIDATE,
            note=self._lifecycle_note(
                f"deployment_stage:{deployment_stage}",
                actor=approved_by,
                reason=reason,
            ),
        )

    def _transition(
        self,
        skill_id: str,
        *,
        approval_status: str | None = None,
        deployment_stage: str | None = None,
        immutable: bool | None = None,
        note: dict,
    ) -> SkillDetail:
        current = self.get(skill_id)
        notes = [*current.lifecycle_notes, note]
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE skills
                SET approval_status = ?, deployment_stage = ?, immutable = ?,
                    lifecycle_notes = ?, updated_at = ?
                WHERE skill_id = ?
                """,
                (
                    approval_status or current.approval_status,
                    deployment_stage or current.deployment_stage,
                    int(current.immutable if immutable is None else immutable),
                    _json_dump(notes),
                    now,
                    skill_id,
                ),
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
            skill_family_id=row["skill_family_id"] or row["skill_id"],
            version=row["version"] or "1.0.0",
            parent_skill_id=row["parent_skill_id"],
            deployment_stage=row["deployment_stage"] or DEPLOYMENT_CANDIDATE,
            immutable=bool(row["immutable"]),
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
