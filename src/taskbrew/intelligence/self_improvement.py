"""Self-improvement: prompt evolution, strategy portfolios, skill transfer, cognitive load, reflections, failure taxonomy, personality profiling, confidence calibration."""

from __future__ import annotations

import logging

from taskbrew.intelligence._utils import utcnow, new_id, clamp

logger = logging.getLogger(__name__)


class SelfImprovementManager:
    """Manage agent self-improvement capabilities."""

    def __init__(self, db, memory_manager=None) -> None:
        self._db = db
        self._memory_manager = memory_manager

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS prompt_versions (
                id TEXT PRIMARY KEY,
                agent_role TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                version_tag TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prompt_performance (
                id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL REFERENCES prompt_versions(id),
                task_id TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                quality_score REAL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategy_portfolio (
                id TEXT PRIMARY KEY,
                agent_role TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                description TEXT,
                task_type TEXT,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                total_duration_ms INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skill_transfers (
                id TEXT PRIMARY KEY,
                source_role TEXT NOT NULL,
                target_role TEXT NOT NULL,
                skill_area TEXT NOT NULL,
                knowledge_content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                applied INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                acknowledged_at TEXT
            );
            CREATE TABLE IF NOT EXISTS cognitive_load_snapshots (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                context_tokens INTEGER NOT NULL,
                max_tokens INTEGER NOT NULL,
                active_files INTEGER NOT NULL DEFAULT 0,
                task_id TEXT,
                load_ratio REAL NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_reflections (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                what_worked TEXT,
                what_failed TEXT,
                lessons TEXT,
                approach_rating REAL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS failure_modes (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT,
                description TEXT,
                severity TEXT NOT NULL DEFAULT 'medium',
                recovery_action TEXT,
                recovered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_profiles (
                id TEXT PRIMARY KEY,
                agent_role TEXT NOT NULL,
                trait TEXT NOT NULL,
                value REAL NOT NULL,
                evidence_task_id TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(agent_role, trait)
            );
            CREATE TABLE IF NOT EXISTS confidence_records (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                predicted_confidence REAL NOT NULL,
                actual_success INTEGER NOT NULL DEFAULT 0,
                recorded_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Feature 1: Prompt Evolution Engine
    # ------------------------------------------------------------------

    async def store_prompt_version(
        self, agent_role: str, prompt_text: str, version_tag: str | None = None
    ) -> dict:
        """Store a new prompt version for an agent role."""
        vid = f"PV-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO prompt_versions (id, agent_role, prompt_text, version_tag, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (vid, agent_role, prompt_text, version_tag, now),
        )
        return {
            "id": vid,
            "agent_role": agent_role,
            "prompt_text": prompt_text,
            "version_tag": version_tag,
            "created_at": now,
        }

    async def record_prompt_outcome(
        self,
        version_id: str,
        task_id: str,
        success: bool,
        quality_score: float | None = None,
    ) -> dict:
        """Record a task outcome for a specific prompt version."""
        pid = f"PP-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO prompt_performance (id, version_id, task_id, success, quality_score, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pid, version_id, task_id, int(success), quality_score, now),
        )
        return {
            "id": pid,
            "version_id": version_id,
            "task_id": task_id,
            "success": success,
            "quality_score": quality_score,
            "recorded_at": now,
        }

    async def get_best_prompt(self, agent_role: str) -> dict | None:
        """Return the prompt version with the highest success rate (min 5 trials)."""
        row = await self._db.execute_fetchone(
            "SELECT pv.*, "
            "COUNT(pp.id) AS trial_count, "
            "CAST(SUM(pp.success) AS REAL) / COUNT(pp.id) AS success_rate "
            "FROM prompt_versions pv "
            "JOIN prompt_performance pp ON pp.version_id = pv.id "
            "WHERE pv.agent_role = ? "
            "GROUP BY pv.id "
            "HAVING COUNT(pp.id) >= 5 "
            "ORDER BY success_rate DESC, trial_count DESC "
            "LIMIT 1",
            (agent_role,),
        )
        return row

    async def get_prompt_history(self, agent_role: str, limit: int = 20) -> list[dict]:
        """List prompt versions with aggregated performance stats."""
        return await self._db.execute_fetchall(
            "SELECT pv.*, "
            "COALESCE(COUNT(pp.id), 0) AS trial_count, "
            "CASE WHEN COUNT(pp.id) > 0 "
            "  THEN CAST(SUM(pp.success) AS REAL) / COUNT(pp.id) "
            "  ELSE 0.0 END AS success_rate "
            "FROM prompt_versions pv "
            "LEFT JOIN prompt_performance pp ON pp.version_id = pv.id "
            "WHERE pv.agent_role = ? "
            "GROUP BY pv.id "
            "ORDER BY pv.created_at DESC "
            "LIMIT ?",
            (agent_role, limit),
        )

    # ------------------------------------------------------------------
    # Feature 2: Strategy Portfolio Manager
    # ------------------------------------------------------------------

    async def register_strategy(
        self,
        agent_role: str,
        strategy_name: str,
        strategy_type: str,
        description: str,
    ) -> dict:
        """Register a new strategy in the portfolio."""
        sid = f"STR-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO strategy_portfolio "
            "(id, agent_role, strategy_name, strategy_type, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, agent_role, strategy_name, strategy_type, description, now),
        )
        return {
            "id": sid,
            "agent_role": agent_role,
            "strategy_name": strategy_name,
            "strategy_type": strategy_type,
            "description": description,
            "created_at": now,
        }

    async def record_strategy_use(
        self,
        strategy_id: str,
        task_id: str,
        success: bool,
        duration_ms: int | None = None,
    ) -> dict:
        """Record a strategy usage outcome."""
        existing = await self._db.execute_fetchone(
            "SELECT * FROM strategy_portfolio WHERE id = ?", (strategy_id,)
        )
        if not existing:
            return {"error": "Strategy not found"}

        new_success = existing["success_count"] + (1 if success else 0)
        new_failure = existing["failure_count"] + (0 if success else 1)
        new_duration = existing["total_duration_ms"] + (duration_ms or 0)

        await self._db.execute(
            "UPDATE strategy_portfolio SET success_count = ?, failure_count = ?, "
            "total_duration_ms = ? WHERE id = ?",
            (new_success, new_failure, new_duration, strategy_id),
        )
        return {
            "strategy_id": strategy_id,
            "task_id": task_id,
            "success": success,
            "success_count": new_success,
            "failure_count": new_failure,
        }

    async def select_strategy(
        self, agent_role: str, task_type: str | None = None
    ) -> dict | None:
        """Return the best strategy by success rate for an agent role and optional task type."""
        if task_type:
            row = await self._db.execute_fetchone(
                "SELECT *, "
                "CASE WHEN (success_count + failure_count) > 0 "
                "  THEN CAST(success_count AS REAL) / (success_count + failure_count) "
                "  ELSE 0.0 END AS success_rate "
                "FROM strategy_portfolio "
                "WHERE agent_role = ? AND (task_type = ? OR task_type IS NULL) "
                "ORDER BY success_rate DESC "
                "LIMIT 1",
                (agent_role, task_type),
            )
        else:
            row = await self._db.execute_fetchone(
                "SELECT *, "
                "CASE WHEN (success_count + failure_count) > 0 "
                "  THEN CAST(success_count AS REAL) / (success_count + failure_count) "
                "  ELSE 0.0 END AS success_rate "
                "FROM strategy_portfolio "
                "WHERE agent_role = ? "
                "ORDER BY success_rate DESC "
                "LIMIT 1",
                (agent_role,),
            )
        return row

    async def get_portfolio(self, agent_role: str) -> list[dict]:
        """List all strategies with stats for an agent role."""
        return await self._db.execute_fetchall(
            "SELECT *, "
            "CASE WHEN (success_count + failure_count) > 0 "
            "  THEN CAST(success_count AS REAL) / (success_count + failure_count) "
            "  ELSE 0.0 END AS success_rate "
            "FROM strategy_portfolio "
            "WHERE agent_role = ? "
            "ORDER BY success_rate DESC",
            (agent_role,),
        )

    # ------------------------------------------------------------------
    # Feature 3: Skill Transfer Protocol
    # ------------------------------------------------------------------

    async def create_transfer(
        self,
        source_role: str,
        target_role: str,
        skill_area: str,
        knowledge_content: str,
    ) -> dict:
        """Create a skill transfer packet."""
        tid = f"SKT-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO skill_transfers "
            "(id, source_role, target_role, skill_area, knowledge_content, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (tid, source_role, target_role, skill_area, knowledge_content, now),
        )
        return {
            "id": tid,
            "source_role": source_role,
            "target_role": target_role,
            "skill_area": skill_area,
            "knowledge_content": knowledge_content,
            "status": "pending",
            "created_at": now,
        }

    async def get_pending_transfers(self, target_role: str) -> list[dict]:
        """List pending skill transfers for a target role."""
        return await self._db.execute_fetchall(
            "SELECT * FROM skill_transfers WHERE target_role = ? AND status = 'pending' "
            "ORDER BY created_at DESC",
            (target_role,),
        )

    async def acknowledge_transfer(
        self, transfer_id: str, applied: bool = True
    ) -> dict:
        """Mark a skill transfer as acknowledged."""
        now = utcnow()
        status = "applied" if applied else "rejected"
        await self._db.execute(
            "UPDATE skill_transfers SET status = ?, applied = ?, acknowledged_at = ? "
            "WHERE id = ?",
            (status, int(applied), now, transfer_id),
        )
        return {"id": transfer_id, "status": status, "acknowledged_at": now}

    # ------------------------------------------------------------------
    # Feature 4: Cognitive Load Balancer
    # ------------------------------------------------------------------

    async def record_load(
        self,
        agent_id: str,
        context_tokens: int,
        max_tokens: int,
        active_files: int,
        task_id: str | None = None,
    ) -> dict:
        """Record a cognitive load snapshot."""
        lid = f"CL-{new_id(8)}"
        now = utcnow()
        load_ratio = clamp(context_tokens / max_tokens if max_tokens > 0 else 0.0)
        await self._db.execute(
            "INSERT INTO cognitive_load_snapshots "
            "(id, agent_id, context_tokens, max_tokens, active_files, task_id, load_ratio, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, agent_id, context_tokens, max_tokens, active_files, task_id, load_ratio, now),
        )
        return {
            "id": lid,
            "agent_id": agent_id,
            "context_tokens": context_tokens,
            "max_tokens": max_tokens,
            "active_files": active_files,
            "task_id": task_id,
            "load_ratio": round(load_ratio, 4),
            "recorded_at": now,
        }

    async def get_load_history(self, agent_id: str, limit: int = 20) -> list[dict]:
        """Return cognitive load history for an agent."""
        return await self._db.execute_fetchall(
            "SELECT * FROM cognitive_load_snapshots WHERE agent_id = ? "
            "ORDER BY recorded_at DESC LIMIT ?",
            (agent_id, limit),
        )

    async def recommend_eviction(self, agent_id: str) -> dict:
        """Suggest context items to evict based on highest load and oldest access."""
        latest = await self._db.execute_fetchone(
            "SELECT * FROM cognitive_load_snapshots WHERE agent_id = ? "
            "ORDER BY recorded_at DESC LIMIT 1",
            (agent_id,),
        )
        if not latest:
            return {"agent_id": agent_id, "recommendation": "no_data", "items_to_evict": 0}

        load_ratio = latest["load_ratio"]
        if load_ratio < 0.8:
            return {
                "agent_id": agent_id,
                "recommendation": "no_eviction_needed",
                "load_ratio": load_ratio,
                "items_to_evict": 0,
            }

        # Suggest evicting based on how overloaded the agent is
        excess = load_ratio - 0.7  # Target 70% utilization
        items_to_evict = max(1, int(latest["active_files"] * excess))
        return {
            "agent_id": agent_id,
            "recommendation": "evict_oldest",
            "load_ratio": load_ratio,
            "items_to_evict": items_to_evict,
        }

    # ------------------------------------------------------------------
    # Feature 5: Reflection Engine
    # ------------------------------------------------------------------

    async def create_reflection(
        self,
        task_id: str,
        agent_id: str,
        what_worked: str,
        what_failed: str,
        lessons: str,
        approach_rating: float,
    ) -> dict:
        """Store a post-task reflection."""
        rid = f"REF-{new_id(8)}"
        now = utcnow()
        approach_rating = clamp(approach_rating, 0.0, 5.0)
        await self._db.execute(
            "INSERT INTO task_reflections "
            "(id, task_id, agent_id, what_worked, what_failed, lessons, approach_rating, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, task_id, agent_id, what_worked, what_failed, lessons, approach_rating, now),
        )
        return {
            "id": rid,
            "task_id": task_id,
            "agent_id": agent_id,
            "what_worked": what_worked,
            "what_failed": what_failed,
            "lessons": lessons,
            "approach_rating": approach_rating,
            "created_at": now,
        }

    async def get_reflections(
        self, agent_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List reflections, optionally filtered by agent."""
        if agent_id:
            return await self._db.execute_fetchall(
                "SELECT * FROM task_reflections WHERE agent_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM task_reflections ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def find_relevant_reflections(
        self, task_description: str, limit: int = 5
    ) -> list[dict]:
        """Find reflections by keyword search on lessons."""
        # Extract significant words (>3 chars) from description for LIKE matching
        words = [w for w in task_description.split() if len(w) > 3]
        if not words:
            return []

        # Build OR conditions for keyword matching
        conditions = " OR ".join(["lessons LIKE ?" for _ in words])
        params = tuple(f"%{w}%" for w in words[:10])  # Cap at 10 keywords
        return await self._db.execute_fetchall(
            f"SELECT * FROM task_reflections WHERE {conditions} "
            f"ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )

    # ------------------------------------------------------------------
    # Feature 6: Failure Mode Taxonomy
    # ------------------------------------------------------------------

    async def classify_failure(
        self,
        task_id: str,
        category: str,
        subcategory: str,
        description: str,
        severity: str,
    ) -> dict:
        """Classify a task failure into the taxonomy."""
        fid = f"FM-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO failure_modes "
            "(id, task_id, category, subcategory, description, severity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fid, task_id, category, subcategory, description, severity, now),
        )
        return {
            "id": fid,
            "task_id": task_id,
            "category": category,
            "subcategory": subcategory,
            "description": description,
            "severity": severity,
            "created_at": now,
        }

    async def get_taxonomy(self, category: str | None = None) -> list[dict]:
        """Get failure modes grouped by category."""
        if category:
            return await self._db.execute_fetchall(
                "SELECT category, subcategory, COUNT(*) AS count, "
                "GROUP_CONCAT(DISTINCT severity) AS severities "
                "FROM failure_modes WHERE category = ? "
                "GROUP BY category, subcategory "
                "ORDER BY count DESC",
                (category,),
            )
        return await self._db.execute_fetchall(
            "SELECT category, subcategory, COUNT(*) AS count, "
            "GROUP_CONCAT(DISTINCT severity) AS severities "
            "FROM failure_modes "
            "GROUP BY category, subcategory "
            "ORDER BY count DESC",
        )

    async def get_recovery_playbook(
        self, category: str, subcategory: str | None = None
    ) -> list[dict]:
        """Find successful recovery patterns for a failure category."""
        if subcategory:
            return await self._db.execute_fetchall(
                "SELECT * FROM failure_modes "
                "WHERE category = ? AND subcategory = ? AND recovered = 1 "
                "ORDER BY created_at DESC",
                (category, subcategory),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM failure_modes "
            "WHERE category = ? AND recovered = 1 "
            "ORDER BY created_at DESC",
            (category,),
        )

    # ------------------------------------------------------------------
    # Feature 7: Agent Personality Profiler
    # ------------------------------------------------------------------

    async def update_profile(
        self,
        agent_role: str,
        trait: str,
        value: float,
        evidence_task_id: str | None = None,
    ) -> dict:
        """Update a personality trait for an agent role (upsert)."""
        now = utcnow()
        value = clamp(value, 0.0, 1.0)

        existing = await self._db.execute_fetchone(
            "SELECT * FROM agent_profiles WHERE agent_role = ? AND trait = ?",
            (agent_role, trait),
        )
        if existing:
            await self._db.execute(
                "UPDATE agent_profiles SET value = ?, evidence_task_id = ?, updated_at = ? "
                "WHERE id = ?",
                (value, evidence_task_id, now, existing["id"]),
            )
            return {
                "id": existing["id"],
                "agent_role": agent_role,
                "trait": trait,
                "value": value,
                "evidence_task_id": evidence_task_id,
                "updated_at": now,
            }
        else:
            pid = f"AP-{new_id(8)}"
            await self._db.execute(
                "INSERT INTO agent_profiles (id, agent_role, trait, value, evidence_task_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pid, agent_role, trait, value, evidence_task_id, now),
            )
            return {
                "id": pid,
                "agent_role": agent_role,
                "trait": trait,
                "value": value,
                "evidence_task_id": evidence_task_id,
                "updated_at": now,
            }

    async def get_profile(self, agent_role: str) -> dict:
        """Get the full personality profile for an agent role."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM agent_profiles WHERE agent_role = ? ORDER BY trait",
            (agent_role,),
        )
        traits = {row["trait"]: row["value"] for row in rows}
        return {"agent_role": agent_role, "traits": traits, "trait_count": len(traits)}

    async def match_task_to_agent(
        self, task_type: str, required_traits: dict[str, float]
    ) -> list[dict]:
        """Find agents whose profiles best match the required traits.

        *required_traits* maps trait names to minimum desired values.
        Returns agents sorted by match quality (number of traits meeting threshold).
        """
        if not required_traits:
            return []

        # Get all profiles
        all_profiles = await self._db.execute_fetchall(
            "SELECT DISTINCT agent_role FROM agent_profiles",
        )

        results = []
        for profile_row in all_profiles:
            role = profile_row["agent_role"]
            traits = await self._db.execute_fetchall(
                "SELECT trait, value FROM agent_profiles WHERE agent_role = ?",
                (role,),
            )
            trait_map = {t["trait"]: t["value"] for t in traits}
            matches = sum(
                1
                for trait_name, min_val in required_traits.items()
                if trait_map.get(trait_name, 0.0) >= min_val
            )
            total = len(required_traits)
            results.append({
                "agent_role": role,
                "match_score": matches / total if total > 0 else 0.0,
                "matched_traits": matches,
                "total_required": total,
            })

        results.sort(key=lambda x: x["match_score"], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Feature 8: Confidence Calibration Tracker
    # ------------------------------------------------------------------

    async def record_confidence(
        self,
        agent_id: str,
        task_id: str,
        predicted_confidence: float,
        actual_success: bool,
    ) -> dict:
        """Record a confidence prediction alongside actual outcome."""
        cid = f"CC-{new_id(8)}"
        now = utcnow()
        predicted_confidence = clamp(predicted_confidence)
        await self._db.execute(
            "INSERT INTO confidence_records "
            "(id, agent_id, task_id, predicted_confidence, actual_success, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, agent_id, task_id, predicted_confidence, int(actual_success), now),
        )
        return {
            "id": cid,
            "agent_id": agent_id,
            "task_id": task_id,
            "predicted_confidence": predicted_confidence,
            "actual_success": actual_success,
            "recorded_at": now,
        }

    async def get_calibration_score(self, agent_id: str) -> dict:
        """Compute the Brier score (mean squared error of confidence vs outcome).

        Lower is better: 0.0 = perfectly calibrated, 1.0 = worst possible.
        """
        row = await self._db.execute_fetchone(
            "SELECT COUNT(*) AS n, "
            "AVG((predicted_confidence - actual_success) * (predicted_confidence - actual_success)) "
            "  AS brier_score "
            "FROM confidence_records WHERE agent_id = ?",
            (agent_id,),
        )
        if not row or row["n"] == 0:
            return {"agent_id": agent_id, "brier_score": None, "sample_count": 0}
        return {
            "agent_id": agent_id,
            "brier_score": round(row["brier_score"], 4),
            "sample_count": row["n"],
        }

    async def get_calibration_history(
        self, agent_id: str, limit: int = 50
    ) -> list[dict]:
        """Return confidence calibration history for an agent."""
        return await self._db.execute_fetchall(
            "SELECT * FROM confidence_records WHERE agent_id = ? "
            "ORDER BY recorded_at DESC LIMIT ?",
            (agent_id, limit),
        )
