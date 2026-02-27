const form = document.getElementById('compare-form');
const queryInput = document.getElementById('query');
const offersBody = document.getElementById('offers-body');
const coveragePill = document.getElementById('coverage-pill');
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
  if (!query) {
    return;
  }

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
  coveragePill.textContent = 'Scanning';
  coveragePill.className = 'coverage-pill neutral';
  findingLabel.textContent = 'Analyzing';
  findingLabel.className = 'finding-pill neutral';
  offersBody.innerHTML = '<tr class="empty-row"><td colspan="6">Scanning product pages and matching offers...</td></tr>';
  warningsList.innerHTML = '<li>Working through Tavily discovery, page extraction, and same-product matching.</li>';
}

function renderCompareResponse(payload) {
  renderPlatformStatuses(payload.platform_statuses || []);
  renderOffers(payload.offers || [], payload.finding);
  renderFinding(payload.finding, payload.coverage_status);
  renderWarnings(payload.warnings || []);
}

function renderPlatformStatuses(statuses) {
  document.querySelectorAll('.status-card').forEach((node) => {
    const platform = node.dataset.platform;
    const status = statuses.find((item) => item.platform === platform);
    if (!status) {
      node.className = 'status-card missing';
      node.querySelector('.status-label').textContent = 'Missing';
      return;
    }
    node.className = `status-card ${status.status}`;
    node.querySelector('.status-label').textContent = status.note || status.status;
  });
}

function renderOffers(offers, finding) {
  if (!offers.length) {
    offersBody.innerHTML = '<tr class="empty-row"><td colspan="6">No priced offers were available for comparison.</td></tr>';
    return;
  }

  const lowest = finding ? finding.lowest_platform : '';
  const highest = finding ? finding.highest_platform : '';
  offersBody.innerHTML = offers.map((offer) => {
    const tags = [];
    if (offer.platform === lowest) tags.push('<span class="platform-tag lowest">Lowest</span>');
    if (offer.platform === highest) tags.push('<span class="platform-tag highest">Highest</span>');
    const promo = offer.promo_text ? `<span class="promo-badge">Promo-heavy</span>${escapeHtml(offer.promo_text)}` : 'No visible promo';
    return `
      <tr>
        <td>${escapeHtml(offer.platform)}<div class="product-meta">${escapeHtml(offer.source_domain || '')}</div></td>
        <td>
          <div class="product-title">${escapeHtml(offer.title)}</div>
          <div class="product-meta">${escapeHtml(offer.brand)} ${offer.model ? '&middot; ' + escapeHtml(offer.model) : ''}</div>
          ${tags.join('')}
        </td>
        <td class="price-cell">
          <div class="price-stack">
            <span class="price-main">${escapeHtml(offer.currency)} ${Number(offer.price).toFixed(2)}</span>
            <a href="${escapeAttr(offer.url)}" target="_blank" rel="noreferrer">Open page</a>
          </div>
        </td>
        <td>${promo}</td>
        <td>${escapeHtml(offer.availability || 'unknown')}</td>
        <td>${Math.round((offer.match_confidence || 0) * 100)}%</td>
      </tr>
    `;
  }).join('');
}

function renderFinding(finding, coverageStatus) {
  coveragePill.textContent = coverageStatus || 'unknown';
  coveragePill.className = `coverage-pill ${coverageStatus || 'neutral'}`;
  if (!finding) {
    findingLabel.textContent = 'No finding';
    findingLabel.className = 'finding-pill neutral';
    spreadValue.textContent = '-';
    findingReasoning.textContent = 'No confident same-product cluster was found for the current query.';
    findingClaim.textContent = 'This tool only compares public listed prices after it has matched the same product across platforms.';
    findingNotes.textContent = 'Try a more specific model number or storage variant.';
    return;
  }
  findingLabel.textContent = finding.label;
  findingLabel.className = `finding-pill ${finding.label}`;
  spreadValue.textContent = `${Number(finding.spread_percent).toFixed(2)}%`;
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
      <div class="history-meta">${escapeHtml(item.generated_at)} · ${escapeHtml(item.coverage_status)} · ${escapeHtml(item.label)}</div>
      <div>Spread: ${Number(item.spread_percent).toFixed(2)}% · Confidence: ${Math.round((item.confidence || 0) * 100)}%</div>
      <div>${escapeHtml((item.platforms || []).join(' / '))}</div>
    </article>
  `).join('');
}

function renderError(message) {
  offersBody.innerHTML = `<tr class="empty-row"><td colspan="6">${escapeHtml(message)}</td></tr>`;
  warningsList.innerHTML = `<li>${escapeHtml(message)}</li>`;
  findingLabel.textContent = 'Error';
  findingLabel.className = 'finding-pill critical';
  spreadValue.textContent = '-';
  findingReasoning.textContent = 'The comparison request did not complete successfully.';
  findingClaim.textContent = 'Check the backend configuration for Tavily, Gemini, and the supported retail domains.';
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
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text.slice(0, 300) };
  }
}
