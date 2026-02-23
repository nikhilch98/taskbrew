// ================================================================
// Data Export (CSV)
// ================================================================
function exportToCsv(filename, headers, rows) {
    var csvContent = headers.map(function(h) { return '"' + String(h).replace(/"/g, '""') + '"'; }).join(',') + '\n';
    rows.forEach(function(row) {
        csvContent += row.map(function(cell) {
            var val = String(cell || '').replace(/<[^>]*>/g, '').replace(/"/g, '""');
            return '"' + val + '"';
        }).join(',') + '\n';
    });
    var blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', filename);
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

function exportBoardData() {
    var headers = ['ID', 'Title', 'Status', 'Assigned To', 'Priority', 'Group', 'Created At'];
    var rows = allTasks.map(function(t) {
        return [
            t.id || '',
            t.title || '',
            t._status || t.status || '',
            t.assigned_to || '',
            t.priority || '',
            t.group_id || '',
            t.created_at || ''
        ];
    });
    exportToCsv('board-tasks-' + new Date().toISOString().slice(0, 10) + '.csv', headers, rows);
    showToast('Board data exported (' + rows.length + ' tasks)', 'success', 3000);
}

function exportQualityData() {
    fetch('/api/quality-scores')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.scores || []);
            var headers = ['Task ID', 'Agent', 'Score', 'Category', 'Details', 'Created At'];
            var rows = items.map(function(s) {
                return [
                    s.task_id || '',
                    s.agent || s.agent_role || '',
                    s.score || s.quality_score || '',
                    s.category || s.type || '',
                    s.details || s.description || '',
                    s.created_at || ''
                ];
            });
            exportToCsv('quality-scores-' + new Date().toISOString().slice(0, 10) + '.csv', headers, rows);
            showToast('Quality data exported (' + rows.length + ' scores)', 'success', 3000);
        })
        .catch(function(err) { showToast('Failed to export quality data: ' + err.message); });
}

function exportMemoryData() {
    fetch('/api/memories')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var items = Array.isArray(data) ? data : [];
            var headers = ['ID', 'Role', 'Type', 'Key', 'Value', 'Created At'];
            var rows = items.map(function(m) {
                return [
                    m.id || '',
                    m.role || m.agent_role || '',
                    m.type || m.memory_type || '',
                    m.key || '',
                    typeof m.value === 'string' ? m.value : JSON.stringify(m.value || ''),
                    m.created_at || ''
                ];
            });
            exportToCsv('memories-' + new Date().toISOString().slice(0, 10) + '.csv', headers, rows);
            showToast('Memory data exported (' + rows.length + ' memories)', 'success', 3000);
        })
        .catch(function(err) { showToast('Failed to export memory data: ' + err.message); });
}

// ================================================================
// Advanced Filters: Date Range
// ================================================================
function applyAdvancedFilters() {
    var dateFrom = document.getElementById('filterDateFrom') ? document.getElementById('filterDateFrom').value : '';
    var dateTo = document.getElementById('filterDateTo') ? document.getElementById('filterDateTo').value : '';
    // Read from both filter bar variants (old: task-search/filter-*, new: taskSearchInput/statusFilter/etc)
    var searchEl = document.getElementById('taskSearchInput');
    var searchEl2 = document.getElementById('task-search');
    var query = (searchEl ? searchEl.value : '') || (searchEl2 ? searchEl2.value : '');
    var statusEl = document.getElementById('statusFilter');
    var statusEl2 = document.getElementById('filter-status');
    var status = (statusEl ? statusEl.value : '') || (statusEl2 ? statusEl2.value : '');
    var roleEl = document.getElementById('roleFilter');
    var roleEl2 = document.getElementById('filter-assignee');
    var role = (roleEl ? roleEl.value : '') || (roleEl2 ? roleEl2.value : '');
    var priEl = document.getElementById('priorityFilter');
    var priEl2 = document.getElementById('filter-priority');
    var priority = (priEl ? priEl.value : '') || (priEl2 ? priEl2.value : '');

    var q = (query || '').toLowerCase();
    var filtered = allTasks.filter(function(t) {
        if (q && !(t.title || '').toLowerCase().includes(q) && !(t.description || '').toLowerCase().includes(q) && !(t.id || '').toLowerCase().includes(q)) return false;
        if (status && t._status !== status) return false;
        if (role && t.assigned_to !== role) return false;
        if (priority && t.priority !== priority) return false;
        if (dateFrom && t.created_at && new Date(t.created_at) < new Date(dateFrom)) return false;
        if (dateTo && t.created_at && new Date(t.created_at) > new Date(dateTo + 'T23:59:59')) return false;
        return true;
    });

    var grouped = { blocked: [], pending: [], in_progress: [], completed: [], rejected: [] };
    for (var i = 0; i < filtered.length; i++) {
        var s = filtered[i]._status || 'pending';
        if (grouped[s]) grouped[s].push(filtered[i]);
    }
    renderBoardView(grouped);
}

// ================================================================
// Phase 8 Feature 1: Real-Time Agent Monitoring
// ================================================================
var monitoringInterval = null;

function loadMonitoringView() {
    fetchMonAgentStatus();
    fetchMonActivityFeed();
    fetchMonTokenUsage();
    // Start auto-refresh every 10 seconds
    if (monitoringInterval) clearInterval(monitoringInterval);
    monitoringInterval = setInterval(function() {
        if (currentView === 'monitoring') {
            fetchMonAgentStatus();
            fetchMonActivityFeed();
            fetchMonTokenUsage();
        }
    }, 10000);
}

function fetchMonAgentStatus() {
    var container = document.getElementById('monAgentGrid');
    Promise.all([
        fetch('/api/agents').then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        }).catch(function() { return []; }),
        fetch('/api/board').then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        }).catch(function() { return []; })
    ]).then(function(results) {
        var agents = Array.isArray(results[0]) ? results[0] : (results[0].agents || []);
        var tasks = Array.isArray(results[1]) ? results[1] : (results[1].tasks || []);
        if (!agents.length) {
            container.innerHTML = '<div class="intel-empty">No agents found</div>';
            return;
        }
        // Build a map of active tasks per agent
        var agentTasks = {};
        tasks.forEach(function(t) {
            var st = t._status || t.status || '';
            if (st === 'in_progress' && t.assigned_to) {
                agentTasks[t.assigned_to] = t;
            }
        });
        var html = '';
        agents.forEach(function(a) {
            var name = a.name || a.agent_name || a.role || 'Unknown';
            var role = a.role || a.agent_role || '';
            var status = a.status || 'idle';
            if (agentTasks[role] || agentTasks[name]) status = 'active';
            if (a.error || a.status === 'error') status = 'error';
            var statusClass = status === 'active' ? 'active' : (status === 'error' ? 'error' : 'idle');
            var lastActivity = a.last_activity || a.last_seen || a.updated_at || '';
            var currentTask = agentTasks[role] || agentTasks[name];
            html += '<div class="mon-agent-card">';
            html += '<div class="mon-agent-header">';
            html += '<span class="mon-status-dot ' + statusClass + '"></span>';
            html += '<div>';
            html += '<div class="mon-agent-name">' + escapeHtml(name) + '</div>';
            html += '<div class="mon-agent-role">' + escapeHtml(role) + '</div>';
            html += '</div>';
            html += '</div>';
            html += '<div class="mon-agent-meta">Status: <strong>' + escapeHtml(status) + '</strong></div>';
            if (lastActivity) {
                html += '<div class="mon-agent-meta">Last active: ' + timeAgo(lastActivity) + '</div>';
            }
            if (currentTask) {
                html += '<div class="mon-agent-task" title="' + escapeHtml(currentTask.title || currentTask.id || '') + '">';
                html += '&#x25B6; ' + escapeHtml(truncate(currentTask.title || currentTask.id || 'Working...', 50));
                html += '</div>';
            }
            html += '</div>';
        });
        container.innerHTML = html;
    }).catch(function() {
        container.innerHTML = '<div class="intel-empty">Unable to load agent status</div>';
    });
}

