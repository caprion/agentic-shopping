/* Aria demo UI — vanilla JS, no build step, no external dependencies. */
(function () {
  "use strict";

  var sessionId = newSessionId();
  var persona = "guest";
  var systemOn = false;

  var el = {
    transcript: document.getElementById("transcript"),
    input: document.getElementById("input"),
    send: document.getElementById("send"),
    mic: document.getElementById("mic"),
    imageBtn: document.getElementById("image-btn"),
    imageInput: document.getElementById("image-input"),
    dropzone: document.getElementById("dropzone"),
    persona: document.getElementById("persona"),
    demoSel: document.getElementById("demo-scripts"),
    reset: document.getElementById("reset"),
    systemToggle: document.getElementById("system-toggle"),
    rightPane: document.getElementById("right-pane"),
    collapse: document.getElementById("collapse"),
    cartItems: document.getElementById("cart-items"),
    cartCount: document.getElementById("cart-count"),
    budgetText: document.getElementById("budget-text"),
    budgetTotal: document.getElementById("budget-total"),
    budgetFill: document.getElementById("budget-fill"),
    checkout: document.getElementById("checkout"),
    orderResult: document.getElementById("order-result"),
    sessionBody: document.getElementById("session-body"),
  };

  var currentTurn = null; // per-turn streaming accumulator

  function newSessionId() {
    return "s-" + Math.random().toString(36).slice(2, 10) +
      Date.now().toString(36).slice(-4);
  }

  function userId() {
    return persona === "guest" ? null : "user_" + persona;
  }

  /* ------------------------------------------------------------- helpers */
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function addMsg(text, role) {
    var msg = document.createElement("div");
    msg.className = "msg " + role;
    var b = document.createElement("div");
    b.className = "bubble";
    b.textContent = text;
    msg.appendChild(b);
    el.transcript.appendChild(msg);
    el.transcript.scrollTop = el.transcript.scrollHeight;
    return msg;
  }

  function ensureTurn() {
    if (currentTurn && currentTurn.root.isConnected) return currentTurn;
    var root = document.createElement("div");
    root.className = "msg assistant turn";
    var toolRow = document.createElement("div");
    toolRow.className = "tool-row";
    var answerEl = document.createElement("div");
    answerEl.className = "bubble answer";
    var citationsEl = document.createElement("div");
    citationsEl.className = "citations";
    var productsEl = document.createElement("div");
    productsEl.className = "products";
    var meta = document.createElement("div");
    meta.className = "turn-meta";
    root.appendChild(toolRow);
    root.appendChild(answerEl);
    root.appendChild(citationsEl);
    root.appendChild(productsEl);
    root.appendChild(meta);
    el.transcript.appendChild(root);
    currentTurn = {
      root: root, toolRow: toolRow, answerEl: answerEl,
      citationsEl: citationsEl, productsEl: productsEl, meta: meta,
      answerText: "", tools: [], citations: [], productIds: [],
      latency: null, tokensIn: 0, tokensOut: 0, flags: []
    };
    return currentTurn;
  }

  function setCursor(on) {
    if (!currentTurn) return;
    var c = currentTurn.answerEl.querySelector(".cursor");
    if (on && !c) {
      c = document.createElement("span");
      c.className = "cursor";
      currentTurn.answerEl.appendChild(c);
    } else if (!on && c) {
      c.remove();
    }
  }

  /* -------------------------------------------------------- event stream */
  function handleEvent(ev) {
    if (!ev || typeof ev !== "object") return;
    switch (ev.type) {
      case "token":
        var t = ensureTurn();
        t.answerText += ev.text || "";
        t.answerEl.textContent = t.answerText;
        setCursor(true);
        break;
      case "tool":
        var t2 = ensureTurn();
        t2.tools.push({ name: ev.name, args: ev.args });
        var chip = document.createElement("span");
        chip.className = "chip";
        var n = document.createElement("span");
        n.className = "t";
        n.textContent = ev.name || "tool";
        chip.appendChild(n);
        if (ev.name === "search_products" && ev.args) {
          var h = document.createElement("span");
          h.className = "h";
          h.textContent = "query \u201c" + ((ev.args.query || "").slice(0, 40)) + "\u201d";
          chip.appendChild(h);
        }
        t2.toolRow.appendChild(chip);
        break;
      case "citation":
        var t3 = ensureTurn();
        if (ev.citation) t3.citations.push(ev.citation);
        renderCitation(t3, ev.citation);
        break;
      case "done":
        var t4 = ensureTurn();
        setCursor(false);
        if (!t4.answerText && ev.answer) {
          t4.answerText = ev.answer;
          t4.answerEl.textContent = ev.answer;
        }
        (ev.citations || []).forEach(function (c) {
          if (!t4.citations.includes(c)) { t4.citations.push(c); renderCitation(t4, c); }
        });
        (ev.product_ids || []).forEach(function (p) { if (!t4.productIds.includes(p)) t4.productIds.push(p); });
        t4.latency = ev.latency_ms; t4.tokensIn = ev.tokens_in || 0;
        t4.tokensOut = ev.tokens_out || 0; t4.flags = ev.flags || [];
        renderProducts(t4, t4.productIds.slice(0, 6));
        renderTurnMeta(t4);
        refreshState();
        break;
    }
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  /* ------------------------------------------------------------- citations */
  function renderCitation(turn, citation) {
    if (!citation) return;
    var pill = document.createElement("span");
    pill.className = "cite";
    var field = citation.field || citation.review_id || "source";
    pill.textContent = "[" + field + "]";
    pill.title = "Show source";
    var card = document.createElement("div");
    card.className = "cite-card";
    card.style.display = "none";
    var html = "";
    if (citation.quote) html += "<div class='q'>\u201c" + esc(citation.quote) + "\u201d</div>";
    if (citation.value != null) html += "<div class='v'>" + esc(citation.value) + "</div>";
    html += "<div class='k'>" + esc(field) + " · " + esc(citation.product_id || "") + "</div>";
    card.innerHTML = html;
    pill.addEventListener("click", function () {
      card.style.display = card.style.display === "none" ? "block" : "none";
    });
    turn.citationsEl.appendChild(pill);
    turn.citationsEl.appendChild(card);
  }

  /* -------------------------------------------------------------- products */
  function renderProducts(turn, productIds) {
    if (!productIds.length) return;
    productIds.forEach(function (pid) {
      fetch("/api/product/" + encodeURIComponent(pid))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || !d.product) return;
          turn.productsEl.appendChild(productCard(d.product));
        })
        .catch(function () {});
    });
  }

  function productCard(p) {
    var card = document.createElement("div");
    card.className = "pcard";
    var img = document.createElement("div");
    img.className = "img";
    if (p.image_url) {
      img.style.backgroundImage = "url(" + p.image_url + ")";
    } else {
      img.textContent = "📦";
    }
    var body = document.createElement("div");
    body.className = "body";
    var title = document.createElement("div");
    title.className = "title";
    title.textContent = p.title || "";
    var brand = document.createElement("div");
    brand.className = "brand";
    brand.textContent = p.brand || "";
    var priceRow = document.createElement("div");
    priceRow.className = "price-row";
    var price = document.createElement("span");
    price.className = "price";
    price.textContent = fmtMoney(p.price);
    priceRow.appendChild(price);
    if (p.price_mrp && p.price_mrp > p.price) {
      var mrp = document.createElement("span");
      mrp.className = "mrp";
      mrp.textContent = fmtMoney(p.price_mrp);
      priceRow.appendChild(mrp);
      var disc = document.createElement("span");
      disc.className = "discount";
      var pct = Math.round((1 - p.price / p.price_mrp) * 100);
      disc.textContent = pct + "% off";
      priceRow.appendChild(disc);
    }
    var rating = document.createElement("span");
    rating.className = "rating";
    rating.textContent = p.rating ? ("★ " + p.rating) : "";
    priceRow.appendChild(rating);
    body.appendChild(title); body.appendChild(brand); body.appendChild(priceRow);
    var actions = document.createElement("div");
    actions.className = "actions";
    var btn = document.createElement("button");
    btn.className = "mini-btn";
    btn.textContent = "Add to cart";
    btn.addEventListener("click", function () { addToCart(p.product_id, p.title, p.price); });
    actions.appendChild(btn);
    card.appendChild(img); card.appendChild(body); card.appendChild(actions);
    return card;
  }

  function fmtMoney(v) {
    if (v == null || isNaN(v)) return "—";
    var n = Number(v);
    return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }

  function renderTurnMeta(turn) {
    if (!turn || !turn.meta) return;
    var lines = [];
    if (turn.tools.length) {
      lines.push("<span class='section-label'>Tools</span><div class='tag-list'>" +
        turn.tools.map(function (t) { return "<span class='tag'>" + esc(t.name) + "</span>"; }).join("") +
        "</div>");
    }
    lines.push("<div class='kv'><span class='k'>latency</span><span class='v'>" +
      (turn.latency == null ? "—" : turn.latency + " ms") + "</span></div>");
    lines.push("<div class='kv'><span class='k'>tokens</span><span class='v'>" +
      turn.tokensIn + " in / " + turn.tokensOut + " out</span></div>");
    lines.push("<div class='kv'><span class='k'>citations</span><span class='v'>" +
      turn.citations.length + "</span></div>");
    if (turn.flags && turn.flags.length) {
      lines.push("<span class='section-label'>Flags</span><div class='tag-list'>" +
        turn.flags.map(function (f) { return "<span class='tag flag'>" + esc(f) + "</span>"; }).join("") +
        "</div>");
    }
    turn.meta.innerHTML = lines.join("");
    turn.meta.style.display = systemOn ? "block" : "none";
  }

  /* ---------------------------------------------------------------- chat */
  function sendMessage(text) {
    text = (text || "").trim();
    if (!text) return;
    addMsg(text, "user");
    currentTurn = null;
    el.send.disabled = true;
    el.input.value = "";

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId, user_id: userId(), message: text, stream: true
      })
    }).then(function (resp) {
      if (!resp.body) throw new Error("no stream body");
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buf = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) { setCursor(false); el.send.disabled = false; return; }
          buf += decoder.decode(r.value, { stream: true });
          var idx;
          while ((idx = buf.indexOf("\n\n")) !== -1) {
            var frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            frame.split("\n").forEach(function (line) {
              if (line.indexOf("data: ") === 0) {
                try { handleEvent(JSON.parse(line.slice(6))); } catch (e) {}
              }
            });
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      var t = ensureTurn();
      t.answerText = "Request failed: " + err.message;
      t.answerEl.textContent = t.answerText;
      el.send.disabled = false;
    });
  }

  /* ---------------------------------------------------------------- cart */
  function addToCart(pid, title, price) {
    fetch("/api/cart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, product_id: pid, qty: 1 })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ok !== false) { renderCart(d); refreshState(); }
      }).catch(function () {});
  }

  function renderCart(d) {
    var items = d && d.items ? d.items : [];
    el.cartCount.textContent = items.length;
    if (!items.length) {
      el.cartItems.innerHTML = "<div class='empty'>Cart is empty</div>";
      el.budgetFill.style.width = "0";
      el.budgetTotal.textContent = "";
      return;
    }
    el.cartItems.innerHTML = "";
    items.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "citem";
      var thumb = document.createElement("div");
      thumb.className = "thumb";
      thumb.textContent = "📦";
      if (it.image_url) thumb.style.backgroundImage = "url(" + it.image_url + ")";
      var info = document.createElement("div");
      info.className = "info";
      var name = document.createElement("div");
      name.className = "name";
      name.textContent = it.title || it.product_id;
      var sub = document.createElement("div");
      sub.className = "sub";
      sub.textContent = fmtMoney(it.unit_price) + " × " + (it.qty || 1) +
        " = " + fmtMoney(it.line_total);
      info.appendChild(name); info.appendChild(sub);
      row.appendChild(thumb); row.appendChild(info);
      el.cartItems.appendChild(row);
    });
    var total = d.total || 0;
    el.budgetTotal.textContent = fmtMoney(total);
    var max = budgetCap();
    var pct = max > 0 ? Math.min(100, (total / max) * 100) : 0;
    el.budgetFill.style.width = pct + "%";
    el.budgetFill.classList.toggle("over", max > 0 && total > max);
  }

  var budgetCapVal = null;
  var SESSION_BUDGET_USD = 2000; // USD session cap (guardrails/tools default)

  function budgetCap() {
    return budgetCapVal || SESSION_BUDGET_USD;
  }

  function checkout() {
    el.orderResult.innerHTML = "";
    var shipTo = "Home, US (mock)"; // mock US delivery address
    fetch("/api/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, ship_to: shipTo })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.order_id) {
          el.orderResult.innerHTML = "<div class='ok'>✓ Order <b>" + esc(d.order_id) +
            "</b> · total " + fmtMoney(d.total) + " · ETA " + d.eta_days + " days</div>";
          renderCart({ items: [], total: 0 });
          refreshState();
        } else {
          el.orderResult.innerHTML = "<div class='err'>" + esc((d && d.error) || "checkout failed") + "</div>";
        }
      }).catch(function (e) {
        el.orderResult.innerHTML = "<div class='err'>" + esc(e.message) + "</div>";
      });
  }

  /* -------------------------------------------------------------- session */
  function refreshState() {
    fetch("/api/session/" + encodeURIComponent(sessionId))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        renderSession(d);
        if (d.cart) renderCart(d.cart);
      }).catch(function () {});
  }

  function renderSession(d) {
    var c = d.constraints || {};
    budgetCapVal = (d.budget != null) ? d.budget : SESSION_BUDGET_USD;
    el.budgetText.textContent = "Budget ≤ " + fmtMoney(budgetCapVal);

    var parts = [];
    if (c.free_text) parts.push("<div class='constraint'>query: " + esc(c.free_text) + "</div>");
    var kv = [];
    if (c.price_min != null) kv.push("min " + fmtMoney(c.price_min));
    if (c.price_max != null) kv.push("max " + fmtMoney(c.price_max));
    if (c.rating_min != null) kv.push("≥" + c.rating_min + "★");
    if (c.brand && c.brand.length) kv.push("brand: " + c.brand.join(", "));
    if (c.category) kv.push("cat: " + c.category);
    if (c.sort) kv.push("sort: " + c.sort);
    if (kv.length) parts.push("<div class='kv'><span class='k'>constraints</span><span class='v'>" +
      esc(kv.join(" · ")) + "</span></div>");

    if (d.flags && d.flags.length) {
      parts.push("<span class='section-label'>Flags</span><div class='tag-list'>" +
        d.flags.map(function (f) { return "<span class='tag flag'>" + esc(f) + "</span>"; }).join("") +
        "</div>");
    }
    if (d.tools && d.tools.length) {
      parts.push("<span class='section-label'>Tools</span><div class='tag-list'>" +
        d.tools.map(function (t) { return "<span class='tag'>" + esc(t) + "</span>"; }).join("") +
        "</div>");
    }
    parts.push("<div class='kv'><span class='k'>latency</span><span class='v'>" +
      (d.latency_ms == null ? "—" : d.latency_ms + " ms") + "</span></div>");
    parts.push("<div class='kv'><span class='k'>tokens</span><span class='v'>" +
      (d.tokens_in || 0) + " in / " + (d.tokens_out || 0) + " out</span></div>");
    if (d.product_ids && d.product_ids.length) {
      parts.push("<div class='kv'><span class='k'>products</span><span class='v'>" +
        d.product_ids.length + " cited</span></div>");
    }
    var nCite = d.citations_count != null ? d.citations_count
      : (d.recent_citations || []).length;
    parts.push("<div class='kv'><span class='k'>citations</span><span class='v'>" +
      nCite + "</span></div>");
    el.sessionBody.innerHTML = parts.join("") || "<div class='empty'>No session yet</div>";
  }

  /* ------------------------------------------------------------- personas */
  var PERSONAS = {
    guest: "Browsing as a guest — no saved taste profile. Try a code-mixed query: “saste earphones under $50 for mom”.",
    alex: "Alex buys gifts and watches the price hard — expect the cheapest option that clears the bar.",
    rhea: "Rhea pays for quality: warranty, rating evidence and review proof before she commits."
  };

  function renderPersonaHint() {
    var hint = document.getElementById("persona-hint");
    if (!hint) return;
    hint.textContent = PERSONAS[persona] || PERSONAS.guest;
  }

  /* ---------------------------------------------------------------- voice */
  function setupMic() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      el.mic.title = "Speech recognition not available in this browser";
      return;
    }
    var rec = new SR();
    rec.lang = "en-IN";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    var listening = false;
    el.mic.addEventListener("click", function () {
      if (listening) { rec.stop(); return; }
      try { rec.start(); } catch (e) { return; }
      listening = true;
      el.mic.classList.add("rec");
    });
    rec.onresult = function (e) {
      var text = e.results[0][0].transcript;
      el.input.value = text;
      // send the transcript through /api/voice first (graceful), then chat
      fetch("/api/voice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, transcript: text })
      }).then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.transcript) sendMessage(d.transcript);
        }).catch(function () { sendMessage(text); });
    };
    rec.onerror = function () { listening = false; el.mic.classList.remove("rec"); };
    rec.onend = function () { listening = false; el.mic.classList.remove("rec"); };
  }

  /* ---------------------------------------------------------------- image */
  function setupImage() {
    el.imageBtn.addEventListener("click", function () { el.imageInput.click(); });
    el.imageInput.addEventListener("change", function () {
      if (el.imageInput.files && el.imageInput.files[0]) uploadImage(el.imageInput.files[0]);
    });
    var depth = 0;
    window.addEventListener("dragenter", function (e) { e.preventDefault(); depth++; el.dropzone.hidden = false; });
    window.addEventListener("dragleave", function (e) { e.preventDefault(); if (--depth <= 0) { depth = 0; el.dropzone.hidden = true; } });
    window.addEventListener("dragover", function (e) { e.preventDefault(); });
    window.addEventListener("drop", function (e) {
      e.preventDefault(); depth = 0; el.dropzone.hidden = true;
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        uploadImage(e.dataTransfer.files[0]);
      }
    });
  }

  function uploadImage(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var dataUrl = reader.result;
      var base64 = dataUrl.split(",")[1] || "";
      addMsg("🖼️ [image search]", "user");
      fetch("/api/image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, image_base64: base64 })
      }).then(function (r) { return r.json(); })
        .then(function (d) {
          var t = ensureTurn();
          currentTurn = t;
          var txt = d.ocr_text ? ("OCR text: \u201c" + d.ocr_text + "\u201d") :
            "Visual search complete" + (d.hints && d.hints.length ? " (" + d.hints.join(", ") + ")" : "");
          t.answerText = txt;
          t.answerEl.textContent = txt;
          var pids = (d.similar_products || []).map(function (s) { return s.product && s.product.product_id; }).filter(Boolean);
          renderProducts(t, pids.slice(0, 6));
          if (d.ocr_text) {
            // also feed the OCR text as a normal search query
            setTimeout(function () { sendMessage(d.ocr_text); }, 200);
          }
        }).catch(function (e) {
          var t = ensureTurn();
          t.answerEl.textContent = "Image search failed: " + e.message;
        });
    };
    reader.readAsDataURL(file);
  }

  /* ------------------------------------------------------------- controls */
  function bindControls() {
    el.send.addEventListener("click", function () { sendMessage(el.input.value); });
    el.input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(el.input.value); }
    });
    el.persona.addEventListener("change", function () {
      persona = el.persona.value;
      renderPersonaHint();
    });
    el.demoSel.addEventListener("change", function () {
      var v = el.demoSel.value;
      el.demoSel.selectedIndex = 0;
      if (v) { el.input.value = v; sendMessage(v); }
    });
    el.reset.addEventListener("click", function () {
      sessionId = newSessionId();
      el.transcript.innerHTML = "";
      el.cartItems.innerHTML = "<div class='empty'>Cart is empty</div>";
      el.cartCount.textContent = "0";
      el.budgetFill.style.width = "0";
      el.orderResult.innerHTML = "";
      el.sessionBody.innerHTML = "<div class='empty'>No session yet</div>";
      addMsg("New session started.", "assistant");
    });
    el.systemToggle.addEventListener("click", function () {
      systemOn = !systemOn;
      el.systemToggle.classList.toggle("active", systemOn);
      document.querySelectorAll(".turn-meta").forEach(function (m) {
        m.style.display = systemOn ? "block" : "none";
      });
    });
    el.collapse.addEventListener("click", function () {
      var collapsed = el.rightPane.classList.toggle("collapsed");
      el.collapse.classList.toggle("flipped", collapsed);
    });
    el.checkout.addEventListener("click", checkout);
  }

  function boot() {
    bindControls();
    setupMic();
    setupImage();
    renderPersonaHint();
    addMsg("Hi, I'm Aria. Ask me to find, compare, or buy something — or pick a demo script up top. Prices are in USD (Amazon US catalogue).", "assistant");
    refreshState();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
