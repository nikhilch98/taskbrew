/* ========== Project Management ========== */

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

/* ========== Constants ========== */
const ROLE_COLORS = {
    pm: '#3b82f6',
    architect: '#8b5cf6',
    coder: '#f59e0b',
    tester: '#10b981',
    reviewer: '#ec4899'
};

const STATUS_COLORS = {
    completed: '#10b981',
    failed: '#f43f5e',
    blocked: '#f59e0b',
    pending: '#3b82f6',
    in_progress: '#06b6d4'
};

const MODEL_COLORS = [
    '#6366f1', '#8b5cf6', '#06b6d4', '#10b981',
    '#f59e0b', '#f43f5e', '#3b82f6', '#ec4899'
];

/* ========== State ========== */
let currentRange = 'today';
let currentGranularity = 'hour';
let charts = {};
let wsRefreshTimer = null;
let liveRefreshInterval = null;

/* ========== Format Helpers ========== */
function formatCost(v) {
    if (v == null || isNaN(v)) return '$0.00';
    return '$' + Number(v).toFixed(2);
}

function formatTokens(v) {
    if (v == null || isNaN(v)) return '0';
    v = Number(v);
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toString();
}

function formatDuration(ms) {
    if (ms == null || isNaN(ms)) return '--';
    ms = Number(ms);
    if (ms <= 0) return '--';
    var mins = ms / 60000;
    if (mins < 1) return (ms / 1000).toFixed(0) + 's';
    return mins.toFixed(1) + 'm';
}

function formatBucket(bucket) {
    if (!bucket) return '';
    try {
        var d = new Date(bucket);
        if (currentGranularity === 'minute') return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        if (currentGranularity === 'hour') return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    } catch (e) {
        return bucket;
    }
}

