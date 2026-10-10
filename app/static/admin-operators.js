const form = document.getElementById("create-operator-form");
const message = document.getElementById("operator-form-message");
const passwordPanel = document.getElementById("temporary-password-panel");
const passwordValue = document.getElementById("temporary-password-value");
const tableBody = document.getElementById("operators-table-body");
const emptyState = document.getElementById("operators-empty-state");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "";
  message.classList.remove("form-message-error");

  const submitButton = form.querySelector('button[type="submit"]');
  submitButton.disabled = true;

  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.elements.username.value.trim(),
        email: form.elements.email.value.trim(),
      }),
    });

    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error(`Сервер вернул некорректный ответ (HTTP ${response.status}).`);
    }

    if (!response.ok) {
      const detail = typeof result.detail === "string"
        ? result.detail
        : `Не удалось создать оператора (HTTP ${response.status}).`;
      throw new Error(detail);
    }

    if (
      typeof result.id !== "string"
      || typeof result.username !== "string"
      || typeof result.email !== "string"
      || typeof result.temporary_password !== "string"
      || !result.temporary_password
    ) {
      throw new Error("Сервер вернул неполные данные созданного оператора.");
    }

    const row = document.createElement("tr");
    for (const value of [result.username, result.email || ""]) {
      const cell = document.createElement("td");
      cell.textContent = value || "—";
      row.append(cell);
    }

    const statusCell = document.createElement("td");
    const statusBadge = document.createElement("span");
    statusBadge.className = "operator-status operator-status-enabled";
    statusBadge.textContent = "Активен";
    statusCell.append(statusBadge);
    row.append(statusCell);

    const createdCell = document.createElement("td");
    createdCell.textContent = "—";
    row.append(createdCell);

    tableBody.append(row);
    tableBody.closest("table").hidden = false;
    if (emptyState) {
      emptyState.hidden = true;
    }

    passwordValue.textContent = "";
    passwordValue.textContent = result.temporary_password;
    passwordPanel.hidden = false;
    form.reset();
    message.textContent = `Оператор ${result.username} создан.`;
  } catch (error) {
    message.textContent = error instanceof Error ? error.message : "Не удалось создать оператора.";
    message.classList.add("form-message-error");
  } finally {
    submitButton.disabled = false;
  }
});