function fetchMonActivityFeed() {
    var container = document.getElementById('monActivityFeed');
    fetch('/api/v2/observability/decisions?limit=20')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.decisions || data.items || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No recent activity</div>';
                return;
            }
            var html = '';
            items.forEach(function(d) {
                var dtype = (d.type || d.decision_type || d.category || 'info').toLowerCase();
                var dotClass = 'info';
                if (dtype.indexOf('approv') >= 0) dotClass = 'approval';
                else if (dtype.indexOf('escal') >= 0) dotClass = 'escalation';
                else if (dtype.indexOf('error') >= 0 || dtype.indexOf('fail') >= 0) dotClass = 'error';
                else if (dtype.indexOf('decision') >= 0) dotClass = 'decision';
                var summary = d.summary || d.description || d.message || d.title || '';
                var agent = d.agent || d.agent_role || d.made_by || '';
                var ts = d.created_at || d.timestamp || d.decided_at || '';
                html += '<div class="mon-feed-item">';
                html += '<span class="mon-feed-dot ' + dotClass + '"></span>';
                html += '<div>';
                html += '<div class="mon-feed-text">';
                if (agent) html += '<strong>' + escapeHtml(agent) + '</strong>: ';
                html += escapeHtml(truncate(summary, 120));
                html += '</div>';
                if (ts) html += '<div class="mon-feed-time">' + timeAgo(ts) + '</div>';
                html += '</div>';
                html += '</div>';
            });
            container.innerHTML = html;
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Activity feed unavailable</div>';
        });
}

function fetchMonTokenUsage() {
    var container = document.getElementById('monTokenChart');
    fetch('/api/v2/observability/costs/by-agent')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.costs || data.agents || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No token usage data</div>';
                return;
            }
            // Find max for scaling
            var maxTokens = 0;
            items.forEach(function(a) {
                var input = a.input_tokens || a.prompt_tokens || 0;
                var output = a.output_tokens || a.completion_tokens || 0;
                var total = input + output;
                if (total > maxTokens) maxTokens = total;
            });
            if (maxTokens === 0) maxTokens = 1;
            var html = '<div class="mon-bar-legend">';
            html += '<div class="mon-bar-legend-item"><span class="mon-bar-legend-swatch" style="background:var(--accent-indigo)"></span> Input tokens</div>';
            html += '<div class="mon-bar-legend-item"><span class="mon-bar-legend-swatch" style="background:var(--accent-purple)"></span> Output tokens</div>';
            html += '</div>';
            items.forEach(function(a) {
                var agent = a.agent || a.agent_role || a.name || 'Unknown';
                var input = a.input_tokens || a.prompt_tokens || 0;
                var output = a.output_tokens || a.completion_tokens || 0;
                var total = input + output;
                var inputPct = (input / maxTokens) * 100;
                var outputPct = (output / maxTokens) * 100;
                html += '<div class="mon-bar-row">';
                html += '<span class="mon-bar-label">' + escapeHtml(agent) + '</span>';
                html += '<div class="mon-bar-track">';
                html += '<div class="mon-bar-input" style="width:' + inputPct.toFixed(1) + '%"></div>';
                html += '<div class="mon-bar-output" style="width:' + outputPct.toFixed(1) + '%"></div>';
                html += '</div>';
                html += '<span class="mon-bar-value">' + formatTokenCount(total) + '</span>';
                html += '</div>';
            });
            container.innerHTML = html;
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Token usage unavailable</div>';
        });
}

function formatTokenCount(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

// ================================================================
// Phase 8 Feature 2: Agent Performance Leaderboard
// ================================================================
var leaderboardData = [];

function loadLeaderboardView() {
    var container = document.getElementById('lbTableContainer');
    container.innerHTML = '<div class="intel-empty">Loading benchmarks...</div>';
    fetch('/api/v2/learning/benchmarks')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.benchmarks || []);
            // Aggregate benchmarks by agent
            var agentMap = {};
            items.forEach(function(b) {
                var agent = b.agent || b.agent_role || 'Unknown';
                if (!agentMap[agent]) {
                    agentMap[agent] = {
                        agent: agent,
                        tasks: 0,
                        qualitySum: 0,
                        qualityCount: 0,
                        durationSum: 0,
                        durationCount: 0,
                        costSum: 0,
                        costCount: 0,
                        scores: []
                    };
                }
                var entry = agentMap[agent];
                entry.tasks += (b.tasks_completed || b.tasks || 1);
                var score = b.score || b.benchmark_score || b.quality_score || 0;
                if (score > 0) {
                    entry.qualitySum += score;
                    entry.qualityCount++;
                    entry.scores.push(score);
                }
                var dur = b.avg_duration || b.duration || 0;
                if (dur > 0) {
                    entry.durationSum += dur;
                    entry.durationCount++;
                }
                var cost = b.cost || b.total_cost || 0;
                if (cost > 0) {
                    entry.costSum += cost;
                    entry.costCount++;
                }
            });
            leaderboardData = Object.keys(agentMap).map(function(k) { return agentMap[k]; });
            renderLeaderboardTable();
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Leaderboard data unavailable</div>';
        });
}

function renderLeaderboardTable() {
    var container = document.getElementById('lbTableContainer');
    if (!leaderboardData.length) {
        container.innerHTML = '<div class="intel-empty">No agent benchmark data</div>';
        return;
    }
    var metric = document.getElementById('lbSortMetric').value;
    // Compute derived values
    var entries = leaderboardData.map(function(e) {
        return {
            agent: e.agent,
            tasks: e.tasks,
            avgQuality: e.qualityCount > 0 ? (e.qualitySum / e.qualityCount) : 0,
            avgDuration: e.durationCount > 0 ? (e.durationSum / e.durationCount) : 9999,
            costEfficiency: e.costCount > 0 ? (e.costSum / e.costCount) : 9999,
            scores: e.scores.slice(-10) // last 10
        };
    });
    // Sort
    if (metric === 'quality') entries.sort(function(a, b) { return b.avgQuality - a.avgQuality; });
    else if (metric === 'tasks') entries.sort(function(a, b) { return b.tasks - a.tasks; });
    else if (metric === 'duration') entries.sort(function(a, b) { return a.avgDuration - b.avgDuration; });
    else if (metric === 'cost') entries.sort(function(a, b) { return a.costEfficiency - b.costEfficiency; });

    // Determine badge winners
    var fastestAgent = entries.slice().sort(function(a, b) { return a.avgDuration - b.avgDuration; })[0];
    var qualityAgent = entries.slice().sort(function(a, b) { return b.avgQuality - a.avgQuality; })[0];
    var costAgent = entries.slice().sort(function(a, b) { return a.costEfficiency - b.costEfficiency; })[0];

    var rows = entries.map(function(e, idx) {
        var rankClass = idx === 0 ? 'gold' : (idx === 1 ? 'silver' : (idx === 2 ? 'bronze' : 'default'));
        var badges = '';
        if (fastestAgent && e.agent === fastestAgent.agent && fastestAgent.avgDuration < 9999) {
            badges += '<span class="lb-badge speed-demon">&#x26A1; Speed Demon</span>';
        }
        if (qualityAgent && e.agent === qualityAgent.agent && qualityAgent.avgQuality > 0) {
            badges += '<span class="lb-badge quality-king">&#x1F451; Quality King</span>';
        }
        if (costAgent && e.agent === costAgent.agent && costAgent.costEfficiency < 9999) {
            badges += '<span class="lb-badge cost-efficient">&#x1F4B0; Cost Efficient</span>';
        }
        var sparkline = renderSparkline(e.scores);
        return [
            '<span class="lb-rank ' + rankClass + '">' + (idx + 1) + '</span>',
            escapeHtml(e.agent) + badges,
            String(e.tasks),
            '<span style="font-weight:700;color:' + (e.avgQuality >= 80 ? 'var(--accent-emerald)' : (e.avgQuality >= 50 ? 'var(--accent-amber)' : 'var(--accent-rose)')) + '">' + e.avgQuality.toFixed(1) + '</span>',
            e.avgDuration < 9999 ? e.avgDuration.toFixed(1) + 's' : '&#8212;',
            e.costEfficiency < 9999 ? '$' + e.costEfficiency.toFixed(4) : '&#8212;',
            '<span class="lb-sparkline-wrap">' + sparkline + '</span>'
        ];
    });
    container.innerHTML = v2RenderTable(['Rank', 'Agent', 'Tasks', 'Avg Quality', 'Avg Duration', 'Cost/Task', 'Trend'], rows);
}

