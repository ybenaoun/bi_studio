frappe.pages["bi_intelligent_pipeline"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: __("Générateur IA"),
    single_column: true,
  });
  wrapper.bi_pipeline = new BIIntelligentPipeline(wrapper);
};

frappe.pages["bi_intelligent_pipeline"].on_page_show = function (wrapper) {
  wrapper.bi_pipeline && wrapper.bi_pipeline.refresh();
};

const STEP_LABELS = {
  Extract: "Extraction des données",
  Clean: "Nettoyage ETL",
  "Rename Columns": "Renommage des colonnes",
  Profile: "Profilage du jeu de données",
  "ETL Complete": "ETL terminé",
  "Cohere Prompt": "Génération JSON par l'IA",
  "Validate AI JSON": "Validation du JSON",
  "Build Dashboard": "Construction du tableau de bord",
};

const STATUS_LABELS = {
  Uploaded: "Importé",
  Extracting: "Extraction",
  Cleaning: "Nettoyage",
  Profiling: "Profilage",
  "ETL Complete": "ETL terminé — en attente de vos choix",
  "Waiting AI": "Génération IA en cours",
  "AI Generated": "JSON IA reçu",
  "Dashboard Ready": "Tableau de bord prêt",
  Failed: "Échec",
};

const ANALYSIS_GOALS = [
  { value: "salaires", label: "Analyse des salaires" },
  { value: "congés", label: "Analyse des congés" },
  { value: "ventes", label: "Analyse des ventes" },
  { value: "revenus", label: "Analyse des revenus" },
  { value: "coûts", label: "Analyse des coûts" },
  { value: "effectifs", label: "Analyse des effectifs" },
  { value: "performance", label: "Analyse des performances" },
  { value: "anomalies", label: "Détection d'anomalies" },
  { value: "évolution temporelle", label: "Évolution temporelle" },
  { value: "comparaison par catégorie", label: "Comparaison par catégorie" },
];

class BIIntelligentPipeline {
  constructor(wrapper) {
    this.wrapper = wrapper;
    this.page = wrapper.page;
    this.importName = null;
    this.poll = null;
    this.dashboardData = null;
    this.dashboardV1Renderer = null;
    this.dashboardV1AssetPromise = null;
    $("body").addClass("bi-studio-active");
    this.$root = $('<div class="bi-studio-shell bi-pipeline"></div>').appendTo(
      $(this.wrapper).find(".page-content").empty()
    );
    this.render();
  }

  refresh() {
    if (this.importName) this.startPolling();
  }

  render() {
    this.unmountDashboardV1Renderer();
    this.$root.html(`
      <div class="bi-pipeline-hero">
        <div class="bi-pipeline-hero-text">
          <span class="bi-pipeline-eyebrow">${frappe.utils.icon ? frappe.utils.icon("sparkles", "sm") : ""} Pipeline IA</span>
          <h2>Générez un tableau de bord à partir d'un fichier Excel</h2>
          <p>Importez votre fichier, l'IA profile vos données, vous choisissez vos analyses, et votre dashboard est prêt.</p>
        </div>
        <ol class="bi-pipeline-steps-static">
          <li><span>1</span><strong>Importer</strong><em>Excel .xlsx ou .xls</em></li>
          <li><span>2</span><strong>ETL</strong><em>Nettoyage &amp; profilage</em></li>
          <li><span>3</span><strong>Vos choix</strong><em>KPIs &amp; axes</em></li>
          <li><span>4</span><strong>Génération IA</strong><em>Spécification JSON</em></li>
          <li><span>5</span><strong>Tableau prêt</strong><em>Publication directe</em></li>
        </ol>
      </div>

      <div class="bi-card bi-pipeline-form">
        <div class="bi-card-header">
          <h3 class="bi-card-title">Lancer le pipeline</h3>
          <span class="bi-pipeline-form-hint">Renseignez les informations puis cliquez sur «&nbsp;Lancer l'ETL&nbsp;».</span>
        </div>
        <div class="bi-pipeline-grid">
          <div class="bi-form-row">
            <label>Titre du jeu de données</label>
            <input type="text" class="bi-input" id="bi-pipeline-title" placeholder="Ex: Employés 2026" />
          </div>
          <div class="bi-form-row">
            <label>Feuille Excel (optionnel)</label>
            <input type="text" class="bi-input" id="bi-pipeline-sheet" placeholder="Détection automatique" />
          </div>
          <div class="bi-form-row">
            <label>Ligne d'en-tête (optionnel)</label>
            <input type="number" class="bi-input" id="bi-pipeline-header" min="0" placeholder="Détection automatique" />
          </div>
          <div class="bi-form-row bi-pipeline-file-row">
            <label>Fichier Excel</label>
            <input type="file" class="bi-input" id="bi-pipeline-file" accept=".xlsx,.xls" />
          </div>
        </div>
        <div class="bi-pipeline-cta">
          <button class="btn btn-primary" id="bi-pipeline-launch">Lancer le nettoyage ETL</button>
        </div>
      </div>
      <div id="bi-pipeline-status"></div>
      <div id="bi-pipeline-intent"></div>
      <div id="bi-pipeline-result"></div>
    `);

    this.$root.find("#bi-pipeline-launch").on("click", () => this.launch());
  }

