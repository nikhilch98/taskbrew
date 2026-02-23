# Settings Panel Redesign - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the basic settings modal with a full-page settings view featuring an interactive SVG pipeline visualizer with drag-and-drop routing, agent cards with rich editing UX, and team configuration with toggle switches.

**Architecture:** New `/settings` route served by a dedicated `settings.html` Jinja2 template. Backend gets new CRUD endpoints for role creation/deletion and route editing. The pipeline visualizer uses inline SVG with mouse event handlers for drag-to-connect. All settings persist to YAML via existing config_loader infrastructure.

**Tech Stack:** Vanilla JS + SVG (no new dependencies), FastAPI backend, YAML persistence, Inter font (already loaded)

---

## Task 1: Backend — New Settings API Endpoints

**Files:**
- Modify: `src/taskbrew/dashboard/app.py` (after line 345, inside the Settings section)
- Modify: `src/taskbrew/config_loader.py` (add helper to serialize RoleConfig back to YAML dict)

**Context:** The existing endpoints are `GET/PUT /api/settings/team` and `GET/PUT /api/settings/roles/{role_name}`. We need:
- `GET /api/settings/roles` already returns role data but is missing `routes_to`, `produces`, `accepts`, `can_create_groups`, `group_type`, `requires_approval`, `context_includes`, `auto_scale`, `max_turns`, `max_execution_time`. We need the full role config.
- New `POST /api/settings/roles` to create a new role (writes new YAML file)
- New `DELETE /api/settings/roles/{role_name}` to delete a role (removes YAML file + removes from in-memory `roles` dict)
- Enhanced `PUT /api/settings/roles/{role_name}` to handle ALL fields including `routes_to`, `auto_scale`, identity fields, etc.
- New `POST /api/settings/validate` to validate routing graph before save (uses existing `validate_routing`)
- New `GET /api/settings/models` to return available model list dynamically

**Step 1: Enhance GET /api/settings/roles to return full config**

In `app.py`, update the `get_roles_settings` endpoint to include all fields:

```python
@app.get("/api/settings/roles")
async def get_roles_settings():
    if not roles:
        return []
    result = []
    for name, rc in roles.items():
        role_data = {
            "role": name,
            "display_name": rc.display_name,
            "system_prompt": rc.system_prompt,
            "model": rc.model,
            "tools": rc.tools,
            "max_instances": rc.max_instances,
            "prefix": rc.prefix,
            "color": rc.color,
            "emoji": rc.emoji,
            "max_turns": getattr(rc, "max_turns", 200),
            "max_execution_time": getattr(rc, "max_execution_time", 1800),
            "produces": rc.produces,
            "accepts": rc.accepts,
            "routes_to": [
                {"role": rt.role, "task_types": rt.task_types}
                for rt in rc.routes_to
            ],
            "can_create_groups": rc.can_create_groups,
            "group_type": rc.group_type,
            "requires_approval": rc.requires_approval,
            "context_includes": rc.context_includes,
            "auto_scale": {
                "enabled": rc.auto_scale.enabled if rc.auto_scale else False,
                "scale_up_threshold": rc.auto_scale.scale_up_threshold if rc.auto_scale else 3,
                "scale_down_idle": rc.auto_scale.scale_down_idle if rc.auto_scale else 15,
            } if rc.auto_scale else None,
        }
        result.append(role_data)
    return result
```

**Step 2: Add POST /api/settings/roles for role creation**

```python
@app.post("/api/settings/roles")
async def create_role(body: dict):
    role_name = body.get("role", "").strip().lower()
    if not role_name or not role_name.isalnum():
        raise HTTPException(400, "Role name must be alphanumeric")
    if role_name in roles:
        raise HTTPException(409, f"Role '{role_name}' already exists")

    # Build YAML data from body with defaults
    yaml_data = {
        "role": role_name,
        "display_name": body.get("display_name", role_name.title()),
        "prefix": body.get("prefix", role_name[:2].upper()),
        "color": body.get("color", "#6366f1"),
        "emoji": body.get("emoji", "\U0001F916"),
        "max_turns": body.get("max_turns", 200),
        "system_prompt": body.get("system_prompt", f"You are a {role_name} agent."),
        "tools": body.get("tools", ["Read", "Glob", "Grep"]),
        "model": body.get("model", "claude-opus-4-6"),
        "produces": body.get("produces", []),
        "accepts": body.get("accepts", []),
        "routes_to": body.get("routes_to", []),
        "can_create_groups": body.get("can_create_groups", False),
        "max_instances": body.get("max_instances", 1),
        "requires_approval": body.get("requires_approval", []),
        "context_includes": body.get("context_includes", []),
    }

    # Write YAML file
    yaml_path = Path(project_dir) / "config" / "roles" / f"{role_name}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Parse and register in memory
    from taskbrew.config_loader import _parse_role
    rc = _parse_role(yaml_data)
    roles[role_name] = rc

    return {"status": "ok", "role": role_name}
```

