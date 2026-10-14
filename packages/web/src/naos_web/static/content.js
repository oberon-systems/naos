(() => {
  "use strict";

  const CHUNK = 64 * 1024;
  const SIDES = ["host", "agent"];
  const ROW_BYTES = 16;
  const NEAR_END = 400;
  // past this many edits the lines are paired by position instead of aligned
  const MAX_EDITS = 1000;
  const KIB = 1024;
  // C0 and C1 controls, DEL and the bidi controls that reorder what a reader sees
  const HIDDEN = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200e\u200f\u202a-\u202e\u2066-\u2069]/;

  function size(value) {
    return value < KIB ? `${value} B` : `${(value / KIB).toFixed(1)} KiB`;
  }

  function concat(head, tail) {
    const joined = new Uint8Array(head.length + tail.length);
    joined.set(head);
    joined.set(tail, head.length);
    return joined;
  }

  function escaped(char) {
    const code = char.codePointAt(0);
    return code > 0xff ? `\\u${code.toString(16).padStart(4, "0")}` : `\\x${code.toString(16).padStart(2, "0")}`;
  }

  // Guest bytes become text nodes only; a control character is spelled out, never acted on.
  function inert(text) {
    const fragment = document.createDocumentFragment();
    let plain = "";
    for (const char of text) {
      if (!HIDDEN.test(char)) {
        plain += char;
        continue;
      }
      if (plain) {
        fragment.appendChild(document.createTextNode(plain));
        plain = "";
      }
      const mark = document.createElement("span");
      mark.className = "reader__escape";
      mark.textContent = escaped(char);
      fragment.appendChild(mark);
    }
    if (plain) {
      fragment.appendChild(document.createTextNode(plain));
    }
    return fragment;
  }

  // Myers' diff over the middle that the common head and tail leave; null when it is too far apart.
  function edits(a, b) {
    let head = 0;
    while (head < a.length && head < b.length && a[head] === b[head]) {
      head += 1;
    }
    let tail = 0;
    while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) {
      tail += 1;
    }
    const x0 = a.slice(head, a.length - tail);
    const y0 = b.slice(head, b.length - tail);
    const middle = myers(x0, y0);
    if (middle === null) {
      return null;
    }
    const ops = [];
    for (let i = 0; i < head; i += 1) {
      ops.push(["same", i, i]);
    }
    for (const [kind, x, y] of middle) {
      ops.push([kind, x + head, y + head]);
    }
    for (let i = 0; i < tail; i += 1) {
      ops.push(["same", a.length - tail + i, b.length - tail + i]);
    }
    return ops;
  }

  function myers(a, b) {
    const n = a.length;
    const m = b.length;
    const max = Math.min(n + m, MAX_EDITS);
    const off = max + 1;
    const v = new Int32Array(2 * max + 3);
    const trace = [];
    for (let d = 0; d <= max; d += 1) {
      trace.push(v.slice(off - d, off + d + 1));
      for (let k = -d; k <= d; k += 2) {
        let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1;
        let y = x - k;
        while (x < n && y < m && a[x] === b[y]) {
          x += 1;
          y += 1;
        }
        v[off + k] = x;
        if (x >= n && y >= m) {
          return backtrack(trace, n, m);
        }
      }
    }
    return n + m === 0 ? [] : null;
  }

  function backtrack(trace, n, m) {
    const ops = [];
    let x = n;
    let y = m;
    for (let d = trace.length - 1; d > 0; d -= 1) {
      const before = trace[d];
      const at = (k) => before[k + d];
      const k = x - y;
      const from = k === -d || (k !== d && at(k - 1) < at(k + 1)) ? k + 1 : k - 1;
      const px = at(from);
      const py = px - from;
      while (x > px && y > py) {
        x -= 1;
        y -= 1;
        ops.push(["same", x, y]);
      }
      if (x === px) {
        y -= 1;
        ops.push(["add", x, y]);
      } else {
        x -= 1;
        ops.push(["del", x, y]);
      }
    }
    while (x > 0 && y > 0) {
      x -= 1;
      y -= 1;
      ops.push(["same", x, y]);
    }
    return ops.reverse();
  }

  // A run of removals and additions pairs up line by line; what is left over stands alone.
  function rows(a, b, ops) {
    const out = [];
    let dels = [];
    let adds = [];
    const flush = () => {
      const paired = Math.min(dels.length, adds.length);
      for (let i = 0; i < Math.max(dels.length, adds.length); i += 1) {
        const host = i < dels.length ? dels[i] : null;
        const agent = i < adds.length ? adds[i] : null;
        out.push({ kind: i < paired ? "changed" : host === null ? "added" : "removed", host, agent, start: i === 0 });
      }
      dels = [];
      adds = [];
    };
    for (const [kind, x, y] of ops) {
      if (kind === "same") {
        flush();
        out.push({ kind: "same", host: x, agent: y, start: false });
      } else if (kind === "del") {
        dels.push(x);
      } else {
        adds.push(y);
      }
    }
    flush();
    return out;
  }

  function paired(a, b) {
    const out = [];
    let before = "same";
    for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
      const host = i < a.length ? i : null;
      const agent = i < b.length ? i : null;
      let kind = "same";
      if (host === null) {
        kind = "added";
      } else if (agent === null) {
        kind = "removed";
      } else if (a[i] !== b[i]) {
        kind = "changed";
      }
      out.push({ kind, host, agent, start: kind !== "same" && before === "same" });
      before = kind;
    }
    return out;
  }

  function start(element) {
    if (element.dataset.started) {
      return;
    }
    element.dataset.started = "true";
    const lines = element.querySelector("[data-lines]");
    const track = element.querySelector("[data-track]");
    const state = element.querySelector("[data-state]");
    const next = element.querySelector("[data-next]");
    const position = element.querySelector("[data-position]");
    const summary = element.querySelector("[data-summary]");
    const note = element.querySelector("[data-note]");
    const sides = {};
    for (const name of SIDES) {
      const present = element.dataset[name] === "1";
      sides[name] = { present, bytes: new Uint8Array(0), size: present ? null : 0, asked: false, failed: null };
    }
    const decoder = new TextDecoder("utf-8", { fatal: true });
    let hex = false;
    let starts = [];

    const done = (side) => side.failed !== null || (side.size !== null && side.bytes.length >= side.size);
    const finished = () => SIDES.every((name) => done(sides[name]));

    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}${element.dataset.stream}`);
    socket.binaryType = "arraybuffer";

    function ask(name) {
      const side = sides[name];
      if (side.asked || done(side) || socket.readyState !== WebSocket.OPEN) {
        return;
      }
      side.asked = true;
      socket.send(JSON.stringify({ side: name, offset: side.bytes.length, length: CHUNK }));
    }

    function nearEnd() {
      return lines.scrollTop + lines.clientHeight >= lines.scrollHeight - NEAR_END;
    }

    function more() {
      if (nearEnd()) {
        SIDES.forEach(ask);
      }
    }

    // A side still being read ends at its last whole line, so no line is shown cut.
    function textOf(side) {
      const end = done(side) ? side.bytes.length : side.bytes.lastIndexOf(10) + 1;
      const text = decoder.decode(side.bytes.subarray(0, end));
      const split = text.split("\n");
      if (split[split.length - 1] === "") {
        split.pop();
      }
      return split;
    }

    function cell(row, number, text, kind) {
      const no = document.createElement("span");
      const code = document.createElement("span");
      no.className = `reader__no reader__no--${kind}`;
      code.className = `reader__code reader__code--${kind}`;
      if (number !== null) {
        no.textContent = String(number);
        code.appendChild(inert(text));
      }
      row.append(no, code);
    }

    function textRows() {
      const a = textOf(sides.host);
      const b = textOf(sides.agent);
      const ops = edits(a, b);
      let out = ops === null ? paired(a, b) : rows(a, b, ops);
      // what follows the last match may still match lines not read yet
      if (!finished() && SIDES.every((name) => sides[name].present)) {
        let last = out.length;
        while (last > 0 && out[last - 1].kind !== "same") {
          last -= 1;
        }
        out = last > 0 ? out.slice(0, last) : out;
      }
      const fragment = document.createDocumentFragment();
      for (const row of out) {
        const line = document.createElement("div");
        line.className = `reader__row reader__row--${row.kind}`;
        const hostKind = row.host === null ? "filler" : row.kind === "added" ? "same" : row.kind;
        const agentKind = row.agent === null ? "filler" : row.kind === "removed" ? "same" : row.kind;
        cell(line, row.host === null ? null : row.host + 1, row.host === null ? "" : a[row.host], hostKind);
        cell(line, row.agent === null ? null : row.agent + 1, row.agent === null ? "" : b[row.agent], agentKind);
        fragment.appendChild(line);
      }
      return { fragment, out };
    }

    function hexCell(row, bytes, from, other, kind) {
      const no = document.createElement("span");
      const code = document.createElement("span");
      no.className = `reader__no reader__no--${kind}`;
      code.className = `reader__code reader__hex reader__code--${kind}`;
      if (from < bytes.length) {
        no.textContent = from.toString(16).padStart(8, "0");
        const chunk = bytes.subarray(from, from + ROW_BYTES);
        const hexes = document.createElement("span");
        const chars = document.createElement("span");
        chars.className = "reader__ascii";
        chunk.forEach((byte, at) => {
          const differs = from + at >= other.length || other[from + at] !== byte;
          const pair = document.createElement("span");
          pair.textContent = byte.toString(16).padStart(2, "0") + (at === 7 ? "  " : " ");
          const char = document.createElement("span");
          char.textContent = byte >= 0x20 && byte < 0x7f ? String.fromCharCode(byte) : ".";
          if (differs) {
            pair.className = "reader__byte--diff";
            char.className = "reader__byte--diff";
          }
          hexes.appendChild(pair);
          chars.appendChild(char);
        });
        code.append(hexes, chars);
      }
      row.append(no, code);
    }

    function hexRows() {
      const a = sides.host.bytes;
      const b = sides.agent.bytes;
      const reading = SIDES.filter((name) => !done(sides[name])).map((name) => sides[name].bytes.length);
      const end = reading.length ? Math.min(...reading) : Math.max(a.length, b.length);
      const out = [];
      const fragment = document.createDocumentFragment();
      let before = "same";
      for (let from = 0; from < end; from += ROW_BYTES) {
        const left = a.subarray(from, from + ROW_BYTES);
        const right = b.subarray(from, from + ROW_BYTES);
        let kind = "same";
        if (left.length === 0) {
          kind = "added";
        } else if (right.length === 0) {
          kind = "removed";
        } else if (left.length !== right.length || left.some((byte, at) => byte !== right[at])) {
          kind = "changed";
        }
        out.push({ kind, start: kind !== "same" && before === "same" });
        before = kind;
        const line = document.createElement("div");
        line.className = `reader__row reader__row--${kind}`;
        hexCell(line, a, from, b, left.length === 0 ? "filler" : kind === "added" ? "same" : kind);
        hexCell(line, b, from, a, right.length === 0 ? "filler" : kind === "removed" ? "same" : kind);
        fragment.appendChild(line);
      }
      return { fragment, out };
    }

    function textual() {
      for (const name of SIDES) {
        const side = sides[name];
        if (side.bytes.includes(0)) {
          return false;
        }
        try {
          textOf(side);
        } catch {
          return false;
        }
      }
      return true;
    }

    function render() {
      for (const name of SIDES) {
        const side = sides[name];
        const shown = element.querySelector(`[data-size="${name}"]`);
        shown.textContent = !side.present ? "\u2014" : side.size === null ? "\u2026" : size(side.size);
      }
      hex = hex || !textual();
      if (hex) {
        note.textContent = note.dataset.hexNote;
      }
      const { fragment, out } = hex ? hexRows() : textRows();
      const scroll = lines.scrollTop;
      lines.replaceChildren(fragment);
      lines.scrollTop = scroll;
      starts = out.flatMap((row, at) => (row.start ? [at] : []));
      track.replaceChildren(
        ...starts.map((at) => {
          const tick = document.createElement("span");
          tick.className = `reader__tick reader__tick--${out[at].kind}`;
          tick.style.top = `${(at / Math.max(out.length, 1)) * 100}%`;
          return tick;
        }),
      );
      const counted = `${starts.length} difference${starts.length === 1 ? "" : "s"}`;
      summary.textContent = [element.dataset.change, hex ? "binary, hex" : null, finished() ? counted : `${counted} so far`]
        .filter(Boolean)
        .join(" \u00b7 ");
      next.disabled = starts.length === 0;
      locate();
      const failed = SIDES.filter((name) => sides[name].failed !== null);
      if (failed.length) {
        state.hidden = false;
        state.textContent = failed.map((name) => `${name}: ${sides[name].failed}`).join(" \u00b7 ");
      } else if (finished()) {
        state.hidden = true;
      } else {
        const read = SIDES.map((name) => sides[name]).filter((side) => side.present && side.size !== null);
        state.hidden = false;
        state.textContent = `Read ${read.map((side) => `${size(side.bytes.length)} of ${size(side.size)}`).join(" and ")}; scroll for more.`;
      }
    }

    function rowAt(index) {
      return lines.children[index] || null;
    }

    // The difference at the top of the view is the current one.
    function current() {
      const top = lines.scrollTop + 1;
      let found = -1;
      starts.forEach((at, order) => {
        const row = rowAt(at);
        if (row && row.offsetTop <= top) {
          found = order;
        }
      });
      return found;
    }

    function locate() {
      const at = current();
      position.textContent = starts.length ? `difference ${Math.max(at, 0) + 1} of ${starts.length}` : "";
    }

    next.addEventListener("click", () => {
      if (!starts.length) {
        return;
      }
      const target = rowAt(starts[(current() + 1) % starts.length]);
      if (target) {
        lines.scrollTop = target.offsetTop;
      }
      locate();
    });
    lines.addEventListener("scroll", () => {
      locate();
      more();
    });

    socket.onopen = () => SIDES.forEach(ask);
    socket.onmessage = (event) => {
      if (typeof event.data === "string") {
        const reply = JSON.parse(event.data);
        const side = sides[reply.side];
        if (!side) {
          return;
        }
        side.asked = false;
        if (reply.absent) {
          side.present = false;
          side.size = 0;
          side.bytes = new Uint8Array(0);
        } else {
          side.failed = String(reply.error || "unreadable");
        }
        render();
        more();
        return;
      }
      const view = new DataView(event.data);
      const side = sides[SIDES[view.getUint8(0)]];
      const offset = Number(view.getBigUint64(1));
      if (!side || offset !== side.bytes.length) {
        return;
      }
      const data = new Uint8Array(event.data, 17);
      side.asked = false;
      side.size = Number(view.getBigUint64(9));
      side.bytes = concat(side.bytes, data);
      // a file that shrank while it was read ends where the reading did
      if (data.length === 0) {
        side.size = side.bytes.length;
      }
      render();
      more();
    };
    socket.onclose = (event) => {
      if (!finished()) {
        state.hidden = false;
        state.textContent = event.code === 1000 ? "the stream ended" : `closed: ${event.reason || event.code}`;
      }
    };
    element.addEventListener("htmx:beforeCleanupElement", () => socket.close());
  }

  function scan(root) {
    root.querySelectorAll(".reader[data-stream]").forEach(start);
  }

  document.addEventListener("DOMContentLoaded", () => {
    scan(document);
    if (window.htmx) {
      window.htmx.onLoad(scan);
    }
  });
})();
