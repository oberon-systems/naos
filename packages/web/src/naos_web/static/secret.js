(() => {
  "use strict";

  // the eye changes the field in this browser only: nothing about it is posted
  function toggle(button) {
    const field = button.closest("[data-secret-field]");
    const hint = field.querySelector("[data-secret-hint]");
    const shown = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(shown));
    button.setAttribute("aria-label", shown ? "Hide the value" : "Show the value");
    field.querySelector("[data-secret-value]").classList.toggle("secret__input--masked", !shown);
    hint.dataset.hidden = hint.dataset.hidden || hint.textContent;
    hint.textContent = shown ? hint.dataset.shown : hint.dataset.hidden;
  }

  function scope(element) {
    return element.closest("#overlay") || document;
  }

  function previous(select) {
    const initial = Array.from(select.options).find((option) => option.defaultSelected);
    return select.dataset.was || (initial ? initial.value : select.options[0].value);
  }

  function settle(select, never) {
    const root = scope(select);
    root.querySelector("[data-secret-never]").value = never ? "confirmed" : "";
    root.querySelector("[data-secret-term-note]").textContent =
      select.selectedOptions[0].dataset.note;
    select.dataset.was = select.value;
  }

  // never is asked about before the form is posted, so the value is never sent to be asked again
  function ask(select) {
    const root = scope(select);
    const confirm = root.querySelector("[data-secret-confirm]");
    const name = root.querySelector("[data-secret-name]");
    if (name) {
      confirm.querySelector("[data-secret-confirm-name]").textContent = name.value;
    }
    confirm.querySelector("[data-secret-confirm-was]").textContent = previous(select);
    confirm.hidden = false;
  }

  function answer(button) {
    const root = scope(button);
    const select = root.querySelector("[data-secret-term]");
    const yes = button.dataset.secretConfirmAnswer === "yes";
    if (!yes) {
      select.value = previous(select);
    }
    settle(select, yes);
    root.querySelector("[data-secret-confirm]").hidden = true;
  }

  document.addEventListener("click", (event) => {
    const eye = event.target.closest("[data-secret-eye]");
    const reply = event.target.closest("[data-secret-confirm-answer]");
    if (eye) {
      toggle(eye);
    } else if (reply) {
      answer(reply);
    }
  });

  document.addEventListener("change", (event) => {
    const select = event.target.closest("[data-secret-term]");
    if (!select) {
      return;
    }
    if (select.value === "never") {
      ask(select);
    } else {
      settle(select, false);
    }
  });
})();