**Step 3: Add DELETE /api/settings/roles/{role_name}**

```python
@app.delete("/api/settings/roles/{role_name}")
async def delete_role(role_name: str):
    if role_name not in roles:
        raise HTTPException(404, f"Role '{role_name}' not found")

    # Remove YAML file
    yaml_path = Path(project_dir) / "config" / "roles" / f"{role_name}.yaml"
    if yaml_path.exists():
        yaml_path.unlink()

    # Remove from in-memory config
    del roles[role_name]

    # Clean up routes pointing to this role from other roles
    for rc in roles.values():
        rc.routes_to = [rt for rt in rc.routes_to if rt.role != role_name]

    return {"status": "ok"}
```

**Step 4: Enhance PUT to handle all fields**

Update the existing `update_role_settings` to handle ALL role config fields, not just `system_prompt`, `model`, `tools`. Handle `routes_to` as a list of `{role, task_types}` dicts, `auto_scale` as `{enabled, scale_up_threshold, scale_down_idle}`, and identity fields like `display_name`, `prefix`, `color`, `emoji`.

**Step 5: Add validation and models endpoints**

```python
@app.post("/api/settings/validate")
async def validate_settings():
    from taskbrew.config_loader import validate_routing
    errors = validate_routing(roles)
    return {"valid": len(errors) == 0, "errors": errors}

@app.get("/api/settings/models")
async def get_available_models():
    return {"models": [
        {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "tier": "flagship"},
        {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "tier": "balanced"},
        {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "tier": "fast"},
    ]}
```

**Step 6: Add /settings route to serve the new template**

```python
@app.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})
```

**Step 7: Run existing tests**

Run: `pytest tests/ -v`
Expected: All 105 tests pass (no breaking changes, only additive endpoints)

**Step 8: Commit**

```bash
git add src/taskbrew/dashboard/app.py
git commit -m "feat: add full CRUD settings API endpoints for roles"
```

---

## Task 2: Settings Page Template — Layout & Navigation

**Files:**
- Create: `src/taskbrew/dashboard/templates/settings.html`
- Modify: `src/taskbrew/dashboard/templates/index.html` (update settings button to navigate to /settings instead of opening modal)

**Context:** Create the full-page settings HTML with the overall page structure. This task only creates the skeleton — the pipeline visualizer, agent cards, and team settings content are added in subsequent tasks.

**Step 1: Create settings.html with page skeleton**

The page should:
- Share the same `<head>` section (Inter font, design tokens CSS) as `index.html`
- Have a top bar with "← Back to Dashboard" link and "Settings" title
- Have three main sections stacked vertically:
  1. Pipeline Visualizer container (empty SVG area, ~250px tall)
  2. Agent Cards Grid container
  3. Team Settings collapsible section
- Include a floating "Save All Changes" button fixed to bottom-right
- Include an "Unsaved changes" indicator
- Use the same dark theme design tokens from index.html (copy the `:root` CSS variables)

The HTML structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <!-- Same meta, fonts, DOMPurify as index.html -->
    <title>Settings - AI Team Dashboard</title>
    <style>
        /* Copy :root design tokens from index.html */
        /* Settings-specific styles */
    </style>