function renderSparkline(scores) {
    if (!scores || scores.length < 2) return '<span style="color:var(--text-muted);font-size:11px">&#8212;</span>';
    var w = 80, h = 24, padding = 2;
    var min = Math.min.apply(null, scores);
    var max = Math.max.apply(null, scores);
    var range = max - min || 1;
    var points = scores.map(function(s, i) {
        var x = padding + (i / (scores.length - 1)) * (w - 2 * padding);
        var y = h - padding - ((s - min) / range) * (h - 2 * padding);
        return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    var lastScore = scores[scores.length - 1];
    var color = lastScore >= 80 ? 'var(--accent-emerald)' : (lastScore >= 50 ? 'var(--accent-amber)' : 'var(--accent-rose)');
    return '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">' +
        '<polyline fill="none" stroke="' + color + '" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" points="' + points + '"/>' +
        '</svg>';
}

// ================================================================
// Phase 8 Feature 3: Notification Center Tab
// ================================================================
var ntabNotifications = [];

function loadNotificationsView() {
    var container = document.getElementById('ntabListContainer');
    container.innerHTML = '<div class="intel-empty">Loading notifications...</div>';
    fetch('/api/notifications')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            ntabNotifications = Array.isArray(data) ? data : (data.notifications || data.items || []);
            updateNtabBadge();
            renderNotificationsTab();
        })
        .catch(function() {
            ntabNotifications = [];
            container.innerHTML = '<div class="intel-empty">Notifications service unavailable</div>';
            updateNtabBadge();
        });
}

function updateNtabBadge() {
    var badge = document.getElementById('ntabTabBadge');
    if (!badge) return;
    var unread = ntabNotifications.filter(function(n) { return !n.read; }).length;
    badge.textContent = unread > 0 ? (unread > 99 ? '99+' : String(unread)) : '';
}

function renderNotificationsTab() {
    var container = document.getElementById('ntabListContainer');
    var filterType = document.getElementById('ntabFilterType').value;
    var filterRead = document.getElementById('ntabFilterRead').value;
    var filterDateFrom = document.getElementById('ntabFilterDateFrom').value;
    var filterDateTo = document.getElementById('ntabFilterDateTo').value;

    var filtered = ntabNotifications.filter(function(n) {
        var ntype = (n.type || n.category || n.severity || 'alert').toLowerCase();
        if (filterType && ntype !== filterType) return false;
        if (filterRead === 'unread' && n.read) return false;
        if (filterRead === 'read' && !n.read) return false;
        if (filterDateFrom && n.created_at && new Date(n.created_at) < new Date(filterDateFrom)) return false;
        if (filterDateTo && n.created_at && new Date(n.created_at) > new Date(filterDateTo + 'T23:59:59')) return false;
        return true;
    });

    if (!filtered.length) {
        container.innerHTML = '<div class="intel-empty">No notifications match the current filters</div>';
        return;
    }

    // Group by type
    var groups = {};
    filtered.forEach(function(n) {
        var ntype = (n.type || n.category || n.severity || 'alert').toLowerCase();
        if (!groups[ntype]) groups[ntype] = [];
        groups[ntype].push(n);
    });

    var typeIcons = { escalation: '&#x1F6A8;', approval: '&#x2705;', anomaly: '&#x26A0;', alert: '&#x1F514;', info: '&#x2139;', error: '&#x274C;', warning: '&#x26A0;', critical: '&#x1F6A8;' };
    var html = '';
    Object.keys(groups).forEach(function(gtype) {
        html += '<div class="ntab-group-header">' + escapeHtml(gtype) + ' (' + groups[gtype].length + ')</div>';
        groups[gtype].forEach(function(n) {
            var isUnread = !n.read;
            var icon = typeIcons[gtype] || '&#x1F514;';
            var iconClass = gtype;
            if (!['escalation', 'approval', 'anomaly', 'alert', 'info'].includes(iconClass)) iconClass = 'alert';
            var msg = n.message || n.title || n.description || '';
            var source = n.source || n.agent || n.source_agent || '';
            var ts = n.created_at || n.timestamp || '';
            var nid = n.id || 0;
            html += '<div class="ntab-item ' + (isUnread ? 'unread' : '') + '" onclick="ntabMarkRead(' + nid + ')">';
            if (isUnread) html += '<span class="ntab-unread-dot"></span>';
            html += '<span class="ntab-icon ' + iconClass + '">' + icon + '</span>';
            html += '<div class="ntab-content">';
            html += '<div class="ntab-msg">' + escapeHtml(msg) + '</div>';
            html += '<div class="ntab-meta">';
            if (source) html += '<span>From: ' + escapeHtml(source) + '</span>';
            if (ts) html += '<span>' + timeAgo(ts) + '</span>';
            html += '</div>';
            html += '</div>';
            html += '</div>';
        });
    });
    container.innerHTML = html;
}

function ntabMarkRead(id) {
    fetch('/api/notifications/' + id, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ read: true })
    }).catch(function() {
        // Fallback to POST endpoint used by existing code
        fetch('/api/notifications/' + id + '/read', { method: 'POST' }).catch(function() {});
    });
    // Optimistic update
    ntabNotifications.forEach(function(n) {
        if (n.id === id) n.read = true;
    });
    updateNtabBadge();
    renderNotificationsTab();
}

function ntabMarkAllRead() {
    fetch('/api/notifications/read-all', { method: 'POST' }).catch(function() {});
    ntabNotifications.forEach(function(n) { n.read = true; });
    updateNtabBadge();
    renderNotificationsTab();
}

// ================================================================
// Phase 8 Feature 4: Pipeline Visualization
// ================================================================
var pipeGroups = [];
var pipeTasks = {};

function loadPipelinesView() {
    var select = document.getElementById('pipeGroupSelect');
    fetch('/api/groups')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            pipeGroups = Array.isArray(data) ? data : (data.groups || []);
            var html = '<option value="">Select a pipeline / group...</option>';
            pipeGroups.forEach(function(g) {
                var gid = g.id || g.group_id || '';
                var name = g.name || g.title || gid;
                html += '<option value="' + escapeHtml(String(gid)) + '">' + escapeHtml(name) + '</option>';
            });
            select.innerHTML = html;
            // If we already had a selection, keep it
            if (select.value) renderSelectedPipeline();
        })
        .catch(function() {
            select.innerHTML = '<option value="">Unable to load groups</option>';
        });
}

function renderSelectedPipeline() {
    var gid = document.getElementById('pipeGroupSelect').value;
    var statsRow = document.getElementById('pipeStatsRow');
    var diagram = document.getElementById('pipeDiagramWrap');
    if (!gid) {
        statsRow.style.display = 'none';
        diagram.style.display = 'none';
        return;
    }
    statsRow.style.display = 'flex';
    diagram.style.display = 'block';
    diagram.innerHTML = '<div class="intel-empty">Loading pipeline...</div>';
    fetch('/api/board?group_id=' + encodeURIComponent(gid))
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var tasks = Array.isArray(data) ? data : (data.tasks || []);
            pipeTasks[gid] = tasks;
            renderPipelineStats(tasks);
            renderPipelineDiagram(tasks);
        })
        .catch(function() {
            diagram.innerHTML = '<div class="intel-empty">Unable to load pipeline tasks</div>';
            statsRow.style.display = 'none';
        });
}

