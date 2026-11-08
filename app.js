const pesos = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 0 });
const form = document.querySelector("#purchaseForm");
const rowsEl = document.querySelector("#purchaseRows");
const searchEl = document.querySelector("#search");
const warningBox = document.querySelector("#warningBox");
const summaryGrid = document.querySelector("#summaryGrid");
const saveButton = document.querySelector("#saveButton");
const cancelButton = document.querySelector("#cancelButton");
const recordId = document.querySelector("#recordId");
const exportPeriod = document.querySelector("#exportPeriod");
const exportPerson = document.querySelector("#exportPerson");
const exportButton = document.querySelector("#exportButton");
const themeToggle = document.querySelector("#themeToggle");

let purchases = [];

function applyTheme(theme, save = true) {
  const selectedTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = selectedTheme;
  const nextTheme = selectedTheme === "dark" ? "light" : "dark";
  const nextLabel = nextTheme === "light" ? "Claro" : "Oscuro";
  document.querySelector("#themeLabel").textContent = nextLabel;
  document.querySelector("#themeIcon").textContent = nextTheme === "light" ? "☼" : "☾";
  themeToggle.setAttribute("aria-label", `Cambiar a modo ${nextLabel.toLowerCase()}`);
  themeToggle.title = `Cambiar a modo ${nextLabel.toLowerCase()}`;
  themeToggle.setAttribute("aria-pressed", String(selectedTheme === "light"));
  document.querySelector('meta[name="theme-color"]').content = selectedTheme === "dark" ? "#101626" : "#f1f5fa";
  if (save) {
    try { localStorage.setItem("patagonia-theme", selectedTheme); } catch (_) { /* Preferencia limitada por el navegador. */ }
  }
}

themeToggle.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});
applyTheme(document.documentElement.dataset.theme || "dark", false);

function todayISO() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function formatDate(value) {
  if (!value) return "";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function periodText(row) {
  return `${formatDate(row.period_start)} al ${formatDate(row.period_end)}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "No se pudo completar la acción.");
  return data;
}

function formData() {
  return {
    fiscal_year: form.fiscal_year.value,
    purchase_date: form.purchase_date.value,
    full_name: form.full_name.value,
    dni: form.dni.value,
    email: form.email.value,
    order_number: form.order_number.value,
    amount: form.amount.value,
  };
}

function showLimitCheck(check) {
  warningBox.hidden = false;
  warningBox.classList.toggle("danger", check.exceeds_limit);
  warningBox.innerHTML = check.exceeds_limit
    ? `Esta persona supera el cupo de compra.<br>Total del período ${formatDate(check.period_start)} al ${formatDate(check.period_end)}: ${pesos.format(check.projected_period_total)}.`
    : `Compra dentro del cupo.<br>Disponible después de esta compra: ${pesos.format(check.available_after)}.`;
}

function hideLimitCheck() {
  warningBox.hidden = true;
  warningBox.textContent = "";
}

function renderRows() {
  document.querySelector("#recordCount").textContent = `${purchases.length} ${purchases.length === 1 ? "registro" : "registros"}`;
  if (!purchases.length) {
    rowsEl.innerHTML = `<tr><td colspan="9" class="empty">No hay compras para mostrar.</td></tr>`;
    return;
  }
  rowsEl.innerHTML = purchases.map((row) => `
    <tr>
      <td>${row.fiscal_year}</td>
      <td>${formatDate(row.purchase_date)}</td>
      <td>${row.full_name}</td>
      <td>${row.dni}</td>
      <td>${row.email}</td>
      <td>${row.order_number}</td>
      <td class="amount">${pesos.format(row.amount)}</td>
      <td>${periodText(row)}</td>
      <td><div class="row-actions">
        <button class="icon-button" title="Editar" aria-label="Editar compra" data-edit="${row.id}">✎</button>
        <button class="icon-button" title="Eliminar" aria-label="Eliminar compra" data-delete="${row.id}">×</button>
      </div></td>
    </tr>`).join("");
}

async function renderSummaries() {
  const summaries = await api("/api/summaries");
  const top = summaries.slice(0, 4);
  summaryGrid.innerHTML = top.map((item) => `
    <article class="summary-card ${item.exceeds_limit ? "exceeded" : ""}">
      <span>${item.full_name} · ${periodText(item)}</span>
      <strong>${pesos.format(item.total)}</strong>
      <span>${item.exceeds_limit ? "Cupo superado" : `Disponible ${pesos.format(item.available)}`}</span>
    </article>`).join("");
}

async function renderMetrics() {
  const allPurchases = await api("/api/purchases");
  const people = new Set(allPurchases.map((row) => row.dni));
  document.querySelector("#metricOrders").textContent = allPurchases.length.toLocaleString("es-AR");
  document.querySelector("#metricPeople").textContent = people.size.toLocaleString("es-AR");
}

async function loadPurchases() {
  const query = searchEl.value.trim();
  purchases = await api(`/api/purchases?search=${encodeURIComponent(query)}`);
  renderRows();
  await renderSummaries();
}

function fillForm(row) {
  recordId.value = row.id;
  form.fiscal_year.value = row.fiscal_year;
  form.purchase_date.value = row.purchase_date;
  form.purchase_date.readOnly = false;
  form.full_name.value = row.full_name;
  form.dni.value = row.dni;
  form.email.value = row.email;
  form.order_number.value = row.order_number;
  form.amount.value = row.amount;
  saveButton.innerHTML = `Actualizar compra <span>→</span>`;
  cancelButton.hidden = false;
  hideLimitCheck();
  document.querySelector("#purchasePanel").scrollIntoView({ behavior: "smooth" });
}

function resetForm() {
  form.reset();
  form.fiscal_year.value = "FY27";
  form.purchase_date.readOnly = true;
  form.purchase_date.value = todayISO();
  recordId.value = "";
  saveButton.innerHTML = `Guardar compra <span>→</span>`;
  cancelButton.hidden = true;
  hideLimitCheck();
}

function addMonthStart(dateValue, months) {
  const date = new Date(`${dateValue}T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + months);
  return date;
}

