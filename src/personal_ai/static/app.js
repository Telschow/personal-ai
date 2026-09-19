// Personal Job Agent - Frontend Application

const API_BASE = '/api/job-agent';

let currentPage = 1;
const PAGE_SIZE = 50;
let currentFilters = {
    user_status: '',
    track: '',
    location: '',
    min_fit: '',
    source: '',
    analyzed: null
};
let currentSort = 'fit_score_desc';
let allTracks = new Set();
let allLocations = new Set();

// Initialize on load
document.addEventListener('DOMContentLoaded', async () => {
    await loadHealth();
    await loadDashboard();
    await loadJobs();
    setupEventListeners();
});

async function loadHealth() {
    try {
        const res = await fetch(`${API_BASE}/health`);
        const data = await res.json();
        const statusEl = document.getElementById('health-status');
        if (data.status === 'healthy') {
            statusEl.textContent = `Healthy (${data.jobs_total} jobs)`;
            statusEl.className = 'status-indicator healthy';
        } else {
            statusEl.textContent = 'Unavailable';
            statusEl.className = 'status-indicator unhealthy';
        }
    } catch (e) {
        document.getElementById('health-status').textContent = 'Error';
        document.getElementById('health-status').className = 'status-indicator unhealthy';
    }
}

async function loadDashboard() {
    try {
        const res = await fetch(`${API_BASE}/health`);
        const data = await res.json();
        if (data.jobs_by_user_status) {
            document.getElementById('stat-total').textContent = data.jobs_total || 0;
            document.getElementById('stat-new').textContent = data.jobs_by_user_status.NEW || 0;
            document.getElementById('stat-saved').textContent = data.jobs_by_user_status.SAVED || 0;
            document.getElementById('stat-rejected').textContent = data.jobs_by_user_status.REJECTED || 0;
            document.getElementById('stat-applied').textContent = data.jobs_by_user_status.APPLIED || 0;
        }
    } catch (e) {
        console.error('Dashboard load failed:', e);
    }
}

async function loadJobs() {
    const tbody = document.getElementById('jobs-tbody');
    tbody.innerHTML = '<tr><td colspan="9" class="loading">Loading jobs...</td></tr>';

    const params = new URLSearchParams({
        limit: PAGE_SIZE,
        offset: (currentPage - 1) * PAGE_SIZE,
    });

    if (currentFilters.user_status) params.append('user_status', currentFilters.user_status);
    if (currentFilters.track) params.append('track', currentFilters.track);
    if (currentFilters.location) params.append('location', currentFilters.location);
    if (currentFilters.min_fit) params.append('min_fit', currentFilters.min_fit);
    if (currentFilters.source) params.append('source', currentFilters.source);
    if (currentFilters.analyzed !== null) params.append('analyzed', currentFilters.analyzed);

    try {
        const res = await fetch(`${API_BASE}/jobs?${params}`);
        const data = await res.json();

        renderJobs(data.jobs || []);
        updatePagination(data.count || 0);
        extractFilterOptions(data.jobs || []);
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="9" class="loading">Failed to load jobs</td></tr>';
        console.error('Jobs load failed:', e);
    }
}

function renderJobs(jobs) {
    const tbody = document.getElementById('jobs-tbody');
    if (!jobs.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="loading">No jobs found</td></tr>';
        return;
    }

    tbody.innerHTML = jobs.map(job => `
        <tr data-job-id="${job.id}">
            <td class="job-title">${escapeHtml(job.title)}</td>
            <td class="job-company">${escapeHtml(job.company)}</td>
            <td>${escapeHtml(job.location || '—')}</td>
            <td>${escapeHtml(job.track || '—')}</td>
            <td>${formatFitScore(job.fit_score)}</td>
            <td><span class="status-badge ${job.user_status}">${job.user_status}</span></td>
            <td><span class="source-badge">${escapeHtml(job.source || '—')}</span></td>
            <td>${formatDate(job.discovered_at)}</td>
            <td>
                <div class="action-buttons">
                    <button class="action-btn save" onclick="openJobDetail('${job.id}')" title="View Details">View</button>
                </div>
            </td>
        </tr>
    `).join('');
}