function renderPipelineStats(tasks) {
    var statsRow = document.getElementById('pipeStatsRow');
    var total = tasks.length;
    var completed = tasks.filter(function(t) { return (t._status || t.status) === 'completed'; }).length;
    var inProgress = tasks.filter(function(t) { return (t._status || t.status) === 'in_progress'; }).length;
    var failed = tasks.filter(function(t) { return (t._status || t.status) === 'failed'; }).length;
    var pct = total > 0 ? ((completed / total) * 100).toFixed(0) : 0;

    // Find bottleneck: longest in_progress task
    var bottleneck = null;
    var longestDur = 0;
    tasks.forEach(function(t) {
        if ((t._status || t.status) === 'in_progress' && t.started_at) {
            var dur = Date.now() - new Date(t.started_at).getTime();
            if (dur > longestDur) {
                longestDur = dur;
                bottleneck = t;
            }
        }
    });

    var html = '';
    html += '<div class="pipe-stat"><div class="pipe-stat-label">Total Tasks</div><div class="pipe-stat-value">' + total + '</div></div>';
    html += '<div class="pipe-stat"><div class="pipe-stat-label">Completed</div><div class="pipe-stat-value" style="color:var(--accent-emerald)">' + pct + '%</div></div>';
    html += '<div class="pipe-stat"><div class="pipe-stat-label">In Progress</div><div class="pipe-stat-value" style="color:var(--accent-blue)">' + inProgress + '</div></div>';
    html += '<div class="pipe-stat"><div class="pipe-stat-label">Failed</div><div class="pipe-stat-value" style="color:var(--accent-rose)">' + failed + '</div></div>';
    if (bottleneck) {
        var mins = Math.floor(longestDur / 60000);
        html += '<div class="pipe-stat"><div class="pipe-stat-label">Bottleneck</div><div class="pipe-stat-value" style="font-size:13px;color:var(--accent-amber)">' + escapeHtml(truncate(bottleneck.title || bottleneck.id || '', 25)) + ' (' + mins + 'm)</div></div>';
    }
    statsRow.innerHTML = html;
}

function renderPipelineDiagram(tasks) {
    var diagram = document.getElementById('pipeDiagramWrap');
    if (!tasks.length) {
        diagram.innerHTML = '<div class="intel-empty">No tasks in this pipeline</div>';
        return;
    }
    // Build task map
    var taskMap = {};
    tasks.forEach(function(t) { taskMap[t.id || t.task_id] = t; });

    // Layout: arrange in rows by status order
    var nodeW = 160, nodeH = 46, gapX = 60, gapY = 20, padX = 40, padY = 30;
    var positioned = [];
    var col = 0;
    // Simple left-to-right layout: order by status progression then by dependency
    var statusOrder = { pending: 0, blocked: 1, in_progress: 2, completed: 3, failed: 4 };
    var sorted = tasks.slice().sort(function(a, b) {
        var sa = statusOrder[(a._status || a.status)] || 0;
        var sb = statusOrder[(b._status || b.status)] || 0;
        return sa - sb;
    });

    var maxPerRow = 4;
    sorted.forEach(function(t, i) {
        var r = Math.floor(i / maxPerRow);
        var c = i % maxPerRow;
        positioned.push({
            task: t,
            x: padX + c * (nodeW + gapX),
            y: padY + r * (nodeH + gapY),
            id: t.id || t.task_id
        });
    });

    var svgW = padX * 2 + Math.min(tasks.length, maxPerRow) * (nodeW + gapX);
    var rows = Math.ceil(tasks.length / maxPerRow);
    var svgH = padY * 2 + rows * (nodeH + gapY);

    // Build position lookup
    var posMap = {};
    positioned.forEach(function(p) { posMap[p.id] = p; });

    var svg = '<svg width="' + svgW + '" height="' + svgH + '" viewBox="0 0 ' + svgW + ' ' + svgH + '" xmlns="http://www.w3.org/2000/svg">';
    // Arrowhead marker
    svg += '<defs><marker id="pipe-arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="rgba(255,255,255,0.3)" /></marker></defs>';

    // Draw dependency arrows
    positioned.forEach(function(p) {
        var deps = p.task.depends_on || p.task.dependencies || [];
        if (!Array.isArray(deps)) deps = [];
        deps.forEach(function(depId) {
            var from = posMap[depId];
            if (from) {
                var x1 = from.x + nodeW;
                var y1 = from.y + nodeH / 2;
                var x2 = p.x;
                var y2 = p.y + nodeH / 2;
                svg += '<line class="pipe-arrow" x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" />';
            }
        });
    });

    // Draw nodes
    positioned.forEach(function(p) {
        var status = (p.task._status || p.task.status || 'pending').replace(/ /g, '_');
        var title = p.task.title || p.task.id || '';
        var assignee = p.task.assigned_to || '';
        svg += '<g class="pipe-node" transform="translate(' + p.x + ',' + p.y + ')">';
        svg += '<rect class="pipe-node-rect ' + escapeHtml(status) + '" width="' + nodeW + '" height="' + nodeH + '" />';
        svg += '<text x="' + (nodeW / 2) + '" y="18" text-anchor="middle" fill="var(--text-primary)" font-size="11" font-weight="600" font-family="Inter,sans-serif">';
        svg += escapeHtml(truncate(title, 20));
        svg += '</text>';
        svg += '<text x="' + (nodeW / 2) + '" y="34" text-anchor="middle" fill="var(--text-muted)" font-size="10" font-family="Inter,sans-serif">';
        svg += escapeHtml(assignee || status);
        svg += '</text>';
        svg += '</g>';
    });

    svg += '</svg>';
    diagram.innerHTML = svg;
}

// ================================================================
// Phase 8 Feature 5: Webhook Management UI
// ================================================================
var whWebhooks = [];

function loadWebhooksView() {
    fetchWebhookList();
}

function fetchWebhookList() {
    var container = document.getElementById('whListContainer');
    container.innerHTML = '<div class="intel-empty">Loading webhooks...</div>';
    fetch('/api/webhooks')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            whWebhooks = Array.isArray(data) ? data : (data.webhooks || []);
            renderWebhookList();
        })
        .catch(function() {
            whWebhooks = [];
            container.innerHTML = '<div class="intel-empty">Webhook service unavailable</div>';
        });
}

function renderWebhookList() {
    var container = document.getElementById('whListContainer');
    if (!whWebhooks.length) {
        container.innerHTML = '<div class="intel-empty">No webhooks configured</div>';
        return;
    }
    var rows = whWebhooks.map(function(wh) {
        var whId = wh.id || wh.webhook_id || 0;
        var status = wh.status || 'active';
        var statusClass = status === 'active' ? 'active' : (status === 'paused' ? 'paused' : 'error');
        var events = wh.events || wh.event_types || [];
        if (!Array.isArray(events)) events = [String(events)];
        var eventTags = events.map(function(e) { return '<span class="wh-event-tag">' + escapeHtml(e) + '</span>'; }).join('');
        var actions = '<button class="wh-btn wh-btn-test" onclick="whTestWebhook(' + whId + ')">Test</button> ';
        actions += '<button class="wh-btn wh-btn-danger" onclick="whDeleteWebhook(' + whId + ')">Delete</button>';
        return [
            escapeHtml(truncate(wh.url || '', 50)),
            '<div class="wh-events-list">' + eventTags + '</div>',
            '<span class="wh-status-pill ' + statusClass + '">' + escapeHtml(status) + '</span>',
            wh.created_at ? timeAgo(wh.created_at) : '&#8212;',
            actions
        ];
    });
    container.innerHTML = v2RenderTable(['URL', 'Events', 'Status', 'Created', 'Actions'], rows);
}