function timeAgo(dateStr) {
    if (!dateStr) return '--';
    try {
        var diff = Date.now() - new Date(dateStr).getTime();
        var mins = Math.floor(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return mins + 'm ago';
        var hrs = Math.floor(mins / 60);
        if (hrs < 24) return hrs + 'h ago';
        return Math.floor(hrs / 24) + 'd ago';
    } catch (e) {
        return '--';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function roleFromAgentId(agentId) {
    if (!agentId) return 'unknown';
    var idx = agentId.indexOf('-');
    return idx > 0 ? agentId.substring(0, idx) : agentId;
}

/* ========== Chart.js Defaults ========== */
Chart.defaults.color = '#8b93a7';
Chart.defaults.borderColor = 'rgba(99, 102, 241, 0.08)';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.plugins.legend.labels.usePointStyle = true;
Chart.defaults.plugins.legend.labels.pointStyleWidth = 10;
Chart.defaults.plugins.legend.labels.padding = 16;
Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(8, 12, 24, 0.95)';
Chart.defaults.plugins.tooltip.borderColor = 'rgba(99, 102, 241, 0.2)';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.cornerRadius = 8;
Chart.defaults.plugins.tooltip.padding = 10;
Chart.defaults.plugins.tooltip.titleFont = { weight: '600', size: 12 };
Chart.defaults.plugins.tooltip.bodyFont = { size: 12 };

function baseScaleOpts() {
    return {
        grid: { color: 'rgba(99, 102, 241, 0.08)', drawBorder: false },
        ticks: { color: '#8b93a7', font: { size: 10 }, maxRotation: 0 }
    };
}

/* ========== Chart Creators ========== */
function createChart(id, config) {
    if (charts[id]) {
        charts[id].data = config.data;
        charts[id].options = config.options;
        charts[id].update('none');
        return charts[id];
    }
    var ctx = document.getElementById(id).getContext('2d');
    charts[id] = new Chart(ctx, config);
    return charts[id];
}

function buildCostChart(data) {
    var usage = data.usage || [];
    var modelMap = {};
    usage.forEach(function(r) {
        var m = r.model || 'unknown';
        if (!modelMap[m]) modelMap[m] = {};
        modelMap[m][r.bucket] = (modelMap[m][r.bucket] || 0) + (r.cost || 0);
    });

    var allBuckets = [];
    usage.forEach(function(r) {
        if (allBuckets.indexOf(r.bucket) === -1) allBuckets.push(r.bucket);
    });
    allBuckets.sort();

    var models = Object.keys(modelMap);
    var datasets = models.map(function(m, i) {
        var color = MODEL_COLORS[i % MODEL_COLORS.length];
        return {
            label: m,
            data: allBuckets.map(function(b) { return modelMap[m][b] || 0; }),
            fill: true,
            backgroundColor: color + '20',
            borderColor: color,
            borderWidth: 2,
            tension: 0.4,
            pointRadius: 2,
            pointHoverRadius: 5
        };
    });

    createChart('chartCost', {
        type: 'line',
        data: {
            labels: allBuckets.map(formatBucket),
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: baseScaleOpts(),
                y: Object.assign(baseScaleOpts(), {
                    ticks: { color: '#8b93a7', font: { size: 10 }, callback: function(v) { return '$' + v.toFixed(2); } }
                })
            },
            plugins: {
                legend: { position: 'top', align: 'end' },
                tooltip: {
                    callbacks: { label: function(ctx) { return ctx.dataset.label + ': ' + formatCost(ctx.parsed.y); } }
                }
            }
        }
    });
}

function buildTokenChart(data) {
    var usage = data.usage || [];
    var bucketMap = {};
    usage.forEach(function(r) {
        if (!bucketMap[r.bucket]) bucketMap[r.bucket] = { input: 0, output: 0 };
        bucketMap[r.bucket].input += (r.input_tokens || 0);
        bucketMap[r.bucket].output += (r.output_tokens || 0);
    });

    var buckets = Object.keys(bucketMap).sort();

    createChart('chartTokens', {
        type: 'bar',
        data: {
            labels: buckets.map(formatBucket),
            datasets: [
                {
                    label: 'Input Tokens',
                    data: buckets.map(function(b) { return bucketMap[b].input; }),
                    backgroundColor: '#3b82f6cc',
                    borderRadius: 6,
                    borderSkipped: false
                },
                {
                    label: 'Output Tokens',
                    data: buckets.map(function(b) { return bucketMap[b].output; }),
                    backgroundColor: '#8b5cf6cc',
                    borderRadius: 6,
                    borderSkipped: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: Object.assign(baseScaleOpts(), { stacked: true }),
                y: Object.assign(baseScaleOpts(), {
                    stacked: true,
                    ticks: { color: '#8b93a7', font: { size: 10 }, callback: function(v) { return formatTokens(v); } }
                })
            },
            plugins: {
                legend: { position: 'top', align: 'end' },
                tooltip: {
                    callbacks: { label: function(ctx) { return ctx.dataset.label + ': ' + formatTokens(ctx.parsed.y); } }
                }
            }
        }
    });
}

function buildThroughputChart(data) {
    var tasks = data.tasks || [];
    var bucketMap = {};
    tasks.forEach(function(r) {
        if (r.status === 'completed') {
            bucketMap[r.bucket] = (bucketMap[r.bucket] || 0) + (r.count || 0);
        }
    });

    var buckets = Object.keys(bucketMap).sort();

    createChart('chartThroughput', {
        type: 'line',
        data: {
            labels: buckets.map(formatBucket),
            datasets: [{
                label: 'Completed',
                data: buckets.map(function(b) { return bucketMap[b]; }),
                borderColor: '#10b981',
                backgroundColor: '#10b98120',
                fill: true,
                tension: 0.4,
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 6,
                pointBackgroundColor: '#10b981'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: baseScaleOpts(),
                y: Object.assign(baseScaleOpts(), {
                    beginAtZero: true,
                    ticks: { color: '#8b93a7', font: { size: 10 }, precision: 0 }
                })
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

function buildStatusChart(data) {
    var totals = data.status_totals || {};
    var labels = [];
    var values = [];
    var colors = [];

    var order = ['completed', 'failed', 'blocked', 'pending', 'in_progress'];
    order.forEach(function(s) {
        if (totals[s] && totals[s] > 0) {
            labels.push(s.replace('_', ' '));
            values.push(totals[s]);
            colors.push(STATUS_COLORS[s] || '#8b93a7');
        }
    });

    if (labels.length === 0) {
        labels = ['No data'];
        values = [1];
        colors = ['rgba(99, 102, 241, 0.1)'];
    }

    createChart('chartStatus', {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderWidth: 0,
                hoverOffset: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '65%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { padding: 12, font: { size: 11 } }
                },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            var total = ctx.dataset.data.reduce(function(a, b) { return a + b; }, 0);
                            var pct = total > 0 ? ((ctx.parsed / total) * 100).toFixed(1) : 0;
                            return ctx.label + ': ' + ctx.parsed + ' (' + pct + '%)';
                        }
                    }
                }
            }
        }
    });
}

function buildPipelineChart(roleData) {
    var taskStats = roleData.task_stats || [];
    var roles = [];
    var totals = [];
    var colors = [];

    taskStats.forEach(function(r) {
        var role = (r.role || 'unknown').toLowerCase();
        roles.push(role);
        totals.push(r.total || 0);
        colors.push(ROLE_COLORS[role] || '#8b93a7');
    });

    createChart('chartPipeline', {
        type: 'bar',
        data: {
            labels: roles.map(function(r) { return r.charAt(0).toUpperCase() + r.slice(1); }),
            datasets: [{
                label: 'Total Tasks',
                data: totals,
                backgroundColor: colors.map(function(c) { return c + 'cc'; }),
                borderColor: colors,
                borderWidth: 1,
                borderRadius: 6,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            scales: {
                x: Object.assign(baseScaleOpts(), {
                    beginAtZero: true,
                    ticks: { color: '#8b93a7', font: { size: 10 }, precision: 0 }
                }),
                y: baseScaleOpts()
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

function buildRoleSuccessChart(roleData) {
    var taskStats = roleData.task_stats || [];
    var roles = [];
    var rates = [];
    var colors = [];

    taskStats.forEach(function(r) {
        var role = (r.role || 'unknown').toLowerCase();
        var total = (r.completed || 0) + (r.failed || 0);
        var rate = total > 0 ? ((r.completed || 0) / total) * 100 : 0;
        roles.push(role);
        rates.push(Math.round(rate * 10) / 10);
        colors.push(ROLE_COLORS[role] || '#8b93a7');
    });

    createChart('chartRoleSuccess', {
        type: 'bar',
        data: {
            labels: roles.map(function(r) { return r.charAt(0).toUpperCase() + r.slice(1); }),
            datasets: [{
                label: 'Success %',
                data: rates,
                backgroundColor: colors.map(function(c) { return c + 'cc'; }),
                borderColor: colors,
                borderWidth: 1,
                borderRadius: 6,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            scales: {
                x: Object.assign(baseScaleOpts(), {
                    beginAtZero: true,
                    max: 100,
                    ticks: { color: '#8b93a7', font: { size: 10 }, callback: function(v) { return v + '%'; } }
                }),
                y: baseScaleOpts()
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: { label: function(ctx) { return ctx.parsed.x.toFixed(1) + '%'; } }
                }
            }
        }
    });
}

function buildRoleCostChart(roleData) {
    var costStats = roleData.cost_stats || [];
    var roles = [];
    var costs = [];
    var colors = [];

    costStats.forEach(function(r) {
        var role = (r.role || 'unknown').toLowerCase();
        roles.push(role);
        costs.push(r.cost || 0);
        colors.push(ROLE_COLORS[role] || '#8b93a7');
    });

    createChart('chartRoleCost', {
        type: 'bar',
        data: {
            labels: roles.map(function(r) { return r.charAt(0).toUpperCase() + r.slice(1); }),
            datasets: [{
                label: 'Cost',
                data: costs,
                backgroundColor: colors.map(function(c) { return c + 'cc'; }),
                borderColor: colors,
                borderWidth: 1,
                borderRadius: 6,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: baseScaleOpts(),
                y: Object.assign(baseScaleOpts(), {
                    beginAtZero: true,
                    ticks: { color: '#8b93a7', font: { size: 10 }, callback: function(v) { return '$' + v.toFixed(2); } }
                })
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: { label: function(ctx) { return formatCost(ctx.parsed.y); } }
                }
            }
        }
    });
}

function buildLeaderboard(agents) {
    var tbody = document.getElementById('leaderboardBody');
    if (!agents || agents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:40px;">No agent data</td></tr>';
        return;
    }

    var html = '';
    agents.forEach(function(a, i) {
        var rankClass = i === 0 ? 'gold' : (i === 1 ? 'silver' : (i === 2 ? 'bronze' : ''));
        var role = roleFromAgentId(a.agent_id);
        html += '<tr>' +
            '<td><span class="rank-badge ' + rankClass + '">' + (i + 1) + '</span></td>' +
            '<td><span class="role-tag ' + escapeHtml(role) + '">' + escapeHtml(role) + '</span> ' + escapeHtml(a.agent_id || '--') + '</td>' +
            '<td>' + (a.tasks_completed || 0) + '</td>' +
            '<td>' + formatCost(a.total_cost) + '</td>' +
            '<td>' + formatDuration(a.avg_duration_ms) + '</td>' +
            '</tr>';
    });
    tbody.innerHTML = html;
}

function buildFailureRateChart(data) {
    var tasks = data.tasks || [];
    var bucketMap = {};
    tasks.forEach(function(r) {
        if (r.status === 'failed') {
            bucketMap[r.bucket] = (bucketMap[r.bucket] || 0) + (r.count || 0);
        }
    });

    var buckets = Object.keys(bucketMap).sort();

    createChart('chartFailureRate', {
        type: 'line',
        data: {
            labels: buckets.map(formatBucket),
            datasets: [{
                label: 'Failures',
                data: buckets.map(function(b) { return bucketMap[b]; }),
                borderColor: '#f43f5e',
                backgroundColor: '#f43f5e20',
                fill: true,
                tension: 0.4,
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 6,
                pointBackgroundColor: '#f43f5e'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: baseScaleOpts(),
                y: Object.assign(baseScaleOpts(), {
                    beginAtZero: true,
                    ticks: { color: '#8b93a7', font: { size: 10 }, precision: 0 }
                })
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

function buildFailureRoleChart(roleData) {
    var taskStats = roleData.task_stats || [];
    var labels = [];
    var values = [];
    var colors = [];

    taskStats.forEach(function(r) {
        var failed = r.failed || 0;
        if (failed > 0) {
            var role = (r.role || 'unknown').toLowerCase();
            labels.push(role.charAt(0).toUpperCase() + role.slice(1));
            values.push(failed);
            colors.push(ROLE_COLORS[role] || '#8b93a7');
        }
    });

    if (labels.length === 0) {
        labels = ['No failures'];
        values = [1];
        colors = ['rgba(99, 102, 241, 0.1)'];
    }

    createChart('chartFailureRole', {
        type: 'pie',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderWidth: 0,
                hoverOffset: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { padding: 12, font: { size: 11 } }
                },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            return ctx.label + ': ' + ctx.parsed + ' failures';
                        }
                    }
                }
            }
        }
    });
}

function buildFailuresTable(failures) {
    var tbody = document.getElementById('failuresBody');
    if (!failures || failures.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:40px;">No recent failures</td></tr>';
        return;
    }

    var html = '';
    failures.forEach(function(f) {
        var role = (f.assigned_to || 'unknown').toLowerCase();
        var title = f.title || '--';
        if (title.length > 40) title = title.substring(0, 40) + '...';
        html += '<tr>' +
            '<td style="font-family:monospace;font-size:11px;color:var(--text-muted);">' + escapeHtml((f.id || '--').toString().substring(0, 8)) + '</td>' +
            '<td title="' + escapeHtml(f.title || '') + '">' + escapeHtml(title) + '</td>' +
            '<td><span class="role-tag ' + escapeHtml(role) + '">' + escapeHtml(role) + '</span></td>' +
            '<td>' + timeAgo(f.completed_at || f.created_at) + '</td>' +
            '</tr>';
    });
    tbody.innerHTML = html;
}

/* ========== KPI Updates ========== */
function updateKPIs(timeseriesData, roleData, agentsData, liveAgents) {
    var usage = timeseriesData.usage || [];
    var totalCost = 0;
    var totalInput = 0;
    var totalOutput = 0;
    usage.forEach(function(r) {
        totalCost += (r.cost || 0);
        totalInput += (r.input_tokens || 0);
        totalOutput += (r.output_tokens || 0);
    });

    document.getElementById('kpiCost').textContent = formatCost(totalCost);
    document.getElementById('kpiCostSub').textContent = currentRange === 'today' ? 'today' : currentRange;

    var totals = timeseriesData.status_totals || {};
    var completed = totals.completed || 0;
    var failed = totals.failed || 0;
    document.getElementById('kpiCompleted').textContent = completed;

    var total = completed + failed;
    if (total > 0) {
        document.getElementById('kpiSuccessRate').textContent = ((completed / total) * 100).toFixed(1) + '%';
    } else {
        document.getElementById('kpiSuccessRate').textContent = '--';
    }

    var avgDur = 0;
    var durCount = 0;
    var costStats = (roleData && roleData.cost_stats) || [];
    costStats.forEach(function(r) {
        if (r.avg_duration_ms && r.avg_duration_ms > 0) {
            avgDur += r.avg_duration_ms;
            durCount++;
        }
    });
    document.getElementById('kpiDuration').textContent = durCount > 0 ? formatDuration(avgDur / durCount) : '--';

    document.getElementById('kpiTokens').textContent = formatTokens(totalInput + totalOutput);

    var activeCount = 0;
    if (liveAgents && liveAgents.length) {
        liveAgents.forEach(function(a) {
            if (a.status === 'working') activeCount++;
        });
    }
    document.getElementById('kpiAgents').textContent = activeCount;
}

/* ========== Data Fetching ========== */
async function fetchJSON(url) {
    try {
        var resp = await fetch(url);
        if (!resp.ok) return null;
        return await resp.json();
    } catch (e) {
        console.error('Fetch error:', url, e);
        return null;
    }
}

async function loadAll() {
    var results = await Promise.all([
        fetchJSON('/api/metrics/timeseries?range=' + currentRange + '&granularity=' + currentGranularity),
        fetchJSON('/api/metrics/roles'),
        fetchJSON('/api/metrics/agents?top=10'),
        fetchJSON('/api/metrics/failures?limit=20'),
        fetchJSON('/api/agents')
    ]);

    var timeseries = results[0] || { usage: [], tasks: [], status_totals: {} };
    var roles = results[1] || { task_stats: [], cost_stats: [] };
    var agents = results[2] || [];
    var failures = results[3] || [];
    var liveAgents = results[4] || [];

    updateKPIs(timeseries, roles, agents, liveAgents);
    buildCostChart(timeseries);
    buildTokenChart(timeseries);
    buildThroughputChart(timeseries);
    buildStatusChart(timeseries);
    buildPipelineChart(roles);
    buildRoleSuccessChart(roles);
    buildRoleCostChart(roles);
    buildLeaderboard(agents);
    buildFailureRateChart(timeseries);
    buildFailureRoleChart(roles);
    buildFailuresTable(failures);

    // Add drill-down to charts
    if (charts['chartCost']) addChartDrilldown(charts['chartCost'], 'Cost Over Time');
    if (charts['chartTokens']) addChartDrilldown(charts['chartTokens'], 'Token Usage');
    if (charts['chartThroughput']) addChartDrilldown(charts['chartThroughput'], 'Task Throughput');
    if (charts['chartStatus']) addChartDrilldown(charts['chartStatus'], 'Status Breakdown');
    if (charts['chartFailureRate']) addChartDrilldown(charts['chartFailureRate'], 'Failure Rate');
}

async function loadTimeseries(customFrom, customTo) {
    var url = '/api/metrics/timeseries?range=' + currentRange + '&granularity=' + currentGranularity;
    if (customFrom && customTo) {
        url += '&from=' + encodeURIComponent(customFrom) + '&to=' + encodeURIComponent(customTo);
    }

    var results = await Promise.all([
        fetchJSON(url),
        fetchJSON('/api/agents')
    ]);

    var timeseries = results[0] || { usage: [], tasks: [], status_totals: {} };
    var liveAgents = results[1] || [];

    var roleData = await fetchJSON('/api/metrics/roles');
    roleData = roleData || { task_stats: [], cost_stats: [] };

    updateKPIs(timeseries, roleData, null, liveAgents);
    buildCostChart(timeseries);
    buildTokenChart(timeseries);
    buildThroughputChart(timeseries);
    buildStatusChart(timeseries);
    buildFailureRateChart(timeseries);
}

function fetchMetrics(range, customFrom, customTo) {
    var granularityMap = {
        'live': 'minute', '1h': 'minute', '6h': 'hour',
        'today': 'hour', '7d': 'day', '30d': 'day', 'custom': 'hour'
    };
    currentRange = range;
    currentGranularity = granularityMap[range] || 'hour';
    loadTimeseries(customFrom, customTo);
}

/* ========== Custom Date Range ========== */
function handleTimeRangeChange() {
    var val = document.getElementById('timeRangeSelect').value;
    document.getElementById('customDateInputs').style.display = val === 'custom' ? 'flex' : 'none';

    // Clear existing auto-refresh
    if (liveRefreshInterval) {
        clearInterval(liveRefreshInterval);
        liveRefreshInterval = null;
    }

    if (val === 'live') {
        fetchMetrics('live');
        liveRefreshInterval = setInterval(function() { fetchMetrics('live'); }, 10000);
    } else if (val !== 'custom') {
        fetchMetrics(val);
    }
}

function applyCustomRange() {
    var from = document.getElementById('dateFrom').value;
    var to = document.getElementById('dateTo').value;
    if (from && to) {
        fetchMetrics('custom', from, to);
    }
}

/* ========== Chart Drill-Down ========== */
function addChartDrilldown(chart, chartType) {
    chart.options.onClick = async function(event, elements) {
        if (elements.length === 0) return;
        var element = elements[0];
        var label = chart.data.labels[element.index];
        showDrilldownPopup(chartType, label, element);
    };
    chart.update('none');
}

function showDrilldownPopup(type, label, element) {
    var existing = document.getElementById('drilldownPopup');
    if (existing) existing.remove();

    var popup = document.createElement('div');
    popup.id = 'drilldownPopup';
    popup.style.cssText = 'position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: var(--bg-secondary); border: 1px solid var(--border-subtle); border-radius: var(--radius-lg); padding: 24px; max-width: 500px; width: 90%; z-index: 300; box-shadow: 0 16px 64px rgba(0,0,0,0.5);';

    popup.innerHTML = '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">' +
        '<h3 style="font-size: 16px;">Details: ' + escapeHtml(label) + '</h3>' +
        '<button onclick="this.parentElement.parentElement.remove()" style="background: none; border: none; color: var(--text-secondary); font-size: 18px; cursor: pointer;">&#10005;</button>' +
        '</div>' +
        '<div id="drilldownContent" style="color: var(--text-secondary); font-size: 13px;">' +
        '<div style="display: grid; gap: 8px;">' +
        '<div style="display: flex; justify-content: space-between; padding: 8px; background: var(--bg-card); border-radius: 6px;">' +
        '<span>Time Bucket</span><span style="color: var(--text-primary); font-weight: 500;">' + escapeHtml(label) + '</span>' +
        '</div>' +
        '<div style="display: flex; justify-content: space-between; padding: 8px; background: var(--bg-card); border-radius: 6px;">' +
        '<span>Chart</span><span style="color: var(--text-primary); font-weight: 500;">' + escapeHtml(type) + '</span>' +
        '</div>' +
        '</div>' +
        '</div>';

    document.body.appendChild(popup);
}

/* ========== Task Timeline / Gantt View ========== */
async function loadGanttView() {
    var groupFilter = document.getElementById('ganttGroupFilter').value;
    var url = '/api/board';
    if (groupFilter) url += '?group_id=' + groupFilter;

    try {
        var resp = await fetch(url);
        var board = await resp.json();

        // Flatten all tasks
        var tasks = [];
        for (var status in board) {
            if (board.hasOwnProperty(status)) {
                var taskList = board[status];
                if (Array.isArray(taskList)) {
                    for (var i = 0; i < taskList.length; i++) {
                        var t = taskList[i];
                        if (t.created_at) {
                            var task = {};
                            for (var key in t) { if (t.hasOwnProperty(key)) task[key] = t[key]; }
                            task.status = status;
                            tasks.push(task);
                        }
                    }
                }
            }
        }

        if (tasks.length === 0) {
            document.getElementById('ganttContainer').innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 40px;">No tasks to display</div>';
            return;
        }

        // Find time range
        var times = tasks.map(function(t) { return new Date(t.created_at).getTime(); });
        var completedTimes = tasks.filter(function(t) { return t.completed_at; }).map(function(t) { return new Date(t.completed_at).getTime(); });
        var minTime = Math.min.apply(null, times);
        var allMaxTimes = times.concat(completedTimes).concat([Date.now()]);
        var maxTime = Math.max.apply(null, allMaxTimes);
        var totalDuration = maxTime - minTime || 1;

        var statusColors = {
            'pending': 'var(--accent-amber)',
            'in_progress': 'var(--accent-blue)',
            'blocked': 'var(--accent-rose)',
            'completed': 'var(--accent-emerald)',
            'failed': 'var(--accent-rose)',
            'cancelled': 'var(--text-muted)'
        };

        // Sort by created_at
        tasks.sort(function(a, b) { return new Date(a.created_at) - new Date(b.created_at); });

        var html = '<div style="min-width: 800px;">';

        // Time axis
        html += '<div style="display: flex; margin-bottom: 8px; padding-left: 200px; color: var(--text-muted); font-size: 11px;">';
        for (var idx = 0; idx <= 4; idx++) {
            var axisTime = new Date(minTime + (totalDuration * idx / 4));
            var align = idx === 0 ? 'left' : (idx === 4 ? 'right' : 'center');
            html += '<div style="flex: 1; text-align: ' + align + ';">' + axisTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) + '</div>';
        }
        html += '</div>';

        // Task bars (limit to 50)
        var displayTasks = tasks.slice(0, 50);
        for (var j = 0; j < displayTasks.length; j++) {
            var task = displayTasks[j];
            var start = new Date(task.created_at).getTime();
            var end = task.completed_at ? new Date(task.completed_at).getTime() : Date.now();
            var leftPct = ((start - minTime) / totalDuration * 100).toFixed(2);
            var widthPct = Math.max(0.5, ((end - start) / totalDuration * 100)).toFixed(2);
            var color = statusColors[task.status] || 'var(--text-muted)';
            var titleText = task.title || '';
            var shortTitle = titleText.length > 30 ? titleText.substring(0, 30) + '...' : titleText;

            html += '<div style="display: flex; align-items: center; height: 28px; margin-bottom: 2px;">' +
                '<div style="width: 200px; flex-shrink: 0; font-size: 11px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-right: 8px;" title="' + escapeHtml(titleText) + '">' + escapeHtml(task.id) + ': ' + escapeHtml(shortTitle) + '</div>' +
                '<div style="flex: 1; position: relative; height: 20px; background: rgba(255,255,255,0.03); border-radius: 3px;">' +
                '<div style="position: absolute; left: ' + leftPct + '%; width: ' + widthPct + '%; height: 100%; background: ' + color + '; border-radius: 3px; opacity: 0.8;" title="' + escapeHtml(titleText) + ' (' + escapeHtml(task.status) + ')"></div>' +
                '</div>' +
                '</div>';
        }

        html += '</div>';
        document.getElementById('ganttContainer').innerHTML = html;
    } catch(e) {
        console.error('Failed to load Gantt view:', e);
        document.getElementById('ganttContainer').innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 40px;">Failed to load timeline</div>';
    }
}

/* ========== Agent Health Monitor ========== */
async function loadAgentHealth() {
    try {
        var results = await Promise.all([
            fetch('/api/agents'),
            fetch('/api/metrics/roles')
        ]);
        var agents = await results[0].json();
        var perf = await results[1].json();

        var grid = document.getElementById('agentHealthGrid');

        if (!agents || agents.length === 0) {
            grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 40px;">No agents registered</div>';
            return;
        }

        var roleColors = {
            'pm': '#3b82f6', 'architect': '#8b5cf6', 'coder': '#f59e0b', 'tester': '#10b981', 'reviewer': '#ec4899'
        };

        var statusIcons = {
            'idle': '&#128994;', 'working': '&#128308;', 'paused': '&#128992;', 'stopped': '&#9898;'
        };

        grid.innerHTML = agents.map(function(agent) {
            var color = roleColors[agent.role] || '#6b7280';
            var icon = statusIcons[agent.status] || '&#9898;';
            var heartbeatAge = agent.last_heartbeat ? Math.round((Date.now() - new Date(agent.last_heartbeat).getTime()) / 1000) : null;
            var heartbeatStatus = heartbeatAge === null ? 'unknown' : heartbeatAge < 30 ? 'healthy' : heartbeatAge < 90 ? 'warning' : 'stale';
            var heartbeatColor = heartbeatStatus === 'healthy' ? 'var(--accent-emerald)' : heartbeatStatus === 'warning' ? 'var(--accent-amber)' : 'var(--accent-rose)';

            return '<div style="background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 16px; border-left: 3px solid ' + color + ';">' +
                '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">' +
                '<span style="font-weight: 600; font-size: 14px;">' + escapeHtml(agent.instance_id) + '</span>' +
                '<span>' + icon + ' ' + escapeHtml(agent.status) + '</span>' +
                '</div>' +
                '<div style="display: grid; gap: 6px; font-size: 12px; color: var(--text-secondary);">' +
                '<div style="display: flex; justify-content: space-between;">' +
                '<span>Role</span><span style="color: ' + color + '; font-weight: 500;">' + escapeHtml(agent.role) + '</span>' +
                '</div>' +
                '<div style="display: flex; justify-content: space-between;">' +
                '<span>Current Task</span><span style="color: var(--text-primary);">' + (agent.current_task ? escapeHtml(agent.current_task) : '&#8212;') + '</span>' +
                '</div>' +
                '<div style="display: flex; justify-content: space-between;">' +
                '<span>Heartbeat</span><span style="color: ' + heartbeatColor + ';">' + (heartbeatAge !== null ? heartbeatAge + 's ago' : 'N/A') + '</span>' +
                '</div>' +
                '</div>' +
                '</div>';
        }).join('');

        document.getElementById('healthLastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
    } catch(e) {
        console.error('Failed to load agent health:', e);
    }
}

/* ========== Export Data ========== */
async function exportData(format) {
    try {
        var resp = await fetch('/api/export?format=' + format);
        if (format === 'csv') {
            var blob = await resp.blob();
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'taskbrew-export-' + new Date().toISOString().slice(0, 10) + '.csv';
            a.click();
            URL.revokeObjectURL(url);
        } else {
            var data = await resp.json();
            var jsonBlob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
            var jsonUrl = URL.createObjectURL(jsonBlob);
            var jsonA = document.createElement('a');
            jsonA.href = jsonUrl;
            jsonA.download = 'taskbrew-export-' + new Date().toISOString().slice(0, 10) + '.json';
            jsonA.click();
            URL.revokeObjectURL(jsonUrl);
        }
    } catch(e) {
        console.error('Export failed:', e);
    }
}

/* ========== Theme Toggle ========== */
function toggleTheme() {
    document.body.classList.toggle('light-theme');
    var isLight = document.body.classList.contains('light-theme');
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
    document.getElementById('themeIcon').textContent = isLight ? '\u2600' : '\u263E';
}

// Restore theme on load
if (localStorage.getItem('theme') === 'light') {
    document.body.classList.add('light-theme');
    var iconEl = document.getElementById('themeIcon');
    if (iconEl) iconEl.textContent = '\u2600';
}

/* ========== WebSocket ========== */
function connectWS() {
    var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var ws = new WebSocket(protocol + '//' + location.host + '/ws');

    ws.onopen = function() {
        document.getElementById('wsDot').classList.remove('disconnected');
        document.getElementById('wsLabel').textContent = 'Connected';
    };

    ws.onclose = function() {
        document.getElementById('wsDot').classList.add('disconnected');
        document.getElementById('wsLabel').textContent = 'Disconnected';
        setTimeout(connectWS, 3000);
    };

    ws.onerror = function() {
        document.getElementById('wsDot').classList.add('disconnected');
        document.getElementById('wsLabel').textContent = 'Disconnected';
    };

    ws.onmessage = function(evt) {
        try {
            var data = JSON.parse(evt.data);
            var evtType = data.type || data.event || '';
            if (evtType === 'task.completed' || evtType === 'task.failed') {
                if (wsRefreshTimer) clearTimeout(wsRefreshTimer);
                wsRefreshTimer = setTimeout(function() {
                    loadAll();
                }, 5000);
            }
        } catch (e) {
            /* ignore parse errors */
        }
    };
}

/* ========== V2 Intelligence Metrics ========== */

function buildCostByAgentChart(data) {
    var items = Array.isArray(data) ? data : (data.agents || []);
    var labels = [];
    var values = [];
    var colors = [];
    var palette = ['#6366f1', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#f43f5e', '#3b82f6', '#ec4899'];

    items.forEach(function(item, i) {
        labels.push(item.agent_id || item.agent || ('Agent ' + (i + 1)));
        values.push(item.total_cost || item.cost || 0);
        colors.push(palette[i % palette.length]);
    });

    if (labels.length === 0) {
        labels = ['No data'];
        values = [0];
        colors = ['rgba(99, 102, 241, 0.2)'];
    }

    createChart('chartCostByAgent', {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Cost',
                data: values,
                backgroundColor: colors.map(function(c) { return c + 'cc'; }),
                borderColor: colors,
                borderWidth: 1,
                borderRadius: 6,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            scales: {
                x: Object.assign(baseScaleOpts(), {
                    beginAtZero: true,
                    ticks: { color: '#8b93a7', font: { size: 10 }, callback: function(v) { return '$' + v.toFixed(2); } }
                }),
                y: baseScaleOpts()
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: { label: function(ctx) { return formatCost(ctx.parsed.x); } }
                }
            }
        }
    });
}

function buildCostByFeatureChart(data) {
    var items = Array.isArray(data) ? data : (data.features || []);
    var labels = [];
    var values = [];
    var colors = [];
    var palette = ['#3b82f6', '#f59e0b', '#10b981', '#8b5cf6', '#f43f5e', '#06b6d4', '#ec4899', '#6366f1'];

    items.forEach(function(item, i) {
        labels.push(item.feature || item.feature_name || ('Feature ' + (i + 1)));
        values.push(item.total_cost || item.cost || 0);
        colors.push(palette[i % palette.length]);
    });

    if (labels.length === 0) {
        labels = ['No data'];
        values = [0];
        colors = ['rgba(99, 102, 241, 0.2)'];
    }

    createChart('chartCostByFeature', {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Cost',
                data: values,
                backgroundColor: colors.map(function(c) { return c + 'cc'; }),
                borderColor: colors,
                borderWidth: 1,
                borderRadius: 6,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: baseScaleOpts(),
                y: Object.assign(baseScaleOpts(), {
                    beginAtZero: true,
                    ticks: { color: '#8b93a7', font: { size: 10 }, callback: function(v) { return '$' + v.toFixed(2); } }
                })
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: { label: function(ctx) { return formatCost(ctx.parsed.y); } }
                }
            }
        }
    });
}

function buildQualityTrendsChart(data) {
    var points = Array.isArray(data) ? data : (data.data_points || data.trends || []);
    var labels = [];
    var values = [];

    points.forEach(function(p) {
        labels.push(formatBucket(p.timestamp || p.bucket || p.date || ''));
        values.push(p.value || p.quality_score || p.score || 0);
    });

    if (labels.length === 0) {
        labels = ['No data'];
        values = [0];
    }

    createChart('chartQualityTrends', {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Quality Score',
                data: values,
                borderColor: '#10b981',
                backgroundColor: '#10b98120',
                fill: true,
                tension: 0.4,
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 6,
                pointBackgroundColor: '#10b981'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: baseScaleOpts(),
                y: Object.assign(baseScaleOpts(), {
                    beginAtZero: true,
                    max: 100,
                    ticks: { color: '#8b93a7', font: { size: 10 }, callback: function(v) { return v + '%'; } }
                })
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: { label: function(ctx) { return 'Quality: ' + ctx.parsed.y.toFixed(1) + '%'; } }
                }
            }
        }
    });
}

function renderSecurityVulnerabilities(data) {
    var container = document.getElementById('secVulnSummary');
    var vulns = Array.isArray(data) ? data : (data.vulnerabilities || []);
    var severityCounts = { critical: 0, high: 0, medium: 0, low: 0 };

    vulns.forEach(function(v) {
        var sev = (v.severity || 'low').toLowerCase();
        if (severityCounts.hasOwnProperty(sev)) {
            severityCounts[sev] += (v.count || 1);
        }
    });

    var severityColors = {
        critical: 'var(--accent-rose)',
        high: 'var(--accent-amber)',
        medium: '#f59e0b',
        low: 'var(--accent-cyan)'
    };

    var total = severityCounts.critical + severityCounts.high + severityCounts.medium + severityCounts.low;

    var html = '<div style="text-align: center; margin-bottom: 16px;">' +
        '<div style="font-size: 36px; font-weight: 800; color: var(--text-primary);">' + total + '</div>' +
        '<div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px;">Total Vulnerabilities</div>' +
        '</div>' +
        '<div style="display: grid; gap: 8px;">';

    ['critical', 'high', 'medium', 'low'].forEach(function(sev) {
        html += '<div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: rgba(255,255,255,0.03); border-radius: 6px; border-left: 3px solid ' + severityColors[sev] + ';">' +
            '<span style="font-size: 12px; color: var(--text-secondary); text-transform: capitalize;">' + sev + '</span>' +
            '<span style="font-size: 14px; font-weight: 700; color: var(--text-primary);">' + severityCounts[sev] + '</span>' +
            '</div>';
    });

    html += '</div>';
    container.innerHTML = html;
}

function renderSecretDetections(data) {
    var container = document.getElementById('secSecretSummary');
    var secrets = Array.isArray(data) ? data : (data.secrets || data.detections || []);
    var count = typeof data === 'object' && data.total !== undefined ? data.total : secrets.length;

    var html = '<div style="text-align: center; margin-bottom: 16px;">' +
        '<div style="font-size: 36px; font-weight: 800; color: ' + (count > 0 ? 'var(--accent-rose)' : 'var(--accent-emerald)') + ';">' + count + '</div>' +
        '<div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px;">Secrets Detected</div>' +
        '</div>';

    if (secrets.length > 0) {
        html += '<div style="display: grid; gap: 6px; max-height: 180px; overflow-y: auto;">';
        secrets.slice(0, 10).forEach(function(s) {
            var file = s.file || s.path || 'unknown';
            var type = s.type || s.pattern || 'secret';
            html += '<div style="padding: 6px 10px; background: rgba(244,63,94,0.06); border-radius: 6px; font-size: 11px; color: var(--text-secondary);">' +
                '<span style="color: var(--accent-rose); font-weight: 600;">' + escapeHtml(type) + '</span> in ' + escapeHtml(file) +
                '</div>';
        });
        html += '</div>';
    } else {
        html += '<div style="text-align: center; color: var(--accent-emerald); font-size: 12px; font-weight: 500;">No secrets detected</div>';
    }

    container.innerHTML = html;
}

function renderSastFindings(data) {
    var container = document.getElementById('secSastSummary');
    var findings = Array.isArray(data) ? data : (data.findings || []);
    var count = typeof data === 'object' && data.total !== undefined ? data.total : findings.length;

    var html = '<div style="text-align: center; margin-bottom: 16px;">' +
        '<div style="font-size: 36px; font-weight: 800; color: ' + (count > 0 ? 'var(--accent-amber)' : 'var(--accent-emerald)') + ';">' + count + '</div>' +
        '<div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px;">SAST Findings</div>' +
        '</div>';

    if (findings.length > 0) {
        html += '<div style="display: grid; gap: 6px; max-height: 180px; overflow-y: auto;">';
        findings.slice(0, 10).forEach(function(f) {
            var rule = f.rule || f.rule_id || 'finding';
            var sev = f.severity || 'medium';
            var sevColor = sev === 'critical' ? 'var(--accent-rose)' : sev === 'high' ? 'var(--accent-amber)' : 'var(--accent-cyan)';
            html += '<div style="padding: 6px 10px; background: rgba(255,255,255,0.03); border-radius: 6px; font-size: 11px; color: var(--text-secondary); display: flex; justify-content: space-between;">' +
                '<span>' + escapeHtml(rule) + '</span>' +
                '<span style="color: ' + sevColor + '; font-weight: 600; text-transform: capitalize;">' + escapeHtml(sev) + '</span>' +
                '</div>';
        });
        html += '</div>';
    } else {
        html += '<div style="text-align: center; color: var(--accent-emerald); font-size: 12px; font-weight: 500;">No SAST findings</div>';
    }

    container.innerHTML = html;
}

function renderBehaviorMetrics(data) {
    var tbody = document.getElementById('behaviorMetricsBody');
    var metrics = Array.isArray(data) ? data : (data.metrics || []);

    if (!metrics || metrics.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--text-muted);padding:40px;">No behavior metrics available</td></tr>';
        return;
    }

    var html = '';
    metrics.forEach(function(m) {
        var name = m.metric_name || m.name || '--';
        var value = m.value !== undefined ? m.value : '--';
        var trend = m.trend || m.direction || 'stable';
        var trendColor = trend === 'up' || trend === 'improving' ? 'var(--accent-emerald)' :
            trend === 'down' || trend === 'declining' ? 'var(--accent-rose)' : 'var(--text-muted)';
        var trendIcon = trend === 'up' || trend === 'improving' ? '&#9650;' :
            trend === 'down' || trend === 'declining' ? '&#9660;' : '&#9644;';

        html += '<tr>' +
            '<td style="font-weight: 500;">' + escapeHtml(name) + '</td>' +
            '<td>' + (typeof value === 'number' ? value.toFixed(2) : escapeHtml(String(value))) + '</td>' +
            '<td style="color: ' + trendColor + ';">' + trendIcon + ' ' + escapeHtml(trend) + '</td>' +
            '</tr>';
    });
    tbody.innerHTML = html;
}

async function loadV2Metrics() {
    var results = await Promise.all([
        fetchJSON('/api/v2/observability/costs/by-agent'),
        fetchJSON('/api/v2/observability/costs/by-feature'),
        fetchJSON('/api/v2/observability/trends?metric_name=quality_score'),
        fetchJSON('/api/v2/security/vulnerabilities'),
        fetchJSON('/api/v2/security/secrets'),
        fetchJSON('/api/v2/security/sast'),
        fetchJSON('/api/v2/observability/behavior-metrics?agent_role=coder')
    ]);

    var costByAgent = results[0] || [];
    var costByFeature = results[1] || [];
    var qualityTrends = results[2] || [];
    var vulns = results[3] || [];
    var secrets = results[4] || [];
    var sast = results[5] || [];
    var behavior = results[6] || [];

    buildCostByAgentChart(costByAgent);
    buildCostByFeatureChart(costByFeature);
    buildQualityTrendsChart(qualityTrends);
    renderSecurityVulnerabilities(vulns);
    renderSecretDetections(secrets);
    renderSastFindings(sast);
    renderBehaviorMetrics(behavior);
}

/* ========== Init ========== */
(async function init() {
    const hasProject = await checkProjectOrRedirect();
    if (hasProject) {
        loadAll();
        loadGanttView();
        loadAgentHealth();
        loadV2Metrics();
        connectWS();

        // Auto-refresh agent health every 10s
        setInterval(loadAgentHealth, 10000);
    }
})();
