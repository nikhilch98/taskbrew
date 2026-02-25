# Configuration Audit Findings - CD-173

**Date:** 2026-02-25
**Status:** Audit Complete
**Scope:** docs/, config/, pipelines/ directories

---

## Executive Summary

This audit identified **7 significant duplication patterns** across the configuration, documentation, and pipeline layers. These duplications create maintenance risks and introduce the possibility of configuration drift between layers. The most critical issue is the **system prompt replication across all 5 role YAML files**, followed by **workflow sequence duplication** between pipeline definitions and role routing configuration.

---

## Duplication Findings

### 1. ⚠️ CRITICAL: System Prompts Duplicated Across All 5 Role Files

**Severity:** CRITICAL
**Impact:** High maintenance burden; changes to role responsibilities must be manually updated in 5 places

**Locations:**
- `config/roles/architect.yaml` (lines 7-26)
- `config/roles/coder.yaml` (lines 7-25)
- `config/roles/reviewer.yaml` (lines 7-23)
- `config/roles/tester.yaml` (lines 7-13)
- `config/roles/pm.yaml` (lines 7-25)

**Current Pattern:**
Each role YAML file contains a full `system_prompt` field with role-specific instructions. While the content is different for each role, they share:
- Header structure: `You are a [Role] on an AI development team.`
- Common section: `Your responsibilities:` followed by 3-4 numbered items
- Many include instructions for using `create_task` tool with identical parameter guidance
- All contain role-specific tool lists inline with the prompt

**Example - Architect vs Coder comparison:**

**architect.yaml (lines 7-26):**
```yaml
system_prompt: |
  You are a Software Architect on an AI development team.
  Your responsibilities:
  1. Create technical design documents for PRDs assigned to you
  2. Identify and document tech debt with concrete fix plans
  3. Review architecture docs created by peer architects
  4. You do NOT write implementation code

  After completing your design, use the create_task tool to create coder tasks:
  - group_id: Use the group ID from your task context
  - assigned_to: "coder"
  ...
```

**coder.yaml (lines 7-25):**
```yaml
system_prompt: |
  You are a Software Engineer (Coder) on an AI development team.
  Your responsibilities:
  1. Implement features based on technical design documents
  2. Write clean, tested code on feature branches
  3. Make atomic commits with clear messages
  4. Create test and review tasks when implementation is complete

  When you finish implementing:
  - Create a task for Tester with status pending
  ...
```

**Duplication Points:**
- Both use identical instruction templates for tool usage
- Both describe routing to downstream roles with similar structure
- Both reference task creation with same parameter explanations
- Both include git workflow instructions with similar formatting