function formatFitScore(score) {
    if (score === null || score === undefined || score === '') {
        return '<span class="fit-score na">—</span>';
    }
    const s = parseFloat(score);
    let cls = 'low';
    if (s >= 0.75) cls = 'high';
    else if (s >= 0.55) cls = 'medium';
    return `<span class="fit-score ${cls}">${(s * 100).toFixed(0)}%</span>`;
}

function formatDate(dateStr) {
    if (!dateStr) return '—';
    try {
        return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
        return dateStr.split('T')[0];
    }
}

function extractFilterOptions(jobs) {
    jobs.forEach(job => {
        if (job.track) allTracks.add(job.track);
        if (job.location) allLocations.add(job.location.split(',')[0].trim());
    });
    populateFilterSelect('filter-track', Array.from(allTracks).sort());
    populateFilterSelect('filter-location', Array.from(allLocations).sort());
}

function populateFilterSelect(selectId, options) {
    const select = document.getElementById(selectId);
    const currentValue = select.value;
    const existingOptions = Array.from(select.options).map(o => o.value);
    options.forEach(opt => {
        if (!existingOptions.includes(opt)) {
            const option = document.createElement('option');
            option.value = opt;
            option.textContent = opt;
            select.appendChild(option);
        }
    });
    select.value = currentValue;
}

function updatePagination(total) {
    const totalPages = Math.ceil(total / PAGE_SIZE);
    document.getElementById('page-info').textContent = `Page ${currentPage} of ${totalPages || 1}`;
    document.getElementById('prev-page').disabled = currentPage <= 1;
    document.getElementById('next-page').disabled = currentPage >= totalPages;
}

function setupEventListeners() {
    // Filters
    ['filter-status', 'filter-track', 'filter-location'].forEach(id => {
        document.getElementById(id).addEventListener('change', (e) => {
            currentFilters[id.replace('filter-', '')] = e.target.value;
            currentPage = 1;
            loadJobs();
        });
    });

    document.getElementById('filter-min-fit').addEventListener('input', debounce((e) => {
        currentFilters.min_fit = e.target.value ? parseFloat(e.target.value) : '';
        currentPage = 1;
        loadJobs();
    }, 300));

    // Sort
    document.getElementById('sort-by').addEventListener('change', (e) => {
        currentSort = e.target.value;
        currentPage = 1;
        loadJobs();
    });

    // Pagination
    document.getElementById('prev-page').addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            loadJobs();
        }
    });
    document.getElementById('next-page').addEventListener('click', () => {
        currentPage++;
        loadJobs();
    });

    // Discovery
    document.getElementById('run-discovery').addEventListener('click', runDiscovery);

    // Modal
    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.querySelector('.modal-overlay').addEventListener('click', closeModal);
    document.getElementById('action-save').addEventListener('click', () => updateModalJobStatus('SAVED'));
    document.getElementById('action-reject').addEventListener('click', () => updateModalJobStatus('REJECTED'));
    document.getElementById('action-applied').addEventListener('click', () => updateModalJobStatus('APPLIED'));
    document.getElementById('action-reset').addEventListener('click', () => updateModalJobStatus('NEW'));
}

let currentModalJobId = null;

async function openJobDetail(jobId) {
    currentModalJobId = jobId;
    const modal = document.getElementById('job-modal');
    modal.style.display = 'flex';

    // Show loading
    document.getElementById('modal-title').textContent = 'Loading...';
    document.getElementById('job-factual').innerHTML = '';
    document.getElementById('job-analysis').innerHTML = '';

    try {
        const res = await fetch(`${API_BASE}/jobs/${jobId}`);
        const job = await res.json();
        renderJobDetail(job);
    } catch (e) {
        document.getElementById('job-factual').innerHTML = '<dd>Failed to load job details</dd>';
        console.error('Job detail load failed:', e);
    }
}

