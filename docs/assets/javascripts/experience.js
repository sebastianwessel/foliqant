/* Illustrative, deterministic navigation aid. No requests or model calls. */
(() => {
  const scenarios = {
    billing: {
      input: '“Please review the duplicate charge on invoice INV-7 for account A-100.”',
      category: 'billing', route: 'Billing flow', result: 'Draft for an agent to review',
      reason: 'The message explicitly identifies a duplicate charge. The workflow selects the billing flow; nothing is sent or changed automatically.'
    },
    cancellation: {
      input: '“Please cancel the next renewal for account A-100.”',
      category: 'cancellation', route: 'Cancellation flow', result: 'Draft for an agent to review',
      reason: 'The message clearly requests cancellation. A different authored route prepares the response; the host still decides whether to take action.'
    },
    review: {
      input: '“Something is wrong with my account. Can you help?”',
      category: 'No supported answer', route: 'Review outcome', result: 'needs_review',
      reason: 'The message does not distinguish billing from cancellation. The process returns a review outcome instead of choosing a category without support.'
    }
  };
  function initialize() {
    document.querySelectorAll('.process-demo').forEach(demo => {
      if (demo.dataset.initialized) return;
      demo.dataset.initialized = 'true';
      const buttons = demo.querySelectorAll('[data-scenario]');
      const content = demo.querySelector('.process-demo__content');
      buttons.forEach(button => button.addEventListener('click', () => {
        const scenario = scenarios[button.dataset.scenario];
        if (!scenario) return;
        buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
        for (const [key, value] of Object.entries(scenario)) {
          const target = demo.querySelector(`[data-demo-${key}]`);
          if (target) target.textContent = value;
        }
        content.classList.remove('is-changing');
        requestAnimationFrame(() => content.classList.add('is-changing'));
      }));
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
  if (typeof document$ !== 'undefined') document$.subscribe(initialize);
})();
