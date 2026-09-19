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

    try {
        const res = await fetch(`${API_BASE}/dashboard`);
        const data = await res.json();
        const stages = data.applications_by_stage || {};
        document.getElementById('stat-interview').textContent = stages.INTERVIEW || 0;
        document.getElementById('stat-offer').textContent = (stages.OFFER || 0) + (stages.HIRED || 0);
        renderLastRun(data);
    } catch (e) {
        const bar = document.getElementById('last-run-bar');
        bar.textContent = 'Run status unavailable';
    }
}

function renderLastRun(data) {
    const bar = document.getElementById('last-run-bar');
    const run = data.last_run;
    const parts = [];
    if (run && run.ran_at) {
        const when = new Date(run.ran_at);
        const hh = String(when.getUTCHours()).padStart(2, '0');
        const mm = String(when.getUTCMinutes()).padStart(2, '0');
        parts.push(`Last discovery: ${formatDate(run.ran_at)} @ ${hh}:${mm} UTC`);
        parts.push(`persisted ${run.jobs_persisted || 0} jobs (${run.jobs_from_providers || 0} provider / ${run.jobs_from_search || 0} search)`);
        if ((run.provider_failures || 0) > 0) parts.push(`provider failures: ${run.provider_failures}`);
    } else {
        parts.push('No discovery run recorded yet');
    }
    const providers = data.provider_runs || [];
    if (providers.length) {
        const ok = providers.filter(p => p.status === 'ok').length;
        const failed = providers.filter(p => p.status === 'failed').length;
        const zero = providers.filter(p => p.status === 'zero_yield').length;
        parts.push(`${providers.length} provider source(s) latest: ${ok} ok / ${zero} zero-yield / ${failed} failed`);
    }
    if (data.provider_failures_total) {
        parts.push(`all-time provider failures: ${data.provider_failures_total}`);
    }
    bar.textContent = parts.join(' · ');
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
    document.getElementById('app-save').addEventListener('click', saveApplication);

    // Artifacts
    document.getElementById('upload-cv').addEventListener('click', () => document.getElementById('cv-file-input').click());
    document.getElementById('cv-file-input').addEventListener('change', (e) => uploadArtifact(e.target.files[0], 'cv'));
    document.getElementById('upload-cover-letter').addEventListener('click', () => document.getElementById('cover-letter-file-input').click());
    document.getElementById('cover-letter-file-input').addEventListener('change', (e) => uploadArtifact(e.target.files[0], 'cover_letter'));
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
    document.getElementById('app-stage').value = '';
    document.getElementById('app-notes').value = '';
    document.getElementById('app-feedback').textContent = '';
    document.getElementById('artifact-list-cv').innerHTML = '';
    document.getElementById('artifact-list-cover-letter').innerHTML = '';

    try {
        const res = await fetch(`${API_BASE}/jobs/${jobId}`);
        const job = await res.json();
        renderJobDetail(job);
        loadApplication(jobId);
        loadArtifacts(jobId);
    } catch (e) {
        document.getElementById('job-factual').innerHTML = '<dd>Failed to load job details</dd>';
        console.error('Job detail load failed:', e);
    }
}

async function loadApplication(jobId) {
    try {
        const res = await fetch(`${API_BASE}/applications/${jobId}`);
        if (res.status === 404) return;
        if (!res.ok) return;
        const app = await res.json();
        if (app.stage) document.getElementById('app-stage').value = app.stage;
        if (app.notes) document.getElementById('app-notes').value = app.notes;
    } catch (e) {
        console.error('Application load failed:', e);
    }
}

