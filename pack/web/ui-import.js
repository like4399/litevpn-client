(() => {
  if (window.__litevpnImportMounted) return;
  window.__litevpnImportMounted = true;

  function walkSecret(o, depth) {
    if (!o || depth > 8) return "";
    if (Array.isArray(o)) {
      for (const x of o) {
        const s = walkSecret(x, depth + 1);
        if (s) return s;
      }
      return "";
    }
    if (!o || typeof o !== "object") return "";
    if (typeof o.secret === "string" && o.secret.length >= 8 && o.secret.length <= 128) return o.secret;
    for (const v of Object.values(o)) {
      if (v && typeof v === "object") {
        const s = walkSecret(v, depth + 1);
        if (s) return s;
      }
    }
    return "";
  }

  function findSecret() {
    const cached = sessionStorage.getItem("litevpn.secret");
    if (cached) return cached;
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      const v = localStorage.getItem(k);
      if (!v || v.length > 400000) continue;
      try {
        const s = walkSecret(JSON.parse(v), 0);
        if (s) return s;
      } catch {
        /* ignore */
      }
    }
    return "";
  }

  function needSecret() {
    let secret = findSecret();
    if (secret) return secret;
    secret = (window.prompt("请输入 API secret（与连接面板时相同）") || "").trim();
    if (secret) sessionStorage.setItem("litevpn.secret", secret);
    return secret;
  }

  const st = document.createElement("style");
  st.textContent = `
    #litevpn-import-bar {
      display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
      margin: 0 0 12px; padding: 0 0 12px;
      border-bottom: 1px solid #333846;
      font-family: ui-sans-serif, system-ui, sans-serif; color: #e8eaed;
      width: 100%; box-sizing: border-box;
    }
    #litevpn-import-bar .lv-label { font-size: 12px; color: #9aa3b2; white-space: nowrap; }
    #litevpn-url {
      flex: 1; min-width: 200px; background: #141820; color: #e8eaed;
      border: 1px solid #3a4250; border-radius: 8px; padding: 8px 10px; font: inherit;
    }
    #litevpn-import-btn {
      background: #3b82f6; color: #fff; border: 0; border-radius: 8px;
      padding: 8px 16px; font-weight: 650; cursor: pointer;
    }
    #litevpn-import-btn:disabled { opacity: .5; cursor: not-allowed; }
    #litevpn-msg { font-size: 12px; color: #9aa3b2; }
    #litevpn-msg.err { color: #ff8b8b; }
    #litevpn-msg.ok { color: #3dd68c; }
    .lv-usage-inject {
      margin-top: 8px; font-family: ui-sans-serif, system-ui, sans-serif; width: 100%;
    }
    .lv-usage-inject .lv-host { font-size: 13px; color: #8b93a2; }
    .lv-usage-inject .lv-usage { font-size: 12px; color: #8b93a2; margin-top: 6px; }
    .lv-usage-inject .lv-bar {
      height: 6px; background: #2b3342; border-radius: 99px; margin-top: 8px; overflow: hidden;
      max-width: 100%;
    }
    .lv-usage-inject .lv-bar > span { display: block; height: 100%; background: #60a5fa; }
  `;
  document.documentElement.appendChild(st);

  const bar = document.createElement("div");
  bar.id = "litevpn-import-bar";
  bar.innerHTML =
    '<input id="litevpn-url" type="url" placeholder="订阅文件链接" />' +
    '<button type="button" id="litevpn-import-btn">导入</button>' +
    '<span id="litevpn-msg"></span>';

  function onProxiesPage() {
    return /proxies/i.test(location.hash || "");
  }

  function orangeish(el) {
    const bg = getComputedStyle(el).backgroundColor || "";
    const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (!m) return false;
    const r = +m[1], g = +m[2], b = +m[3];
    return r > 170 && g < 180 && b < 150;
  }

  function providersTabEl() {
    const nodes = document.querySelectorAll("button, a, div, span");
    for (const el of nodes) {
      const t = (el.textContent || "").replace(/\s+/g, "");
      if (t === "代理提供者" || t.startsWith("代理提供者")) return el;
    }
    return null;
  }

  function providersTabActive() {
    if (!onProxiesPage()) return false;
    const el = providersTabEl();
    if (!el) return false;
    const btn = el.closest("button") || el;
    if (btn.getAttribute("aria-selected") === "true" || btn.getAttribute("aria-pressed") === "true") return true;
    if (/active|selected|primary/i.test(btn.className)) return true;
    if (orangeish(btn) || orangeish(el)) return true;
    return false;
  }

  function looksLikeCard(el) {
    if (!el || el.id === "litevpn-import-bar") return false;
    const w = el.clientWidth;
    const h = el.clientHeight;
    if (w < 300 || h < 72 || h > 520) return false;
    const cs = getComputedStyle(el);
    const radius = parseFloat(cs.borderRadius) || 0;
    const bg = cs.backgroundColor || "";
    const opaque = /rgba?\(\s*\d+,\s*\d+,\s*\d+/.test(bg) && !bg.includes("0, 0, 0, 0");
    return radius >= 6 || opaque;
  }

  function fileCardEl(wantName) {
    const badges = document.querySelectorAll("div, span, p");
    for (const el of badges) {
      const t = (el.textContent || "").trim();
      if (t !== "File" && t !== "HTTP") continue;
      let n = el.parentElement;
      let found = null;
      for (let i = 0; i < 14 && n; i++) {
        if (n.id === "litevpn-import-bar") break;
        const txt = n.textContent || "";
        if (wantName && !txt.includes(wantName)) {
          n = n.parentElement;
          continue;
        }
        if (looksLikeCard(n) && (txt.includes("File") || txt.includes("HTTP"))) {
          found = n;
        }
        n = n.parentElement;
      }
      if (found) return found;
    }
    return null;
  }

  function placeBar() {
    document.body.style.paddingBottom = "";
    const oldDock = document.getElementById("litevpn-dock");
    if (oldDock && oldDock.parentNode) oldDock.parentNode.removeChild(oldDock);
    if (!providersTabActive()) {
      if (bar.parentNode) bar.parentNode.removeChild(bar);
      document.querySelectorAll(".lv-usage-inject").forEach((n) => n.remove());
      return;
    }
    const card = fileCardEl();
    if (!card) return;
    if (bar.parentNode === card && card.firstElementChild === bar) return;
    card.insertBefore(bar, card.firstChild);
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    const gb = 1024 * 1024 * 1024;
    if (n >= gb) return (n / gb).toFixed(2) + "GB";
    const mb = 1024 * 1024;
    if (n >= mb) return (n / mb).toFixed(2) + "MB";
    if (n <= 0) return "0";
    return n + "B";
  }

  function decorateProviders(items) {
    if (!providersTabActive()) {
      document.querySelectorAll(".lv-usage-inject").forEach((n) => n.remove());
      return;
    }
    for (const it of items || []) {
      const name = (it.name || "").trim();
      if (!name) continue;
      const card = fileCardEl(name);
      if (!card) continue;
      const used = (it.upload || 0) + (it.download || 0);
      const total = it.total || 0;
      const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
      const hostText = it.host || "";
      const usageText = total ? fmtBytes(used) + " / " + fmtBytes(total) : "";
      const sig = hostText + "|" + usageText + "|" + pct;
      const old = card.querySelector(":scope > .lv-usage-inject");
      if (old && old.dataset.sig === sig) continue;
      if (old) old.remove();
      const box = document.createElement("div");
      box.className = "lv-usage-inject";
      box.dataset.sig = sig;
      box.innerHTML =
        '<div class="lv-host"></div><div class="lv-usage"></div><div class="lv-bar"><span></span></div>';
      box.querySelector(".lv-host").textContent = hostText;
      box.querySelector(".lv-usage").textContent = usageText;
      box.querySelector(".lv-bar > span").style.width = pct + "%";
      if (!total) box.querySelector(".lv-bar").style.display = "none";
      card.appendChild(box);
    }
  }

  async function loadSubs() {
    if (!providersTabActive()) return;
    const secret = findSecret();
    if (!secret) return;
    try {
      const res = await fetch("/api/litevpn/subs", {
        headers: { Authorization: "Bearer " + secret, "Content-Type": "application/json" },
      });
      if (!res.ok) return;
      const data = await res.json();
      decorateProviders(data.items || []);
    } catch {
      /* ignore */
    }
  }

  async function doImport() {
    const input = document.getElementById("litevpn-url");
    const btn = document.getElementById("litevpn-import-btn");
    const box = document.getElementById("litevpn-msg");
    if (!input || !btn || !box) return;
    const url = (input.value || "").trim();
    box.className = "";
    if (!url) {
      box.textContent = "请填写链接";
      box.className = "err";
      return;
    }
    const secret = needSecret();
    if (!secret) return;
    btn.disabled = true;
    box.textContent = "导入中…";
    try {
      const res = await fetch("/api/litevpn/import", {
        method: "POST",
        headers: { Authorization: "Bearer " + secret, "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status === 401) sessionStorage.removeItem("litevpn.secret");
        throw new Error((data && data.error) || "导入失败");
      }
      box.textContent = "已导入";
      box.className = "ok";
      input.value = "";
      setTimeout(() => location.reload(), 500);
    } catch (e) {
      box.textContent = e.message || "导入失败";
      box.className = "err";
    }
    btn.disabled = false;
  }

  bar.addEventListener("click", (e) => {
    if (e.target && e.target.id === "litevpn-import-btn") doImport();
  });
  bar.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target && e.target.id === "litevpn-url") doImport();
  });
  document.addEventListener("click", () => setTimeout(() => { placeBar(); loadSubs(); }, 60), true);
  window.addEventListener("hashchange", () => setTimeout(() => { placeBar(); loadSubs(); }, 60));
  const obs = new MutationObserver(() => placeBar());
  const start = () => {
    if (!document.body) return setTimeout(start, 50);
    obs.observe(document.body, { subtree: true, childList: true });
    placeBar();
    loadSubs();
    setInterval(() => {
      placeBar();
      loadSubs();
    }, 2000);
  };
  start();
})();