function renderJobDetail(job) {
    document.getElementById('modal-title').textContent = `${job.title} — ${job.company}`;

    // Factual
    const factual = document.getElementById('job-factual');
    factual.innerHTML = `
        <dt>Title</dt><dd>${escapeHtml(job.title)}</dd>
        <dt>Company</dt><dd>${escapeHtml(job.company)}</dd>
        <dt>Location</dt><dd>${escapeHtml(job.location || '—')}</dd>
        <dt>Country</dt><dd>${escapeHtml(job.country || '—')}</dd>
        <dt>Remote Mode</dt><dd>${escapeHtml(job.remote_mode || '—')}</dd>
        <dt>Employment Type</dt><dd>${escapeHtml(job.employment_type || '—')}</dd>
        <dt>Source</dt><dd>${escapeHtml(job.source || '—')}</dd>
        <dt>Source Type</dt><dd>${escapeHtml(job.source_type || '—')}</dd>
        <dt>Date Posted</dt><dd>${formatDate(job.date_posted)}</dd>
        <dt>Discovered</dt><dd>${formatDate(job.discovered_at)}</dd>
        <dt>Last Seen</dt><dd>${formatDate(job.last_seen)}</dd>
        <dt>Canonical URL</dt><dd><a href="${escapeHtml(job.canonical_url || job.url)}" target="_blank">${escapeHtml(job.canonical_url || job.url)}</a></dd>
        <dt>Apply URL</dt><dd>${job.apply_url ? `<a href="${escapeHtml(job.apply_url)}" target="_blank">${escapeHtml(job.apply_url)}</a>` : '—'}</dd>
        <dt>Salary (EUR)</dt><dd>${job.salary_min_eur || job.salary_max_eur ? `${job.salary_min_eur ? job.salary_min_eur.toLocaleString() : '?'} – ${job.salary_max_eur ? job.salary_max_eur.toLocaleString() : '?'}` : 'Not specified'}</dd>
        <dt>Status</dt><dd><span class="status-badge ${job.user_status}">${job.user_status}</span></dd>
    `;

    // Update action buttons state
    updateActionButtons(job.user_status);

    // Open original link
    document.getElementById('action-open-original').href = job.canonical_url || job.url;

    // Analysis
    const analysis = document.getElementById('job-analysis');
    let html = '';

    if (job.evaluation) {
        html += `<div class="fit-score-display ${getFitClass(job.evaluation.total)}">${(job.evaluation.total * 100).toFixed(0)}%</div>`;
        html += `<p style="text-align:center; margin-bottom: 16px; color: #6b7280;">Decision: <strong>${escapeHtml(job.evaluation.decision)}</strong></p>`;
    } else if (job.fit_score !== undefined) {
        html += `<div class="fit-score-display ${getFitClass(job.fit_score)}">${(job.fit_score * 100).toFixed(0)}%</div>`;
    } else {
        html += `<div class="fit-score-display na">Not Analyzed</div>`;
    }

    if (job.career_fit) {
        const cf = job.career_fit;
        html += `
            <div class="analysis-section">
                <h4>Current Fit: ${(cf.current_fit * 100).toFixed(0)}%</h4>
            </div>
            <div class="analysis-section">
                <h4>Career Upside: ${(cf.career_upside * 100).toFixed(0)}%</h4>
            </div>
            <div class="analysis-section">
                <h4>Evidence Coverage: ${(cf.evidence_coverage * 100).toFixed(0)}%</h4>
            </div>
        `;

        if (cf.strengths?.length) {
            html += `<div class="analysis-section"><h4>Strengths</h4><ul>${cf.strengths.map(s => `<li class="strength">${escapeHtml(s)}</li>`).join('')}</ul></div>`;
        }
        if (cf.gaps?.length) {
            html += `<div class="analysis-section"><h4>Gaps</h4><ul>${cf.gaps.map(g => `<li class="gap">${escapeHtml(g)}</li>`).join('')}</ul></div>`;
        }
        if (cf.transferables?.length) {
            html += `<div class="analysis-section"><h4>Transferables</h4><ul>${cf.transferables.map(t => `<li class="transferable">${escapeHtml(t)}</li>`).join('')}</ul></div>`;
        }
        if (cf.narrative) {
            html += `<div class="analysis-section"><h4>Narrative</h4><div class="analysis-narrative">${escapeHtml(cf.narrative)}</div></div>`;
        }
    } else if (job.evaluation) {
        const ev = job.evaluation;
        if (ev.reasons?.length) {
            html += `<div class="analysis-section"><h4>Reasons</h4><ul>${ev.reasons.map(r => `<li class="generic">${escapeHtml(r)}</li>`).join('')}</ul></div>`;
        }
        if (ev.gaps?.length) {
            html += `<div class="analysis-section"><h4>Gaps</h4><ul>${ev.gaps.map(g => `<li class="gap">${escapeHtml(g)}</li>`).join('')}</ul></div>`;
        }
    }

    if (job.provenance?.length) {
        html += `<div class="analysis-section"><h4>Provenance</h4><div class="provenance-list">`;
        job.provenance.forEach(p => {
            html += `<div class="provenance-item"><strong>${escapeHtml(p.source_name || p.source_id)}</strong> — ${escapeHtml(p.discovery_method || 'unknown')} — ${escapeHtml(p.query || '—')} ${p.canonical_url ? `— <a href="${escapeHtml(p.canonical_url)}" target="_blank">canonical</a>` : ''}</div>`;
        });
        html += `</div></div>`;
    }

    analysis.innerHTML = html || '<p style="color: #6b7280;">No analysis available</p>';
}

