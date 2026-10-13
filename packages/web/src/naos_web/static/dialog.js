(() => {
  "use strict";

  // Next stays shut while a required select is unset; Chrome never restyles the button
  // from a :has() rule on its form, so the state is set here
  function sync(form) {
    const next = form.querySelector(".dialog__next");
    if (next) {
      const required = form.querySelectorAll("select[required]");
      next.disabled = [...required].some((select) => !select.value);
    }
  }

  document.addEventListener("change", (event) => {
    const form = event.target.closest("form");
    if (form) {
      sync(form);
    }
  });
  document.addEventListener("htmx:load", (event) => {
    const root = event.detail.elt;
    [root, ...root.querySelectorAll("form")].filter((node) => node.matches("form")).forEach(sync);
  });
})();