  async launch() {
    const $btn = this.$root.find("#bi-pipeline-launch");
    const fileInput = this.$root.find("#bi-pipeline-file")[0];
    if (!fileInput.files || !fileInput.files.length) {
      frappe.msgprint("Veuillez sélectionner un fichier Excel.");
      return;
    }
    $btn.prop("disabled", true).text("Téléversement en cours...");
    try {
      const fileUrl = await this.uploadFile(fileInput.files[0]);
      const datasetTitle = this.$root.find("#bi-pipeline-title").val() || fileInput.files[0].name;
      const sheet = this.$root.find("#bi-pipeline-sheet").val() || null;
      const header = parseInt(this.$root.find("#bi-pipeline-header").val(), 10) || null;

      const response = await frappe.call({
        method: "bi_studio.api.excel_pipeline.run_excel_to_dashboard_pipeline",
        args: { file_url: fileUrl, dataset_title: datasetTitle, sheet_name: sheet, header_row: header },
      });
      this.importName = response.message.import_name;
      this.$root.find("#bi-pipeline-intent").empty();
      this.startPolling();
    } catch (err) {
      frappe.msgprint({ title: "Erreur", message: String(err.message || err), indicator: "red" });
    } finally {
      $btn.prop("disabled", false).text("Lancer le nettoyage ETL");
    }
  }

