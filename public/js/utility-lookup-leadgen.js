/**
 * Utility Lookup Lead Gen Tool
 * 
 * Required Backend Endpoints (add to existing Railway app):
 * 
 * POST /api/leadgen/lookup
 *   Body: { address, utilities, email?, ref_code?, ip_address }
 *   Returns: { status, utilities, searches_remaining } or { status: 'limit_exceeded' }
 * 
 * GET /api/leadgen/check-limit?email=x&ip=y
 *   Returns: { can_search, searches_remaining }
 * 
 * POST /api/leadgen/track-cta
 *   Body: { email, ref_code }
 *   Returns: { success: true }
 * 
 * GET /api/leadgen/resolve-ref?ref=abc123
 *   Returns: { email } or { error }
 */

(function() {
  'use strict';
  
  // Configuration
  const CONFIG = {
    apiBase: 'https://utility-lookup-v2-production.up.railway.app',
    ctaUrl: 'https://www.utilityprofit.com/book-a-demo-via-calendly',
    maxSearches: 5
  };
  
  // State
  let state = {
    refCode: null,
    email: null,
    searchesRemaining: CONFIG.maxSearches,
    pendingSearch: null // { address, utilities }
  };
  
  // DOM Elements
  const elements = {};
  
  // Utility icons mapping
  const utilityIcons = {
    electric: '⚡',
    gas: '🔥',
    water: '💧',
    sewer: '🚿',
    internet: '🌐',
    trash: '🗑️'
  };
  
  /**
   * Initialize the tool
   */
  function init() {
    // Cache DOM elements
    elements.form = document.getElementById('ulForm');
    elements.addressInput = document.getElementById('ulAddress');
    elements.utilitiesContainer = document.getElementById('ulUtilities');
    elements.submitBtn = document.getElementById('ulSubmit');
    elements.errorEl = document.getElementById('ulError');
    elements.resultsSection = document.getElementById('ulResults');
    elements.resultsAddress = document.getElementById('ulResultsAddress');
    elements.providerList = document.getElementById('ulProviderList');
    elements.cta = document.getElementById('ulCta');
    elements.ctaButton = document.getElementById('ulCtaButton');
    elements.limitMessage = document.getElementById('ulLimitMessage');
    elements.searchesRemaining = document.getElementById('ulSearchesRemaining');
    elements.remainingCount = document.getElementById('ulRemainingCount');
    elements.modal = document.getElementById('ulModal');
    elements.modalForm = document.getElementById('ulModalForm');
    elements.emailInput = document.getElementById('ulEmail');
    elements.modalCancel = document.getElementById('ulModalCancel');
    
    // Parse ref code from URL
    const urlParams = new URLSearchParams(window.location.search);
    state.refCode = urlParams.get('ref');
    
    // If we have a ref code, resolve it to get the email
    if (state.refCode) {
      resolveRefCode(state.refCode);
    }
    
    // Bind event listeners
    bindEvents();
    
    // Check initial limit status
    checkLimitStatus();
  }
  
  /**
   * Bind all event listeners
   */
  function bindEvents() {
    // Form submission
    elements.form.addEventListener('submit', handleFormSubmit);
    
    // Utility toggles
    elements.utilitiesContainer.querySelectorAll('.ul-utility-toggle').forEach(toggle => {
      toggle.addEventListener('click', handleUtilityToggle);
    });
    
    // Modal form
    elements.modalForm.addEventListener('submit', handleModalSubmit);
    elements.modalCancel.addEventListener('click', hideModal);
    
    // Modal backdrop click
    elements.modal.addEventListener('click', (e) => {
      if (e.target === elements.modal) {
        hideModal();
      }
    });
    
    // CTA button tracking
    elements.ctaButton.addEventListener('click', trackCtaClick);
    
    // Escape key to close modal
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && elements.modal.classList.contains('visible')) {
        hideModal();
      }
    });
  }
  
  /**
   * Handle utility toggle click
   */
  function handleUtilityToggle(e) {
    const toggle = e.currentTarget;
    const checkbox = toggle.querySelector('input');
    checkbox.checked = !checkbox.checked;
    toggle.classList.toggle('active', checkbox.checked);
  }
  
  /**
   * Get selected utilities
   */
  function getSelectedUtilities() {
    const selected = [];
    elements.utilitiesContainer.querySelectorAll('.ul-utility-toggle').forEach(toggle => {
      if (toggle.classList.contains('active')) {
        selected.push(toggle.dataset.utility);
      }
    });
    return selected;
  }
  
  /**
   * Handle main form submission
   */
  async function handleFormSubmit(e) {
    e.preventDefault();
    
    const address = elements.addressInput.value.trim();
    const utilities = getSelectedUtilities();
    
    if (!address) {
      showError('Please enter an address.');
      return;
    }
    
    if (utilities.length === 0) {
      showError('Please select at least one utility type.');
      return;
    }
    
    hideError();
    
    // If we don't have an email (no ref code, not entered yet), show modal
    if (!state.email && !state.refCode) {
      state.pendingSearch = { address, utilities };
      showModal();
      return;
    }
    
    // Perform the search
    await performSearch(address, utilities);
  }
  
  /**
   * Handle email modal submission
   */
  async function handleModalSubmit(e) {
    e.preventDefault();
    
    const email = elements.emailInput.value.trim();
    
    if (!email || !isValidEmail(email)) {
      return;
    }
    
    state.email = email;
    
    // Save pending search before hiding modal (which clears it)
    const searchToPerform = state.pendingSearch;
    hideModal();
    
    // Perform the pending search
    if (searchToPerform) {
      await performSearch(searchToPerform.address, searchToPerform.utilities);
    }
  }
  
  /**
   * Perform the actual lookup
   */
  async function performSearch(address, utilities) {
    setLoading(true);
    hideError();
    
    try {
      // Check limits first
      const limitCheck = await checkLimit();
      
      if (!limitCheck.can_search) {
        showLimitExceeded();
        setLoading(false);
        return;
      }
      
      // Fetch a single-use token first
      let token = null;
      try {
        const tokenResp = await fetch(`${CONFIG.apiBase}/api/leadgen/token`);
        const tokenData = await tokenResp.json();
        token = tokenData.token;
      } catch (err) {
        console.error('Token fetch error:', err);
        showError('Unable to connect. Please try again.');
        setLoading(false);
        return;
      }

      // Call the lookup API
      const response = await fetch(`${CONFIG.apiBase}/api/leadgen/lookup`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          address: address,
          utilities: utilities.join(','),
          email: state.email || null,
          ref_code: state.refCode || null,
          token: token
        })
      });
      
      const data = await response.json();
      
      if (data.status === 'limit_exceeded') {
        showLimitExceeded();
        setLoading(false);
        return;
      }
      
      if (data.status === 'error') {
        showError(data.message || 'An error occurred. Please try again.');
        setLoading(false);
        return;
      }
      
      // Update remaining searches
      if (typeof data.searches_remaining === 'number') {
        state.searchesRemaining = data.searches_remaining;
        updateSearchesRemaining();
      }
      
      // Display results
      displayResults(address, data.utilities || {});
      
    } catch (err) {
      console.error('Search error:', err);
      showError('Unable to connect. Please check your internet connection and try again.');
    }
    
    setLoading(false);
  }
  
  /**
   * Display utility provider results
   */
  function displayResults(address, utilities) {
    elements.resultsAddress.textContent = address;
    elements.providerList.innerHTML = '';
    
    let hasResults = false;
    
    // Order: electric, gas, water, sewer, internet
    const order = ['electric', 'gas', 'water', 'sewer', 'internet', 'trash'];
    
    order.forEach(utilityType => {
      const providers = utilities[utilityType];
      
      if (!providers || providers.length === 0) {
        // Check if this utility was requested
        const wasRequested = getSelectedUtilities().includes(utilityType);
        if (wasRequested) {
          elements.providerList.appendChild(createNoResultCard(utilityType));
        }
        return;
      }
      
      hasResults = true;
      
      providers.forEach(provider => {
        elements.providerList.appendChild(createProviderCard(utilityType, provider));
      });
    });
    
    // Show results section
    elements.resultsSection.classList.add('visible');
    
    // Show CTA
    elements.cta.classList.add('visible');
    
    // Scroll to results
    elements.resultsSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  
  /**
   * Create a provider card element
   */
  function createProviderCard(type, provider) {
    const card = document.createElement('div');
    card.className = 'ul-provider-card';
    
    const icon = utilityIcons[type] || '📍';
    const phone = provider.phone ? formatPhone(provider.phone) : null;
    const website = provider.website;
    
    let contactHtml = '';
    if (phone) {
      contactHtml += `<a href="tel:${provider.phone}">📞 ${phone}</a>`;
    }
    if (website) {
      contactHtml += `<a href="${website}" target="_blank" rel="noopener">🌐 Website</a>`;
    }
    
    card.innerHTML = `
      <div class="ul-provider-icon ${type}">${icon}</div>
      <div class="ul-provider-info">
        <div class="ul-provider-type">${type}</div>
        <div class="ul-provider-name">${escapeHtml(provider.name)}</div>
        ${contactHtml ? `<div class="ul-provider-contact">${contactHtml}</div>` : ''}
      </div>
    `;
    
    return card;
  }
  
  /**
   * Create a "no result" card for a utility type
   */
  function createNoResultCard(type) {
    const card = document.createElement('div');
    card.className = 'ul-no-result';
    const icon = utilityIcons[type] || '📍';
    card.innerHTML = `
      <span>${icon}</span>
      <span>No ${type} provider found for this address.</span>
    `;
    return card;
  }
  
  /**
   * Check limit status
   */
  async function checkLimit() {
    try {
      const params = new URLSearchParams();
      if (state.email) params.set('email', state.email);
      if (state.refCode) params.set('ref', state.refCode);
      
      const response = await fetch(`${CONFIG.apiBase}/api/leadgen/check-limit?${params}`);
      
      const data = await response.json();
      
      if (typeof data.searches_remaining === 'number') {
        state.searchesRemaining = data.searches_remaining;
        updateSearchesRemaining();
      }
      
      return data;
    } catch (err) {
      console.error('Limit check error:', err);
      // Default to allowing search if check fails
      return { can_search: true, searches_remaining: state.searchesRemaining };
    }
  }
  
  /**
   * Initial limit status check
   */
  async function checkLimitStatus() {
    // Only show remaining count if we have ref code (known user)
    if (state.refCode || state.email) {
      const status = await checkLimit();
      if (!status.can_search) {
        showLimitExceeded();
      }
    }
  }
  
  /**
   * Resolve ref code to email and personalization data
   */
  async function resolveRefCode(refCode) {
    try {
      const response = await fetch(`${CONFIG.apiBase}/api/leadgen/resolve-ref?ref=${refCode}`);
      
      const data = await response.json();
      
      if (data.success && data.data) {
        if (data.data.email) {
          state.email = data.data.email;
        }
        applyPersonalization(data.data);
      }
    } catch (err) {
      // Silent fail - show fallback content
    }
  }
  
  /**
   * Apply personalization data to elements with data-dynamic attributes
   */
  function applyPersonalization(data) {
    if (!data) return;
    
    // Handle data-dynamic attributes (text or image src)
    document.querySelectorAll('[data-dynamic]').forEach(el => {
      const fieldName = el.getAttribute('data-dynamic');
      const value = data[fieldName];
      if (value) {
        if (el.tagName === 'IMG') {
          el.src = value;
        } else {
          el.textContent = value;
        }
      }
    });
    
    // Handle data-dynamic-color attributes (set style.color)
    document.querySelectorAll('[data-dynamic-color]').forEach(el => {
      const fieldName = el.getAttribute('data-dynamic-color');
      const value = data[fieldName];
      if (value) {
        el.style.color = value;
      }
    });
  }
  
  /**
   * Track CTA button click
   */
  async function trackCtaClick() {
    try {
      await fetch(`${CONFIG.apiBase}/api/leadgen/track-cta`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          email: state.email || null,
          ref_code: state.refCode || null
        })
      });
    } catch (err) {
      // Silent fail - don't block navigation
    }
  }
  
  /**
   * UI Helpers
   */
  function showModal() {
    elements.modal.classList.add('visible');
    elements.emailInput.focus();
  }
  
  function hideModal() {
    elements.modal.classList.remove('visible');
    state.pendingSearch = null;
  }
  
  function showError(message) {
    elements.errorEl.textContent = message;
    elements.errorEl.classList.add('visible');
  }
  
  function hideError() {
    elements.errorEl.classList.remove('visible');
  }
  
  function setLoading(loading) {
    elements.submitBtn.classList.toggle('loading', loading);
    elements.submitBtn.disabled = loading;
    elements.addressInput.disabled = loading;
  }
  
  function showLimitExceeded() {
    elements.form.style.display = 'none';
    elements.resultsSection.classList.remove('visible');
    elements.cta.classList.remove('visible');
    elements.limitMessage.classList.add('visible');
  }
  
  function updateSearchesRemaining() {
    if (state.searchesRemaining <= CONFIG.maxSearches && state.searchesRemaining > 0) {
      elements.remainingCount.textContent = state.searchesRemaining;
      elements.searchesRemaining.style.display = 'block';
    } else if (state.searchesRemaining <= 0) {
      elements.searchesRemaining.style.display = 'none';
    }
  }
  
  /**
   * Utility functions
   */
  function isValidEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  }
  
  function formatPhone(phone) {
    const cleaned = phone.replace(/\D/g, '');
    if (cleaned.length === 10) {
      return `(${cleaned.slice(0,3)}) ${cleaned.slice(3,6)}-${cleaned.slice(6)}`;
    }
    if (cleaned.length === 11 && cleaned[0] === '1') {
      return `(${cleaned.slice(1,4)}) ${cleaned.slice(4,7)}-${cleaned.slice(7)}`;
    }
    return phone;
  }
  
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
  
  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
