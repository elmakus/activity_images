/** Calendar Coverage operational view. Read-only except for explicit
 * assign/ignore actions and a separately confirmed manual generation.
 * Opening, filtering, searching, refreshing or editing ignores never
 * sends AI requests. All user-originated strings are assigned as text. */
const COVERAGE = "activity_images/calendar/coverage";
const REFRESH = "activity_images/calendar/refresh";
const IGNORE = "activity_images/calendar/ignore";
const FILTERS = [["all", "All"], ["missing", "Missing"], ["available", "Available"],
  ["ignored", "Ignored"], ["queued", "Queued"], ["failed", "Failed"]];
const SORTS = [["title", "Title"], ["occurrences", "Occurrences"]];
const PAGE_SIZE = 25;

const STYLE = [
  ":host { display:block; color:var(--primary-text-color); }",
  "* { box-sizing:border-box; }",
  ".row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }",
  ".toolbar { margin:16px 0; } .grow { flex:1; min-width:180px; }",
  "button, input, select { font:inherit; color:inherit; min-height:44px; border:1px solid var(--divider-color);",
  "  border-radius:var(--ha-card-border-radius, 12px); background:var(--card-background-color); padding:10px 14px; }",
  "button { cursor:pointer; } button:hover { border-color:var(--primary-color); }",
  "button:disabled { opacity:var(--disabled-opacity, .5); cursor:default; }",
  "button[aria-pressed=true] { border-color:var(--primary-color); }",
  ":focus-visible { outline:3px solid var(--primary-color); outline-offset:3px; }",
  "label { display:grid; gap:8px; margin:12px 0; }",
  ".muted, small { color:var(--secondary-text-color); } .notice { padding:12px 0; line-height:1.6; }",
  ".error { color:var(--error-color); white-space:pre-wrap; }",
  ".card { border:1px solid var(--ha-card-border-color, var(--divider-color));",
  "  border-radius:var(--ha-card-border-radius, 16px); background:var(--ha-card-background, var(--card-background-color));",
  "  padding:16px; margin:12px 0; overflow-wrap:anywhere; }",
  ".badges { display:flex; gap:8px; flex-wrap:wrap; margin:8px 0; }",
  ".badge { border:1px solid var(--divider-color); border-radius:999px; padding:4px 12px; font-size:13px; }",
  ".raw { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; background:var(--secondary-background-color);",
  "  border-radius:12px; padding:12px; margin:8px 0; }",
  ".thumb { display:grid; place-items:center; min-height:96px; background:var(--secondary-background-color);",
  "  border-radius:12px; color:var(--secondary-text-color); margin:8px 0; }",
  ".thumb img { max-width:100%; max-height:240px; object-fit:contain; }",
  "dialog { color:var(--primary-text-color); background:var(--card-background-color);",
  "  border:1px solid var(--divider-color); border-radius:var(--ha-card-border-radius, 16px);",
  "  padding:24px; width:min(640px,calc(100vw - 24px)); max-height:calc(100dvh - 24px); overflow:auto; }",
  "dialog::backdrop { background:var(--dialog-backdrop-color, var(--primary-background-color)); opacity:.8; }",
  "dialog h2 { margin-top:0; } .actions { display:flex; gap:12px; justify-content:flex-end; margin-top:20px; flex-wrap:wrap; }",
  "@media(max-width:600px) { .toolbar > * { flex:1 1 100%; } dialog { padding:16px; } }",
].join("\n");

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function action(text, onClick) {
  const node = element("button", text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}
function statusMessage(error) {
  return error?.message || String(error);
}

export class ActivityImagesCoverage extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this._query = "";
    this._filter = "all";
    this._calendar = "";
    this._sort = "title";
    this._offset = 0;
    this._version = 0;
    this._refreshToken = null;
    this._loaded = false;
    this._items = [];
    this._total = 0;
    this._stats = null;
    this._scan = null;
    this._ignoreRevision = null;
    this._entities = [];
    this.onAssign = null;
    this.onGenerate = null;
    this.previewImage = null;
    this.clearPreviews = null;
  }
  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this._loaded) this._load();
  }
  get hass() { return this._hass; }
  connectedCallback() {
    if (this._hass && !this._loaded) this._load();
  }
  disconnectedCallback() {
    this._version++;
    this._loaded = false;
    clearTimeout(this._debounce);
    this._refreshToken = null;
    this.clearPreviews?.();
    if (this._dialog?.open) this._dialog.close();
  }
  invalidate() { this._version++; }
  reload() {
    if (this.isConnected && this._hass && !document.hidden) this._load();
  }
  _call(type, data = {}) {
    return this._hass.callWS({type: `activity_images/calendar/${type}`, ...data});
  }
  _paging() {
    return {
      query: this._query, filter: this._filter, sort: this._sort,
      offset: this._offset, limit: PAGE_SIZE,
      ...(this._calendar ? {calendar: this._calendar} : {}),
    };
  }

  _build() {
    this.shadowRoot.replaceChildren(element("style", STYLE));
    const root = this.shadowRoot;
    root.append(element("h2", "Calendar Coverage"));
    root.append(element("p",
      "Grouped calendar titles with occurrence context, library matches and generation state. " +
      "Opening, filtering, searching, refreshing or editing ignores never sends an AI request.", "muted"));
    const toolbar = element("div", undefined, "row toolbar");
    const searchLabel = element("label", "Search titles", "grow");
    this._search = element("input");
    this._search.type = "search";
    this._search.maxLength = 255;
    this._search.addEventListener("input", () => {
      this._query = this._search.value;
      this._offset = 0;
      this._version++;
      clearTimeout(this._debounce);
      this._debounce = setTimeout(() => this._load(), 300);
    });
    searchLabel.append(this._search);
    const calendarLabel = element("label", "Calendar");
    this._calendars = element("select");
    this._calendars.addEventListener("change", () => {
      this._calendar = this._calendars.value;
      this._offset = 0;
      this._load();
    });
    calendarLabel.append(this._calendars);
    const sortLabel = element("label", "Sort");
    this._sortSelect = element("select");
    for (const [value, title] of SORTS) {
      const option = element("option", title);
      option.value = value;
      this._sortSelect.append(option);
    }
    this._sortSelect.value = this._sort;
    this._sortSelect.addEventListener("change", () => {
      this._sort = this._sortSelect.value;
      this._offset = 0;
      this._load();
    });
    sortLabel.append(this._sortSelect);
    const refresh = action("Refresh coverage", () => this._refresh());
    toolbar.append(searchLabel, calendarLabel, sortLabel, refresh);
    root.append(toolbar);
    this._filters = element("div", undefined, "row");
    this._filters.setAttribute("role", "group");
    this._filters.setAttribute("aria-label", "Coverage filters");
    root.append(this._filters);
    this._statsLine = element("p", "", "muted");
    this._scanLine = element("p", "", "muted");
    root.append(this._statsLine, this._scanLine);
    this._status = element("div", "Loading calendar coverage…", "notice");
    this._status.setAttribute("role", "status");
    this._status.setAttribute("aria-live", "polite");
    root.append(this._status);
    this._list = element("div");
    root.append(this._list);
    this._pager = element("div", undefined, "row pager");
    root.append(this._pager);
    this._dialog = element("dialog");
    this._dialog.setAttribute("aria-labelledby", "coverage-dialog-title");
    this._dialog.addEventListener("close", () => {
      this._version++;
      if (this._returnFocus?.isConnected) this._returnFocus.focus();
    });
    root.append(this._dialog);
  }

  _paintControls() {
    const active = this.shadowRoot.activeElement;
    const focusedFilter = this._filters.contains(active) ? active.dataset.filter : null;
    this._filters.replaceChildren();
    const stats = this._stats || {};
    const counts = {all: stats.titles ?? 0, missing: stats.missing ?? 0, available: stats.available ?? 0,
      ignored: stats.ignored ?? 0, queued: stats.queued ?? 0, failed: stats.failed ?? 0};
    for (const [value, title] of FILTERS) {
      const tab = action(`${title} (${counts[value] ?? 0})`, () => {
        this._filter = value;
        this._offset = 0;
        this._load();
      });
      tab.setAttribute("aria-pressed", String(this._filter === value));
      tab.dataset.filter = value;
      this._filters.append(tab);
      if (focusedFilter === value) tab.focus();
    }
    this._calendars.replaceChildren();
    const all = element("option", "All calendars");
    all.value = "";
    this._calendars.append(all);
    for (const entity of this._entities) {
      const option = element("option", `${entity.name || entity.entity_id} (${entity.entity_id})${entity.available ? "" : " — Calendar unavailable"}`);
      option.value = entity.entity_id;
      this._calendars.append(option);
    }
    this._calendars.value = this._calendar;
    if (this._calendars.value !== this._calendar) {
      this._calendar = "";
      this._calendars.value = "";
    }
  }

  _paintMeta() {
    const stats = this._stats;
    this._statsLine.textContent = stats
      ? `${stats.titles} titles · ${stats.missing} missing · ${stats.available} available · ${stats.ignored} ignored · ${stats.queued} queued · ${stats.failed} failed`
      : "";
    const scan = this._scan;
    if (!scan) {
      this._scanLine.textContent = "";
      return;
    }
    const parts = [`Scan: ${Array.isArray(scan.calendars) ? scan.calendars.length : scan.calendars} calendars · ${scan.tracked_titles} tracked titles · ${scan.images} images`];
    if (scan.last_scan) parts.push(`last scan ${scan.last_scan}`);
    parts.push(`automatic generation ${scan.automatic_generation ? "on" : "off"}`);
    this._scanLine.textContent = parts.join(" · ");
    if (scan.error) this._scanLine.append(element("div", String(scan.error), "error"));
    if (!scan.complete) this._scanLine.append(element("div", "Coverage incomplete — the scan did not finish. Counts may be partial.", "error"));
    else if (scan.stale) this._scanLine.append(element("div", "Coverage data may be stale — the last scan did not complete cleanly.", "muted"));
  }

  _paintItems() {
    const active = this.shadowRoot.activeElement;
    const focusedCard = active?.closest("article");
    const focusedTitle = focusedCard?.querySelector(".raw")?.textContent;
    const focusedAction = focusedCard ? [...focusedCard.querySelectorAll("button")].indexOf(active) : -1;
    // Drop panel-held preview object URLs/fetches before removing their nodes.
    this.clearPreviews?.();
    this._list.replaceChildren();
    for (const item of this._items) {
      const card = element("article", undefined, "card");
      card.append(element("strong", item.title || "(untitled)"));
      const badges = element("div", undefined, "badges");
      const badge = (text) => badges.append(element("span", text, "badge"));
      badge(item.image_status === "available" ? "Available" : item.image_status === "missing" ? "Missing" : "Unknown image");
      if (item.ignored) badge("Ignored");
      if (item.job_status === "queued" || item.job_status === "running") badge("Queued");
      if (item.job_status === "failed") badge("Failed");
      badge(item.scheduled ? "Scheduled" : "Unscheduled");
      card.append(badges);
      const rawLabel = element("small", "Exact calendar title:");
      const raw = element("pre", item.raw_summary, "raw");
      card.append(rawLabel, raw);
      const meta = element("p",
        `${item.occurrence_count} occurrence${item.occurrence_count === 1 ? "" : "s"} · ` +
        `image: ${item.image_status} · resolution: ${item.resolution}`, "muted");
      card.append(meta);
      if (Array.isArray(item.calendars) && item.calendars.length) {
        const lines = item.calendars.map((entry) =>
          `${entry.name || entry.entity_id} · ${entry.count} occurrence${entry.count === 1 ? "" : "s"}${entry.available ? "" : " · Calendar unavailable"}${entry.auto_generate ? " · auto" : ""}`);
        card.append(element("p", "Calendars: " + lines.join("; "), "muted"));
      }
      if (item.eligible_for_auto) {
        card.append(element("p", "Eligible for automatic generation.", "muted"));
      }
      if (item.job_status === "queued" || item.job_status === "running") {
        card.append(element("p", "Queued for generation — see Queue & history. Opening Coverage never requeues it.", "muted"));
      }
      if (item.job_status === "failed") {
        card.append(element("p", "Generation failed — see Queue & history for the recorded error.", "error"));
      }
      if (item.image) {
        const thumb = element("div", "Loading preview…", "thumb");
        card.append(thumb);
        if (typeof this.previewImage === "function") {
          this.previewImage(thumb, item.image.thumbnail_url || item.image.image_url, `Library image: ${item.image.title || item.title}`);
        } else {
          thumb.textContent = "Preview unavailable";
        }
      }
      const tools = element("div", undefined, "row");
      if (item.image_status === "missing") {
        tools.append(action("Assign image", () => this.onAssign?.(item)));
        tools.append(action("Generate…", () => this.onGenerate?.(item)));
      } else if (item.image) {
        const preview = element("div");
        preview.hidden = true;
        const inspect = action("Inspect image", () => {
          preview.hidden = !preview.hidden;
          inspect.setAttribute("aria-expanded", String(!preview.hidden));
          if (!preview.hidden) {
            preview.replaceChildren(element("div", "Loading preview…", "thumb"));
            if (typeof this.previewImage === "function") {
              this.previewImage(preview.firstChild, item.image.image_url, `Library image: ${item.image.title || item.title}`);
            } else {
              preview.firstChild.textContent = "Preview unavailable";
            }
          }
        });
        inspect.setAttribute("aria-expanded", "false");
        tools.append(inspect);
        card.append(preview);
      }
      tools.append(action(item.ignored ? "Remove ignore" : "Ignore exact title", () => this._ignoreDialog(item)));
      card.append(tools);
      this._list.append(card);
      if (item.raw_summary === focusedTitle && focusedAction >= 0) {
        ([...tools.children].find((control) => control.textContent === active.textContent)
          || tools.children[Math.min(focusedAction, tools.children.length - 1)])?.focus();
      }
    }
    if (focusedCard && !this.shadowRoot.activeElement) this._search.focus();
  }

  _paintPager(total, offset, limit) {
    const active = this.shadowRoot.activeElement;
    const restoreFocus = this._pager.contains(active);
    this._pager.replaceChildren();
    const previous = action("Previous", () => {
      this._offset = Math.max(0, this._offset - PAGE_SIZE);
      this._load();
    });
    previous.disabled = !offset;
    const next = action("Next", () => {
      this._offset += PAGE_SIZE;
      this._load();
    });
    next.disabled = offset + limit >= total;
    this._pager.append(previous,
      element("span", total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}` : "0 titles"),
      next);
    if (restoreFocus) {
      ([previous, next].find((control) => control.textContent === active.textContent && !control.disabled)
        || [previous, next].find((control) => !control.disabled) || this._search).focus();
    }
  }

  async _load() {
    if (!this.isConnected || !this._hass || document.hidden) return;
    if (!this._list) this._build();
    const version = ++this._version;
    this._status.textContent = "Loading calendar coverage…";
    try {
      const data = await this._call("coverage", this._paging());
      if (version !== this._version || !this.isConnected) return;
      this._loaded = true;
      this._items = data.items || [];
      this._total = data.total ?? 0;
      this._offset = data.offset ?? 0;
      this._stats = data.stats || null;
      this._scan = data.scan || null;
      this._ignoreRevision = data.ignore_revision ?? null;
      this._entities = data.entities || [];
      this._paintControls();
      this._paintMeta();
      this._paintItems();
      this._paintPager(this._total, this._offset, data.limit ?? PAGE_SIZE);
      this._status.textContent = this._items.length ? "" : "No calendar titles found. Try another search or filter.";
    } catch (error) {
      if (version !== this._version) return;
      this._status.replaceChildren(
        element("span", "Cannot load calendar coverage: " + statusMessage(error) + " ", "error"),
        action("Try again", () => this._load()));
    }
  }

  async _refresh() {
    if (!this.isConnected || !this._hass || this._refreshToken) return;
    const token = {};
    this._refreshToken = token;
    // Invalidate any earlier read so it cannot paint over this refresh.
    const version = ++this._version;
    this._status.textContent = "Refreshing coverage…";
    try {
      // Resolve-only refresh: recomputes coverage without AI or queue writes.
      const data = await this._call("refresh", this._paging());
      if (version !== this._version || !this.isConnected) return;
      this._items = data.items || [];
      this._total = data.total ?? 0;
      this._offset = data.offset ?? 0;
      this._stats = data.stats || null;
      this._scan = data.scan || null;
      this._ignoreRevision = data.ignore_revision ?? null;
      this._entities = data.entities || [];
      this._paintControls();
      this._paintMeta();
      this._paintItems();
      this._paintPager(this._total, this._offset, data.limit ?? PAGE_SIZE);
      this._status.textContent = this._items.length ? "Coverage refreshed. No AI request was sent." : "No calendar titles found. Try another search or filter.";
    } catch (error) {
      if (version !== this._version) return;
      this._status.replaceChildren(
        element("span", "Cannot refresh coverage: " + statusMessage(error) + " ", "error"),
        action("Try again", () => this._refresh()));
    } finally {
      // Only the owning refresh may release the lock, even when a newer
      // read discarded this result.
      if (this._refreshToken === token) this._refreshToken = null;
    }
  }

  _ignoreDialog(item) {
    const remove = item.ignored;
    // Capture the ignore revision the user saw; the submitted payload must
    // carry it back unchanged so the backend can reject stale edits.
    const revision = this._ignoreRevision;
    this._version++;
    if (!this._dialog.open) this._returnFocus = this.shadowRoot.activeElement;
    this._dialog.replaceChildren();
    const heading = element("h2", remove ? "Remove ignore" : "Ignore exact title");
    heading.id = "coverage-dialog-title";
    const error = element("p", "", "error");
    error.setAttribute("role", "alert");
    this._dialog.append(heading, error);
    this._dialog.append(element("p", remove
      ? "Stop ignoring this exact title? It stays visible; nothing is deleted or unlinked."
      : "Add this exact title to the ignored list in all monitored calendars? It stays visible; nothing is deleted or unlinked."));
    this._dialog.append(element("pre", item.raw_summary, "raw"));
    this._dialog.append(element("p",
      "New attempts stop; in-flight requests may still finish and bill. " +
      (remove
        ? "Removing may authorize the NEXT scheduled scan but never starts one immediately."
        : "An ignored title can still be assigned an image or manually generated; its ignore state is preserved."), "muted"));
    const confirm = action(remove ? "Remove ignore" : "Ignore exact title", async () => {
      confirm.disabled = true;
      error.textContent = "";
      try {
        // The exact raw string is sent unchanged: no trimming or normalization.
        const result = await this._call("ignore", {raw_summary: item.raw_summary, ignored: !remove, revision});
        if (!this.isConnected) return;
        this._ignoreRevision = result.revision ?? this._ignoreRevision;
        this._dialog.close();
        await this._load();
      } catch (invokeError) {
        const code = String(invokeError?.code || "");
        const text = statusMessage(invokeError);
        if (/conflict|revision|stale/i.test(code + " " + text)) {
          this._dialog.close();
          await this._load();
          this._status.replaceChildren(element("span",
            "The ignore list changed elsewhere. Reloaded latest coverage — review before retrying. ", "error"));
        } else {
          error.textContent = text;
          confirm.disabled = false;
        }
      }
    });
    const cancel = action("Cancel", () => this._dialog.close());
    const row = element("div", undefined, "actions");
    row.append(confirm, cancel);
    this._dialog.append(row);
    if (!this._dialog.open) this._dialog.showModal();
    queueMicrotask(() => (this._dialog.querySelector("button") || this._dialog).focus());
  }
}
if (!customElements.get("activity-images-coverage")) {
  customElements.define("activity-images-coverage", ActivityImagesCoverage);
}