function whAddWebhook() {
    var url = document.getElementById('whUrlInput').value.trim();
    if (!url) { alert('Please enter a webhook URL.'); return; }
    var checks = document.querySelectorAll('#whEventChecks input[type="checkbox"]:checked');
    var events = [];
    checks.forEach(function(cb) { events.push(cb.value); });
    if (!events.length) { alert('Please select at least one event type.'); return; }
    var secret = document.getElementById('whSecretInput').value.trim();
    var payload = { url: url, events: events };
    if (secret) payload.secret = secret;
    fetch('/api/webhooks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
    })
    .then(function() {
        // Reset form
        document.getElementById('whUrlInput').value = '';
        document.getElementById('whSecretInput').value = '';
        document.querySelectorAll('#whEventChecks input[type="checkbox"]').forEach(function(cb) { cb.checked = false; });
        fetchWebhookList();
    })
    .catch(function(err) {
        alert('Failed to create webhook: ' + err.message);
    });
}

function whTestWebhook(id) {
    fetch('/api/webhooks/' + id + '/test', { method: 'POST' })
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            alert('Test payload sent successfully!');
        })
        .catch(function(err) {
            alert('Test failed: ' + err.message);
        });
}

function whDeleteWebhook(id) {
    if (!confirm('Are you sure you want to delete this webhook?')) return;
    fetch('/api/webhooks/' + id, { method: 'DELETE' })
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            fetchWebhookList();
        })
        .catch(function(err) {
            alert('Delete failed: ' + err.message);
        });
}

// ================================================================
// V3: Self-Improvement Intelligence
// ================================================================
function loadSelfImproveView() {
    fetchSiPromptHistory();
    fetchSiPortfolio();
    fetchSiReflections();
    fetchSiFailureModes();
}

function fetchSiPromptHistory() {
    var container = document.getElementById('siPromptHistoryContainer');
    fetch('/api/v3/self-improvement/prompt-history?agent_role=')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.history || data.prompts || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No prompt history available</div>';
                return;
            }
            var rows = items.map(function(p) {
                return [
                    escapeHtml(p.agent_role || p.role || ''),
                    escapeHtml(truncate(p.prompt || p.template || '', 80)),
                    escapeHtml(p.version || p.revision || ''),
                    '<span style="font-weight:600">' + escapeHtml(String(p.success_rate || p.score || '')) + '</span>',
                    p.created_at ? timeAgo(p.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Agent', 'Prompt', 'Version', 'Success Rate', 'Created'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Prompt history service unavailable</div>';
        });
}

function fetchSiPortfolio() {
    var container = document.getElementById('siPortfolioContainer');
    fetch('/api/v3/self-improvement/portfolio?agent_role=')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.strategies || data.portfolio || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No strategies in portfolio</div>';
                return;
            }
            var rows = items.map(function(s) {
                return [
                    escapeHtml(s.name || s.strategy || ''),
                    escapeHtml(s.agent_role || s.role || ''),
                    escapeHtml(s.status || s.state || 'active'),
                    '<span style="font-weight:600">' + escapeHtml(String(s.effectiveness || s.score || '')) + '</span>',
                    escapeHtml(String(s.usage_count || s.uses || 0))
                ];
            });
            container.innerHTML = v2RenderTable(['Strategy', 'Agent', 'Status', 'Effectiveness', 'Uses'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Portfolio service unavailable</div>';
        });
}

function fetchSiReflections() {
    var container = document.getElementById('siReflectionsContainer');
    fetch('/api/v3/self-improvement/reflections')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.reflections || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No recent reflections</div>';
                return;
            }
            var rows = items.map(function(r) {
                return [
                    escapeHtml(r.agent_role || r.agent || ''),
                    escapeHtml(truncate(r.reflection || r.insight || r.content || '', 100)),
                    escapeHtml(r.category || r.type || ''),
                    r.created_at ? timeAgo(r.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Agent', 'Reflection', 'Category', 'When'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Reflections service unavailable</div>';
        });
}

function fetchSiFailureModes() {
    var container = document.getElementById('siFailureModesContainer');
    fetch('/api/v3/self-improvement/failure-modes')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.failure_modes || data.failures || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No failure modes catalogued</div>';
                return;
            }
            var rows = items.map(function(f) {
                return [
                    escapeHtml(f.category || f.mode || f.name || ''),
                    escapeHtml(truncate(f.description || f.pattern || '', 80)),
                    escapeHtml(String(f.occurrence_count || f.count || 0)),
                    escapeHtml(f.severity || f.impact || ''),
                    escapeHtml(truncate(f.mitigation || f.fix || '', 60))
                ];
            });
            container.innerHTML = v2RenderTable(['Category', 'Description', 'Occurrences', 'Severity', 'Mitigation'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Failure taxonomy service unavailable</div>';
        });
}

// ================================================================
// V3: Social Intelligence
// ================================================================
function loadSocialView() {
    fetchSocTrustNetwork();
    fetchSocArguments();
    fetchSocMentalModel();
    fetchSocCollaborations();
}

function fetchSocTrustNetwork() {
    var container = document.getElementById('socTrustContainer');
    fetch('/api/v3/social/trust/network')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.edges || data.trust || data.network || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No trust network data</div>';
                return;
            }
            var rows = items.map(function(e) {
                var score = e.trust_score || e.score || 0;
                var barColor = score >= 0.7 ? 'var(--accent-emerald)' : (score >= 0.4 ? 'var(--accent-amber)' : 'var(--accent-rose)');
                return [
                    escapeHtml(e.from || e.source || ''),
                    escapeHtml(e.to || e.target || ''),
                    '<span style="font-weight:700;color:' + barColor + '">' + Number(score).toFixed(2) + '</span>',
                    escapeHtml(String(e.interactions || e.history_count || 0)),
                    e.updated_at ? timeAgo(e.updated_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['From', 'To', 'Trust Score', 'Interactions', 'Updated'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Trust network service unavailable</div>';
        });
}

function fetchSocArguments() {
    var container = document.getElementById('socArgumentsContainer');
    fetch('/api/v3/social/arguments')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.arguments || data.debates || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No active arguments</div>';
                return;
            }
            var rows = items.map(function(a) {
                return [
                    escapeHtml(truncate(a.topic || a.subject || '', 60)),
                    escapeHtml((a.participants || []).join(', ')),
                    escapeHtml(a.status || a.state || 'open'),
                    escapeHtml(a.resolution || a.outcome || ''),
                    a.created_at ? timeAgo(a.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Topic', 'Participants', 'Status', 'Resolution', 'Started'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Arguments service unavailable</div>';
        });
}

function fetchSocMentalModel() {
    var container = document.getElementById('socMentalModelContainer');
    fetch('/api/v3/social/mental-model')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.concepts || data.model || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No shared mental model data</div>';
                return;
            }
            var rows = items.map(function(c) {
                var alignment = c.alignment || c.agreement || 0;
                var pct = (alignment * 100).toFixed(0);
                return [
                    escapeHtml(c.concept || c.topic || c.name || ''),
                    escapeHtml(truncate(c.definition || c.description || '', 80)),
                    '<span style="font-weight:600">' + pct + '%</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + pct + '%;background:var(--accent-indigo)"></div></div>',
                    escapeHtml(String(c.contributors || c.agents || ''))
                ];
            });
            container.innerHTML = v2RenderTable(['Concept', 'Definition', 'Alignment', 'Contributors'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Mental model service unavailable</div>';
        });
}

function fetchSocCollaborations() {
    var container = document.getElementById('socCollabContainer');
    fetch('/api/v3/social/collaborations/best')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.collaborations || data.pairs || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No collaboration scores</div>';
                return;
            }
            var rows = items.map(function(c) {
                var score = c.score || c.collaboration_score || 0;
                var barWidth = Math.min(100, score);
                var barColor = score >= 80 ? 'var(--accent-emerald)' : (score >= 50 ? 'var(--accent-amber)' : 'var(--accent-rose)');
                return [
                    escapeHtml(c.agent_a || c.from || ''),
                    escapeHtml(c.agent_b || c.to || ''),
                    '<span style="font-weight:700;color:' + barColor + '">' + Number(score).toFixed(1) + '</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + barWidth + '%;background:' + barColor + '"></div></div>',
                    escapeHtml(String(c.tasks_completed || c.joint_tasks || 0))
                ];
            });
            container.innerHTML = v2RenderTable(['Agent A', 'Agent B', 'Score', 'Joint Tasks'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Collaboration service unavailable</div>';
        });
}

// ================================================================
// V3: Code Reasoning
// ================================================================
function loadCodeReasonView() {
    fetchCrSemanticSearch();
    fetchCrImpact();
    fetchCrDebt();
    fetchCrRefactoring();
}

function fetchCrSemanticSearch() {
    var container = document.getElementById('crSemanticContainer');
    fetch('/api/v3/code-reasoning/semantic-search?query=')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.results || data.matches || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No semantic search results</div>';
                return;
            }
            var rows = items.map(function(r) {
                return [
                    escapeHtml(r.file || r.path || ''),
                    escapeHtml(truncate(r.snippet || r.content || '', 80)),
                    '<span style="font-weight:600">' + Number(r.similarity || r.score || 0).toFixed(2) + '</span>',
                    escapeHtml(r.language || r.type || '')
                ];
            });
            container.innerHTML = v2RenderTable(['File', 'Snippet', 'Similarity', 'Language'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Semantic search service unavailable</div>';
        });
}