  uploadFile(file) {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("is_private", 1);
      fd.append("folder", "Home");
      $.ajax({
        url: "/api/method/upload_file",
        type: "POST",
        data: fd,
        processData: false,
        contentType: false,
        headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
        success: (resp) => resolve(resp.message.file_url),
        error: (xhr) => reject(new Error(xhr.responseText || "Erreur de téléversement")),
      });
    });
  }

  unmountDashboardV1Renderer() {
    if (this.dashboardV1Renderer && typeof this.dashboardV1Renderer.unmount === "function") {
      this.dashboardV1Renderer.unmount();
    }
    this.dashboardV1Renderer = null;
  }

  loadDashboardV1Renderer() {
    if (window.BIStudioDashboardV1Renderer) return Promise.resolve(window.BIStudioDashboardV1Renderer);
    if (this.dashboardV1AssetPromise) return this.dashboardV1AssetPromise;

    const loadScript = (src) => {
      window.__biStudioDashboardV1AssetPromises = window.__biStudioDashboardV1AssetPromises || {};
      if (window.__biStudioDashboardV1AssetPromises[src]) return window.__biStudioDashboardV1AssetPromises[src];
      window.__biStudioDashboardV1AssetPromises[src] = new Promise((resolve, reject) => {
        if (src.includes("dashboard_v1_utils") && window.BIStudioDashboardV1Utils) return resolve();
        if (src.includes("dashboard_v1_renderer") && window.BIStudioDashboardV1Renderer) return resolve();
        const script = document.createElement("script");
        script.src = src;
        script.async = true;
        script.setAttribute("data-bi-dashboard-v1-src", src);
        script.onload = resolve;
        script.onerror = () => reject(new Error(`Impossible de charger ${src}`));
        document.head.appendChild(script);
      });
      return window.__biStudioDashboardV1AssetPromises[src];
    };

    this.dashboardV1AssetPromise = loadScript("/assets/bi_studio/js/dashboard_v1_utils.js")
      .then(() => loadScript("/assets/bi_studio/js/dashboard_v1_renderer.js"))
      .then(() => window.BIStudioDashboardV1Renderer);
    return this.dashboardV1AssetPromise;
  }

  startPolling() {
    if (this.poll) clearInterval(this.poll);
    this.tick();
    this.poll = setInterval(() => this.tick(), 2500);
  }

  async tick() {
    if (!this.importName) return;
    try {
      const resp = await frappe.call({
        method: "bi_studio.api.excel_pipeline.get_pipeline_status",
        args: { import_name: this.importName },
      });
      const data = resp.message;
      this.renderStatus(data);

      if (data.status === "ETL Complete") {
        clearInterval(this.poll);
        this.poll = null;
        this.renderUserIntentForm(data);
      } else if (data.status === "Dashboard Ready" || data.status === "Failed") {
        clearInterval(this.poll);
        this.poll = null;
        if (data.status === "Dashboard Ready" && data.created_dashboard) {
          this.loadDashboard(data.created_dashboard, data);
        }
      }
    } catch (err) {
      console.error(err);
    }
  }

  renderStatus(data) {
    const stepsHtml = (data.logs || [])
      .map((log) => {
        const statusClass =
          log.status === "Success" ? "success" : log.status === "Failed" ? "failed" : "in-progress";
        const label = STEP_LABELS[log.job_type] || log.job_type;
        return `
          <div class="bi-step ${statusClass}">
            <span>${label}</span>
            <span class="bi-step-status">${log.status}</span>
          </div>
        `;
      })
      .join("");

    const errorBlock = data.error_message
      ? `<div class="bi-error">${frappe.utils.escape_html(data.error_message)}</div>`
      : "";

    const retryButton =
      data.status === "Failed" && data.clean_dataset
        ? `<button class="btn btn-secondary" id="bi-pipeline-retry">Réessayer la génération IA</button>`
        : "";

    this.$root.find("#bi-pipeline-status").html(`
      <div class="bi-card">
        <h3>Suivi du pipeline — ${frappe.utils.escape_html(data.dataset_title || data.name)}</h3>
        <p>Statut: <strong>${STATUS_LABELS[data.status] || data.status}</strong></p>
        ${errorBlock}
        <div class="bi-steps">${stepsHtml}</div>
        <div class="bi-actions" style="margin-top:12px;">${retryButton}</div>
      </div>
    `);

    this.$root.find("#bi-pipeline-retry").off("click").on("click", () => this.retryAi());
  }

  // -------------------------------------------------------------------------
  // User intent form (shown after ETL Complete)
  // -------------------------------------------------------------------------

  renderUserIntentForm(data) {
    const columnMetadata = data.column_metadata || [];

    const numericCols = columnMetadata.filter(
      (c) => ["number", "currency", "numeric"].includes(c.type) || ["measure", "currency"].includes(c.semantic_type)
    );
    const categoricalCols = columnMetadata.filter(
      (c) =>
        ["category", "categorical"].includes(c.type) ||
        ["dimension", "attribute"].includes(c.semantic_type)
    );

    const goalsHtml = ANALYSIS_GOALS.map(
      (g) => `
        <label class="bi-checkbox-label">
          <input type="checkbox" class="bi-intent-goal" value="${frappe.utils.escape_html(g.value)}" />
          ${frappe.utils.escape_html(g.label)}
        </label>`
    ).join("");

    const kpiOptions = numericCols.map(
      (c) => `
        <label class="bi-checkbox-label">
          <input type="checkbox" class="bi-intent-kpi" value="${frappe.utils.escape_html(c.name)}" />
          ${frappe.utils.escape_html(c.label || c.name)}
          ${c.semantic_type ? `<em>(${frappe.utils.escape_html(c.semantic_type)})</em>` : ""}
        </label>`
    ).join("") || "<p class='text-muted'>Aucune colonne numérique détectée.</p>";

    const dimOptions = categoricalCols.map(
      (c) => `
        <label class="bi-checkbox-label">
          <input type="checkbox" class="bi-intent-dim" value="${frappe.utils.escape_html(c.name)}" />
          ${frappe.utils.escape_html(c.label || c.name)}
          ${c.unique_count ? `<em>(${c.unique_count} valeurs)</em>` : ""}
        </label>`
    ).join("") || "<p class='text-muted'>Aucune dimension catégorielle détectée.</p>";

    this.$root.find("#bi-pipeline-intent").html(`
      <div class="bi-card bi-intent-form">
        <div class="bi-card-header">
          <h3 class="bi-card-title">Que souhaitez-vous analyser ?</h3>
          <p>L'ETL a détecté <strong>${columnMetadata.length}</strong> colonne(s).
             Précisez vos objectifs pour que l'IA génère le dashboard le plus pertinent.</p>
        </div>

        <div class="bi-intent-section">
          <h4>Objectifs analytiques</h4>
          <p class="text-muted">Cochez les thèmes qui vous intéressent.</p>
          <div class="bi-checkbox-group">${goalsHtml}</div>
        </div>

        <div class="bi-intent-section">
          <h4>KPIs préférés</h4>
          <p class="text-muted">Sélectionnez les métriques à mettre en avant.</p>
          <div class="bi-checkbox-group">${kpiOptions}</div>
        </div>

        <div class="bi-intent-section">
          <h4>Dimensions de comparaison</h4>
          <p class="text-muted">Choisissez les axes de regroupement souhaités.</p>
          <div class="bi-checkbox-group">${dimOptions}</div>
        </div>

        <div class="bi-pipeline-cta">
          <button class="btn btn-primary" id="bi-intent-submit">Générer le dashboard avec l'IA</button>
          <button class="btn btn-secondary" id="bi-intent-skip">Générer sans préférences</button>
        </div>
      </div>
    `);

    this.$root.find("#bi-intent-submit").on("click", () => this.submitUserIntent(false));
    this.$root.find("#bi-intent-skip").on("click", () => this.submitUserIntent(true));
  }

  async submitUserIntent(skipIntent) {
    const $btn = this.$root.find("#bi-intent-submit");
    $btn.prop("disabled", true).text("Génération en cours...");

    let userIntent = {};
    if (!skipIntent) {
      const goals = [];
      this.$root.find(".bi-intent-goal:checked").each(function () {
        goals.push($(this).val());
      });
      const kpis = [];
      this.$root.find(".bi-intent-kpi:checked").each(function () {
        kpis.push($(this).val());
      });
      const dims = [];
      this.$root.find(".bi-intent-dim:checked").each(function () {
        dims.push($(this).val());
      });
      userIntent = {
        analysis_goals: goals,
        preferred_kpis: kpis,
        preferred_dimensions: dims,
        preferred_visualizations: [],
      };
    }

    try {
      await frappe.call({
        method: "bi_studio.api.excel_pipeline.submit_user_intent_and_generate",
        args: {
          import_name: this.importName,
          user_intent_json: JSON.stringify(userIntent),
        },
      });
      this.$root.find("#bi-pipeline-intent").empty();
      this.startPolling();
    } catch (err) {
      frappe.msgprint({ title: "Erreur", message: String(err.message || err), indicator: "red" });
      $btn.prop("disabled", false).text("Générer le dashboard avec l'IA");
    }
  }

  async retryAi() {
    if (!this.importName) return;
    await frappe.call({
      method: "bi_studio.api.excel_pipeline.retry_ai_generation",
      args: { import_name: this.importName },
    });
    this.$root.find("#bi-pipeline-intent").empty();
    this.startPolling();
  }

  async loadDashboard(dashboardName, statusData) {
    const resp = await frappe.call({
      method: "bi_studio.api.dashboard_builder.get_intelligent_dashboard",
      args: { dashboard_name: dashboardName },
    });
    this.dashboardData = resp.message;
    this.renderDashboard(this.dashboardData, statusData);
  }

  renderDashboard(data, statusData) {
    this.unmountDashboardV1Renderer();
    this.$root.find("#bi-pipeline-result").html(`
      <div id="bi-pipeline-dashboard-v1" class="bi-pipeline-dashboard-v1"></div>
    `);

    const mount = this.$root.find("#bi-pipeline-dashboard-v1")[0];
    const actions = [
      { key: "open-dashboard", label: "Ouvrir", variant: "primary" },
      { key: "show-json", label: "Voir JSON" },
      { key: "show-etl", label: "Voir résultat ETL" },
      { key: "retry-ai", label: "Régénérer" },
    ];

    this.loadDashboardV1Renderer()
      .then((renderer) =>
        renderer.mount(mount, data, {
          quality_score: data.quality_score,
          status: STATUS_LABELS[(statusData && statusData.status) || "Dashboard Ready"] || "Tableau prêt",
          actions,
          onAction: (key) => {
            if (key === "open-dashboard") return frappe.set_route("bi_studio", "dashboard", data.name);
            if (key === "show-json") return this.showAiJson(data.ai_spec);
            if (key === "show-etl") return this.showEtlResult(data.clean_dataset, statusData);
            if (key === "retry-ai") return this.retryAi();
          },
        })
      )
      .then((instance) => {
        this.dashboardV1Renderer = instance;
      })
      .catch((error) => {
        this.$root.find("#bi-pipeline-dashboard-v1").html(`
          <div class="bi-card"><div class="bi-error">${frappe.utils.escape_html(error.message || error)}</div></div>
        `);
      });
  }

  async showAiJson(specName) {
    if (!specName) return frappe.msgprint("Aucune spécification IA disponible.");
    const resp = await frappe.call({
      method: "bi_studio.api.dashboard_builder.get_ai_spec_json",
      args: { spec_name: specName },
    });
    const json = JSON.stringify(resp.message.validated_json || resp.message.response_json, null, 2);
    const dialog = new frappe.ui.Dialog({
      title: "JSON généré par l'IA",
      size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "json_html" }],
    });
    dialog.fields_dict.json_html.$wrapper.html(`<pre class="bi-json">${frappe.utils.escape_html(json)}</pre>`);
    dialog.show();
  }

  async showEtlResult(cleanDatasetName, statusData) {
    if (!cleanDatasetName) return;
    const doc = await frappe.db.get_doc("BI Clean Dataset", cleanDatasetName);
    const profile = doc.profile_json ? JSON.parse(doc.profile_json) : {};
    const mapping = doc.column_mapping_json ? JSON.parse(doc.column_mapping_json) : {};
    const labels = doc.column_labels_json ? JSON.parse(doc.column_labels_json) : {};

    const mappingRows = Object.entries(mapping)
      .map(([orig, normalized]) => {
        const col = (profile.columns || []).find((c) => c.name === normalized) || {};
        return `<tr>
          <td>${frappe.utils.escape_html(orig)}</td>
          <td>${frappe.utils.escape_html(normalized)}</td>
          <td>${frappe.utils.escape_html(labels[normalized] || "")}</td>
          <td>${frappe.utils.escape_html(col.detected_type || col.type || "")}</td>
          <td>${frappe.utils.escape_html(col.semantic_type || "")}</td>
          <td>${col.missing_rate !== undefined ? (col.missing_rate * 100).toFixed(1) + "%" : ""}</td>
          <td>${col.nullable !== undefined ? (col.nullable ? "Oui" : "Non") : ""}</td>
        </tr>`;
      })
      .join("");

    const dialog = new frappe.ui.Dialog({
      title: "Résultat ETL",
      size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "etl_html" }],
    });
    dialog.fields_dict.etl_html.$wrapper.html(`
      <h4>Score qualité: ${doc.quality_score}/100</h4>
      <h4>Mapping des colonnes (${Object.keys(mapping).length} colonnes)</h4>
      <table class="bi-data-table bi-mapping-table">
        <thead>
          <tr>
            <th>Ancien nom</th><th>Nouveau nom</th><th>Libellé</th>
            <th>Type détecté</th><th>Type sémantique</th><th>Valeurs manquantes</th><th>Nullable</th>
          </tr>
        </thead>
        <tbody>${mappingRows}</tbody>
      </table>
    `);
    dialog.show();
  }
}
