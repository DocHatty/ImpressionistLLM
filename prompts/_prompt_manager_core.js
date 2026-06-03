// ==================== STATE ====================
let API_BASE = "";
let currentPrompt = null;
let prompts = [];
let allModels = [];
let activeDropdown = null;
let selectedModelForDropdown = {};
let modelParameterInfo = {};
let currentParameters = {};
let openRouterDefaults = {}; // Fresh from OpenRouter API per model
let userParameters = {}; // User's custom saved parameters
let parameterMode = "default"; // "default" = OpenRouter recommended, "user" = custom
let paramsExpanded = false; // Whether params are expanded in default mode
const MIN_REASONING_COMPLETION_TOKENS = 128;

function findModelById(modelId) {
  return allModels.find((m) => m.id === modelId) || null;
}

function modelSupportsParameter(model, key) {
  return window.PromptUtils.modelSupportsParameter(model, key);
}

function modelInputDisplay(modelId) {
  const model = findModelById(modelId);
  return model?.name || (modelId ? `Missing: ${modelId}` : "Select model...");
}

function updateModelSelectorValue(name, modelId) {
  const input = document.getElementById(`${name}Input`);
  const hidden = document.getElementById(`${name}Value`);
  if (input) input.value = modelInputDisplay(modelId);
  if (hidden) hidden.value = modelId;
  selectedModelForDropdown[name] = modelId;
}

let currentModelParamsController = null;

function fetchWithTimeout(url, options = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  
  if (options.signal) {
    if (options.signal.aborted) {
      clearTimeout(timer);
      controller.abort();
    } else {
      options.signal.addEventListener('abort', () => {
        clearTimeout(timer);
        controller.abort();
      });
    }
  }
  
  const merged = { ...options, signal: controller.signal };
  return fetch(url, merged).finally(() => clearTimeout(timer));
}

function escapeHtml(value) {
  return window.PromptUtils.escapeHtml(value);
}

function jsArg(value) {
  return window.PromptUtils.jsArg(value);
}

function isFastModelId(modelId) {
  const id = String(modelId || "");
  return (
    id.includes("mini") ||
    id.includes("flash") ||
    id.includes("haiku") ||
    id.includes("instant")
  );
}

function normalizeModelMetadata(model) {
  const m = model || {};
  return {
    ...m,
    provider: String(m.id || "").split("/")[0],
    isReasoning: (m.supported_parameters || []).includes("reasoning"),
    isFast: isFastModelId(m.id),
    description: m.description || getModelDescription(m.id),
  };
}

function normalizeModels(models) {
  return (models || [])
    .map(normalizeModelMetadata)
    .sort((a, b) => (a.name || a.id || "").localeCompare(b.name || b.id || ""));
}

const getDefaultOutputSettings = (name) => {
  const base = { useEditWindow: false, useChatSession: false };
  if (!name) return { ...base };
  const normalized = name.trim().toLowerCase();
  if (normalized === "association" || normalized === "pirads") {
    return { useEditWindow: true, useChatSession: false };
  }
  if (
    normalized === "differential" ||
    normalized === "staging" ||
    normalized === "walkthrough" ||
    normalized === "whats the deal" ||
    normalized === "whats-the-deal"
  ) {
    return { useEditWindow: false, useChatSession: true };
  }
  return { ...base };
};
let outputSettings = getDefaultOutputSettings(""); // Output behavior settings

// ==================== INITIALIZATION ====================
async function initAPIBase() {
  if (window.location.protocol === "file:") {
    const ports = Array.from({ length: 20 }, (_, i) => 58080 + i);
    for (const port of ports) {
      try {
        const r = await fetchWithTimeout(
          `http://127.0.0.1:${port}/health`,
          {},
          300,
        );
        if (r.ok) {
          API_BASE = `http://127.0.0.1:${port}`;
          return;
        }
      } catch {}
    }
    API_BASE = "http://127.0.0.1:58080";
  } else {
    API_BASE = window.location.origin;
  }
}

async function initializeApp() {
  await initAPIBase();
  await Promise.all([loadPrompts(), loadModels()]);
}

// ==================== API ====================
async function loadPrompts() {
  try {
    const response = await fetch(`${API_BASE}/api/prompts`);
    const result = await response.json();
    if (result.success) {
      prompts = result.data || [];
      renderPromptList();
    }
  } catch (error) {
    showStatus("Error loading prompts", "error");
  }
}

async function loadModels() {
  try {
    const response = await fetchWithTimeout(
      `${API_BASE}/api/models`,
      {},
      10000,
    );
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const result = await response.json();
    if (result.success && result.models) {
      // Sort by name and add metadata
      allModels = normalizeModels(result.models);
    } else {
      allModels = [];
      showStatus("Unable to load model list", "error");
    }

    // Background refresh to keep the list current (won't block UI)
    fetchWithTimeout(`${API_BASE}/api/models?refresh=1`, {}, 12000)
      .then((r) => (r.ok ? r.json() : null))
      .then((fresh) => {
        if (fresh?.success && fresh.models) {
          allModels = normalizeModels(fresh.models);
        }
      })
      .catch(() => {});
  } catch (error) {
    allModels = [];
    showStatus(
      `Error loading models: ${error.message || "request failed"}`,
      "error",
    );
  }
}

function getModelDescription(modelId) {
  const descriptions = {
    "openai/gpt-5.5":
      "GPT-5.4 default model for general reasoning and prompt workflows.",
    "openai/gpt-4o":
      "Most capable GPT-4 model. Great for complex reasoning and analysis.",
    "openai/gpt-4o-mini":
      "Fast and cost-effective. Good for rule analysis and simple tasks.",
    "openai/gpt-4-turbo": "Previous generation GPT-4 with vision capabilities.",
    "anthropic/claude-3.5-sonnet":
      "Excellent for nuanced analysis and creative tasks.",
    "anthropic/claude-3-haiku":
      "Very fast Claude model. Great for quick evaluations.",
    "anthropic/claude-3-opus":
      "Most capable Claude model. Best for complex tasks.",
    "google/gemini-flash-1.5":
      "Extremely fast. Good for high-volume rule evaluation.",
    "google/gemini-pro-1.5": "Advanced reasoning with large context window.",
    "meta-llama/llama-3.1-70b-instruct":
      "Open-source model with strong capabilities.",
    "mistralai/mistral-large":
      "European AI model with strong multilingual support.",
  };
  return (
    descriptions[modelId] ||
    "AI language model for text generation and analysis."
  );
}

async function loadPrompt(name) {
  try {
    const response = await fetch(
      `${API_BASE}/api/prompt/${encodeURIComponent(name)}`,
    );
    const result = await response.json();
    if (result.success) {
      currentPrompt = result.data;
      if (!currentPrompt.examples) currentPrompt.examples = [];
      // Load output settings
      const defaultOutputSettings = getDefaultOutputSettings(
        currentPrompt.name,
      );
      outputSettings = {
        ...defaultOutputSettings,
        ...(currentPrompt.output_settings || {}),
      };
      renderEditor();
      renderPromptList();
    }
  } catch (error) {
    showStatus("Error loading prompt", "error");
  }
}