function fetchCrImpact() {
    var container = document.getElementById('crImpactContainer');
    fetch('/api/v3/code-reasoning/impact/history')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.impacts || data.history || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No dependency impact data</div>';
                return;
            }
            var rows = items.map(function(i) {
                return [
                    escapeHtml(i.dependency || i.module || i.file || ''),
                    escapeHtml(String(i.dependents || i.affected_count || 0)),
                    escapeHtml(i.risk_level || i.impact || ''),
                    escapeHtml(truncate(i.change || i.description || '', 60)),
                    i.analyzed_at ? timeAgo(i.analyzed_at) : (i.created_at ? timeAgo(i.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Dependency', 'Dependents', 'Risk', 'Change', 'Analyzed'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Dependency impact service unavailable</div>';
        });
}

function fetchCrDebt() {
    var container = document.getElementById('crDebtContainer');
    fetch('/api/v3/code-reasoning/debt/prioritized')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.debt || data.items || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No tech debt identified</div>';
                return;
            }
            var rows = items.map(function(d) {
                return [
                    escapeHtml(d.file || d.location || ''),
                    escapeHtml(d.category || d.type || ''),
                    escapeHtml(truncate(d.description || d.issue || '', 80)),
                    escapeHtml(d.priority || d.severity || ''),
                    escapeHtml(d.estimated_effort || d.effort || '')
                ];
            });
            container.innerHTML = v2RenderTable(['File', 'Category', 'Description', 'Priority', 'Effort'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Tech debt service unavailable</div>';
        });
}

function fetchCrRefactoring() {
    var container = document.getElementById('crRefactorContainer');
    fetch('/api/v3/code-reasoning/refactoring')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.opportunities || data.refactorings || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No refactoring opportunities</div>';
                return;
            }
            var rows = items.map(function(r) {
                return [
                    escapeHtml(r.file || r.location || ''),
                    escapeHtml(r.type || r.pattern || ''),
                    escapeHtml(truncate(r.description || r.suggestion || '', 80)),
                    escapeHtml(r.impact || r.benefit || ''),
                    escapeHtml(r.effort || r.complexity || '')
                ];
            });
            container.innerHTML = v2RenderTable(['File', 'Type', 'Description', 'Impact', 'Effort'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Refactoring service unavailable</div>';
        });
}

// ================================================================
// V3: Task Intelligence
// ================================================================
function loadTaskIntelView() {
    fetchTiComplexity();
    fetchTiEffortTracking();
    fetchTiPredictions();
    fetchTiParallel();
}

function fetchTiComplexity() {
    var container = document.getElementById('tiComplexityContainer');
    fetch('/api/v3/task-intel/complexity')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.estimates || data.tasks || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No complexity estimates</div>';
                return;
            }
            var rows = items.map(function(t) {
                return [
                    escapeHtml(t.task_id || t.id || ''),
                    escapeHtml(truncate(t.title || t.name || '', 60)),
                    escapeHtml(t.complexity || t.level || ''),
                    escapeHtml(String(t.estimated_hours || t.estimate || '')),
                    escapeHtml(String(t.confidence || ''))
                ];
            });
            container.innerHTML = v2RenderTable(['Task', 'Title', 'Complexity', 'Est. Hours', 'Confidence'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Complexity service unavailable</div>';
        });
}

function fetchTiEffortTracking() {
    var container = document.getElementById('tiEffortContainer');
    fetch('/api/v3/task-intel/effort-tracking/history')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.history || data.tracking || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No effort drift data</div>';
                return;
            }
            var rows = items.map(function(e) {
                var drift = e.drift || e.variance || 0;
                var driftColor = Math.abs(drift) <= 10 ? 'var(--accent-emerald)' : (Math.abs(drift) <= 30 ? 'var(--accent-amber)' : 'var(--accent-rose)');
                return [
                    escapeHtml(e.task_id || e.id || ''),
                    escapeHtml(String(e.estimated || e.planned || '')),
                    escapeHtml(String(e.actual || e.spent || '')),
                    '<span style="font-weight:700;color:' + driftColor + '">' + (drift > 0 ? '+' : '') + Number(drift).toFixed(1) + '%</span>',
                    e.completed_at ? timeAgo(e.completed_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Task', 'Estimated', 'Actual', 'Drift', 'Completed'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Effort tracking service unavailable</div>';
        });
}

function fetchTiPredictions() {
    var container = document.getElementById('tiPredictionsContainer');
    fetch('/api/v3/task-intel/predictions/accuracy')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.predictions || data.accuracy || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No outcome predictions</div>';
                return;
            }
            var rows = items.map(function(p) {
                var accuracy = p.accuracy || p.score || 0;
                var barWidth = Math.min(100, accuracy);
                var barColor = accuracy >= 80 ? 'var(--accent-emerald)' : (accuracy >= 50 ? 'var(--accent-amber)' : 'var(--accent-rose)');
                return [
                    escapeHtml(p.model || p.predictor || p.name || ''),
                    escapeHtml(p.outcome_type || p.category || ''),
                    '<span style="font-weight:700;color:' + barColor + '">' + Number(accuracy).toFixed(1) + '%</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + barWidth + '%;background:' + barColor + '"></div></div>',
                    escapeHtml(String(p.sample_size || p.predictions_count || ''))
                ];
            });
            container.innerHTML = v2RenderTable(['Model', 'Outcome Type', 'Accuracy', 'Sample Size'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Predictions service unavailable</div>';
        });
}

function fetchTiParallel() {
    var container = document.getElementById('tiParallelContainer');
    fetch('/api/v3/task-intel/parallel')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.opportunities || data.parallel || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No parallel opportunities found</div>';
                return;
            }
            var rows = items.map(function(o) {
                return [
                    escapeHtml((o.tasks || o.task_ids || []).join(', ')),
                    escapeHtml(o.reason || o.rationale || ''),
                    escapeHtml(String(o.time_saved || o.savings || '')),
                    escapeHtml(o.risk || o.conflict_risk || 'low')
                ];
            });
            container.innerHTML = v2RenderTable(['Tasks', 'Reason', 'Time Saved', 'Risk'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Parallel analysis service unavailable</div>';
        });
}

