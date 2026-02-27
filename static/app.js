const form = document.getElementById('compare-form');
const queryInput = document.getElementById('query');
const purchaseOptionsBody = document.getElementById('purchase-options-body');
const scanPill = document.getElementById('scan-pill');
const clusterPill = document.getElementById('cluster-pill');
const summaryStatus = document.getElementById('summary-status');
const summaryDuration = document.getElementById('summary-duration');
const summaryScanned = document.getElementById('summary-scanned');
const summaryKept = document.getElementById('summary-kept');
const summaryCluster = document.getElementById('summary-cluster');
const sourcesList = document.getElementById('sources-list');
const clusterContent = document.getElementById('cluster-content');
const findingLabel = document.getElementById('finding-label');
const spreadValue = document.getElementById('spread-value');
const findingReasoning = document.getElementById('finding-reasoning');
const findingClaim = document.getElementById('finding-claim');
const findingNotes = document.getElementById('finding-notes');
const warningsList = document.getElementById('warnings-list');
const historyList = document.getElementById('history-list');
const sampleChips = document.querySelectorAll('.sample-chip');

sampleChips.forEach((chip) => {
  chip.addEventListener('click', () => {
    queryInput.value = chip.dataset.query || '';
    queryInput.focus();
  });
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;

  setLoadingState();
  try {
    const response = await fetch('/api/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, category: 'electronics' }),
    });
    const payload = await parseResponse(response);
    if (!response.ok) {
      throw new Error(payload.detail || 'Compare request failed.');
    }
    renderCompareResponse(payload);
    await loadHistory(query);
  } catch (error) {
    renderError(error.message || 'Compare request failed.');
  }
});

async function loadHistory(query) {
  try {
    const response = await fetch(`/api/history?query=${encodeURIComponent(query)}`);
    const payload = await parseResponse(response);
    if (!response.ok) {
      throw new Error(payload.detail || 'Could not load history.');
    }
    renderHistory(payload);
  } catch (error) {
    historyList.innerHTML = `<p class="history-empty">${escapeHtml(error.message || 'Could not load history.')}</p>`;
  }
}

function setLoadingState() {
  summaryStatus.textContent = 'Scanning';
  summaryDuration.textContent = '-';
  summaryScanned.textContent = '0';
  summaryKept.textContent = '0';
  summaryCluster.textContent = '0';
  scanPill.textContent = 'Scanning';
  scanPill.className = 'coverage-pill neutral';
  clusterPill.textContent = 'Scanning';
  clusterPill.className = 'finding-pill neutral';
  findingLabel.textContent = 'Analyzing';
  findingLabel.className = 'finding-pill neutral';
  sourcesList.innerHTML = '<span class="source-badge muted">Searching the public web...</span>';
  clusterContent.innerHTML = '<p class="body-copy">Building a strict exact-model cluster from the relevant purchase options.</p>';
  purchaseOptionsBody.innerHTML = '<tr class="empty-row"><td colspan="7">Scanning product pages, filtering conditions, and ranking relevance...</td></tr>';
  warningsList.innerHTML = '<li>Running open-web discovery, extraction, relevance ranking, and exact-model matching.</li>';
}

function renderCompareResponse(payload) {
  renderSummary(payload);
  renderPurchaseOptions(payload.purchase_options || []);
  renderCluster(payload.comparison_cluster, payload.finding);
  renderFinding(payload.finding, payload.comparison_cluster, payload.scan_status);
  renderWarnings(payload.warnings || []);
}

function renderSummary(payload) {
  summaryStatus.textContent = payload.scan_status || 'unknown';
  summaryDuration.textContent = `${Number(payload.scan_duration_ms || 0)} ms`;
  summaryScanned.textContent = String(payload.offers_scanned || 0);
  summaryKept.textContent = String(payload.offers_kept || 0);
  summaryCluster.textContent = String(payload.comparison_cluster ? payload.comparison_cluster.offer_count : 0);
  scanPill.textContent = payload.scan_status || 'unknown';
  scanPill.className = `coverage-pill ${payload.scan_status || 'neutral'}`;

  const sources = payload.sources_seen || [];
  if (!sources.length) {
    sourcesList.innerHTML = '<span class="source-badge muted">No sources were retained for this run.</span>';
    return;
  }
  sourcesList.innerHTML = sources.map((source) => `<span class="source-badge">${escapeHtml(source)}</span>`).join('');
}

function renderPurchaseOptions(options) {
  if (!options.length) {
    purchaseOptionsBody.innerHTML = '<tr class="empty-row"><td colspan="7">No relevant new purchase options cleared the filter.</td></tr>';
    return;
  }
  purchaseOptionsBody.innerHTML = options.map((option) => {
    const promo = option.promo_text ? `<span class="promo-badge">Promo</span>${escapeHtml(option.promo_text)}` : 'No visible promo';
    const condition = option.condition === 'unknown' ? 'unknown' : option.condition.replaceAll('_', ' ');
    return `
      <tr>
        <td>
          <div class="product-title">${escapeHtml(option.seller_name)}</div>
          <div class="product-meta">${escapeHtml(option.source_domain)}</div>
        </td>
        <td>
          <div class="product-title">${escapeHtml(option.title)}</div>
          <div class="product-meta">${escapeHtml(option.brand)}${option.model ? ' · ' + escapeHtml(option.model) : ''}${option.variant ? ' · ' + escapeHtml(option.variant) : ''}</div>
        </td>
        <td class="price-cell">
          <div class="price-stack">
            <span class="price-main">${escapeHtml(option.currency)} ${Number(option.price).toFixed(2)}</span>
            <a href="${escapeAttr(option.url)}" target="_blank" rel="noreferrer">Open page</a>
          </div>
        </td>
        <td>${escapeHtml(condition)}</td>
        <td>${promo}</td>
        <td>${Math.round((option.relevance_score || 0) * 100)}%</td>
        <td>${Math.round((option.match_confidence || 0) * 100)}%</td>
      </tr>
    `;
  }).join('');
}

