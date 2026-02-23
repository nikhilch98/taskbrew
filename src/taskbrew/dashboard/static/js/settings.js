// ================================================================
// Project Management
// ================================================================

async function checkProjectOrRedirect() {
    try {
        const resp = await fetch('/api/projects/status');
        const status = await resp.json();
        if (!status.active) {
            window.location.href = '/';
            return false;
        }
        document.getElementById('projectSelector').style.display = 'flex';
        document.getElementById('activeProjectName').textContent = status.active.name;
        return true;
    } catch (e) {
        console.error('Failed to check project status:', e);
        return true; // fallback
    }
}

async function loadProjectList() {
    const resp = await fetch('/api/projects');
    const projects = await resp.json();
    const activeResp = await fetch('/api/projects/active');
    const active = await activeResp.json();
    const activeId = active ? active.id : null;

    const list = document.getElementById('projectList');
    list.innerHTML = '';
    projects.forEach(function(p) {
        const btn = document.createElement('button');
        btn.className = 'dropdown-item project-item' + (p.id === activeId ? ' active' : '');
        btn.dataset.projectId = p.id;
        btn.addEventListener('click', function() { switchProject(p.id); });

        const dot = document.createElement('span');
        dot.className = 'project-dot' + (p.id === activeId ? ' active' : '');
        btn.appendChild(dot);

        const info = document.createElement('div');
        info.className = 'project-info';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'project-name';
        nameSpan.textContent = p.name;
        info.appendChild(nameSpan);

        const dirSpan = document.createElement('span');
        dirSpan.className = 'project-dir';
        dirSpan.textContent = p.directory;
        info.appendChild(dirSpan);

        btn.appendChild(info);
        list.appendChild(btn);
    });
}

function toggleProjectDropdown() {
    const dd = document.getElementById('projectDropdown');
    dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
    if (dd.style.display === 'block') {
        loadProjectList();
        document.addEventListener('click', closeProjectDropdownOutside);
    }
}

function closeProjectDropdownOutside(e) {
    const selector = document.getElementById('projectSelector');
    if (!selector.contains(e.target)) {
        document.getElementById('projectDropdown').style.display = 'none';
        document.removeEventListener('click', closeProjectDropdownOutside);
    }
}

async function switchProject(projectId) {
    document.getElementById('projectDropdown').style.display = 'none';
    try {
        const resp = await fetch(`/api/projects/${projectId}/activate`, { method: 'POST' });
        if (resp.ok) {
            window.location.reload();
        }
    } catch (e) {
        console.error('Failed to switch project:', e);
    }
}

// ================================================================
// State
// ================================================================
let settingsData = { team: {}, roles: [] };
let originalData = null;
let availableModels = [];
let unsaved = false;
let wizardStep = 0;
let wizardData = { role: '', display_name: '', prefix: '', color: '#6366f1', emoji: '', model: '', system_prompt: '', tools: [], receives_from: [], routes_to: [] };
let promptFullscreenRole = null;
let dragState = null; // For pipeline drag-to-connect

const COMMON_TOOLS = ['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep', 'WebSearch', 'mcp__task-tools__create_task'];
const CONTEXT_OPTIONS = ['parent_artifact', 'root_artifact', 'sibling_summary', 'rejection_history'];

// ================================================================
// Helpers
// ================================================================
function escapeHtml(str) {
    if (!str) return '';
    return DOMPurify.sanitize(String(str), { ALLOWED_TAGS: [] });
}

function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
}

function slugify(str) {
    return String(str).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
}

function autoPrefix(name) {
    if (!name) return '';
    const words = name.trim().split(/\s+/);
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    return name.substring(0, 2).toUpperCase();
}

function getRoleByName(roleName) {
    return settingsData.roles.find(r => r.role === roleName);
}

// ================================================================
// Theme
// ================================================================
function toggleTheme() {
    document.body.classList.toggle('light-theme');
    const isLight = document.body.classList.contains('light-theme');
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
    document.getElementById('themeIcon').innerHTML = isLight ? '&#9728;' : '&#9790;';
}

if (localStorage.getItem('theme') === 'light') {
    document.body.classList.add('light-theme');
    document.getElementById('themeIcon').innerHTML = '&#9728;';
}

// ================================================================
// Toast Notifications
// ================================================================
function showToast(message, type) {
    type = type || 'info';
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    const iconMap = {
        success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
        error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
        info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
    };
    toast.innerHTML = (iconMap[type] || iconMap.info) + '<span>' + escapeHtml(message) + '</span>';
    container.appendChild(toast);
    setTimeout(function() {
        toast.classList.add('removing');
        setTimeout(function() { toast.remove(); }, 300);
    }, 3000);
}

// ================================================================
// Unsaved Change Tracking
// ================================================================
function markUnsaved() {
    unsaved = true;
    document.getElementById('unsavedBadge').classList.add('visible');
}

function markSaved() {
    unsaved = false;
    document.getElementById('unsavedBadge').classList.remove('visible');
    originalData = deepClone(settingsData);
}

window.addEventListener('beforeunload', function(e) {
    if (unsaved) {
        e.preventDefault();
        e.returnValue = '';
    }
});

// ================================================================
// Data Loading
// ================================================================
async function loadSettings() {
    try {
        const [teamRes, rolesRes, modelsRes] = await Promise.all([
            fetch('/api/settings/team'),
            fetch('/api/settings/roles'),
            fetch('/api/settings/models').catch(function() { return null; })
        ]);
        settingsData.team = await teamRes.json();
        settingsData.roles = await rolesRes.json();
        if (modelsRes && modelsRes.ok) {
            const md = await modelsRes.json();
            availableModels = md.models || [];
        }
        originalData = deepClone(settingsData);
        renderAll();
    } catch (e) {
        showToast('Failed to load settings: ' + e.message, 'error');
    }
}

function renderAll() {
    renderPipeline();
    renderAgentCards();
    renderTeamSettings();
}

// ================================================================
// Pipeline Visualizer (SVG)
// ================================================================
function computeGraphLayout(roles) {
    // Build role map and adjacency
    const roleMap = {};
    roles.forEach(function(r) { roleMap[r.role] = r; });
    const allEdges = [];
    roles.forEach(function(r) {
        (r.routes_to || []).forEach(function(rt) {
            if (roleMap[rt.role]) {
                allEdges.push({ from: r.role, to: rt.role, taskTypes: rt.task_types || [] });
            }
        });
    });

    // Assign layers via longest-path from sources (ignoring back-edges iteratively)
    const layers = {};
    // First pass: BFS layering ignoring any edge that would create a cycle
    const visited = new Set();
    const inDegree = {};
    const forwardAdj = {};
    roles.forEach(function(r) { inDegree[r.role] = 0; forwardAdj[r.role] = []; });
    // Tentative forward edges
    allEdges.forEach(function(e) {
        if (e.from !== e.to) { // skip self-loops for layering
            forwardAdj[e.from].push(e.to);
            inDegree[e.to] = (inDegree[e.to] || 0) + 1;
        }
    });
    // Kahn's to get an ordering (handles cycles by appending remaining)
    const queue = [];
    Object.keys(inDegree).forEach(function(k) { if (inDegree[k] === 0) queue.push(k); });
    const topoOrder = [];
    const tempInDeg = Object.assign({}, inDegree);
    while (queue.length > 0) {
        const node = queue.shift();
        topoOrder.push(node);
        (forwardAdj[node] || []).forEach(function(next) {
            tempInDeg[next]--;
            if (tempInDeg[next] === 0) queue.push(next);
        });
    }
    roles.forEach(function(r) { if (topoOrder.indexOf(r.role) === -1) topoOrder.push(r.role); });

    // Assign layers: longest path from any source
    topoOrder.forEach(function(r) { layers[r] = 0; });
    topoOrder.forEach(function(r) {
        (forwardAdj[r] || []).forEach(function(next) {
            if (topoOrder.indexOf(next) > topoOrder.indexOf(r)) {
                layers[next] = Math.max(layers[next], layers[r] + 1);
            }
        });
    });

    // Classify edges as forward or backward
    const forwardEdges = [];
    const backwardEdges = [];
    const selfLoops = [];
    allEdges.forEach(function(e) {
        if (e.from === e.to) {
            selfLoops.push(e);
        } else if (layers[e.to] > layers[e.from]) {
            forwardEdges.push(e);
        } else {
            backwardEdges.push(e);
        }
    });

    // Group nodes by layer for Y positioning
    const layerGroups = {};
    topoOrder.forEach(function(r) {
        const l = layers[r];
        if (!layerGroups[l]) layerGroups[l] = [];
        layerGroups[l].push(r);
    });

    return {
        topoOrder: topoOrder,
        layers: layers,
        layerGroups: layerGroups,
        forwardEdges: forwardEdges,
        backwardEdges: backwardEdges,
        selfLoops: selfLoops,
        roleMap: roleMap,
        numLayers: Math.max.apply(null, Object.values(layers)) + 1
    };
}