// ================================================================
// V3: Verification Intelligence
// ================================================================
function loadVerificationView() {
    fetchVerFlakyTests();
    fetchVerRegressions();
    fetchVerQualityGates();
    fetchVerAnnotations();
}

function fetchVerFlakyTests() {
    var container = document.getElementById('verFlakyContainer');
    fetch('/api/v3/verification/flaky-tests/list')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.flaky_tests || data.tests || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No flaky tests detected</div>';
                return;
            }
            var rows = items.map(function(t) {
                var flakeRate = t.flake_rate || t.failure_rate || 0;
                var barColor = flakeRate >= 50 ? 'var(--accent-rose)' : (flakeRate >= 20 ? 'var(--accent-amber)' : 'var(--accent-emerald)');
                return [
                    escapeHtml(t.test_name || t.name || ''),
                    escapeHtml(t.file || t.suite || ''),
                    '<span style="font-weight:700;color:' + barColor + '">' + Number(flakeRate).toFixed(1) + '%</span>',
                    escapeHtml(String(t.total_runs || t.runs || 0)),
                    t.last_flake ? timeAgo(t.last_flake) : (t.last_failure ? timeAgo(t.last_failure) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Test', 'File', 'Flake Rate', 'Total Runs', 'Last Flake'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Flaky tests service unavailable</div>';
        });
}

function fetchVerRegressions() {
    var container = document.getElementById('verRegressionsContainer');
    fetch('/api/v3/verification/regressions')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.regressions || data.fingerprints || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No regression fingerprints</div>';
                return;
            }
            var rows = items.map(function(r) {
                return [
                    escapeHtml(r.fingerprint || r.id || ''),
                    escapeHtml(truncate(r.pattern || r.description || '', 80)),
                    escapeHtml(String(r.occurrences || r.count || 0)),
                    escapeHtml(r.severity || r.impact || ''),
                    r.last_seen ? timeAgo(r.last_seen) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Fingerprint', 'Pattern', 'Occurrences', 'Severity', 'Last Seen'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Regressions service unavailable</div>';
        });
}

function fetchVerQualityGates() {
    var container = document.getElementById('verQualityGatesContainer');
    fetch('/api/v3/verification/quality-gates')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.gates || data.quality_gates || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No quality gates configured</div>';
                return;
            }
            var rows = items.map(function(g) {
                var passed = g.passed || g.status === 'passed';
                var statusHtml = passed ?
                    '<span style="color:var(--accent-emerald);font-weight:600">Passed</span>' :
                    '<span style="color:var(--accent-rose);font-weight:600">Failed</span>';
                return [
                    escapeHtml(g.name || g.gate || ''),
                    escapeHtml(g.metric || g.check || ''),
                    escapeHtml(String(g.threshold || '')),
                    escapeHtml(String(g.actual || g.value || '')),
                    statusHtml
                ];
            });
            container.innerHTML = v2RenderTable(['Gate', 'Metric', 'Threshold', 'Actual', 'Status'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Quality gates service unavailable</div>';
        });
}

function fetchVerAnnotations() {
    var container = document.getElementById('verAnnotationsContainer');
    fetch('/api/v3/verification/annotations')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.annotations || data.reviews || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No review annotations</div>';
                return;
            }
            var rows = items.map(function(a) {
                return [
                    escapeHtml(a.reviewer || a.agent || ''),
                    escapeHtml(a.file || a.location || ''),
                    escapeHtml(a.type || a.category || ''),
                    escapeHtml(truncate(a.comment || a.annotation || a.message || '', 80)),
                    a.created_at ? timeAgo(a.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Reviewer', 'File', 'Type', 'Comment', 'When'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Annotations service unavailable</div>';
        });
}

// ================================================================
// V3: Process Intelligence
// ================================================================
function loadProcessView() {
    fetchProcVelocity();
    fetchProcRisk();
    fetchProcBottlenecks();
    fetchProcRetro();
}

function fetchProcVelocity() {
    var container = document.getElementById('procVelocityContainer');
    fetch('/api/v3/process/velocity/history')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.velocity || data.history || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No velocity data</div>';
                return;
            }
            var rows = items.map(function(v) {
                return [
                    escapeHtml(v.sprint || v.period || v.week || ''),
                    escapeHtml(String(v.completed || v.points_done || 0)),
                    escapeHtml(String(v.planned || v.points_planned || 0)),
                    escapeHtml(String(v.forecast || v.predicted || '')),
                    escapeHtml(String(v.trend || v.direction || ''))
                ];
            });
            container.innerHTML = v2RenderTable(['Sprint', 'Completed', 'Planned', 'Forecast', 'Trend'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Velocity service unavailable</div>';
        });
}

function fetchProcRisk() {
    var container = document.getElementById('procRiskContainer');
    fetch('/api/v3/process/risk-scores/heat-map')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.risks || data.heat_map || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No risk data available</div>';
                return;
            }
            var rows = items.map(function(r) {
                var score = r.risk_score || r.score || 0;
                var barColor = score >= 70 ? 'var(--accent-rose)' : (score >= 40 ? 'var(--accent-amber)' : 'var(--accent-emerald)');
                return [
                    escapeHtml(r.area || r.component || r.name || ''),
                    '<span style="font-weight:700;color:' + barColor + '">' + Number(score).toFixed(0) + '</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + Math.min(100, score) + '%;background:' + barColor + '"></div></div>',
                    escapeHtml(r.likelihood || r.probability || ''),
                    escapeHtml(r.impact || r.consequence || ''),
                    escapeHtml(truncate(r.mitigation || r.action || '', 60))
                ];
            });
            container.innerHTML = v2RenderTable(['Area', 'Risk Score', 'Likelihood', 'Impact', 'Mitigation'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Risk heat map service unavailable</div>';
        });
}

function fetchProcBottlenecks() {
    var container = document.getElementById('procBottlenecksContainer');
    fetch('/api/v3/process/bottlenecks')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.bottlenecks || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No bottlenecks detected</div>';
                return;
            }
            var rows = items.map(function(b) {
                return [
                    escapeHtml(b.stage || b.phase || b.name || ''),
                    escapeHtml(truncate(b.description || b.cause || '', 80)),
                    escapeHtml(String(b.wait_time || b.delay || '')),
                    escapeHtml(String(b.affected_tasks || b.blocked_count || 0)),
                    escapeHtml(truncate(b.suggestion || b.recommendation || '', 60))
                ];
            });
            container.innerHTML = v2RenderTable(['Stage', 'Description', 'Wait Time', 'Affected Tasks', 'Suggestion'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Bottleneck service unavailable</div>';
        });
}

function fetchProcRetro() {
    var container = document.getElementById('procRetroContainer');
    fetch('/api/v3/process/retrospectives/list')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.retrospectives || data.retros || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No sprint retrospectives</div>';
                return;
            }
            var rows = items.map(function(r) {
                return [
                    escapeHtml(r.sprint || r.period || r.name || ''),
                    escapeHtml(truncate(r.went_well || r.positives || '', 60)),
                    escapeHtml(truncate(r.to_improve || r.negatives || '', 60)),
                    escapeHtml(truncate(r.action_items || r.actions || '', 60)),
                    r.created_at ? timeAgo(r.created_at) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Sprint', 'Went Well', 'To Improve', 'Actions', 'Date'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Retrospectives service unavailable</div>';
        });
}

// ================================================================
// V3: Knowledge Management
// ================================================================
function loadKnowledgeMgmtView() {
    fetchKmStale();
    fetchKmDocGaps();
    fetchKmInstitutional();
    fetchKmCompression();
}

