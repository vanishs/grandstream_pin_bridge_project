/* Device details extension: depends on HA's internal device-page DOM. */
class GDSAccessUsers extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; margin-top:16px; }
        ha-card { padding:16px; }
        header { display:flex; align-items:center; justify-content:space-between; gap:12px; }
        h2 { font-size:20px; font-weight:400; margin:0; }
        button { color:var(--primary-color); background:transparent; border:1px solid var(--divider-color); border-radius:8px; padding:8px 16px; cursor:pointer; }
        button:disabled { opacity:.5; cursor:wait; }
        .scroll { max-height:400px; overflow:auto; }
        table { width:100%; border-collapse:collapse; }
        th,td { text-align:start; padding:10px 4px; border-bottom:1px solid var(--divider-color); overflow-wrap:anywhere; }
        td { white-space:pre-wrap; }
        p { color:var(--secondary-text-color); }
      </style>
      <ha-card><header><h2>Access Control Users</h2><button type="button"></button></header>
        <p role="status" aria-live="polite"></p>
        <div class="scroll"><table><thead><tr><th>user_id</th><th>username</th></tr></thead><tbody></tbody></table></div>
      </ha-card>`;
    this.shadowRoot.querySelector("button").addEventListener("click", () => this.refresh());
  }

  configure(hass, deviceId) {
    this.hass = hass;
    const changed = this.deviceId !== deviceId;
    this.deviceId = deviceId;
    this.shadowRoot.querySelector("button").textContent = this.zh ? "刷新" : "Refresh";
    if (changed) this.refresh();
  }

  get zh() { return this.hass?.language?.startsWith("zh"); }

  disconnectedCallback() {
    // Ignore requests completing after navigation/removal.
    this.requestId = (this.requestId || 0) + 1;
  }

  async refresh() {
    const requestId = this.requestId = (this.requestId || 0) + 1;
    const button = this.shadowRoot.querySelector("button");
    const status = this.shadowRoot.querySelector("p");
    const body = this.shadowRoot.querySelector("tbody");
    button.disabled = true;
    body.replaceChildren();
    status.textContent = this.zh ? "正在读取…" : "Loading…";
    try {
      const result = await this.hass.callWS({
        type: "grandstream_pin_bridge/access_users", device_id: this.deviceId,
      });
      if (requestId !== this.requestId) return;
      for (const user of result.users) {
        const row = document.createElement("tr");
        for (const key of ["user_id", "username"]) {
          const cell = document.createElement("td");
          cell.textContent = user[key];
          row.append(cell);
        }
        body.append(row);
      }
      status.textContent = this.zh ? `共 ${result.users.length} 位用户` : `${result.users.length} users`;
    } catch (error) {
      if (requestId !== this.requestId) return;
      const auth = error?.code === "invalid_auth";
      status.textContent = this.zh
        ? (auth ? "设备认证失败或无读取权限。" : "读取失败，请确认集成已加载及设备可连接后重试。")
        : (auth ? "Device authentication or permissions rejected." : "Could not read users. Check the integration and device connection, then retry.");
    } finally {
      if (requestId === this.requestId) button.disabled = false;
    }
  }
}

if (!customElements.get("gds-access-users")) {
  customElements.define("gds-access-users", GDSAccessUsers);
  customElements.whenDefined("ha-config-device-page").then(() => {
    const prototype = customElements.get("ha-config-device-page").prototype;
    const original = prototype.updated;
    prototype.updated = function (changed) {
      original?.call(this, changed);
      const device = this.hass?.devices?.[this.deviceId];
      const supported = this.hass?.user?.is_admin && device?.manufacturer === "Grandstream" && device?.model === "GDS3725";
      const existing = this.shadowRoot?.querySelector("gds-access-users");
      if (!supported) { existing?.remove(); return; }
      const anchor = this.shadowRoot?.querySelector("ha-device-info-card");
      if (!anchor) return;
      const card = existing || document.createElement("gds-access-users");
      if (!existing) anchor.insertAdjacentElement("afterend", card);
      card.configure(this.hass, this.deviceId);
    };
  });
}
