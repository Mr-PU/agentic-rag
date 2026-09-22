const messagesEl = document.getElementById("messages");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const docList = document.getElementById("docList");
const fileInput = document.getElementById("fileInput");
const dropZone = document.getElementById("dropZone");
const uploadStatus = document.getElementById("uploadStatus");

async function loadDocuments() {
  const res = await fetch("/api/documents");
  const docs = await res.json();
  docList.innerHTML = "";
  if (docs.length === 0) {
    docList.innerHTML = '<li style="opacity:.6">No documents yet</li>';
    return;
  }
  for (const d of docs) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = d.filename;
    const right = document.createElement("span");
    right.className = "doc-right";

    const status = document.createElement("span");
    status.className = `status-badge status-${d.status}`;
    status.textContent =
      d.status === "ready" ? `${d.num_chunks} chunks` : d.status;
    if (d.status === "failed" && d.error) status.title = d.error;

    const del = document.createElement("button");
    del.className = "delete-btn";
    del.title = "Delete document";
    del.textContent = "✕";
    del.addEventListener("click", () => deleteDocument(d.id, d.filename));

    right.appendChild(status);
    right.appendChild(del);
    li.appendChild(label);
    li.appendChild(right);
    docList.appendChild(li);
  }

  // Keep polling while anything is still pending/processing
  const stillWorking = docs.some((d) => d.status === "pending" || d.status === "processing");
  if (stillWorking) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(loadDocuments, 2000);
  }
}

let pollTimer = null;

async function deleteDocument(id, filename) {
  if (!confirm(`Remove "${filename}" from the index?`)) return;
  try {
    const res = await fetch(`/api/documents/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error((await res.json()).detail || "Delete failed");
    await loadDocuments();
  } catch (e) {
    alert(`Error deleting document: ${e.message}`);
  }
}

async function uploadFile(file) {
  uploadStatus.textContent = `Uploading ${file.name}...`;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch("/api/ingest", { method: "POST", body: formData });
    if (!res.ok) throw new Error((await res.json()).detail || "Upload failed");
    const data = await res.json();
    uploadStatus.textContent = `${data.filename} queued for processing...`;
    await loadDocuments();
  } catch (e) {
    uploadStatus.textContent = `Error: ${e.message}`;
  }
}

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
});

["dragenter", "dragover"].forEach((evt) =>
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
  })
);
dropZone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function renderAssistantResponse(container, data) {
  container.textContent = data.answer;

  if (data.citations && data.citations.length > 0) {
    const citeDiv = document.createElement("div");
    citeDiv.className = "citations";
    citeDiv.innerHTML =
      "Sources: " +
      data.citations
        .map((c) => `<span class="chip">${c.doc_name} #${c.chunk_index}</span>`)
        .join("");
    container.appendChild(citeDiv);
  }

  const toggle = document.createElement("div");
  toggle.className = "trace-toggle";
  toggle.textContent = `Show agent trace (${data.iterations} iteration${data.iterations === 1 ? "" : "s"})`;
  const traceBody = document.createElement("div");
  traceBody.className = "trace-body";
  traceBody.textContent = JSON.stringify(data.trace, null, 2);
  toggle.addEventListener("click", () => traceBody.classList.toggle("open"));

  container.appendChild(toggle);
  container.appendChild(traceBody);
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = chatInput.value.trim();
  if (!query) return;

  addMessage("user", query);
  chatInput.value = "";
  const loadingEl = addMessage("assistant", "");
  loadingEl.classList.add("loading");
  loadingEl.textContent = "Thinking...";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "Request failed");
    const data = await res.json();
    loadingEl.classList.remove("loading");
    renderAssistantResponse(loadingEl, data);
  } catch (err) {
    loadingEl.classList.remove("loading");
    loadingEl.textContent = `Error: ${err.message}`;
  }
});

loadDocuments();