function fetchKmStale() {
    var container = document.getElementById('kmStaleContainer');
    fetch('/api/v3/knowledge/stale')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.stale || data.items || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No stale knowledge detected</div>';
                return;
            }
            var rows = items.map(function(s) {
                return [
                    escapeHtml(s.topic || s.title || s.name || ''),
                    escapeHtml(s.source || s.origin || ''),
                    escapeHtml(String(s.age_days || s.staleness || '')),
                    escapeHtml(truncate(s.reason || s.why_stale || '', 60)),
                    s.last_updated ? timeAgo(s.last_updated) : ''
                ];
            });
            container.innerHTML = v2RenderTable(['Topic', 'Source', 'Age (days)', 'Reason', 'Last Updated'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Stale knowledge service unavailable</div>';
        });
}

function fetchKmDocGaps() {
    var container = document.getElementById('kmDocGapsContainer');
    fetch('/api/v3/knowledge/doc-gaps')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.gaps || data.doc_gaps || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No documentation gaps found</div>';
                return;
            }
            var rows = items.map(function(g) {
                return [
                    escapeHtml(g.component || g.area || g.module || ''),
                    escapeHtml(g.gap_type || g.type || ''),
                    escapeHtml(truncate(g.description || g.detail || '', 80)),
                    escapeHtml(g.priority || g.severity || ''),
                    escapeHtml(g.suggested_owner || g.assignee || '')
                ];
            });
            container.innerHTML = v2RenderTable(['Component', 'Gap Type', 'Description', 'Priority', 'Suggested Owner'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Doc gaps service unavailable</div>';
        });
}

function fetchKmInstitutional() {
    var container = document.getElementById('kmInstitutionalContainer');
    fetch('/api/v3/knowledge/institutional')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.knowledge || data.institutional || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No institutional knowledge recorded</div>';
                return;
            }
            var rows = items.map(function(k) {
                return [
                    escapeHtml(k.topic || k.name || ''),
                    escapeHtml(k.holder || k.expert || k.agent || ''),
                    escapeHtml(truncate(k.summary || k.description || '', 80)),
                    escapeHtml(String(k.references || k.ref_count || 0)),
                    k.captured_at ? timeAgo(k.captured_at) : (k.created_at ? timeAgo(k.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Topic', 'Holder', 'Summary', 'References', 'Captured'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Institutional knowledge service unavailable</div>';
        });
}

function fetchKmCompression() {
    var container = document.getElementById('kmCompressionContainer');
    fetch('/api/v3/knowledge/compression/stats')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.stats || data.compression || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No compression stats available</div>';
                return;
            }
            var rows = items.map(function(s) {
                var ratio = s.ratio || s.compression_ratio || 0;
                var barWidth = Math.min(100, ratio * 100);
                return [
                    escapeHtml(s.category || s.type || s.name || ''),
                    escapeHtml(String(s.original_size || s.raw || '')),
                    escapeHtml(String(s.compressed_size || s.compressed || '')),
                    '<span style="font-weight:600">' + Number(ratio).toFixed(2) + 'x</span>' +
                        '<div class="v2-score-bar"><div class="v2-score-fill" style="width:' + barWidth + '%;background:var(--accent-cyan)"></div></div>',
                    escapeHtml(String(s.entries || s.count || 0))
                ];
            });
            container.innerHTML = v2RenderTable(['Category', 'Original', 'Compressed', 'Ratio', 'Entries'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Compression stats service unavailable</div>';
        });
}

// ================================================================
// V3: Compliance & Threat Modeling
// ================================================================
function loadComplianceView() {
    fetchCompThreats();
    fetchCompRules();
    fetchCompStatus();
    fetchCompUnmitigated();
}

function fetchCompThreats() {
    var container = document.getElementById('compThreatContainer');
    fetch('/api/v3/compliance/threat-models')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.threat_models || data.threats || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No threat models defined</div>';
                return;
            }
            var rows = items.map(function(t) {
                return [
                    escapeHtml(t.name || t.title || t.id || ''),
                    escapeHtml(t.category || t.type || ''),
                    escapeHtml(t.severity || t.risk_level || ''),
                    escapeHtml(truncate(t.description || t.detail || '', 80)),
                    escapeHtml(t.status || t.mitigated ? 'mitigated' : 'open')
                ];
            });
            container.innerHTML = v2RenderTable(['Threat', 'Category', 'Severity', 'Description', 'Status'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Threat model service unavailable</div>';
        });
}

function fetchCompRules() {
    var container = document.getElementById('compRulesContainer');
    fetch('/api/v3/compliance/rules')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.rules || data.policies || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No compliance rules configured</div>';
                return;
            }
            var rows = items.map(function(r) {
                var enforced = r.enforced || r.active || false;
                var statusHtml = enforced ?
                    '<span style="color:var(--accent-emerald);font-weight:600">Enforced</span>' :
                    '<span style="color:var(--accent-amber);font-weight:600">Advisory</span>';
                return [
                    escapeHtml(r.rule_id || r.id || ''),
                    escapeHtml(r.name || r.title || ''),
                    escapeHtml(truncate(r.description || r.detail || '', 80)),
                    escapeHtml(r.category || r.scope || ''),
                    statusHtml
                ];
            });
            container.innerHTML = v2RenderTable(['Rule ID', 'Name', 'Description', 'Category', 'Status'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Compliance rules service unavailable</div>';
        });
}

function fetchCompStatus() {
    var container = document.getElementById('compStatusContainer');
    fetch('/api/v3/compliance/status')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.status || data.checks || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No compliance status data</div>';
                return;
            }
            var rows = items.map(function(s) {
                var compliant = s.compliant || s.passed || s.status === 'compliant';
                var statusHtml = compliant ?
                    '<span style="color:var(--accent-emerald);font-weight:600">Compliant</span>' :
                    '<span style="color:var(--accent-rose);font-weight:600">Non-Compliant</span>';
                return [
                    escapeHtml(s.rule || s.rule_id || s.name || ''),
                    escapeHtml(s.area || s.scope || ''),
                    statusHtml,
                    escapeHtml(truncate(s.details || s.message || '', 60)),
                    s.checked_at ? timeAgo(s.checked_at) : (s.last_check ? timeAgo(s.last_check) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Rule', 'Area', 'Status', 'Details', 'Checked'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Compliance status service unavailable</div>';
        });
}

function fetchCompUnmitigated() {
    var container = document.getElementById('compUnmitigatedContainer');
    fetch('/api/v3/compliance/threat-models/unmitigated')
        .then(function(r) {
            if (r.status === 503 || r.status === 404) return [];
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var items = Array.isArray(data) ? data : (data.threats || data.unmitigated || []);
            if (!items.length) {
                container.innerHTML = '<div class="intel-empty">No unmitigated threats &mdash; all clear</div>';
                return;
            }
            var rows = items.map(function(t) {
                var severity = (t.severity || t.risk_level || 'medium').toLowerCase();
                var sevColor = severity === 'critical' || severity === 'high' ? 'var(--accent-rose)' : (severity === 'medium' ? 'var(--accent-amber)' : 'var(--accent-emerald)');
                return [
                    escapeHtml(t.name || t.title || t.id || ''),
                    '<span style="font-weight:600;color:' + sevColor + '">' + escapeHtml(t.severity || t.risk_level || '') + '</span>',
                    escapeHtml(truncate(t.description || t.detail || '', 80)),
                    escapeHtml(t.owner || t.assignee || 'unassigned'),
                    t.identified_at ? timeAgo(t.identified_at) : (t.created_at ? timeAgo(t.created_at) : '')
                ];
            });
            container.innerHTML = v2RenderTable(['Threat', 'Severity', 'Description', 'Owner', 'Identified'], rows);
        })
        .catch(function() {
            container.innerHTML = '<div class="intel-empty">Unmitigated threats service unavailable</div>';
        });
}

        // Run onboarding check
checkOnboarding();