function renderPipeline() {
    const svg = document.getElementById('pipelineSvg');
    const roles = settingsData.roles || [];
    if (roles.length === 0) {
        svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="var(--text-muted)" font-size="14" font-family="Inter">No agents configured</text>';
        return;
    }

    const layout = computeGraphLayout(roles);
    const roleMap = layout.roleMap;

    const nodeW = 160;
    const nodeH = 56;
    const gapX = 100;
    const gapY = 36;
    const paddingX = 60;
    const arcSpace = 70; // space above/below for backward arcs

    // Compute max nodes in any single layer
    var maxLayerSize = 1;
    Object.values(layout.layerGroups).forEach(function(g) {
        if (g.length > maxLayerSize) maxLayerSize = g.length;
    });

    // Vertical center of the main graph area
    const mainHeight = maxLayerSize * nodeH + (maxLayerSize - 1) * gapY;
    const paddingY = arcSpace;
    const centerY = paddingY + mainHeight / 2;

    // Compute positions per node
    const positions = {};
    Object.keys(layout.layerGroups).forEach(function(layerStr) {
        const layer = parseInt(layerStr);
        const group = layout.layerGroups[layer];
        const groupHeight = group.length * nodeH + (group.length - 1) * gapY;
        const startY = centerY - groupHeight / 2;
        group.forEach(function(roleName, idx) {
            positions[roleName] = {
                x: paddingX + layer * (nodeW + gapX),
                y: startY + idx * (nodeH + gapY),
                layer: layer
            };
        });
    });

    // ViewBox
    const numLayers = layout.numLayers;
    const totalW = numLayers * (nodeW + gapX) + gapX + paddingX + 80; // extra for + node
    const totalH = mainHeight + arcSpace * 2 + 20;
    svg.setAttribute('viewBox', '0 0 ' + totalW + ' ' + totalH);
    svg.style.minHeight = Math.max(250, totalH) + 'px';

    let html = '';

    // ---- Defs ----
    html += '<defs>';
    // Connection glow filter
    html += '<filter id="connection-glow" x="-20%" y="-50%" width="140%" height="200%">';
    html += '<feGaussianBlur stdDeviation="4" result="blur"/>';
    html += '<feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>';
    html += '</filter>';
    // Particle glow filter
    html += '<filter id="particle-glow" x="-100%" y="-100%" width="300%" height="300%">';
    html += '<feGaussianBlur stdDeviation="3" result="blur"/>';
    html += '<feMerge><feMergeNode in="blur"/><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>';
    html += '</filter>';
    // Back-edge glow filter (subtler)
    html += '<filter id="back-glow" x="-20%" y="-50%" width="140%" height="200%">';
    html += '<feGaussianBlur stdDeviation="2.5" result="blur"/>';
    html += '<feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>';
    html += '</filter>';

    // Per-edge gradients and arrowheads
    var connectionIdx = 0;
    var allEdges = layout.forwardEdges.concat(layout.backwardEdges).concat(layout.selfLoops);
    allEdges.forEach(function(e) {
        const fromRole = roleMap[e.from];
        const toRole = roleMap[e.to];
        const fromColor = (fromRole && fromRole.color) || '#6366f1';
        const toColor = (toRole && toRole.color) || '#6366f1';
        const gid = 'conn-grad-' + connectionIdx;
        const mid = 'arrow-' + connectionIdx;
        html += '<linearGradient id="' + gid + '" x1="0%" y1="0%" x2="100%" y2="0%">';
        html += '<stop offset="0%" stop-color="' + escapeHtml(fromColor) + '"/>';
        html += '<stop offset="100%" stop-color="' + escapeHtml(toColor) + '"/>';
        html += '</linearGradient>';
        html += '<marker id="' + mid + '" markerWidth="12" markerHeight="9" refX="11" refY="4.5" orient="auto">';
        html += '<polygon points="0 0, 12 4.5, 0 9" fill="' + escapeHtml(toColor) + '" opacity="0.85"/>';
        html += '</marker>';
        connectionIdx++;
    });

    // Glow filter per role (for nodes)
    roles.forEach(function(r) {
        const c = r.color || '#6366f1';
        html += '<filter id="glow-' + r.role + '" x="-30%" y="-30%" width="160%" height="160%">';
        html += '<feGaussianBlur stdDeviation="6" result="blur"/>';
        html += '<feFlood flood-color="' + escapeHtml(c) + '" flood-opacity="0.3" result="color"/>';
        html += '<feComposite in="color" in2="blur" operator="in" result="shadow"/>';
        html += '<feMerge><feMergeNode in="shadow"/><feMergeNode in="SourceGraphic"/></feMerge>';
        html += '</filter>';
    });
    html += '</defs>';

    // ---- Forward connections ----
    var connIdx = 0;
    layout.forwardEdges.forEach(function(e) {
        const from = positions[e.from];
        const to = positions[e.to];
        if (!from || !to) { connIdx++; return; }
        const x1 = from.x + nodeW;
        const y1 = from.y + nodeH / 2;
        const x2 = to.x;
        const y2 = to.y + nodeH / 2;
        const cx1 = x1 + (x2 - x1) * 0.4;
        const cx2 = x2 - (x2 - x1) * 0.4;
        const pathD = 'M' + x1 + ' ' + y1 + ' C' + cx1 + ' ' + y1 + ' ' + cx2 + ' ' + y2 + ' ' + x2 + ' ' + y2;
        const gradId = 'conn-grad-' + connIdx;
        const arrowId = 'arrow-' + connIdx;
        const fromColor = (roleMap[e.from] && roleMap[e.from].color) || '#6366f1';
        const toColor = (roleMap[e.to] && roleMap[e.to].color) || '#6366f1';

        // Glow
        html += '<path class="pipeline-connection-glow" d="' + pathD + '" fill="none" stroke="url(#' + gradId + ')" stroke-width="8" opacity="0.15" filter="url(#connection-glow)"/>';
        // Main path
        html += '<path class="pipeline-connection" data-from="' + escapeHtml(e.from) + '" data-to="' + escapeHtml(e.to) + '" d="' + pathD + '" fill="none" stroke="url(#' + gradId + ')" stroke-width="2.5" stroke-linecap="round" marker-end="url(#' + arrowId + ')" opacity="0.7"/>';
        // Particles
        html += '<circle class="pipeline-particle" r="3.5" fill="' + escapeHtml(fromColor) + '" opacity="0.9" filter="url(#particle-glow)">';
        html += '<animateMotion dur="2.5s" repeatCount="indefinite" path="' + pathD + '"/>';
        html += '</circle>';
        html += '<circle class="pipeline-particle" r="2.5" fill="' + escapeHtml(toColor) + '" opacity="0.7" filter="url(#particle-glow)">';
        html += '<animateMotion dur="2.5s" repeatCount="indefinite" begin="1.25s" path="' + pathD + '"/>';
        html += '</circle>';
        connIdx++;
    });

    // ---- Backward connections (arcs looping above/below) ----
    var backIdx = 0;
    layout.backwardEdges.forEach(function(e) {
        const from = positions[e.from];
        const to = positions[e.to];
        if (!from || !to) { connIdx++; backIdx++; return; }
        const fromColor = (roleMap[e.from] && roleMap[e.from].color) || '#6366f1';
        const toColor = (roleMap[e.to] && roleMap[e.to].color) || '#6366f1';
        const gradId = 'conn-grad-' + connIdx;
        const arrowId = 'arrow-' + connIdx;

        // Start/end points: exit bottom of source, enter bottom of target
        const x1 = from.x + nodeW / 2;
        const y1 = from.y + nodeH;
        const x2 = to.x + nodeW / 2;
        const y2 = to.y + nodeH;

        // Arc below: how far down to curve
        var arcDrop = 40 + backIdx * 28;
        var arcY = Math.max(y1, y2) + arcDrop;

        // Cubic bezier: go down from source, curve across, come up to target
        const pathD = 'M' + x1 + ' ' + y1 + ' C' + x1 + ' ' + arcY + ' ' + x2 + ' ' + arcY + ' ' + x2 + ' ' + y2;

        // Glow (subtler)
        html += '<path class="pipeline-connection-glow" d="' + pathD + '" fill="none" stroke="url(#' + gradId + ')" stroke-width="6" opacity="0.08" filter="url(#back-glow)"/>';
        // Main path (dashed to distinguish from forward)
        html += '<path class="pipeline-connection pipeline-back-edge" data-from="' + escapeHtml(e.from) + '" data-to="' + escapeHtml(e.to) + '" d="' + pathD + '" fill="none" stroke="url(#' + gradId + ')" stroke-width="1.8" stroke-dasharray="8 5" stroke-linecap="round" marker-end="url(#' + arrowId + ')" opacity="0.5"/>';
        // Label
        var labelX = (x1 + x2) / 2;
        var labelY = arcY + 4;
        var labelText = (e.taskTypes && e.taskTypes.length > 0) ? e.taskTypes[0] : '';
        if (labelText) {
            html += '<text x="' + labelX + '" y="' + labelY + '" text-anchor="middle" font-size="9" font-weight="500" fill="var(--text-muted)" font-family="Inter, sans-serif" opacity="0.7">' + escapeHtml(labelText) + '</text>';
        }
        // Single small particle
        html += '<circle class="pipeline-particle" r="2" fill="' + escapeHtml(toColor) + '" opacity="0.6" filter="url(#particle-glow)">';
        html += '<animateMotion dur="3.5s" repeatCount="indefinite" path="' + pathD + '"/>';
        html += '</circle>';
        connIdx++;
        backIdx++;
    });

    // ---- Self-loops ----
    layout.selfLoops.forEach(function(e) {
        const pos = positions[e.from];
        if (!pos) { connIdx++; return; }
        const c = (roleMap[e.from] && roleMap[e.from].color) || '#6366f1';
        const arrowId = 'arrow-' + connIdx;
        // Small loop arc on top of the node
        const cx = pos.x + nodeW / 2;
        const cy = pos.y;
        const loopR = 18;
        const pathD = 'M' + (cx - 14) + ' ' + cy + ' C' + (cx - 14) + ' ' + (cy - loopR * 2) + ' ' + (cx + 14) + ' ' + (cy - loopR * 2) + ' ' + (cx + 14) + ' ' + cy;
        html += '<path class="pipeline-connection" d="' + pathD + '" fill="none" stroke="' + escapeHtml(c) + '" stroke-width="1.5" stroke-dasharray="4 3" marker-end="url(#' + arrowId + ')" opacity="0.45"/>';
        html += '<circle class="pipeline-particle" r="2" fill="' + escapeHtml(c) + '" opacity="0.6" filter="url(#particle-glow)">';
        html += '<animateMotion dur="2s" repeatCount="indefinite" path="' + pathD + '"/>';
        html += '</circle>';
        connIdx++;
    });

    // ---- Nodes ----
    layout.topoOrder.forEach(function(roleName) {
        const r = roleMap[roleName];
        if (!r) return;
        const pos = positions[roleName];
        const c = r.color || '#6366f1';
        html += '<g class="pipeline-node" data-role="' + escapeHtml(r.role) + '" transform="translate(' + pos.x + ',' + pos.y + ')">';
        html += '<rect width="' + nodeW + '" height="' + nodeH + '" rx="12" fill="' + escapeHtml(c) + '" fill-opacity="0.12" stroke="' + escapeHtml(c) + '" stroke-width="1.5" filter="url(#glow-' + r.role + ')"/>';
        html += '<text x="' + (nodeW / 2) + '" y="' + (nodeH / 2 - 6) + '" text-anchor="middle" font-size="18" fill="var(--text-primary)">' + escapeHtml(r.emoji || '') + '</text>';
        html += '<text x="' + (nodeW / 2) + '" y="' + (nodeH / 2 + 14) + '" text-anchor="middle" font-size="12" font-weight="600" fill="var(--text-primary)" font-family="Inter, sans-serif">' + escapeHtml(r.display_name || r.role) + '</text>';
        // Output port (right edge)
        html += '<circle class="pipeline-port output-port" cx="' + nodeW + '" cy="' + (nodeH / 2) + '" r="5" fill="' + escapeHtml(c) + '" stroke="var(--bg-primary)" stroke-width="2" data-role="' + escapeHtml(r.role) + '" data-port="output"/>';
        // Input port (left edge)
        html += '<circle class="pipeline-port input-port" cx="0" cy="' + (nodeH / 2) + '" r="5" fill="' + escapeHtml(c) + '" stroke="var(--bg-primary)" stroke-width="2" data-role="' + escapeHtml(r.role) + '" data-port="input"/>';
        html += '</g>';
    });

    // "+" node at the end (next to last layer)
    var addX = paddingX + numLayers * (nodeW + gapX);
    var addY = centerY - nodeH / 2;
    html += '<g class="pipeline-add-node" style="cursor:pointer" onclick="openWizard()">';
    html += '<rect x="' + addX + '" y="' + addY + '" width="' + nodeH + '" height="' + nodeH + '" rx="12" fill="none" stroke="var(--border-subtle)" stroke-width="2" stroke-dasharray="6 4"/>';
    html += '<text x="' + (addX + nodeH / 2) + '" y="' + (addY + nodeH / 2 + 6) + '" text-anchor="middle" font-size="24" fill="var(--text-muted)" font-family="Inter, sans-serif">+</text>';
    html += '</g>';

    // Temp drag line
    html += '<line id="pipelineDragLine" x1="0" y1="0" x2="0" y2="0" stroke="var(--accent-cyan)" stroke-width="2" stroke-dasharray="4 4" visibility="hidden"/>';

    svg.innerHTML = html;
    attachPipelineDragHandlers();
}

