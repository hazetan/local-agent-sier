/**
 * SIer Multi-Agent Realtime Web Client (文字数制限なし・WebSocket即時反映)
 * 特設ページ内の以下のID要素に自動バインドします:
 * - #sier-active-role, #sier-vram-bar, #sier-ram-bar, #sier-roles-tbody, #sier-tasks-list
 * - #sier-raw-header, #sier-raw-thought, #sier-raw-tool, #sier-raw-output
 * - #sier-feed-log, #sier-chat-form, #sier-chat-input, #sier-chat-files
 */
export class SIerDashboardClient {
  constructor(apiBase = "/api/sier") {
    this.apiBase = apiBase;
    this.ws = null;
  }

  init() {
    this.connectWebSocket();
    this.bindChatForm();
  }

  connectWebSocket() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}${this.apiBase}/ws`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "init") {
        this.renderMetrics(msg.metrics);
        this.renderRawStream(msg.metrics.raw_stream);
        (msg.feed || []).forEach((ev) => this.appendFeedEvent(ev));
      } else if (msg.type === "metrics") {
        this.renderMetrics(msg.data);
      } else if (msg.type === "raw_stream") {
        this.renderRawStream(msg.data);
      } else if (msg.type === "feed") {
        this.appendFeedEvent(msg.event);
      }
    };

    this.ws.onclose = () => {
      setTimeout(() => this.connectWebSocket(), 3000);
    };
  }

  renderMetrics(data) {
    const activeEl = document.getElementById("sier-active-role");
    if (activeEl) activeEl.textContent = `${data.active_role_label} (${data.timestamp})`;

    const hw = data.hardware;
    const vramEl = document.getElementById("sier-vram-bar");
    if (vramEl) {
      vramEl.textContent = `VRAM: ${hw.vram_used_gb} / ${hw.vram_total_gb} GB (${hw.vram_pct}%)`;
    }
    const ramEl = document.getElementById("sier-ram-bar");
    if (ramEl) {
      ramEl.textContent = `RAM: ${hw.ram_used_gb} / ${hw.ram_total_gb} GB (${hw.ram_pct}%) | RPi Free: ${hw.rpi_free_gb} GB`;
    }

    const rolesTbody = document.getElementById("sier-roles-tbody");
    if (rolesTbody) {
      rolesTbody.innerHTML = data.roles
        .map((r) => {
          const badge = r.is_heavy ? "★ PRO" : r.is_active ? "▶ RUN" : "• IDLE";
          return `<tr class="${r.is_active ? "active-role" : ""}">
            <td>${badge} ${r.role}</td>
            <td>${r.model}</td>
            <td>${r.mem_gb} GB (${r.mem_pct}%)</td>
            <td>${r.tps} t/s</td>
            <td>${r.ctx_tokens.toLocaleString()} / ${r.max_ctx.toLocaleString()} (${r.ctx_pct}%)</td>
          </tr>`;
        })
        .join("");
    }

    const tasksList = document.getElementById("sier-tasks-list");
    if (tasksList) {
      tasksList.innerHTML = data.tasks
        .map((t) => {
          const activeTag = t.working_role ? ` [稼働中: ${t.working_role}]` : "";
          return `<li class="task-${t.status.toLowerCase()}">
            <strong>[${t.status}]${activeTag}</strong> ${this.escapeHtml(t.title)}
          </li>`;
        })
        .join("");
    }
  }

  renderRawStream(raw) {
    if (!raw) return;
    const hdr = document.getElementById("sier-raw-header");
    if (hdr) {
      hdr.textContent = `Role: ${raw.role} (${raw.model}) | Step: ${raw.step}/${raw.max_steps} (${raw.latency}s, ${raw.tps} t/s)`;
    }
    this.setPreContent("sier-raw-thought", raw.thought);
    this.setPreContent("sier-raw-tool", `Tool: ${raw.tool_name}\nArgs: ${raw.tool_args}`);
    this.setPreContent("sier-raw-output", raw.tool_output);
  }

  setPreContent(elementId, text) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    el.textContent = text || "";
    if (isAtBottom) el.scrollTop = el.scrollHeight;
  }

  appendFeedEvent(ev) {
    const feedEl = document.getElementById("sier-feed-log");
    if (!feedEl) return;
    const item = document.createElement("div");
    item.className = `feed-item feed-${ev.sender.toLowerCase()}`;
    item.innerHTML = `<div class="feed-meta">[${ev.timestamp}] <strong>${this.escapeHtml(ev.sender)}</strong></div>
                      <pre class="feed-body">${this.escapeHtml(ev.text)}</pre>`;
    feedEl.appendChild(item);
    feedEl.scrollTop = feedEl.scrollHeight;
  }

  bindChatForm() {
    const form = document.getElementById("sier-chat-form");
    const input = document.getElementById("sier-chat-input");
    const filesInput = document.getElementById("sier-chat-files");
    if (!form || !input) return;

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const formData = new FormData();
      formData.append("message", input.value);
      if (filesInput && filesInput.files) {
        for (const f of filesInput.files) {
          formData.append("files", f);
        }
      }
      input.value = "";
      if (filesInput) filesInput.value = "";

      await fetch(`${this.apiBase}/chat`, {
        method: "POST",
        body: formData,
      });
    });
  }

  escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }
}