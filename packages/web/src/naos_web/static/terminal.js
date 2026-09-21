(() => {
  "use strict";

  const THEME = { background: "#0b1220", foreground: "#cbd5e1", cursor: "#4ade80" };

  function start(element) {
    if (element.dataset.started) {
      return;
    }
    element.dataset.started = "true";
    const font = getComputedStyle(document.body).getPropertyValue("--font-mono");
    const term = new Terminal({
      disableStdin: true,
      scrollback: 10000,
      fontFamily: font,
      fontSize: element.classList.contains("terminal--window") ? 13 : 12,
      theme: THEME,
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(element);
    fit.fit();

    const scope = element.closest(".run__body, .window") || document;
    const follow = scope.querySelector("[data-follow]");
    const state = scope.querySelector("[data-terminal-state]");
    let following = true;
    follow?.addEventListener("click", () => {
      following = !following;
      follow.classList.toggle("terminal__chip--on", following);
      follow.setAttribute("aria-pressed", String(following));
      if (following) {
        term.scrollToBottom();
      }
    });

    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${scheme}//${location.host}${element.dataset.stream}`);
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event) => {
      term.write(new Uint8Array(event.data), () => following && term.scrollToBottom());
    };
    socket.onclose = (event) => {
      if (state) {
        state.textContent = event.code === 1000 ? "stream ended" : `detached: ${event.reason || event.code}`;
      }
    };

    const resize = new ResizeObserver(() => fit.fit());
    resize.observe(element);
    element.addEventListener("htmx:beforeCleanupElement", () => {
      resize.disconnect();
      socket.close();
      term.dispose();
    });
  }

  function scan(root) {
    root.querySelectorAll(".terminal[data-stream]").forEach(start);
  }

  document.addEventListener("DOMContentLoaded", () => {
    scan(document);
    if (window.htmx) {
      window.htmx.onLoad(scan);
    }
  });
})();