function attachPipelineDragHandlers() {
    const svg = document.getElementById('pipelineSvg');
    const dragLine = document.getElementById('pipelineDragLine');
    if (!svg || !dragLine) return;

    svg.querySelectorAll('.output-port').forEach(function(port) {
        port.addEventListener('mousedown', function(e) {
            e.stopPropagation();
            const role = port.getAttribute('data-role');
            const rect = svg.getBoundingClientRect();
            const svgW = svg.viewBox.baseVal.width;
            const svgH = svg.viewBox.baseVal.height;
            const scaleX = svgW / rect.width;
            const scaleY = svgH / rect.height;
            const cx = parseFloat(port.getAttribute('cx'));
            const parentTransform = port.closest('.pipeline-node').getAttribute('transform');
            const match = parentTransform.match(/translate\(([^,]+),([^)]+)\)/);
            const tx = match ? parseFloat(match[1]) : 0;
            const ty = match ? parseFloat(match[2]) : 0;
            const cy = parseFloat(port.getAttribute('cy'));
            dragState = {
                fromRole: role,
                startX: tx + cx,
                startY: ty + cy,
                rect: rect,
                scaleX: scaleX,
                scaleY: scaleY
            };
            dragLine.setAttribute('x1', dragState.startX);
            dragLine.setAttribute('y1', dragState.startY);
            dragLine.setAttribute('x2', dragState.startX);
            dragLine.setAttribute('y2', dragState.startY);
            dragLine.setAttribute('visibility', 'visible');
        });
    });

    svg.addEventListener('mousemove', function(e) {
        if (!dragState) return;
        const x = (e.clientX - dragState.rect.left) * dragState.scaleX;
        const y = (e.clientY - dragState.rect.top) * dragState.scaleY;
        dragLine.setAttribute('x2', x);
        dragLine.setAttribute('y2', y);
    });

    svg.addEventListener('mouseup', function(e) {
        if (!dragState) return;
        const target = e.target.closest('.input-port');
        if (target) {
            const toRole = target.getAttribute('data-role');
            if (toRole && toRole !== dragState.fromRole) {
                addRouteFromPipeline(dragState.fromRole, toRole);
            }
        }
        dragLine.setAttribute('visibility', 'hidden');
        dragState = null;
    });

    // Click connection to offer delete
    svg.querySelectorAll('.pipeline-connection').forEach(function(conn) {
        conn.addEventListener('click', function(e) {
            e.stopPropagation();
            var from = conn.getAttribute('data-from');
            var to = conn.getAttribute('data-to');
            if (confirm('Remove route from ' + from + ' to ' + to + '?')) {
                removeRoute(from, to);
            }
        });
    });
}

