document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const label = button.textContent;
  navigator.clipboard.writeText(button.dataset.copy).then(
    () => { button.textContent = "Copied"; },
    () => { button.textContent = "Copy failed"; },
  ).finally(() => setTimeout(() => { button.textContent = label; }, 1500));
});