async function saveApplication() {
    if (!currentModalJobId) return;
    const stage = document.getElementById('app-stage').value;
    const notes = document.getElementById('app-notes').value.trim();
    if (!stage) {
        document.getElementById('app-feedback').textContent = 'Select a stage first';
        return;
    }
    const feedback = document.getElementById('app-feedback');
    try {
        const res = await fetch(`${API_BASE}/applications/${currentModalJobId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stage, notes })
        });
        if (!res.ok) {
            const err = await res.json();
            feedback.textContent = ((err.error && err.error.message) || 'Failed to save');
            return;
        }
        feedback.textContent = 'Saved';
        await loadDashboard();
    } catch (e) {
        feedback.textContent = 'Failed to save: ' + e.message;
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

async function loadArtifacts(jobId) {
    try {
        const res = await fetch(`${API_BASE}/jobs/${jobId}/artifacts`);
        if (!res.ok) return;
        const data = await res.json();
        renderArtifacts(data.artifacts || []);
    } catch (e) {
        console.error('Artifacts load failed:', e);
    }
}

function renderArtifacts(artifacts) {
    const cvList = document.getElementById('artifact-list-cv');
    const clList = document.getElementById('artifact-list-cover-letter');

    const cvArtifacts = artifacts.filter(a => a.artifact_type === 'cv');
    const clArtifacts = artifacts.filter(a => a.artifact_type === 'cover_letter');

    cvList.innerHTML = cvArtifacts.length ? cvArtifacts.map(a => artifactHtml(a)).join('') : '<p class="artifact-empty">No CV uploaded</p>';
    clList.innerHTML = clArtifacts.length ? clArtifacts.map(a => artifactHtml(a)).join('') : '<p class="artifact-empty">No cover letter uploaded</p>';
}

function artifactHtml(a) {
    const statusCls = a.status === 'approved' ? 'status-approved' : a.status === 'archived' ? 'status-archived' : 'status-uploaded';
    const approved = a.approved_at ? `Approved: ${formatDate(a.approved_at)}` : '';
    return `
        <div class="artifact-item" data-artifact-id="${a.id}">
            <div class="artifact-info">
                <span class="artifact-filename">${escapeHtml(a.filename)}</span>
                <span class="artifact-status ${statusCls}">${a.status.toUpperCase()}</span>
                ${approved ? `<span class="artifact-approved">${escapeHtml(approved)}</span>` : ''}
                <span class="artifact-date">Added: ${formatDate(a.created_at)}</span>
            </div>
            <div class="artifact-actions">
                <a class="btn btn-sm btn-secondary" href="${API_BASE}/artifacts/${a.id}/content" target="_blank">Preview</a>
                <a class="btn btn-sm btn-secondary" href="${API_BASE}/artifacts/${a.id}/content" download>Download</a>
                ${a.status !== 'approved' && a.status !== 'archived' ? `<button class="btn btn-sm btn-primary" onclick="approveArtifact('${a.id}')">Approve</button>` : ''}
                ${a.status !== 'archived' ? `<button class="btn btn-sm btn-warning" onclick="archiveArtifact('${a.id}')">Archive</button>` : ''}
                <button class="btn btn-sm btn-danger" onclick="deleteArtifact('${a.id}')">Delete</button>
            </div>
        </div>
    `;
}

async function uploadArtifact(file, artifactType) {
    if (!currentModalJobId || !file) return;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('artifact_type', artifactType);

    try {
        const res = await fetch(`${API_BASE}/jobs/${currentModalJobId}/artifacts`, {
            method: 'POST',
            body: formData,
        });
        if (!res.ok) {
            const err = await res.json();
            alert('Upload failed: ' + (err.error?.message || 'Unknown error'));
            return;
        }
        await loadArtifacts(currentModalJobId);
        document.getElementById('cv-file-input').value = '';
        document.getElementById('cover-letter-file-input').value = '';
    } catch (e) {
        alert('Upload failed: ' + e.message);
    }
}

async function approveArtifact(artifactId) {
    try {
        const res = await fetch(`${API_BASE}/artifacts/${artifactId}/approve`, { method: 'POST' });
        if (!res.ok) throw new Error('Failed to approve');
        await loadArtifacts(currentModalJobId);
    } catch (e) {
        alert('Approve failed: ' + e.message);
    }
}

async function archiveArtifact(artifactId) {
    try {
        const res = await fetch(`${API_BASE}/artifacts/${artifactId}/archive`, { method: 'POST' });
        if (!res.ok) throw new Error('Failed to archive');
        await loadArtifacts(currentModalJobId);
    } catch (e) {
        alert('Archive failed: ' + e.message);
    }
}

async function deleteArtifact(artifactId) {
    if (!confirm('Delete this artifact? This cannot be undone.')) return;
    try {
        const res = await fetch(`${API_BASE}/artifacts/${artifactId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Failed to delete');
        await loadArtifacts(currentModalJobId);
    } catch (e) {
        alert('Delete failed: ' + e.message);
    }
}

async function runDiscovery() {
    const btn = document.getElementById('run-discovery');
    btn.disabled = true;
    btn.textContent = 'Running...';

    const dryRun = document.getElementById('discovery-dry-run') && document.getElementById('discovery-dry-run').checked;
    const limit = document.getElementById('discovery-limit') && parseInt(document.getElementById('discovery-limit').value, 10) || 30;

    try {
        const res = await fetch(`${API_BASE}/discover`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ limit_total: limit, limit_per_track: 5, max_pages: 30, dry_run: dryRun })
        });
        const data = await res.json();
        const providerLine = (data.provider_runs || []).map(p =>
            `${p.provider} → ${p.status} (${p.candidate_jobs}/${p.hits} candidates, ${Math.round(p.latency_ms)}ms)`
        ).join('\n');
        alert(`Discovery ${data.dry_run ? '[DRY RUN — nothing persisted]' : ''} complete!\nPlanned: ${data.planned_queries}\nFound: ${data.candidates_found}\nPersisted: ${data.jobs_persisted} (${data.jobs_from_providers || 0} from providers / ${data.jobs_from_search || 0} from search)\nDuplicates: ${data.duplicates}\nErrors: ${data.fetch_errors}` + (providerLine ? '\n\nProviders:\n' + providerLine : '') + (data.fetch_error_details?.length ? '\n\n' + data.fetch_error_details.join('\n') : ''));
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