function addRouteFromPipeline(fromRole, toRole) {
    var r = getRoleByName(fromRole);
    if (!r) return;
    if (!r.routes_to) r.routes_to = [];
    var exists = r.routes_to.some(function(rt) { return rt.role === toRole; });
    if (exists) {
        showToast('Route already exists', 'info');
        return;
    }
    r.routes_to.push({ role: toRole, task_types: [] });
    markUnsaved();
    renderAll();
    showToast('Route added: ' + fromRole + ' \u2192 ' + toRole, 'success');
}

function removeRoute(fromRole, toRole) {
    var r = getRoleByName(fromRole);
    if (!r || !r.routes_to) return;
    r.routes_to = r.routes_to.filter(function(rt) { return rt.role !== toRole; });
    markUnsaved();
    renderAll();
    showToast('Route removed', 'info');
}

// ================================================================
// Agent Cards
// ================================================================
function renderAgentCards() {
    var grid = document.getElementById('agentsGrid');
    var html = '';

    (settingsData.roles || []).forEach(function(role, idx) {
        var c = role.color || '#6366f1';
        var toolCount = (role.tools || []).length;
        var modelName = (role.model || 'default').split('/').pop().split('-').slice(0, 3).join('-');
        if (modelName.length > 24) modelName = modelName.substring(0, 24) + '...';

        html += '<div class="agent-card" style="animation-delay:' + (idx * 0.08) + 's">';
        html += '<div class="agent-card-accent" style="background:' + escapeHtml(c) + '"></div>';

        // Header
        html += '<div class="agent-card-header">';
        html += '<div class="agent-card-identity">';
        html += '<span class="agent-emoji">' + escapeHtml(role.emoji || '') + '</span>';
        html += '<div>';
        html += '<span class="agent-name">' + escapeHtml(role.display_name || role.role) + '</span>';
        html += '<span class="agent-role-tag">' + escapeHtml(role.role) + '</span>';
        html += '</div></div>';
        html += '<div class="agent-card-actions">';
        html += '<button class="card-action-btn" title="Duplicate" onclick="duplicateRole(\'' + escapeHtml(role.role) + '\')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>';
        html += '<button class="card-action-btn delete" title="Delete" onclick="deleteRole(\'' + escapeHtml(role.role) + '\')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>';
        html += '</div></div>';

        // Quick stats
        html += '<div class="quick-stats">';
        html += '<span class="stat-chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>' + escapeHtml(modelName) + '</span>';
        html += '<span class="stat-chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>' + (role.max_instances || 1) + ' inst</span>';
        html += '<span class="stat-chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>' + toolCount + ' tools</span>';
        html += '</div>';

        // Accordions
        var roleId = escapeHtml(role.role);

        // Identity
        html += renderAccordion(roleId, 'identity', 'Identity', renderIdentityFields(role));
        // Model & Execution
        html += renderAccordion(roleId, 'model', 'Model & Execution', renderModelFields(role));
        // Tools
        html += renderAccordion(roleId, 'tools', 'Tools', renderToolsFields(role));
        // Routing
        html += renderAccordion(roleId, 'routing', 'Routing', renderRoutingFields(role));
        // System Prompt
        html += renderAccordion(roleId, 'prompt', 'System Prompt', renderPromptFields(role));
        // Advanced
        html += renderAccordion(roleId, 'advanced', 'Advanced', renderAdvancedFields(role));

        html += '</div>';
    });

    // Add new agent card
    html += '<div class="add-agent-card" onclick="openWizard()" role="button" tabindex="0" aria-label="Add new agent" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openWizard()}">';
    html += '<div class="plus-icon">+</div>';
    html += '<span>Add New Agent</span>';
    html += '</div>';

    grid.innerHTML = html;
}

function renderAccordion(roleId, section, title, content) {
    var id = roleId + '-' + section;
    return '<div class="accordion">' +
        '<div class="accordion-header" onclick="toggleAccordion(\'' + id + '\')" id="ah-' + id + '" role="button" tabindex="0" aria-expanded="false" aria-controls="ab-' + id + '" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();toggleAccordion(\'' + id + '\')}">' +
        '<span>' + title + '</span>' +
        '<svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>' +
        '</div>' +
        '<div class="accordion-body" id="ab-' + id + '" role="region" aria-labelledby="ah-' + id + '">' +
        '<div class="accordion-content">' + content + '</div>' +
        '</div></div>';
}

function toggleAccordion(id) {
    var header = document.getElementById('ah-' + id);
    var body = document.getElementById('ab-' + id);
    if (!header || !body) return;
    header.classList.toggle('open');
    body.classList.toggle('open');
    var isOpen = header.classList.contains('open');
    header.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
}

// ----- Identity Fields -----
function renderIdentityFields(role) {
    var r = escapeHtml(role.role);
    return '<div class="form-row">' +
        '<div class="form-group" role="group" aria-label="Display name for ' + r + '"><label class="form-label" for="id-displayname-' + r + '">Display Name</label>' +
        '<input class="form-input" id="id-displayname-' + r + '" value="' + escapeHtml(role.display_name || '') + '" onchange="updateRole(\'' + r + '\',\'display_name\',this.value)"></div>' +
        '<div class="form-group" role="group" aria-label="Prefix for ' + r + '"><label class="form-label" for="id-prefix-' + r + '">Prefix</label>' +
        '<input class="form-input" id="id-prefix-' + r + '" value="' + escapeHtml(role.prefix || '') + '" maxlength="4" onchange="updateRole(\'' + r + '\',\'prefix\',this.value)"></div>' +
        '</div>' +
        '<div class="form-row">' +
        '<div class="form-group" role="group" aria-label="Color for ' + r + '"><label class="form-label" for="id-color-' + r + '">Color</label>' +
        '<input type="color" class="form-input" id="id-color-' + r + '" value="' + escapeHtml(role.color || '#6366f1') + '" onchange="updateRole(\'' + r + '\',\'color\',this.value)"></div>' +
        '<div class="form-group" role="group" aria-label="Emoji for ' + r + '"><label class="form-label" for="id-emoji-' + r + '">Emoji</label>' +
        '<input class="form-input" id="id-emoji-' + r + '" value="' + escapeHtml(role.emoji || '') + '" onchange="updateRole(\'' + r + '\',\'emoji\',this.value)" style="font-size:18px"></div>' +
        '</div>';
}

