/* Integration-owned custom panel. No dashboard resources or runtime dependencies. */
(() => {
  const PREFIX = "activity_images/library/";
  const PAGE_SIZE = 48;
  const ACTIVE = new Set(["pending", "running"]);
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const button = (text, action) => {
    const node = el("button", text);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  };
  const message = (error) => error?.message || String(error);
  const style = `
    :host { display:flex; flex-direction:column; height:100vh; height:100dvh; min-height:0; overflow:hidden; color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family, sans-serif); background:var(--lovelace-background, var(--primary-background-color)); }
    * { box-sizing:border-box; } [hidden] { display:none !important; }
    header { display:flex; flex-shrink:0; align-items:center; gap:12px; min-height:64px; padding:8px 16px; background:var(--app-header-background-color, var(--card-background-color)); color:var(--app-header-text-color, var(--primary-text-color)); }
    h1 { font-size:22px; font-weight:500; margin:0; flex:1; } h2 { font-size:20px; font-weight:500; } h3 { font-size:16px; }
    main { flex:1; min-height:0; overflow:auto; width:100%; max-width:1440px; margin:0 auto; padding:24px; } .row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; } .toolbar { margin:16px 0; } .grow { flex:1; min-width:180px; }
    button, input, select { font:inherit; color:inherit; min-height:44px; border:1px solid var(--divider-color); border-radius:var(--ha-card-border-radius, 12px); background:var(--card-background-color); padding:10px 14px; }
    button { cursor:pointer; } button:hover { border-color:var(--primary-color); } button:disabled { opacity:var(--disabled-opacity, .5); cursor:default; } button.primary { background:var(--primary-color); color:var(--text-primary-color); } button.danger { color:var(--error-color); border-color:var(--error-color); }
    button[aria-pressed=true] { border-color:var(--primary-color); } .tile[aria-pressed=true] { outline:2px solid var(--primary-color); outline-offset:2px; } .selection { margin:12px 0; }
    :focus-visible { outline:3px solid var(--primary-color); outline-offset:3px; }
    label { display:grid; gap:8px; margin:12px 0; } input[type=checkbox] { min-width:24px; min-height:24px; } .check { display:flex; align-items:center; gap:12px; }
    .muted, small { color:var(--secondary-text-color); } .notice { padding:12px 0; line-height:1.6; } .error { color:var(--error-color); white-space:pre-wrap; }
    .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:16px; }
    .tile, .job { border:1px solid var(--ha-card-border-color, var(--divider-color)); border-radius:var(--ha-card-border-radius, 16px); background:var(--ha-card-background, var(--card-background-color)); box-shadow:var(--ha-card-box-shadow, none); }
    .tile { padding:0; text-align:start; overflow:hidden; min-width:0; } .tile .caption { padding:12px; } .tile strong, .tile small { display:block; overflow-wrap:anywhere; } .tile small { margin-top:6px; }
    .image, .preview, .job-preview { position:relative; display:grid; place-items:center; min-width:0; min-height:0; background:var(--secondary-background-color); color:var(--secondary-text-color); }
    .image { aspect-ratio:1; overflow:hidden; }
    img { display:block; object-fit:contain; }
    .image img, .preview img, .job-preview img { position:absolute; inset:0; margin:auto; width:auto; height:auto; max-width:calc(100% - 16px); max-height:calc(100% - 16px); outline:1px solid var(--image-boundary, var(--secondary-text-color)); }
    .preview { width:100%; height:min(52vh,540px); border-radius:12px; }
    .preview[data-background=dark], .preview[data-background=light] { background:Canvas; --image-boundary:color-mix(in srgb, CanvasText 45%, Canvas); }
    .preview[data-background=dark] { color-scheme:dark; }
    .preview[data-background=light] { color-scheme:light; }
    .preview[data-background=checkerboard] { background-color:var(--secondary-background-color); background-image:repeating-conic-gradient(var(--divider-color) 0% 25%, transparent 0% 50%); background-size:20px 20px; }
    .preview-controls { margin:12px 0; }
    .pager { justify-content:center; margin:24px 0; } .job { padding:16px; margin:12px 0; overflow-wrap:anywhere; } .job h3 { margin:0 0 8px; }
    .job-content { display:flex; gap:16px; align-items:center; } .job-info { flex:1; min-width:0; } .job-preview { flex:0 0 112px; height:128px; border-radius:12px; padding:8px; text-align:center; font-size:13px; } .deletion-items { padding-left:20px; } .deletion-items li { margin:12px 0; overflow-wrap:anywhere; }
    dialog { color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:var(--ha-card-border-radius, 16px); padding:24px; width:min(760px,calc(100vw - 24px)); max-height:calc(100dvh - 24px); overflow:auto; }
    dialog::backdrop { background:var(--dialog-backdrop-color, var(--primary-background-color)); opacity:.8; } dialog h2 { margin-top:0; } .actions { justify-content:flex-end; margin-top:20px; } dl { display:grid; grid-template-columns:auto 1fr; gap:8px 16px; overflow-wrap:anywhere; } dd { margin:0; } .comparison { display:grid; grid-template-columns:1fr 1fr; gap:12px; } .comparison .preview { height:240px; } .aliases { padding-left:20px; }
    @media(max-width:600px) { main { padding:12px; } header { padding:8px; } h1 { font-size:19px; } .grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; } .tile .caption { padding:10px; } .toolbar > * { flex:1 1 100%; } .comparison { grid-template-columns:1fr; } .job-content { gap:10px; } .job-preview { flex-basis:80px; height:104px; } .job-info .row { flex-direction:column; align-items:stretch; } dialog { padding:16px; } dl { grid-template-columns:1fr; gap:4px; } dd { margin-bottom:8px; } }
  `;

  class ActivityImagesPanel extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._offset = 0;
      this._query = "";
      this._filter = "all";
      this._listVersion = 0;
      this._jobsVersion = 0;
      this._dialogVersion = 0;
      this._lifecycle = 0;
      this._scopes = new Map();
      this._jobPreviews = new Map();
      this._selected = new Map();
      this._selectionMode = false;
      this._previewBackground = "dark";
      this._busy = false;
      this._uncertain = false;
      this._jobs = { jobs: [], pending: [], failed: {} };
      this._visibility = () => this._resume();
      this._connectionReady = () => this._resume();
      this._connectionLost = () => {
        this._uncertain = true;
        this._jobsVersion++;
        clearTimeout(this._poll);
        this._syncButtons();
        this._queueStatus.textContent = "Generation status unknown — connection lost.";
        this._status.textContent = "Connection lost. Refresh job status before another paid request.";
      };
      this._build();
      this.shadowRoot.addEventListener("click", (event) => {
        if (this._busy) { event.preventDefault(); event.stopImmediatePropagation(); }
      }, true);
    }

    set hass(value) {
      const previous = this._hass?.connection;
      this._hass = value;
      if (previous !== value?.connection) {
        this._bindConnection(previous, false);
        if (this.isConnected) this._bindConnection(value?.connection, true);
      }
      if (this._settingsView) this._settingsView.hass = value;
      if (this.isConnected && !this._started) this._start();
    }
    get hass() { return this._hass; }
    set narrow(value) { this._menu.hidden = !value; }
    _bindConnection(connection, add) {
      const method = add ? "addEventListener" : "removeEventListener";
      connection?.[method]?.("ready", this._connectionReady);
      connection?.[method]?.("disconnected", this._connectionLost);
    }
    connectedCallback() {
      document.addEventListener("visibilitychange", this._visibility);
      this._bindConnection(this._hass?.connection, true);
      this._start();
    }
    disconnectedCallback() {
      document.removeEventListener("visibilitychange", this._visibility);
      this._bindConnection(this._hass?.connection, false);
      clearTimeout(this._poll);
      clearTimeout(this._debounce);
      this._started = false;
      this._lifecycle++;
      if (this._busy) this._uncertain = true;
      this._busy = false;
      this._listVersion++;
      this._jobsVersion++;
      this._dialogVersion++;
      this._clearImages("gallery");
      this._clearImages("dialog");
      this._clearImages("jobs");
      this._jobPreviews.clear();
      if (this._dialog.open) this._dialog.close();
    }
    _start() {
      if (!this._hass || this._started) return;
      this._started = true;
      this._resume();
    }
    _resume() {
      clearTimeout(this._poll);
      if (!this.isConnected || !this._hass || document.hidden) return;
      this._loadJobs();
      this._loadList();
    }
    _ws(command, data = {}) { return this._hass.callWS({ type: PREFIX + command, ...data }); }

    _build() {
      const sheet = el("style", style);
      const header = el("header");
      this._menu = button("☰", () => this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
      this._menu.setAttribute("aria-label", "Open sidebar");
      this._menu.hidden = true;
      const icon = el("ha-icon");
      icon.setAttribute("icon", "mdi:image-multiple");
      icon.setAttribute("aria-hidden", "true");
      header.append(this._menu, icon, el("h1", "Activity Images"));
      const main = el("main");
      const tabs = el("nav", undefined, "row");
      tabs.setAttribute("aria-label", "Library views");
      this._galleryTab = button("Library", () => this._showTab(false));
      this._queueTab = button("Queue & history", () => this._showTab(true));
      this._settingsTab = button("Settings", () => this._openSettings());
      tabs.append(this._galleryTab, this._queueTab, this._settingsTab);
      this._queueStatus = el("p", "Generation status: loading…", "muted");
      this._queueStatus.setAttribute("role", "status");
      this._status = el("div", "", "notice");
      this._status.setAttribute("role", "status");
      this._status.setAttribute("aria-live", "polite");
      this._gallery = el("section");
      const toolbar = el("div", undefined, "row toolbar");
      const searchLabel = el("label", "Search filename, title or alias", "grow");
      this._search = el("input");
      this._search.type = "search";
      this._search.maxLength = 255;
      this._search.addEventListener("input", () => {
        this._query = this._search.value;
        this._offset = 0;
        this._listVersion++; // Invalidate an in-flight response during the debounce too.
        clearTimeout(this._debounce);
        this._debounce = setTimeout(() => this._loadList(), 300);
      });
      searchLabel.append(this._search);
      const filterLabel = el("label", "Show");
      this._select = el("select");
      this._select.addEventListener("change", () => {
        this._filter = this._select.value;
        this._offset = 0;
        this._loadList();
      });
      filterLabel.append(this._select);
      this._selectionToggle = button("Select images", () => {
        this._selectionMode = !this._selectionMode;
        if (!this._selectionMode) this._selected.clear();
        this._renderSelection();
      });
      toolbar.append(searchLabel, filterLabel, button("Refresh", () => { this._offset = 0; this._loadList(true); this._loadJobs(); }), button("Generate new image", () => this._generateDialog()), this._selectionToggle);
      this._selection = el("div", undefined, "row selection");
      this._selection.hidden = true;
      this._selectionCount = el("span");
      this._selectionCount.setAttribute("role", "status");
      this._deleteSelected = button("Delete selected…", () => this._deleteDialog([...this._selected.keys()]));
      this._deleteSelected.className = "danger";
      this._selection.append(this._selectionCount, button("Clear selection", () => { this._selected.clear(); this._renderSelection(); }), this._deleteSelected);
      this._stats = el("p", "", "muted");
      this._listStatus = el("div", "Loading library…", "notice");
      this._listStatus.setAttribute("role", "status");
      this._grid = el("div", undefined, "grid");
      this._pager = el("div", undefined, "row pager");
      this._gallery.append(toolbar, this._selection, this._stats, this._listStatus, this._grid, this._pager);
      this._queue = el("section");
      this._queue.hidden = true;
      this._settings = el("section");
      this._settings.hidden = true;
      main.append(tabs, this._queueStatus, this._status, this._gallery, this._queue, this._settings);
      this._dialog = el("dialog");
      this._dialog.setAttribute("aria-labelledby", "dialog-title");
      this._dialog.addEventListener("cancel", (event) => { if (this._busy) event.preventDefault(); });
      this._dialog.addEventListener("close", () => {
        this._dialogVersion++;
        this._clearImages("dialog");
        if (this._returnFocus?.isConnected) this._returnFocus.focus();
      });
      this.shadowRoot.append(sheet, header, main, this._dialog);
      this._showTab(false);
    }
    async _openSettings() {
      this._queue.hidden = true;
      this._gallery.hidden = true;
      this._settings.hidden = false;
      this._clearImages("jobs");
      this._settingsTab.setAttribute("aria-pressed", "true");
      this._galleryTab.setAttribute("aria-pressed", "false");
      this._queueTab.setAttribute("aria-pressed", "false");
      if (this._settingsView) return;
      this._settings.textContent = "Loading Settings…";
      try {
        if (!customElements.get("activity-images-settings")) await import("./settings/settings-view.mjs");
        if (!this.isConnected) return;
        this._settingsView = el("activity-images-settings");
        this._settings.replaceChildren(this._settingsView);
        this._settingsView.hass = this._hass;
      } catch (error) {
        this._settings.textContent = "Unable to load Settings: " + message(error);
      }
    }
    _showTab(queue) {
      this._settings.hidden = true;
      this._settingsTab.setAttribute("aria-pressed", "false");
      this._queue.hidden = !queue;
      this._gallery.hidden = queue;
      this._queueTab.setAttribute("aria-pressed", String(queue));
      this._galleryTab.setAttribute("aria-pressed", String(!queue));
      if (queue) { this._renderJobs(); this._loadJobs(); }
      else { this._clearImages("jobs"); this._jobPreviews.clear(); }
    }
    _renderSelection() {
      this._selection.hidden = !this._selectionMode;
      this._selectionToggle.textContent = this._selectionMode ? "Done selecting" : "Select images";
      this._selectionToggle.setAttribute("aria-pressed", String(this._selectionMode));
      this._selectionCount.textContent = `${this._selected.size} selected · maximum 64 per operation`;
      this._deleteSelected.disabled = !this._selected.size;
      for (const tile of this._grid.children) {
        if (this._selectionMode) tile.setAttribute("aria-pressed", String(this._selected.has(tile.dataset.imageId)));
        else tile.removeAttribute("aria-pressed");
        tile.querySelector(".selection-state").hidden = !this._selectionMode;
        tile.querySelector(".selection-state").textContent = this._selected.has(tile.dataset.imageId) ? "Selected" : "Select image";
      }
    }
    _toggleSelection(item) {
      if (this._selected.has(item.id)) this._selected.delete(item.id);
      else if (this._selected.size < 64) this._selected.set(item.id, item.filename);
      else { this._status.textContent = "Select at most 64 images per operation."; return; }
      this._renderSelection();
    }

    async _loadList(refresh = false) {
      if (!this.isConnected || !this._hass || document.hidden) return;
      const version = ++this._listVersion;
      this._gallery.setAttribute("aria-busy", "true");
      this._listStatus.textContent = "Loading library…";
      this._clearImages("gallery");
      this._grid.replaceChildren();
      this._pager.replaceChildren();
      try {
        const data = await this._ws("list", { query: this._query, filter: this._filter, offset: this._offset, limit: PAGE_SIZE, refresh });
        if (version !== this._listVersion || !this.isConnected) return;
        this._offset = data.offset;
        this._select.replaceChildren();
        for (const value of data.filters) {
          const option = el("option", ({ all: "All images", linked: "Linked", unlinked: "Unlinked", generated: "Generated" })[value] || value);
          option.value = value;
          this._select.append(option);
        }
        this._select.value = this._filter;
        const stats = data.stats;
        this._stats.textContent = `${stats.images} images · ${stats.linked} linked · ${stats.unlinked} unlinked · ${stats.mappings} title associations · ${(stats.storage_bytes / 1048576).toFixed(1)} MiB`;
        this._listStatus.textContent = data.items.length ? "" : "No images found. Try another search or generate a new image.";
        for (const item of data.items) {
          const tile = button("", () => this._selectionMode ? this._toggleSelection(item) : this._openDetail(item.id));
          tile.className = "tile";
          tile.dataset.imageId = item.id;
          const image = el("div", "Loading preview…", "image");
          const caption = el("div", undefined, "caption");
          caption.append(el("strong", item.title || item.filename), el("small", item.filename), el("small", `${item.titles.length} linked title${item.titles.length === 1 ? "" : "s"} · ${item.origin || "Unknown origin"}`), el("small", "", "selection-state"));
          tile.append(image, caption);
          this._grid.append(tile);
          this._image(image, item.thumbnail_url || item.image_url, item.title || item.filename, "gallery", true);
        }
        this._renderSelection();
        const previous = button("Previous", () => { this._offset = Math.max(0, this._offset - PAGE_SIZE); this._loadList(); });
        previous.disabled = !data.offset;
        const next = button("Next", () => { this._offset += PAGE_SIZE; this._loadList(); });
        next.disabled = data.offset + data.limit >= data.total;
        this._pager.append(previous, el("span", data.total ? `${data.offset + 1}–${Math.min(data.offset + data.limit, data.total)} of ${data.total}` : "0 images"), next);
      } catch (error) {
        if (version !== this._listVersion) return;
        this._listStatus.replaceChildren(el("span", `Cannot load library: ${message(error)} `, "error"), button("Try again", () => this._loadList()));
      } finally {
        if (version === this._listVersion) this._gallery.setAttribute("aria-busy", "false");
      }
    }

    _clearImages(name) {
      const scope = this._scopes.get(name);
      if (!scope) return;
      scope.controller.abort();
      scope.observer?.disconnect();
      for (const url of scope.urls) URL.revokeObjectURL(url);
      this._scopes.delete(name);
    }
    _image(target, path, alt, name, lazy = false) {
      let scope = this._scopes.get(name);
      if (!scope) {
        scope = { controller: new AbortController(), urls: new Set(), queue: [], running: 0 };
        this._scopes.set(name, scope);
        if (typeof IntersectionObserver !== "undefined") {
          scope.observer = new IntersectionObserver((entries) => {
            for (const entry of entries) if (entry.isIntersecting) {
              scope.observer.unobserve(entry.target);
              const task = scope.waiting.get(entry.target);
              scope.waiting.delete(entry.target);
              if (task) scope.queue.push(task);
            }
            this._pumpImages(scope);
          }, { rootMargin: "200px" });
          scope.waiting = new Map();
        }
      }
      const task = { target, path, alt };
      if (lazy && scope.observer) {
        scope.waiting.set(target, task);
        scope.observer.observe(target);
      } else {
        scope.queue.push(task);
        this._pumpImages(scope);
      }
    }
    _pumpImages(scope) {
      while (!scope.controller.signal.aborted && scope.running < 4 && scope.queue.length) {
        const { target, path, alt } = scope.queue.shift();
        scope.running++;
        (async () => {
          try {
            // Only protected same-origin API paths may receive HA credentials.
            if (typeof path !== "string" || !path.startsWith("/api/") || path.includes("?") || path.includes("#") || path.includes("\\")) throw new Error("Invalid protected image URL");
            const response = await this._hass.fetchWithAuth(path, { signal: scope.controller.signal });
            if (!response.ok) throw new Error(`Image request failed (${response.status})`);
            const blob = await response.blob();
            if (scope.controller.signal.aborted || !target.isConnected) return;
            const url = URL.createObjectURL(blob);
            scope.urls.add(url);
            const image = el("img");
            image.alt = alt;
            image.src = url;
            image.addEventListener("error", () => {
              if (scope.urls.delete(url)) URL.revokeObjectURL(url);
              if (!scope.controller.signal.aborted) {
                target.dataset.previewFailed = "true";
                target.replaceChildren(el("span", "Preview unavailable"));
              }
            }, { once: true });
            target.replaceChildren(image);
          } catch (error) {
            if (!scope.controller.signal.aborted) {
              target.dataset.previewFailed = "true";
              target.replaceChildren(el("span", `Preview unavailable: ${message(error)}`));
            }
          } finally {
            scope.running--;
            this._pumpImages(scope);
          }
        })();
      }
    }

    _modal(title) {
      this._dialogVersion++;
      this._clearImages("dialog");
      if (!this._dialog.open) this._returnFocus = this.shadowRoot.activeElement;
      this._dialog.replaceChildren();
      const heading = el("h2", title);
      heading.id = "dialog-title";
      this._dialogError = el("p", "", "error");
      this._dialogError.setAttribute("role", "alert");
      this._dialog.append(heading, this._dialogError);
      if (!this._dialog.open) this._dialog.showModal();
      queueMicrotask(() => (this._dialog.querySelector("input, select, button") || this._dialog).focus());
      return this._dialogVersion;
    }
    _actions(...nodes) {
      const row = el("div", undefined, "row actions");
      row.append(...nodes, button("Close", () => this._dialog.close()));
      this._dialog.append(row);
      this._syncButtons();
    }
    _paidButton(text, action) {
      const node = button(text, action);
      node.dataset.paid = "true";
      node.className = "primary";
      return node;
    }
    _syncButtons() {
      for (const node of this._dialog.querySelectorAll("button, input, select")) {
        node.disabled = this._busy || node.dataset.unavailable === "true" || (node.dataset.paid === "true" && this._uncertain);
      }
      this._dialog.setAttribute("aria-busy", String(this._busy));
    }
    async _mutate(command, data, done) {
      if (this._busy || (["generate", "retry", "delete", "delete_retry", "delete_abandon"].includes(command) && this._uncertain)) return;
      const lifecycle = this._lifecycle;
      this._busy = true;
      this._dialogError.textContent = "";
      this._syncButtons();
      try {
        const result = await this._ws(command, data);
        if (!this.isConnected || lifecycle !== this._lifecycle) return;
        await done?.(result);
        if (!this.isConnected || lifecycle !== this._lifecycle) return;
        this._status.textContent = command.startsWith("delete") ? "Deletion operation recorded. Check Queue & history for its result. Home Tasks copies remain unchanged." : "Changes saved. Home Tasks copies are not synchronized.";
        this._loadList();
        this._loadJobs();
      } catch (error) {
        if (!this.isConnected || lifecycle !== this._lifecycle) return;
        if (command === "link" && error.code === "conflict") {
          this._busy = false;
          this._replaceDialog(data);
          return;
        }
        if (!error.code || error.code === "outcome_unknown") {
          this._uncertain = true;
          this._jobsVersion++;
          clearTimeout(this._poll);
          this._status.replaceChildren(el("span", "Request outcome unknown. Do not repeat it. Recover the recorded result before another paid request. "), button("Refresh job status", () => this._loadJobs()));
        }
        this._dialogError.textContent = message(error);
        if (this._uncertain) this._dialogError.append(button("Refresh job status", () => this._loadJobs()));
      } finally {
        if (lifecycle === this._lifecycle) {
          this._busy = false;
          this._syncButtons();
        }
      }
    }

    async _openDetail(id) {
      const version = this._modal("Image details");
      this._dialog.append(el("p", "Loading image details…"));
      this._actions();
      try {
        const item = await this._ws("get", { image_id: id });
        if (version !== this._dialogVersion || !this.isConnected) return;
        this._detail(item);
      } catch (error) {
        if (version === this._dialogVersion) this._dialogError.textContent = message(error);
      }
    }
    _detail(item) {
      this._modal(item.title || item.filename);
      const preview = el("div", "Loading preview…", "preview");
      preview.dataset.background = this._previewBackground;
      const backgrounds = el("div", undefined, "row preview-controls");
      backgrounds.setAttribute("role", "group");
      backgrounds.setAttribute("aria-label", "Preview background");
      for (const [key, label] of Object.entries({ dark: "Dark", light: "Light", checkerboard: "Transparency checkerboard" })) {
        const control = button(label, () => {
          this._previewBackground = key;
          preview.dataset.background = key;
          for (const option of backgrounds.children) option.setAttribute("aria-pressed", String(option.dataset.background === key));
        });
        control.dataset.background = key;
        control.setAttribute("aria-pressed", String(key === this._previewBackground));
        backgrounds.append(control);
      }
      const variants = el("div", undefined, "row preview-controls");
      variants.setAttribute("role", "group");
      variants.setAttribute("aria-label", "Preview variant");
      const showVariant = (key, label) => {
        this._clearImages("dialog");
        preview.textContent = "Loading preview…";
        for (const control of variants.children) control.setAttribute("aria-pressed", String(control.dataset.variant === key));
        this._image(preview, item.assets[key], `${label} preview: ${item.title || item.filename}`, "dialog");
      };
      for (const [key, label] of Object.entries({ master: "Master", thumbnail: "Thumbnail", qa: "QA" })) {
        if (!item.assets[key]) continue;
        const control = button(label, () => showVariant(key, label));
        control.dataset.variant = key;
        variants.append(control);
      }
      this._dialog.append(variants, backgrounds, preview);
      showVariant("master", "Master");
      const info = el("dl");
      for (const [key, value] of Object.entries({ Filename: item.filename, "Master dimensions": item.width && item.height ? `${item.width} × ${item.height}` : "Unknown", "Master format": item.format || "Unknown", "File modified (not creation date)": item.modified_at || "Unknown", Created: item.created_at || "Unknown", Source: item.origin || "Unknown" })) info.append(el("dt", key), el("dd", String(value)));
      this._dialog.append(info, el("h3", "Linked titles / aliases"));
      const aliases = el("ul", undefined, "aliases");
      for (const title of item.titles) {
        const row = el("li");
        row.append(el("span", title + " "), button("Unlink", () => this._unlinkDialog(item, title)));
        aliases.append(row);
      }
      this._dialog.append(item.titles.length ? aliases : el("p", "No linked titles. Unlinked does not mean unused outside this integration."));
      const remove = button("Delete permanently…", () => this._deleteDialog([item.id]));
      remove.className = "danger";
      this._actions(button("Assign title", () => this._linkDialog(item)), button("Regenerate…", () => this._generateDialog(item)), remove);
    }
    _titleInput(label, values) {
      const wrapper = el("label", label);
      const input = el(values?.length ? "select" : "input");
      if (values?.length) for (const title of values) { const option = el("option", title); option.value = title; input.append(option); }
      else { input.type = "text"; input.maxLength = 255; }
      wrapper.append(input);
      this._dialog.append(wrapper);
      return input;
    }
    _linkDialog(item) {
      this._modal("Assign activity title");
      const input = this._titleInput("Title");
      this._dialog.append(el("p", "Creates one association. Other aliases are unchanged. Home Tasks copies are not synchronized."));
      this._actions(button("Assign", () => {
        if (!input.value.trim()) { this._dialogError.textContent = "Enter a title."; return; }
        this._mutate("link", { image_id: item.id, title: input.value }, () => this._openDetail(item.id));
      }));
    }
    _replaceDialog(data) {
      this._modal("Replace existing association?");
      this._dialog.append(el("p", `“${data.title}” already refers to another image. Replace only this title's association? No files or other aliases will be removed. Home Tasks copies are not synchronized.`));
      this._actions(button("Replace association", () => this._mutate("link", { ...data, replace: true }, () => this._openDetail(data.image_id))));
    }
    _unlinkDialog(item, title) {
      this._modal("Unlink title?");
      this._dialog.append(el("p", `Unlink “${title}” from this image? This removes only this title association, never image files or other aliases. Home Tasks copies are not synchronized.`));
      this._actions(button("Unlink title", () => this._mutate("unlink", { image_id: item.id, title }, () => this._openDetail(item.id))));
    }
    _paidCheck() {
      const label = el("label", undefined, "check");
      const checkbox = el("input");
      checkbox.type = "checkbox";
      label.append(checkbox, el("span", "I authorize a new paid AI image request. Provider charges may apply."));
      this._dialog.append(label);
      return checkbox;
    }
    _generateDialog(item) {
      this._modal(item ? "Regenerate one activity" : "Generate new image");
      const input = this._titleInput(item?.titles.length ? "Choose exactly one linked title" : "Activity title", item?.titles);
      this._dialog.append(el("p", "The current mapping stays unchanged until you accept the candidate. Only the selected title changes; other aliases are unaffected. Home Tasks copies are not synchronized."));
      const check = this._paidCheck();
      this._actions(this._paidButton("Generate paid candidate", () => {
        if (!input.value.trim() || !check.checked) { this._dialogError.textContent = "Enter a title and confirm the paid request."; return; }
        this._mutate("generate", { title: input.value, ...(item ? { image_id: item.id } : {}) }, () => { this._dialog.close(); this._showTab(true); });
      }));
      if (this._uncertain) this._dialogError.textContent = "Refresh job status before another paid request.";
    }

    async _deleteDialog(imageIds, operationId) {
      const version = this._modal(operationId ? "Review deletion retry" : "Review permanent deletion");
      this._dialog.append(el("p", "Checking image files, title associations and current calendar needs…"));
      this._actions();
      try {
        const plan = await this._ws(operationId ? "delete_retry_plan" : "delete_plan", operationId ? { operation_id: operationId } : { image_ids: imageIds });
        if (version !== this._dialogVersion || !this.isConnected) return;
        this._confirmDelete(plan, operationId);
      } catch (error) {
        if (version === this._dialogVersion) this._dialogError.textContent = message(error);
      }
    }
    _confirmDelete(plan, operationId) {
      this._modal(operationId ? "Retry permanent deletion?" : "Delete images permanently?");
      this._dialog.append(el("p", `${plan.items.length} image${plan.items.length === 1 ? "" : "s"} selected. The following files and Activity Images associations are included in this confirmation. Permanent deletion cannot be undone.`));
      const list = el("ul", undefined, "deletion-items");
      for (const item of plan.items) {
        const row = el("li");
        row.append(el("strong", item.filename));
        row.append(el("p", `Files: ${item.files.join(", ") || "No remaining files"}`));
        row.append(el("p", `Linked titles: ${item.titles.join("; ") || "None"}`));
        row.append(el("p", item.needed_titles.length ? `Replacement needed: ${item.needed_titles.join("; ")}` : "No replacement required by the checked Activity Images sources."));
        if (item.blocked_reason) row.append(el("p", item.blocked_reason, "error"));
        if (item.error) row.append(el("p", item.error, "error"));
        list.append(row);
      }
      this._dialog.append(list, el("p", plan.external_warning || "External references cannot be verified. Unlinked does not mean unused. Dashboards may retain these image URLs. Home Tasks copies will not be modified."));
      this._dialog.append(el("p", "The activity check covers selected calendars within the configured lookahead only. Later events and external uses cannot be verified."));
      if (plan.calendar_warning) this._dialog.append(el("p", plan.calendar_warning, "error"));
      const requests = plan.replacement_requests;
      this._dialog.append(el("p", `${requests} new AI generation request${requests === 1 ? "" : "s"} ${operationId ? "remain" : "may be required"}. Activity Images may make up to ${plan.max_provider_attempts} AI Task attempts in total. Provider retries and billing are outside this budget; failed or timed-out attempts may still incur charges.`));
      this._dialog.append(el("p", "Replacements are saved before the old image is removed. If generation fails, that image and its associations remain. Successful images in a batch can finish independently. Automatic generation stays unchanged."));
      const acknowledgement = el("input");
      acknowledgement.type = "checkbox";
      const label = el("label", undefined, "check");
      label.append(acknowledgement, el("span", "I confirm permanent deletion of the listed files and associations, including the risk of broken external image references."));
      this._dialog.append(label);
      let paid;
      if (requests) {
        const paidLabel = el("label", undefined, "check");
        paid = el("input");
        paid.type = "checkbox";
        paidLabel.append(paid, el("span", `I authorize only the ${requests} listed replacement request${requests === 1 ? "" : "s"}, including the existing bounded provider retries.`));
        this._dialog.append(paidLabel);
      }
      let actionLabel = requests ? "Delete & Regenerate" : "Delete permanently";
      if (operationId) actionLabel = requests ? "Retry Delete & Regenerate" : "Retry deletion cleanup";
      const remove = button(actionLabel, () => {
        if (!acknowledgement.checked || (paid && !paid.checked)) { this._dialogError.textContent = "Confirm permanent deletion and any paid replacement requests first."; return; }
        this._mutate(operationId ? "delete_retry" : "delete", { token: plan.token, confirm: true, ...(operationId ? { operation_id: operationId } : { regenerate: requests > 0 }) }, () => {
          for (const item of plan.items) this._selected.delete(item.id);
          if (!this._selected.size) this._selectionMode = false;
          this._renderSelection();
          this._dialog.close();
          this._showTab(true);
        });
      });
      remove.className = "danger";
      remove.dataset.paid = "true"; // Unknown outcomes must block destructive submissions, even without AI.
      remove.dataset.unavailable = String(plan.items.some((item) => item.blocked_reason) || !!plan.blocked_reason);
      this._actions(remove);
      if (plan.blocked_reason) this._dialogError.textContent = plan.blocked_reason;
      if (this._uncertain) this._dialogError.textContent = "Refresh job status before another deletion request.";
    }
    _abandonDeleteDialog(operation) {
      this._modal("Keep originals and stop deletion?");
      this._dialog.append(el("p", "Stop only failed or interrupted image deletions that have not committed replacements. Their original files, associations and saved candidates remain. This releases their image and title reservations so you can review a fresh plan."));
      this._dialog.append(el("p", "Completed deletions cannot be undone. Committed cleanup is not stopped. This action sends no AI request and does not enable automatic generation."));
      for (const item of operation.items) this._dialog.append(el("p", `${item.filename} · ${item.state}`));
      const keep = button("Keep originals", () => this._mutate("delete_abandon", { operation_id: operation.id, confirm: true }, () => this._dialog.close()));
      keep.dataset.paid = "true";
      this._actions(keep);
    }
    _jobPreview(job) {
      const path = job.result_unavailable ? null : job.thumbnail_url || job.image_url;
      const existing = this._jobPreviews.get(job.id);
      if (existing && existing.path === path) return existing.target;
      if (existing) this._removeJobPreview(job.id);
      let placeholder = path ? "Loading preview…" : "No image available";
      if (job.state === "pending") placeholder = "Awaiting generation";
      if (job.state === "running") placeholder = "Generating…";
      if (job.result_unavailable) placeholder = "Preview unavailable";
      if (job.result_deleted) placeholder = "Result deleted";
      const target = el("div", placeholder, "job-preview");
      target.setAttribute("role", "img");
      target.setAttribute("aria-label", job.result_deleted ? "Result deleted" : `Image preview: ${job.title}`);
      if (path && !job.result_deleted && !this._queue.hidden) {
        this._jobPreviews.set(job.id, { path, target });
        this._image(target, path, `Result: ${job.title}`, "jobs", true);
      }
      return target;
    }
    _removeJobPreview(id) {
      const preview = this._jobPreviews.get(id);
      if (!preview) return;
      const scope = this._scopes.get("jobs");
      if (scope) {
        scope.observer?.unobserve(preview.target);
        scope.waiting?.delete(preview.target);
        scope.queue = scope.queue.filter((task) => task.target !== preview.target);
        const url = preview.target.querySelector("img")?.src;
        if (url && scope.urls.delete(url)) URL.revokeObjectURL(url);
      }
      this._jobPreviews.delete(id);
    }
    async _loadJobs(retryPreviews = false) {
      clearTimeout(this._poll);
      if (!this.isConnected || !this._hass || document.hidden) return;
      const version = ++this._jobsVersion;
      try {
        const previous = JSON.stringify([this._jobs.jobs.map((job) => [job.id, job.state]), this._jobs.operations]);
        const previousStatus = JSON.stringify(this._jobs);
        const jobs = await this._ws("jobs");
        if (version !== this._jobsVersion || !this.isConnected || document.hidden) return;
        this._jobs = jobs;
        const recovered = this._uncertain;
        let retryFailed = false;
        if (retryPreviews || recovered) for (const [id, preview] of this._jobPreviews) {
          if (preview.target.dataset.previewFailed === "true") { this._removeJobPreview(id); retryFailed = true; }
        }
        this._queueStatus.textContent = jobs.active ? "Generation active — see Queue & history for recorded jobs." : "Generation idle — no active generation reported.";
        if (this._uncertain) {
          this._status.textContent = "Job status recovered. Review queue and history before starting another paid request.";
          if (!this._busy && this._dialog.open) this._dialog.close();
          this._queue.hidden = false;
          this._gallery.hidden = true;
          this._queueTab.setAttribute("aria-pressed", "true");
          this._galleryTab.setAttribute("aria-pressed", "false");
        }
        this._uncertain = false;
        this._syncButtons();
        if (recovered || retryFailed || !this._queue.childElementCount || previousStatus !== JSON.stringify(jobs)) this._renderJobs();
        if (previous !== JSON.stringify([jobs.jobs.map((job) => [job.id, job.state]), jobs.operations])) this._loadList();
        const deleting = (jobs.operations || []).some((operation) => operation.active || ["pending", "running", "preparing", "generating", "replacing", "committing", "cleaning", "cleanup"].includes(operation.state));
        if (jobs.active || jobs.current || jobs.pending.length || deleting || jobs.jobs.some((job) => ACTIVE.has(job.state))) this._poll = setTimeout(() => this._loadJobs(), 3000);
      } catch (error) {
        if (version !== this._jobsVersion || !this.isConnected) return;
        this._queueStatus.textContent = "Generation status unknown — refresh failed.";
        this._status.replaceChildren(el("span", `Cannot refresh job status: ${message(error)} `, "error"), button("Refresh job status", () => this._loadJobs()));
      }
    }
    _renderJobs() {
      this._queue.replaceChildren(el("h2", "Queue & history"));
      this._queue.append(el("p", "Generation is serialized. Running provider calls cannot be cancelled. Manual candidates require acceptance; discarding never deletes files. Confirmed Delete & Regenerate operations save replacements before deleting their old files.", "muted"));
      const tools = el("div", undefined, "row");
      tools.append(button("Refresh job status", () => this._loadJobs(true)), button("Clear recorded errors", () => {
        this._modal("Clear recorded errors?");
        this._dialog.append(el("p", "This clears recorded errors only. It does not enqueue or authorize another paid request."));
        this._actions(button("Clear errors", () => this._mutate("clear_errors", {}, () => this._dialog.close())));
      }));
      this._queue.append(tools);
      const jobs = this._jobs.jobs;
      const previewIds = new Set(jobs.filter((job) => !job.result_deleted && !job.result_unavailable && (job.thumbnail_url || job.image_url)).map((job) => job.id));
      for (const id of this._jobPreviews.keys()) if (!previewIds.has(id)) this._removeJobPreview(id);
      for (const operation of this._jobs.operations || []) {
        const card = el("article", undefined, "job");
        card.append(el("h3", "Permanent deletion"), el("p", `${operation.items.length} image${operation.items.length === 1 ? "" : "s"} · ${operation.state}`));
        for (const item of operation.items) card.append(el("p", `${item.filename} · ${item.state}${item.error ? ` — ${item.error}` : ""}`, item.error ? "error" : ""));
        if (operation.error) card.append(el("p", operation.error, "error"));
        if (["failed", "interrupted", "cleanup_failed", "partial"].includes(operation.state)) card.append(button("Review deletion retry…", () => this._deleteDialog([], operation.id)));
        if (operation.can_abandon) card.append(button("Keep originals…", () => this._abandonDeleteDialog(operation)));
        this._queue.append(card);
      }
      const represented = new Set(jobs.filter((job) => ACTIVE.has(job.state) || job.state === "failed").flatMap((job) => [job.title, job.key || job.title]));
      if (this._jobs.current && !represented.has(this._jobs.current)) this._queue.append(this._legacyJob(this._jobs.current, "running"));
      for (const title of this._jobs.pending) if (title !== this._jobs.current && !represented.has(title)) this._queue.append(this._legacyJob(title, "pending"));
      for (const [title, error] of Object.entries(this._jobs.failed)) if (!represented.has(title)) this._queue.append(this._legacyJob(title, "failed", error));
      for (const title of this._jobs.retry_blocked || []) if (!represented.has(title) && !Object.hasOwn(this._jobs.failed, title)) this._queue.append(this._legacyJob(title, "failed"));
      for (const job of jobs) {
        const card = el("article", undefined, "job");
        const content = el("div", undefined, "job-content");
        const info = el("div", undefined, "job-info");
        info.append(el("h3", job.title), el("p", `${job.kind} · ${job.state}${job.operation_id ? " · deletion replacement" : ""}`));
        if (job.updated_at) info.append(el("small", `Updated: ${job.updated_at}`));
        if (job.error) info.append(el("p", job.error, "error"));
        const actions = el("div", undefined, "row");
        if (!job.operation_id && job.state === "pending") actions.append(button("Cancel waiting", () => this._cancelDialog(job.title)));
        if (!job.operation_id && ["failed", "interrupted"].includes(job.state)) actions.append(button("Retry…", () => this._retryDialog(job.kind === "manual" ? { job_id: job.id } : { title: job.title }, job.title)));
        if (!job.operation_id && job.state === "review") actions.append(button("Review candidate", () => this._review(job)));
        if (job.image_id && !job.result_deleted && !job.result_unavailable) actions.append(button("Open result", () => this._openDetail(job.image_id)));
        info.append(actions);
        content.append(info, this._jobPreview(job));
        card.append(content);
        this._queue.append(card);
      }
      if (!jobs.length && !(this._jobs.operations || []).length && !this._jobs.current && !this._jobs.pending.length && !Object.keys(this._jobs.failed).length && !(this._jobs.retry_blocked || []).length) this._queue.append(el("p", "No queued jobs or recorded history."));
    }
    _legacyJob(title, state, error) {
      const card = el("article", undefined, "job");
      const content = el("div", undefined, "job-content");
      const info = el("div", undefined, "job-info");
      info.append(el("h3", title), el("p", `Background job · ${state}`));
      if (error) info.append(el("p", error, "error"));
      if (state === "pending") info.append(button("Cancel waiting", () => this._cancelDialog(title)));
      if (state === "failed") info.append(button("Retry…", () => this._retryDialog({ title }, title)));
      content.append(info, this._jobPreview({ title, state }));
      card.append(content);
      return card;
    }
    _cancelDialog(title) {
      this._modal("Cancel waiting job?");
      this._dialog.append(el("p", `Cancel the pending request for “${title}”? A provider call already running cannot be cancelled.`));
      this._actions(button("Cancel waiting job", () => this._mutate("cancel", { titles: [title] }, () => this._dialog.close())));
    }
    _retryDialog(data, title) {
      this._modal("Retry paid generation?");
      this._dialog.append(el("p", `Start a new paid request for “${title}”? Clearing errors alone never retries a job.`));
      const check = this._paidCheck();
      this._actions(this._paidButton("Retry paid request", () => {
        if (!check.checked) { this._dialogError.textContent = "Confirm the paid request first."; return; }
        this._mutate("retry", data, () => this._dialog.close());
      }));
    }
    async _review(job) {
      const version = this._modal(`Review candidate: ${job.title}`);
      this._dialog.append(el("p", "Accept changes only this title's mapping. Other aliases remain unchanged. Home Tasks copies are not synchronized. Discard keeps the candidate files."));
      const comparison = el("div", undefined, "comparison");
      const old = el("section");
      const candidate = el("section");
      old.append(el("h3", "Current mapping"));
      candidate.append(el("h3", "New candidate"));
      const nextPreview = el("div", "Loading preview…", "preview");
      candidate.append(nextPreview);
      comparison.append(old, candidate);
      this._dialog.append(comparison);
      this._image(nextPreview, job.image_url, "New candidate", "dialog");
      this._actions(button("Accept for this title", () => this._mutate("accept", { job_id: job.id }, () => this._dialog.close())), button("Discard candidate", () => this._mutate("discard", { job_id: job.id }, () => this._dialog.close())));
      try {
        // Resolve the current exact alias, never a fuzzy or filename match.
        let item;
        let offset = 0;
        do {
          const page = await this._ws("list", { query: job.title, filter: "linked", offset, limit: 96 });
          if (version !== this._dialogVersion || !this.isConnected) return;
          item = page.items.find((entry) => entry.titles.includes(job.title));
          offset += page.limit;
          if (item || !page.items.length || offset >= page.total) break;
        } while (true);
        if (item) {
          const preview = el("div", "Loading preview…", "preview");
          old.append(preview);
          this._image(preview, item.image_url, "Current mapping", "dialog");
        } else old.append(el("p", "No exact current association found."));
      } catch (error) {
        if (version === this._dialogVersion) old.append(el("p", `Current preview unavailable: ${message(error)}`, "error"));
      }
    }
  }
  if (!customElements.get("activity-images-panel")) customElements.define("activity-images-panel", ActivityImagesPanel);
})();