function getSupportedParameterKeys() {
  return new Set(Object.keys(modelParameterInfo || {}));
}

function sanitizeParameterValueForInfo(key, value, info) {
  if (!info) return value;
  if (info.type === "integer" || info.type === "float") {
    const defaultNumber = Number.isFinite(Number(info.default))
      ? Number(info.default)
      : Number.isFinite(Number(info.min))
        ? Number(info.min)
        : 0;
    let numVal = Number(value);
    if (!Number.isFinite(numVal)) numVal = defaultNumber;
    if (info.min !== undefined) numVal = Math.max(Number(info.min), numVal);
    if (info.max !== undefined) numVal = Math.min(Number(info.max), numVal);
    if (info.type === "integer") numVal = Math.round(numVal);
    return numVal;
  }
  if (key === "reasoning") {
    return value && typeof value === "object" ? value : undefined;
  }
  if (info.type === "string" && Array.isArray(info.options)) {
    const stringVal = String(value ?? info.default ?? "");
    return info.options.includes(stringVal) ? stringVal : info.default;
  }
  return value;
}

function normalizeUserParametersForCurrentModel(params) {
  const supported = getSupportedParameterKeys();
  const cleaned = {};
  for (const [key, value] of Object.entries(params || {})) {
    if (!supported.has(key)) continue;
    const normalized = sanitizeParameterValueForInfo(
      key,
      value,
      modelParameterInfo[key] || {},
    );
    if (normalized !== undefined) cleaned[key] = normalized;
  }
  return cleaned;
}

function reasoningIsEnabled() {
  const cfg = currentParameters?.reasoning;
  return !!(cfg && typeof cfg === "object" && cfg.enabled);
}

function currentSelectedModelId() {
  return (
    document.getElementById("promptModelValue")?.value ||
    currentPrompt?.model ||
    ""
  );
}

function reasoningCompletionFloor() {
  const cfg = currentParameters?.reasoning || {};
  const effort = String(cfg.effort || "medium").toLowerCase();
  const effortFloor = {
    none: 16,
    minimal: 128,
    low: 256,
    medium: 512,
    high: 1024,
    xhigh: 2048,
    max: 4096,
  };
  let floor = effortFloor[effort] || MIN_REASONING_COMPLETION_TOKENS;
  const reasoningMax = Number(cfg.max_tokens);
  if (Number.isFinite(reasoningMax) && reasoningMax > 0) {
    floor = Math.max(floor, reasoningMax + 128);
  }
  const modelId = currentSelectedModelId().toLowerCase();
  if (modelId.startsWith("anthropic/")) {
    floor = Math.max(floor, 1536);
    if (effort === "xhigh") floor = Math.max(floor, 4096);
  }
  return Math.max(MIN_REASONING_COMPLETION_TOKENS, floor);
}

function enforceReasoningCompletionBudget() {
  if (!reasoningIsEnabled()) return;
  const floor = reasoningCompletionFloor();
  ["max_completion_tokens", "max_tokens"].forEach((key) => {
    if (!Object.prototype.hasOwnProperty.call(modelParameterInfo, key)) return;
    const value = Number(currentParameters[key]);
    if (Number.isFinite(value) && value > 0 && value < floor) {
      currentParameters[key] = floor;
      if (parameterMode === "user") userParameters[key] = floor;
    }
  });
}

function enforceReasoningCapableCompletionBudget() {
  if (reasoningIsEnabled()) return;
  if (!Object.prototype.hasOwnProperty.call(modelParameterInfo, "reasoning"))
    return;
  ["max_completion_tokens", "max_tokens"].forEach((key) => {
    if (!Object.prototype.hasOwnProperty.call(modelParameterInfo, key)) return;
    const value = Number(currentParameters[key]);
    if (Number.isFinite(value) && value > 0 && value < 64) {
      currentParameters[key] = 64;
      if (parameterMode === "user") userParameters[key] = 64;
    }
  });
}

async function savePrompt(event) {
  event.preventDefault();
  const name = document.getElementById("promptName").value.trim();
  const model = document.getElementById("promptModelValue").value.trim();
  const content = document.getElementById("promptContent").value.trim();
  if (!name || !model || !content) {
    showStatus("All fields are required", "error");
    return;
  }
  const invalidChars = /[\\\/:*?"<>|]/;
  if (invalidChars.test(name)) {
    showStatus("Prompt name can't contain \\\\ / : * ? \" < > |", "error");
    return;
  }
  if (/[\\. ]$/.test(name)) {
    showStatus("Prompt name can't end with a space or period", "error");
    return;
  }

  // Get output settings from checkboxes
  const useEditWindowCheckbox = document.getElementById("useEditWindow");
  const useChatSessionCheckbox = document.getElementById("useChatSession");
  if (useEditWindowCheckbox) {
    outputSettings.useEditWindow = useEditWindowCheckbox.checked;
  }
  if (useChatSessionCheckbox) {
    outputSettings.useChatSession = useChatSessionCheckbox.checked;
  }

  try {
    // Normalize any in-progress numeric parameter edits before saving so values
    // like an empty top-k field fall back intelligently instead of triggering
    // native browser validation errors.
    document
      .querySelectorAll('#parametersContainer input[type="number"]')
      .forEach((el) => {
        if (el.id && el.id.startsWith("param_input_")) {
          const key = el.id.replace("param_input_", "");
          updateParameterFromInput(key, el.value);
        }
      });

    enforceReasoningCapableCompletionBudget();
    enforceReasoningCompletionBudget();

    const normalizedCurrentUserParameters =
      parameterMode === "user"
        ? normalizeUserParametersForCurrentModel(currentParameters || {})
        : {};
    const effectiveParameterMode =
      parameterMode === "user" &&
      Object.keys(normalizedCurrentUserParameters).length > 0
        ? "user"
        : "default";

    const data = {
      name,
      previous_name: currentPrompt.is_new ? null : currentPrompt.name,
      model,
      content,
      examples: (currentPrompt.examples || []).filter(
        (ex) => ex.input.trim() || ex.output.trim(),
      ),
      parameter_mode: effectiveParameterMode,
      user_parameters:
        effectiveParameterMode === "user" &&
        Object.keys(normalizedCurrentUserParameters).length > 0
          ? normalizedCurrentUserParameters
          : null,
      output_settings: outputSettings,
    };

    const response = await fetch(`${API_BASE}/api/prompt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (result.success) {
      const savedModel = result.model || model;
      currentPrompt = {
        ...(currentPrompt || {}),
        name,
        model: savedModel,
        content,
        parameter_mode: parameterMode,
        user_parameters: parameterMode === "user" ? { ...userParameters } : {},
        output_settings: { ...outputSettings },
      };
      if (savedModel !== model) {
        updateModelSelectorValue("promptModel", savedModel);
      }
      showStatus("Prompt saved!", "success");
      await new Promise((resolve) => setTimeout(resolve, 150));
      await loadPrompts();
      await loadPrompt(name);
    } else {
      const suggestions =
        Array.isArray(result.suggestions) && result.suggestions.length
          ? ` Suggestions: ${result.suggestions.slice(0, 3).join(", ")}`
          : "";
      showStatus(
        "Error: " + (result.error || "Unknown") + suggestions,
        "error",
      );
    }
  } catch (error) {
    showStatus("Error saving prompt", "error");
  }
}

async function deletePrompt() {
  if (!confirm(`Delete "${currentPrompt.name}"?`)) return;
  try {
    const response = await fetch(`${API_BASE}/api/prompt/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: currentPrompt.name }),
    });
    const result = await response.json();
    if (result.success) {
      showStatus("Deleted!", "success");
      currentPrompt = null;
      await loadPrompts();
      renderEditor();
    }
  } catch (error) {
    showStatus("Error deleting", "error");
  }
}

