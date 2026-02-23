"""Social intelligence: argument resolution, trust networks, communication styles, shared mental models, coordination detection, context bridging, collaboration scoring, consensus prediction."""

from __future__ import annotations

import json
import logging

from taskbrew.intelligence._utils import utcnow, new_id, clamp

logger = logging.getLogger(__name__)


class SocialIntelligenceManager:
    """Manage social intelligence capabilities between agents."""

    # Exponential moving average factor for trust updates
    TRUST_EMA_ALPHA: float = 0.3

    def __init__(self, db, event_bus=None, instance_manager=None) -> None:
        self._db = db
        self._event_bus = event_bus
        self._instance_manager = instance_manager

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS argument_sessions (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                participants TEXT NOT NULL,
                context TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                winner_position TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS argument_evidence (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES argument_sessions(id),
                agent_id TEXT NOT NULL,
                position TEXT NOT NULL,
                evidence TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                submitted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trust_scores (
                id TEXT PRIMARY KEY,
                from_agent TEXT NOT NULL,
                to_agent TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.5,
                interaction_count INTEGER NOT NULL DEFAULT 0,
                last_updated TEXT NOT NULL,
                UNIQUE(from_agent, to_agent)
            );
            CREATE TABLE IF NOT EXISTS communication_preferences (
                id TEXT PRIMARY KEY,
                agent_role TEXT NOT NULL,
                preference_key TEXT NOT NULL,
                preference_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(agent_role, preference_key)
            );
            CREATE TABLE IF NOT EXISTS mental_model_facts (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                retracted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coordination_alerts (
                id TEXT PRIMARY KEY,
                agent_ids TEXT NOT NULL,
                overlapping_files TEXT NOT NULL,
                task_ids TEXT,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS work_areas (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                task_id TEXT,
                reported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context_shares (
                id TEXT PRIMARY KEY,
                from_agent TEXT NOT NULL,
                to_agent TEXT NOT NULL,
                context_key TEXT NOT NULL,
                context_value TEXT NOT NULL,
                relevance_score REAL NOT NULL DEFAULT 1.0,
                consumed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                consumed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS collaboration_scores (
                id TEXT PRIMARY KEY,
                agent_a TEXT NOT NULL,
                agent_b TEXT NOT NULL,
                task_id TEXT NOT NULL,
                effectiveness REAL NOT NULL,
                notes TEXT,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS consensus_predictions (
                id TEXT PRIMARY KEY,
                proposal_description TEXT NOT NULL,
                participants TEXT NOT NULL,
                predicted_outcome TEXT NOT NULL,
                predicted_confidence REAL NOT NULL DEFAULT 0.5,
                actual_outcome TEXT,
                correct INTEGER,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
        """)

    # ------------------------------------------------------------------
    # Feature 9: Argument Resolution Protocol
    # ------------------------------------------------------------------

    async def open_argument(
        self, topic: str, participants: list[str], context: str | None = None
    ) -> dict:
        """Create an argument session."""
        aid = f"ARG-{new_id(8)}"
        now = utcnow()
        participants_json = json.dumps(participants)
        await self._db.execute(
            "INSERT INTO argument_sessions "
            "(id, topic, participants, context, status, created_at) "
            "VALUES (?, ?, ?, ?, 'open', ?)",
            (aid, topic, participants_json, context, now),
        )
        return {
            "id": aid,
            "topic": topic,
            "participants": participants,
            "context": context,
            "status": "open",
            "created_at": now,
        }

    async def submit_evidence(
        self,
        session_id: str,
        agent_id: str,
        position: str,
        evidence: str,
        confidence: float,
    ) -> dict:
        """Submit evidence for an argument position."""
        eid = f"AE-{new_id(8)}"
        now = utcnow()
        confidence = clamp(confidence)
        await self._db.execute(
            "INSERT INTO argument_evidence "
            "(id, session_id, agent_id, position, evidence, confidence, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eid, session_id, agent_id, position, evidence, confidence, now),
        )
        return {
            "id": eid,
            "session_id": session_id,
            "agent_id": agent_id,
            "position": position,
            "evidence": evidence,
            "confidence": confidence,
            "submitted_at": now,
        }

    async def resolve_argument(self, session_id: str) -> dict:
        """Resolve an argument by tallying evidence weighted by confidence.

        Each position's score is the sum of confidence values of its evidence.
        The position with the highest total wins.
        """
        evidence_rows = await self._db.execute_fetchall(
            "SELECT position, SUM(confidence) AS total_confidence "
            "FROM argument_evidence WHERE session_id = ? "
            "GROUP BY position "
            "ORDER BY total_confidence DESC",
            (session_id,),
        )
        if not evidence_rows:
            return {"session_id": session_id, "status": "no_evidence", "winner": None}

        winner = evidence_rows[0]
        now = utcnow()
        await self._db.execute(
            "UPDATE argument_sessions SET status = 'resolved', winner_position = ?, resolved_at = ? "
            "WHERE id = ?",
            (winner["position"], now, session_id),
        )
        return {
            "session_id": session_id,
            "status": "resolved",
            "winner": winner["position"],
            "score": winner["total_confidence"],
            "all_positions": [
                {"position": r["position"], "score": r["total_confidence"]}
                for r in evidence_rows
            ],
            "resolved_at": now,
        }

    async def get_argument_history(self, limit: int = 20) -> list[dict]:
        """List argument sessions."""
        return await self._db.execute_fetchall(
            "SELECT * FROM argument_sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 10: Trust Score Network
    # ------------------------------------------------------------------

    async def update_trust(
        self,
        from_agent: str,
        to_agent: str,
        interaction_type: str,
        outcome_quality: float,
    ) -> dict:
        """Update the trust score using an exponential moving average."""
        outcome_quality = clamp(outcome_quality)
        now = utcnow()

        existing = await self._db.execute_fetchone(
            "SELECT * FROM trust_scores WHERE from_agent = ? AND to_agent = ?",
            (from_agent, to_agent),
        )

        if existing:
            old_score = existing["score"]
            new_score = round(
                self.TRUST_EMA_ALPHA * outcome_quality
                + (1 - self.TRUST_EMA_ALPHA) * old_score,
                4,
            )
            new_count = existing["interaction_count"] + 1
            await self._db.execute(
                "UPDATE trust_scores SET score = ?, interaction_count = ?, last_updated = ? "
                "WHERE id = ?",
                (new_score, new_count, now, existing["id"]),
            )
            return {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "score": new_score,
                "interaction_count": new_count,
                "last_updated": now,
            }
        else:
            tid = f"TS-{new_id(8)}"
            # First interaction: score equals the outcome quality
            await self._db.execute(
                "INSERT INTO trust_scores "
                "(id, from_agent, to_agent, score, interaction_count, last_updated) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (tid, from_agent, to_agent, outcome_quality, now),
            )
            return {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "score": outcome_quality,
                "interaction_count": 1,
                "last_updated": now,
            }

    async def get_trust(self, from_agent: str, to_agent: str) -> dict | None:
        """Get the current trust score between two agents."""
        return await self._db.execute_fetchone(
            "SELECT * FROM trust_scores WHERE from_agent = ? AND to_agent = ?",
            (from_agent, to_agent),
        )

    async def get_trust_network(self) -> list[dict]:
        """Get all agent pairs with their trust scores."""
        return await self._db.execute_fetchall(
            "SELECT * FROM trust_scores ORDER BY score DESC",
        )

    async def get_most_trusted(self, for_agent: str, limit: int = 5) -> list[dict]:
        """Get the top trusted agents for a given agent."""
        return await self._db.execute_fetchall(
            "SELECT * FROM trust_scores WHERE from_agent = ? "
            "ORDER BY score DESC LIMIT ?",
            (for_agent, limit),
        )

    # ------------------------------------------------------------------
    # Feature 11: Communication Style Adapter
    # ------------------------------------------------------------------

    async def record_preference(
        self, agent_role: str, preference_key: str, preference_value: str
    ) -> dict:
        """Store or update a communication preference."""
        now = utcnow()
        existing = await self._db.execute_fetchone(
            "SELECT * FROM communication_preferences "
            "WHERE agent_role = ? AND preference_key = ?",
            (agent_role, preference_key),
        )
        if existing:
            await self._db.execute(
                "UPDATE communication_preferences SET preference_value = ?, updated_at = ? "
                "WHERE id = ?",
                (preference_value, now, existing["id"]),
            )
            return {
                "id": existing["id"],
                "agent_role": agent_role,
                "preference_key": preference_key,
                "preference_value": preference_value,
                "updated_at": now,
            }
        else:
            pid = f"CP-{new_id(8)}"
            await self._db.execute(
                "INSERT INTO communication_preferences "
                "(id, agent_role, preference_key, preference_value, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, agent_role, preference_key, preference_value, now),
            )
            return {
                "id": pid,
                "agent_role": agent_role,
                "preference_key": preference_key,
                "preference_value": preference_value,
                "updated_at": now,
            }

    async def get_style(self, agent_role: str) -> dict:
        """Get all communication preferences as a dict."""
        rows = await self._db.execute_fetchall(
            "SELECT preference_key, preference_value FROM communication_preferences "
            "WHERE agent_role = ?",
            (agent_role,),
        )
        return {row["preference_key"]: row["preference_value"] for row in rows}

    async def adapt_message(self, target_role: str, message_type: str) -> dict:
        """Return recommended style parameters for communicating with a target role."""
        style = await self.get_style(target_role)
        # Provide defaults if no preferences recorded
        verbosity = style.get("verbosity", "medium")
        fmt = style.get("format", "structured")
        detail = style.get("detail_level", "standard")
        return {
            "target_role": target_role,
            "message_type": message_type,
            "verbosity": verbosity,
            "format": fmt,
            "detail_level": detail,
        }

    # ------------------------------------------------------------------
    # Feature 12: Shared Mental Model Builder
    # ------------------------------------------------------------------

    async def assert_fact(
        self, key: str, value: str, source_agent: str, confidence: float = 1.0
    ) -> dict:
        """Add or update a fact in the shared mental model."""
        now = utcnow()
        confidence = clamp(confidence)

        # Check if this agent already asserted this key
        existing = await self._db.execute_fetchone(
            "SELECT * FROM mental_model_facts WHERE key = ? AND source_agent = ? AND retracted = 0",
            (key, source_agent),
        )
        if existing:
            await self._db.execute(
                "UPDATE mental_model_facts SET value = ?, confidence = ?, updated_at = ? "
                "WHERE id = ?",
                (value, confidence, now, existing["id"]),
            )
            return {
                "id": existing["id"],
                "key": key,
                "value": value,
                "source_agent": source_agent,
                "confidence": confidence,
                "updated": True,
            }
        else:
            fid = f"MMF-{new_id(8)}"
            await self._db.execute(
                "INSERT INTO mental_model_facts "
                "(id, key, value, source_agent, confidence, retracted, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (fid, key, value, source_agent, confidence, now, now),
            )
            return {
                "id": fid,
                "key": key,
                "value": value,
                "source_agent": source_agent,
                "confidence": confidence,
                "updated": False,
            }

    async def get_model(self, prefix: str | None = None) -> list[dict]:
        """Get the current shared mental model (all non-retracted facts)."""
        if prefix:
            return await self._db.execute_fetchall(
                "SELECT * FROM mental_model_facts WHERE key LIKE ? AND retracted = 0 "
                "ORDER BY key",
                (f"{prefix}%",),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM mental_model_facts WHERE retracted = 0 ORDER BY key",
        )

    async def retract_fact(self, key: str, agent_id: str) -> dict:
        """Mark a fact as retracted by the agent who asserted it."""
        now = utcnow()
        await self._db.execute(
            "UPDATE mental_model_facts SET retracted = 1, updated_at = ? "
            "WHERE key = ? AND source_agent = ? AND retracted = 0",
            (now, key, agent_id),
        )
        return {"key": key, "agent_id": agent_id, "retracted": True}

    async def get_conflicts(self) -> list[dict]:
        """Find facts where multiple agents asserted different values for the same key."""
        rows = await self._db.execute_fetchall(
            "SELECT key, COUNT(DISTINCT value) AS distinct_values, "
            "COUNT(DISTINCT source_agent) AS agent_count, "
            "GROUP_CONCAT(DISTINCT source_agent) AS agents, "
            "GROUP_CONCAT(DISTINCT value) AS conflicting_values "
            "FROM mental_model_facts "
            "WHERE retracted = 0 "
            "GROUP BY key "
            "HAVING COUNT(DISTINCT value) > 1",
        )
        return rows

    # ------------------------------------------------------------------
    # Feature 13: Implicit Coordination Detector
    # ------------------------------------------------------------------

    async def report_work_area(
        self, agent_id: str, file_paths: list[str], task_id: str
    ) -> dict:
        """Agent reports the files they are working on."""
        now = utcnow()
        for fp in file_paths:
            wid = f"WA-{new_id(8)}"
            await self._db.execute(
                "INSERT INTO work_areas (id, agent_id, file_path, task_id, reported_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (wid, agent_id, fp, task_id, now),
            )
        return {
            "agent_id": agent_id,
            "file_count": len(file_paths),
            "task_id": task_id,
            "reported_at": now,
        }

    async def detect_overlaps(self) -> list[dict]:
        """Find agents working on the same files without explicit coordination."""
        rows = await self._db.execute_fetchall(
            "SELECT w1.file_path, w1.agent_id AS agent_a, w2.agent_id AS agent_b, "
            "w1.task_id AS task_a, w2.task_id AS task_b "
            "FROM work_areas w1 "
            "JOIN work_areas w2 ON w1.file_path = w2.file_path AND w1.agent_id < w2.agent_id "
            "ORDER BY w1.file_path",
        )

        if not rows:
            return []

        now = utcnow()
        alerts = []
        seen = set()
        for row in rows:
            pair_key = (row["agent_a"], row["agent_b"], row["file_path"])
            if pair_key in seen:
                continue
            seen.add(pair_key)

            alert_id = f"CA-{new_id(8)}"
            agent_ids = json.dumps([row["agent_a"], row["agent_b"]])
            task_ids = json.dumps([row["task_a"], row["task_b"]])
            await self._db.execute(
                "INSERT INTO coordination_alerts "
                "(id, agent_ids, overlapping_files, task_ids, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (alert_id, agent_ids, row["file_path"], task_ids, now),
            )
            alerts.append({
                "id": alert_id,
                "agents": [row["agent_a"], row["agent_b"]],
                "file": row["file_path"],
                "tasks": [row["task_a"], row["task_b"]],
            })

        return alerts

    async def get_alerts(self, resolved: bool = False, limit: int = 20) -> list[dict]:
        """List coordination alerts."""
        return await self._db.execute_fetchall(
            "SELECT * FROM coordination_alerts WHERE resolved = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (int(resolved), limit),
        )

    async def resolve_alert(self, alert_id: str) -> dict:
        """Mark a coordination alert as resolved."""
        now = utcnow()
        await self._db.execute(
            "UPDATE coordination_alerts SET resolved = 1, resolved_at = ? WHERE id = ?",
            (now, alert_id),
        )
        return {"id": alert_id, "resolved": True, "resolved_at": now}

    # ------------------------------------------------------------------
    # Feature 14: Cross-Agent Context Bridge
    # ------------------------------------------------------------------

    async def share_context(
        self,
        from_agent: str,
        to_agent: str,
        context_key: str,
        context_value: str,
        relevance_score: float = 1.0,
    ) -> dict:
        """Share context from one agent to another."""
        sid = f"CS-{new_id(8)}"
        now = utcnow()
        relevance_score = clamp(relevance_score)
        await self._db.execute(
            "INSERT INTO context_shares "
            "(id, from_agent, to_agent, context_key, context_value, relevance_score, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, from_agent, to_agent, context_key, context_value, relevance_score, now),
        )
        return {
            "id": sid,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "context_key": context_key,
            "relevance_score": relevance_score,
            "created_at": now,
        }

    async def get_shared_context(self, agent_id: str, limit: int = 20) -> list[dict]:
        """Get context shared with a specific agent (unconsumed only)."""
        return await self._db.execute_fetchall(
            "SELECT * FROM context_shares WHERE to_agent = ? AND consumed = 0 "
            "ORDER BY relevance_score DESC, created_at DESC LIMIT ?",
            (agent_id, limit),
        )

    async def acknowledge_context(self, share_id: str) -> dict:
        """Mark a shared context item as consumed."""
        now = utcnow()
        await self._db.execute(
            "UPDATE context_shares SET consumed = 1, consumed_at = ? WHERE id = ?",
            (now, share_id),
        )
        return {"id": share_id, "consumed": True, "consumed_at": now}

    # ------------------------------------------------------------------
    # Feature 15: Collaboration Effectiveness Scorer
    # ------------------------------------------------------------------

    async def record_collaboration(
        self,
        agent_a: str,
        agent_b: str,
        task_id: str,
        effectiveness: float,
        notes: str | None = None,
    ) -> dict:
        """Record a collaboration effectiveness score between two agents."""
        cid = f"COL-{new_id(8)}"
        now = utcnow()
        effectiveness = clamp(effectiveness, 0.0, 5.0)
        # Normalize pair order so (a, b) and (b, a) are treated the same
        a, b = sorted([agent_a, agent_b])
        await self._db.execute(
            "INSERT INTO collaboration_scores "
            "(id, agent_a, agent_b, task_id, effectiveness, notes, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, a, b, task_id, effectiveness, notes, now),
        )
        return {
            "id": cid,
            "agent_a": a,
            "agent_b": b,
            "task_id": task_id,
            "effectiveness": effectiveness,
            "notes": notes,
            "recorded_at": now,
        }

    async def get_pair_score(self, agent_a: str, agent_b: str) -> dict:
        """Get the average effectiveness score for an agent pair."""
        a, b = sorted([agent_a, agent_b])
        row = await self._db.execute_fetchone(
            "SELECT AVG(effectiveness) AS avg_effectiveness, COUNT(*) AS collaboration_count "
            "FROM collaboration_scores WHERE agent_a = ? AND agent_b = ?",
            (a, b),
        )
        if not row or row["collaboration_count"] == 0:
            return {"agent_a": a, "agent_b": b, "avg_effectiveness": None, "collaboration_count": 0}
        return {
            "agent_a": a,
            "agent_b": b,
            "avg_effectiveness": round(row["avg_effectiveness"], 4),
            "collaboration_count": row["collaboration_count"],
        }

    async def get_best_pairs(self, limit: int = 10) -> list[dict]:
        """Get the top agent pairs by average effectiveness."""
        return await self._db.execute_fetchall(
            "SELECT agent_a, agent_b, AVG(effectiveness) AS avg_effectiveness, "
            "COUNT(*) AS collaboration_count "
            "FROM collaboration_scores "
            "GROUP BY agent_a, agent_b "
            "ORDER BY avg_effectiveness DESC "
            "LIMIT ?",
            (limit,),
        )

    async def get_worst_pairs(self, limit: int = 10) -> list[dict]:
        """Get the bottom agent pairs by average effectiveness."""
        return await self._db.execute_fetchall(
            "SELECT agent_a, agent_b, AVG(effectiveness) AS avg_effectiveness, "
            "COUNT(*) AS collaboration_count "
            "FROM collaboration_scores "
            "GROUP BY agent_a, agent_b "
            "ORDER BY avg_effectiveness ASC "
            "LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 16: Consensus Prediction Engine
    # ------------------------------------------------------------------

    async def predict_consensus(
        self, proposal_description: str, participants: list[str]
    ) -> dict:
        """Predict consensus outcome based on historical voting patterns.

        Uses past argument resolution history to estimate whether participants
        tend to agree or disagree.
        """
        pid = f"CPR-{new_id(8)}"
        now = utcnow()
        participants_json = json.dumps(participants)

        # Look at historical argument resolutions to estimate consensus likelihood
        history = await self._db.execute_fetchall(
            "SELECT status, winner_position FROM argument_sessions "
            "WHERE status = 'resolved' "
            "ORDER BY resolved_at DESC LIMIT 20",
        )

        # Simple heuristic: if most past arguments resolved, predict consensus
        total = len(history)
        if total == 0:
            predicted_outcome = "likely_consensus"
            predicted_confidence = 0.5
        else:
            resolved_count = sum(1 for h in history if h["status"] == "resolved")
            ratio = resolved_count / total
            if ratio >= 0.7:
                predicted_outcome = "likely_consensus"
                predicted_confidence = round(min(0.5 + ratio * 0.4, 0.95), 4)
            else:
                predicted_outcome = "likely_disagreement"
                predicted_confidence = round(0.5 + (1 - ratio) * 0.3, 4)

        await self._db.execute(
            "INSERT INTO consensus_predictions "
            "(id, proposal_description, participants, predicted_outcome, predicted_confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pid, proposal_description, participants_json, predicted_outcome, predicted_confidence, now),
        )
        return {
            "id": pid,
            "proposal_description": proposal_description,
            "participants": participants,
            "predicted_outcome": predicted_outcome,
            "predicted_confidence": predicted_confidence,
            "created_at": now,
        }

    async def record_prediction_outcome(
        self, prediction_id: str, actual_outcome: str
    ) -> dict:
        """Record the actual outcome of a consensus prediction."""
        now = utcnow()
        prediction = await self._db.execute_fetchone(
            "SELECT * FROM consensus_predictions WHERE id = ?",
            (prediction_id,),
        )
        if not prediction:
            return {"error": "Prediction not found"}

        correct = 1 if prediction["predicted_outcome"] == actual_outcome else 0
        await self._db.execute(
            "UPDATE consensus_predictions SET actual_outcome = ?, correct = ?, resolved_at = ? "
            "WHERE id = ?",
            (actual_outcome, correct, now, prediction_id),
        )
        return {
            "id": prediction_id,
            "predicted_outcome": prediction["predicted_outcome"],
            "actual_outcome": actual_outcome,
            "correct": bool(correct),
            "resolved_at": now,
        }

    async def get_prediction_accuracy(self) -> dict:
        """Get overall prediction accuracy percentage."""
        row = await self._db.execute_fetchone(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(correct), 0) AS correct_count "
            "FROM consensus_predictions "
            "WHERE actual_outcome IS NOT NULL",
        )
        if not row or row["total"] == 0:
            return {"accuracy": None, "total_predictions": 0, "correct": 0}
        accuracy = round(row["correct_count"] / row["total"] * 100, 2)
        return {
            "accuracy": accuracy,
            "total_predictions": row["total"],
            "correct": row["correct_count"],
        }