// ----- Model & Execution -----
function renderModelFields(role) {
    var r = escapeHtml(role.role);
    var modelOpts = '';
    var models = availableModels.length > 0 ? availableModels : [
        { id: 'claude-opus-4-6', name: 'Claude Opus 4.6' },
        { id: 'claude-sonnet-4-6', name: 'Claude Sonnet 4.6' },
        { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5' }
    ];
    models.forEach(function(m) {
        var mid = m.id || m;
        var mname = m.name || mid;
        modelOpts += '<option value="' + escapeHtml(mid) + '"' + (role.model === mid ? ' selected' : '') + '>' + escapeHtml(mname) + '</option>';
    });

    var autoScale = role.auto_scale || {};
    var autoEnabled = autoScale.enabled || false;

    var html = '<div class="form-group" role="group" aria-label="Model selection for ' + r + '"><label class="form-label" for="model-' + r + '">Model</label>' +
        '<select class="form-select" id="model-' + r + '" onchange="updateRole(\'' + r + '\',\'model\',this.value)">' + modelOpts + '</select></div>';
    html += '<div class="form-row-3">';
    html += '<div class="form-group" role="group" aria-label="Max turns for ' + r + '"><label class="form-label" for="maxturns-' + r + '">Max Turns</label>' +
        '<input type="number" class="form-input" id="maxturns-' + r + '" value="' + (role.max_turns || '') + '" min="1" onchange="updateRole(\'' + r + '\',\'max_turns\',parseInt(this.value)||0)"></div>';
    html += '<div class="form-group" role="group" aria-label="Max execution time for ' + r + '"><label class="form-label" for="maxexec-' + r + '">Max Execution Time</label><div class="number-with-unit">' +
        '<input type="number" class="form-input" id="maxexec-' + r + '" value="' + (role.max_execution_time || '') + '" min="0" onchange="updateRole(\'' + r + '\',\'max_execution_time\',parseInt(this.value)||0)" aria-describedby="maxexec-unit-' + r + '">' +
        '<span class="unit-label" id="maxexec-unit-' + r + '">seconds</span></div></div>';
    html += '<div class="form-group" role="group" aria-label="Max instances for ' + r + '"><label class="form-label" for="maxinst-' + r + '">Max Instances</label>' +
        '<input type="number" class="form-input" id="maxinst-' + r + '" value="' + (role.max_instances || 1) + '" min="1" onchange="updateRole(\'' + r + '\',\'max_instances\',parseInt(this.value)||1)"></div>';
    html += '</div>';

    html += '<div class="toggle-row"><span class="toggle-label">Auto Scale</span>' +
        '<label class="toggle-switch"><input type="checkbox"' + (autoEnabled ? ' checked' : '') + ' onchange="toggleAutoScale(\'' + r + '\',this.checked)"><span class="toggle-track"></span><span class="toggle-thumb"></span></label></div>';
    html += '<div id="autoscale-' + r + '" style="' + (autoEnabled ? '' : 'display:none;') + 'margin-top:12px">';
    html += '<div class="form-row">';
    html += '<div class="form-group" role="group" aria-label="Scale up threshold for ' + r + '"><label class="form-label" for="scaleup-' + r + '">Scale Up Threshold</label>' +
        '<input type="number" class="form-input" id="scaleup-' + r + '" value="' + (autoScale.scale_up_threshold || '') + '" min="1" onchange="updateAutoScale(\'' + r + '\',\'scale_up_threshold\',parseInt(this.value)||0)"></div>';
    html += '<div class="form-group" role="group" aria-label="Scale down idle time for ' + r + '"><label class="form-label" for="scaledown-' + r + '">Scale Down Idle<span class="form-sublabel" id="scaledown-unit-' + r + '">(minutes)</span></label>' +
        '<input type="number" class="form-input" id="scaledown-' + r + '" value="' + (autoScale.scale_down_idle || '') + '" min="1" onchange="updateAutoScale(\'' + r + '\',\'scale_down_idle\',parseInt(this.value)||0)" aria-describedby="scaledown-unit-' + r + '"></div>';
    html += '</div></div>';
    return html;
}

function toggleAutoScale(roleName, enabled) {
    var r = getRoleByName(roleName);
    if (!r) return;
    if (!r.auto_scale) r.auto_scale = {};
    r.auto_scale.enabled = enabled;
    var el = document.getElementById('autoscale-' + roleName);
    if (el) el.style.display = enabled ? '' : 'none';
    markUnsaved();
}

function updateAutoScale(roleName, field, value) {
    var r = getRoleByName(roleName);
    if (!r) return;
    if (!r.auto_scale) r.auto_scale = {};
    r.auto_scale[field] = value;
    markUnsaved();
}

// ----- Tools Fields -----
function renderToolsFields(role) {
    var r = escapeHtml(role.role);
    var html = '<div class="chips-container" id="tools-chips-' + r + '">';
    (role.tools || []).forEach(function(tool) {
        html += '<span class="chip">' + escapeHtml(tool) + '<button class="chip-remove" onclick="removeTool(\'' + r + '\',\'' + escapeHtml(tool) + '\')">&times;</button></span>';
    });
    html += '</div>';
    html += '<input class="chip-add-input" placeholder="Add tool..." list="toolsList-' + r + '" onkeydown="addToolOnEnter(event,\'' + r + '\')">';
    html += '<datalist id="toolsList-' + r + '">';
    COMMON_TOOLS.forEach(function(t) {
        html += '<option value="' + escapeHtml(t) + '">';
    });
    html += '</datalist>';
    return html;
}

function removeTool(roleName, tool) {
    var r = getRoleByName(roleName);
    if (!r) return;
    r.tools = (r.tools || []).filter(function(t) { return t !== tool; });
    markUnsaved();
    renderAgentCards();
}

function addToolOnEnter(e, roleName) {
    if (e.key !== 'Enter') return;
    var val = e.target.value.trim();
    if (!val) return;
    var r = getRoleByName(roleName);
    if (!r) return;
    if (!r.tools) r.tools = [];
    if (r.tools.indexOf(val) === -1) {
        r.tools.push(val);
        markUnsaved();
    }
    e.target.value = '';
    renderAgentCards();
}

// ----- Routing Fields -----
function renderRoutingFields(role) {
    var r = escapeHtml(role.role);
    var html = '';
    (role.routes_to || []).forEach(function(rt, idx) {
        var target = getRoleByName(rt.role);
        var emoji = target ? (target.emoji || '') : '';
        var name = target ? (target.display_name || rt.role) : rt.role;
        html += '<div class="route-item">';
        html += '<span class="route-target">' + escapeHtml(emoji) + ' ' + escapeHtml(name) + '</span>';
        html += '<div class="route-tasks">';
        (rt.task_types || []).forEach(function(tt) {
            html += '<span class="route-task-chip">' + escapeHtml(tt) + '</span>';
        });
        html += '</div>';
        html += '<button class="route-remove-btn" onclick="removeRouteFromCard(\'' + r + '\',' + idx + ')" title="Remove route">&times;</button>';
        html += '</div>';
    });

    // Add route form
    html += '<div class="add-route-row">';
    html += '<select class="form-select" id="addRouteTarget-' + r + '">';
    html += '<option value="">Select role...</option>';
    (settingsData.roles || []).forEach(function(otherRole) {
        if (otherRole.role !== role.role) {
            html += '<option value="' + escapeHtml(otherRole.role) + '">' + escapeHtml(otherRole.emoji || '') + ' ' + escapeHtml(otherRole.display_name || otherRole.role) + '</option>';
        }
    });
    html += '</select>';
    html += '<input class="form-input" id="addRouteTypes-' + r + '" placeholder="Task types (comma-sep)">';
    html += '<button class="add-route-btn" onclick="addRouteFromCard(\'' + r + '\')">Add</button>';
    html += '</div>';
    return html;
}

function removeRouteFromCard(roleName, idx) {
    var r = getRoleByName(roleName);
    if (!r || !r.routes_to) return;
    r.routes_to.splice(idx, 1);
    markUnsaved();
    renderAll();
}

function addRouteFromCard(roleName) {
    var targetEl = document.getElementById('addRouteTarget-' + roleName);
    var typesEl = document.getElementById('addRouteTypes-' + roleName);
    if (!targetEl || !typesEl) return;
    var target = targetEl.value;
    if (!target) { showToast('Select a target role', 'error'); return; }
    var types = typesEl.value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
    var r = getRoleByName(roleName);
    if (!r) return;
    if (!r.routes_to) r.routes_to = [];
    var exists = r.routes_to.some(function(rt) { return rt.role === target; });
    if (exists) {
        showToast('Route already exists', 'info');
        return;
    }
    r.routes_to.push({ role: target, task_types: types });
    markUnsaved();
    renderAll();
    showToast('Route added', 'success');
}

// ----- System Prompt Fields -----
function renderPromptFields(role) {
    var r = escapeHtml(role.role);
    return '<div class="prompt-header"><label class="form-label" for="prompt-' + r + '">System Prompt</label>' +
        '<button class="expand-btn" onclick="openPromptFullscreen(\'' + r + '\')" aria-label="Expand system prompt editor for ' + r + '"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>Expand</button></div>' +
        '<textarea class="form-textarea" rows="6" id="prompt-' + r + '" aria-label="System prompt for ' + r + '" onchange="updateRole(\'' + r + '\',\'system_prompt\',this.value)">' + escapeHtml(role.system_prompt || '') + '</textarea>';
}

function openPromptFullscreen(roleName) {
    promptFullscreenRole = roleName;
    var r = getRoleByName(roleName);
    var overlay = document.getElementById('promptFullscreen');
    var textarea = document.getElementById('promptFullscreenTextarea');
    document.getElementById('promptFullscreenTitle').textContent = (r ? (r.display_name || r.role) : roleName) + ' - System Prompt';
    textarea.value = r ? (r.system_prompt || '') : '';
    overlay.classList.add('open');
    textarea.focus();
}

function closePromptFullscreen() {
    var overlay = document.getElementById('promptFullscreen');
    if (!overlay.classList.contains('open')) return;
    if (promptFullscreenRole) {
        var textarea = document.getElementById('promptFullscreenTextarea');
        updateRole(promptFullscreenRole, 'system_prompt', textarea.value);
        // sync back to card textarea
        var cardTa = document.getElementById('prompt-' + promptFullscreenRole);
        if (cardTa) cardTa.value = textarea.value;
    }
    overlay.classList.remove('open');
    promptFullscreenRole = null;
}

// ----- Advanced Fields -----
function renderAdvancedFields(role) {
    var r = escapeHtml(role.role);
    var html = '';

    // Produces
    html += '<div class="form-group"><label class="form-label">Produces</label>';
    html += '<div class="chips-container">';
    (role.produces || []).forEach(function(p) {
        html += '<span class="chip">' + escapeHtml(p) + '<button class="chip-remove" onclick="removeFromArray(\'' + r + '\',\'produces\',\'' + escapeHtml(p) + '\')">&times;</button></span>';
    });
    html += '</div>';
    html += '<input class="chip-add-input" placeholder="Add produce type..." onkeydown="addToArrayOnEnter(event,\'' + r + '\',\'produces\')">';
    html += '</div>';

    // Accepts
    html += '<div class="form-group"><label class="form-label">Accepts</label>';
    html += '<div class="chips-container">';
    (role.accepts || []).forEach(function(a) {
        html += '<span class="chip">' + escapeHtml(a) + '<button class="chip-remove" onclick="removeFromArray(\'' + r + '\',\'accepts\',\'' + escapeHtml(a) + '\')">&times;</button></span>';
    });
    html += '</div>';
    html += '<input class="chip-add-input" placeholder="Add accept type..." onkeydown="addToArrayOnEnter(event,\'' + r + '\',\'accepts\')">';
    html += '</div>';


    // Context includes
    html += '<div class="form-group"><label class="form-label">Context Includes</label>';
    html += '<div class="checkbox-group">';
    CONTEXT_OPTIONS.forEach(function(opt) {
        var checked = (role.context_includes || []).indexOf(opt) !== -1;
        html += '<label class="checkbox-item"><input type="checkbox"' + (checked ? ' checked' : '') + ' onchange="toggleContextInclude(\'' + r + '\',\'' + opt + '\',this.checked)"><label>' + escapeHtml(opt.replace(/_/g, ' ')) + '</label></label>';
    });
    html += '</div></div>';

    // Can create groups + group type
    var canCreate = role.can_create_groups || false;
    html += '<div class="toggle-row"><span class="toggle-label">Can Create Groups</span>' +
        '<label class="toggle-switch"><input type="checkbox"' + (canCreate ? ' checked' : '') + ' onchange="updateRole(\'' + r + '\',\'can_create_groups\',this.checked);toggleGroupType(\'' + r + '\',this.checked)"><span class="toggle-track"></span><span class="toggle-thumb"></span></label></div>';
    html += '<div id="grouptype-' + r + '" style="' + (canCreate ? '' : 'display:none;') + 'margin-top:8px">';
    html += '<div class="form-group"><label class="form-label">Group Type</label>' +
        '<input class="form-input" value="' + escapeHtml(role.group_type || '') + '" onchange="updateRole(\'' + r + '\',\'group_type\',this.value)"></div>';
    html += '</div>';

    return html;
}

function removeFromArray(roleName, field, val) {
    var r = getRoleByName(roleName);
    if (!r || !r[field]) return;
    r[field] = r[field].filter(function(v) { return v !== val; });
    markUnsaved();
    renderAgentCards();
}

function addToArrayOnEnter(e, roleName, field) {
    if (e.key !== 'Enter') return;
    var val = e.target.value.trim();
    if (!val) return;
    var r = getRoleByName(roleName);
    if (!r) return;
    if (!r[field]) r[field] = [];
    if (r[field].indexOf(val) === -1) {
        r[field].push(val);
        markUnsaved();
    }
    e.target.value = '';
    renderAgentCards();
}

function toggleContextInclude(roleName, opt, checked) {
    var r = getRoleByName(roleName);
    if (!r) return;
    if (!r.context_includes) r.context_includes = [];
    if (checked && r.context_includes.indexOf(opt) === -1) {
        r.context_includes.push(opt);
    } else if (!checked) {
        r.context_includes = r.context_includes.filter(function(v) { return v !== opt; });
    }
    markUnsaved();
}

function toggleGroupType(roleName, show) {
    var el = document.getElementById('grouptype-' + roleName);
    if (el) el.style.display = show ? '' : 'none';
}

// ================================================================
// Generic Role Update
// ================================================================
function updateRole(roleName, field, value) {
    var r = getRoleByName(roleName);
    if (!r) return;
    r[field] = value;
    markUnsaved();
}

// ================================================================
// Duplicate & Delete Role
// ================================================================
function duplicateRole(roleName) {
    var r = getRoleByName(roleName);
    if (!r) return;
    var clone = deepClone(r);
    clone.role = r.role + '_copy';
    clone.display_name = (r.display_name || r.role) + ' (Copy)';
    clone.prefix = (r.prefix || 'XX') + '2';
    settingsData.roles.push(clone);
    markUnsaved();
    renderAll();
    showToast('Duplicated ' + roleName, 'success');
}

async function deleteRole(roleName) {
    if (!confirm('Delete agent role "' + roleName + '"? This cannot be undone.')) return;
    try {
        await fetch('/api/settings/roles/' + encodeURIComponent(roleName), { method: 'DELETE' });
        settingsData.roles = settingsData.roles.filter(function(r) { return r.role !== roleName; });
        // Remove routes pointing to deleted role
        settingsData.roles.forEach(function(r) {
            if (r.routes_to) {
                r.routes_to = r.routes_to.filter(function(rt) { return rt.role !== roleName; });
            }
        });
        renderAll();
        showToast('Deleted ' + roleName, 'success');
    } catch (e) {
        showToast('Failed to delete: ' + e.message, 'error');
    }
}

// ================================================================
// Team Settings
// ================================================================
function toggleTeamSection() {
    var header = document.getElementById('teamSectionHeader');
    var body = document.getElementById('teamSectionBody');
    header.classList.toggle('open');
    body.classList.toggle('open');
    var isOpen = header.classList.contains('open');
    header.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
}

function renderTeamSettings() {
    var t = settingsData.team || {};
    var content = document.getElementById('teamSectionContent');
    var html = '';

    // General group
    html += '<div class="team-group" role="group" aria-label="General team settings"><h3>General</h3>';
    html += '<div class="form-group"><label class="form-label" for="team-name">Team Name</label>' +
        '<input class="form-input" id="team-name" value="' + escapeHtml(t.name || '') + '" onchange="updateTeam(\'name\',this.value)"></div>';
    html += '<div class="form-group"><label class="form-label" for="team-projdir">Project Directory</label>' +
        '<input class="form-input" id="team-projdir" value="' + escapeHtml(t.project_dir || '') + '" disabled></div>';
    html += '<div class="form-group"><label class="form-label" for="team-model">Default Model</label>' +
        '<input class="form-input" id="team-model" value="' + escapeHtml(t.default_model || '') + '" onchange="updateTeam(\'default_model\',this.value)"></div>';
    html += '<div class="form-group"><label class="form-label" for="team-poll">Poll Interval<span class="form-sublabel" id="team-poll-unit">(seconds)</span></label>' +
        '<input type="number" class="form-input" id="team-poll" value="' + (t.default_poll_interval || '') + '" min="1" onchange="updateTeam(\'default_poll_interval\',parseInt(this.value)||5)" aria-describedby="team-poll-unit"></div>';
    html += '<div class="form-group"><label class="form-label" for="team-idle">Idle Timeout<span class="form-sublabel" id="team-idle-unit">(seconds)</span></label>' +
        '<input type="number" class="form-input" id="team-idle" value="' + (t.default_idle_timeout || '') + '" min="0" onchange="updateTeam(\'default_idle_timeout\',parseInt(this.value)||0)" aria-describedby="team-idle-unit"></div>';
    html += '<div class="form-group"><label class="form-label" for="team-maxinst">Default Max Instances</label>' +
        '<input type="number" class="form-input" id="team-maxinst" value="' + (t.default_max_instances || '') + '" min="1" onchange="updateTeam(\'default_max_instances\',parseInt(this.value)||1)"></div>';
    html += '</div>';

    // Features group
    html += '<div class="team-group" role="group" aria-label="Feature toggles"><h3>Features</h3>';
    html += renderTeamToggle('Auth Enabled', 'auth_enabled', t.auth_enabled);
    html += renderTeamToggle('Cost Budgets', 'cost_budgets_enabled', t.cost_budgets_enabled);
    html += renderTeamToggle('Webhooks', 'webhooks_enabled', t.webhooks_enabled);
    html += '</div>';

    // Group Prefixes
    html += '<div class="team-group" role="group" aria-label="Group prefixes"><h3>Group Prefixes</h3>';
    html += '<div class="kv-rows" id="groupPrefixRows">';
    var prefixes = t.group_prefixes || {};
    Object.keys(prefixes).forEach(function(key) {
        html += renderKvRow(key, prefixes[key]);
    });
    html += '</div>';
    html += '<button class="kv-add-btn" onclick="addGroupPrefix()">+ Add Prefix</button>';
    html += '</div>';

    content.innerHTML = html;
}

function renderTeamToggle(label, field, value) {
    return '<div class="toggle-row"><span class="toggle-label">' + escapeHtml(label) + '</span>' +
        '<label class="toggle-switch"><input type="checkbox"' + (value ? ' checked' : '') + ' onchange="updateTeam(\'' + field + '\',this.checked)"><span class="toggle-track"></span><span class="toggle-thumb"></span></label></div>';
}

function renderKvRow(key, val) {
    return '<div class="kv-row">' +
        '<input class="form-input" value="' + escapeHtml(key) + '" placeholder="Key" onchange="updateGroupPrefixes()">' +
        '<input class="form-input" value="' + escapeHtml(val) + '" placeholder="Value" onchange="updateGroupPrefixes()">' +
        '<button class="kv-remove-btn" onclick="this.closest(\'.kv-row\').remove();updateGroupPrefixes()">&times;</button>' +
        '</div>';
}

function addGroupPrefix() {
    var rows = document.getElementById('groupPrefixRows');
    if (!rows) return;
    var div = document.createElement('div');
    div.className = 'kv-row';
    div.innerHTML = '<input class="form-input" placeholder="Key" onchange="updateGroupPrefixes()">' +
        '<input class="form-input" placeholder="Value" onchange="updateGroupPrefixes()">' +
        '<button class="kv-remove-btn" onclick="this.closest(\'.kv-row\').remove();updateGroupPrefixes()">&times;</button>';
    rows.appendChild(div);
}

function updateGroupPrefixes() {
    var rows = document.querySelectorAll('#groupPrefixRows .kv-row');
    var prefixes = {};
    rows.forEach(function(row) {
        var inputs = row.querySelectorAll('input');
        var key = inputs[0].value.trim();
        var val = inputs[1].value.trim();
        if (key) prefixes[key] = val;
    });
    settingsData.team.group_prefixes = prefixes;
    markUnsaved();
}

function updateTeam(field, value) {
    settingsData.team[field] = value;
    markUnsaved();
}

// ================================================================
// Validate & Save
// ================================================================
async function validateSettings() {
    try {
        var res = await fetch('/api/settings/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ team: settingsData.team, roles: settingsData.roles })
        });
        var data = await res.json();
        if (data.valid) {
            showToast('Validation passed', 'success');
        } else {
            (data.errors || []).forEach(function(err) {
                showToast(err, 'error');
            });
            if (!data.errors || data.errors.length === 0) {
                showToast('Validation failed', 'error');
            }
        }
    } catch (e) {
        showToast('Validation request failed: ' + e.message, 'error');
    }
}