</head>
<body>
    <header class="settings-header">
        <a href="/" class="back-link">← Back to Dashboard</a>
        <h1>Settings</h1>
        <div class="header-actions">
            <span class="unsaved-badge" id="unsavedBadge" style="display:none">Unsaved changes</span>
            <button class="btn-validate" onclick="validateConfig()">Validate</button>
            <button class="btn-save-all" onclick="saveAllChanges()">Save All Changes</button>
        </div>
    </header>

    <main class="settings-main">
        <section class="pipeline-section">
            <div class="section-header">
                <h2>Agent Pipeline</h2>
                <p class="section-desc">Drag between agents to create routing connections</p>
            </div>
            <div class="pipeline-canvas" id="pipelineCanvas">
                <svg id="pipelineSvg" width="100%" height="250"></svg>
            </div>
        </section>

        <section class="agents-section">
            <div class="section-header">
                <h2>Agents</h2>
                <button class="btn-add-agent" onclick="openNewAgentWizard()">+ New Agent</button>
            </div>
            <div class="agents-grid" id="agentsGrid">
                <!-- Agent cards rendered by JS -->
            </div>
        </section>

        <section class="team-section">
            <div class="section-header collapsible" onclick="toggleTeamSection()">
                <h2>Team Configuration</h2>
                <span class="collapse-icon">▾</span>
            </div>
            <div class="team-settings-body" id="teamSettingsBody">
                <!-- Team settings rendered by JS -->
            </div>
        </section>
    </main>

    <div class="toast-container" id="toastContainer"></div>

    <!-- New Agent Wizard Modal -->
    <div class="wizard-overlay" id="wizardOverlay" style="display:none">
        <!-- Filled by JS -->
    </div>

    <script>
        // All JS goes here - loaded in subsequent tasks
    </script>
</body>
</html>
```

**Step 2: Style the page layout**

Key CSS:
- `.settings-header`: Fixed top bar, dark glass background, flexbox with space-between
- `.settings-main`: `max-width: 1400px`, centered, `padding: 100px 40px 40px`
- `.pipeline-section`: Glass card with border, full width
- `.agents-grid`: CSS Grid, `grid-template-columns: repeat(auto-fill, minmax(380px, 1fr))`, gap 24px
- `.team-section`: Collapsible glass card
- `.btn-save-all`: Primary gradient button (indigo → purple)
- `.btn-add-agent`: Outlined button with dashed border and "+" icon
- `.unsaved-badge`: Amber pill badge with pulse animation
- `.toast-container`: Fixed bottom-right for success/error toasts

**Step 3: Update index.html settings button**

Change the settings button from calling `toggleSettingsModal()` to navigating to `/settings`:

```javascript
// Old:
function toggleSettingsModal() { ... }

// New: Change the nav button onclick to:
// window.location.href = '/settings';
```

Keep the old modal code for now (can be removed in a cleanup task).

**Step 4: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html src/taskbrew/dashboard/templates/index.html src/taskbrew/dashboard/app.py
git commit -m "feat: add settings page layout with navigation"
```

---

## Task 3: Interactive SVG Pipeline Visualizer

**Files:**
- Modify: `src/taskbrew/dashboard/templates/settings.html` (add JS + SVG rendering)

**Context:** This is the crown jewel — an SVG-based interactive pipeline diagram. Each agent is a node. Connections show routing relationships. Users can drag from one node to another to create routes.

**Step 1: Implement pipeline node rendering**

Each agent node in the SVG should be:
- A rounded rect (120×80) filled with the agent's color (with low opacity)
- Agent emoji centered at top
- Display name below emoji
- A small "output port" circle on the right edge (for dragging connections FROM)
- A small "input port" circle on the left edge (for receiving connections TO)
- Positioned using force-directed or horizontal layout algorithm

Layout algorithm (simple horizontal):
1. Find "entry" roles (roles where `can_create_groups` is true or that are not targeted by any route) — place them on the left
2. Use topological sort of the routing graph to order nodes left-to-right
3. Space nodes evenly across the SVG width
4. If there are branches, stack vertically within the same column

```javascript
function renderPipeline(rolesData) {
    const svg = document.getElementById('pipelineSvg');
    svg.innerHTML = '';

    // Build adjacency from routes_to
    const graph = {};
    const inDegree = {};
    rolesData.forEach(r => {
        graph[r.role] = (r.routes_to || []).map(rt => rt.role);
        if (!(r.role in inDegree)) inDegree[r.role] = 0;
        graph[r.role].forEach(target => {
            inDegree[target] = (inDegree[target] || 0) + 1;
        });
    });

    // Topological sort for layout order
    const order = topoSort(graph, inDegree);

    // Position nodes
    const nodeWidth = 130, nodeHeight = 80, gap = 60;
    const totalWidth = order.length * (nodeWidth + gap) - gap;
    const startX = (svg.clientWidth - totalWidth) / 2;

    const positions = {};
    order.forEach((role, i) => {
        positions[role] = {
            x: startX + i * (nodeWidth + gap),
            y: 85 // centered vertically in 250px SVG
        };
    });

    // Draw connection lines first (behind nodes)
    rolesData.forEach(r => {
        (r.routes_to || []).forEach(rt => {
            if (positions[r.role] && positions[rt.role]) {
                drawConnection(svg, positions[r.role], positions[rt.role], r.color);
            }
        });
    });

    // Draw nodes
    rolesData.forEach(r => {
        if (positions[r.role]) {
            drawNode(svg, r, positions[r.role], nodeWidth, nodeHeight);
        }
    });

    // Draw "+ Add Agent" node at the end
    drawAddNode(svg, startX + order.length * (nodeWidth + gap), 85);
}
```

