(() => {
  "use strict";

  const THEME = { background: "#0b1220", foreground: "#cbd5e1", cursor: "#4ade80" };

  // the guest has one size, so a viewer keeps saying what it wants and takes
  // the claim over as soon as the one who held it lets go
  const CLAIM_MS = 5000;

  function start(element) {
    if (element.dataset.started) {
      return;
    }
    element.dataset.started = "true";
    const font = getComputedStyle(document.body).getPropertyValue("--font-mono");
    const view = element.classList.contains("terminal--window") ? "window" : "panel";
    const design = view === "window" ? 13 : 12;
    const term = new Terminal({
      disableStdin: true,
      scrollback: 10000,
      fontFamily: font,
      fontSize: design,
      theme: THEME,
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    const surface = document.createElement("div");
    surface.className = "terminal__surface";
    element.appendChild(surface);
    term.open(surface);

    // a background tab paints no frames, so the fit may not wait for one
    let fitting = false;
    let driving = true;
    let socket = null;

    function claim() {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ cols: term.cols, rows: term.rows, view }));
      }
    }

    // a viewer that does not hold the size shows the holder's grid instead, and
    // shrinks the font, never past the design size, until that grid fits
    function squeeze() {
      const style = getComputedStyle(surface);
      const viewport = surface.querySelector(".xterm-viewport");
      const bar = viewport ? viewport.offsetWidth - viewport.clientWidth : 0;
      const width = parseFloat(style.width) - bar;
      const height = parseFloat(style.height);
      for (let pass = 0; pass < 2; pass += 1) {
        const cell = term._core._renderService.dimensions.css.cell;
        if (!cell.width || !cell.height) {
          return;
        }
        const scale = Math.min(
          width / (cell.width * term.cols),
          height / (cell.height * term.rows),
        );
        const size = Math.max(6, Math.min(design, Math.floor(term.options.fontSize * scale)));
        if (size === term.options.fontSize) {
          return;
        }
        term.options.fontSize = size;
      }
    }

    const refit = () => {
      if (fitting || element.clientWidth === 0 || element.clientHeight === 0) {
        return;
      }
      fitting = true;
      try {
        if (driving) {
          term.options.fontSize = design;
          fit.fit();
          claim();
        } else {
          squeeze();
        }
      } finally {
        fitting = false;
      }
    };
    refit();
    document.fonts?.ready.then(refit);
    window.addEventListener("resize", refit);

    const scope = element.closest(".run__body, .window") || document;
    const follow = scope.querySelector("[data-follow]");
    const state = scope.querySelector("[data-terminal-state]");
    const note = scope.querySelector("[data-terminal-size]");
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
    socket = new WebSocket(`${scheme}//${location.host}${element.dataset.stream}`);
    socket.binaryType = "arraybuffer";
    socket.onopen = claim;
    const keep = setInterval(claim, CLAIM_MS);
    socket.onmessage = (event) => {
      if (typeof event.data === "string") {
        const reply = JSON.parse(event.data);
        const held = driving;
        driving = reply.driving;
        if (!driving) {
          if (term.cols !== reply.cols || term.rows !== reply.rows) {
            term.resize(reply.cols, reply.rows);
          }
          squeeze();
        } else if (!held) {
          refit();
        }
        if (note) {
          note.textContent = driving ? "" : "the detached window sets the size";
        }
        return;
      }
      term.write(new Uint8Array(event.data), () => following && term.scrollToBottom());
    };
    socket.onclose = (event) => {
      if (state) {
        state.textContent = event.code === 1000 ? "stream ended" : `detached: ${event.reason || event.code}`;
      }
    };

    const resize = new ResizeObserver(refit);
    resize.observe(element);
    element.addEventListener("htmx:beforeCleanupElement", () => {
      clearInterval(keep);
      window.removeEventListener("resize", refit);
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