function renderCluster(cluster, finding) {
  if (!cluster) {
    clusterPill.textContent = 'No cluster';
    clusterPill.className = 'finding-pill neutral';
    clusterContent.innerHTML = '<p class="body-copy">No strict exact-model cluster was confirmed from the current purchase options.</p>';
    return;
  }

  const eligibility = finding && finding.alert_eligible ? 'Alert eligible' : 'Not alert eligible';
  clusterPill.textContent = eligibility;
  clusterPill.className = `finding-pill ${finding && finding.alert_eligible ? finding.label : 'neutral'}`;
  const rows = cluster.offers.map((offer) => `
    <tr>
      <td>${escapeHtml(offer.seller_name)}</td>
      <td>${escapeHtml(offer.source_domain)}</td>
      <td>${escapeHtml(offer.currency)} ${Number(offer.price).toFixed(2)}</td>
      <td>${Math.round((offer.match_confidence || 0) * 100)}%</td>
    </tr>
  `).join('');
  clusterContent.innerHTML = `
    <div class="cluster-meta">
      <div><strong>${escapeHtml(cluster.brand)} ${escapeHtml(cluster.model)}</strong>${cluster.variant ? ` · ${escapeHtml(cluster.variant)}` : ''}</div>
      <div class="product-meta">${escapeHtml(cluster.match_method)} · confidence ${Math.round((cluster.confidence || 0) * 100)}% · ${cluster.offer_count} offers</div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Seller</th>
            <th>Domain</th>
            <th>Price</th>
            <th>Match</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderFinding(finding, cluster, scanStatus) {
  if (!finding) {
    findingLabel.textContent = 'No finding';
    findingLabel.className = 'finding-pill neutral';
    spreadValue.textContent = '-';
    findingReasoning.textContent = 'No exact-match cluster was available for a pricing-intel conclusion.';
    findingClaim.textContent = 'The tool can still return relevant purchase options even when the strict comparison cluster is missing.';
    findingNotes.textContent = `Scan status: ${escapeHtml(scanStatus || 'unknown')}. Try a tighter model number or storage variant.`;
    return;
  }

  findingLabel.textContent = finding.alert_eligible ? finding.label : 'No alert';
  findingLabel.className = `finding-pill ${finding.alert_eligible ? finding.label : 'neutral'}`;
  spreadValue.textContent = `${Number(finding.spread_percent || 0).toFixed(2)}%`;
  findingReasoning.textContent = finding.reasoning;
  findingClaim.textContent = finding.claim_style_text;
  findingNotes.textContent = finding.evidence_notes;
}

function renderWarnings(warnings) {
  const items = warnings.length ? warnings : ['No extra warnings.'];
  warningsList.innerHTML = items.map((item) => `<li>${escapeHtml(item)}</li>`).join('');
}

function renderHistory(items) {
  if (!items.length) {
    historyList.innerHTML = '<p class="history-empty">No prior runs for this exact query yet.</p>';
    return;
  }
  historyList.innerHTML = items.map((item) => `
    <article class="history-card">
      <strong>${escapeHtml(item.query)}</strong>
      <div class="history-meta">${escapeHtml(item.generated_at)} · ${escapeHtml(item.scan_status)} · ${escapeHtml(item.finding_label)}</div>
      <div>Offers kept: ${Number(item.offers_kept || 0)} · Cluster: ${Number(item.cluster_offer_count || 0)} · Spread: ${Number(item.spread_percent || 0).toFixed(2)}%</div>
      <div>${escapeHtml((item.top_domains || []).join(' / '))}</div>
    </article>
  `).join('');
}

function renderError(message) {
  summaryStatus.textContent = 'Error';
  scanPill.textContent = 'Error';
  scanPill.className = 'coverage-pill degraded';
  purchaseOptionsBody.innerHTML = `<tr class="empty-row"><td colspan="7">${escapeHtml(message)}</td></tr>`;
  sourcesList.innerHTML = '<span class="source-badge muted">No sources available.</span>';
  clusterPill.textContent = 'Error';
  clusterPill.className = 'finding-pill critical';
  clusterContent.innerHTML = '<p class="body-copy">The exact-match cluster could not be computed because the compare request failed.</p>';
  warningsList.innerHTML = `<li>${escapeHtml(message)}</li>`;
  findingLabel.textContent = 'Error';
  findingLabel.className = 'finding-pill critical';
  spreadValue.textContent = '-';
  findingReasoning.textContent = 'The comparison request did not complete successfully.';
  findingClaim.textContent = 'Check the backend configuration for Tavily, Gemini, and page extraction.';
  findingNotes.textContent = 'No legal or pricing conclusion should be drawn from a failed run.';
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function escapeAttr(value) {
  return escapeHtml(value);
}

async function parseResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text.slice(0, 300) };
  }
}