**Step 2: Implement connection drawing**

Connections use SVG `<path>` with cubic bezier curves and an arrowhead marker:

```javascript
function drawConnection(svg, from, to, color) {
    const startX = from.x + 130; // right edge of source
    const startY = from.y + 40;  // vertical center
    const endX = to.x;           // left edge of target
    const endY = to.y + 40;
    const midX = (startX + endX) / 2;

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M${startX},${startY} C${midX},${startY} ${midX},${endY} ${endX},${endY}`);
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', '2');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke-dasharray', '8,4');
    path.setAttribute('marker-end', 'url(#arrowhead)');
    path.classList.add('pipeline-connection');

    // Animate dash offset for flow effect
    path.style.animation = 'flowDash 1.5s linear infinite';

    svg.appendChild(path);
}
```

Add a `<defs>` block in the SVG for the arrowhead marker and the flowDash animation:

```css
@keyframes flowDash {
    to { stroke-dashoffset: -12; }
}
```

**Step 3: Implement drag-to-connect**

When the user mousedowns on an output port (right side of a node):
1. Start tracking mouse position
2. Draw a temporary bezier curve from the port to the cursor
3. On mouseup over an input port (left side of another node): create the route
4. On mouseup elsewhere: cancel

```javascript
let dragState = null;

function startDrag(sourceRole, portX, portY) {
    dragState = { sourceRole, portX, portY };
    const tempLine = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    tempLine.id = 'dragLine';
    tempLine.setAttribute('stroke', '#6366f1');
    tempLine.setAttribute('stroke-width', '2');
    tempLine.setAttribute('stroke-dasharray', '4,4');
    tempLine.setAttribute('fill', 'none');
    document.getElementById('pipelineSvg').appendChild(tempLine);
}

function onSvgMouseMove(e) {
    if (!dragState) return;
    const svg = document.getElementById('pipelineSvg');
    const pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const svgPt = pt.matrixTransform(svg.getScreenCTM().inverse());

    const line = document.getElementById('dragLine');
    const midX = (dragState.portX + svgPt.x) / 2;
    line.setAttribute('d',
        `M${dragState.portX},${dragState.portY} C${midX},${dragState.portY} ${midX},${svgPt.y} ${svgPt.x},${svgPt.y}`
    );
}

function endDrag(targetRole) {
    if (!dragState || dragState.sourceRole === targetRole) {
        cancelDrag();
        return;
    }
    // Add route to local data
    addRoute(dragState.sourceRole, targetRole);
    cancelDrag();
    markUnsaved();
    renderPipeline(settingsData.roles);
}
```

**Step 4: Implement click-to-delete connections**

When user clicks an existing connection line, show a small "×" button on the line. Clicking it removes the route.

**Step 5: Implement the "+ Add Agent" node**

A dashed-border rect with "+" icon. Clicking it opens the New Agent Wizard (Task 5).

**Step 6: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html
git commit -m "feat: add interactive SVG pipeline visualizer with drag-to-connect"
```

---

## Task 4: Agent Cards Grid

**Files:**
- Modify: `src/taskbrew/dashboard/templates/settings.html` (add agent card rendering + styles)

**Context:** Each agent gets a rich, expandable card in a responsive grid. Cards have the agent's color as an accent and accordion sections for editing.

**Step 1: Implement agent card rendering**