// ==================== PARAMETERS ====================
async function loadModelParameters(modelId) {
  if (!modelId) {
    renderNoParameters();
    return;
  }

  const container = document.getElementById("parametersContainer");
  if (container) {
    container.innerHTML =
      '<div class="loading-params"><span class="spinner"></span> Loading parameters for model...</div>';
  }

  if (currentModelParamsController) {
    currentModelParamsController.abort();
  }
  currentModelParamsController = new AbortController();

  try {
    const response = await fetchWithTimeout(
      `${API_BASE}/api/model-defaults/${encodeURIComponent(modelId)}`,
      { signal: currentModelParamsController.signal },
      10000
    );
    const result = await response.json();

    if (result.success && result.data) {
      const resolvedModelId = result.data.model_id || modelId;
      if (resolvedModelId !== modelId) {
        updateModelSelectorValue("promptModel", resolvedModelId);
        if (currentPrompt) currentPrompt.model = resolvedModelId;
        showStatus(`Model normalized to ${resolvedModelId}`, "success");
      }

      modelParameterInfo = result.data.parameter_info || {};
      openRouterDefaults = {};

      // Always fetch fresh defaults from OpenRouter API
      for (const [param, info] of Object.entries(modelParameterInfo)) {
        if (info.default !== null && info.default !== undefined) {
          openRouterDefaults[param] = info.default;
        }
      }

      // Load saved user parameters and mode from prompt
      if (currentPrompt) {
        userParameters = normalizeUserParametersForCurrentModel(
          currentPrompt.user_parameters || {},
        );
        parameterMode = currentPrompt.parameter_mode || "default";
      } else {
        userParameters = {};
        parameterMode = "default";
      }

      // Remove stale reasoning settings when the selected model does not support reasoning.
      if (
        !Object.prototype.hasOwnProperty.call(modelParameterInfo, "reasoning")
      ) {
        delete userParameters.reasoning;
      }

      // Drop any unsupported values from the working set too, so the UI cannot
      // continue offering stale reasoning controls from a previous model selection.
      currentParameters = normalizeUserParametersForCurrentModel(
        currentParameters || {},
      );

      // Set current parameters based on mode
      if (parameterMode === "user" && Object.keys(userParameters).length > 0) {
        // User mode: use saved custom parameters
        currentParameters = normalizeUserParametersForCurrentModel({
          ...openRouterDefaults,
          ...userParameters,
        });
      } else {
        // Default mode: always use fresh OpenRouter defaults
        currentParameters = { ...openRouterDefaults };
        parameterMode = "default";
      }

      renderParametersSection();
    } else {
      renderModelParameterError(
        result.error || "Model is not available",
        result.suggestions || [],
      );
    }
  } catch (error) {
    if (error.name === 'AbortError') return;
    console.error("Error loading parameters:", error);
    renderModelParameterError("Unable to load model parameters", []);
  }
}

function renderNoParameters() {
  const container = document.getElementById("parametersContainer");
  if (container) {
    container.innerHTML =
      '<div class="no-params">Select a model to see available parameters</div>';
  }
}

function renderModelParameterError(message, suggestions = []) {
  modelParameterInfo = {};
  openRouterDefaults = {};
  currentParameters = {};
  const container = document.getElementById("parametersContainer");
  if (!container) return;
  const suggestionHtml = (suggestions || []).length
    ? `<div class="model-suggestion-list">${suggestions
        .map(
          (id) =>
            `<button type="button" class="model-suggestion-btn" onclick="selectModelFromDropdown('promptModel', ${jsArg(id)})">${escapeHtml(id)}</button>`,
        )
        .join("")}</div>`
    : "";
  container.innerHTML = `
                    <div class="model-warning">
                        <div class="model-warning-title">Model unavailable</div>
                        <div class="model-warning-body">${escapeHtml(message)}</div>
                        ${suggestionHtml}
                    </div>`;
}

