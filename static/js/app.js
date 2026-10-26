(() => {
  'use strict';
  const dialog = document.getElementById('confirm-dialog');
  let pendingForm = null;
  if (dialog) {
    const message = dialog.querySelector('[data-confirm-message]');
    const yes = dialog.querySelector('[data-dialog-ok]');
    const cancel = dialog.querySelector('[data-dialog-cancel]');
    document.addEventListener('submit', (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-confirm') || form.dataset.confirmed === 'true') return;
      event.preventDefault();
      pendingForm = form;
      if (message) message.textContent = form.getAttribute('data-confirm') || 'Continue with this action?';
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else if (window.confirm(message?.textContent || 'Continue?')) {
        form.dataset.confirmed = 'true';
        form.requestSubmit();
      }
    });
    const close = () => {
      if (dialog.open) dialog.close();
      pendingForm = null;
    };
    yes?.addEventListener('click', () => {
      const form = pendingForm;
      close();
      if (form) {
        form.dataset.confirmed = 'true';
        form.requestSubmit();
      }
    });
    cancel?.addEventListener('click', close);
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) close();
    });
  }

  document.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-modal-open]');
    if (opener) {
      const target = document.getElementById(opener.getAttribute('data-modal-open'));
      if (target && typeof target.showModal === 'function') target.showModal();
    }
    const closer = event.target.closest('[data-modal-close]');
    if (closer) {
      const parent = closer.closest('dialog');
      if (parent && parent.open) parent.close();
    }
  });

  document.querySelectorAll('dialog').forEach((item) => {
    item.addEventListener('click', (event) => {
      if (event.target === item && item.id !== 'confirm-dialog') item.close();
    });
  });
})();