```javascript
function renderAgentCards(rolesData) {
    const grid = document.getElementById('agentsGrid');
    grid.innerHTML = '';

    rolesData.forEach(role => {
        const card = document.createElement('div');
        card.className = 'agent-card';
        card.style.setProperty('--agent-color', role.color);
        card.dataset.role = role.role;

        card.innerHTML = `
            <div class="agent-card-header">
                <div class="agent-identity">
                    <span class="agent-emoji">${role.emoji}</span>
                    <div>
                        <h3 class="agent-name">${role.display_name}</h3>
                        <span class="agent-role-tag">${role.role}</span>
                    </div>
                </div>
                <div class="agent-actions">
                    <button class="btn-icon" onclick="duplicateAgent('${role.role}')" title="Duplicate">⧉</button>
                    <button class="btn-icon btn-danger" onclick="deleteAgent('${role.role}')" title="Delete">✕</button>
                </div>
            </div>

            <div class="agent-quick-stats">
                <div class="stat-chip"><span class="stat-label">Model</span><span class="stat-value">${role.model.split('-').slice(1,3).join(' ')}</span></div>
                <div class="stat-chip"><span class="stat-label">Instances</span><span class="stat-value">${role.max_instances}</span></div>
                <div class="stat-chip"><span class="stat-label">Tools</span><span class="stat-value">${role.tools.length}</span></div>
            </div>

            <div class="agent-sections">
                ${renderAccordion('Identity', renderIdentitySection(role), role.role)}
                ${renderAccordion('Model & Execution', renderModelSection(role), role.role)}
                ${renderAccordion('Tools', renderToolsSection(role), role.role)}
                ${renderAccordion('Routing', renderRoutingSection(role), role.role)}
                ${renderAccordion('System Prompt', renderPromptSection(role), role.role)}
                ${renderAccordion('Advanced', renderAdvancedSection(role), role.role)}
            </div>
        `;

        grid.appendChild(card);
    });

    // Add the "+ New Agent" card
    const addCard = document.createElement('div');
    addCard.className = 'agent-card agent-card-add';
    addCard.onclick = () => openNewAgentWizard();
    addCard.innerHTML = `
        <div class="add-agent-content">
            <span class="add-icon">+</span>
            <span class="add-text">Add New Agent</span>
        </div>
    `;
    grid.appendChild(addCard);
}
```

**Step 2: Style agent cards**

Key CSS for `.agent-card`:
- Background: `var(--bg-card)` with backdrop blur
- Border: `1px solid var(--border-subtle)`, on hover `var(--border-hover)`
- Top accent bar: 3px solid using `var(--agent-color)` via border-top
- Border-radius: `var(--radius-lg)`
- Padding: 24px
- Transition: transform 0.2s, border-color 0.2s
- Hover: slight scale(1.01), glow shadow using agent color

`.agent-card-add`:
- Dashed border: `2px dashed var(--border-subtle)`
- Display: flex, center content
- Min-height: 200px
- Cursor: pointer
- Hover: border-color brightens, background lightens

**Step 3: Implement accordion sections**

```javascript
function renderAccordion(title, content, roleId) {
    const sectionId = `${roleId}-${title.toLowerCase().replace(/\s+/g, '-')}`;
    return `
        <div class="accordion-section" id="section-${sectionId}">
            <button class="accordion-trigger" onclick="toggleAccordion('${sectionId}')">
                <span>${title}</span>
                <span class="accordion-icon">▸</span>
            </button>
            <div class="accordion-content" style="display:none">
                ${content}
            </div>
        </div>
    `;
}
```

**Step 4: Implement Identity section**

Fields:
- Display Name: text input
- Prefix: text input (2-3 chars, uppercase)
- Color: native `<input type="color">` with hex preview
- Emoji: text input (single emoji character) with preview

All inputs call `markUnsaved()` on change and update the local `settingsData`.

**Step 5: Implement Model & Execution section**

Fields:
- Model: `<select>` dropdown (populated from `/api/settings/models`)
- Max Turns: number input
- Max Execution Time: number input (seconds) with human-readable label (e.g., "30 min")
- Max Instances: number input
- Auto-Scale: toggle switch → reveals threshold inputs when enabled

**Step 6: Implement Tools section — tag chip UI**

Display tools as colored chips/tags with "×" to remove:

```javascript
function renderToolsSection(role) {
    const chips = role.tools.map(t =>
        `<span class="tool-chip">
            ${t}
            <button class="chip-remove" onclick="removeTool('${role.role}', '${t}')">&times;</button>
        </span>`
    ).join('');

    return `
        <div class="tools-container">
            <div class="tool-chips">${chips}</div>
            <div class="tool-input-row">
                <input type="text" class="tool-input" id="toolInput-${role.role}"
                    placeholder="Add tool..." list="toolSuggestions-${role.role}">
                <datalist id="toolSuggestions-${role.role}">
                    <option value="Read">
                    <option value="Write">
                    <option value="Edit">
                    <option value="Bash">
                    <option value="Glob">
                    <option value="Grep">
                    <option value="WebSearch">
                    <option value="mcp__task-tools__create_task">
                </datalist>
                <button class="btn-add-tool" onclick="addTool('${role.role}')">Add</button>
            </div>
        </div>
    `;
}
```

