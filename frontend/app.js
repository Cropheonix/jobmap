/* JobMap 前端主逻辑：Vue 3（本地 vendor 构建，无构建步骤）。
 * 核心原则：滑块/筛选变化全部在客户端即时重排，不重新请求；搜索任务才走网络。
 */
(() => {
  const { createApp } = Vue;

  const DEFAULT_WEIGHTS = { M: 30, P: 25, C: 20, R: 15, D: 10, V: 10 };
  const PRECISION_RANK = { building: 0, block: 1, district: 2, unlocatable: 3 };
  const STATUS_GROUP = { active: 0, unverified: 1 };
  const ORG_GROUP = { studio: 0, company: 1, enterprise: 2, institute: 3, unknown: 4 };
  const MISSING_LABELS = {
    salary: "薪资未披露", address: "地址缺失", coords: "无法定位",
    published_at: "发布时间未知",
  };
  const FLAG_LABELS = {
    snippet_only: "仅索引摘要", stale: "信息陈旧", needs_review: "建议人工核实",
    entity_unconfirmed: "主体待确认", entity_ambiguous: "同名歧义", city_mismatch: "城市不符",
    salary_invalid: "薪资异常", date_invalid: "日期异常", geo_unverified: "地区待核实",
    archived: "已归档", entity_ambiguous_2fa: "歧义",
  };

  // WGS-84 → GCJ-02（当底图为国测局坐标系时使用）
  function wgs84ToGcj02(lon, lat) {
    const PI = Math.PI, A = 6378245.0, EE = 0.00669342162296594323;
    const outOfChina = (lo, la) => !(lo > 73.66 && lo < 135.05 && la > 3.86 && la < 53.55);
    if (outOfChina(lon, lat)) return [lon, lat];
    const tLat = (x, y) => {
      let r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
      r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
      r += (20 * Math.sin(y * PI) + 40 * Math.sin(y / 3 * PI)) * 2 / 3;
      r += (160 * Math.sin(y / 12 * PI) + 320 * Math.sin(y * PI / 30)) * 2 / 3;
      return r;
    };
    const tLon = (x, y) => {
      let r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
      r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
      r += (20 * Math.sin(x * PI) + 40 * Math.sin(x / 3 * PI)) * 2 / 3;
      r += (150 * Math.sin(x / 12 * PI) + 300 * Math.sin(x / 30 * PI)) * 2 / 3;
      return r;
    };
    let dLat = tLat(lon - 105, lat - 35), dLon = tLon(lon - 105, lat - 35);
    const radLat = lat / 180 * PI;
    let magic = Math.sin(radLat); magic = 1 - EE * magic * magic;
    const sqrtMagic = Math.sqrt(magic);
    dLat = (dLat * 180) / ((A * (1 - EE)) / (magic * sqrtMagic) * PI);
    dLon = (dLon * 180) / (A / sqrtMagic * Math.cos(radLat) * PI);
    return [lon + dLon, lat + dLat];
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const app = createApp({
    data() {
      return {
        session: { enums: { job_categories: [], org_types: {}, statuses: {}, precisions: {}, actions: {}, ranking_modes: {}, source_types: {}, weight_labels: {} }, tile: {} },
        token: "",
        loading: false,
        searching: false,
        jobs: [],
        stats: {},
        tasks: [],
        logs: [],
        activeTask: null,
        modal: null,
        selectedId: null,
        expandedId: null,
        mapPrecision: "district",
        channel: { on: false, hint: "" },
        searchText: "",
        rankingMode: "score",
        weights: { ...DEFAULT_WEIGHTS },
        activePreset: "",
        presets: {},
        importMsg: "",
        draft: { min: null, max: null, lon: null, lat: null, orgType: "", tileUrl: "", tileAttr: "", tileCrs: "wgs84", extUrl: "", filterLevel: "standard" },
        filters: {
          keywordsText: "", keywordMode: "AND", city: "", district: "",
          categories: [], experience: "", salaryMin: 0, keepUndisclosed: true,
          statuses: ["active", "unverified"], sourceTypes: [], precisions: [],
          publishedDays: 0, action: "all",
        },
        experienceLevels: ["应届/实习", "1-3年", "3-5年", "5-10年", "10年以上", "不限"],
      };
    },

    computed: {
      normalizedWeights() {
        const sum = Object.values(this.weights).reduce((a, b) => a + (Number(b) || 0), 0);
        if (sum <= 0) {
          const defSum = Object.values(DEFAULT_WEIGHTS).reduce((a, b) => a + b, 0);
          return Object.fromEntries(Object.entries(DEFAULT_WEIGHTS).map(([k, v]) => [k, v / defSum]));
        }
        return Object.fromEntries(Object.entries(this.weights).map(([k, v]) => [k, (Number(v) || 0) / sum]));
      },
      cities() { return [...new Set(this.jobs.map((j) => j.city).filter(Boolean))]; },
      districts() {
        const pool = this.filters.city ? this.jobs.filter((j) => j.city === this.filters.city) : this.jobs;
        return [...new Set(pool.map((j) => j.district).filter(Boolean))];
      },
      keywordTokens() {
        return this.filters.keywordsText.trim().toLowerCase()
          .replace(/[，,、]/g, " ").split(/\s+/).filter(Boolean);
      },
      filteredJobs() {
        const f = this.filters;
        return this.jobs.filter((j) => {
          if (f.city && j.city !== f.city) return false;
          if (f.district && j.district !== f.district) return false;
          if (f.categories.length && !f.categories.includes(j.job_category)) return false;
          if (f.experience && j.experience !== f.experience) return false;
          if (f.statuses.length && !f.statuses.includes(j.status)) return false;
          if (f.sourceTypes.length && !j.sources.some((s) => f.sourceTypes.includes(s.source_type))) return false;
          if (f.precisions.length && !f.precisions.includes(j.location_precision)) return false;
          if (f.action !== "all" && j.action.status !== f.action) return false;
          const undisclosed = !j.salary_max && !j.salary_min;
          if (!f.keepUndisclosed && undisclosed) return false;
          if (f.salaryMin > 0 && !undisclosed && (j.salary_max || 0) < f.salaryMin) return false;
          if (f.publishedDays > 0) {
            if (!j.published_at) return false;
            const days = (Date.now() - new Date(j.published_at)) / 86400000;
            if (days > f.publishedDays) return false;
          }
          const toks = this.keywordTokens;
          if (toks.length) {
            const blob = [j.title, j.description_summary, j.organization.name,
              (j.keywords || []).join(" "), j.industry, j.job_category, j.district].join(" ").toLowerCase();
            const hit = (t) => blob.includes(t);
            if (f.keywordMode === "AND" ? !toks.every(hit) : !toks.some(hit)) return false;
          }
          return true;
        });
      },
      rankedJobs() {
        const items = this.filteredJobs.map((j) => ({ ...j, _total: this.jobTotal(j) }));
        const byScore = (a, b) => b._total - a._total ||
          (b.subscores.R || 0) - (a.subscores.R || 0) ||
          (b.published_at || "").localeCompare(a.published_at || "") || a.id - b.id;
        if (this.rankingMode === "newest") {
          items.sort((a, b) => (b.published_at || "").localeCompare(a.published_at || "") || byScore(a, b));
        } else if (this.rankingMode === "salary") {
          items.sort((a, b) => (b.salary_max ?? -1) - (a.salary_max ?? -1) || byScore(a, b));
        } else if (this.rankingMode === "team") {
          items.sort((a, b) =>
            (ORG_GROUP[a.organization.type] ?? 9) - (ORG_GROUP[b.organization.type] ?? 9) ||
            (STATUS_GROUP[a.status] ?? 2) - (STATUS_GROUP[b.status] ?? 2) || byScore(a, b));
        } else {
          items.sort((a, b) =>
            (STATUS_GROUP[a.status] ?? 2) - (STATUS_GROUP[b.status] ?? 2) || byScore(a, b));
        }
        return items;
      },
      pipelineSteps() {
        const steps = ["discover", "extract", "dedup", "rank", "geo", "enrich", "export"];
        const labels = { discover: "多源搜索", extract: "信息提取", dedup: "去重校验",
                         rank: "权重排序", geo: "地图标点", enrich: "公司核查", export: "导出报告" };
        if (this.activeTask) {
          const map = Object.fromEntries((this.activeTask.steps || []).map((s) => [s.step, s]));
          return steps.map((k) => map[k] || { step: k, state: "pending", counts: {} });
        }
        const s = this.stats;
        const countsByKey = {
          discover: s.sources_total, extract: s.total_jobs, dedup: s.total_jobs,
          rank: this.rankedJobs.length, geo: s.geo_located, enrich: s.discovered_jobs ?? 0,
          export: 1,
        };
        return steps.map((k) => ({ step: k, label: labels[k], state: "done", counts: { [k]: countsByKey[k] } }));
      },
      taskStateLabel() {
        const t = this.activeTask;
        if (!t) return "";
        return { running: "⟳ 搜索进行中…", done: "✓ 搜索完成", cancelled: "已取消", failed: "搜索失败", pending: "排队中" }[t.state] || t.state;
      },
    },

    watch: {
      weights: {
        handler() { this.debouncedSaveWeights(); },
        deep: true,
      },
      rankingMode() { this.debouncedSaveWeights(); },
      "filters.city"() { this.filters.district = ""; },
    },

    async mounted() {
      try { this.presets = JSON.parse(localStorage.getItem("jobmap_presets") || "{}"); } catch { this.presets = {}; }
      await this.loadSession();
      await Promise.all([this.loadPrefs(), this.loadJobs(), this.loadStats(), this.loadProviders()]);
      this.$nextTick(() => this.initMap());
      setInterval(() => { if (!this.modal) this.loadStats(); }, 30000);
    },

    methods: {
      /* ── API ── */
      async api(path, opts = {}) {
        const headers = { ...(opts.headers || {}) };
        if (opts.method && opts.method !== "GET") headers["X-JobMap-Token"] = this.token;
        if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
        const res = await fetch(path, { ...opts, headers });
        if (!res.ok) {
          let msg = res.statusText;
          try { msg = (await res.json()).detail || msg; } catch { /* ignore */ }
          throw new Error(msg);
        }
        return res.json();
      },
      async loadSession() {
        this.session = await this.api("/api/session/config");
        this.token = this.session.token;
      },
      async loadPrefs() {
        const p = await this.api("/api/preferences");
        this.weights = { ...DEFAULT_WEIGHTS, ...(p.weights || {}) };
        this.rankingMode = p.ranking_mode || "score";
        this.filters.keepUndisclosed = p.keep_undisclosed_salary !== false;
        this.draft = {
          min: p.salary_expect_min, max: p.salary_expect_max,
          lon: p.commute_origin_lon, lat: p.commute_origin_lat,
          orgType: p.org_type_pref || "",
          tileUrl: this.session.tile?.url || "", tileAttr: this.session.tile?.attribution || "",
          tileCrs: this.session.tile?.crs || "wgs84",
          extUrl: p.external_searxng_url || "", filterLevel: p.filter_level || "standard",
        };
        if (p.keywords?.length) this.searchText = p.keywords.join(" ");
        if (p.city) this.filters.city = p.city;
      },
      async loadJobs() {
        this.loading = true;
        try {
          const d = await this.api("/api/jobs?limit=2000");
          this.jobs = d.items;
        } finally { this.loading = false; }
      },
      async loadStats() { this.stats = await this.api("/api/stats"); },
      async loadProviders() {
        try {
          const d = await this.api("/api/providers");
          const p = (d.providers || []).find((x) => x.name === "searxng_discovery");
          this.channel = {
            on: !!(p && p.health && p.health.ok),
            hint: (p && p.health && p.health.hint) || "",
          };
        } catch { this.channel = { on: false, hint: "" }; }
      },

      /* ── 评分（客户端即时计算，维度键随服务端枚举扩展） ── */
      scoreKeys() {
        return Object.keys(this.session.enums.weight_labels || {});
      },
      jobTotal(job) {
        const w = this.normalizedWeights;
        let sum = 0;
        for (const k of this.scoreKeys()) sum += (w[k] || 0) * (job.subscores?.[k] ?? 50);
        return Math.round(sum * 10) / 10;
      },
      weightsPct(key) { return Math.round((this.normalizedWeights[key] || 0) * 100); },
      scoreClass(v) { return v >= 80 ? "hi" : v >= 65 ? "mid" : v >= 50 ? "low" : "weak"; },
      rankClass(idx) { return idx === 0 ? "r1" : idx === 1 ? "r2" : idx === 2 ? "r3" : ""; },
      missingLabel(k) { return MISSING_LABELS[k] || k; },
      flagLabel(f) {
        if (f.startsWith("conflict:")) return "字段冲突";
        return FLAG_LABELS[f] || f;
      },
      componentNotes(job, key) {
        const c = job.components?.[key];
        if (!c) return "";
        return Object.entries(c).filter(([, v]) => v !== "" && v !== null)
          .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join("；");
      },

      /* ── 权重与预设 ── */
      resetWeights() { this.weights = { ...DEFAULT_WEIGHTS }; },
      savePreset() {
        const name = prompt("预设名称：", `预设${Object.keys(this.presets).length + 1}`);
        if (!name) return;
        this.presets[name] = { weights: { ...this.weights }, rankingMode: this.rankingMode };
        localStorage.setItem("jobmap_presets", JSON.stringify(this.presets));
      },
      applyPreset() {
        const p = this.presets[this.activePreset];
        if (!p) return;
        this.weights = { ...p.weights };
        this.rankingMode = p.rankingMode || "score";
      },
      debouncedSaveWeights() {
        clearTimeout(this._wTimer);
        this._wTimer = setTimeout(async () => {
          try {
            await this.api("/api/preferences", { method: "PUT", json: { weights: this.weights, ranking_mode: this.rankingMode } });
          } catch { /* 本地保存失败不打断交互 */ }
        }, 1200);
      },

      /* ── 筛选 ── */
      toggleIn(arr, v) {
        const i = arr.indexOf(v);
        i >= 0 ? arr.splice(i, 1) : arr.push(v);
      },
      resetFilters() {
        this.filters = {
          keywordsText: "", keywordMode: "AND", city: "", district: "",
          categories: [], experience: "", salaryMin: 0, keepUndisclosed: true,
          statuses: ["active", "unverified"], sourceTypes: [], precisions: [],
          publishedDays: 0, action: "all",
        };
      },

      /* ── 搜索任务 ── */
      async startSearch() {
        if (this.searching) return;
        this.searching = true;
        try {
          const keywords = this.searchText.trim().replace(/[，,、]/g, " ").split(/\s+/).filter(Boolean);
          const providers = this.channel.on ? ["searxng_discovery", "sample"] : ["sample"];
          const task = await this.api("/api/search/tasks", {
            method: "POST",
            json: { keywords, city: this.filters.city, providers },
          });
          this.pollTask(task.id);
        } catch (e) {
          alert("搜索失败：" + e.message);
          this.searching = false;
        }
      },
      async pollTask(id) {
        try {
          const t = await this.api(`/api/search/tasks/${id}`);
          this.activeTask = t;
          if (t.state === "running" || t.state === "pending") {
            setTimeout(() => this.pollTask(id), 700);
          } else {
            this.searching = false;
            await Promise.all([this.loadJobs(), this.loadStats(), this.loadTasks()]);
            this.renderMarkers();
          }
        } catch { this.searching = false; }
      },
      async cancelTask() {
        if (!this.activeTask) return;
        await this.api(`/api/search/tasks/${this.activeTask.id}/cancel`, { method: "POST" }).catch(() => {});
      },
      taskStateLabelOf(t) {
        return { running: "⟳ 进行中", done: "✓ 完成", cancelled: "已取消", failed: "失败", pending: "排队" }[t.state] || t.state;
      },
      taskCountsText(t) {
        const c = Object.values(t.counts || {})[0];
        return c ? `发现 ${c.discovered ?? "-"} / 合并 ${c.merged ?? "-"} / 新建 ${c.created ?? "-"}` : "—";
      },
      stepCountText(s) {
        if (s.state === "running") return "…";
        const v = s.counts?.[s.step];
        if (s.step === "export") return s.state === "done" ? "就绪" : "";
        return v !== undefined && v !== null ? `${v}` : "";
      },
      async openTasks() {
        await this.loadTasks();
        this.modal = "tasks";
      },
      async loadTasks() {
        const d = await this.api("/api/search/tasks");
        this.tasks = d.tasks;
      },

      /* ── 选择联动（列表 ↔ 地图） ── */
      selectJob(id, from) {
        this.selectedId = id;
        this.expandedId = this.expandedId === id ? null : id;
        const job = this.jobs.find((j) => j.id === id);
        if (job?.location && from === "card") this.flyToJob(job);
        if (from === "map") {
          this.$nextTick(() => {
            const el = document.getElementById(`job-card-${id}`);
            if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
          });
        }
        // 展开时拉取公司风险信号（详情接口才带 org_risk）
        if (this.expandedId === id && job && !job.org_risk) {
          this.api(`/api/jobs/${id}`).then((detail) => {
            const i = this.jobs.findIndex((j) => j.id === id);
            if (i >= 0) this.jobs[i] = { ...this.jobs[i], ...detail };
          }).catch(() => {});
        }
      },

      /* ── 求职进度 ── */
      async setAction(job, status) {
        try {
          const updated = await this.api(`/api/jobs/${job.id}/action`, { method: "PATCH", json: { status } });
          const i = this.jobs.findIndex((j) => j.id === job.id);
          if (i >= 0) this.jobs[i] = updated;
        } catch (e) { alert("保存失败：" + e.message); }
      },
      async saveNote(job) {
        try {
          await this.api(`/api/jobs/${job.id}/action`, { method: "PATCH", json: { note: job.action.note } });
        } catch (e) { alert("备注保存失败：" + e.message); }
      },

      /* ── 导出 / 导入 ── */
      exportData(fmt) {
        const f = this.filters;
        const qs = new URLSearchParams({
          format: fmt, order: this.rankingMode,
          ...(f.city ? { city: f.city } : {}),
          ...(f.district ? { district: f.district } : {}),
          ...(f.categories.length ? { category: f.categories.join(",") } : {}),
          ...(f.statuses.length ? { status: f.statuses.join(",") } : {}),
          ...(f.sourceTypes.length ? { source_type: f.sourceTypes.join(",") } : {}),
          ...(f.salaryMin > 0 ? { salary_min: f.salaryMin } : {}),
          ...(f.keepUndisclosed ? {} : { salary_undisclosed: "exclude" }),
        });
        window.open(`/api/export?${qs}`, "_blank");
      },
      async importFile(ev) {
        const file = ev.target.files?.[0];
        if (!file) return;
        const fd = new FormData();
        fd.append("file", file);
        try {
          const r = await this.api("/api/import", { method: "POST", body: fd });
          this.importMsg = `导入完成：新增 ${r.added}，合并 ${r.merged}，拒绝 ${r.rejected}`;
          alert(this.importMsg + (r.errors?.length ? `\n${r.errors.slice(0, 5).join("\n")}` : ""));
          await Promise.all([this.loadJobs(), this.loadStats()]);
          this.renderMarkers();
        } catch (e) { alert("导入失败：" + e.message); }
        ev.target.value = "";
      },

      /* ── 设置 ── */
      openSettings() { this.modal = "settings"; },
      async saveSettings() {
        try {
          await this.api("/api/preferences", {
            method: "PUT",
            json: {
              salary_expect_min: this.draft.min || null,
              salary_expect_max: this.draft.max || null,
              commute_origin_lon: this.draft.lon || null,
              commute_origin_lat: this.draft.lat || null,
              org_type_pref: this.draft.orgType,
              tile_url: this.draft.tileUrl,
              tile_attribution: this.draft.tileAttr,
              tile_crs: this.draft.tileCrs,
              external_searxng_url: this.draft.extUrl || "",
              filter_level: this.draft.filterLevel,
            },
          });
          await this.api("/api/scores/recompute", { method: "POST" });
          await Promise.all([this.loadSession(), this.loadJobs(), this.loadProviders()]);
          this.initMap();
          this.modal = null;
        } catch (e) { alert("保存失败：" + e.message); }
      },
      async openLogs() {
        const d = await this.api("/api/logs?limit=50");
        this.logs = d.logs;
        this.modal = "logs";
      },

      /* ── 地图 ── */
      initMap() {
        const el = document.getElementById("map");
        if (!el || typeof L === "undefined") return;
        if (this._map) { this._map.remove(); this._map = null; }
        this._map = L.map(el, { center: [30.5728, 104.0668], zoom: 11, zoomControl: true });
        const tile = this.session.tile || {};
        this._tileLayer = L.tileLayer(tile.url || "https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: tile.attribution || "OpenStreetMap",
          maxZoom: 19,
        }).addTo(this._map);
        this._markers = {};
        this.renderMarkers();
      },
      renderMarkers() {
        if (!this._map) return;
        if (this._markerLayer) this._markerLayer.remove();
        this._markerLayer = L.layerGroup().addTo(this._map);
        const useGcj = (this.session.tile?.crs) === "gcj02";
        const threshold = PRECISION_RANK[this.mapPrecision] ?? 2;
        this.rankedJobs.forEach((job, idx) => {
          const loc = job.location;
          if (!loc || (PRECISION_RANK[loc.precision] ?? 3) > threshold) return;
          let [lon, lat] = [loc.lon, loc.lat];
          if (useGcj) [lon, lat] = wgs84ToGcj02(lon, lat);
          const type = job.organization.type || "unknown";
          const html = `<div class="mk ${type} ${job.status !== "active" ? "unv" : ""} ${job.id === this.selectedId ? "sel" : ""}">${idx + 1}</div>`;
          const marker = L.marker([lat, lon], {
            icon: L.divIcon({ html, className: "", iconSize: [26, 26], iconAnchor: [13, 26] }),
            zIndexOffset: job.id === this.selectedId ? 1000 : 0,
          });
          marker.bindPopup(`
            <div class="pop-title">${esc(job.organization.name)} · ${esc(job.title)}</div>
            <div>${esc(job.salary_display)} ｜ ${esc(job.city)} ${esc(job.district || "")}（${esc(loc.precision_label)}）</div>
            <div>${esc(job.status_label)} ｜ 综合 ${job._total ?? this.jobTotal(job)} 分</div>
            ${job.sources?.[0] ? `<a href="${esc(job.sources[0].url)}" target="_blank" rel="noopener noreferrer nofollow">查看来源</a>` : ""}
            <div class="pop-actions"><button onclick="window.__jmSelect(${job.id})">在列表中查看</button></div>
          `);
          marker.on("click", () => this.selectJob(job.id, "map"));
          marker.addTo(this._markerLayer);
          this._markers[job.id] = marker;
        });
      },
      flyToJob(job) {
        if (!this._map || !job.location) return;
        let [lon, lat] = [job.location.lon, job.location.lat];
        if ((this.session.tile?.crs) === "gcj02") [lon, lat] = wgs84ToGcj02(lon, lat);
        this._map.flyTo([lat, lon], 14, { duration: 0.6 });
        this.renderMarkers();
        this.$nextTick(() => this._markers[job.id]?.openPopup());
      },

      /* ── 杂项 ── */
      fmtDateTime(iso) {
        if (!iso) return "—";
        const d = new Date(iso);
        if (isNaN(d)) return iso;
        const p = (n) => String(n).padStart(2, "0");
        return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
      },
    },
  }).mount("#app");

  // 地图弹窗按钮 → 选中并滚动到列表卡片（经挂载实例调用，避免依赖内部属性）
  window.__jmSelect = (id) => app.selectJob(id, "map");
})();
