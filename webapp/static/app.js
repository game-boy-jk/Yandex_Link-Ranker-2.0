const uploadForm = document.querySelector("#uploadForm");
const fileInput = document.querySelector("#fileInput");
const uploadButton = uploadForm.querySelector('button[type="submit"]');
const cancelJobButton = document.querySelector("#cancelJobButton");
const clearFileButton = document.querySelector("#clearFileButton");
const fileTitle = document.querySelector("#fileTitle");
const fileHint = document.querySelector("#fileHint");
const progressBox = document.querySelector("#progressBox");
const progressLabel = document.querySelector("#progressLabel");
const progressValue = document.querySelector("#progressValue");
const progressFill = document.querySelector("#progressFill");
const progressCount = document.querySelector("#progressCount");
const progressLeft = document.querySelector("#progressLeft");
const downloadLink = document.querySelector("#downloadLink");
const requestUnitPrice = document.querySelector("#requestUnitPrice");
const requestPricePer1000 = document.querySelector("#requestPricePer1000");
const dayTariff = document.querySelector("#dayTariff");
const nightTariff = document.querySelector("#nightTariff");
const currentCount = document.querySelector("#currentCount");
const currentCost = document.querySelector("#currentCost");
const daySpendCount = document.querySelector("#daySpendCount");
const nightSpendCount = document.querySelector("#nightSpendCount");
const dailyCount = document.querySelector("#dailyCount");
const dailyLimit = document.querySelector("#dailyLimit");
const quotaRing = document.querySelector("#quotaRing");
const quotaPercent = document.querySelector("#quotaPercent");
const quotaNote = document.querySelector("#quotaNote");
const singleSearchForm = document.querySelector("#singleSearchForm");
const singleQuery = document.querySelector("#singleQuery");
const singleResults = document.querySelector("#singleResults");
const toast = document.querySelector("#toast");
const historyPanel = document.querySelector("#historyPanel");
const historyList = document.querySelector("#historyList");
const openHistory = document.querySelector("#openHistory");
const closeHistory = document.querySelector("#closeHistory");
const clearHistory = document.querySelector("#clearHistory");

let activePoll = null;
let activeJobId = "";
let isBatchRunning = false;
let estimateRequestId = 0;
let lastUsage = null;
let quotaBlockMessage = "";

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) {
    clearSelectedFile({ resetInput: false });
    return;
  }

  uploadForm.classList.add("has-file");
  fileTitle.textContent = file.name;
  clearFileButton.hidden = false;
  resetCurrentEstimate();

  if (!isAllowedFile(file.name)) {
    fileHint.textContent = `${formatBytes(file.size)}. Нужен CSV или XLSX`;
    showToast("Можно загрузить только CSV или XLSX");
    return;
  }

  fileHint.textContent = `${formatBytes(file.size)}. Считаю запросы`;
  uploadButton.disabled = true;
  await estimateSelectedFile(file, ++estimateRequestId);
});

clearFileButton.addEventListener("click", () => {
  if (isBatchRunning) {
    return;
  }

  clearSelectedFile();
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (isBatchRunning) {
    showToast("Обработка уже идёт");
    return;
  }

  const file = fileInput.files[0];
  if (!file) {
    showToast("Сначала выберите CSV или XLSX файл");
    return;
  }

  if (!isAllowedFile(file.name)) {
    showToast("Можно загрузить только CSV или XLSX");
    return;
  }

  if (quotaBlockMessage) {
    showToast(quotaBlockMessage);
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  setProgress({ status: "uploading", done: 0, total: 0, percent: 0 });
  progressBox.hidden = false;
  downloadLink.hidden = true;
  setBatchRunning(true);

  let response;
  let payload;
  try {
    response = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });
    payload = await response.json();
  } catch (error) {
    showToast("Не удалось загрузить файл");
    setProgress({ status: "error", done: 0, total: 0, percent: 0 });
    setBatchRunning(false);
    return;
  }

  if (!response.ok) {
    if (payload.quota) {
      applyQuotaPreview(payload.quota);
    }
    showToast(payload.error || "Не удалось загрузить файл");
    setProgress({ status: "error", done: 0, total: 0, percent: 0 });
    setBatchRunning(false);
    return;
  }

  updateCost(payload.job.cost);
  updateTariff(payload.job.cost);
  currentCount.textContent = formatInteger(payload.job.total);
  setBatchRunning(true, payload.job.id);
  pollJob(payload.job.id);
});