function getFitClass(score) {
    if (score >= 0.75) return 'high';
    if (score >= 0.55) return 'medium';
    return 'low';
}

function updateActionButtons(userStatus) {
    const buttons = {
        'action-save': 'SAVED',
        'action-reject': 'REJECTED',
        'action-applied': 'APPLIED',
        'action-reset': 'NEW'
    };
    Object.entries(buttons).forEach(([id, status]) => {
        const btn = document.getElementById(id);
        if (userStatus === status) {
            btn.style.opacity = '0.5';
            btn.disabled = true;
        } else {
            btn.style.opacity = '1';
            btn.disabled = false;
        }
    });
}

async function updateModalJobStatus(newStatus) {
    if (!currentModalJobId) return;

    try {
        const res = await fetch(`${API_BASE}/jobs/${currentModalJobId}/status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_status: newStatus })
        });
        if (!res.ok) throw new Error('Failed to update');
        const job = await res.json();
        renderJobDetail(job);
        await loadDashboard();
        await loadJobs();
    } catch (e) {
        alert('Failed to update status: ' + e.message);
    }
}

function closeModal() {
    document.getElementById('job-modal').style.display = 'none';
    currentModalJobId = null;
}

async function runDiscovery() {
    const btn = document.getElementById('run-discovery');
    btn.disabled = true;
    btn.textContent = 'Running...';

    try {
        const res = await fetch(`${API_BASE}/discover`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ limit_total: 30, limit_per_track: 5, max_pages: 30 })
        });
        const data = await res.json();
        alert(`Discovery complete!\nPlanned: ${data.planned_queries}\nFound: ${data.candidates_found}\nPersisted: ${data.jobs_persisted}\nDuplicates: ${data.duplicates}\nErrors: ${data.fetch_errors}`);
        await loadHealth();
        await loadDashboard();
        await loadJobs();
    } catch (e) {
        alert('Discovery failed: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Run Discovery';
    }
}

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
        .replace(/&/g, '&')
        .replace(/</g, '<')
        .replace(/>/g, '>')
        .replace(/"/g, '"')
        .replace(/'/g, '&#039;');
}

function debounce(fn, ms) {
    let timeoutId;
    return (...args) => {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => fn(...args), ms);
    };
}