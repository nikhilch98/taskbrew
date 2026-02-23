// ================================================================
// Intelligence Tab: Memory
// ================================================================
var memoryData = [];
var memoryPaginator = null;
var MEMORY_PAGE_SIZE = 20;

function loadMemories() {
    var role = document.getElementById('memoryRoleFilter').value;
    var mtype = document.getElementById('memoryTypeFilter').value;
    var params = new URLSearchParams();
    if (role) params.set('role', role);
    if (mtype) params.set('type', mtype);
    var url = '/api/memories' + (params.toString() ? '?' + params.toString() : '');

    fetch(url)
        .then(function(r) {
            if (r.status === 503) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            memoryData = Array.isArray(data) ? data : [];
            paginateMemories(memoryData);
        })
        .catch(function(err) {
            document.getElementById('memoryTableContainer').innerHTML =
                '<div class="intel-empty">Memory service unavailable</div>';
        });
}

function paginateMemories(items) {
    var container = document.getElementById('memoryTableContainer');
    if (!items.length) {
        container.innerHTML = '<div class="intel-empty">No memories found</div>';
        memoryPaginator = null;
        return;
    }
    memoryPaginator = createPagination('memoryTableContainer', items, MEMORY_PAGE_SIZE, function(pageItems) {
        renderMemoryTable(pageItems);
    });
}

function renderMemoryTable(items) {
    var container = document.getElementById('memoryTableContainer');
    // Remove only the table, preserve pagination controls
    var existingTable = container.querySelector('.intel-table');
    if (existingTable) existingTable.remove();
    var existingEmpty = container.querySelector('.intel-empty');
    if (existingEmpty) existingEmpty.remove();

    if (!items.length) {
        var emptyDiv = document.createElement('div');
        emptyDiv.className = 'intel-empty';
        emptyDiv.textContent = 'No memories found';
        container.insertBefore(emptyDiv, container.firstChild);
        return;
    }
    var html = '<table class="intel-table"><thead><tr>' +
        '<th>Title</th><th>Content</th><th>Type</th><th>Role</th><th>Tags</th><th>Created</th><th>Actions</th>' +
        '</tr></thead><tbody>';
    items.forEach(function(m) {
        var content = escapeHtml(truncate(m.content || '', 100));
        var title = escapeHtml(m.title || '(untitled)');
        var mtype = escapeHtml(m.memory_type || m.type || '');
        var role = escapeHtml(m.agent_role || m.role || '');
        var tags = '';
        var tagList = m.tags;
        if (typeof tagList === 'string') {
            try { tagList = JSON.parse(tagList); } catch(e) { tagList = tagList ? [tagList] : []; }
        }
        if (Array.isArray(tagList)) {
            tags = tagList.map(function(t) { return '<span class="intel-tag">' + escapeHtml(t) + '</span>'; }).join('');
        }
        var created = m.created_at ? timeAgo(m.created_at) : '';
        var mid = m.id || m.memory_id || '';
        html += '<tr>' +
            '<td style="color:var(--text-primary);font-weight:500">' + title + '</td>' +
            '<td>' + content + '</td>' +
            '<td><span class="intel-tag">' + mtype + '</span></td>' +
            '<td>' + role + '</td>' +
            '<td>' + tags + '</td>' +
            '<td style="white-space:nowrap">' + created + '</td>' +
            '<td><button class="intel-btn-sm" onclick="deleteMemory(' + mid + ')">Delete</button></td>' +
            '</tr>';
    });
    html += '</tbody></table>';
    // Insert table before pagination controls
    var paginationEl = container.querySelector('.pagination-controls');
    var tableWrapper = document.createElement('div');
    tableWrapper.innerHTML = html;
    var tableEl = tableWrapper.firstChild;
    if (paginationEl) {
        container.insertBefore(tableEl, paginationEl);
    } else {
        container.insertBefore(tableEl, container.firstChild);
    }
}

function filterMemoryTable() {
    var q = (document.getElementById('memorySearchInput').value || '').toLowerCase();
    if (!q) { paginateMemories(memoryData); return; }
    var filtered = memoryData.filter(function(m) {
        var searchable = ((m.title || '') + ' ' + (m.content || '') + ' ' + JSON.stringify(m.tags || [])).toLowerCase();
        return searchable.indexOf(q) !== -1;
    });
    paginateMemories(filtered);
}

function deleteMemory(id) {
    if (!id) return;
    fetch('/api/memories/' + id, { method: 'DELETE' })
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            showToast('Memory deleted', 'success', 3000);
            loadMemories();
        })
        .catch(function(err) {
            showToast('Failed to delete memory: ' + err.message, 'error');
        });
}

// ================================================================
// Intelligence Tab: Quality
// ================================================================
var qualityPaginator = null;
var QUALITY_PAGE_SIZE = 20;

function loadQualityScores() {
    fetch('/api/quality/scores')
        .then(function(r) {
            if (r.status === 503) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var scores = Array.isArray(data) ? data : [];
            renderQualitySummary(scores);
            paginateQuality(scores);
        })
        .catch(function(err) {
            document.getElementById('qualityTableContainer').innerHTML =
                '<div class="intel-empty">Quality service unavailable</div>';
            document.getElementById('qualitySummaryContainer').innerHTML = '';
        });
}

