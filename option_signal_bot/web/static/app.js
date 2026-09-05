/* ---------------------------------------------------------------
   داشبورد GarnetTrader — بدون فریم‌ورک، بدون build
   --------------------------------------------------------------- */
"use strict";

// ------------------------------------------------------------------ utils
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const fmt = (n, digits = 0) =>
  n === null || n === undefined || Number.isNaN(n)
    ? "—"
    : Number(n).toLocaleString("fa-IR", { maximumFractionDigits: digits });

function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show " + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast " + kind), 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* پاسخ بدون بدنه */
  }
  if (!res.ok) {
    throw new Error((body && body.detail) || `خطای ${res.status}`);
  }
  return body;
}

// ------------------------------------------------------------------ tabs
$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  btn.classList.add("active");
  $("#panel-" + btn.dataset.tab).classList.add("active");
  if (btn.dataset.tab === "strategies") loadStrategies();
  if (btn.dataset.tab === "symbols") loadSymbols();
  if (btn.dataset.tab === "risk") loadRisk();
  if (btn.dataset.tab === "account") { loadBrokerSetup(); loadAccount(); }
  if (btn.dataset.tab === "report") loadReport();
});

// ------------------------------------------------------------------ status
async function loadStatus() {
  const box = $("#status");
  try {
    const s = await api("/api/status");
    box.innerHTML = "";

    // بازار باز است یا نه
    let mk = "نامشخص", mkCls = "chip-muted";
    if (s.market_open === true) { mk = "بازار باز"; mkCls = "chip-ok"; }
    else if (s.market_open === false) { mk = "بازار بسته"; mkCls = "chip-warn"; }
    box.append(el("span", "chip " + mkCls, mk));

    // منبع داده — پروژه داده ساختگی ندارد، پس همیشه واقعی است
    const src = `${s.market_data_provider}+${s.option_chain_provider}`;
    box.append(el("span", "chip chip-ok", "داده واقعی: " + src));

    box.append(el("span", "chip chip-muted", `${fmt(s.signal_count)} سیگنال ذخیره‌شده`));
  } catch (err) {
    box.innerHTML = "";
    box.append(el("span", "chip chip-bad", "خطا: " + err.message));
  }
}

// ------------------------------------------------------------------ signals
let allSignals = [];

function signalCard(s) {
  const isCall = s.option_type === "call";
  const card = el("div", "sig " + (isCall ? "call" : "put"));

  const head = el("div", "sig-head");
  head.append(el("span", "sig-badge " + (isCall ? "badge-call" : "badge-put"),
    (s.side === "buy" ? "خرید " : "فروش ") + (isCall ? "Call" : "Put")));
  head.append(el("span", "sig-sym", s.symbol));
  head.append(el("span", "sig-strategy", s.strategy_name));
  const when = s.created_at ? new Date(s.created_at).toLocaleString("fa-IR") : "";
  head.append(el("span", "sig-time", when));
  card.append(head);

  const grid = el("div", "sig-grid");
  const cell = (k, v, cls) => {
    const d = el("div");
    d.append(el("span", "k", k));
    d.append(el("span", "v " + (cls || ""), v));
    return d;
  };
  grid.append(cell("نماد پایه", `${s.underlying || "—"} @ ${fmt(s.underlying_price)}`));
  grid.append(cell("قیمت اعمال", fmt(s.strike)));
  grid.append(cell("سررسید", `${s.expiry} (${fmt(s.days_to_expiry)} روز)`));
  grid.append(cell("پرمیوم", fmt(s.suggested_price)));
  grid.append(cell("تعداد", fmt(s.suggested_qty) + " قرارداد"));
  grid.append(cell("حد ضرر", fmt(s.stop_loss), "v-loss"));
  grid.append(cell("حد سود", fmt(s.take_profit), "v-gain"));
  if (s.confidence !== null && s.confidence !== undefined) {
    grid.append(cell("اعتماد", fmt(s.confidence * 100) + "٪"));
  }
  card.append(grid);

  if (s.reason) card.append(el("div", "sig-reason", s.reason));

  const src = s.metadata && s.metadata.data_source;
  if (src) card.append(el("div", "sig-src", "منبع داده: " + src));
  return card;
}