async function saveAll() {
    var saveBtn = document.getElementById('saveBtn');
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation:spin 0.8s linear infinite;width:16px;height:16px"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Saving...';

    try {
        // Validate first
        var vRes = await fetch('/api/settings/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ team: settingsData.team, roles: settingsData.roles })
        });
        var vData = await vRes.json();
        if (vData.valid === false) {
            (vData.errors || ['Validation failed']).forEach(function(err) {
                showToast(err, 'error');
            });
            return;
        }

        // Save team
        await fetch('/api/settings/team', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settingsData.team)
        });

        // Save each role
        for (var i = 0; i < settingsData.roles.length; i++) {
            var role = settingsData.roles[i];
            await fetch('/api/settings/roles/' + encodeURIComponent(role.role), {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(role)
            });
        }

        markSaved();
        showToast('All settings saved successfully', 'success');
    } catch (e) {
        showToast('Save failed: ' + e.message, 'error');
    } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:16px;height:16px"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg> Save All Changes';
    }
}

// ================================================================
// New Agent Wizard
// ================================================================
function openWizard() {
    wizardStep = 0;
    wizardData = { role: '', display_name: '', prefix: '', color: '#6366f1', emoji: '', model: '', system_prompt: '', tools: [], receives_from: [], routes_to: [] };
    document.getElementById('wizardOverlay').classList.add('open');
    renderWizard();
}