function renderParametersSection() {
  const container = document.getElementById("parametersContainer");
  if (!container) return;

  enforceReasoningCapableCompletionBudget();
  enforceReasoningCompletionBudget();

  const params = Object.entries(modelParameterInfo);
  if (params.length === 0) {
    container.innerHTML =
      '<div class="no-params">No adjustable parameters available for this model</div>';
    return;
  }

  // Mode toggle UI
  // Build mode toggle with smart collapse for default mode
  const paramCount = params.length;
  const isDefaultMode = parameterMode === "default";

  let modeToggleHtml = `
                    <div class="parameter-mode-toggle">
                        <div class="mode-toggle-header">
                            <span class="mode-label">Parameter Mode:</span>
                            <div class="mode-buttons">
                                <button type="button" class="mode-btn ${isDefaultMode ? "active" : ""}" onclick="setParameterMode('default')">
                                    OpenRouter Defaults
                                </button>
                                <button type="button" class="mode-btn ${parameterMode === "user" ? "active" : ""}" onclick="setParameterMode('user')">
                                    Custom (User)
                                </button>
                            </div>
                        </div>
                        <p class="mode-description">
                            ${
                              isDefaultMode
                                ? "Using OpenRouter recommended defaults for this model. Parameters refresh from API each time."
                                : "Using your custom parameters. These are saved with the prompt and persist until you switch back to defaults."
                            }
                        </p>`;

  // In default mode, show a summary and collapsible toggle
  if (isDefaultMode) {
    modeToggleHtml += `
                        <div class="default-mode-summary">
                            <span class="check-icon">✓</span>
                            <div class="summary-text">
                                <div class="summary-title">Optimal Settings Applied</div>
                                <div class="summary-desc">${paramCount} parameters auto-configured by OpenRouter for best results with this model.</div>
                            </div>
                        </div>
                        <div class="params-peek-toggle ${paramsExpanded ? "expanded" : ""}" onclick="toggleParamsExpanded()">
                            <span class="toggle-icon">${paramsExpanded ? "▲" : "▼"}</span>
                            <span>${paramsExpanded ? "Hide parameters" : "View parameters (read-only)"}</span>
                        </div>`;
  }

  modeToggleHtml += `</div>`;

  // Group parameters by category
  const categories = {};
  const hasUnifiedReasoning = params.some(([key]) => key === "reasoning");
  params.forEach(([key, info]) => {
    const cat = info.category || "other";
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push({ key, ...info });
  });

  const categoryOrder = [
    "reasoning",
    "creativity",
    "output",
    "repetition",
    "reproducibility",
    "advanced",
    "other",
  ];
  const categoryLabels = {
    reasoning: "Reasoning & Thinking",
    creativity: "Creativity & Randomness",
    output: "Output Control",
    repetition: "Repetition Control",
    reproducibility: "Reproducibility",
    advanced: "Advanced",
    other: "Other",
  };

  let paramsHtml = "";
  categoryOrder.forEach((cat) => {
    if (categories[cat] && categories[cat].length > 0) {
      paramsHtml += `<div class="parameter-category">
                            <div class="parameter-category-header">${categoryLabels[cat] || cat}</div>
                            <div class="parameter-grid">`;

      categories[cat].forEach((param) => {
        paramsHtml += renderParameterCard(param);
      });

      paramsHtml += `</div></div>`;
    }
  });

  // Wrap params in collapsible container for default mode
  if (isDefaultMode) {
    const collapseClass = paramsExpanded ? "expanded" : "collapsed";
    paramsHtml = `<div class="parameters-collapsible ${collapseClass}">${paramsHtml}</div>`;
  }

  container.innerHTML =
    modeToggleHtml +
    (paramsHtml ||
      '<div class="no-params">No adjustable parameters available</div>');
}

function toggleParamsExpanded() {
  paramsExpanded = !paramsExpanded;
  renderParametersSection();
}

function setParameterMode(mode) {
  parameterMode = mode;

  if (mode === "default") {
    // Switch to OpenRouter defaults (fresh from API)
    currentParameters = { ...openRouterDefaults };
    paramsExpanded = false; // Collapse when switching to default
    showStatus("Switched to OpenRouter recommended defaults", "success");
  } else {
    // Switch to user mode - always show params when customizing
    paramsExpanded = true;
    if (Object.keys(userParameters).length > 0) {
      // Use saved user params
      currentParameters = {
        ...openRouterDefaults,
        ...userParameters,
      };
      showStatus("Switched to your custom parameters", "success");
    } else {
      // No saved user params - start fresh, any changes will be saved
      showStatus(
        "Custom mode enabled. Your changes will be saved with this prompt.",
        "success",
      );
    }
  }

  // Update prompt
  if (currentPrompt) {
    currentPrompt.parameter_mode = mode;
  }

  renderParametersSection();
}

