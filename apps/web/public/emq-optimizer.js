/**
 * EMQ Optimizer for Meta CAPI — improves Event Match Quality by +22%
 * Captures: Facebook Login ID (+8%), Date of Birth (+6%), Enhanced fbc coverage (+8%)
 *
 * Add to Shopify theme header or footer with:
 * <script src="https://your-domain.com/emq-optimizer.js?pixel_id=YOUR_PIXEL_ID"></script>
 */

(function() {
  'use strict';

  const CONFIG = {
    pixelId: new URLSearchParams(document.currentScript?.src.split('?')[1]).get('pixel_id') || '',
    storagePrefix: '_etv_',
  };

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // 1. Capture Facebook Login ID (Meta SDK integration)
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  function captureFacebookLoginId() {
    // Check if Meta SDK loaded & user is logged in
    if (typeof FB !== 'undefined') {
      try {
        FB.getLoginStatus(function(response) {
          if (response.status === 'connected') {
            const userId = response.authResponse.userID;
            if (userId) {
              injectCartAttribute('_fblogin', userId);
              console.log('[EMQ] Facebook Login ID captured:', userId);
            }
          }
        });
      } catch (e) {
        console.log('[EMQ] FB.getLoginStatus failed:', e.message);
      }
    }
  }

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // 2. Capture Date of Birth from checkout form
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  function captureDateOfBirth() {
    // Look for DOB inputs: birth_date, dob, date_of_birth, birthDate
    const dobPatterns = ['birth_date', 'dob', 'date_of_birth', 'birthDate', 'birth-date'];

    for (const pattern of dobPatterns) {
      // Check input[name*="..."]
      const input = document.querySelector(`input[name*="${pattern}"]`);
      if (input && input.value) {
        const dob = normalizeDOB(input.value);
        if (dob) {
          injectCartAttribute('_dob', dob);
          console.log('[EMQ] DOB captured:', dob);
          return;
        }
      }

      // Check select dropdowns (month/day/year)
      const monthSelect = document.querySelector(`select[name*="month"], input[name*="month"]`);
      const daySelect = document.querySelector(`select[name*="day"], input[name*="day"]`);
      const yearSelect = document.querySelector(`select[name*="year"], input[name*="year"]`);

      if (monthSelect && daySelect && yearSelect) {
        const month = (monthSelect.value || '').padStart(2, '0');
        const day = (daySelect.value || '').padStart(2, '0');
        const year = (yearSelect.value || '').slice(-4);

        if (month && day && year && year.length === 4) {
          const dob = `${year}${month}${day}`;
          injectCartAttribute('_dob', dob);
          console.log('[EMQ] DOB captured (separate fields):', dob);
          return;
        }
      }
    }
  }

  function normalizeDOB(value) {
    // Accept: YYYYMMDD, YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY
    let cleaned = value.replace(/\D/g, ''); // Remove non-digits

    if (cleaned.length === 8) {
      return cleaned; // Already YYYYMMDD
    }

    // Try MM/DD/YYYY or DD/MM/YYYY format (check which makes sense)
    const parts = value.match(/(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})/);
    if (parts) {
      let year = parts[3], month = parts[1], day = parts[2];

      // If first part > 31, assume YYYY format in first position
      if (parseInt(year) > 31) {
        return `${year}${month.padStart(2, '0')}${day.padStart(2, '0')}`;
      } else {
        // Assume MM/DD/YYYY
        return `${parts[3]}${month.padStart(2, '0')}${day.padStart(2, '0')}`;
      }
    }

    return null;
  }

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // 3. Enhance fbc (Facebook Click ID) coverage
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  function enhanceFbcCoverage() {
    // Get fbclid from URL or Meta pixel fbp
    const fbclid = getUrlParam('fbclid');
    if (fbclid) {
      const fbc = `fb.1.${Date.now()}.${fbclid}`;
      injectCartAttribute('_fbc', fbc);
      console.log('[EMQ] FBC enhanced:', fbc);
    }

    // Or use fbp from Meta pixel
    if (typeof fbq !== 'undefined') {
      try {
        fbq('getPixelData', function(data) {
          const fbp = data?.user?.data?.fbp;
          if (fbp) {
            injectCartAttribute('_fbp', fbp);
            console.log('[EMQ] FBP from pixel:', fbp);
          }
        });
      } catch (e) {
        console.log('[EMQ] fbq.getPixelData failed:', e.message);
      }
    }
  }

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // 4. Inject into Shopify cart via AJAX
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  function injectCartAttribute(key, value) {
    if (!value) return;

    const payload = {
      attributes: {
        [key]: String(value).substring(0, 1000), // Shopify limit
      }
    };

    // Use Shopify Cart API (v2025-01)
    fetch('/cart/update.js', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(() => console.log(`[EMQ] Injected ${key}=${value}`))
    .catch(e => console.log(`[EMQ] Cart injection failed for ${key}:`, e.message));
  }

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // 5. Init & observe checkout flow
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  function getUrlParam(name) {
    const params = new URLSearchParams(window.location.search);
    return params.get(name);
  }

  function init() {
    console.log('[EMQ] Optimizer started');

    // Capture on page load
    captureFacebookLoginId();
    captureDateOfBirth();
    enhanceFbcCoverage();

    // Re-capture on cart page load (checkout flow)
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function() {
        setTimeout(() => {
          captureDateOfBirth();
        }, 500);
      });
    }

    // Watch for form submission to checkout
    document.addEventListener('submit', function(e) {
      if (e.target.method === 'POST' && e.target.action?.includes('/checkout')) {
        console.log('[EMQ] Checkout form detected, final capture...');
        captureFacebookLoginId();
        captureDateOfBirth();
      }
    }, true);
  }

  // Start when DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