function closeWizard() {
    document.getElementById('wizardOverlay').classList.remove('open');
}

function renderWizard() {
    // Update step dots
    document.querySelectorAll('.wizard-step-dot').forEach(function(dot, i) {
        dot.className = 'wizard-step-dot';
        if (i < wizardStep) dot.classList.add('completed');
        if (i === wizardStep) dot.classList.add('active');
    });

    var body = document.getElementById('wizardBody');
    var backBtn = document.getElementById('wizardBackBtn');
    var nextBtn = document.getElementById('wizardNextBtn');

    backBtn.style.visibility = wizardStep === 0 ? 'hidden' : 'visible';
    nextBtn.textContent = wizardStep === 2 ? 'Create' : 'Next';

    if (wizardStep === 0) {
        body.innerHTML = renderWizardStep1();
    } else if (wizardStep === 1) {
        body.innerHTML = renderWizardStep2();
    } else {
        body.innerHTML = renderWizardStep3();
    }
}

function renderWizardStep1() {
    return '<div class="form-group"><label class="form-label">Display Name</label>' +
        '<input class="form-input" id="wiz-display-name" value="' + escapeHtml(wizardData.display_name) + '" placeholder="e.g. Product Manager" oninput="wizUpdateIdentity()"></div>' +
        '<div class="form-row">' +
        '<div class="form-group"><label class="form-label">Role ID<span class="form-sublabel">(auto-generated)</span></label>' +
        '<input class="form-input" id="wiz-role-id" value="' + escapeHtml(wizardData.role) + '" placeholder="auto-generated"></div>' +
        '<div class="form-group"><label class="form-label">Prefix<span class="form-sublabel">(2-char)</span></label>' +
        '<input class="form-input" id="wiz-prefix" value="' + escapeHtml(wizardData.prefix) + '" maxlength="4"></div>' +
        '</div>' +
        '<div class="form-row">' +
        '<div class="form-group"><label class="form-label">Color</label>' +
        '<input type="color" class="form-input" id="wiz-color" value="' + escapeHtml(wizardData.color) + '"></div>' +
        '<div class="form-group"><label class="form-label">Emoji</label>' +
        '<input class="form-input" id="wiz-emoji" value="' + escapeHtml(wizardData.emoji) + '" placeholder="e.g. \uD83D\uDCCB" style="font-size:18px"></div>' +
        '</div>';
}

function wizUpdateIdentity() {
    var nameEl = document.getElementById('wiz-display-name');
    var roleEl = document.getElementById('wiz-role-id');
    var prefixEl = document.getElementById('wiz-prefix');
    if (nameEl && roleEl) roleEl.value = slugify(nameEl.value);
    if (nameEl && prefixEl && !prefixEl._userEdited) prefixEl.value = autoPrefix(nameEl.value);
}