CSS for `.tool-chip`:
- Display: inline-flex, align-items center
- Background: rgba(99, 102, 241, 0.15)
- Border: 1px solid rgba(99, 102, 241, 0.3)
- Border-radius: 6px
- Padding: 4px 8px
- Font-size: 13px, monospace font
- `.chip-remove`: no border/bg, cursor pointer, color red on hover

**Step 7: Implement Routing section**

Show current routes as a visual list:

```
Routes to:
  [Architect] → tech_design, architecture_review  [×]
  [+ Add Route]
```

Each route shows the target role (with its emoji and color), the task types as sub-chips, and a remove button. "Add Route" opens a mini-picker with all available roles and a multi-select for task types.

**Step 8: Implement System Prompt section**

A full-width `<textarea>` with:
- Monospace font (font-family: 'SF Mono', Consolas, monospace)
- Auto-growing height (min 6 rows, max 20 rows)
- Line numbers via a side gutter (CSS counter)
- "Expand" button to go full-screen (modal overlay with larger editor)
- Character count displayed below

**Step 9: Implement Advanced section**

Fields:
- Produces: tag chips (same pattern as tools)
- Accepts: tag chips
- Requires Approval: tag chips
- Context Includes: multi-select checkboxes for `parent_artifact`, `root_artifact`, `sibling_summary`
- Can Create Groups: toggle switch
- Group Type: text input (only shown if can_create_groups is true)

**Step 10: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html
git commit -m "feat: add rich agent cards with accordion sections and chip editors"
```

---

## Task 5: New Agent Wizard & Delete Confirmation

**Files:**
- Modify: `src/taskbrew/dashboard/templates/settings.html` (add wizard modal + delete confirmation)

**Context:** The wizard guides users through creating a new agent. Delete shows a confirmation with impact analysis.

**Step 1: Implement New Agent Wizard**

A multi-step modal wizard:

Step 1 of 3 - "Identity":
- Role ID (lowercase, alphanumeric, auto-slugifies from display name)
- Display Name
- Prefix (auto-generated from first 2 chars)
- Color picker
- Emoji picker (grid of common emojis or text input)

Step 2 of 3 - "Configuration":
- Model dropdown
- System prompt textarea
- Tools selection (checkbox grid of common tools)

Step 3 of 3 - "Pipeline":
- "Accepts tasks from:" multi-select of existing roles
- "Routes tasks to:" multi-select of existing roles + task type input
- Visual preview showing where the new agent fits in the pipeline

Navigation: Back/Next buttons, step indicator dots, final "Create Agent" button.

```javascript
function openNewAgentWizard() {
    const overlay = document.getElementById('wizardOverlay');
    overlay.style.display = 'flex';
    wizardStep = 1;
    wizardData = { role: '', display_name: '', prefix: '', color: '#6366f1', emoji: '🤖', ... };
    renderWizardStep();
}

function renderWizardStep() {
    const content = document.getElementById('wizardContent');
    switch (wizardStep) {
        case 1: content.innerHTML = renderWizardIdentity(); break;
        case 2: content.innerHTML = renderWizardConfig(); break;
        case 3: content.innerHTML = renderWizardPipeline(); break;
    }
}
```

**Step 2: Implement Delete confirmation**

When "Delete" is clicked on an agent card:

```javascript
async function deleteAgent(role) {
    // Find roles that route TO this role (will break their pipeline)
    const dependents = settingsData.roles.filter(r =>
        r.routes_to.some(rt => rt.role === role)
    );

    const msg = dependents.length > 0
        ? `Deleting "${role}" will break routing from: ${dependents.map(d => d.display_name).join(', ')}. Continue?`
        : `Delete agent "${role}"? This removes the config file.`;

    if (!confirm(msg)) return;

    const resp = await fetch(`/api/settings/roles/${role}`, { method: 'DELETE' });
    if (resp.ok) {
        showToast(`Agent "${role}" deleted`, 'success');
        await loadSettings();
    } else {
        showToast('Failed to delete agent', 'error');
    }
}
```

**Step 3: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html
git commit -m "feat: add new agent wizard and delete confirmation with impact analysis"
```

---

## Task 6: Team Settings Section

**Files:**
- Modify: `src/taskbrew/dashboard/templates/settings.html` (add team settings rendering)

**Context:** Collapsible section with clean form for team-level configuration. Toggle switches for feature flags.

**Step 1: Implement team settings rendering**