**Recommendation:**
Create a **centralized prompt template system** that:
1. Defines common prompt sections in `config/prompts/` directory
2. Keeps role-specific responsibilities in each role YAML as `responsibilities: []`
3. Generates full system_prompt at runtime by combining template + role responsibilities
4. Stores template components (header, task_creation_instructions, git_workflow) once
5. Source of truth: **config/prompts/** (new)

---

### 2. ⚠️ HIGH: Workflow Sequences Duplicated Between Pipelines and Role Routing

**Severity:** HIGH
**Impact:** Workflow changes must be updated in 2 locations; risk of routing divergence

**Locations:**
- `pipelines/feature_dev.yaml` (defines full sequence in steps)
- `config/roles/<role>.yaml` (routes_to field in each file)

**Current Pattern:**

**pipelines/feature_dev.yaml** defines workflow as:
```yaml
steps:
  - agent: pm
  - agent: researcher
  - agent: architect
  - agent: coder
  - agent: tester
  - agent: reviewer
```

**Role files define routing individually:**

**config/roles/architect.yaml:**
```yaml
routes_to:
  - role: coder
    task_types: [implementation, bug_fix]
  - role: architect
    task_types: [architecture_review]
```

**config/roles/coder.yaml:**
```yaml
routes_to:
  - role: tester
    task_types: [qa_verification]
  - role: reviewer
    task_types: [code_review]
```

**config/roles/reviewer.yaml:**
```yaml
routes_to:
  - role: coder
    task_types: [revision]
  - role: architect
    task_types: [rejection]
```

**Duplication Points:**
- Pipeline explicitly defines pm → researcher → architect → coder → tester → reviewer sequence
- Each role's routes_to field defines local routing rules that mirror this sequence
- Changes to workflow require updating both files

**Example of Risk:** If feature_dev pipeline should change from `pm → researcher → architect` to `pm → architect` (skip researcher), the code/tester/reviewer routing would become out of sync.

**Recommendation:**
Choose **pipelines/ as source of truth** for workflow sequences and:
1. Remove or minimize `routes_to` fields from individual role YAML files
2. Store `routes_to` definitions in `config/workflows/` or `config/routing/`
3. Create a mapping from pipeline definitions to role routing rules
4. Source of truth: **pipelines/** (single location)
5. Generated reference: **config/routing/** (derived from pipelines)

---

### 3. ⚠️ HIGH: Tool List Configurations Partially Duplicated

**Severity:** HIGH
**Impact:** Tool availability inconsistencies; duplication of tool set management

**Locations:**
- `config/roles/architect.yaml` (line 28)
- `config/roles/coder.yaml` (line 27)
- `config/roles/reviewer.yaml` (line 25)
- `config/roles/tester.yaml` (line 15)
- `config/roles/pm.yaml` (line 27)

**Current Pattern:**

| Role | Tools |
|------|-------|
| architect | `[Read, Glob, Grep, Write, WebSearch, mcp__task-tools__create_task]` |
| coder | `[Read, Write, Edit, Bash, Glob, Grep]` |
| reviewer | `[Read, Glob, Grep, Bash]` |
| tester | `[Read, Write, Edit, Bash, Glob, Grep]` |
| pm | `[Read, Glob, Grep, WebSearch, mcp__task-tools__create_task]` |

**Duplication Points:**
- `Read, Glob, Grep` appears in 5 out of 5 roles
- `Bash` appears in 3 out of 5 roles (coder, reviewer, tester)
- `Write, Edit` appears in 3 out of 5 roles (coder, tester, and architect)
- `WebSearch` appears in 2 out of 5 roles (architect, pm)
- `mcp__task-tools__create_task` appears in 2 out of 5 roles (architect, pm)

**Duplication Risk:** Core tool sets are repeated; if base tool permissions need to change (e.g., all roles get a new security audit tool), 5 files must be updated.

**Recommendation:**
Create a **tool configuration hierarchy** in `config/tools/`:
1. Define tool groups: `core_read: [Read, Glob, Grep]`, `file_modification: [Write, Edit]`, `execution: [Bash]`, etc.
2. Each role references tool groups instead of listing tools individually
3. Source of truth: **config/tools/tool-groups.yaml** (new)
4. Example structure:
```yaml
tool_groups:
  core_read: [Read, Glob, Grep]
  file_modification: [Write, Edit]
  execution: [Bash]
  web: [WebSearch]
  task_management: [mcp__task-tools__create_task]

role_tools:
  architect:
    includes: [core_read, file_modification, web, task_management]
  coder:
    includes: [core_read, file_modification, execution]
  reviewer:
    includes: [core_read, execution]
```

---

### 4. ⚠️ MEDIUM: Context Includes Configuration Duplicated Across Roles

**Severity:** MEDIUM
**Impact:** Inconsistent context propagation; difficult to maintain consistent context rules

**Locations:**
- `config/roles/architect.yaml` (line 45-49)
- `config/roles/coder.yaml` (line 44-47)
- `config/roles/reviewer.yaml` (line 37-40)
- `config/roles/tester.yaml` (line 27-29)
- `config/roles/pm.yaml` (line 38-41)

**Current Pattern:**

| Role | context_includes |
|------|------------------|
| architect | `[parent_artifact, root_artifact, sibling_summary, rejection_history]` |
| coder | `[parent_artifact, root_artifact, sibling_summary, rejection_history]` |
| reviewer | `[parent_artifact, root_artifact, sibling_summary]` |
| tester | `[parent_artifact, root_artifact]` |
| pm | `[parent_artifact, root_artifact, sibling_summary]` |

**Duplication Points:**
- `parent_artifact, root_artifact` in all 5 roles
- `sibling_summary` in 3 roles (architect, coder, pm, reviewer)
- `rejection_history` in 2 roles (architect, coder)

**Duplication Risk:** Context defaults are defined in every role; if all roles should include a new context type (e.g., `collaboration_notes`), 5 files must be updated.

**Recommendation:**
Move **context configuration to config/team.yaml** and:
1. Define default context includes in team.yaml
2. Allow role-level overrides only for exceptions
3. Source of truth: **config/team.yaml** (lines 13-27 for defaults)
4. Example:
```yaml
defaults:
  context_includes: [parent_artifact, root_artifact]

role_overrides:
  architect:
    adds: [sibling_summary, rejection_history]
  tester:
    uses_defaults: true
```

---

### 5. ⚠️ MEDIUM: Auto-scaling Configuration Scattered Across Files

**Severity:** MEDIUM
**Impact:** Difficult to maintain consistent scaling policies; scattered responsibility

**Locations:**
- `config/team.yaml` (lines 17-20, global defaults)
- `config/roles/architect.yaml` (lines 40-43)
- `config/roles/coder.yaml` (lines 38-41)
- `config/roles/tester.yaml` (lines 22-25)
- `config/roles/pm.yaml` (lines 36) - not present

**Current Pattern:**

**config/team.yaml** (global):
```yaml
defaults:
  auto_scale:
    enabled: false
    scale_up_threshold: 3
    scale_down_idle: 15
```

**config/roles/architect.yaml** (role-specific override):
```yaml
auto_scale:
  enabled: true
  scale_up_threshold: 4
  scale_down_idle: 20
```

**config/roles/coder.yaml** (role-specific override):
```yaml
auto_scale:
  enabled: true
  scale_up_threshold: 3
  scale_down_idle: 15
```

**Duplication Points:**
- Team defaults defined once in team.yaml
- Same values repeated when roles override defaults (e.g., coder mirrors team defaults)
- No clear inheritance pattern - unclear which value applies if both exist
- PM role has no auto_scale config - should it inherit team defaults?

**Duplication Risk:** If team defaults change, unclear whether role-specific values should also change.

**Recommendation:**
Establish **clear inheritance hierarchy**:
1. Define all team-level defaults in config/team.yaml
2. Role-level auto_scale only specifies what DIFFERS from team defaults
3. Document inheritance rules clearly
4. Source of truth: **config/team.yaml** (defaults) + role-specific overrides
5. Example clarification:
```yaml
# config/team.yaml
defaults:
  max_instances: 1
  auto_scale:
    enabled: false
    scale_up_threshold: 3
    scale_down_idle: 15

# config/roles/architect.yaml
# Only specify OVERRIDES:
auto_scale:
  enabled: true        # Changed from false
  scale_up_threshold: 4  # Changed from 3
  # scale_down_idle: inherits 15 from team defaults
```

---

### 6. ⚠️ MEDIUM: Role Metadata Structure Consistency

**Severity:** MEDIUM
**Impact:** Harder to parse and update role configurations; structure inconsistency

**Locations:**
- `config/roles/*.yaml` (all 5 role files)

**Current Pattern:**
Each role file includes:
- `role: [name]` - role identifier
- `display_name: "[Name]"` - human-readable name
- `prefix: "[XX]"` - task ID prefix
- `color: "[hex]"` - UI color
- `emoji: "[unicode]"` - UI emoji

All present in all 5 files, but structure varies slightly:
- architect.yaml: Lines 1-5 contain metadata
- coder.yaml: Lines 1-5 contain metadata
- All follow same pattern (consistent ✓)

**Duplication Points:**
While the structure is consistent (good!), each role file contains all this metadata. This isn't strictly "duplication" but could be optimized.

**Recommendation:**
Consider extracting to **config/role-metadata.yaml** (optional optimization):
```yaml
roles:
  architect:
    display_name: "Architect"
    prefix: "AR"
    color: "#8b5cf6"
    emoji: "🏗️"
  coder:
    display_name: "Coder"
    prefix: "CD"
    color: "#f59e0b"
    emoji: "💻"
```

This allows individual role YAML files to be purely about behavior/configuration, not UI metadata.

---

### 7. ⚠️ MEDIUM: produces/accepts/routes_to Task Type Consistency

**Severity:** MEDIUM
**Impact:** Task routing inconsistencies if not carefully managed

**Locations:**
- `config/roles/*.yaml` - produces/accepts/routes_to fields

**Current Pattern:**

| Role | produces | accepts | routes_to |
|------|----------|---------|-----------|
| architect | tech_design, tech_debt, architecture_review | prd, architecture_review_request, rejection | coder, architect |
| coder | implementation, bug_fix, revision | implementation, bug_fix, revision | tester, reviewer |
| reviewer | code_review, approval, rejection | code_review | coder, architect |
| tester | qa_verification, test_suite, regression_test | qa_verification | (empty) |
| pm | prd, goal_decomposition, requirement | goal, revision | architect |

**Duplication Points:**
- Task type names must be consistent across `produces` and downstream `accepts`
- Inconsistency risk: if architect changes `produces: [tech_design]` to `produces: [architectural_design]`, coder's `accepts: [prd]` might not match
- No centralized task type registry

**Duplication Risk:** Task type mismatches if roles are updated independently.

**Recommendation:**
Create **centralized task type registry** in `config/task-types.yaml`:
```yaml
task_types:
  # Produced by architects
  tech_design:
    description: "Technical design document"
    produced_by: architect
    consumed_by: [coder]

  prd:
    description: "Product Requirements Document"
    produced_by: pm
    consumed_by: [architect]

  implementation:
    description: "Implementation task"
    produced_by: coder
    consumed_by: [tester, reviewer]

  # ... all task types defined once
```

Then roles reference this registry instead of defining task types locally.

---

## Consolidation Recommendations by Priority

### Phase 1: Critical (Do First)
1. **Consolidate System Prompts** (Finding #1)
   - Create `config/prompts/` with template components
   - Extract common patterns to templates
   - Keep role-specific responsibilities in YAML
   - Estimated impact: Eliminates 80% of system_prompt duplication

### Phase 2: High (Do Soon)
2. **Unify Workflow Definitions** (Finding #2)
   - Establish pipelines/ as single source of truth
   - Generate role routing from pipeline definitions
   - Estimated impact: Single point of change for workflow modifications

3. **Centralize Tool Configurations** (Finding #3)
   - Create tool groups in config/tools/
   - Reference groups instead of listing tools
   - Estimated impact: Easier tool permission management

### Phase 3: Medium (Nice to Have)
4. **Consolidate Context Includes** (Finding #4)
   - Move to team.yaml with role overrides
   - Estimated impact: Simpler context management

5. **Clarify Auto-scaling Configuration** (Finding #5)
   - Document inheritance rules clearly
   - Create config/scaling/ for explicit policies
   - Estimated impact: Easier to maintain scaling rules

6. **Extract Role Metadata** (Finding #6)
   - Create config/role-metadata.yaml
   - Estimated impact: Cleaner role behavior files

7. **Create Task Type Registry** (Finding #7)
   - Define config/task-types.yaml
   - Estimated impact: Prevents task routing bugs

---

## Configuration Consolidation Example: Before & After

### Before: Scattered Configuration

```
config/
├── team.yaml                    (global defaults, scattered)
└── roles/
    ├── architect.yaml           (includes system prompt, tools, routing)
    ├── coder.yaml              (includes system prompt, tools, routing)
    ├── reviewer.yaml           (includes system prompt, tools, routing)
    ├── tester.yaml             (includes system prompt, tools, routing)
    └── pm.yaml                 (includes system prompt, tools, routing)

pipelines/
├── feature_dev.yaml            (workflow definition #1)
├── code_review.yaml            (workflow definition #2)
└── bugfix.yaml                 (workflow definition #3)
```

### After: Consolidated Configuration

```
config/
├── team.yaml                   (single source: defaults, global settings)
├── role-metadata.yaml          (new: UI metadata only)
├── task-types.yaml             (new: task type registry)
├── scaling/
│   └── policies.yaml           (new: scaling rules)
├── tools/
│   ├── tool-groups.yaml        (new: tool definitions)
│   └── role-tools.yaml         (new: role-to-tool mapping)
├── workflows/
│   └── routing.yaml            (new: derived from pipelines/)
├── prompts/
│   ├── templates.yaml          (new: prompt templates)
│   └── roles/
│       ├── architect.txt       (new: architect responsibilities)
│       ├── coder.txt           (new: coder responsibilities)
│       └── ...
└── roles/
    ├── architect.yaml          (now: behavior/config only)
    ├── coder.yaml             (now: behavior/config only)
    ├── reviewer.yaml          (now: behavior/config only)
    ├── tester.yaml            (now: behavior/config only)
    └── pm.yaml                (now: behavior/config only)

pipelines/                      (single source: workflow definitions)
├── feature_dev.yaml
├── code_review.yaml
└── bugfix.yaml
```

---

## Cross-References

- **AR-052 Section 2**: "The Problem: Configuration Duplication" - This audit validates the concerns raised in AR-052
- **AR-015**: Referenced in task description regarding exemption rules (not found in current codebase; may need investigation)
- **Related Tasks**: Future tasks should reference CD-173 when implementing consolidation

---

## Acceptance Criteria Checklist

- [x] Created CONFIG-AUDIT-FINDINGS.md documenting all duplication
- [x] Identified 7 duplication instances with clear locations and examples
- [x] Severity classification assigned to each (1 CRITICAL, 2 HIGH, 4 MEDIUM)
- [x] Recommendations include which layer should be source of truth for each finding
- [x] Examples show before/after for consolidation strategies
- [x] Cross-referenced to AR-052 for guidance on proper placement

---

**Document created by:** Coder (coder-2)
**Task:** CD-173
**Status:** ✅ Complete