function renderQualitySummary(scores) {
    var container = document.getElementById('qualitySummaryContainer');
    if (!scores.length) { container.innerHTML = ''; return; }
    // Group by score_type and compute averages
    var groups = {};
    scores.forEach(function(s) {
        var st = s.score_type || 'unknown';
        if (!groups[st]) groups[st] = { total: 0, count: 0 };
        groups[st].total += (s.score || 0);
        groups[st].count += 1;
    });
    var html = '<div class="quality-summary-bar">';
    Object.keys(groups).forEach(function(key) {
        var avg = (groups[key].total / groups[key].count).toFixed(1);
        html += '<div class="quality-summary-item">' +
            '<div class="qs-label">' + escapeHtml(key) + '</div>' +
            '<div class="qs-value">' + avg + '</div>' +
            '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
}

function paginateQuality(scores) {
    var container = document.getElementById('qualityTableContainer');
    if (!scores.length) {
        container.innerHTML = '<div class="intel-empty">No quality scores recorded</div>';
        qualityPaginator = null;
        return;
    }
    qualityPaginator = createPagination('qualityTableContainer', scores, QUALITY_PAGE_SIZE, function(pageItems) {
        renderQualityTable(pageItems);
    });
}

function renderQualityTable(scores) {
    var container = document.getElementById('qualityTableContainer');
    // Remove only the table, preserve pagination controls
    var existingTable = container.querySelector('.intel-table');
    if (existingTable) existingTable.remove();
    var existingEmpty = container.querySelector('.intel-empty');
    if (existingEmpty) existingEmpty.remove();

    if (!scores.length) {
        var emptyDiv = document.createElement('div');
        emptyDiv.className = 'intel-empty';
        emptyDiv.textContent = 'No quality scores recorded';
        container.insertBefore(emptyDiv, container.firstChild);
        return;
    }
    var html = '<table class="intel-table"><thead><tr>' +
        '<th>Task ID</th><th>Agent</th><th>Score Type</th><th>Score</th><th>Created</th>' +
        '</tr></thead><tbody>';
    scores.forEach(function(s) {
        var scoreVal = (s.score !== undefined && s.score !== null) ? Number(s.score).toFixed(1) : '-';
        var scoreColor = 'var(--text-secondary)';
        if (s.score >= 8) scoreColor = 'var(--accent-emerald)';
        else if (s.score >= 5) scoreColor = 'var(--accent-amber)';
        else if (s.score !== undefined && s.score !== null) scoreColor = 'var(--accent-rose)';
        html += '<tr>' +
            '<td style="font-family:monospace;font-size:12px">' + escapeHtml(s.task_id || '') + '</td>' +
            '<td>' + escapeHtml(s.agent_id || s.agent_role || '') + '</td>' +
            '<td><span class="intel-tag">' + escapeHtml(s.score_type || '') + '</span></td>' +
            '<td style="font-weight:700;color:' + scoreColor + '">' + scoreVal + '</td>' +
            '<td style="white-space:nowrap">' + (s.created_at ? timeAgo(s.created_at) : '') + '</td>' +
            '</tr>';
    });
    html += '</tbody></table>';
    // Insert table before pagination controls
    var paginationEl = container.querySelector('.pagination-controls');
    var tableWrapper = document.createElement('div');
    tableWrapper.innerHTML = html;
    var tableEl = tableWrapper.firstChild;
    if (paginationEl) {
        container.insertBefore(tableEl, paginationEl);
    } else {
        container.insertBefore(tableEl, container.firstChild);
    }
}

// ================================================================
// Intelligence Tab: Skills
// ================================================================
function loadSkills() {
    fetch('/api/skills')
        .then(function(r) {
            if (r.status === 503) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var skills = Array.isArray(data) ? data : [];
            renderSkillCards(skills);
        })
        .catch(function(err) {
            document.getElementById('skillsContainer').innerHTML =
                '<div class="intel-empty">Skills service unavailable</div>';
        });
}

function renderSkillCards(skills) {
    var container = document.getElementById('skillsContainer');
    if (!skills.length) {
        container.innerHTML = '<div class="intel-empty">No skill badges earned yet</div>';
        return;
    }
    // Sort by proficiency descending
    skills.sort(function(a, b) { return (b.proficiency || 0) - (a.proficiency || 0); });

    var html = '<div class="skill-cards-grid">';
    skills.forEach(function(s) {
        var role = s.agent_role || s.role || 'unknown';
        var rc = getRoleColor(role);
        var proficiency = Math.min(100, Math.max(0, (s.proficiency || 0)));
        var progressColor = 'var(--accent-indigo)';
        if (proficiency >= 80) progressColor = 'var(--accent-emerald)';
        else if (proficiency >= 50) progressColor = 'var(--accent-amber)';
        else if (proficiency < 30) progressColor = 'var(--accent-rose)';

        html += '<div class="skill-card">' +
            '<div class="skill-card-header">' +
            '<span class="skill-card-role" style="background:' + rc.bg + ';color:' + rc.text + '">' + escapeHtml(role) + '</span>' +
            '<span class="skill-card-type">' + escapeHtml(s.skill_type || s.type || 'General') + '</span>' +
            '</div>' +
            '<div class="skill-progress-bar"><div class="skill-progress-fill" style="width:' + proficiency + '%;background:' + progressColor + '"></div></div>' +
            '<div class="skill-card-stats">' +
            '<span>Proficiency: ' + proficiency + '%</span>' +
            '<span>Tasks: ' + (s.tasks_completed || 0) + '</span>' +
            '<span>Success: ' + ((s.success_rate || 0) * 100).toFixed(0) + '%</span>' +
            '</div>' +
            '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
}

// ================================================================
// Intelligence Tab: Knowledge Graph
// ================================================================
function loadKnowledgeGraph() {
    fetch('/api/knowledge-graph/stats')
        .then(function(r) {
            if (r.status === 503) return null;
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            if (!data) {
                document.getElementById('kgStatsContainer').innerHTML = '';
                document.getElementById('kgNodesContainer').innerHTML =
                    '<div class="intel-empty">Knowledge graph service unavailable</div>';
                return;
            }
            renderKgStats(data);
            renderKgNodes(data);
        })
        .catch(function(err) {
            document.getElementById('kgStatsContainer').innerHTML = '';
            document.getElementById('kgNodesContainer').innerHTML =
                '<div class="intel-empty">Knowledge graph service unavailable</div>';
        });
}

function renderKgStats(data) {
    var container = document.getElementById('kgStatsContainer');
    var nodeCount = data.node_count || data.nodes || 0;
    var edgeCount = data.edge_count || data.edges || 0;
    var typeCount = 0;
    if (data.nodes_by_type) typeCount = Object.keys(data.nodes_by_type).length;
    else if (data.types) typeCount = Object.keys(data.types).length;

    container.innerHTML = '<div class="kg-stats-row">' +
        '<div class="kg-stat-card"><div class="kg-stat-value">' + nodeCount + '</div><div class="kg-stat-label">Nodes</div></div>' +
        '<div class="kg-stat-card"><div class="kg-stat-value">' + edgeCount + '</div><div class="kg-stat-label">Edges</div></div>' +
        '<div class="kg-stat-card"><div class="kg-stat-value">' + typeCount + '</div><div class="kg-stat-label">Types</div></div>' +
        '</div>';
}

function renderKgNodes(data) {
    var container = document.getElementById('kgNodesContainer');
    var nodesByType = data.nodes_by_type || data.types || {};
    if (!Object.keys(nodesByType).length) {
        // Might be a flat list
        if (data.nodes_list) {
            nodesByType = {};
            data.nodes_list.forEach(function(n) {
                var t = n.type || 'other';
                if (!nodesByType[t]) nodesByType[t] = [];
                nodesByType[t].push(n.name || n.id || 'unnamed');
            });
        }
    }

    if (!Object.keys(nodesByType).length) {
        container.innerHTML = '<div class="intel-empty">No nodes in the knowledge graph</div>';
        return;
    }

    var typeIcons = {
        'function': '&#x2699;', 'class': '&#x1F4E6;', 'module': '&#x1F4C1;',
        'file': '&#x1F4C4;', 'variable': '&#x1F4CC;', 'method': '&#x1F527;'
    };

    var html = '';
    Object.keys(nodesByType).sort().forEach(function(typeName) {
        var items = nodesByType[typeName];
        var itemList = Array.isArray(items) ? items : [];
        var icon = typeIcons[typeName] || '&#x25CF;';
        html += '<div class="kg-group">' +
            '<div class="kg-group-header" onclick="this.parentElement.classList.toggle(\'open\')">' +
            '<span class="kg-chevron">&#9654;</span>' +
            '<span>' + icon + ' ' + escapeHtml(typeName) + '</span>' +
            '<span class="kg-group-count">' + itemList.length + '</span>' +
            '</div>' +
            '<div class="kg-group-items">';
        itemList.forEach(function(item) {
            var name = typeof item === 'string' ? item : (item.name || item.id || 'unnamed');
            html += '<div class="kg-node-item" onclick="loadKgDependencies(\'' + escapeHtml(name).replace(/'/g, "\\'") + '\')">' + escapeHtml(name) + '</div>';
        });
        html += '</div></div>';
    });
    container.innerHTML = html;
}

function loadKgDependencies(name) {
    var container = document.getElementById('kgDepsContainer');
    container.innerHTML = '<div class="intel-empty">Loading dependencies for ' + escapeHtml(name) + '...</div>';

    fetch('/api/knowledge-graph/dependencies?name=' + encodeURIComponent(name))
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            renderKgDeps(name, data);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Could not load dependencies</div>';
        });
}

function renderKgDeps(name, data) {
    var container = document.getElementById('kgDepsContainer');
    var deps = Array.isArray(data) ? data : (data.dependencies || data.deps || []);
    if (!deps.length) {
        container.innerHTML = '<div class="kg-deps-panel"><h4>Dependencies: ' + escapeHtml(name) + '</h4>' +
            '<div class="intel-empty" style="padding:16px">No dependencies found</div></div>';
        return;
    }
    var html = '<div class="kg-deps-panel"><h4>Dependencies: ' + escapeHtml(name) + '</h4><ul class="kg-dep-list">';
    deps.forEach(function(d) {
        var depName = typeof d === 'string' ? d : (d.name || d.target || d.id || 'unknown');
        var depType = typeof d === 'object' ? (d.type || d.relation || '') : '';
        var typeColor = 'rgba(99, 102, 241, 0.15)';
        if (depType === 'import' || depType === 'imports') typeColor = 'rgba(6, 182, 212, 0.15)';
        else if (depType === 'calls') typeColor = 'rgba(245, 158, 11, 0.15)';
        else if (depType === 'extends' || depType === 'inherits') typeColor = 'rgba(139, 92, 246, 0.15)';
        html += '<li>';
        if (depType) html += '<span class="kg-dep-type" style="background:' + typeColor + '">' + escapeHtml(depType) + '</span>';
        html += escapeHtml(depName) + '</li>';
    });
    html += '</ul></div>';
    container.innerHTML = html;
}

// ================================================================
// V2 Intelligence: Security View
// ================================================================
function loadSecurityView() {
    fetchSecurityVulnerabilities();
    fetchSecuritySast();
    fetchSecuritySecrets();
    fetchSecurityFlags();
}

function v2SeverityBadge(severity) {
    var s = (severity || 'info').toLowerCase();
    var cls = 'v2-severity-info';
    if (s === 'critical') cls = 'v2-severity-critical';
    else if (s === 'high') cls = 'v2-severity-high';
    else if (s === 'medium') cls = 'v2-severity-medium';
    else if (s === 'low') cls = 'v2-severity-low';
    return '<span class="v2-severity-badge ' + cls + '">' + escapeHtml(severity || 'info') + '</span>';
}

function v2RenderTable(headers, rows) {
    if (!rows.length) return '<div class="intel-empty">No data found</div>';
    var html = '<table class="intel-table"><thead><tr>';
    headers.forEach(function(h) { html += '<th>' + escapeHtml(h) + '</th>'; });
    html += '</tr></thead><tbody>';
    rows.forEach(function(row) {
        html += '<tr>';
        row.forEach(function(cell) { html += '<td>' + cell + '</td>'; });
        html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
}

function fetchSecurityVulnerabilities() {
    var container = document.getElementById('secVulnContainer');
    fetch('/api/v2/security/vulnerabilities')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.vulnerabilities || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No vulnerabilities detected</div>';
                return;
            }
            var rows = items.map(function(v) {
                return [
                    v2SeverityBadge(v.severity),
                    escapeHtml(v.title || v.name || v.id || ''),
                    escapeHtml(v.file || v.location || ''),
                    escapeHtml(truncate(v.description || v.message || '', 80)),
                    v.created_at ? timeAgo(v.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Severity', 'Title', 'File', 'Description', 'Found'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Vulnerability service unavailable</div>';
        });
}

function fetchSecuritySast() {
    var container = document.getElementById('secSastContainer');
    fetch('/api/v2/security/sast')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.findings || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No SAST findings</div>';
                return;
            }
            var rows = items.map(function(f) {
                return [
                    v2SeverityBadge(f.severity),
                    escapeHtml(f.rule || f.rule_id || ''),
                    escapeHtml(f.file || f.location || ''),
                    '<span style="font-family:monospace;font-size:12px">' + escapeHtml(f.line ? 'L' + f.line : '') + '</span>',
                    escapeHtml(truncate(f.message || f.description || '', 80))
                ];
            });
            container.innerHTML = v2RenderTable(['Severity', 'Rule', 'File', 'Line', 'Message'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">SAST service unavailable</div>';
        });
}

function fetchSecuritySecrets() {
    var container = document.getElementById('secSecretsContainer');
    fetch('/api/v2/security/secrets')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.secrets || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No secrets detected</div>';
                return;
            }
            var rows = items.map(function(s) {
                return [
                    v2SeverityBadge(s.severity || 'high'),
                    escapeHtml(s.type || s.secret_type || ''),
                    escapeHtml(s.file || s.location || ''),
                    '<span style="font-family:monospace;font-size:12px">' + escapeHtml(s.line ? 'L' + s.line : '') + '</span>',
                    escapeHtml(s.status || 'detected')
                ];
            });
            container.innerHTML = v2RenderTable(['Severity', 'Type', 'File', 'Line', 'Status'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Secrets service unavailable</div>';
        });
}

function fetchSecurityFlags() {
    var container = document.getElementById('secFlagsContainer');
    fetch('/api/v2/security/flags')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.flags || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No security flags</div>';
                return;
            }
            var rows = items.map(function(f) {
                return [
                    v2SeverityBadge(f.severity || 'medium'),
                    escapeHtml(f.flag || f.name || f.type || ''),
                    escapeHtml(truncate(f.description || f.message || '', 100)),
                    escapeHtml(f.task_id || ''),
                    f.created_at ? timeAgo(f.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Severity', 'Flag', 'Description', 'Task', 'Flagged'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Security flags service unavailable</div>';
        });
}

// ================================================================
// V2 Intelligence: Observability View
// ================================================================
function loadObservabilityView() {
    fetchObsDecisions();
    fetchObsCostByAgent();
    fetchObsCostByFeature();
    fetchObsBottlenecks();
    fetchObsAnomalies();
}

function fetchObsDecisions() {
    var container = document.getElementById('obsDecisionsContainer');
    fetch('/api/v2/observability/decisions')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.decisions || []);
            document.getElementById('obsDecisionCount').textContent = items.length;
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No decisions recorded</div>';
                return;
            }
            var rows = items.map(function(d) {
                return [
                    escapeHtml(d.agent || d.agent_role || ''),
                    escapeHtml(d.decision_type || d.type || ''),
                    escapeHtml(truncate(d.rationale || d.description || '', 100)),
                    escapeHtml(d.task_id || ''),
                    d.created_at ? timeAgo(d.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Agent', 'Type', 'Rationale', 'Task', 'Time'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Decisions service unavailable</div>';
            document.getElementById('obsDecisionCount').textContent = '--';
        });
}

function fetchObsCostByAgent() {
    var container = document.getElementById('obsCostAgentContainer');
    fetch('/api/v2/observability/costs/by-agent')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.costs || data.agents || []);
            var totalCost = 0;
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No cost data available</div>';
                document.getElementById('obsTotalCost').textContent = '$0.00';
                return;
            }
            var rows = items.map(function(c) {
                var cost = c.total_cost || c.cost || 0;
                totalCost += cost;
                var barWidth = Math.min(100, (cost / (items[0].total_cost || items[0].cost || 1)) * 100);
                return [
                    escapeHtml(c.agent || c.agent_role || c.name || ''),
                    '$' + Number(cost).toFixed(4),
                    '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + barWidth + '%;background:var(--accent-indigo)"></div></div>',
                    escapeHtml(c.requests || c.call_count || '')
                ];
            });
            document.getElementById('obsTotalCost').textContent = '$' + totalCost.toFixed(4);
            container.innerHTML = v2RenderTable(['Agent', 'Cost', 'Relative', 'Requests'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Cost service unavailable</div>';
            document.getElementById('obsTotalCost').textContent = '--';
        });
}

function fetchObsCostByFeature() {
    var container = document.getElementById('obsCostFeatureContainer');
    fetch('/api/v2/observability/costs/by-feature')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.costs || data.features || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No feature cost data</div>';
                return;
            }
            var rows = items.map(function(c) {
                return [
                    escapeHtml(c.feature || c.group_id || c.name || ''),
                    '$' + Number(c.total_cost || c.cost || 0).toFixed(4),
                    escapeHtml(c.task_count || c.tasks || ''),
                    escapeHtml(c.agent_count || c.agents || '')
                ];
            });
            container.innerHTML = v2RenderTable(['Feature', 'Cost', 'Tasks', 'Agents'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Feature cost service unavailable</div>';
        });
}

function fetchObsBottlenecks() {
    var container = document.getElementById('obsBottlenecksContainer');
    fetch('/api/v2/observability/bottlenecks')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.bottlenecks || []);
            document.getElementById('obsBottleneckCount').textContent = items.length;
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No bottlenecks detected</div>';
                return;
            }
            var rows = items.map(function(b) {
                return [
                    v2SeverityBadge(b.severity || 'medium'),
                    escapeHtml(b.type || b.bottleneck_type || ''),
                    escapeHtml(truncate(b.description || b.message || '', 100)),
                    escapeHtml(b.agent || b.task_id || ''),
                    b.detected_at ? timeAgo(b.detected_at) : (b.created_at ? timeAgo(b.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Severity', 'Type', 'Description', 'Context', 'Detected'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Bottleneck service unavailable</div>';
            document.getElementById('obsBottleneckCount').textContent = '--';
        });
}

function fetchObsAnomalies() {
    var container = document.getElementById('obsAnomaliesContainer');
    fetch('/api/v2/observability/anomalies')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.anomalies || []);
            document.getElementById('obsAnomalyCount').textContent = items.length;
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No anomalies detected</div>';
                return;
            }
            var rows = items.map(function(a) {
                return [
                    v2SeverityBadge(a.severity || 'medium'),
                    escapeHtml(a.type || a.anomaly_type || ''),
                    escapeHtml(truncate(a.description || a.message || '', 100)),
                    escapeHtml(a.metric || a.agent || ''),
                    a.detected_at ? timeAgo(a.detected_at) : (a.created_at ? timeAgo(a.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Severity', 'Type', 'Description', 'Metric', 'Detected'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Anomaly service unavailable</div>';
            document.getElementById('obsAnomalyCount').textContent = '--';
        });
}

// ================================================================
// V2 Intelligence: Code Intel View
// ================================================================
function loadCodeIntelView() {
    fetchCiPatterns();
    fetchCiDebt();
    fetchCiTestGaps();
}

function fetchCiPatterns() {
    var container = document.getElementById('ciPatternsContainer');
    fetch('/api/v2/code-intel/patterns')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.patterns || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No architecture patterns detected</div>';
                return;
            }
            var rows = items.map(function(p) {
                return [
                    escapeHtml(p.pattern || p.name || ''),
                    escapeHtml(p.category || p.type || ''),
                    escapeHtml(truncate(p.description || '', 100)),
                    escapeHtml(p.file || p.location || ''),
                    '<span style="font-weight:700;color:var(--text-primary)">' + (p.confidence ? (Number(p.confidence) * 100).toFixed(0) + '%' : (p.count || '')) + '</span>'
                ];
            });
            container.innerHTML = v2RenderTable(['Pattern', 'Category', 'Description', 'Location', 'Confidence'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Patterns service unavailable</div>';
        });
}

function fetchCiDebt() {
    var container = document.getElementById('ciDebtContainer');
    fetch('/api/v2/code-intel/debt?limit=20')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.debt || data.items || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No tech debt reported</div>';
                return;
            }
            var rows = items.map(function(d) {
                var score = d.score || d.debt_score || 0;
                var scoreColor = 'var(--accent-emerald)';
                if (score >= 8) scoreColor = 'var(--accent-rose)';
                else if (score >= 5) scoreColor = 'var(--accent-amber)';
                var barWidth = Math.min(100, score * 10);
                return [
                    escapeHtml(d.file || d.location || d.name || ''),
                    escapeHtml(d.category || d.type || ''),
                    '<span style="font-weight:700;color:' + scoreColor + '">' + Number(score).toFixed(1) + '</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + barWidth + '%;background:' + scoreColor + '"></div></div>',
                    escapeHtml(truncate(d.description || d.reason || '', 80)),
                    escapeHtml(d.suggestion || d.fix || '')
                ];
            });
            container.innerHTML = v2RenderTable(['File', 'Category', 'Score', 'Description', 'Suggestion'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Tech debt service unavailable</div>';
        });
}

function fetchCiTestGaps() {
    var container = document.getElementById('ciTestGapsContainer');
    fetch('/api/v2/code-intel/test-gaps')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.gaps || data.functions || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No test gaps found</div>';
                return;
            }
            var rows = items.map(function(g) {
                var riskColor = 'var(--text-secondary)';
                var risk = (g.risk || g.priority || '').toLowerCase();
                if (risk === 'high' || risk === 'critical') riskColor = 'var(--accent-rose)';
                else if (risk === 'medium') riskColor = 'var(--accent-amber)';
                else if (risk === 'low') riskColor = 'var(--accent-emerald)';
                return [
                    escapeHtml(g.function_name || g.name || g.symbol || ''),
                    escapeHtml(g.file || g.location || ''),
                    '<span style="font-family:monospace;font-size:12px">' + escapeHtml(g.line ? 'L' + g.line : '') + '</span>',
                    '<span style="font-weight:600;color:' + riskColor + '">' + escapeHtml(g.risk || g.priority || 'unknown') + '</span>',
                    escapeHtml(truncate(g.reason || g.description || '', 80))
                ];
            });
            container.innerHTML = v2RenderTable(['Function', 'File', 'Line', 'Risk', 'Reason'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Test gaps service unavailable</div>';
        });
}

// ================================================================
// V2 Intelligence: Planning View
// ================================================================
function loadPlanningView() {
    fetchPlanPostMortems();
    fetchPlanScopeFlags();
    fetchPlanStandups();
    fetchPlanHeartbeats();
}

function fetchPlanPostMortems() {
    var container = document.getElementById('planPostMortemsContainer');
    fetch('/api/v2/planning/post-mortems')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.post_mortems || data.reports || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No post-mortem reports</div>';
                return;
            }
            var html = '';
            items.forEach(function(pm, i) {
                var title = pm.title || pm.task_id || ('Post-Mortem #' + (i + 1));
                var summary = pm.summary || pm.description || '';
                var rootCause = pm.root_cause || '';
                var lessons = pm.lessons_learned || pm.lessons || [];
                if (typeof lessons === 'string') lessons = [lessons];
                html += '<div class="v2-accordion">' +
                    '<button class="v2-accordion-header" onclick="this.parentElement.classList.toggle(\'open\')">' +
                    '<span>' + escapeHtml(title) + (pm.created_at ? ' <span style=\'color:var(--text-muted);font-size:12px;font-weight:400\'>' + timeAgo(pm.created_at) + '</span>' : '') + '</span>' +
                    '<span class="v2-accordion-chevron">&#9654;</span>' +
                    '</button>' +
                    '<div class="v2-accordion-body">';
                if (summary) html += '<p><strong>Summary:</strong> ' + escapeHtml(summary) + '</p>';
                if (rootCause) html += '<p><strong>Root Cause:</strong> ' + escapeHtml(rootCause) + '</p>';
                if (lessons.length) {
                    html += '<p><strong>Lessons Learned:</strong></p><ul>';
                    lessons.forEach(function(l) { html += '<li>' + escapeHtml(typeof l === 'string' ? l : (l.lesson || l.description || '')) + '</li>'; });
                    html += '</ul>';
                }
                html += '</div></div>';
            });
            container.innerHTML = html;
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Post-mortem service unavailable</div>';
        });
}