```javascript
function renderTeamSettings(teamData) {
    const body = document.getElementById('teamSettingsBody');
    body.innerHTML = `
        <div class="team-settings-grid">
            <div class="settings-group">
                <h3>General</h3>
                <div class="field-row">
                    <label>Team Name</label>
                    <input type="text" id="teamName" value="${teamData.name}"
                        onchange="markUnsaved()">
                </div>
                <div class="field-row">
                    <label>Project Directory</label>
                    <input type="text" value="${teamData.project_dir}" disabled>
                    <span class="field-hint">Read-only — set in team.yaml</span>
                </div>
                <div class="field-row">
                    <label>Default Poll Interval</label>
                    <input type="number" id="pollInterval"
                        value="${teamData.default_poll_interval}" min="1" max="60"
                        onchange="markUnsaved()"> seconds
                </div>
            </div>

            <div class="settings-group">
                <h3>Features</h3>
                <div class="toggle-row">
                    <label>Authentication</label>
                    <div class="toggle-switch" onclick="toggleFeature('auth')">
                        <div class="toggle-track ${teamData.auth_enabled ? 'active' : ''}">
                            <div class="toggle-thumb"></div>
                        </div>
                    </div>
                </div>
                <div class="toggle-row">
                    <label>Cost Budgets</label>
                    <div class="toggle-switch" onclick="toggleFeature('cost_budgets')">
                        <div class="toggle-track ${teamData.cost_budgets_enabled ? 'active' : ''}">
                            <div class="toggle-thumb"></div>
                        </div>
                    </div>
                </div>
                <div class="toggle-row">
                    <label>Webhooks</label>
                    <div class="toggle-switch" onclick="toggleFeature('webhooks')">
                        <div class="toggle-track ${teamData.webhooks_enabled ? 'active' : ''}">
                            <div class="toggle-thumb"></div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="settings-group">
                <h3>Group Prefixes</h3>
                <div id="prefixRows">
                    <!-- key-value rows rendered dynamically -->
                </div>
                <button class="btn-add-prefix" onclick="addPrefixRow()">+ Add Prefix</button>
            </div>
        </div>
    `;
}
```

**Step 2: Style toggle switches**

```css
.toggle-track {
    width: 44px; height: 24px;
    border-radius: 12px;
    background: rgba(100, 100, 120, 0.3);
    position: relative;
    cursor: pointer;
    transition: background 0.2s;
}
.toggle-track.active {
    background: var(--accent-indigo);
}
.toggle-thumb {
    width: 20px; height: 20px;
    border-radius: 50%;
    background: white;
    position: absolute;
    top: 2px; left: 2px;
    transition: transform 0.2s;
}
.toggle-track.active .toggle-thumb {
    transform: translateX(20px);
}
```

**Step 3: Style the settings grid**

`.team-settings-grid`: CSS Grid with `grid-template-columns: repeat(auto-fit, minmax(300px, 1fr))`, gap 32px.

`.settings-group`: Glass card background, padding 24px, border-radius var(--radius-md).

`.field-row`: Flex column, gap 6px. Label is 13px secondary color. Input is full-width, dark background, subtle border.

`.toggle-row`: Flex row with space-between, padding 12px 0, border-bottom subtle.

**Step 4: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html
git commit -m "feat: add team settings section with toggle switches and feature flags"
```

---

## Task 7: Save, Validate & Change Tracking

**Files:**
- Modify: `src/taskbrew/dashboard/templates/settings.html` (add save/validate/undo logic)

**Context:** Track changes across all settings, validate before save, show diff preview, support undo.

**Step 1: Implement change tracking**

```javascript
let originalData = null;  // snapshot taken on load
let settingsData = { team: null, roles: [] };
let hasUnsaved = false;

async function loadSettings() {
    const [teamResp, rolesResp, modelsResp] = await Promise.all([
        fetch('/api/settings/team').then(r => r.json()),
        fetch('/api/settings/roles').then(r => r.json()),
        fetch('/api/settings/models').then(r => r.json()),
    ]);
    settingsData.team = teamResp;
    settingsData.roles = rolesResp;
    settingsData.models = modelsResp.models;
    originalData = JSON.parse(JSON.stringify(settingsData));  // deep clone
    hasUnsaved = false;
    updateUnsavedBadge();

    renderPipeline(settingsData.roles);
    renderAgentCards(settingsData.roles);
    renderTeamSettings(settingsData.team);
}

function markUnsaved() {
    hasUnsaved = true;
    updateUnsavedBadge();
}