function renderSignals() {
  const strat = $("#filter-strategy").value;
  const und = $("#filter-underlying").value;
  const list = allSignals.filter(
    (s) => (!strat || s.strategy_name === strat) && (!und || s.underlying === und)
  );

  const box = $("#signals");
  box.innerHTML = "";
  if (!list.length) {
    box.append(el("p", "empty",
      allSignals.length
        ? "با این فیلتر سیگنالی نیست."
        : "هنوز سیگنالی ثبت نشده. «اجرای پاس رصد بازار» را بزنید."));
    return;
  }
  list.forEach((s) => box.append(signalCard(s)));
}

function fillFilters() {
  const strategies = [...new Set(allSignals.map((s) => s.strategy_name))].sort();
  const unders = [...new Set(allSignals.map((s) => s.underlying).filter(Boolean))].sort();

  const keep = (sel, values, allLabel) => {
    const prev = sel.value;
    sel.innerHTML = "";
    sel.append(el("option", "", allLabel));
    sel.firstChild.value = "";
    values.forEach((v) => {
      const o = el("option", "", v);
      o.value = v;
      sel.append(o);
    });
    if (values.includes(prev)) sel.value = prev;
  };
  keep($("#filter-strategy"), strategies, "همه استراتژی‌ها");
  keep($("#filter-underlying"), unders, "همه نمادها");
}

async function loadSignals() {
  try {
    const data = await api("/api/signals?limit=200");
    allSignals = data.signals || [];
    fillFilters();
    renderSignals();
  } catch (err) {
    $("#signals").innerHTML = "";
    $("#signals").append(el("div", "error", "خطا در خواندن سیگنال‌ها: " + err.message));
  }
}

async function scan() {
  const btns = [$("#btn-scan")];
  const btn = btns[0];
  const label = btn.textContent;
  btns.forEach((b) => (b.disabled = true));
  btn.innerHTML = '<span class="spin"></span>در حال رصد بازار…';
  $("#scan-result").innerHTML = "";

  try {
    const r = await api("/api/scan", { method: "POST" });
    $("#scan-result").append(
      el("div", "ok-box",
        r.generated
          ? `پاس رصد تمام شد: ${fmt(r.generated)} سیگنال تولید شد.`
          : "پاس رصد تمام شد؛ شرایط هیچ استراتژی برقرار نبود.")
    );
    await Promise.all([loadSignals(), loadStatus()]);
  } catch (err) {
    $("#scan-result").append(el("div", "error", "پاس رصد ناموفق بود: " + err.message));
  } finally {
    btns.forEach((b) => (b.disabled = false));
    btn.textContent = label;
  }
}

$("#btn-scan").addEventListener("click", () => scan());
$("#btn-refresh").addEventListener("click", () => { loadSignals(); loadStatus(); });
$("#filter-strategy").addEventListener("change", renderSignals);
$("#filter-underlying").addEventListener("change", renderSignals);

