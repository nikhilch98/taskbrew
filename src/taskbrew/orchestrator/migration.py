"""Simple database migration system."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Define migrations as (version, name, sql) tuples.
# Future migrations should be appended here.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "add_agent_memories", """
        CREATE TABLE IF NOT EXISTS agent_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_role TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_task_id TEXT,
            relevance_score REAL DEFAULT 1.0,
            access_count INTEGER DEFAULT 0,
            tags TEXT,
            project_id TEXT,
            created_at TEXT NOT NULL,
            last_accessed TEXT,
            FOREIGN KEY (source_task_id) REFERENCES tasks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_memories_role_type ON agent_memories(agent_role, memory_type);
        CREATE INDEX IF NOT EXISTS idx_memories_tags ON agent_memories(tags);
    """),
    (2, "add_knowledge_graph", """
        CREATE TABLE IF NOT EXISTS knowledge_graph_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type TEXT NOT NULL,
            name TEXT NOT NULL,
            file_path TEXT,
            description TEXT,
            metadata TEXT,
            last_updated TEXT NOT NULL,
            UNIQUE(node_type, name, file_path)
        );
        CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES knowledge_graph_nodes(id),
            target_id INTEGER NOT NULL REFERENCES knowledge_graph_nodes(id),
            edge_type TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON knowledge_graph_edges(source_id, edge_type);
        CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON knowledge_graph_edges(target_id, edge_type);
    """),
    (3, "add_review_feedback", """
        CREATE TABLE IF NOT EXISTS review_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            feedback_type TEXT NOT NULL,
            pattern TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_review_feedback_reviewer ON review_feedback(reviewer, feedback_type);
    """),
    (4, "enhance_agent_messages", """
        ALTER TABLE agent_messages ADD COLUMN message_type TEXT DEFAULT 'direct';
        ALTER TABLE agent_messages ADD COLUMN priority TEXT DEFAULT 'normal';
        ALTER TABLE agent_messages ADD COLUMN delivered_at TEXT;
        ALTER TABLE agent_messages ADD COLUMN thread_id TEXT;
    """),
    (5, "add_escalations", """
        CREATE TABLE IF NOT EXISTS escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT,
            reason TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'open',
            resolution TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_escalations_status ON escalations(status, created_at);
    """),
    (6, "add_checkpoints", """
        CREATE TABLE IF NOT EXISTS checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            context TEXT,
            decided_by TEXT,
            decided_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_checkpoints_pending ON checkpoints(status) WHERE status = 'pending';
    """),
    (7, "add_task_plans", """
        CREATE TABLE IF NOT EXISTS task_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            plan_type TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL,
            created_by TEXT,
            status TEXT DEFAULT 'draft',
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_plans_task ON task_plans(task_id, plan_type);
    """),
    (8, "add_context_snapshots", """
        CREATE TABLE IF NOT EXISTS context_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_type TEXT NOT NULL,
            scope TEXT,
            data TEXT NOT NULL,
            expires_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_context_type ON context_snapshots(context_type, scope);
    """),
    (9, "add_quality_scores", """
        CREATE TABLE IF NOT EXISTS quality_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            score_type TEXT NOT NULL,
            score REAL NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_quality_task ON quality_scores(task_id, score_type);
    """),
    (10, "add_skill_badges", """
        CREATE TABLE IF NOT EXISTS skill_badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_role TEXT NOT NULL,
            skill_type TEXT NOT NULL,
            proficiency REAL NOT NULL DEFAULT 0.5,
            tasks_completed INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 0.0,
            last_updated TEXT NOT NULL,
            UNIQUE(agent_role, skill_type)
        );
        CREATE INDEX IF NOT EXISTS idx_badges_role ON skill_badges(agent_role);
    """),
    (11, "add_model_routing_rules", """
        CREATE TABLE IF NOT EXISTS model_routing_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            complexity_threshold TEXT NOT NULL,
            model TEXT NOT NULL,
            criteria TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_routing_role ON model_routing_rules(role, active);
    """),
    (12, "add_autonomous_tables", """
        CREATE TABLE IF NOT EXISTS task_decompositions (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            subtask_title TEXT NOT NULL,
            subtask_description TEXT NOT NULL,
            reasoning TEXT,
            estimated_effort TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decompositions_task ON task_decompositions(task_id);

        CREATE TABLE IF NOT EXISTS work_discoveries (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            discovery_type TEXT NOT NULL,
            file_path TEXT,
            description TEXT,
            priority TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_discoveries_status ON work_discoveries(status, created_at);

        CREATE TABLE IF NOT EXISTS priority_bids (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            bid_score REAL NOT NULL,
            reasoning TEXT,
            workload_factor REAL,
            skill_factor REAL,
            urgency_factor REAL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bids_task ON priority_bids(task_id, bid_score);

        CREATE TABLE IF NOT EXISTS retry_strategies (
            id TEXT PRIMARY KEY,
            failure_type TEXT NOT NULL,
            strategy TEXT NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            avg_recovery_time_ms REAL DEFAULT 0,
            last_updated TEXT NOT NULL,
            UNIQUE(failure_type, strategy)
        );
        CREATE INDEX IF NOT EXISTS idx_retry_failure ON retry_strategies(failure_type);

        CREATE TABLE IF NOT EXISTS pipeline_fixes (
            id TEXT PRIMARY KEY,
            failure_signature TEXT NOT NULL,
            fix_applied TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,
            source_task_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fixes_signature ON pipeline_fixes(failure_signature);
    """),
    (13, "add_code_intel_tables", """
        CREATE TABLE IF NOT EXISTS code_embeddings (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            symbol_name TEXT NOT NULL,
            symbol_type TEXT NOT NULL,
            embedding TEXT,
            description TEXT,
            last_updated TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_embeddings_file ON code_embeddings(file_path, symbol_name);

        CREATE TABLE IF NOT EXISTS architecture_patterns (
            id TEXT PRIMARY KEY,
            pattern_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            details TEXT,
            severity TEXT NOT NULL DEFAULT 'info',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_patterns_type ON architecture_patterns(pattern_type, created_at);

        CREATE TABLE IF NOT EXISTS technical_debt (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            debt_type TEXT NOT NULL,
            score REAL NOT NULL,
            details TEXT,
            trend TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_debt_score ON technical_debt(score);

        CREATE TABLE IF NOT EXISTS test_gaps (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            function_name TEXT NOT NULL,
            gap_type TEXT NOT NULL,
            suggested_test TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gaps_file ON test_gaps(file_path);
    """),
    (14, "add_learning_tables", """
        CREATE TABLE IF NOT EXISTS prompt_experiments (
            id TEXT PRIMARY KEY,
            experiment_name TEXT NOT NULL,
            agent_role TEXT NOT NULL,
            variant_key TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            trials INTEGER NOT NULL DEFAULT 0,
            successes INTEGER NOT NULL DEFAULT 0,
            avg_quality_score REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_experiments_name ON prompt_experiments(experiment_name);

        CREATE TABLE IF NOT EXISTS cross_project_knowledge (
            id TEXT PRIMARY KEY,
            source_project TEXT NOT NULL,
            knowledge_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            applicability_score REAL DEFAULT 1.0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cpk_type ON cross_project_knowledge(knowledge_type, applicability_score);

        CREATE TABLE IF NOT EXISTS agent_benchmarks (
            id TEXT PRIMARY KEY,
            agent_role TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            period TEXT,
            details TEXT,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_benchmarks_role ON agent_benchmarks(agent_role, metric_name);

        CREATE TABLE IF NOT EXISTS codebase_conventions (
            id TEXT PRIMARY KEY,
            convention_type TEXT NOT NULL,
            pattern TEXT NOT NULL,
            frequency INTEGER NOT NULL DEFAULT 0,
            confidence REAL DEFAULT 0.0,
            examples TEXT,
            last_updated TEXT NOT NULL,
            UNIQUE(convention_type, pattern)
        );
        CREATE INDEX IF NOT EXISTS idx_conventions_type ON codebase_conventions(convention_type);

        CREATE TABLE IF NOT EXISTS error_clusters (
            id TEXT PRIMARY KEY,
            cluster_name TEXT NOT NULL UNIQUE,
            root_cause TEXT,
            error_pattern TEXT,
            occurrence_count INTEGER NOT NULL DEFAULT 0,
            last_seen TEXT,
            prevention_hint TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_clusters_name ON error_clusters(cluster_name);
    """),
    (15, "add_coordination_tables", """
        CREATE TABLE IF NOT EXISTS standup_reports (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            report_type TEXT NOT NULL DEFAULT 'daily',
            completed_tasks TEXT,
            in_progress_tasks TEXT,
            blockers TEXT,
            plan TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_standups_agent ON standup_reports(agent_id, created_at);

        CREATE TABLE IF NOT EXISTS file_locks (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL UNIQUE,
            locked_by TEXT NOT NULL,
            task_id TEXT,
            locked_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_locks_file ON file_locks(file_path);

        CREATE TABLE IF NOT EXISTS knowledge_digests (
            id TEXT PRIMARY KEY,
            digest_type TEXT NOT NULL,
            content TEXT NOT NULL,
            target_roles TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_digests_type ON knowledge_digests(digest_type, created_at);

        CREATE TABLE IF NOT EXISTS mentor_pairs (
            id TEXT PRIMARY KEY,
            mentor_role TEXT NOT NULL,
            mentee_role TEXT NOT NULL,
            skill_area TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pairs_roles ON mentor_pairs(mentor_role, mentee_role);

        CREATE TABLE IF NOT EXISTS consensus_votes (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            voter_id TEXT NOT NULL,
            vote TEXT NOT NULL,
            reasoning TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(proposal_id, voter_id)
        );
        CREATE INDEX IF NOT EXISTS idx_votes_proposal ON consensus_votes(proposal_id);

        CREATE TABLE IF NOT EXISTS progress_heartbeats (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            progress_pct REAL NOT NULL,
            status_message TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_heartbeats_task ON progress_heartbeats(task_id, created_at);
    """),
    (16, "add_testing_quality_tables", """
        CREATE TABLE IF NOT EXISTS generated_tests (
            id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            function_name TEXT NOT NULL,
            test_skeleton TEXT NOT NULL,
            test_type TEXT NOT NULL DEFAULT 'unit',
            status TEXT NOT NULL DEFAULT 'generated',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mutation_results (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            mutation_type TEXT NOT NULL,
            survived INTEGER NOT NULL DEFAULT 0,
            killed INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            details TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS regression_predictions (
            id TEXT PRIMARY KEY,
            pr_identifier TEXT,
            risk_score REAL NOT NULL,
            risk_factors TEXT,
            files_changed TEXT,
            predicted_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS review_checklists (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            task_type TEXT,
            checklist_items TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS doc_drift_reports (
            id TEXT PRIMARY KEY,
            doc_file TEXT NOT NULL,
            code_file TEXT,
            drift_type TEXT NOT NULL,
            details TEXT,
            severity TEXT NOT NULL DEFAULT 'medium',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS perf_baselines (
            id TEXT PRIMARY KEY,
            test_name TEXT UNIQUE NOT NULL,
            avg_duration_ms REAL NOT NULL DEFAULT 0,
            std_deviation_ms REAL NOT NULL DEFAULT 0,
            sample_count INTEGER NOT NULL DEFAULT 0,
            last_updated TEXT NOT NULL
        );
    """),
    (17, "add_security_intel_tables", """
        CREATE TABLE IF NOT EXISTS vulnerability_scans (
            id TEXT PRIMARY KEY,
            package_name TEXT NOT NULL,
            installed_version TEXT,
            vulnerability TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            fix_version TEXT,
            scanned_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS secret_detections (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            line_number INTEGER,
            secret_type TEXT NOT NULL,
            pattern_matched TEXT,
            severity TEXT NOT NULL DEFAULT 'critical',
            detected_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sast_findings (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            line_number INTEGER,
            finding_type TEXT NOT NULL,
            description TEXT,
            severity TEXT NOT NULL DEFAULT 'high',
            code_snippet TEXT,
            detected_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS license_checks (
            id TEXT PRIMARY KEY,
            package_name TEXT NOT NULL,
            license_type TEXT,
            license_category TEXT,
            compliant INTEGER NOT NULL DEFAULT 1,
            checked_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS security_flags (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            flag_reason TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            reviewed INTEGER NOT NULL DEFAULT 0,
            flagged_at TEXT NOT NULL
        );
    """),
    (18, "add_observability_tables", """
        CREATE TABLE IF NOT EXISTS decision_audit_log (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            task_id TEXT,
            decision_type TEXT NOT NULL,
            decision TEXT NOT NULL,
            reasoning TEXT,
            context TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_behavior_metrics (
            id TEXT PRIMARY KEY,
            agent_role TEXT NOT NULL,
            metric_type TEXT NOT NULL,
            value REAL NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cost_attributions (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            feature_tag TEXT,
            agent_id TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            attributed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_bottlenecks (
            id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            avg_wait_ms REAL NOT NULL DEFAULT 0,
            avg_process_ms REAL NOT NULL DEFAULT 0,
            queue_depth INTEGER NOT NULL DEFAULT 0,
            detected_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS anomaly_detections (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            anomaly_type TEXT NOT NULL,
            description TEXT,
            severity TEXT NOT NULL DEFAULT 'medium',
            metric_value REAL,
            expected_range TEXT,
            detected_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS quality_trends (
            id TEXT PRIMARY KEY,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            dimension TEXT,
            period TEXT NOT NULL DEFAULT 'daily',
            recorded_at TEXT NOT NULL
        );
    """),
    (19, "add_advanced_planning_tables", """
        CREATE TABLE IF NOT EXISTS scheduling_graph (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            depends_on TEXT,
            scheduled_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS resource_snapshots (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            active_tasks INTEGER NOT NULL DEFAULT 0,
            capacity TEXT NOT NULL DEFAULT 'available',
            snapshot_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS deadline_estimates (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            estimated_hours REAL,
            confidence_low REAL,
            confidence_high REAL,
            based_on_samples INTEGER NOT NULL DEFAULT 0,
            estimated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scope_creep_flags (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            original_length INTEGER,
            current_length INTEGER,
            growth_pct REAL,
            flagged_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS delivery_increments (
            id TEXT PRIMARY KEY,
            feature_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            increment_order INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'planned',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS post_mortems (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            group_id TEXT,
            total_tasks INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            success_rate REAL NOT NULL DEFAULT 0,
            avg_duration_hours REAL,
            common_failures TEXT,
            lessons TEXT,
            created_at TEXT NOT NULL
        );
    """),
    (20, "V3: Self-improvement tables (prompt evolution, strategies, skill transfer, cognitive load, reflections, failure taxonomy, personality, confidence)", """
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
        CREATE INDEX IF NOT EXISTS idx_prompt_versions_role ON prompt_versions(agent_role);
        CREATE INDEX IF NOT EXISTS idx_prompt_performance_version ON prompt_performance(version_id);
        CREATE INDEX IF NOT EXISTS idx_strategy_portfolio_role ON strategy_portfolio(agent_role, strategy_type);
        CREATE INDEX IF NOT EXISTS idx_skill_transfers_target ON skill_transfers(target_role, status);
        CREATE INDEX IF NOT EXISTS idx_cognitive_load_agent ON cognitive_load_snapshots(agent_id, recorded_at);
        CREATE INDEX IF NOT EXISTS idx_task_reflections_agent ON task_reflections(agent_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_failure_modes_category ON failure_modes(category, severity);
        CREATE INDEX IF NOT EXISTS idx_agent_profiles_role ON agent_profiles(agent_role);
        CREATE INDEX IF NOT EXISTS idx_confidence_records_agent ON confidence_records(agent_id, recorded_at);
    """),
    (21, "V3: Social intelligence tables (arguments, trust, communication, mental models, coordination, context sharing, collaboration, consensus)", """
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
        CREATE INDEX IF NOT EXISTS idx_argument_sessions_status ON argument_sessions(status);
        CREATE INDEX IF NOT EXISTS idx_argument_evidence_session ON argument_evidence(session_id);
        CREATE INDEX IF NOT EXISTS idx_trust_scores_from ON trust_scores(from_agent, to_agent);
        CREATE INDEX IF NOT EXISTS idx_communication_prefs_role ON communication_preferences(agent_role);
        CREATE INDEX IF NOT EXISTS idx_mental_model_key ON mental_model_facts(key, retracted);
        CREATE INDEX IF NOT EXISTS idx_coordination_alerts_resolved ON coordination_alerts(resolved, created_at);
        CREATE INDEX IF NOT EXISTS idx_work_areas_agent ON work_areas(agent_id, file_path);
        CREATE INDEX IF NOT EXISTS idx_context_shares_to ON context_shares(to_agent, consumed);
        CREATE INDEX IF NOT EXISTS idx_collaboration_scores_pair ON collaboration_scores(agent_a, agent_b);
        CREATE INDEX IF NOT EXISTS idx_consensus_predictions_outcome ON consensus_predictions(predicted_outcome);
    """),
    (22, "V3: Code reasoning tables (semantic index, dependencies, impact, style, refactoring, debt, API versions, narratives, invariants)", """
        CREATE TABLE IF NOT EXISTS semantic_index (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            function_name TEXT NOT NULL,
            intent_description TEXT NOT NULL,
            keywords TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dependency_graph (
            id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            target_file TEXT NOT NULL,
            dep_type TEXT NOT NULL DEFAULT 'import',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS impact_predictions (
            id TEXT PRIMARY KEY,
            changed_file TEXT NOT NULL,
            affected_files TEXT NOT NULL,
            max_depth INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS style_patterns (
            id TEXT PRIMARY KEY,
            pattern_name TEXT NOT NULL,
            category TEXT NOT NULL,
            example TEXT NOT NULL,
            file_path TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS refactoring_opportunities (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            opportunity_type TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'medium',
            dismissed INTEGER NOT NULL DEFAULT 0,
            dismiss_reason TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS debt_items (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            effort_estimate INTEGER NOT NULL,
            business_impact INTEGER NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            resolution_notes TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS api_versions (
            id TEXT PRIMARY KEY,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            version TEXT NOT NULL,
            schema_hash TEXT NOT NULL,
            breaking_change INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS code_narratives (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            function_name TEXT NOT NULL,
            code_snippet TEXT,
            narrative_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS code_invariants (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            function_name TEXT NOT NULL,
            invariant_expression TEXT NOT NULL,
            invariant_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_semantic_index_file ON semantic_index(file_path, function_name);
        CREATE INDEX IF NOT EXISTS idx_dependency_graph_source ON dependency_graph(source_file);
        CREATE INDEX IF NOT EXISTS idx_dependency_graph_target ON dependency_graph(target_file);
        CREATE INDEX IF NOT EXISTS idx_impact_predictions_file ON impact_predictions(changed_file);
        CREATE INDEX IF NOT EXISTS idx_style_patterns_category ON style_patterns(category);
        CREATE INDEX IF NOT EXISTS idx_refactoring_opps_file ON refactoring_opportunities(file_path, dismissed);
        CREATE INDEX IF NOT EXISTS idx_debt_items_resolved ON debt_items(resolved, category);
        CREATE INDEX IF NOT EXISTS idx_api_versions_endpoint ON api_versions(endpoint, method);
        CREATE INDEX IF NOT EXISTS idx_code_narratives_file ON code_narratives(file_path, function_name);
        CREATE INDEX IF NOT EXISTS idx_code_invariants_file ON code_invariants(file_path, function_name);
    """),
    (23, "V3: Task intelligence tables (complexity, prerequisites, decomposition, parallelism, context budgets, outcomes, fingerprints, effort tracking)", """
        CREATE TABLE IF NOT EXISTS complexity_estimates (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT,
            files_involved TEXT,
            complexity_score INTEGER NOT NULL,
            actual_complexity INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS detected_prerequisites (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            prerequisite_task_id TEXT,
            reason TEXT NOT NULL,
            confirmed INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decomposition_metrics (
            id TEXT PRIMARY KEY,
            parent_task_id TEXT NOT NULL,
            subtask_count INTEGER NOT NULL,
            avg_subtask_duration_ms REAL NOT NULL,
            success_rate REAL NOT NULL,
            task_type TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parallel_opportunities (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            task_set TEXT NOT NULL,
            reason TEXT NOT NULL,
            exploited INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS context_budgets (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE,
            estimated_files INTEGER NOT NULL,
            estimated_tokens_per_file INTEGER NOT NULL DEFAULT 500,
            total_budget INTEGER NOT NULL,
            actual_tokens_used INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outcome_predictions (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            complexity_score INTEGER NOT NULL,
            agent_role TEXT NOT NULL,
            historical_success_rate REAL,
            predicted_success REAL NOT NULL,
            actual_success INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_fingerprints (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT,
            task_type TEXT,
            keywords TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS effort_tracking (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE,
            estimated_duration_ms INTEGER NOT NULL,
            started_at_epoch_ms INTEGER NOT NULL,
            completed_at_epoch_ms INTEGER,
            actual_duration_ms INTEGER,
            drift_ratio REAL,
            status TEXT NOT NULL DEFAULT 'tracking',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_complexity_estimates_task ON complexity_estimates(task_id);
        CREATE INDEX IF NOT EXISTS idx_detected_prerequisites_task ON detected_prerequisites(task_id);
        CREATE INDEX IF NOT EXISTS idx_decomposition_metrics_parent ON decomposition_metrics(parent_task_id);
        CREATE INDEX IF NOT EXISTS idx_parallel_opportunities_group ON parallel_opportunities(group_id, exploited);
        CREATE INDEX IF NOT EXISTS idx_context_budgets_task ON context_budgets(task_id);
        CREATE INDEX IF NOT EXISTS idx_outcome_predictions_task ON outcome_predictions(task_id, agent_role);
        CREATE INDEX IF NOT EXISTS idx_task_fingerprints_task ON task_fingerprints(task_id);
        CREATE INDEX IF NOT EXISTS idx_effort_tracking_task ON effort_tracking(task_id, status);
    """),
    (24, "V3: Verification tables (regression fingerprints, test mappings, test runs, behavioral specs, review annotations, quality gates)", """
        CREATE TABLE IF NOT EXISTS regression_fingerprints (
            id TEXT PRIMARY KEY,
            test_name TEXT NOT NULL,
            error_message TEXT NOT NULL,
            failing_commit TEXT NOT NULL,
            last_passing_commit TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS test_file_mappings (
            id TEXT PRIMARY KEY,
            test_file TEXT NOT NULL,
            source_file TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS test_runs (
            id TEXT PRIMARY KEY,
            test_name TEXT NOT NULL,
            passed INTEGER NOT NULL,
            duration_ms INTEGER,
            run_id TEXT,
            quarantined INTEGER NOT NULL DEFAULT 0,
            quarantine_reason TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS behavioral_specs (
            id TEXT PRIMARY KEY,
            test_file TEXT NOT NULL,
            test_name TEXT NOT NULL,
            asserted_behavior TEXT NOT NULL,
            source_file TEXT,
            documented INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_annotations (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            line_number INTEGER NOT NULL,
            annotation_type TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quality_gates (
            id TEXT PRIMARY KEY,
            gate_name TEXT NOT NULL UNIQUE,
            conditions TEXT NOT NULL,
            risk_level TEXT NOT NULL DEFAULT 'standard',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gate_results (
            id TEXT PRIMARY KEY,
            gate_name TEXT NOT NULL,
            passed INTEGER NOT NULL,
            details TEXT,
            metrics TEXT,
            evaluated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_regression_fps_test ON regression_fingerprints(test_name);
        CREATE INDEX IF NOT EXISTS idx_test_file_mappings_source ON test_file_mappings(source_file);
        CREATE INDEX IF NOT EXISTS idx_test_file_mappings_test ON test_file_mappings(test_file);
        CREATE INDEX IF NOT EXISTS idx_test_runs_name ON test_runs(test_name, quarantined);
        CREATE INDEX IF NOT EXISTS idx_behavioral_specs_test ON behavioral_specs(test_file, test_name);
        CREATE INDEX IF NOT EXISTS idx_review_annotations_file ON review_annotations(file_path, line_number);
        CREATE INDEX IF NOT EXISTS idx_quality_gates_name ON quality_gates(gate_name);
        CREATE INDEX IF NOT EXISTS idx_gate_results_name ON gate_results(gate_name, evaluated_at);
    """),
    (25, "V3: Process intelligence tables (velocity, risk scores, process metrics, readiness, stakeholder impacts, retrospectives)", """
        CREATE TABLE IF NOT EXISTS velocity_samples (
            id TEXT PRIMARY KEY,
            sprint_id TEXT NOT NULL,
            tasks_completed INTEGER NOT NULL,
            story_points REAL,
            duration_days REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS risk_scores (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL UNIQUE,
            change_frequency INTEGER NOT NULL DEFAULT 0,
            complexity_score REAL NOT NULL DEFAULT 0.0,
            test_coverage_pct REAL NOT NULL DEFAULT 0.0,
            risk_score REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS process_metrics (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS readiness_assessments (
            id TEXT PRIMARY KEY,
            release_id TEXT NOT NULL,
            score REAL NOT NULL,
            metrics TEXT NOT NULL,
            breakdown TEXT NOT NULL,
            assessed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stakeholder_impacts (
            id TEXT PRIMARY KEY,
            change_id TEXT NOT NULL,
            stakeholder_group TEXT NOT NULL,
            impact_level TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sprint_retrospectives (
            id TEXT PRIMARY KEY,
            sprint_id TEXT NOT NULL UNIQUE,
            what_improved TEXT,
            what_regressed TEXT,
            stalled TEXT,
            recommendations TEXT,
            generated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_velocity_samples_sprint ON velocity_samples(sprint_id);
        CREATE INDEX IF NOT EXISTS idx_risk_scores_file ON risk_scores(file_path);
        CREATE INDEX IF NOT EXISTS idx_risk_scores_score ON risk_scores(risk_score);
        CREATE INDEX IF NOT EXISTS idx_process_metrics_task ON process_metrics(task_id, phase);
        CREATE INDEX IF NOT EXISTS idx_readiness_assessments_release ON readiness_assessments(release_id);
        CREATE INDEX IF NOT EXISTS idx_stakeholder_impacts_change ON stakeholder_impacts(change_id);
        CREATE INDEX IF NOT EXISTS idx_stakeholder_impacts_group ON stakeholder_impacts(stakeholder_group);
        CREATE INDEX IF NOT EXISTS idx_sprint_retrospectives_sprint ON sprint_retrospectives(sprint_id);
    """),
    (26, "V3: Knowledge management tables (entries, staleness, doc gaps, institutional knowledge, compression profiles)", """
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id TEXT PRIMARY KEY,
            key TEXT NOT NULL,
            content TEXT NOT NULL,
            source_file TEXT,
            source_agent TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_staleness (
            id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL REFERENCES knowledge_entries(id),
            flagged_at TEXT NOT NULL,
            reason TEXT,
            resolved INTEGER NOT NULL DEFAULT 0,
            resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS doc_gaps (
            id TEXT PRIMARY KEY,
            symbol_name TEXT NOT NULL,
            symbol_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            doc_reference TEXT,
            resolved INTEGER NOT NULL DEFAULT 0,
            resolved_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS institutional_knowledge (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_ref TEXT,
            content TEXT NOT NULL,
            tags TEXT,
            file_path TEXT,
            line_number INTEGER,
            author TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS compression_profiles (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            original_tokens INTEGER NOT NULL,
            compressed_tokens INTEGER NOT NULL,
            items_kept INTEGER NOT NULL,
            items_dropped INTEGER NOT NULL,
            strategy TEXT NOT NULL DEFAULT 'salience',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_entries_key ON knowledge_entries(key);
        CREATE INDEX IF NOT EXISTS idx_knowledge_staleness_entry ON knowledge_staleness(entry_id, resolved);
        CREATE INDEX IF NOT EXISTS idx_doc_gaps_file ON doc_gaps(file_path, resolved);
        CREATE INDEX IF NOT EXISTS idx_doc_gaps_severity ON doc_gaps(severity, resolved);
        CREATE INDEX IF NOT EXISTS idx_institutional_knowledge_type ON institutional_knowledge(source_type);
        CREATE INDEX IF NOT EXISTS idx_institutional_knowledge_file ON institutional_knowledge(file_path);
        CREATE INDEX IF NOT EXISTS idx_compression_profiles_task ON compression_profiles(task_id);
    """),
    (27, "V3: Compliance tables (threat models, threat entries, compliance rules, checks, exemptions)", """
        CREATE TABLE IF NOT EXISTS threat_models (
            id TEXT PRIMARY KEY,
            feature_name TEXT NOT NULL,
            description TEXT,
            data_flows TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS threat_entries (
            id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL REFERENCES threat_models(id),
            threat_type TEXT NOT NULL,
            description TEXT NOT NULL,
            risk_level TEXT NOT NULL DEFAULT 'medium',
            mitigation TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS compliance_rules (
            id TEXT PRIMARY KEY,
            rule_id TEXT NOT NULL UNIQUE,
            framework TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            check_pattern TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS compliance_checks (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            violations_found INTEGER NOT NULL DEFAULT 0,
            rules_checked INTEGER NOT NULL DEFAULT 0,
            details TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS compliance_exemptions (
            id TEXT PRIMARY KEY,
            rule_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            reason TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(rule_id, file_path)
        );
        CREATE INDEX IF NOT EXISTS idx_threat_models_feature ON threat_models(feature_name);
        CREATE INDEX IF NOT EXISTS idx_threat_entries_model ON threat_entries(model_id, threat_type);
        CREATE INDEX IF NOT EXISTS idx_threat_entries_risk ON threat_entries(risk_level);
        CREATE INDEX IF NOT EXISTS idx_compliance_rules_framework ON compliance_rules(framework, category);
        CREATE INDEX IF NOT EXISTS idx_compliance_checks_file ON compliance_checks(file_path);
        CREATE INDEX IF NOT EXISTS idx_compliance_exemptions_rule ON compliance_exemptions(rule_id, file_path);
    """),
]


class MigrationManager:
    """Apply sequential schema migrations to the database.

    The ``schema_migrations`` table tracks which migrations have already been
    applied so they are never re-run.

    Parameters
    ----------
    db:
        An initialised :class:`~taskbrew.orchestrator.database.Database` instance.
    """

    def __init__(self, db) -> None:
        self._db = db

    async def get_current_version(self) -> int:
        """Return the highest migration version that has been applied."""
        try:
            row = await self._db.execute_fetchone(
                "SELECT MAX(version) as version FROM schema_migrations"
            )
            return row["version"] if row and row["version"] else 0
        except Exception:
            return 0

    async def apply_pending(self) -> list[str]:
        """Apply all pending migrations.

        Returns
        -------
        list[str]
            Names of the migrations that were applied.
        """
        current = await self.get_current_version()
        applied: list[str] = []

        for version, name, sql in MIGRATIONS:
            if version > current:
                logger.info("Applying migration %d: %s", version, name)
                await self._db.executescript(sql)
                await self._db.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (version, name, datetime.now(timezone.utc).isoformat()),
                )
                applied.append(name)

        return applied