cancelJobButton.addEventListener("click", async () => {
  if (!activeJobId) {
    return;
  }

  cancelJobButton.disabled = true;

  let response;
  let payload;
  try {
    response = await fetch(`/api/jobs/${activeJobId}/cancel`, {
      method: "POST",
    });
    payload = await response.json();
  } catch (error) {
    showToast("Не удалось отменить обработку");
    cancelJobButton.disabled = false;
    return;
  }

  if (!response.ok) {
    showToast(payload.error || "Не удалось отменить обработку");
    cancelJobButton.disabled = false;
    return;
  }

  setProgress(payload.job);

  if (payload.job.status === "canceled") {
    clearInterval(activePoll);
    setBatchRunning(false);
    showToast("Обработка отменена");
    loadHistory();
    return;
  }

  showToast("Останавливаю обработку");
  cancelJobButton.disabled = false;
});

async function estimateSelectedFile(file, requestId) {
  const formData = new FormData();
  formData.append("file", file);

  let response;
  let payload;
  try {
    response = await fetch("/api/estimate", {
      method: "POST",
      body: formData,
    });
    payload = await response.json();
  } catch (error) {
    if (requestId === estimateRequestId) {
      fileHint.textContent = `${formatBytes(file.size)}. Не удалось посчитать`;
      showToast("Не удалось получить оценку стоимости");
    }
    return;
  }

  if (requestId !== estimateRequestId) {
    return;
  }

  if (!response.ok) {
    fileHint.textContent = `${formatBytes(file.size)}. Не удалось посчитать`;
    showToast(payload.error || "Не удалось посчитать запросы в файле");
    return;
  }

  currentCount.textContent = formatInteger(payload.total || 0);
  updateCost(payload.cost);
  updateTariff(payload.tariff || payload.cost);
  updateUsage(payload.usage);
  applyQuotaPreview(payload.quota);

  if (payload.quota && payload.quota.overLimit) {
    quotaBlockMessage = quotaLimitMessage(payload.quota);
    uploadForm.classList.add("limit-blocked");
    uploadButton.disabled = true;
    fileHint.textContent = (
      `${formatBytes(file.size)}. ${quotaBlockMessage}`
    );
    showToast(quotaBlockMessage);
    return;
  }

  quotaBlockMessage = "";
  uploadForm.classList.remove("limit-blocked");
  uploadButton.disabled = false;
  fileHint.textContent = (
    `${formatBytes(file.size)}. Запросов: ${formatInteger(payload.total || 0)}`
  );
}

singleSearchForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const query = singleQuery.value.trim();
  if (!query) {
    showToast("Введите поисковый запрос");
    return;
  }

  singleResults.innerHTML = '<div class="result-item">Ищу ссылки...</div>';

  let response;
  let payload;
  try {
    response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    payload = await response.json();
  } catch (error) {
    showToast("Поиск не удался");
    singleResults.innerHTML = "";
    return;
  }

  if (!response.ok) {
    showToast(payload.error || "Поиск не удался");
    singleResults.innerHTML = "";
    return;
  }

  renderSingleResults(payload.urls);
  updateTariff(payload.tariff || payload.cost);
  updateUsage(payload.usage);
  loadHistory();
});

openHistory.addEventListener("click", () => {
  historyPanel.classList.add("open");
  loadHistory();
});

closeHistory.addEventListener("click", () => {
  historyPanel.classList.remove("open");
});

clearHistory.addEventListener("click", async () => {
  const items = historyList.querySelectorAll(".history-item[data-history-id]");
  if (!items.length) {
    return;
  }

  clearHistory.disabled = true;

  let response;
  try {
    response = await fetch("/api/history", { method: "DELETE" });
  } catch (error) {
    showToast("Не удалось очистить историю");
    clearHistory.disabled = false;
    return;
  }

  clearHistory.disabled = false;

  if (!response.ok) {
    showToast("Не удалось очистить историю");
    return;
  }

  renderHistoryItems([]);
  showToast("История очищена");
});

