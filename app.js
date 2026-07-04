const pesos = new Intl.NumberFormat("es-AR", {
  style: "currency",
  currency: "ARS",
  maximumFractionDigits: 0,
});

const form = document.querySelector("#purchaseForm");
const rowsEl = document.querySelector("#purchaseRows");
const searchEl = document.querySelector("#search");
const warningBox = document.querySelector("#warningBox");
const summaryGrid = document.querySelector("#summaryGrid");
const saveButton = document.querySelector("#saveButton");
const cancelButton = document.querySelector("#cancelButton");
const recordId = document.querySelector("#recordId");

let purchases = [];

function todayISO() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function setPurchaseDateToToday() {
  form.purchase_date.value = todayISO();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "No se pudo completar la accion.");
  return data;
}

function formatDate(value) {
  if (!value) return "";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function periodText(row) {
  return `${formatDate(row.period_start)} al ${formatDate(row.period_end)}`;
}

function formData() {
  return {
    fiscal_year: form.fiscal_year.value,
    purchase_date: todayISO(),
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
  if (check.exceeds_limit) {
    warningBox.innerHTML = `
      Esta persona queda excedida en el limite de compra.<br>
      Total del periodo ${formatDate(check.period_start)} al ${formatDate(check.period_end)}:
      ${pesos.format(check.projected_period_total)}.
    `;
  } else {
    warningBox.innerHTML = `
      Compra dentro del cupo.<br>
      Disponible posterior: ${pesos.format(check.available_after)}.
    `;
  }
}

function hideLimitCheck() {
  warningBox.hidden = true;
  warningBox.textContent = "";
}

function renderRows() {
  if (!purchases.length) {
    rowsEl.innerHTML = `<tr><td colspan="9" class="empty">No hay registros para mostrar.</td></tr>`;
    return;
  }

  rowsEl.innerHTML = purchases
    .map(
      (row) => `
        <tr>
          <td>${row.fiscal_year}</td>
          <td>${formatDate(row.purchase_date)}</td>
          <td>${row.full_name}</td>
          <td>${row.dni}</td>
          <td>${row.email}</td>
          <td>${row.order_number}</td>
          <td class="amount">${pesos.format(row.amount)}</td>
          <td>${periodText(row)}</td>
          <td>
            <div class="row-actions">
              <button class="icon-button" title="Editar" data-edit="${row.id}">&#9998;</button>
              <button class="icon-button" title="Eliminar" data-delete="${row.id}">&times;</button>
            </div>
          </td>
        </tr>
      `
    )
    .join("");
}

async function renderSummaries() {
  const summaries = await api("/api/summaries");
  const top = summaries.slice(0, 4);
  if (!top.length) {
    summaryGrid.innerHTML = "";
    return;
  }
  summaryGrid.innerHTML = top
    .map(
      (item) => `
        <article class="summary-card ${item.exceeds_limit ? "exceeded" : ""}">
          <span>${item.full_name} · ${periodText(item)}</span>
          <strong>${pesos.format(item.total)}</strong>
          <span>${item.exceeds_limit ? "Excedido" : `Disponible ${pesos.format(item.available)}`}</span>
        </article>
      `
    )
    .join("");
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
  setPurchaseDateToToday();
  form.full_name.value = row.full_name;
  form.dni.value = row.dni;
  form.email.value = row.email;
  form.order_number.value = row.order_number;
  form.amount.value = row.amount;
  saveButton.textContent = "Actualizar compra";
  cancelButton.hidden = false;
  hideLimitCheck();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function resetForm() {
  form.reset();
  form.fiscal_year.value = "FY27";
  setPurchaseDateToToday();
  recordId.value = "";
  saveButton.textContent = "Guardar compra";
  cancelButton.hidden = true;
  hideLimitCheck();
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
    await loadPurchases();
  } catch (error) {
    warningBox.hidden = false;
    warningBox.classList.add("danger");
    warningBox.textContent = error.message;
  }
});

cancelButton.addEventListener("click", resetForm);
searchEl.addEventListener("input", () => loadPurchases());

rowsEl.addEventListener("click", async (event) => {
  const editId = event.target.dataset.edit;
  const deleteId = event.target.dataset.delete;
  if (editId) {
    const row = purchases.find((item) => String(item.id) === editId);
    if (row) fillForm(row);
  }
  if (deleteId && confirm("Eliminar este registro?")) {
    await api(`/api/purchases/${deleteId}`, { method: "DELETE" });
    await loadPurchases();
  }
});

api("/api/config").then((config) => {
  document.querySelector("#limitAmount").textContent = pesos.format(config.limit_amount);
});
setPurchaseDateToToday();
loadPurchases();