function populateExportPeriods(anchorValue, currentStartValue) {
  const anchor = new Date(`${anchorValue}T00:00:00Z`);
  const currentStart = new Date(`${currentStartValue}T00:00:00Z`);
  const periods = [];
  for (let start = new Date(anchor); start <= currentStart; start = addMonthStart(start.toISOString().slice(0, 10), 6)) {
    const next = addMonthStart(start.toISOString().slice(0, 10), 6);
    const end = new Date(next.getTime() - 86400000);
    const startISO = start.toISOString().slice(0, 10);
    const endISO = end.toISOString().slice(0, 10);
    periods.push({ value: `${startISO}|${endISO}`, label: `${formatDate(startISO)} al ${formatDate(endISO)}` });
  }
  periods.reverse().forEach((period) => {
    const option = document.createElement("option");
    option.value = period.value;
    option.textContent = period.label;
    exportPeriod.append(option);
  });
}

function populateExportPeople(employees) {
  employees.forEach((employee) => {
    const option = document.createElement("option");
    option.value = employee.dni;
    option.textContent = `${employee.full_name} · DNI ${employee.dni}`;
    exportPerson.append(option);
  });
}

function updateExportLink() {
  const params = new URLSearchParams({ period: exportPeriod.value });
  if (exportPerson.value !== "all") params.set("dni", exportPerson.value);
  exportButton.href = `/api/export.xlsx?${params.toString()}`;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const id = recordId.value;
    const result = id
      ? await api(`/api/purchases/${id}`, { method: "PUT", body: JSON.stringify(formData()) })
      : await api("/api/purchases", { method: "POST", body: JSON.stringify(formData()) });
    resetForm();
    showLimitCheck(result.limit_check);
    await Promise.all([loadPurchases(), renderMetrics()]);
  } catch (error) {
    warningBox.hidden = false;
    warningBox.classList.add("danger");
    warningBox.textContent = error.message;
  }
});

cancelButton.addEventListener("click", resetForm);
searchEl.addEventListener("input", () => loadPurchases());
exportPeriod.addEventListener("change", updateExportLink);
exportPerson.addEventListener("change", updateExportLink);

rowsEl.addEventListener("click", async (event) => {
  const editButton = event.target.closest("button[data-edit]");
  const deleteButton = event.target.closest("button[data-delete]");
  if (editButton) {
    const row = purchases.find((item) => String(item.id) === editButton.dataset.edit);
    if (row) fillForm(row);
  }
  if (deleteButton && confirm("¿Eliminar este registro?")) {
    await api(`/api/purchases/${deleteButton.dataset.delete}`, { method: "DELETE" });
    await Promise.all([loadPurchases(), renderMetrics()]);
  }
});

api("/api/config").then((config) => {
  document.querySelector("#limitAmount").textContent = pesos.format(config.limit_amount);
  document.querySelector("#metricLimit").textContent = pesos.format(config.limit_amount);
  if (config.current_period_start && config.current_period_end) {
    const range = `${formatDate(config.current_period_start)} al ${formatDate(config.current_period_end)}`;
    document.querySelector("#periodText").textContent = range;
    document.querySelector("#metricPeriod").textContent = range;
    populateExportPeriods(config.anchor_date, config.current_period_start);
    populateExportPeople(config.employees || []);
    updateExportLink();
  }
}).catch((error) => console.error("No se pudo cargar la configuración", error));

form.purchase_date.value = todayISO();
loadPurchases();
renderMetrics();
