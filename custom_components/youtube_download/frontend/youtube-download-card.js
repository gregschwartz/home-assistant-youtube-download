/*
 * YouTube Download card
 *
 * Paste a YouTube link or an image URL, check the preview is the right thing,
 * pick the media folder, hit Download. YouTube links are saved as MP3, images
 * are saved as-is. Everything runs over the Home Assistant websocket
 * connection, so no long-lived access token is needed.
 */

const DOMAIN = "youtube_download";
const CARD_VERSION = "1.0.0";

const PREVIEW_DEBOUNCE_MS = 600;

const STATE_LABELS = {
  downloading: "Downloading",
  completed: "Saved",
  failed: "Failed",
  cancelled: "Cancelled",
};

const STATE_ICONS = {
  downloading: "mdi:download",
  completed: "mdi:check-circle",
  failed: "mdi:alert-circle",
  cancelled: "mdi:cancel",
};

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .header {
    display: flex; align-items: center; gap: 8px;
    font-size: 1.25rem; font-weight: 500; margin-bottom: 12px;
  }
  .header ha-icon { color: var(--primary-color); }

  label.field { display: block; margin-top: 12px; }
  label.field > span {
    display: block; font-size: 0.8rem; font-weight: 500;
    color: var(--secondary-text-color); margin-bottom: 4px;
  }
  input[type="text"] {
    width: 100%; box-sizing: border-box;
    padding: 10px 12px;
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 6px;
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    font-size: 1rem; font-family: inherit;
  }
  input[type="text"]:focus { outline: none; border-color: var(--primary-color); }

  .preview {
    display: flex; gap: 12px; align-items: flex-start;
    margin-top: 12px; padding: 10px;
    border: 1px solid var(--divider-color, #e0e0e0); border-radius: 8px;
  }
  .preview img {
    flex: 0 0 auto; width: 160px; max-width: 45%;
    border-radius: 6px; display: block;
    background: var(--secondary-background-color, #f5f5f5);
  }
  .preview.image-preview img { width: 100%; max-width: 100%; }
  .preview.image-preview { flex-direction: column; }
  .preview-text { flex: 1 1 auto; min-width: 0; }
  .preview-title {
    font-weight: 500; overflow-wrap: anywhere;
  }
  .preview-sub {
    font-size: 0.85rem; color: var(--secondary-text-color); margin-top: 2px;
    overflow-wrap: anywhere;
  }
  .preview-kind {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--primary-color); margin-bottom: 4px;
  }
  .checking {
    margin-top: 12px; font-size: 0.9rem; color: var(--secondary-text-color);
  }

  .folders {
    margin-top: 12px; max-height: 260px; overflow-y: auto;
    border: 1px solid var(--divider-color, #e0e0e0); border-radius: 8px;
    padding: 4px 0;
  }
  .folder {
    display: flex; align-items: center; gap: 10px;
    padding: 7px 12px; cursor: pointer; font-size: 0.95rem;
  }
  .folder:hover { background: var(--secondary-background-color, #f5f5f5); }
  .folder input { flex: 0 0 auto; margin: 0; accent-color: var(--primary-color); }
  .folder-label { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
  .folder-source {
    flex: 0 0 auto; font-size: 0.75rem; color: var(--secondary-text-color);
  }
  .folders-empty {
    padding: 12px; font-size: 0.9rem; color: var(--secondary-text-color);
  }

  .actions { display: flex; gap: 8px; align-items: center; margin-top: 14px; }
  button {
    font-family: inherit; font-size: 0.95rem; cursor: pointer;
    border-radius: 6px; border: none; padding: 10px 16px;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.secondary {
    background: transparent; color: var(--primary-color);
    border: 1px solid var(--divider-color, #e0e0e0);
    padding: 6px 10px; font-size: 0.85rem;
  }

  .error {
    margin-top: 12px; padding: 8px 12px; border-radius: 6px;
    background: rgba(219, 68, 55, 0.12); color: var(--error-color, #db4437);
    font-size: 0.9rem; overflow-wrap: anywhere;
  }

  .jobs { margin-top: 16px; display: flex; flex-direction: column; gap: 10px; }
  .job {
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 8px; padding: 10px 12px;
  }
  .job-top { display: flex; align-items: center; gap: 8px; }
  .job-title {
    flex: 1 1 auto; min-width: 0; font-weight: 500;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .job-meta {
    font-size: 0.8rem; color: var(--secondary-text-color);
    margin-top: 2px; overflow-wrap: anywhere;
  }
  .bar {
    height: 6px; border-radius: 3px; margin-top: 8px; overflow: hidden;
    background: var(--divider-color, #e0e0e0);
  }
  .bar > div {
    height: 100%; background: var(--primary-color);
    transition: width 0.3s ease;
  }
  .state-completed ha-icon { color: var(--success-color, #43a047); }
  .state-failed ha-icon, .state-cancelled ha-icon { color: var(--error-color, #db4437); }
`;

const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char])
  );

class YouTubeDownloadCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });

    this._hass = null;
    this._config = {};
    this._built = false;

    this._url = "";
    this._filename = "";
    this._folder = null;
    this._folders = [];
    this._preferredFolder = null;
    this._preview = null;
    this._checking = false;
    this._error = "";
    this._jobs = [];
    this._starting = false;

    this._previewTimer = null;
    this._previewToken = 0;
    this._unsubscribe = null;
    this._loadedFolders = false;
  }

  static getConfigElement() {
    return document.createElement("youtube-download-card-editor");
  }

  static getStubConfig() {
    return { title: "Download to media" };
  }

  setConfig(config) {
    this._config = config || {};
    if (this._built) this._render();
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._build();
      this._loadFolders();
      this._subscribe();
    }
  }

  connectedCallback() {
    if (this._hass && !this._unsubscribe) this._subscribe();
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = null;
    }
    clearTimeout(this._previewTimer);
  }

  // -- data -------------------------------------------------------------

  async _call(message) {
    return this._hass.connection.sendMessagePromise(message);
  }

  async _loadFolders() {
    if (this._loadedFolders) return;
    this._loadedFolders = true;
    try {
      const result = await this._call({ type: `${DOMAIN}/folders` });
      this._folders = result.folders || [];
      this._preferredFolder = result.preferred || null;
    } catch (err) {
      this._error = err.message || "Could not list media folders";
    }
    this._render();
  }

  async _subscribe() {
    if (this._unsubscribe || !this._hass) return;
    try {
      this._unsubscribe = await this._hass.connection.subscribeMessage(
        (event) => {
          if (event.type === "jobs") {
            this._jobs = event.jobs || [];
          } else if (event.type === "job") {
            const rest = this._jobs.filter((job) => job.id !== event.job.id);
            this._jobs = [event.job, ...rest];
          }
          this._renderJobs();
        },
        { type: `${DOMAIN}/subscribe` }
      );
    } catch (err) {
      this._error = err.message || "Could not subscribe to downloads";
      this._render();
    }
  }

  _queuePreview() {
    clearTimeout(this._previewTimer);
    const url = this._url.trim();

    if (!url) {
      this._preview = null;
      this._checking = false;
      this._error = "";
      this._previewToken += 1;
      this._render();
      return;
    }

    this._previewTimer = setTimeout(() => this._fetchPreview(), PREVIEW_DEBOUNCE_MS);
  }

  async _fetchPreview() {
    const url = this._url.trim();
    if (!url) return;

    const token = ++this._previewToken;
    this._checking = true;
    this._error = "";
    this._render();

    let preview = null;
    let error = "";
    try {
      preview = await this._call({ type: `${DOMAIN}/preview`, url });
    } catch (err) {
      error = err.message || "Could not read that URL";
    }

    // A newer keystroke already started its own lookup; drop this answer.
    if (token !== this._previewToken) return;

    this._checking = false;
    this._preview = preview;
    this._error = error;

    if (preview) {
      this._filename = preview.suggested_filename || "";
      // A video defaults to the preferred folder; an image clears the choice
      // so a destination has to be picked deliberately.
      this._folder = preview.suggested_folder || null;
    }
    this._render();
  }

  async _download() {
    if (!this._preview || !this._folder || this._starting) return;

    this._starting = true;
    this._error = "";
    this._render();

    try {
      await this._call({
        type: `${DOMAIN}/download`,
        url: this._preview.url || this._url.trim(),
        folder: this._folder,
        filename: this._filename.trim() || null,
      });
      // Clear the form; the job list takes over from here.
      this._url = "";
      this._filename = "";
      this._preview = null;
      this._folder = null;
      this._previewToken += 1;
    } catch (err) {
      this._error = err.message || "Could not start the download";
    }

    this._starting = false;
    this._render();
  }

  async _cancel(jobId) {
    try {
      await this._call({ type: `${DOMAIN}/cancel`, job_id: jobId });
    } catch (err) {
      this._error = err.message || "Could not cancel";
      this._render();
    }
  }

  async _clearFinished() {
    try {
      await this._call({ type: `${DOMAIN}/clear_finished` });
    } catch (err) {
      this._error = err.message || "Could not clear the list";
      this._render();
    }
  }

  // -- rendering --------------------------------------------------------

  _build() {
    if (this._built) return;
    this._built = true;

    const style = document.createElement("style");
    style.textContent = STYLES;
    this.shadowRoot.appendChild(style);

    this._card = document.createElement("ha-card");
    this.shadowRoot.appendChild(this._card);

    this._render();
  }

  _render() {
    if (!this._card) return;

    // A render replaces the whole card, so remember where the caret was and put
    // it back afterwards - otherwise a preview arriving mid-sentence would kick
    // you out of the field you are typing in.
    const active = this.shadowRoot.activeElement;
    const focusedId = active && active.id ? active.id : null;
    const caret = focusedId ? active.selectionStart : null;

    const title = this._config.title || "Download to media";
    const canDownload = Boolean(this._preview && this._folder && !this._starting);

    this._card.innerHTML = `
      <div class="header">
        <ha-icon icon="mdi:download-box"></ha-icon>
        <span>${escapeHtml(title)}</span>
      </div>

      <label class="field">
        <span>YouTube or image URL</span>
        <input id="url" type="text" spellcheck="false"
               placeholder="https://www.youtube.com/watch?v=..."
               value="${escapeHtml(this._url)}">
      </label>

      ${this._previewHtml()}

      ${
        this._preview
          ? `<label class="field">
               <span>Filename${
                 this._preview.extension
                   ? ` (${escapeHtml(this._preview.extension)} is added)`
                   : ""
               }</span>
               <input id="filename" type="text" spellcheck="false"
                      value="${escapeHtml(this._filename)}">
             </label>`
          : ""
      }

      <label class="field"><span>Destination folder</span></label>
      ${this._foldersHtml()}

      <div class="actions">
        <button id="download" ${canDownload ? "" : "disabled"}>
          ${this._starting ? "Starting…" : "Download"}
        </button>
        ${
          this._jobs.some((job) => job.state !== "downloading")
            ? `<button id="clear" class="secondary">Clear finished</button>`
            : ""
        }
      </div>

      ${this._error ? `<div class="error">${escapeHtml(this._error)}</div>` : ""}

      <div class="jobs" id="jobs">${this._jobsHtml()}</div>
    `;

    this._wireUp();

    if (focusedId) {
      const restored = this._card.querySelector(`#${focusedId}`);
      if (restored) {
        restored.focus();
        if (caret !== null) {
          try {
            restored.setSelectionRange(caret, caret);
          } catch (err) {
            // Not every input type supports a selection range; harmless.
          }
        }
      }
    }
  }

  _previewHtml() {
    if (this._checking) {
      return `<div class="checking">Checking the URL…</div>`;
    }
    if (!this._preview) return "";

    const preview = this._preview;
    const isImage = preview.kind === "image";
    const source = isImage ? preview.image : preview.thumbnail;

    return `
      <div class="preview ${isImage ? "image-preview" : ""}">
        ${
          source
            ? `<img src="${escapeHtml(source)}" alt="" referrerpolicy="no-referrer">`
            : ""
        }
        <div class="preview-text">
          <div class="preview-kind">
            <ha-icon icon="${isImage ? "mdi:image" : "mdi:youtube"}"
                     style="--mdc-icon-size:16px"></ha-icon>
            ${isImage ? "Image" : "YouTube → MP3"}
          </div>
          <div class="preview-title">${escapeHtml(preview.title)}</div>
          ${
            preview.subtitle
              ? `<div class="preview-sub">${escapeHtml(preview.subtitle)}</div>`
              : ""
          }
        </div>
      </div>
    `;
  }

  _foldersHtml() {
    if (!this._folders.length) {
      return `<div class="folders-empty">
        No media directories are configured, so there is nowhere to save to.
      </div>`;
    }

    const rows = this._folders
      .map((folder, index) => {
        const checked = this._folder === folder.path ? "checked" : "";
        const showSource =
          new Set(this._folders.map((item) => item.source)).size > 1;
        return `
          <label class="folder">
            <input type="radio" name="folder" value="${index}" ${checked}>
            <span class="folder-label">${escapeHtml(folder.label)}</span>
            ${
              showSource
                ? `<span class="folder-source">${escapeHtml(folder.source)}</span>`
                : ""
            }
          </label>
        `;
      })
      .join("");

    return `<div class="folders">${rows}</div>`;
  }

  _jobsHtml() {
    const limit = this._config.max_jobs || 5;
    return this._jobs
      .slice(0, limit)
      .map((job) => {
        const label = STATE_LABELS[job.state] || job.state;
        const icon = STATE_ICONS[job.state] || "mdi:download";
        const running = job.state === "downloading";
        return `
          <div class="job state-${escapeHtml(job.state)}">
            <div class="job-top">
              <ha-icon icon="${icon}"></ha-icon>
              <div class="job-title">${escapeHtml(job.filename || job.title)}</div>
              ${
                running
                  ? `<button class="secondary cancel" data-job="${escapeHtml(
                      job.id
                    )}">Cancel</button>`
                  : ""
              }
            </div>
            <div class="job-meta">${escapeHtml(label)} · ${escapeHtml(
          job.message || ""
        )}</div>
            ${
              running
                ? `<div class="bar"><div style="width:${Math.round(
                    (job.progress || 0) * 100
                  )}%"></div></div>`
                : ""
            }
          </div>
        `;
      })
      .join("");
  }

  _renderJobs() {
    // Re-rendering only the job list keeps focus in the URL field while a
    // download is streaming progress in.
    const container = this._card && this._card.querySelector("#jobs");
    if (!container) {
      this._render();
      return;
    }
    container.innerHTML = this._jobsHtml();
    this._wireUpJobs();
  }

  _wireUp() {
    const url = this._card.querySelector("#url");
    if (url) {
      url.addEventListener("input", (event) => {
        this._url = event.target.value;
        this._queuePreview();
      });
      url.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          clearTimeout(this._previewTimer);
          this._fetchPreview();
        }
      });
    }

    const filename = this._card.querySelector("#filename");
    if (filename) {
      filename.addEventListener("input", (event) => {
        this._filename = event.target.value;
        const button = this._card.querySelector("#download");
        if (button) button.disabled = !(this._preview && this._folder);
      });
    }

    this._card.querySelectorAll('input[name="folder"]').forEach((radio) => {
      radio.addEventListener("change", (event) => {
        const folder = this._folders[Number(event.target.value)];
        this._folder = folder ? folder.path : null;
        const button = this._card.querySelector("#download");
        if (button) button.disabled = !(this._preview && this._folder);
      });
    });

    const download = this._card.querySelector("#download");
    if (download) download.addEventListener("click", () => this._download());

    const clear = this._card.querySelector("#clear");
    if (clear) clear.addEventListener("click", () => this._clearFinished());

    this._wireUpJobs();
  }

  _wireUpJobs() {
    this._card.querySelectorAll(".cancel").forEach((button) => {
      button.addEventListener("click", () =>
        this._cancel(button.getAttribute("data-job"))
      );
    });
  }
}

class YouTubeDownloadCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  _render() {
    if (this._rendered) return;
    this._rendered = true;

    this.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;padding:8px 0;">
        <label>Card title
          <input id="title" type="text" style="width:100%;padding:8px;margin-top:4px;">
        </label>
        <label>Downloads to show
          <input id="max_jobs" type="number" min="1" max="20"
                 style="width:100%;padding:8px;margin-top:4px;">
        </label>
      </div>
    `;

    ["title", "max_jobs"].forEach((field) => {
      const input = this.querySelector(`#${field}`);
      if (this._config[field] !== undefined) input.value = this._config[field];
      input.addEventListener("change", () => {
        this._config = {
          ...this._config,
          [field]:
            field === "max_jobs" ? Number(input.value) || 5 : input.value,
        };
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: this._config },
            bubbles: true,
            composed: true,
          })
        );
      });
    });
  }
}

customElements.define("youtube-download-card", YouTubeDownloadCard);
customElements.define("youtube-download-card-editor", YouTubeDownloadCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "youtube-download-card",
  name: "YouTube Download",
  description:
    "Paste a YouTube link or image URL, preview it, and save it to a media folder.",
  preview: false,
  documentationURL:
    "https://github.com/gregschwartz/home-assistant-youtube-download",
});

console.info(
  `%c YOUTUBE-DOWNLOAD-CARD %c ${CARD_VERSION} `,
  "color:white;background:#03a9f4;font-weight:700;",
  "color:#03a9f4;background:white;font-weight:700;"
);