function renderParameterCard(param) {
  const {
    key,
    name,
    description,
    type,
    min,
    max,
    step,
    default: defaultVal,
  } = param;
  const currentVal = currentParameters[key] ?? defaultVal;
  const keyArg = jsArg(key);
  const safeName = escapeHtml(name);
  const safeDescription = escapeHtml(description);
  const isModified =
    currentParameters[key] !== undefined &&
    currentParameters[key] !== defaultVal;

  if (type === "reasoning_config") {
    const cfg = getReasoningConfigValue(key, defaultVal);
    const cfgDefault = defaultVal || {};
    const cfgModified = JSON.stringify(cfg) !== JSON.stringify(cfgDefault);
    const effortOptions = ["none", "minimal", "low", "medium", "high", "xhigh"];
    const effortButtons = effortOptions
      .map(
        (opt) =>
          `<button type="button" class="reasoning-effort-btn ${cfg.effort === opt ? "active" : ""}"
                                    onclick="updateReasoningField(${keyArg}, 'effort', '${opt}')">${escapeHtml(opt)}</button>`,
      )
      .join("");
    const strategyClass = cfg.enabled ? "" : "reasoning-disabled";
    const maxTokensVal = cfg.max_tokens ?? "";

    return `
                        <div class="parameter-card reasoning-config-card ${cfgModified ? "modified" : ""}" id="param_card_${key}">
                            <div class="parameter-card-header">
                                <span class="parameter-name" id="param_label_${key}">${safeName}</span>
                                <span class="parameter-value-display" id="param_display_${key}">${escapeHtml(cfg.enabled ? (cfg.max_tokens ? `reasoning max:${cfg.max_tokens}${cfg.exclude ? " | hidden" : " | shown"}` : `reasoning ${cfg.effort || "medium"}${cfg.exclude ? " | hidden" : " | shown"}`) : "off")}</span>
                            </div>
                            <div class="parameter-description" id="param_desc_${key}">${safeDescription}</div>
                            <div class="reasoning-config-grid">
                                <div class="reasoning-row">
                                    <label for="reasoning_enabled_${key}">Enable reasoning</label>
                                    <select id="reasoning_enabled_${key}" class="parameter-input parameter-input-auto"
                                            onchange="updateReasoningEnabled(${keyArg}, this.value === 'true')">
                                        <option value="false" ${!cfg.enabled ? "selected" : ""}>Off</option>
                                        <option value="true" ${cfg.enabled ? "selected" : ""}>On</option>
                                    </select>
                                </div>
                                <div class="${strategyClass}">
                                    <div class="reasoning-row">
                                        <label for="reasoning_max_tokens_${key}">Effort level</label>
                                        <button type="button" class="parameter-reset-btn" onclick="setReasoningStrategy(${keyArg}, 'effort')" title="Use effort mode">Use effort</button>
                                    </div>
                                    <div class="reasoning-effort-grid">${effortButtons}</div>
                                    <div class="reasoning-row reasoning-row-spaced">
                                        <label for="reasoning_max_tokens_${key}">Max reasoning tokens</label>
                                        <input id="reasoning_max_tokens_${key}" type="number" min="1" step="1" class="parameter-input"
                                               value="${escapeHtml(maxTokensVal)}" placeholder="optional"
                                               onchange="updateReasoningMaxTokens(${keyArg}, this.value)">
                                    </div>
                                    <div class="reasoning-row">
                                        <label for="reasoning_exclude_${key}">Exclude reasoning from response</label>
                                        <select id="reasoning_exclude_${key}" class="parameter-input parameter-input-auto"
                                                onchange="updateReasoningField(${keyArg}, 'exclude', this.value === 'true')">
                                            <option value="false" ${!cfg.exclude ? "selected" : ""}>No</option>
                                            <option value="true" ${cfg.exclude ? "selected" : ""}>Yes</option>
                                        </select>
                                    </div>
                                </div>
                                <div class="reasoning-help">Reasoning tokens are billed as output tokens. Use either effort OR max token budget, not both. Completion caps are raised while reasoning is enabled so the model has room to answer.</div>
                            </div>
                            <div class="parameter-reset-row"><button type="button" class="parameter-reset-btn" onclick="resetReasoningParameter(${keyArg})" title="Reset to default">Reset</button></div>
                        </div>`;
  }

  // Skip non-adjustable types
  if (type === "array" || type === "object" || type === "unknown") {
    return "";
  }

  let controlHtml = "";
  if (type === "float" || type === "integer") {
    const sliderMin = min ?? 0;
    const sliderMax = max ?? (type === "integer" ? 1000 : 2);
    const sliderStep = step ?? (type === "integer" ? 1 : 0.1);
    const numberInputStep = type === "integer" ? 1 : sliderStep;
    const rawDisplayVal = currentVal ?? defaultVal ?? sliderMin;
    let displayVal = Number.isFinite(Number(rawDisplayVal))
      ? Number(rawDisplayVal)
      : Number(sliderMin);
    if (min !== undefined) displayVal = Math.max(Number(min), displayVal);
    if (max !== undefined) displayVal = Math.min(Number(max), displayVal);

    controlHtml = `
                        <div class="parameter-slider-container">
                            <input type="range" class="parameter-slider"
                                   id="param_slider_${key}"
                                   min="${sliderMin}" max="${sliderMax}" step="${sliderStep}"
                                   value="${displayVal}"
                                   aria-labelledby="param_label_${key}"
                                   aria-describedby="param_desc_${key}"
                                   oninput="updateParameterFromSlider(${keyArg}, this.value)">
                            <input type="number" class="parameter-input"
                                   id="param_input_${key}"
                                   min="${sliderMin}" max="${sliderMax}" step="${numberInputStep}"
                                   value="${displayVal}"
                                   aria-labelledby="param_label_${key}"
                                   aria-describedby="param_desc_${key}"
                                   onchange="updateParameterFromInput(${keyArg}, this.value)">
                            <button type="button" class="parameter-reset-btn" onclick="resetParameter(${keyArg})" title="Reset to default">Reset</button>
                        </div>`;
  } else if (type === "boolean") {
    controlHtml = `
                        <div class="parameter-slider-container">
                            <select class="parameter-input parameter-input-auto"
                                    aria-labelledby="param_label_${key}"
                                    aria-describedby="param_desc_${key}"
                                    onchange="updateParameter(${keyArg}, this.value === 'true')">
                                <option value="false" ${!currentVal ? "selected" : ""}>False</option>
                                <option value="true" ${currentVal ? "selected" : ""}>True</option>
                            </select>
                            <button type="button" class="parameter-reset-btn" onclick="resetParameter(${keyArg})" title="Reset to default">Reset</button>
                        </div>`;
  } else if (
    type === "string" &&
    Array.isArray(param.options) &&
    param.options.length
  ) {
    const optionsHtml = param.options
      .map(
        (opt) =>
          `<option value="${escapeHtml(opt)}" ${String(currentVal) === String(opt) ? "selected" : ""}>${escapeHtml(opt)}</option>`,
      )
      .join("");
    controlHtml = `
                        <div class="parameter-slider-container">
                            <select class="parameter-input parameter-input-auto"
                                    aria-labelledby="param_label_${key}"
                                    aria-describedby="param_desc_${key}"
                                    onchange="updateParameter(${keyArg}, this.value)">
                                ${optionsHtml}
                            </select>
                            <button type="button" class="parameter-reset-btn" onclick="resetParameter(${keyArg})" title="Reset to default">Reset</button>
                        </div>`;
  } else {
    return ""; // Skip string/unknown types for now
  }

  return `
                    <div class="parameter-card ${isModified ? "modified" : ""}" id="param_card_${key}">
                        <div class="parameter-card-header">
                            <span class="parameter-name" id="param_label_${key}">${safeName}</span>
                            <span class="parameter-value-display" id="param_display_${key}">${escapeHtml(formatParameterValue(currentVal))}</span>
                        </div>
                        <div class="parameter-description" id="param_desc_${key}">${safeDescription}</div>
                        ${controlHtml}
                    </div>`;
}

function formatParameterValue(val) {
  if (val === null || val === undefined) return "default";
  if (typeof val === "number") {
    return Number.isInteger(val) ? val : val.toFixed(2);
  }
  return String(val);
}

function updateParameterFromSlider(key, value) {
  const info = modelParameterInfo[key];
  const numVal = info.type === "integer" ? parseInt(value) : parseFloat(value);
  updateParameter(key, numVal);

  const input = document.getElementById(`param_input_${key}`);
  if (input) input.value = numVal;
}

function updateParameterFromInput(key, value) {
  const info = modelParameterInfo[key] || {};
  const defaultVal = openRouterDefaults[key];
  const defaultNumber = Number.isFinite(Number(defaultVal))
    ? Number(defaultVal)
    : Number.isFinite(Number(info.min))
      ? Number(info.min)
      : 0;

  let numVal;
  if (value === "" || value === null || value === undefined) {
    numVal = defaultNumber;
  } else {
    numVal = info.type === "integer" ? parseInt(value, 10) : parseFloat(value);
    if (!Number.isFinite(numVal)) numVal = defaultNumber;
  }

  if (info.min !== undefined) numVal = Math.max(info.min, numVal);
  if (info.max !== undefined) numVal = Math.min(info.max, numVal);

  updateParameter(key, numVal);

  const slider = document.getElementById(`param_slider_${key}`);
  const input = document.getElementById(`param_input_${key}`);
  if (slider) slider.value = numVal;
  if (input) input.value = numVal;
}

function updateParameter(key, value) {
  currentParameters[key] = value;

  // Update display
  const display = document.getElementById(`param_display_${key}`);
  if (display) display.textContent = formatParameterValue(value);

  // Update card modified state
  const card = document.getElementById(`param_card_${key}`);
  if (card) {
    const isModified = value !== openRouterDefaults[key];
    card.classList.toggle("modified", isModified);
  }

  const isModified = value !== openRouterDefaults[key];
  if (isModified && parameterMode !== "user") {
    parameterMode = "user";
    paramsExpanded = true;
  }

  // Track as a user parameter whenever customization is active.
  if (parameterMode === "user") {
    if (isModified) {
      userParameters[key] = value;
    } else {
      delete userParameters[key]; // Remove if back to default
    }

    // Store in current prompt
    if (currentPrompt) {
      currentPrompt.user_parameters = { ...userParameters };
      currentPrompt.parameter_mode = parameterMode;
    }
  }
}