// ------------------------------------------------------------------ strategies
async function loadStrategies() {
  const box = $("#strategies");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  try {
    const { strategies } = await api("/api/strategies");
    box.innerHTML = "";
    strategies.forEach((st) => box.append(strategyCard(st)));
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

function strategyCard(st) {
  const card = el("div", "card");

  const head = el("div", "strat-head");
  head.append(el("span", "strat-name", st.name));

  const sw = el("label", "switch");
  const cb = el("input");
  cb.type = "checkbox";
  cb.checked = st.enabled;
  const lbl = el("span", "label", st.enabled ? "فعال" : "غیرفعال");
  sw.append(cb, el("span", "track"), lbl);
  head.append(sw);
  card.append(head);

  if (st.doc) card.append(el("div", "strat-doc", st.doc));

  cb.addEventListener("change", async () => {
    cb.disabled = true;
    try {
      await api("/api/strategies/" + encodeURIComponent(st.name), {
        method: "PUT",
        body: JSON.stringify({ enabled: cb.checked }),
      });
      lbl.textContent = cb.checked ? "فعال" : "غیرفعال";
      toast(`«${st.name}» ${cb.checked ? "فعال" : "غیرفعال"} شد.`, "ok");
    } catch (err) {
      cb.checked = !cb.checked;
      toast("خطا: " + err.message, "bad");
    } finally {
      cb.disabled = false;
    }
  });

  // پارامترها
  const params = el("div", "params");
  const inputs = {};
  Object.entries(st.params).forEach(([key, val]) => {
    const f = el("div", "field");
    f.append(el("label", "", key));
    const inp = el("input");
    const numeric = typeof val === "number";
    inp.type = numeric ? "number" : "text";
    if (numeric && !Number.isInteger(val)) inp.step = "0.01";
    inp.value = val;
    f.append(inp);
    const dflt = st.defaults[key];
    if (dflt !== undefined) f.append(el("span", "dflt", "پیش‌فرض: " + dflt));
    inp.addEventListener("input", () =>
      f.classList.toggle("changed", String(inp.value) !== String(val))
    );
    inputs[key] = { inp, original: val, numeric };
    params.append(f);
  });
  card.append(params);

  const actions = el("div", "card-actions");
  const save = el("button", "btn btn-primary btn-sm", "ذخیره پارامترها");
  const note = el("span", "note");
  actions.append(save, note);
  card.append(actions);

  save.addEventListener("click", async () => {
    const patch = {};
    let bad = null;
    for (const [key, { inp, original, numeric }] of Object.entries(inputs)) {
      if (String(inp.value) === String(original)) continue;
      if (numeric) {
        const n = Number(inp.value);
        if (!Number.isFinite(n)) { bad = key; break; }
        patch[key] = n;
      } else {
        patch[key] = inp.value;
      }
    }
    if (bad) { note.className = "note bad"; note.textContent = `مقدار «${bad}» عدد نیست.`; return; }
    if (!Object.keys(patch).length) { note.className = "note"; note.textContent = "تغییری نبود."; return; }

    save.disabled = true;
    note.className = "note";
    note.textContent = "در حال ذخیره…";
    try {
      await api("/api/strategies/" + encodeURIComponent(st.name), {
        method: "PUT",
        body: JSON.stringify({ params: patch }),
      });
      note.className = "note ok";
      note.textContent = "ذخیره شد؛ از پاس بعدی اعمال می‌شود.";
      toast(`پارامترهای «${st.name}» ذخیره شد.`, "ok");
      loadStrategies();
    } catch (err) {
      note.className = "note bad";
      note.textContent = err.message;
    } finally {
      save.disabled = false;
    }
  });

  return card;
}

// ------------------------------------------------------------------ symbols
let watched = new Set();

async function loadSymbols() {
  // این درخواست کل بازار را می‌گیرد و چند ثانیه طول می‌کشد. بدون این حالت
  // انتظار، تب چند ثانیه کاملاً خالی می‌ماند و شکسته به نظر می‌رسد.
  const avail = $("#available");
  avail.innerHTML = "";
  avail.append(el("span", "note", "در حال گرفتن لیست بازار…"));

  try {
    const d = await api("/api/symbols");
    watched = new Set(d.watched || []);
    renderWatched();

    avail.innerHTML = "";
    $("#available-count").textContent = d.available.length ? `(${d.available.length})` : "";

    if (d.error) {
      avail.append(
        el("div", "error", "لیست بازار در دسترس نیست (بازار بسته یا شبکه قطع): " + d.error)
      );
      return;
    }
    renderAvailable(d.available);
    $("#symbol-search").oninput = () => renderAvailable(d.available);
  } catch (err) {
    avail.innerHTML = "";
    avail.append(el("div", "error", "خطا: " + err.message));
    $("#watched").innerHTML = "";
    $("#watched").append(el("div", "error", "خطا: " + err.message));
  }
}

function renderWatched() {
  const box = $("#watched");
  box.innerHTML = "";
  $("#watched-count").textContent = watched.size ? `(${watched.size})` : "";
  if (!watched.size) {
    box.append(el("span", "note", "هیچ نمادی انتخاب نشده."));
    return;
  }
  [...watched].sort().forEach((s) => {
    const chip = el("span", "sym on");
    chip.append(document.createTextNode(s));
    chip.append(el("span", "x", "×"));
    chip.title = "حذف از لیست رصد";
    chip.onclick = () => { watched.delete(s); renderWatched(); };
    box.append(chip);
  });
}

function renderAvailable(list) {
  const q = ($("#symbol-search").value || "").trim();
  const box = $("#available");
  box.innerHTML = "";
  const shown = q ? list.filter((s) => s.includes(q)) : list;
  if (!shown.length) {
    box.append(el("span", "note", "نمادی پیدا نشد."));
    return;
  }
  shown.forEach((s) => {
    const chip = el("span", "sym" + (watched.has(s) ? " on" : ""), s);
    chip.title = watched.has(s) ? "در لیست رصد است" : "افزودن به لیست رصد";
    chip.onclick = () => {
      watched.has(s) ? watched.delete(s) : watched.add(s);
      renderWatched();
      renderAvailable(list);
    };
    box.append(chip);
  });
}

$("#btn-save-symbols").addEventListener("click", async () => {
  const note = $("#symbols-note");
  if (!watched.size) {
    note.className = "note bad";
    note.textContent = "حداقل یک نماد لازم است.";
    return;
  }
  note.className = "note";
  note.textContent = "در حال ذخیره…";
  try {
    await api("/api/symbols", {
      method: "PUT",
      body: JSON.stringify({ symbols: [...watched] }),
    });
    note.className = "note ok";
    note.textContent = `${watched.size} نماد ذخیره شد.`;
    toast("لیست نمادها ذخیره شد.", "ok");
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

// ------------------------------------------------------------------ risk
const RISK_LABELS = {
  account_equity: "دارایی حساب (ریال)",
  risk_per_trade_pct: "درصد ریسک در هر معامله",
  max_position_pct: "سقف درصد دارایی در یک پوزیشن",
  max_contracts: "سقف تعداد قرارداد",
  stop_loss_pct: "درصد حد ضرر",
  take_profit_pct: "درصد حد سود",
};

async function loadRisk() {
  const box = $("#risk-fields");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  try {
    const risk = await api("/api/risk");
    box.innerHTML = "";
    Object.entries(RISK_LABELS).forEach(([key, label]) => {
      if (!(key in risk)) return;
      const f = el("div", "field");
      f.style.marginBottom = "11px";
      f.append(el("label", "", label));
      const inp = el("input");
      inp.type = "number";
      inp.step = key === "max_contracts" || key === "account_equity" ? "1" : "0.1";
      inp.value = risk[key];
      inp.dataset.key = key;
      inp.dataset.original = risk[key];
      inp.addEventListener("input", () =>
        f.classList.toggle("changed", inp.value !== inp.dataset.original)
      );
      f.append(inp);
      box.append(f);
    });
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

$("#btn-save-risk").addEventListener("click", async () => {
  const note = $("#risk-note");
  const patch = {};
  document.querySelectorAll("#risk-fields input").forEach((inp) => {
    if (inp.value !== inp.dataset.original) patch[inp.dataset.key] = Number(inp.value);
  });
  if (!Object.keys(patch).length) {
    note.className = "note";
    note.textContent = "تغییری نبود.";
    return;
  }
  note.className = "note";
  note.textContent = "در حال ذخیره…";
  try {
    await api("/api/risk", { method: "PUT", body: JSON.stringify(patch) });
    note.className = "note ok";
    note.textContent = "ذخیره شد؛ از پاس بعدی اعمال می‌شود.";
    toast("تنظیمات ریسک ذخیره شد.", "ok");
    loadRisk();
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

// ------------------------------------------------------------------ account
async function loadAccount() {
  const box = $("#account");
  box.innerHTML = '<p class="empty">در حال خواندن حساب…</p>';
  try {
    const d = await api("/api/account");
    box.innerHTML = "";

    if (!d.enabled) {
      box.append(
        el("div", "hint",
          "اتصال به حساب کارگزاری خاموش است. برای روشن کردن، در " +
          "config/settings.yaml مقدار broker.enabled را true بگذارید و " +
          "با 3-discover-api.bat یک بار لاگین کنید.")
      );
      return;
    }
    if (d.reason) {
      box.append(el("div", "error", d.reason));
      return;
    }
    if (!d.positions.length) {
      box.append(el("p", "empty", "پوزیشن باز آپشنی ندارید."));
      return;
    }

    d.positions.forEach((p) => {
      const card = el("div", "sig " + (p.is_long ? "call" : "put"));
      const head = el("div", "sig-head");
      head.append(el("span", "sig-badge " + (p.is_long ? "badge-call" : "badge-put"),
        p.is_long ? "خرید" : "فروش"));
      head.append(el("span", "sig-sym", p.symbol_name || p.symbol_isin));
      if (p.cash_settlement_date) {
        head.append(el("span", "sig-time", "تسویه نقدی: " + p.cash_settlement_date));
      }
      card.append(head);

      const grid = el("div", "sig-grid");
      const cell = (k, v, cls) => {
        const dv = el("div");
        dv.append(el("span", "k", k));
        dv.append(el("span", "v " + (cls || ""), v));
        return dv;
      };
      grid.append(cell("تعداد", fmt(p.quantity) + " قرارداد"));
      grid.append(cell("قیمت اعمال", fmt(p.strike_price)));
      grid.append(cell("میانگین خرید", fmt(p.buy_average_price)));
      grid.append(cell("میانگین فروش", fmt(p.sell_average_price)));
      grid.append(cell("وجه تضمین", fmt(p.total_margin)));
      if (p.open_buy_quantity) grid.append(cell("سفارش خرید باز", fmt(p.open_buy_quantity)));
      if (p.open_sell_quantity) grid.append(cell("سفارش فروش باز", fmt(p.open_sell_quantity)));
      if (p.closed_pnl) {
        grid.append(cell("سود/زیان بسته‌شده", fmt(p.closed_pnl),
          p.closed_pnl >= 0 ? "v-gain" : "v-loss"));
      }
      card.append(grid);
      box.append(card);
    });
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

$("#btn-refresh-account").addEventListener("click", loadAccount);


// ------------------------------------------------------------------ report
const pct = (v) => (v === null || v === undefined ? "نامعلوم" : fmt(v * 100, 1) + "٪");
const pnl = (v) =>
  v === null || v === undefined ? "—" : (v >= 0 ? "+" : "") + fmt(v, 1) + "٪";

async function loadReport() {
  const box = $("#report");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  const win = $("#report-window").value;
  try {
    const d = await api("/api/report" + (win ? `?days=${win}` : ""));
    box.innerHTML = "";

    // --- خلاصه ---
    const s = d.summary;
    const cards = el("div", "stat-row");
    const stat = (label, value, cls) => {
      const c = el("div", "stat");
      c.append(el("div", "stat-v " + (cls || ""), value));
      c.append(el("div", "stat-k", label));
      return c;
    };
    cards.append(stat("کل سیگنال", fmt(s.total)));
    cards.append(stat("برد", fmt(s.wins), "v-gain"));
    cards.append(stat("باخت", fmt(s.losses), "v-loss"));
    cards.append(stat("در انتظار", fmt(s.pending)));
    cards.append(stat("نرخ برد", pct(s.win_rate)));
    cards.append(stat("میانگین سود", pnl(s.avg_pnl_pct),
      s.avg_pnl_pct >= 0 ? "v-gain" : "v-loss"));
    box.append(cards);

    if (s.pending === s.total && s.total > 0) {
      box.append(el("div", "hint",
        "هیچ سیگنالی هنوز ارزیابی نشده. دکمه «ارزیابی» را بزنید تا قیمت " +
        "فعلی از بازار خوانده و نتیجه ثبت شود."));
    }

    // --- به تفکیک استراتژی ---
    if (d.by_strategy.length) {
      const card = el("div", "card");
      card.append(el("h3", "", "به تفکیک استراتژی"));
      card.append(buildTable(
        ["استراتژی", "کل", "برد", "باخت", "در انتظار", "نرخ برد", "میانگین", "بهترین", "بدترین"],
        d.by_strategy.map((r) => [
          r.strategy, fmt(r.total), fmt(r.wins), fmt(r.losses), fmt(r.pending),
          pct(r.win_rate), pnl(r.avg_pnl_pct), pnl(r.best_pnl_pct), pnl(r.worst_pnl_pct),
        ])));
      box.append(card);
    }

    // --- به تفکیک نماد ---
    if (d.by_underlying.length) {
      const card = el("div", "card");
      card.append(el("h3", "", "به تفکیک نماد پایه"));
      card.append(buildTable(
        ["نماد", "کل", "برد", "باخت", "میانگین سود"],
        d.by_underlying.map((r) => [
          r.underlying || "—", fmt(r.total), fmt(r.wins), fmt(r.losses), pnl(r.avg_pnl_pct),
        ])));
      box.append(card);
    }

    // --- سیگنال‌های اخیر ---
    if (d.recent.length) {
      const card = el("div", "card");
      card.append(el("h3", "", `سیگنال‌های اخیر (${d.recent.length})`));
      card.append(buildTable(
        ["تاریخ", "نماد", "استراتژی", "پرمیوم", "قیمت فعلی", "سود/زیان", "نتیجه"],
        d.recent.map((r) => [
          (r.created_at || "").slice(0, 16).replace("T", " "),
          r.symbol, r.strategy_name, fmt(r.suggested_price),
          r.price_at_check ? fmt(r.price_at_check) : "—",
          pnl(r.pnl_pct), outcomeLabel(r.outcome),
        ])));
      box.append(card);
    }
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

function outcomeLabel(o) {
  return { win: "برد", loss: "باخت", pending: "در انتظار",
           expired: "منقضی", unknown: "نامعلوم" }[o] || o;
}

function buildTable(headers, rows) {
  const wrap = el("div", "table-wrap");
  const t = el("table");
  const thead = el("thead");
  const hr = el("tr");
  headers.forEach((h) => hr.append(el("th", "", h)));
  thead.append(hr);
  t.append(thead);
  const tb = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr");
    row.forEach((cell) => {
      const td = el("td", "", String(cell));
      if (String(cell).startsWith("+")) td.className = "v-gain";
      else if (String(cell).startsWith("-")) td.className = "v-loss";
      tr.append(td);
    });
    tb.append(tr);
  });
  t.append(tb);
  wrap.append(t);
  return wrap;
}

$("#btn-refresh-report").addEventListener("click", loadReport);
$("#report-window").addEventListener("change", () => {
  const w = $("#report-window").value;
  $("#btn-export").href = "/api/report/export" + (w ? `?days=${w}` : "");
  loadReport();
});

$("#btn-evaluate").addEventListener("click", async () => {
  const btn = $("#btn-evaluate");
  const label = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>در حال خواندن قیمت‌ها…';
  $("#evaluate-result").innerHTML = "";
  try {
    const r = await api("/api/report/evaluate", { method: "POST" });
    $("#evaluate-result").append(el("div", "ok-box",
      `${fmt(r.evaluated)} سیگنال ارزیابی شد` +
      (r.skipped ? `، ${fmt(r.skipped)} رد شد (قیمت در دسترس نبود).` : ".")));
    await loadReport();
  } catch (err) {
    $("#evaluate-result").append(el("div", "error", "ارزیابی ناموفق بود: " + err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
});

// ------------------------------------------------------------------ broker setup
async function loadBrokerSetup() {
  try {
    const d = await api("/api/account");
    $("#broker-enabled").checked = !!d.enabled;
  } catch {
    /* تب حساب خودش خطا را نشان می‌دهد */
  }
}

$("#btn-save-broker").addEventListener("click", async () => {
  const note = $("#broker-note");
  const body = { enabled: $("#broker-enabled").checked };
  const token = $("#broker-token").value.trim();
  if (token) body.token = token;

  note.className = "note";
  note.textContent = "در حال ذخیره…";
  try {
    await api("/api/broker", { method: "PUT", body: JSON.stringify(body) });
    note.className = "note ok";
    note.textContent = "ذخیره شد.";
    $("#broker-token").value = "";  // توکن در فرم نمی‌ماند
    toast("تنظیمات کارگزاری ذخیره شد.", "ok");
    await loadAccount();
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

// ------------------------------------------------------------------ boot
loadStatus();
loadSignals();
setInterval(loadStatus, 60000);