function renderWizardStep2() {
    var models = availableModels.length > 0 ? availableModels : [
        { id: 'claude-opus-4-6', name: 'Claude Opus 4.6' },
        { id: 'claude-sonnet-4-6', name: 'Claude Sonnet 4.6' },
        { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5' }
    ];
    var opts = '';
    models.forEach(function(m) {
        var mid = m.id || m;
        var mname = m.name || mid;
        opts += '<option value="' + escapeHtml(mid) + '"' + (wizardData.model === mid ? ' selected' : '') + '>' + escapeHtml(mname) + '</option>';
    });

    var html = '<div class="form-group"><label class="form-label">Model</label>' +
        '<select class="form-select" id="wiz-model">' + opts + '</select></div>';
    html += '<div class="form-group"><label class="form-label">System Prompt</label>' +
        '<textarea class="form-textarea" id="wiz-prompt" rows="6" placeholder="Describe this agent\'s responsibilities...">' + escapeHtml(wizardData.system_prompt) + '</textarea></div>';
    html += '<div class="form-group"><label class="form-label">Tools</label><div class="checkbox-group">';
    COMMON_TOOLS.forEach(function(tool) {
        var checked = wizardData.tools.indexOf(tool) !== -1;
        html += '<label class="checkbox-item"><input type="checkbox" value="' + escapeHtml(tool) + '"' + (checked ? ' checked' : '') + ' class="wiz-tool-cb"><label>' + escapeHtml(tool) + '</label></label>';
    });
    html += '</div></div>';
    return html;
}

function renderWizardStep3() {
    var roleNames = settingsData.roles.map(function(r) { return r; });
    var html = '<div class="form-group"><label class="form-label">Receives From<span class="form-sublabel">(which agents route tasks to this one)</span></label>';
    html += '<div class="checkbox-group">';
    roleNames.forEach(function(r) {
        var checked = wizardData.receives_from.indexOf(r.role) !== -1;
        html += '<label class="checkbox-item"><input type="checkbox" value="' + escapeHtml(r.role) + '"' + (checked ? ' checked' : '') + ' class="wiz-recv-cb"><label>' + escapeHtml(r.emoji || '') + ' ' + escapeHtml(r.display_name || r.role) + '</label></label>';
    });
    html += '</div></div>';

    html += '<div class="form-group"><label class="form-label">Routes To<span class="form-sublabel">(tasks types to send)</span></label>';
    html += '<div class="checkbox-group">';
    roleNames.forEach(function(r) {
        var checked = wizardData.routes_to.some(function(rt) { return rt.role === r.role; });
        html += '<label class="checkbox-item"><input type="checkbox" value="' + escapeHtml(r.role) + '"' + (checked ? ' checked' : '') + ' class="wiz-route-cb"><label>' + escapeHtml(r.emoji || '') + ' ' + escapeHtml(r.display_name || r.role) + '</label></label>';
    });
    html += '</div></div>';
    return html;
}

function collectWizardData() {
    if (wizardStep === 0) {
        var nameEl = document.getElementById('wiz-display-name');
        var roleEl = document.getElementById('wiz-role-id');
        var prefixEl = document.getElementById('wiz-prefix');
        var colorEl = document.getElementById('wiz-color');
        var emojiEl = document.getElementById('wiz-emoji');
        if (nameEl) wizardData.display_name = nameEl.value;
        if (roleEl) wizardData.role = roleEl.value || slugify(wizardData.display_name);
        if (prefixEl) wizardData.prefix = prefixEl.value || autoPrefix(wizardData.display_name);
        if (colorEl) wizardData.color = colorEl.value;
        if (emojiEl) wizardData.emoji = emojiEl.value;
    } else if (wizardStep === 1) {
        var modelEl = document.getElementById('wiz-model');
        var promptEl = document.getElementById('wiz-prompt');
        if (modelEl) wizardData.model = modelEl.value;
        if (promptEl) wizardData.system_prompt = promptEl.value;
        wizardData.tools = [];
        document.querySelectorAll('.wiz-tool-cb:checked').forEach(function(cb) {
            wizardData.tools.push(cb.value);
        });
    } else if (wizardStep === 2) {
        wizardData.receives_from = [];
        document.querySelectorAll('.wiz-recv-cb:checked').forEach(function(cb) {
            wizardData.receives_from.push(cb.value);
        });
        wizardData.routes_to = [];
        document.querySelectorAll('.wiz-route-cb:checked').forEach(function(cb) {
            wizardData.routes_to.push({ role: cb.value, task_types: [] });
        });
    }
}

function wizardBack() {
    collectWizardData();
    if (wizardStep > 0) { wizardStep--; renderWizard(); }
}

function wizardNext() {
    collectWizardData();
    if (wizardStep < 2) {
        if (wizardStep === 0 && !wizardData.display_name.trim()) {
            showToast('Display name is required', 'error');
            return;
        }
        wizardStep++;
        renderWizard();
    } else {
        createNewAgent();
    }
}

async function createNewAgent() {
    if (!wizardData.role) {
        showToast('Role ID is required', 'error');
        return;
    }
    try {
        var payload = {
            role: wizardData.role,
            display_name: wizardData.display_name,
            prefix: wizardData.prefix,
            color: wizardData.color,
            emoji: wizardData.emoji,
            model: wizardData.model,
            system_prompt: wizardData.system_prompt,
            tools: wizardData.tools,
            routes_to: wizardData.routes_to,
            max_instances: 1,
            produces: [],
            accepts: [],
            context_includes: ['parent_artifact', 'root_artifact']
        };

        var res = await fetch('/api/settings/roles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) throw new Error('Server returned ' + res.status);

        // Add "receives_from" by updating source roles
        for (var i = 0; i < wizardData.receives_from.length; i++) {
            var sourceRole = getRoleByName(wizardData.receives_from[i]);
            if (sourceRole) {
                if (!sourceRole.routes_to) sourceRole.routes_to = [];
                sourceRole.routes_to.push({ role: wizardData.role, task_types: [] });
            }
        }

        closeWizard();
        showToast('Agent "' + wizardData.display_name + '" created', 'success');
        await loadSettings();
    } catch (e) {
        showToast('Failed to create agent: ' + e.message, 'error');
    }
}

// ================================================================
// V2 Intelligence Feature Sections
// ================================================================

function toggleV2Section(section) {
    var headerMap = { intel: 'v2IntelHeader', sec: 'v2SecHeader', budget: 'v2BudgetHeader' };
    var bodyMap = { intel: 'v2IntelBody', sec: 'v2SecBody', budget: 'v2BudgetBody' };
    var header = document.getElementById(headerMap[section]);
    var body = document.getElementById(bodyMap[section]);
    if (!header || !body) return;
    var isOpen = body.classList.contains('open');
    header.classList.toggle('open', !isOpen);
    body.classList.toggle('open', !isOpen);
    header.setAttribute('aria-expanded', String(!isOpen));
}

async function submitBudgetForm(event) {
    event.preventDefault();
    var scope = document.getElementById('v2BudgetScope').value;
    var amount = parseFloat(document.getElementById('v2BudgetAmount').value);
    var period = document.getElementById('v2BudgetPeriod').value;
    var feedback = document.getElementById('v2BudgetFeedback');
    var submitBtn = document.getElementById('v2BudgetSubmitBtn');

    if (isNaN(amount) || amount <= 0) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(244, 63, 94, 0.1)';
        feedback.style.color = 'var(--accent-rose)';
        feedback.style.border = '1px solid rgba(244, 63, 94, 0.25)';
        feedback.textContent = 'Please enter a valid budget amount greater than 0.';
        return false;
    }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px;animation:spin 1s linear infinite"><circle cx="12" cy="12" r="10" stroke-dasharray="60" stroke-dashoffset="15"/></svg> Creating...';

    try {
        var resp = await fetch('/api/budgets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scope: scope, amount: amount, period: period })
        });

        if (resp.ok) {
            var result = await resp.json();
            feedback.style.display = 'block';
            feedback.style.background = 'rgba(16, 185, 129, 0.1)';
            feedback.style.color = 'var(--accent-emerald)';
            feedback.style.border = '1px solid rgba(16, 185, 129, 0.25)';
            feedback.textContent = 'Budget created successfully: $' + amount.toFixed(2) + ' ' + period + ' (' + scope + ')';
            document.getElementById('v2BudgetForm').reset();
            if (typeof showToast === 'function') {
                showToast('Budget created successfully', 'success');
            }
        } else {
            var err = await resp.text();
            feedback.style.display = 'block';
            feedback.style.background = 'rgba(244, 63, 94, 0.1)';
            feedback.style.color = 'var(--accent-rose)';
            feedback.style.border = '1px solid rgba(244, 63, 94, 0.25)';
            feedback.textContent = 'Failed to create budget: ' + (err || resp.statusText);
        }
    } catch (e) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(244, 63, 94, 0.1)';
        feedback.style.color = 'var(--accent-rose)';
        feedback.style.border = '1px solid rgba(244, 63, 94, 0.25)';
        feedback.textContent = 'Network error: ' + e.message;
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:16px;height:16px"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Create Budget';
    }

    return false;
}

// ================================================================
// Keyboard Shortcuts
// ================================================================
document.addEventListener('keydown', function(e) {
    // Ctrl/Cmd + S
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        saveAll();
    }
    // Escape
    if (e.key === 'Escape') {
        var wizard = document.getElementById('wizardOverlay');
        var promptFs = document.getElementById('promptFullscreen');
        if (promptFs && promptFs.classList.contains('open')) {
            closePromptFullscreen();
        } else if (wizard && wizard.classList.contains('open')) {
            closeWizard();
        }
    }
});

// Close wizard on overlay click
document.getElementById('wizardOverlay').addEventListener('click', function(e) {
    if (e.target === this) closeWizard();
});

// ================================================================
// Spin animation for save button loading state
// ================================================================
var spinStyle = document.createElement('style');
spinStyle.textContent = '@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }';
document.head.appendChild(spinStyle);

// ================================================================
// Init
// ================================================================
(async function init() {
    const hasProject = await checkProjectOrRedirect();
    if (hasProject) {
        await loadSettings();
    }
})();