function resetParameter(key) {
  const defaultVal = openRouterDefaults[key];
  currentParameters[key] = defaultVal;

  // Update UI
  const slider = document.getElementById(`param_slider_${key}`);
  const input = document.getElementById(`param_input_${key}`);
  const display = document.getElementById(`param_display_${key}`);
  const card = document.getElementById(`param_card_${key}`);

  if (slider) slider.value = defaultVal;
  if (input) input.value = defaultVal;
  if (display) display.textContent = formatParameterValue(defaultVal);
  if (card) card.classList.remove("modified");

  // If in user mode, remove from user parameters
  if (parameterMode === "user") {
    delete userParameters[key];
    if (currentPrompt) {
      currentPrompt.user_parameters = { ...userParameters };
    }
  }
}

function getReasoningConfigValue(key, defaultVal) {
  const defaults = {
    enabled: false,
    effort: "medium",
    exclude: false,
  };
  const base =
    currentParameters[key] && typeof currentParameters[key] === "object"
      ? { ...currentParameters[key] }
      : defaultVal && typeof defaultVal === "object"
        ? { ...defaultVal }
        : {};
  const cfg = { ...defaults, ...base };
  if (
    cfg.max_tokens !== undefined &&
    cfg.max_tokens !== null &&
    cfg.max_tokens !== ""
  ) {
    const parsed = parseInt(cfg.max_tokens, 10);
    cfg.max_tokens = Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
  }
  if (!cfg.effort) cfg.effort = "medium";
  cfg.enabled = !!cfg.enabled;
  cfg.exclude = !!cfg.exclude;
  return cfg;
}

function updateReasoningParameterState(key, cfg) {
  currentParameters[key] = cfg;
  const display = document.getElementById(`param_display_${key}`);
  if (display) {
    display.textContent = cfg.enabled
      ? cfg.max_tokens
        ? `reasoning max:${cfg.max_tokens}${cfg.exclude ? " | hidden" : " | shown"}`
        : `reasoning ${cfg.effort || "medium"}${cfg.exclude ? " | hidden" : " | shown"}`
      : "off";
  }

  const card = document.getElementById(`param_card_${key}`);
  const defaultCfg = modelParameterInfo[key]?.default || {};
  const isModified = JSON.stringify(cfg) !== JSON.stringify(defaultCfg);
  if (card) card.classList.toggle("modified", isModified);

  if (isModified && parameterMode !== "user") {
    parameterMode = "user";
    paramsExpanded = true;
  }

  if (parameterMode === "user") {
    if (isModified) {
      userParameters[key] = cfg;
    } else {
      delete userParameters[key];
    }
    if (currentPrompt) {
      currentPrompt.user_parameters = { ...userParameters };
      currentPrompt.parameter_mode = parameterMode;
    }
  }

  renderParametersSection();
}

function updateReasoningField(key, field, value) {
  const defaultVal = modelParameterInfo[key]?.default || {};
  const cfg = getReasoningConfigValue(key, defaultVal);
  cfg[field] = value;
  if (field === "effort" && value) {
    delete cfg.max_tokens;
  }
  updateReasoningParameterState(key, cfg);
}

function updateReasoningEnabled(key, enabled) {
  const defaultVal = modelParameterInfo[key]?.default || {};
  const cfg = getReasoningConfigValue(key, defaultVal);
  cfg.enabled = !!enabled;
  if (cfg.enabled && !cfg.effort && !cfg.max_tokens) {
    cfg.effort = "medium";
  }
  updateReasoningParameterState(key, cfg);
}

function setReasoningStrategy(key, strategy) {
  const defaultVal = modelParameterInfo[key]?.default || {};
  const cfg = getReasoningConfigValue(key, defaultVal);
  if (strategy === "effort") {
    delete cfg.max_tokens;
    if (!cfg.effort) cfg.effort = "medium";
  }
  updateReasoningParameterState(key, cfg);
}

function updateReasoningMaxTokens(key, value) {
  const defaultVal = modelParameterInfo[key]?.default || {};
  const cfg = getReasoningConfigValue(key, defaultVal);
  const parsed = parseInt(value, 10);
  if (!value || !Number.isFinite(parsed) || parsed <= 0) {
    delete cfg.max_tokens;
  } else {
    cfg.max_tokens = parsed;
    delete cfg.effort;
  }
  updateReasoningParameterState(key, cfg);
}

function resetReasoningParameter(key) {
  const defaultVal = modelParameterInfo[key]?.default || {};
  const cfg = { ...defaultVal };
  updateReasoningParameterState(key, cfg);
}

// ==================== EXAMPLES ====================
function addExample() {
  if (!currentPrompt.examples) currentPrompt.examples = [];
  if (currentPrompt.examples.length >= 10) {
    showStatus("Max 10 examples", "error");
    return;
  }
  currentPrompt.examples.push({ input: "", output: "" });
  renderExamplesSection();
}

function removeExample(index) {
  currentPrompt.examples.splice(index, 1);
  renderExamplesSection();
}
function updateExample(index, field, value) {
  if (currentPrompt.examples[index])
    currentPrompt.examples[index][field] = value;
}

// ==================== MODEL SELECTOR ====================
function createModelSelector(containerId, name, defaultValue = "") {
  const container = document.getElementById(containerId);
  const displayName = modelInputDisplay(defaultValue);

  container.innerHTML = `
        <div class="model-selector-container" data-name="${escapeHtml(name)}">
            <input type="text" class="model-selector-input" id="${escapeHtml(name)}Input" value="${escapeHtml(displayName)}"
                   placeholder="Search models..." autocomplete="off"
                   onfocus="openModelDropdown(${jsArg(name)})" oninput="filterModelDropdown(${jsArg(name)}, this.value)">
            <span class="model-selector-icon">▼</span>
            <input type="hidden" id="${escapeHtml(name)}Value" value="${escapeHtml(defaultValue)}">
                <div class="model-dropdown" id="${escapeHtml(name)}Dropdown">
                    <div class="model-dropdown-list" id="${escapeHtml(name)}List"></div>
                    <div class="model-dropdown-detail" id="${escapeHtml(name)}Detail">
                    <div class="model-detail-placeholder">Hover over a model to see details</div>
                </div>
            </div>
        </div>`;

  selectedModelForDropdown[name] = defaultValue;
}

function openModelDropdown(name) {
  closeAllDropdowns();
  const dropdown = document.getElementById(`${name}Dropdown`);
  dropdown.classList.add("show");
  activeDropdown = name;
  renderModelList(name, "");
}

function closeAllDropdowns() {
  document
    .querySelectorAll(".model-dropdown")
    .forEach((d) => d.classList.remove("show"));
  activeDropdown = null;
}

function filterModelDropdown(name, searchTerm) {
  renderModelList(name, searchTerm);
}

