'use strict';
// Shared with the Node tests; this module does not require a DOM.
const WorkbenchI18n = {
  translate(catalog, language, key, params = {}) {
    const template = catalog[language]?.[key] ?? catalog.en[key] ?? key;
    return template.replace(/\{(\w+)\}/g, (match, name) => String(params[name] ?? match));
  },
  apply(document, language, t) {
    document.documentElement.lang = language;
    document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = t(node.dataset.i18n); });
    for (const attribute of ['placeholder', 'alt', 'aria-label']) {
      document.querySelectorAll(`[data-i18n-${attribute}]`).forEach(node => {
        node.setAttribute(attribute, t(node.getAttribute(`data-i18n-${attribute}`)));
      });
    }
  },
  persist(document, language) {
    if (!['en', 'ja'].includes(language)) return false;
    document.cookie = `workbench_language=${language}; Max-Age=31536000; Path=/; SameSite=Lax`;
    return true;
  }
};
if (typeof module !== 'undefined') module.exports = WorkbenchI18n;