historyList.addEventListener("click", async (event) => {
  const button = event.target.closest(".history-delete-button");
  if (!button) {
    return;
  }

  const historyItem = button.closest(".history-item");
  const itemId = historyItem?.dataset.historyId;
  if (!itemId) {
    return;
  }

  button.disabled = true;

  let response;
  let payload;
  try {
    response = await fetch(`/api/history/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
    });
    payload = await response.json();
  } catch (error) {
    showToast("Не удалось удалить запись");
    button.disabled = false;
    return;
  }

  if (!response.ok) {
    showToast(payload.error || "Не удалось удалить запись");
    button.disabled = false;
    return;
  }

  renderHistoryItems(payload.items || []);
});

async function pollJob(jobId) {
  clearInterval(activePoll);

  activePoll = setInterval(async () => {
    const response = await fetch(`/api/jobs/${jobId}`);
    const payload = await response.json();

    if (!response.ok) {
      clearInterval(activePoll);
      setBatchRunning(false);
      showToast(payload.error || "Не удалось получить статус");
      return;
    }

    setProgress(payload.job);
    updateUsage(payload.usage);

    if (payload.job.status === "done") {
      clearInterval(activePoll);
      setBatchRunning(false);
      downloadLink.href = payload.job.downloadUrl;
      downloadLink.hidden = false;
      showToast("Файл готов. Можно скачивать результат");
      loadHistory();
    }

    if (payload.job.status === "error") {
      clearInterval(activePoll);
      setBatchRunning(false);
      showToast(payload.job.error || "Обработка завершилась ошибкой");
    }

    if (payload.job.status === "canceled") {
      clearInterval(activePoll);
      setBatchRunning(false);
      showToast("Обработка отменена");
      loadHistory();
    }
  }, 1000);
}

function setBatchRunning(running, jobId = "") {
  isBatchRunning = running;
  activeJobId = running ? jobId || activeJobId : "";
  const hasActiveJob = Boolean(activeJobId);
  uploadButton.disabled = running || Boolean(quotaBlockMessage);
  uploadButton.textContent = running ? "Обработка идёт" : "Запустить обработку";
  cancelJobButton.hidden = !running;
  cancelJobButton.disabled = running && !hasActiveJob;
  fileInput.disabled = running;
  clearFileButton.disabled = running;
  uploadForm.classList.toggle("is-running", running);
}

function setProgress(job) {
  const total = job.total || 0;
  const done = job.done || 0;
  const percent = job.percent || 0;

  progressLabel.textContent = statusText(job.status);
  progressValue.textContent = `${percent}%`;
  progressFill.style.width = `${percent}%`;
  progressCount.textContent = `${done}/${total}`;
  progressLeft.textContent = `осталось: ${Math.max(total - done, 0)}`;
}

function renderSingleResults(urls) {
  if (!urls.length) {
    singleResults.innerHTML = '<div class="result-item">Ничего не нашлось</div>';
    return;
  }

  singleResults.innerHTML = urls
    .map((url) => (
      `<div class="result-item result-link"><a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a></div>`
    ))
    .join("");
}

async function loadHistory() {
  let payload;
  try {
    const response = await fetch("/api/history");
    payload = await response.json();
  } catch (error) {
    historyList.innerHTML = '<div class="history-item">История недоступна</div>';
    return;
  }

  const items = payload.items || [];

  renderHistoryItems(items);
}

function renderHistoryItems(items) {
  if (!items.length) {
    historyList.innerHTML = '<div class="history-item">История пока пустая</div>';
    return;
  }

  historyList.innerHTML = items
    .map((item) => {
      const itemId = item.item_id || "";
      const createdAt = formatHistoryDate(item.created_at || "");
      return (
        `<div class="history-item" data-history-id="${escapeHtml(itemId)}">
        <div class="history-item-text">
          <strong>${escapeHtml(item.query || item.filename || "Запрос")}</strong>
          <span>${escapeHtml(item.kind || "")} · ${escapeHtml(item.status || "")} · ${escapeHtml(createdAt)}</span>
        </div>
        <button
          class="history-delete-button"
          type="button"
          title="Удалить запись"
          aria-label="Удалить запись"
        >
          x
        </button>
      </div>`
      );
    })
    .join("");
}

function formatHistoryDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function loadMetrics() {
  let payload;
  try {
    const response = await fetch("/api/metrics?count=0");
    payload = await response.json();
  } catch (error) {
    showToast("Не удалось загрузить метрики");
    return;
  }

  updateTariff(payload.tariff || payload.cost);
  updateUsage(payload.usage);
}

function updateCost(cost) {
  currentCost.textContent = `${formatMoney(cost.total_price || 0)} ₽`;
}

function updateTariff(tariff) {
  if (!tariff) {
    return;
  }

  const pricePer1000 = tariff.pricePer1000 || tariff.price_per_1000 || 0;
  const isNight = Boolean(tariff.isNight || tariff.tariff_code === "night");

  requestUnitPrice.textContent = `${formatUnitPrice(pricePer1000 / 1000)} ₽`;
  requestPricePer1000.textContent = `${formatMoney(pricePer1000)} ₽`;
  dayTariff.classList.toggle("active", !isNight);
  dayTariff.classList.toggle("inactive", isNight);
  nightTariff.classList.toggle("active", isNight);
  nightTariff.classList.toggle("inactive", !isNight);
}

function resetCurrentEstimate() {
  currentCount.textContent = "0";
  updateCost({ total_price: 0 });
  quotaBlockMessage = "";
  uploadButton.disabled = false;
  uploadForm.classList.remove("limit-blocked");
  if (lastUsage) {
    updateUsage(lastUsage);
  } else {
    updateUsage({
      currentHour: 0,
      today: 0,
      limit: 35000,
      overLimit: false,
      day: { requests: 0 },
      night: { requests: 0 },
    });
  }
}

function clearSelectedFile({ resetInput = true } = {}) {
  estimateRequestId += 1;

  if (resetInput) {
    fileInput.value = "";
  }

  uploadForm.classList.remove("has-file", "limit-blocked");
  fileTitle.textContent = "Файл не выбран";
  fileHint.textContent = "Поддерживаются только .csv и .xlsx";
  clearFileButton.hidden = true;
  progressBox.hidden = true;
  downloadLink.hidden = true;
  setProgress({ status: "queued", done: 0, total: 0, percent: 0 });
  resetCurrentEstimate();
}

function updateUsage(usage) {
  if (!usage) {
    return;
  }

  lastUsage = usage;
  const currentHour = usage.currentHour ?? usage.today ?? 0;
  const limit = usage.limit || 35000;
  const percent = Math.min(Math.round((currentHour / limit) * 100), 100);
  const degrees = Math.min((currentHour / limit) * 360, 360);
  const color = usage.overLimit ? "var(--red)" : "var(--green)";

  dailyCount.textContent = formatInteger(currentHour);
  dailyLimit.textContent = formatInteger(limit);
  updateTariffSpend(usage);
  quotaPercent.textContent = `${percent}%`;
  quotaRing.classList.toggle("danger", Boolean(usage.overLimit));
  quotaRing.style.background = `conic-gradient(${color} ${degrees}deg, #e5e7eb 0deg)`;
  quotaNote.textContent = usage.overLimit ? "Лимит превышен" : "В норме";
  quotaNote.classList.toggle("danger", Boolean(usage.overLimit));
}

function updateTariffSpend(usage) {
  const day = usage.day || {};
  const night = usage.night || {};

  daySpendCount.textContent = formatInteger(day.requests || 0);
  nightSpendCount.textContent = formatInteger(night.requests || 0);
}

function applyQuotaPreview(quota) {
  if (!quota) {
    return;
  }

  const limit = quota.limit || 35000;
  const used = quota.currentHour ?? quota.today ?? 0;
  const total = quota.totalAfterRequest ?? used;
  const percent = Math.min(Math.round((total / limit) * 100), 100);
  const degrees = Math.min((total / limit) * 360, 360);
  const color = quota.overLimit ? "var(--red)" : "var(--green)";

  dailyCount.textContent = formatInteger(used);
  dailyLimit.textContent = formatInteger(limit);
  quotaPercent.textContent = `${percent}%`;
  quotaRing.classList.toggle("danger", Boolean(quota.overLimit));
  quotaRing.style.background = `conic-gradient(${color} ${degrees}deg, #e5e7eb 0deg)`;
  quotaNote.textContent = quota.overLimit ? quotaLimitMessage(quota) : "В норме";
  quotaNote.classList.toggle("danger", Boolean(quota.overLimit));
}

function quotaLimitMessage(quota) {
  const remaining = Math.max(quota.remaining || 0, 0);
  const requested = quota.requested || 0;
  return (
    `Превышен лимит: доступно ${formatInteger(remaining)}, `
    + `в списке ${formatInteger(requested)}`
  );
}

function statusText(status) {
  const values = {
    queued: "В очереди",
    uploading: "Загрузка",
    running: "Идёт обработка",
    canceling: "Отмена",
    canceled: "Отменено",
    done: "Готово",
    error: "Ошибка",
  };

  return values[status] || "Ожидание";
}

function isAllowedFile(filename) {
  const value = filename.toLowerCase();
  return value.endsWith(".csv") || value.endsWith(".xlsx");
}

function formatBytes(value) {
  if (value < 1024) {
    return `${value} Б`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} КБ`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} МБ`;
}

function formatMoney(value) {
  return Number(value).toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
}

function formatUnitPrice(value) {
  return Number(value).toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 5,
  });
}

function formatInteger(value) {
  return Number(value).toLocaleString("ru-RU");
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3500);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

loadMetrics();
loadHistory();