function renderModelList(name, searchTerm = "") {
  const listContainer = document.getElementById(`${name}List`);
  const term = searchTerm.toLowerCase();

  let filtered = allModels;
  if (term) {
    filtered = allModels.filter(
      (m) =>
        m.id.toLowerCase().includes(term) ||
        m.name.toLowerCase().includes(term) ||
        m.provider.toLowerCase().includes(term),
    );
  }

  // Group by provider
  const grouped = {};
  filtered.forEach((m) => {
    const provider = m.provider.charAt(0).toUpperCase() + m.provider.slice(1);
    if (!grouped[provider]) grouped[provider] = [];
    grouped[provider].push(m);
  });

  let html = "";
  for (const [provider, models] of Object.entries(grouped)) {
    html += `<div class="model-group-header">${escapeHtml(provider)}</div>`;
    models.forEach((m) => {
      const badges = [];
      if (m.is_free) badges.push('<span class="model-badge free">Free</span>');
      if (m.isFast) badges.push('<span class="model-badge fast">Fast</span>');
      if (m.isReasoning)
        badges.push('<span class="model-badge reasoning">Reasoning</span>');
      if (m.is_vision)
        badges.push('<span class="model-badge vision">Vision</span>');

      html += `
                <div class="model-option" onclick="selectModelFromDropdown(${jsArg(name)}, ${jsArg(m.id)})"
                     onmouseenter="showModelDetail(${jsArg(name)}, ${jsArg(m.id)})">
                    <div class="model-option-content">
                        <div class="model-option-name">${escapeHtml(m.name)}${badges.join("")}</div>
                        <div class="model-option-id">${escapeHtml(m.id)}</div>
                    </div>
                </div>`;
    });
  }

  if (!html) html = '<div class="model-empty">No models found</div>';
  listContainer.innerHTML = html;
}

function showModelDetail(name, modelId) {
  const detailContainer = document.getElementById(`${name}Detail`);
  const model = allModels.find((m) => m.id === modelId);
  if (!model) return;

  const contextK = model.context_length
    ? Math.round(model.context_length / 1000) + "K"
    : "Unknown";
  const supported = model.supported_parameters || [];
  const supportsReasoning = modelSupportsParameter(model, "reasoning");
  const supportsTokens = modelSupportsParameter(model, "max_completion_tokens")
    ? "max_completion_tokens"
    : modelSupportsParameter(model, "max_tokens")
      ? "max_tokens"
      : "provider default";

  detailContainer.innerHTML = `
        <div class="model-detail-title">${escapeHtml(model.name)}</div>
        <div class="model-detail-id">${escapeHtml(model.id)}</div>
        <div class="model-detail-desc">${escapeHtml(model.description)}</div>
        <div class="model-detail-stats">
            <div class="model-stat"><span class="model-stat-label">Context</span><span class="model-stat-value">${escapeHtml(contextK)} tokens</span></div>
            <div class="model-stat"><span class="model-stat-label">Cost</span><span class="model-stat-value">${model.is_free ? "Free" : "Paid"}</span></div>
            <div class="model-stat"><span class="model-stat-label">Speed</span><span class="model-stat-value">${model.isFast ? "Fast" : "Standard"}</span></div>
            <div class="model-stat"><span class="model-stat-label">Reasoning</span><span class="model-stat-value">${supportsReasoning ? "Supported" : "No"}</span></div>
            <div class="model-stat"><span class="model-stat-label">Token field</span><span class="model-stat-value">${escapeHtml(supportsTokens)}</span></div>
            <div class="model-stat"><span class="model-stat-label">Params</span><span class="model-stat-value">${supported.length || 0}</span></div>
        </div>`;
}

function selectModelFromDropdown(name, modelId) {
  const model = allModels.find((m) => m.id === modelId);
  document.getElementById(`${name}Input`).value = model?.name || modelId;
  document.getElementById(`${name}Value`).value = modelId;
  selectedModelForDropdown[name] = modelId;
  closeAllDropdowns();

  // If this is the main prompt model, reload parameters
  if (name === "promptModel") {
    loadModelParameters(modelId);
  }
}

// ==================== RENDERING ====================
function renderPromptList() {
  const list = document.getElementById("promptList");
  if (prompts.length === 0) {
    list.innerHTML =
      '<li class="prompt-list-placeholder">No prompts found</li>';
    return;
  }
  // Defense-in-depth: ensure case-insensitive sorting regardless of backend behavior
  const sortedPrompts = [...prompts].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
  );
  list.innerHTML = sortedPrompts
    .map(
      (p) => `
        <li class="prompt-item ${currentPrompt?.name === p.name ? "active" : ""}" onclick="loadPrompt(${jsArg(p.name)})">
            ${escapeHtml(p.name)}
        </li>`,
    )
    .join("");
}

function renderEditor() {
  const editor = document.getElementById("editor");
  if (!currentPrompt) {
    editor.innerHTML =
      '<div class="editor-empty"><h2>Welcome to Prompt Manager</h2><p>Select a prompt or create new</p></div>';
    return;
  }

  editor.innerHTML = `
        <h2>${currentPrompt.is_new ? "+ Create New Prompt" : "Edit Prompt"}</h2>
        <form onsubmit="savePrompt(event)" novalidate>
            <div class="form-group">
                <label for="promptName">Prompt Name</label>
                <input type="text" id="promptName" value="${escapeHtml(currentPrompt.name || "")}" placeholder="e.g., impression" required>
            </div>
            <div class="form-group">
                <label for="promptModelInput">Model</label>
                <div id="promptModelContainer"></div>
            </div>
            <div class="form-group">
                <label for="promptContent">Prompt Content</label>
                <textarea id="promptContent" required placeholder="Enter your system prompt...&#10;&#10;Use {clipboard} for user input.">${escapeHtml(currentPrompt.content || "")}</textarea>
                <div class="help-text">Use {clipboard} to insert user's selected text</div>
            </div>

            <!-- Output Settings -->
            <div class="form-group">
                <label for="useEditWindow">Output Behavior</label>
                <div class="output-settings-toggle output-settings-toggle-spaced">
                    <label class="toggle-switch" for="useEditWindow">
                        <input type="checkbox" id="useEditWindow" ${outputSettings.useEditWindow ? "checked" : ""} aria-labelledby="toggle_title_editWindow" aria-describedby="toggle_description_editWindow" onchange="handleOutputModeChange('editWindow')">
                        <span class="toggle-slider"></span>
                    </label>
                    <div class="toggle-label">
                        <span class="toggle-title" id="toggle_title_editWindow">Show Edit Window</span>
                        <span class="toggle-description" id="toggle_description_editWindow">Output appears in an editable window for review before pasting.</span>
                    </div>
                </div>
                <div class="output-settings-toggle">
                    <label class="toggle-switch" for="useChatSession">
                        <input type="checkbox" id="useChatSession" ${outputSettings.useChatSession ? "checked" : ""} aria-labelledby="toggle_title_chatSession" aria-describedby="toggle_description_chatSession" onchange="handleOutputModeChange('chatSession')">
                        <span class="toggle-slider"></span>
                    </label>
                    <div class="toggle-label">
                        <span class="toggle-title" id="toggle_title_chatSession">Interactive Chat Session</span>
                        <span class="toggle-description" id="toggle_description_chatSession">Opens a streaming chat window for multi-turn conversation with the LLM. You can continue the dialogue until you close the window.</span>
                    </div>
                </div>
                <div class="help-text output-settings-help">Default: Output pastes directly to clipboard. Enable one option above to change behavior. Chat Session overrides Edit Window if both are enabled.</div>
            </div>

            <!-- Parameters -->
            <div class="section">
                <div class="section-header"><h3>Model Parameters</h3></div>
                <div id="parametersContainer">
                    <div class="loading-params"><span class="spinner"></span> Loading parameters...</div>
                </div>
                <div class="section-help">
                    Parameters control how the AI generates responses. Changes are saved with the prompt.
                </div>
            </div>

            <!-- Examples -->
            <div class="section">
                <div class="section-header">
                    <h3>Few-Shot Examples</h3>
                    <button type="button" class="btn btn-primary btn-sm" onclick="addExample()">+ Add Example</button>
                </div>
                <div class="section-help examples-help">
                    <strong>What are few-shot examples?</strong> These are input/output pairs that teach the AI how to respond.
                    The AI learns the pattern from your examples and applies it to new inputs.
                    More examples = more consistent outputs matching your desired style.
                </div>
                <div id="examplesContainer" class="examples-list"></div>
            </div>

            <div class="button-group">
                <button type="submit" class="btn btn-primary">Save Prompt</button>
                ${!currentPrompt.is_new ? '<button type="button" class="btn btn-danger" onclick="deletePrompt()">Delete</button>' : ""}
                <button type="button" class="btn btn-secondary" onclick="cancelEdit()">Cancel</button>
            </div>
        </form>`;

  // Initialize model selectors
  createModelSelector(
    "promptModelContainer",
    "promptModel",
    currentPrompt.model || "",
  );
  renderExamplesSection();

  // Load parameters for current model
  if (currentPrompt.model) {
    loadModelParameters(currentPrompt.model);
  } else {
    renderNoParameters();
  }
}