function updateUnsavedBadge() {
    document.getElementById('unsavedBadge').style.display = hasUnsaved ? 'inline-flex' : 'none';
}
```

**Step 2: Implement "Save All Changes"**

```javascript
async function saveAllChanges() {
    // 1. Validate first
    const validation = await fetch('/api/settings/validate', { method: 'POST' });
    const result = await validation.json();
    if (!result.valid) {
        showToast(`Validation failed: ${result.errors.join(', ')}`, 'error');
        return;
    }

    // 2. Save team settings
    await fetch('/api/settings/team', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: settingsData.team.name }),
    });

    // 3. Save each modified role
    for (const role of settingsData.roles) {
        await fetch(`/api/settings/roles/${role.role}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(role),
        });
    }

    showToast('All settings saved!', 'success');
    originalData = JSON.parse(JSON.stringify(settingsData));
    hasUnsaved = false;
    updateUnsavedBadge();
}
```

**Step 3: Implement validation UI**

The "Validate" button calls `/api/settings/validate` and shows results:
- Green toast if valid
- Red toast with error list if invalid
- Highlight problematic agent cards with red border

**Step 4: Implement toast notifications**

```javascript
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => toast.classList.add('show'));

    // Auto-dismiss after 3s
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
```

**Step 5: Warn on page leave with unsaved changes**

```javascript
window.addEventListener('beforeunload', (e) => {
    if (hasUnsaved) {
        e.preventDefault();
        e.returnValue = '';
    }
});
```

**Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass

**Step 7: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html
git commit -m "feat: add save, validate, change tracking, and toast notifications"
```

---

## Task 8: Polish — Animations, Responsive, Keyboard Shortcuts

**Files:**
- Modify: `src/taskbrew/dashboard/templates/settings.html` (add polish CSS + responsive + keyboard)

**Context:** Final polish pass to make everything feel premium.

**Step 1: Add CSS animations**

- Accordion open/close: smooth `max-height` transition with `overflow: hidden`
- Agent cards: staggered fade-in on page load (`animation-delay: calc(var(--i) * 80ms)`)
- Pipeline connections: animated flowing dashes (already in Task 3)
- Pipeline nodes: subtle pulse when hovered
- Toggle switches: smooth background + thumb transition
- Toast notifications: slide-in from right + fade-out
- Unsaved badge: gentle pulse animation
- Wizard steps: slide left/right on step transitions

**Step 2: Add responsive breakpoints**

```css
@media (max-width: 1200px) {
    .agents-grid {
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    }
}

@media (max-width: 768px) {
    .settings-main { padding: 80px 16px 16px; }
    .agents-grid { grid-template-columns: 1fr; }
    .team-settings-grid { grid-template-columns: 1fr; }
    .pipeline-canvas { overflow-x: auto; }
    #pipelineSvg { min-width: 800px; }
    .settings-header { padding: 12px 16px; }
}
```

**Step 3: Add keyboard shortcuts**

- `Ctrl+S` / `Cmd+S`: Save all changes
- `Escape`: Close wizard/expanded prompt editor / navigate back if nothing open
- `Ctrl+Z` / `Cmd+Z`: Undo last field change (simple: revert to originalData)

**Step 4: Add light theme support**

Match the existing light theme toggle from index.html:

```css
.light-theme {
    --bg-primary: #f8f9fc;
    --bg-secondary: rgba(255, 255, 255, 0.95);
    --bg-card: rgba(255, 255, 255, 0.9);
    --text-primary: #1a1a2e;
    --text-secondary: #6b7280;
    --border-subtle: rgba(99, 102, 241, 0.15);
}
```

**Step 5: Final visual QA**

- Verify all agent cards align properly in grid
- Verify pipeline SVG resizes correctly
- Verify all form inputs are accessible (labels, focus states)
- Verify toast notifications stack properly
- Verify wizard modal is scrollable on small screens
- Verify color picker shows live preview on agent card

**Step 6: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html
git commit -m "feat: add polish — animations, responsive layout, keyboard shortcuts, light theme"
```

---

## Summary

| Task | Description | Scope |
|------|-------------|-------|
| 1 | Backend CRUD API | 5 new endpoints, enhanced 2 existing |
| 2 | Page layout & navigation | New settings.html skeleton, update nav |
| 3 | SVG pipeline visualizer | Interactive drag-to-connect flow diagram |
| 4 | Agent cards grid | Rich cards with accordion sections |
| 5 | New agent wizard + delete | Multi-step wizard, impact-aware delete |
| 6 | Team settings section | Toggle switches, feature flags |
| 7 | Save/validate/tracking | Change detection, bulk save, validation |
| 8 | Polish & responsive | Animations, keyboard shortcuts, light theme |
