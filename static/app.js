// HorseBin client sugar. Everything works without it; this adds live word preview,
// drag-drop / Ctrl+V files, copy buttons, confirm dialogs and the expiry countdown.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = s => s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- copy buttons
  $$("button.copy").forEach(b => b.addEventListener("click", async e => {
    e.preventDefault();
    const t = $(b.dataset.copy).textContent;
    try { await navigator.clipboard.writeText(t); } catch {
      const r = document.createRange(); r.selectNodeContents($(b.dataset.copy));
      getSelection().removeAllRanges(); getSelection().addRange(r); document.execCommand("copy");
    }
    const old = b.textContent; b.textContent = "copied ✓"; setTimeout(() => (b.textContent = old), 1200);
  }));

  $$("button[data-confirm]").forEach(b => b.addEventListener("click", e => {
    if (!confirm(b.dataset.confirm)) e.preventDefault();
  }));

  // ---- countdown
  const left = $(".left[data-expires]");
  if (left) {
    const end = +left.dataset.expires * 1000;
    const tick = () => {
      const s = Math.max(0, Math.round((end - Date.now()) / 1000));
      left.textContent = s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`
        : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
      if (s === 0) { left.textContent = "now. Bye 🐴"; clearInterval(iv); }
    };
    const iv = setInterval(tick, 1000); tick();
  }

  // ---- live preview of what the typed words resolve to (mirrors words.py)
  const q = $("#q"), hint = $("#qhint");
  if (q && hint) {
    let WORDS = null, SET, PREFIX;
    const defaultHint = hint.innerHTML;
    fetch("/static/words.txt").then(r => r.text()).then(t => {
      WORDS = t.split(/\s+/).filter(Boolean); SET = new Set(WORDS);
      PREFIX = new Map(WORDS.map(w => [w.slice(0, 3), w]));
      render();
    });
    const dist = (a, b, cap = 3) => {
      if (Math.abs(a.length - b.length) > cap) return cap + 1;
      let p2 = null, p = [...Array(b.length + 1).keys()];
      for (let i = 1; i <= a.length; i++) {
        const c = [i];
        for (let j = 1; j <= b.length; j++) {
          c[j] = Math.min(p[j] + 1, c[j - 1] + 1, p[j - 1] + (a[i - 1] !== b[j - 1]));
          if (p2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) c[j] = Math.min(c[j], p2[j - 2] + 1);
        }
        p2 = p; p = c;
      }
      return p[b.length];
    };
    const one = t => {
      if (SET.has(t)) return t;
      if (t.length < 3) return null;
      const w = PREFIX.get(t.slice(0, 3));
      if (w && (w.startsWith(t) || dist(t, w) <= Math.max(1, Math.floor(w.length / 3)))) return w;
      const cap = t.length <= 4 ? 1 : 2;
      const best = WORDS.map(w => [dist(t, w, cap), w]).filter(([d]) => d <= cap).sort((x, y) => x[0] - y[0]);
      if (!best.length || (best.length > 1 && best[0][0] === best[1][0])) return null;
      return best[0][1];
    };
    const segment = s => {
      const best = [[]];
      for (let i = 0; i < s.length; i++) {
        if (!best[i]) continue;
        for (let L = 3; L <= 8; L++) if (SET.has(s.slice(i, i + L)) && !best[i + L]) best[i + L] = [...best[i], s.slice(i, i + L)];
      }
      return best[s.length] || null;
    };
    const render = () => {
      if (!WORDS) return;
      let toks = q.value.toLowerCase().match(/[a-z]+/g) || [];
      if (!toks.length) { hint.innerHTML = defaultHint; return; }
      if ((toks.join("") + "xxxxxxxxxxxx").startsWith("correcthorse")) { hint.innerHTML = "😏 nice try."; return; }
      if (toks.length === 1 && toks[0].length > 8) toks = segment(toks[0]) || toks;
      const chips = toks.slice(0, 4).map(t => {
        const w = one(t);
        return w ? `<span class="w"><b>${esc(w.slice(0, 3))}</b>${esc(w.slice(3))}</span>`
                 : `<span class="w bad">${esc(t)}?</span>`;
      }).join("");
      hint.innerHTML = `<div class="words left">${chips}</div>`;
    };
    q.addEventListener("input", render);
  }

  // ---- nothing focused: typing letters goes to Receive, pasting text goes to Send
  addEventListener("keydown", e => {
    const a = document.activeElement;
    if (!q || (a && /INPUT|TEXTAREA|SELECT|BUTTON/.test(a.tagName))) return;
    if (e.key.length === 1 && /[a-z]/i.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) q.focus();
  });
  addEventListener("paste", e => {
    const a = document.activeElement, t = $("#text");
    if (!t || (a && /INPUT|TEXTAREA/.test(a.tagName)) || e.clipboardData?.files?.length) return;
    const s = e.clipboardData?.getData("text"); if (!s) return;
    e.preventDefault(); t.value = s; t.focus();
  });

  // ---- files: drag & drop anywhere, Ctrl+V images, show chosen name
  const file = $("#file"), fname = $("#filename"), text = $("#text");
  if (file) {
    const setFiles = fl => {
      if (!fl || !fl.length) return;
      const dt = new DataTransfer(); dt.items.add(fl[0]); file.files = dt.files; showName();
    };
    const showName = () => {
      const f = file.files[0];
      fname.textContent = f ? `📎 ${f.name} (${(f.size / 1024).toFixed(0)} KB) ✕` : "📎 attach a file";
    };
    file.addEventListener("change", showName);
    fname.addEventListener("click", e => {
      if (file.files.length) { e.preventDefault(); file.value = ""; showName(); }
    });
    let depth = 0;
    addEventListener("dragenter", e => { if (e.dataTransfer.types.includes("Files")) { depth++; document.body.classList.add("dragging"); } });
    addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; document.body.classList.remove("dragging"); } });
    addEventListener("dragover", e => e.preventDefault());
    addEventListener("drop", e => {
      e.preventDefault(); depth = 0; document.body.classList.remove("dragging");
      setFiles(e.dataTransfer.files);
    });
    addEventListener("paste", e => {
      const f = [...(e.clipboardData?.files || [])];
      if (f.length) {
        e.preventDefault();
        const img = f[0];
        const named = img.name && img.name !== "image.png" ? img
          : new File([img], `screenshot-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.${(img.type.split("/")[1] || "bin")}`, { type: img.type });
        setFiles([named]);
      }
    });
    // Enter in the words box shouldn't be confused with Ctrl+Enter submit of the paste box
    text.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#newform").requestSubmit(); });
    // Empty submit: say so right here instead of navigating to an error page
    $("#newform").addEventListener("submit", e => {
      if (!text.value.trim() && !file.files.length) {
        e.preventDefault();
        text.setCustomValidity("Paste some text here first, or attach a file.");
        text.reportValidity(); text.focus();
      }
    });
    text.addEventListener("input", () => text.setCustomValidity(""));
    file.addEventListener("change", () => text.setCustomValidity(""));
  }
})();