function fetchPlanScopeFlags() {
    var container = document.getElementById('planScopeFlagsContainer');
    fetch('/api/v2/planning/scope-flags')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.flags || data.scope_flags || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No scope creep flags</div>';
                return;
            }
            var rows = items.map(function(f) {
                return [
                    v2SeverityBadge(f.severity || 'medium'),
                    escapeHtml(f.task_id || ''),
                    escapeHtml(f.flag_type || f.type || ''),
                    escapeHtml(truncate(f.description || f.message || '', 100)),
                    f.created_at ? timeAgo(f.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Severity', 'Task', 'Type', 'Description', 'Flagged'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Scope flags service unavailable</div>';
        });
}

function fetchPlanStandups() {
    var container = document.getElementById('planStandupsContainer');
    fetch('/api/v2/coordination/standups')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.standups || data.reports || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No standup reports</div>';
                return;
            }
            var html = '';
            items.forEach(function(s) {
                var role = s.agent || s.agent_role || s.role || '';
                var rc = getRoleColor(role);
                html += '<div class="v2-standup-item">' +
                    '<div class="v2-standup-role"><span style="background:' + rc.bg + ';color:' + rc.text + ';padding:2px 8px;border-radius:10px;font-size:11px">' + escapeHtml(role) + '</span></div>' +
                    '<div class="v2-standup-text">';
                if (s.completed) html += '<strong>Done:</strong> ' + escapeHtml(typeof s.completed === 'string' ? s.completed : JSON.stringify(s.completed)) + '<br>';
                if (s.in_progress || s.working_on) html += '<strong>Doing:</strong> ' + escapeHtml(typeof (s.in_progress || s.working_on) === 'string' ? (s.in_progress || s.working_on) : JSON.stringify(s.in_progress || s.working_on)) + '<br>';
                if (s.blocked || s.blockers) html += '<strong>Blocked:</strong> ' + escapeHtml(typeof (s.blocked || s.blockers) === 'string' ? (s.blocked || s.blockers) : JSON.stringify(s.blocked || s.blockers)) + '<br>';
                if (s.summary) html += escapeHtml(s.summary);
                html += '</div>';
                if (s.created_at) html += '<div class="v2-standup-time">' + timeAgo(s.created_at) + '</div>';
                html += '</div>';
            });
            container.innerHTML = html;
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Standup service unavailable</div>';
        });
}