function renderExamplesSection() {
  const container = document.getElementById("examplesContainer");
  if (!container) return;
  const examples = currentPrompt.examples || [];

  if (examples.length === 0) {
    container.innerHTML = `
                        <div class="empty-state examples-empty">
                            <p class="examples-empty-title">No examples added yet. Click "+ Add Example" to get started.</p>
                            <div class="examples-empty-suggestions">
                                <p class="examples-empty-intro">Example ideas for different use cases:</p>
                                <div class="examples-empty-card">
                                    <strong>Medical Report Formatting:</strong>
                                    <div class="examples-empty-line">Input: "patient has mass in right lung 2.3cm..."</div>
                                    <div class="examples-empty-line">Output: "FINDINGS: Right lung mass measuring 2.3 cm..."</div>
                                </div>
                                <div class="examples-empty-card">
                                    <strong>Code Documentation:</strong>
                                    <div class="examples-empty-line">Input: "function calculateTotal(items) { ... }"</div>
                                    <div class="examples-empty-line">Output: "/**\\n * Calculates the total price...\\n */"</div>
                                </div>
                                <div class="examples-empty-card examples-empty-card-final">
                                    <strong>Translation Style:</strong>
                                    <div class="examples-empty-line">Input: "The quick brown fox jumps"</div>
                                    <div class="examples-empty-line">Output: "El rápido zorro marrón salta"</div>
                                </div>
                            </div>
                        </div>`;
    return;
  }

  container.innerHTML = examples
    .map(
      (ex, idx) => `
            <div class="example-card">
                <div class="example-card-header">
                    <span class="example-number">Example ${idx + 1}</span>
                    <button type="button" class="btn btn-danger btn-sm" onclick="removeExample(${idx})">Remove</button>
                </div>
                <div class="example-field">
                    <label for="example-input-${idx}">Input</label>
                    <textarea id="example-input-${idx}" placeholder="The raw input text that would be given to the AI..." oninput="updateExample(${idx}, 'input', this.value)">${escapeHtml(ex.input || "")}</textarea>
                </div>
                <div class="example-field">
                    <label for="example-output-${idx}">Expected Output</label>
                    <textarea id="example-output-${idx}" placeholder="How you want the AI to respond to this input..." oninput="updateExample(${idx}, 'output', this.value)">${escapeHtml(ex.output || "")}</textarea>
                </div>
            </div>`,
    )
    .join("");
}

// ==================== UTILITIES ====================
function createNewPrompt() {
  currentPrompt = {
    name: "",
    model: "openai/gpt-5.5",
    content: "",
    examples: [],
    is_new: true,
  };
  // Reset output settings for new prompt
  const nameInput = document.getElementById("promptName");
  const nextName = nameInput ? nameInput.value : "";
  outputSettings = getDefaultOutputSettings(nextName);
  renderEditor();
  renderPromptList();
}

function cancelEdit() {
  currentPrompt = null;
  renderEditor();
  renderPromptList();
}

// Handle output mode toggle changes - chat session takes priority
function handleOutputModeChange(mode) {
  const editWindowCheckbox = document.getElementById("useEditWindow");
  const chatSessionCheckbox = document.getElementById("useChatSession");

  if (
    mode === "chatSession" &&
    chatSessionCheckbox &&
    chatSessionCheckbox.checked
  ) {
    // If enabling chat session, disable edit window (chat takes priority)
    if (editWindowCheckbox) {
      editWindowCheckbox.checked = false;
      outputSettings.useEditWindow = false;
    }
    outputSettings.useChatSession = true;
  } else if (
    mode === "editWindow" &&
    editWindowCheckbox &&
    editWindowCheckbox.checked
  ) {
    // If enabling edit window, disable chat session
    if (chatSessionCheckbox) {
      chatSessionCheckbox.checked = false;
      outputSettings.useChatSession = false;
    }
    outputSettings.useEditWindow = true;
  } else {
    // Just update the setting that was toggled
    if (mode === "chatSession") {
      outputSettings.useChatSession = chatSessionCheckbox
        ? chatSessionCheckbox.checked
        : false;
    } else if (mode === "editWindow") {
      outputSettings.useEditWindow = editWindowCheckbox
        ? editWindowCheckbox.checked
        : false;
    }
  }
}

function showStatus(message, type) {
  window.PromptUtils.showNotification({
    target: "statusMessage",
    message,
    type,
    baseClass: "status-message",
    duration: 4000,
  });
}

// ==================== EVENT LISTENERS ====================
document.addEventListener("click", function (event) {
  if (activeDropdown && !event.target.closest(".model-selector-container")) {
    closeAllDropdowns();
  }
});

document.addEventListener("keydown", function (event) {
  if (event.key === "Escape") {
    closeAllDropdowns();
  }
});

document
  .getElementById("newPromptBtn")
  .addEventListener("click", createNewPrompt);

// ==================== INIT ====================
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeApp);
} else {
  initializeApp();
}