function fetchPlanHeartbeats() {
    var container = document.getElementById('planHeartbeatsContainer');
    fetch('/api/v2/coordination/heartbeats?task_id=latest')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.heartbeats || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No heartbeats received</div>';
                return;
            }
            var rows = items.map(function(h) {
                var progress = h.progress || h.percent || 0;
                var barColor = 'var(--accent-indigo)';
                if (progress >= 80) barColor = 'var(--accent-emerald)';
                else if (progress >= 50) barColor = 'var(--accent-amber)';
                return [
                    escapeHtml(h.agent || h.agent_role || ''),
                    escapeHtml(h.task_id || ''),
                    escapeHtml(h.status || h.state || ''),
                    '<span style="font-weight:600">' + progress + '%</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + Math.min(100, progress) + '%;background:' + barColor + '"></div></div>',
                    h.updated_at ? timeAgo(h.updated_at) : (h.created_at ? timeAgo(h.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Agent', 'Task', 'Status', 'Progress', 'Updated'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Heartbeat service unavailable</div>';
        });
}

// ================================================================
// V2 Intelligence: Autonomous View
// ================================================================
function loadAutonomousView() {
    fetchAutoDiscoveries();
    fetchAutoDecompositions();
    fetchAutoBids();
    fetchAutoRetryStrategies();
}

function fetchAutoDiscoveries() {
    var container = document.getElementById('autoDiscoveriesContainer');
    fetch('/api/v2/autonomous/discoveries')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.discoveries || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No discoveries found</div>';
                return;
            }
            var rows = items.map(function(d) {
                return [
                    escapeHtml(d.type || d.discovery_type || ''),
                    escapeHtml(truncate(d.title || d.name || '', 80)),
                    escapeHtml(truncate(d.description || d.summary || '', 100)),
                    escapeHtml(d.agent || d.discovered_by || ''),
                    d.created_at ? timeAgo(d.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Type', 'Title', 'Description', 'Agent', 'Found'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Discoveries service unavailable</div>';
        });
}

function fetchAutoDecompositions() {
    var container = document.getElementById('autoDecompositionsContainer');
    fetch('/api/v2/autonomous/decompositions')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.decompositions || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No task decompositions</div>';
                return;
            }
            var rows = items.map(function(d) {
                var subtaskCount = d.subtasks ? (Array.isArray(d.subtasks) ? d.subtasks.length : d.subtasks) : (d.subtask_count || 0);
                return [
                    escapeHtml(d.task_id || d.parent_task || ''),
                    escapeHtml(truncate(d.title || d.task_title || '', 80)),
                    '<span style="font-weight:700;color:var(--accent-indigo)">' + subtaskCount + '</span>',
                    escapeHtml(d.strategy || d.method || ''),
                    escapeHtml(d.status || d.state || ''),
                    d.created_at ? timeAgo(d.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Task', 'Title', 'Subtasks', 'Strategy', 'Status', 'Created'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Decompositions service unavailable</div>';
        });
}

function fetchAutoBids() {
    var container = document.getElementById('autoBidsContainer');
    fetch('/api/v2/autonomous/bids?task_id=')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.bids || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No active bids</div>';
                return;
            }
            var rows = items.map(function(b) {
                var confidence = b.confidence || b.score || 0;
                var barWidth = Math.min(100, confidence * 100);
                return [
                    escapeHtml(b.agent || b.bidder || ''),
                    escapeHtml(b.task_id || ''),
                    '<span style="font-weight:600">' + (confidence * 100).toFixed(0) + '%</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + barWidth + '%;background:var(--accent-indigo)"></div></div>',
                    escapeHtml(truncate(b.rationale || b.reason || '', 80)),
                    escapeHtml(b.status || 'pending')
                ];
            });
            container.innerHTML = v2RenderTable(['Agent', 'Task', 'Confidence', 'Rationale', 'Status'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Bids service unavailable</div>';
        });
}

function fetchAutoRetryStrategies() {
    var container = document.getElementById('autoRetryContainer');
    fetch('/api/v2/autonomous/retry-strategies')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.strategies || data.retries || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No retry strategies</div>';
                return;
            }
            var rows = items.map(function(r) {
                return [
                    escapeHtml(r.task_id || ''),
                    escapeHtml(r.strategy || r.type || ''),
                    '<span style="font-weight:700">' + escapeHtml(String(r.attempt || r.retry_count || 0)) + '/' + escapeHtml(String(r.max_retries || r.max_attempts || '-')) + '</span>',
                    escapeHtml(r.status || r.state || ''),
                    escapeHtml(truncate(r.reason || r.last_error || '', 80)),
                    r.next_retry_at ? timeAgo(r.next_retry_at) : (r.updated_at ? timeAgo(r.updated_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Task', 'Strategy', 'Attempts', 'Status', 'Reason', 'Next Retry'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Retry strategies service unavailable</div>';
        });
}

// ================================================================
// V2 Intelligence: Coordination View
// ================================================================
function loadCoordinationView() {
    fetchCoordStandups();
    fetchCoordLocks();
    fetchCoordProposals();
    fetchCoordHeartbeats();
    fetchCoordPairs();
}

function fetchCoordStandups() {
    var container = document.getElementById('coordStandupsContainer');
    fetch('/api/v2/coordination/standups')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.standups || data.reports || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No standup reports</div>';
                return;
            }
            var html = '';
            items.forEach(function(s) {
                var role = s.agent || s.agent_role || s.role || '';
                var rc = getRoleColor(role);
                html += '<div class="v2-standup-item">' +
                    '<div class="v2-standup-role"><span style="background:' + rc.bg + ';color:' + rc.text + ';padding:2px 8px;border-radius:10px;font-size:11px">' + escapeHtml(role) + '</span></div>' +
                    '<div class="v2-standup-text">';
                if (s.completed) html += '<strong>Done:</strong> ' + escapeHtml(typeof s.completed === 'string' ? s.completed : JSON.stringify(s.completed)) + '<br>';
                if (s.in_progress || s.working_on) html += '<strong>Doing:</strong> ' + escapeHtml(typeof (s.in_progress || s.working_on) === 'string' ? (s.in_progress || s.working_on) : JSON.stringify(s.in_progress || s.working_on)) + '<br>';
                if (s.blocked || s.blockers) html += '<strong>Blocked:</strong> ' + escapeHtml(typeof (s.blocked || s.blockers) === 'string' ? (s.blocked || s.blockers) : JSON.stringify(s.blocked || s.blockers)) + '<br>';
                if (s.summary) html += escapeHtml(s.summary);
                html += '</div>';
                if (s.created_at) html += '<div class="v2-standup-time">' + timeAgo(s.created_at) + '</div>';
                html += '</div>';
            });
            container.innerHTML = html;
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Standup service unavailable</div>';
        });
}

function fetchCoordLocks() {
    var container = document.getElementById('coordLocksContainer');
    fetch('/api/v2/coordination/locks')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.locks || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No active file locks</div>';
                return;
            }
            var rows = items.map(function(l) {
                return [
                    escapeHtml(l.file || l.path || l.resource || ''),
                    escapeHtml(l.agent || l.locked_by || ''),
                    escapeHtml(l.lock_type || l.type || 'exclusive'),
                    escapeHtml(l.task_id || ''),
                    l.acquired_at ? timeAgo(l.acquired_at) : (l.created_at ? timeAgo(l.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['File', 'Locked By', 'Type', 'Task', 'Acquired'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Locks service unavailable</div>';
        });
}

function fetchCoordProposals() {
    var container = document.getElementById('coordProposalsContainer');
    fetch('/api/v2/coordination/proposals')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.proposals || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No consensus proposals</div>';
                return;
            }
            var rows = items.map(function(p) {
                var votesFor = p.votes_for || p.yes_votes || 0;
                var votesAgainst = p.votes_against || p.no_votes || 0;
                var totalVotes = votesFor + votesAgainst;
                var pct = totalVotes > 0 ? ((votesFor / totalVotes) * 100).toFixed(0) : 0;
                return [
                    escapeHtml(truncate(p.title || p.proposal || p.description || '', 80)),
                    escapeHtml(p.proposed_by || p.author || ''),
                    '<span style="color:var(--accent-emerald);font-weight:600">' + votesFor + '</span> / <span style="color:var(--accent-rose);font-weight:600">' + votesAgainst + '</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + pct + '%;background:var(--accent-emerald)"></div></div>',
                    escapeHtml(p.status || 'open'),
                    p.created_at ? timeAgo(p.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Proposal', 'Author', 'Votes (For/Against)', 'Status', 'Created'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Proposals service unavailable</div>';
        });
}

function fetchCoordHeartbeats() {
    var container = document.getElementById('coordHeartbeatsContainer');
    fetch('/api/v2/coordination/heartbeats')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.heartbeats || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No heartbeats received</div>';
                return;
            }
            var rows = items.map(function(h) {
                var progress = h.progress || h.percent || 0;
                var barColor = 'var(--accent-indigo)';
                if (progress >= 80) barColor = 'var(--accent-emerald)';
                else if (progress >= 50) barColor = 'var(--accent-amber)';
                return [
                    escapeHtml(h.agent || h.agent_role || ''),
                    escapeHtml(h.task_id || ''),
                    escapeHtml(h.status || h.state || ''),
                    '<span style="font-weight:600">' + progress + '%</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + Math.min(100, progress) + '%;background:' + barColor + '"></div></div>',
                    h.updated_at ? timeAgo(h.updated_at) : (h.created_at ? timeAgo(h.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Agent', 'Task', 'Status', 'Progress', 'Updated'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Heartbeat service unavailable</div>';
        });
}

function fetchCoordPairs() {
    var container = document.getElementById('coordPairsContainer');
    fetch('/api/v2/coordination/pairs')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.pairs || data.mentors || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No mentor pairs</div>';
                return;
            }
            var rows = items.map(function(p) {
                return [
                    escapeHtml(p.mentor || p.mentor_agent || ''),
                    escapeHtml(p.mentee || p.mentee_agent || ''),
                    escapeHtml(p.skill || p.focus_area || p.topic || ''),
                    escapeHtml(p.status || 'active'),
                    p.created_at ? timeAgo(p.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Mentor', 'Mentee', 'Focus Area', 'Status', 'Paired'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Mentor pairs service unavailable</div>';
        });
}

// ================================================================
// V2 Intelligence: Learning View
// ================================================================
function loadLearningView() {
    fetchLearnExperiments();
    fetchLearnBenchmarks();
    fetchLearnConventions();
    fetchLearnErrorClusters();
    fetchLearnCrossProject();
}

function fetchLearnExperiments() {
    var container = document.getElementById('learnExperimentsContainer');
    fetch('/api/v2/learning/experiments')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.experiments || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No active experiments</div>';
                return;
            }
            var rows = items.map(function(e) {
                return [
                    escapeHtml(e.name || e.title || e.experiment_id || ''),
                    escapeHtml(e.hypothesis || e.description || ''),
                    escapeHtml(e.status || e.state || 'running'),
                    escapeHtml(e.variant || e.treatment || ''),
                    e.started_at ? timeAgo(e.started_at) : (e.created_at ? timeAgo(e.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Experiment', 'Hypothesis', 'Status', 'Variant', 'Started'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Experiments service unavailable</div>';
        });
}

function fetchLearnBenchmarks() {
    var container = document.getElementById('learnBenchmarksContainer');
    fetch('/api/v2/learning/benchmarks')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.benchmarks || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No agent benchmarks</div>';
                return;
            }
            var rows = items.map(function(b) {
                var score = b.score || b.benchmark_score || 0;
                var barWidth = Math.min(100, score);
                var barColor = score >= 80 ? 'var(--accent-emerald)' : (score >= 50 ? 'var(--accent-amber)' : 'var(--accent-rose)');
                return [
                    escapeHtml(b.agent || b.agent_role || ''),
                    escapeHtml(b.metric || b.benchmark_name || b.category || ''),
                    '<span style="font-weight:700;color:' + barColor + '">' + Number(score).toFixed(1) + '</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + barWidth + '%;background:' + barColor + '"></div></div>',
                    escapeHtml(b.baseline || b.previous || ''),
                    b.measured_at ? timeAgo(b.measured_at) : (b.created_at ? timeAgo(b.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Agent', 'Metric', 'Score', 'Baseline', 'Measured'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Benchmarks service unavailable</div>';
        });
}

function fetchLearnConventions() {
    var container = document.getElementById('learnConventionsContainer');
    fetch('/api/v2/learning/conventions')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.conventions || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No codebase conventions detected</div>';
                return;
            }
            var rows = items.map(function(c) {
                var confidence = c.confidence || c.score || 0;
                var pct = (confidence * 100).toFixed(0);
                return [
                    escapeHtml(c.convention || c.name || c.rule || ''),
                    escapeHtml(c.category || c.type || ''),
                    escapeHtml(truncate(c.description || c.example || '', 100)),
                    '<span style="font-weight:600">' + pct + '%</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + pct + '%;background:var(--accent-indigo)"></div></div>',
                    escapeHtml(String(c.violations || c.exceptions || 0))
                ];
            });
            container.innerHTML = v2RenderTable(['Convention', 'Category', 'Description', 'Confidence', 'Violations'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Conventions service unavailable</div>';
        });
}

function fetchLearnErrorClusters() {
    var container = document.getElementById('learnErrorClustersContainer');
    fetch('/api/v2/learning/error-clusters')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.clusters || data.errors || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No error clusters found</div>';
                return;
            }
            var rows = items.map(function(c) {
                var count = c.count || c.occurrences || c.frequency || 0;
                return [
                    escapeHtml(c.cluster_id || c.name || c.pattern || ''),
                    escapeHtml(truncate(c.message || c.description || c.error_type || '', 100)),
                    '<span style="font-weight:700;color:var(--accent-rose)">' + count + '</span>',
                    escapeHtml(c.agent || c.affected_agents || ''),
                    escapeHtml(truncate(c.suggested_fix || c.resolution || '', 80)),
                    c.last_seen ? timeAgo(c.last_seen) : (c.created_at ? timeAgo(c.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Cluster', 'Message', 'Count', 'Agent', 'Fix', 'Last Seen'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Error clusters service unavailable</div>';
        });
}

function fetchLearnCrossProject() {
    var container = document.getElementById('learnCrossProjectContainer');
    fetch('/api/v2/learning/cross-project')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.knowledge || data.insights || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No cross-project knowledge</div>';
                return;
            }
            var rows = items.map(function(k) {
                return [
                    escapeHtml(k.source_project || k.project || ''),
                    escapeHtml(k.type || k.knowledge_type || k.category || ''),
                    escapeHtml(truncate(k.insight || k.description || k.lesson || '', 100)),
                    escapeHtml(k.applicability || k.relevance || ''),
                    k.created_at ? timeAgo(k.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Source Project', 'Type', 'Insight', 'Applicability', 'Discovered'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Cross-project service unavailable</div>';
        });
}

// ================================================================
// V2 Intelligence: Testing Quality View
// ================================================================
function loadTestingView() {
    fetchTestMutations();
    fetchTestRegressions();
    fetchTestChecklists();
    fetchTestDocDrift();
    fetchTestPerfBaselines();
}

function fetchTestMutations() {
    var container = document.getElementById('testMutationsContainer');
    fetch('/api/v2/testing/mutations')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.mutations || data.results || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No mutation analysis results</div>';
                return;
            }
            var rows = items.map(function(m) {
                var killed = m.killed || m.mutations_killed || 0;
                var total = m.total || m.mutations_total || 1;
                var score = total > 0 ? ((killed / total) * 100).toFixed(1) : 0;
                var barColor = score >= 80 ? 'var(--accent-emerald)' : (score >= 50 ? 'var(--accent-amber)' : 'var(--accent-rose)');
                return [
                    escapeHtml(m.file || m.module || m.target || ''),
                    '<span style="font-weight:700;color:' + barColor + '">' + score + '%</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + Math.min(100, score) + '%;background:' + barColor + '"></div></div>',
                    escapeHtml(killed + '/' + total),
                    escapeHtml(String(m.survived || m.mutations_survived || (total - killed))),
                    m.run_at ? timeAgo(m.run_at) : (m.created_at ? timeAgo(m.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['File', 'Score', 'Killed/Total', 'Survived', 'Run'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Mutation analysis service unavailable</div>';
        });
}

function fetchTestRegressions() {
    var container = document.getElementById('testRegressionsContainer');
    fetch('/api/v2/testing/regressions')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.regressions || data.predictions || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No regression predictions</div>';
                return;
            }
            var rows = items.map(function(r) {
                var risk = (r.risk || r.severity || 'medium').toLowerCase();
                var riskColor = risk === 'high' || risk === 'critical' ? 'var(--accent-rose)' : (risk === 'medium' ? 'var(--accent-amber)' : 'var(--accent-emerald)');
                return [
                    '<span style="font-weight:600;color:' + riskColor + '">' + escapeHtml(risk) + '</span>',
                    escapeHtml(r.file || r.module || r.area || ''),
                    escapeHtml(truncate(r.prediction || r.description || r.reason || '', 100)),
                    escapeHtml(r.change || r.commit || r.trigger || ''),
                    r.predicted_at ? timeAgo(r.predicted_at) : (r.created_at ? timeAgo(r.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Risk', 'File', 'Prediction', 'Trigger', 'Predicted'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Regressions service unavailable</div>';
        });
}

function fetchTestChecklists() {
    var container = document.getElementById('testChecklistsContainer');
    fetch('/api/v2/testing/checklists')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.checklists || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No review checklists</div>';
                return;
            }
            var html = '';
            items.forEach(function(cl, i) {
                var title = cl.title || cl.name || ('Checklist #' + (i + 1));
                var checkItems = cl.items || cl.checks || [];
                if (typeof checkItems === 'string') checkItems = [checkItems];
                var completedCount = checkItems.filter(function(c) { return c.completed || c.passed || c.checked; }).length;
                var totalCount = checkItems.length;
                html += '<div class="v2-accordion">' +
                    '<button class="v2-accordion-header" onclick="this.parentElement.classList.toggle(\'open\')">' +
                    '<span>' + escapeHtml(title) + ' <span style="color:var(--text-muted);font-size:12px;font-weight:400">' + completedCount + '/' + totalCount + ' passed</span></span>' +
                    '<span class="v2-accordion-chevron">&#9654;</span>' +
                    '</button>' +
                    '<div class="v2-accordion-body"><ul>';
                checkItems.forEach(function(c) {
                    var label = typeof c === 'string' ? c : (c.label || c.name || c.description || '');
                    var passed = typeof c === 'object' ? (c.completed || c.passed || c.checked) : false;
                    var icon = passed ? '<span style="color:var(--accent-emerald)">&#10003;</span>' : '<span style="color:var(--accent-rose)">&#10007;</span>';
                    html += '<li>' + icon + ' ' + escapeHtml(label) + '</li>';
                });
                html += '</ul></div></div>';
            });
            container.innerHTML = html;
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Checklists service unavailable</div>';
        });
}

function fetchTestDocDrift() {
    var container = document.getElementById('testDocDriftContainer');
    fetch('/api/v2/testing/doc-drift')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.drift || data.reports || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No doc drift detected</div>';
                return;
            }
            var rows = items.map(function(d) {
                var drift = d.drift_score || d.score || d.severity || 0;
                var driftColor = drift >= 0.7 ? 'var(--accent-rose)' : (drift >= 0.4 ? 'var(--accent-amber)' : 'var(--accent-emerald)');
                var driftPct = typeof drift === 'number' ? (drift * 100).toFixed(0) : drift;
                return [
                    escapeHtml(d.doc_file || d.file || d.document || ''),
                    escapeHtml(d.code_file || d.related_code || d.source || ''),
                    '<span style="font-weight:700;color:' + driftColor + '">' + driftPct + (typeof drift === 'number' ? '%' : '') + '</span>',
                    escapeHtml(truncate(d.description || d.reason || d.diff_summary || '', 80)),
                    d.detected_at ? timeAgo(d.detected_at) : (d.created_at ? timeAgo(d.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Document', 'Code File', 'Drift', 'Description', 'Detected'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Doc drift service unavailable</div>';
        });
}

function fetchTestPerfBaselines() {
    var container = document.getElementById('testPerfBaselinesContainer');
    fetch('/api/v2/testing/perf-baselines')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.baselines || data.benchmarks || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No performance baselines</div>';
                return;
            }
            var rows = items.map(function(b) {
                var current = b.current || b.value || 0;
                var baseline = b.baseline || b.previous || b.target || 0;
                var diff = baseline > 0 ? (((current - baseline) / baseline) * 100).toFixed(1) : 0;
                var diffColor = diff > 10 ? 'var(--accent-rose)' : (diff > 0 ? 'var(--accent-amber)' : 'var(--accent-emerald)');
                return [
                    escapeHtml(b.metric || b.name || b.test || ''),
                    escapeHtml(b.unit || ''),
                    '<span style="font-weight:600">' + escapeHtml(String(current)) + '</span>',
                    escapeHtml(String(baseline)),
                    '<span style="font-weight:600;color:' + diffColor + '">' + (diff > 0 ? '+' : '') + diff + '%</span>',
                    b.measured_at ? timeAgo(b.measured_at) : (b.created_at ? timeAgo(b.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Metric', 'Unit', 'Current', 'Baseline', 'Change', 'Measured'], rows);
        })
        .catch(function(err) {
            container.innerHTML = '<div class="intel-empty">Performance baselines service unavailable</div>';
        });
}

