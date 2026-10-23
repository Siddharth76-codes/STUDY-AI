const byId = (id) => document.getElementById(id);
const toast = byId("toast");
let toastTimer;

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 4200);
}

function renderSubscription(subscription) {
  if (!subscription) return;
  const planLabel = `${subscription.plan} plan`;
  const currentPlanLabel = byId("current-plan-label");
  if (currentPlanLabel) currentPlanLabel.textContent = planLabel;
  const planPill = document.querySelector(".plan-pill");
  if (planPill) planPill.textContent = planLabel;

  for (const [category, label] of [["tutor", "used"], ["practice", "answered"]]) {
    const usage = subscription.usage[category];
    const targets = [
      [`${category}-usage-text`, `${category}-usage-bar`],
      [`rail-${category}-usage`, `rail-${category}-bar`],
    ];
    for (const [textId, barId] of targets) {
      const text = byId(textId);
      const bar = byId(barId);
      const track = bar?.parentElement;
      if (!text || !bar || !track) continue;
      text.textContent = usage.limit === null
        ? `${usage.used} ${label} · unlimited`
        : `${usage.used} / ${usage.limit} ${label}`;
      bar.style.width = usage.limit === null
        ? "0%"
        : `${Math.min(100, Math.round((usage.used / usage.limit) * 100))}%`;
      track.setAttribute("aria-valuemax", String(usage.limit ?? 100));
      track.setAttribute("aria-valuenow", String(
        usage.limit === null ? 0 : Math.min(usage.used, usage.limit),
      ));
      track.setAttribute(
        "aria-valuetext",
        usage.limit === null
          ? `${usage.used} ${label}; unlimited`
          : `${usage.used} of ${usage.limit} ${label}`,
      );
    }
  }
}

function closePaymentPanel() {
  const paymentPanel = byId("payment-panel");
  if (paymentPanel) paymentPanel.classList.add("hidden");
}

function openPlanPayment(plan) {
  const paymentPanel = byId("payment-panel");
  const selectedPlanName = byId("selected-plan-name");
  const selectedPlanPrice = byId("selected-plan-price");
  const selectedPlanChip = byId("selected-plan-chip");
  if (!paymentPanel || !selectedPlanName || !selectedPlanPrice || !selectedPlanChip) return;

  const details = {
    FREE: { amount: "₹0", label: "FREE" },
    GO: { amount: "₹100", label: "GO" },
    PRO: { amount: "₹150", label: "PRO" },
  };
  const choice = details[plan] || details.GO;
  selectedPlanName.textContent = choice.label;
  selectedPlanPrice.textContent = choice.amount;
  selectedPlanChip.textContent = `${choice.label} · ${choice.amount}`;
  paymentPanel.classList.remove("hidden");
}

async function api(path, payload, method = "POST") {
  const headers = {
    "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
  };
  if (payload !== null) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      ...(payload === null ? {} : { body: JSON.stringify(payload) }),
    });
  } catch (error) {
    throw new Error("We couldn't reach StudyAI. Check your connection and try again.");
  }
  const raw = await response.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch (error) {
    data = {};
  }
  if (data.subscription) renderSubscription(data.subscription);
  if (!response.ok) {
    throw new Error(data.error || "Something went wrong on our side. Please try again.");
  }
  return data;
}

function setButtonLoading(button, loading, labelText) {
  if (!button) return;
  const label = button.querySelector(".button-label");
  if (loading) {
    if (label) {
      if (!button.dataset.defaultLabel) button.dataset.defaultLabel = label.textContent;
      label.textContent = labelText;
    } else {
      if (!button.dataset.defaultLabel) button.dataset.defaultLabel = button.textContent;
      button.textContent = labelText;
    }
    button.classList.add("is-loading");
    button.disabled = true;
    if (!button.querySelector(".button-spinner")) {
      const spinner = document.createElement("span");
      spinner.className = "button-spinner";
      spinner.setAttribute("aria-hidden", "true");
      button.insertBefore(spinner, button.firstChild);
    }
  } else {
    button.classList.remove("is-loading");
    button.disabled = false;
    button.querySelector(".button-spinner")?.remove();
    if (label && button.dataset.defaultLabel) label.textContent = button.dataset.defaultLabel;
    else if (!label && button.dataset.defaultLabel) button.textContent = button.dataset.defaultLabel;
  }
}

function showAnswer(text, isError = false, targetId = "answer-result", title) {
  const box = byId(targetId);
  if (!box) return;
  box.replaceChildren();
  box.classList.toggle("error", isError);
  const heading = document.createElement("strong");
  heading.textContent = isError ? "A little setup needed" : title || "Direct answer";
  const content = document.createElement("div");
  content.className = "response-content";
  if (isError) content.textContent = text;
  else renderRichText(content, text);
  box.append(heading, content);
  box.classList.remove("hidden");
}

/* ---------------------------------------------------------------- chat --- */

const chatThread = byId("chat-thread");
const userInitial = (document.querySelector(".avatar")?.textContent || "Y").trim().toUpperCase() || "Y";

function scrollChatToBottom() {
  if (!chatThread) return;
  chatThread.scrollTo({ top: chatThread.scrollHeight, behavior: "smooth" });
}

function appendInlineContent(parent, text) {
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|`[^`]+`)/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("**") || token.startsWith("__")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else {
      const emphasis = document.createElement("em");
      emphasis.textContent = token.slice(1, -1);
      parent.append(emphasis);
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
}

function renderRichText(container, text) {
  const lines = String(text ?? "").split(/\r?\n/);
  let list = null;
  let listType = null;

  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      list = null;
      listType = null;
      return;
    }
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const type = bullet ? "ul" : "ol";
      if (!list || listType !== type) {
        list = document.createElement(type);
        listType = type;
        container.append(list);
      }
      const item = document.createElement("li");
      appendInlineContent(item, bullet ? bullet[1] : numbered[1]);
      list.append(item);
      return;
    }
    list = null;
    listType = null;
    const paragraph = document.createElement("p");
    appendInlineContent(paragraph, line);
    container.append(paragraph);
  });
}

function appendChatMessage(role, text, { isError = false } = {}) {
  if (!chatThread) return null;
  const row = document.createElement("div");
  row.className = `message-row ${role === "user" ? "user-row" : "assistant-row"}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "user" ? userInitial : "S";

  const bubble = document.createElement("div");
  bubble.className = `message-bubble ${role === "user" ? "user-bubble" : "assistant-bubble"}`;

  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = role === "user" ? "You" : "StudyAI";
  bubble.append(label);

  if (isError) {
    const title = document.createElement("span");
    title.className = "message-error-title";
    title.textContent = "A little setup needed";
    bubble.append(title, document.createTextNode(text));
  } else {
    renderRichText(bubble, text);
  }

  if (!isError && role !== "user") {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "message-action";
    copy.textContent = "Copy answer";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = "Copied";
      } catch {
        copy.textContent = "Select and copy";
      }
      setTimeout(() => { copy.textContent = "Copy answer"; }, 1800);
    });
    actions.append(copy);
    bubble.append(actions);
  }

  row.append(avatar, bubble);
  chatThread.append(row);
  scrollChatToBottom();
  return row;
}

function showTypingIndicator() {
  if (!chatThread) return null;
  const row = document.createElement("div");
  row.className = "message-row assistant-row typing-row";

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = "S";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble assistant-bubble typing-bubble";

  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = "StudyAI";

  const dots = document.createElement("span");
  dots.className = "typing-dots";
  dots.setAttribute("role", "status");
  dots.setAttribute("aria-label", "StudyAI is thinking");
  for (let index = 0; index < 3; index += 1) dots.append(document.createElement("span"));

  bubble.append(label, dots);
  row.append(avatar, bubble);
  chatThread.append(row);
  scrollChatToBottom();
  return row;
}

/* ------------------------------------------------------------ composer --- */

const askQuestionInput = byId("ask-question");
const askTopicInput = byId("ask-topic");

function autoGrow(field) {
  if (!field) return;
  field.style.height = "auto";
  field.style.height = `${Math.min(field.scrollHeight, 320)}px`;
}

function updateQuestionCount() {
  const counter = byId("question-count");
  if (!counter || !askQuestionInput) return;
  const length = askQuestionInput.value.trim().length;
  counter.textContent = length ? `${length} characters` : "";
}

if (askQuestionInput) {
  askQuestionInput.addEventListener("input", () => {
    autoGrow(askQuestionInput);
    updateQuestionCount();
  });
  autoGrow(askQuestionInput);
  updateQuestionCount();
}

if (askTopicInput) {
  askTopicInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      askQuestionInput?.focus();
    }
  });
}

if (askQuestionInput) {
  askQuestionInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      askQuestionInput.closest("form")?.requestSubmit();
    }
  });
}

const clearQuestionButton = byId("clear-question");
if (clearQuestionButton) {
  clearQuestionButton.addEventListener("click", () => {
    if (askQuestionInput) {
      askQuestionInput.value = "";
      autoGrow(askQuestionInput);
    }
    if (askTopicInput) askTopicInput.value = "";
    updateQuestionCount();
    askQuestionInput?.focus();
  });
}

const promptChips = document.querySelectorAll(".prompt-chip");
promptChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    if (!askQuestionInput) return;
    const text = chip.dataset.prompt || chip.textContent.trim();
    askQuestionInput.value = text;
    askQuestionInput.dispatchEvent(new Event("input"));
    askQuestionInput.focus();
    askQuestionInput.setSelectionRange(text.length, text.length);
    if (askTopicInput && !askTopicInput.value.trim()) {
      const topic = text.includes("photosynthesis") ? "Biology" : text.includes("algebra") ? "Mathematics" : "General study";
      askTopicInput.value = topic;
    }
  });
});

const studyForm = byId("ask-form");
if (studyForm) {
  studyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = askQuestionInput?.value.trim();
    if (!question) {
      showToast("Write a question first, and your tutor will help.");
      askQuestionInput?.focus();
      return;
    }
    const button = event.currentTarget.querySelector("button[type=submit]");
    appendChatMessage("user", question);
    if (askQuestionInput) {
      askQuestionInput.value = "";
      autoGrow(askQuestionInput);
    }
    updateQuestionCount();
    setButtonLoading(button, true, "Thinking…");
    const typing = showTypingIndicator();
    try {
      const result = await api("/api/ask", {
        topic: askTopicInput?.value.trim() || "",
        question,
      });
      typing?.remove();
      appendChatMessage("assistant", result.explanation);
      await refreshDashboard();
    } catch (error) {
      typing?.remove();
      appendChatMessage("assistant", error.message, { isError: true });
    } finally {
      setButtonLoading(button, false);
    }
  });
}

let questionImageUrl = null;
const questionImageInput = byId("question-image");
const questionImagePreview = byId("question-image-preview");
const questionImagePreviewImg = byId("question-image-preview-img");
const questionImageError = byId("image-question-error");

function clearQuestionImage() {
  if (!questionImageInput || !questionImagePreview || !questionImagePreviewImg) return;
  if (questionImageUrl) URL.revokeObjectURL(questionImageUrl);
  questionImageUrl = null;
  questionImageInput.value = "";
  questionImagePreviewImg.removeAttribute("src");
  questionImagePreview.classList.add("hidden");
}

if (questionImageInput) {
  questionImageInput.addEventListener("change", () => {
    const [file] = questionImageInput.files;
    if (!questionImageError || !questionImagePreview || !questionImagePreviewImg) return;
    questionImageError.classList.add("hidden");
    if (questionImageUrl) URL.revokeObjectURL(questionImageUrl);
    questionImageUrl = null;
    questionImagePreview.classList.add("hidden");
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      questionImageInput.value = "";
      questionImageError.textContent = "Choose a PNG, JPG, or WebP image.";
      questionImageError.classList.remove("hidden");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      questionImageInput.value = "";
      questionImageError.textContent = "That image is too large. Choose an image under 5 MB.";
      questionImageError.classList.remove("hidden");
      return;
    }
    questionImageUrl = URL.createObjectURL(file);
    questionImagePreviewImg.src = questionImageUrl;
    questionImagePreview.classList.remove("hidden");
  });
}

const removeQuestionImageButton = byId("remove-question-image");
if (removeQuestionImageButton) removeQuestionImageButton.addEventListener("click", clearQuestionImage);

const imageQuestionForm = byId("image-question-form");
if (imageQuestionForm && questionImageInput) {
  imageQuestionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const [file] = questionImageInput.files;
    if (!file) {
      questionImageError.textContent = "Choose a picture of the question first.";
      questionImageError.classList.remove("hidden");
      questionImageInput.focus();
      return;
    }
    const button = event.currentTarget.querySelector("button[type=submit]");
    setButtonLoading(button, true, "Reading…");
    questionImageError.classList.add("hidden");
    try {
      const formData = new FormData();
      formData.append("image", file);
      formData.append("topic", askTopicInput?.value.trim() || "");
      formData.append("question", byId("image-question-prompt")?.value.trim() || "");
      const response = await fetch("/api/solve-image", {
        method: "POST",
        headers: {
          "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
        },
        body: formData,
      });
      const raw = await response.text();
      let result = {};
      try {
        result = raw ? JSON.parse(raw) : {};
      } catch (error) {
        result = {};
      }
      if (result.subscription) renderSubscription(result.subscription);
      if (!response.ok) throw new Error(result.error || "Couldn't read that image. Try another photo.");
      showAnswer(result.explanation, false, "image-answer-result");
      await refreshDashboard();
    } catch (error) {
      showAnswer(error.message, true, "image-answer-result");
    } finally {
      setButtonLoading(button, false);
    }
  });
}

let currentPractice = null;

function renderPractice(questions) {
  const container = byId("practice-result");
  container.replaceChildren();
  const form = document.createElement("form");
  form.id = "quiz-form";

  questions.forEach((question, questionIndex) => {
    const card = document.createElement("div");
    card.className = "quiz-question";
    const prompt = document.createElement("p");
    prompt.className = "quiz-prompt";
    prompt.textContent = `${questionIndex + 1}. ${question.question}`;
    const options = document.createElement("div");
    options.className = "quiz-options";
    question.options.forEach((option, optionIndex) => {
      const label = document.createElement("label");
      label.className = "quiz-option";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `answer-${questionIndex}`;
      input.value = String(optionIndex);
      input.required = true;
      const text = document.createElement("span");
      text.textContent = option;
      label.append(input, text);
      options.append(label);
    });
    card.append(prompt, options);
    form.append(card);
  });

  const submit = document.createElement("button");
  submit.className = "primary-button quiz-submit";
  submit.type = "submit";
  const submitLabel = document.createElement("span");
  submitLabel.className = "button-label";
  submitLabel.textContent = "Check my answers";
  submit.append(submitLabel);
  form.append(submit);
  form.addEventListener("submit", submitPractice);
  container.append(form);
  container.classList.remove("hidden");
}

const practiceForm = byId("practice-form");
if (practiceForm) {
  practiceForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const practiceTopic = byId("practice-topic");
    const topic = practiceTopic?.value.trim();
    if (!topic) {
      showToast("Add a topic first so your questions are just right.");
      practiceTopic?.focus();
      return;
    }
    const button = event.currentTarget.querySelector("button[type=submit]");
    setButtonLoading(button, true, "Creating questions…");
    try {
      currentPractice = await api("/api/practice", { topic });
      renderPractice(currentPractice.questions);
      await refreshDashboard();
    } catch (error) {
      showToast(error.message);
    } finally {
      setButtonLoading(button, false);
    }
  });
}

async function submitPractice(event) {
  event.preventDefault();
  if (!currentPractice) return;
  const form = event.currentTarget;
  const answers = currentPractice.questions.map((_, index) => {
    const selected = form.querySelector(`input[name="answer-${index}"]:checked`);
    return selected ? Number(selected.value) : -1;
  });
  if (answers.some((answer) => answer < 0)) {
    showToast("Choose one answer for each question before checking.");
    return;
  }
  const button = form.querySelector("button[type=submit]");
  setButtonLoading(button, true, "Checking…");
  try {
    const grade = await api("/api/grade", {
      id: currentPractice.id,
      answers,
    });
    form.querySelectorAll("input").forEach((input) => { input.disabled = true; });
    form.querySelectorAll(".quiz-question").forEach((card, index) => {
      const feedback = document.createElement("div");
      feedback.className = `quiz-feedback ${grade.results[index].correct ? "good" : "missed"}`;
      const title = grade.results[index].correct
        ? "That's right!"
        : `Not quite — the answer is ${currentPractice.questions[index].options[grade.results[index].answer]}.`;
      feedback.textContent = `${title} ${grade.results[index].explanation}`;
      card.append(feedback);
    });
    button.remove();
    const summary = document.createElement("div");
    summary.className = "response-box";
    summary.textContent = `You got ${grade.correct} out of ${grade.total} right. Every try helps you learn!`;
    form.append(summary);
    await refreshDashboard();
  } catch (error) {
    showToast(error.message);
  } finally {
    if (button.isConnected) setButtonLoading(button, false);
  }
}

let generatedNote = null;
let savedNotes = [];

function buildNoteContent(note, container) {
  const title = document.createElement("h3");
  title.textContent = note.title;
  const topic = document.createElement("div");
  topic.className = "note-topic";
  topic.textContent = note.topic;
  const summaryHeading = document.createElement("h4");
  summaryHeading.textContent = "The short version";
  const summary = document.createElement("p");
  summary.className = "note-summary";
  summary.textContent = note.summary;
  container.append(title, topic, summaryHeading, summary);

  const pointsHeading = document.createElement("h4");
  pointsHeading.textContent = "Key points";
  const points = document.createElement("ul");
  points.className = "note-points";
  note.key_points.forEach((point) => {
    const item = document.createElement("li");
    item.textContent = point;
    points.append(item);
  });
  container.append(pointsHeading, points);

  if (note.key_terms.length) {
    const termsHeading = document.createElement("h4");
    termsHeading.textContent = "Words to know";
    const terms = document.createElement("dl");
    terms.className = "note-terms";
    note.key_terms.forEach(({ term, definition }) => {
      const name = document.createElement("dt");
      name.textContent = term;
      terms.append(name);
      if (definition) {
        const meaning = document.createElement("dd");
        meaning.textContent = definition;
        terms.append(meaning);
      }
    });
    container.append(termsHeading, terms);
  }

  const remember = document.createElement("p");
  remember.className = "note-remember";
  const rememberLabel = document.createElement("strong");
  rememberLabel.textContent = "Remember: ";
  remember.append(rememberLabel, document.createTextNode(note.remember));
  container.append(remember);
}

function buildEmptyState(icon, title, copy) {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  const badge = document.createElement("span");
  badge.className = "empty-state-icon";
  badge.setAttribute("aria-hidden", "true");
  badge.textContent = icon;
  const heading = document.createElement("strong");
  heading.textContent = title;
  const detail = document.createElement("span");
  detail.textContent = copy;
  empty.append(badge, heading, detail);
  return empty;
}

function renderSavedNotes() {
  const list = byId("saved-notes-list");
  list.replaceChildren();
  byId("saved-notes-count").textContent = `${savedNotes.length} ${savedNotes.length === 1 ? "note" : "notes"}`;
  if (!savedNotes.length) {
    list.append(buildEmptyState(
      "✎",
      "No saved notes yet",
      "Notes you save will show up here for your next study session.",
    ));
    return;
  }

  savedNotes.forEach((note) => {
    const card = document.createElement("article");
    card.className = "saved-note-card";
    const content = document.createElement("div");
    content.className = "saved-note-content";
    buildNoteContent(note, content);
    const remove = document.createElement("button");
    remove.className = "note-delete-button";
    remove.type = "button";
    remove.dataset.noteId = note.id;
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove saved notes for ${note.title}`);
    card.append(content, remove);
    list.append(card);
  });
}

function renderGeneratedNote(note) {
  const result = byId("generated-notes");
  result.replaceChildren();
  const content = document.createElement("div");
  content.className = "generated-note-content";
  buildNoteContent(note, content);
  const save = document.createElement("button");
  save.className = "primary-button save-note-button";
  save.type = "button";
  const saveLabel = document.createElement("span");
  saveLabel.className = "button-label";
  saveLabel.textContent = "Save these notes";
  save.append(saveLabel);
  save.addEventListener("click", async () => {
    setButtonLoading(save, true, "Saving…");
    try {
      const saved = await api("/api/notes/save", { note });
      note.id = saved.id;
      savedNotes.unshift(note);
      renderSavedNotes();
      saveLabel.textContent = "Saved to your account";
      save.disabled = true;
    } catch (error) {
      setButtonLoading(save, false);
      showToast(error.message);
    }
  });
  result.append(content, save);
  result.classList.remove("hidden");
  byId("notes-error").classList.add("hidden");
}

const notesSource = byId("notes-source");
if (notesSource) {
  notesSource.addEventListener("input", (event) => {
    const notesCharCount = byId("notes-char-count");
    if (notesCharCount) notesCharCount.textContent = `${event.currentTarget.value.length.toLocaleString()} / 12,000`;
  });
}

const notesForm = byId("notes-form");
if (notesForm) {
  notesForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const source = byId("notes-source")?.value.trim();
    if (!source) {
      showToast("Paste lesson text or enter a topic first.");
      byId("notes-source")?.focus();
      return;
    }
    const button = event.currentTarget.querySelector("button[type=submit]");
    setButtonLoading(button, true, "Writing your notes…");
    const notesError = byId("notes-error");
    if (notesError) notesError.classList.add("hidden");
    try {
      const response = await api("/api/notes", {
        topic: byId("notes-topic")?.value.trim() || "",
        source,
      });
      generatedNote = {
        ...response.note,
        id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        created_at: new Date().toISOString(),
      };
      renderGeneratedNote(generatedNote);
    } catch (error) {
      if (notesError) {
        notesError.textContent = error.message;
        notesError.classList.remove("hidden");
      }
    } finally {
      setButtonLoading(button, false);
    }
  });
}

const savedNotesList = byId("saved-notes-list");
if (savedNotesList) {
  savedNotesList.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-note-id]");
    if (!button) return;
    try {
      await api(`/api/notes/${encodeURIComponent(button.dataset.noteId)}`, null, "DELETE");
      savedNotes = savedNotes.filter((note) => note.id !== button.dataset.noteId);
      renderSavedNotes();
      showToast("Saved note removed.");
    } catch (error) {
      showToast(error.message);
    }
  });
}

function renderTopicList(element, topics, predicate, emptyText, weakList = false) {
  element.replaceChildren();
  const selected = topics.filter(predicate);
  if (!selected.length) {
    element.append(buildEmptyState(
      weakList ? "✓" : "◎",
      weakList ? "Nothing flagged yet" : "No topics yet",
      emptyText,
    ));
    return;
  }
  selected.slice(0, 5).forEach((topic) => {
    const row = document.createElement("div");
    row.className = "topic-row";
    const dot = document.createElement("span");
    dot.className = `topic-dot${topic.weak ? " weak" : ""}`;
    const name = document.createElement("strong");
    name.textContent = topic.topic;
    const meta = document.createElement("span");
    meta.className = "topic-meta";
    meta.textContent = topic.practice_total
      ? `${topic.accuracy}% accuracy`
      : `${topic.questions_asked} ${topic.questions_asked === 1 ? "question" : "questions"}`;
    row.append(dot, name);
    if (weakList) {
      const revisit = document.createElement("span");
      revisit.className = "weak-label";
      revisit.textContent = "Revisit";
      row.append(revisit);
    }
    row.append(meta);
    element.append(row);
  });
}

async function refreshDashboard() {
  try {
    const response = await fetch("/api/dashboard");
    if (!response.ok) throw new Error("Couldn't load your progress.");
    const data = await response.json();
    renderSubscription(data.subscription);
    byId("topics-studied").textContent = data.topics_studied;
    byId("questions-asked").textContent = data.questions_asked;
    byId("practice-accuracy").textContent = `${data.accuracy}%`;
    const accuracyBar = byId("accuracy-bar");
    accuracyBar.style.width = `${data.accuracy}%`;
    const accuracyProgress = accuracyBar.parentElement;
    accuracyProgress.setAttribute("aria-valuenow", String(data.accuracy));
    accuracyProgress.setAttribute(
      "aria-valuetext",
      data.practice_total
        ? `${data.accuracy}% accuracy across ${data.practice_total} answers`
        : "No practice completed yet",
    );
    const masteryRing = byId("mastery-ring");
    if (masteryRing) {
      masteryRing.style.setProperty("--value", String(data.accuracy));
      const masteryValue = byId("mastery-value");
      if (masteryValue) masteryValue.textContent = `${data.accuracy}%`;
    }
    const weakTopicList = byId("weak-topic-list");
    if (weakTopicList) {
      renderTopicList(
        weakTopicList,
        data.topics,
        (topic) => topic.weak,
        "No topics to revisit yet. After a few practice tries, we'll flag any topic that needs another look.",
        true,
      );
    }
    const topicHistoryList = byId("topic-history-list");
    if (topicHistoryList) {
      renderTopicList(
        topicHistoryList,
        data.topics,
        () => true,
        "Your topics will show up here as you study.",
      );
    }
  } catch (error) {
    showToast(error.message);
  }
}

const todayLabel = byId("today-label");
if (todayLabel) {
  todayLabel.textContent = new Intl.DateTimeFormat(undefined, {
    weekday: "short", month: "short", day: "numeric",
  }).format(new Date());
}
const closeSetup = byId("close-setup");
if (closeSetup) {
  closeSetup.addEventListener("click", () => {
    const setupHelp = byId("setup-help");
    if (setupHelp) setupHelp.classList.remove("open");
  });
}
document.querySelector(".setup-banner a")?.addEventListener("click", (event) => {
  event.preventDefault();
  const setupHelp = byId("setup-help");
  if (setupHelp) setupHelp.classList.add("open");
});
document.addEventListener("keydown", (event) => {
  const setupHelp = byId("setup-help");
  if (event.key === "Escape" && setupHelp) setupHelp.classList.remove("open");
});

const navigationLinks = [...document.querySelectorAll(".nav-link")];
const navigationSections = navigationLinks
  .map((link) => {
    const targetId = link.getAttribute("href")?.replace(/^.*#/, "");
    return targetId ? document.getElementById(targetId) : null;
  })
  .filter((section) => section && !section.closest("dialog"));
const plansDialog = byId("plans-dialog");

function setActiveNavigation(sectionId) {
  navigationLinks.forEach((link) => {
    const isActive = link.hash === `#${sectionId}`;
    link.classList.toggle("active", isActive);
    if (isActive) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  });
}

navigationLinks.forEach((link) => {
  link.addEventListener("click", (event) => {
    if (link.hash === "#subscription" && plansDialog) {
      event.preventDefault();
      plansDialog.showModal();
      setActiveNavigation("subscription");
      return;
    }
    if (link.getAttribute("href")?.startsWith("/")) return;
    const sectionId = link.hash.slice(1);
    if (sectionId) setActiveNavigation(sectionId);
    navigationLockUntil = performance.now() + 1500;
  });
});

const paymentPlanButtons = document.querySelectorAll(".plan-button[data-plan]");
const paymentConfirmButton = byId("confirm-payment");
const cancelPaymentButton = byId("cancel-payment");
let selectedPlanForPayment = null;

paymentPlanButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const plan = button.dataset.plan;
    if (!plan) return;
    if (button.disabled) return;
    selectedPlanForPayment = plan;
    openPlanPayment(plan);
  });
});

if (cancelPaymentButton) {
  cancelPaymentButton.addEventListener("click", () => {
    selectedPlanForPayment = null;
    closePaymentPanel();
  });
}

if (paymentConfirmButton) {
  paymentConfirmButton.addEventListener("click", async () => {
    if (!selectedPlanForPayment) return;
    const button = paymentConfirmButton;
    setButtonLoading(button, true, "Activating…");
    try {
      const result = await api("/api/plan/activate", { plan: selectedPlanForPayment });
      renderSubscription(result.subscription);
      const planName = selectedPlanForPayment;
      const cards = document.querySelectorAll(".plan-card");
      cards.forEach((card) => {
        const current = card.querySelector(".plan-button");
        if (!current) return;
        const isCurrent = current.dataset.plan === planName;
        card.classList.toggle("is-current", isCurrent);
        current.classList.toggle("is-current-btn", isCurrent);
        current.disabled = isCurrent;
        current.textContent = isCurrent ? "Current plan" : "Pay & activate";
      });
      selectedPlanForPayment = null;
      closePaymentPanel();
      showToast(`Your ${planName} plan is active now.`);
    } catch (error) {
      showToast(error.message);
    } finally {
      setButtonLoading(button, false);
    }
  });
}

if (plansDialog) {
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && plansDialog.open) plansDialog.close();
  });
  byId("close-plans").addEventListener("click", () => plansDialog.close());
  plansDialog.addEventListener("click", (event) => {
    if (event.target === plansDialog) plansDialog.close();
  });
  plansDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    plansDialog.close();
  });
  plansDialog.addEventListener("close", () => {
    navigationLockUntil = 0;
    if (typeof updateActiveNavigationFromScroll === "function") updateActiveNavigationFromScroll();
  });
}

let navigationLockUntil = 0;
let navigationUpdatePending = false;
function updateActiveNavigationFromScroll() {
  navigationUpdatePending = false;
  if (!navigationSections.length || performance.now() < navigationLockUntil) return;
  const atPageEnd = window.scrollY + window.innerHeight
    >= document.documentElement.scrollHeight - 4;
  if (atPageEnd) {
    setActiveNavigation(navigationSections[navigationSections.length - 1].id);
    return;
  }
  const marker = window.innerHeight * 0.35;
  const currentSection = navigationSections.reduce((current, section) => {
    return section.getBoundingClientRect().top <= marker ? section : current;
  }, navigationSections[0]);
  setActiveNavigation(currentSection.id);
}
if (navigationSections.length) {
  window.addEventListener("scroll", () => {
    if (navigationUpdatePending) return;
    navigationUpdatePending = true;
    window.requestAnimationFrame(updateActiveNavigationFromScroll);
  }, { passive: true });
  window.addEventListener("resize", updateActiveNavigationFromScroll);
  updateActiveNavigationFromScroll();
}

async function loadSavedNotes() {
  const list = byId("saved-notes-list");
  if (!list) return;
  try {
    const response = await fetch("/api/notes");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Couldn't load your saved notes.");
    savedNotes = data.notes;
    renderSavedNotes();
  } catch (error) {
    showToast(error.message);
  }
}

if (document.getElementById("saved-notes-list") || document.getElementById("notes-form")) {
  loadSavedNotes();
}
if (document.getElementById("topics-studied") || document.getElementById("questions-asked")) {
  refreshDashboard();
}

const redeemBtn = document.getElementById("redeem-coupon");
if (redeemBtn) {
  redeemBtn.addEventListener("click", async () => {
    const code = document.getElementById("coupon-code").value.trim();
    const status = document.getElementById("coupon-status");
    try {
      const result = await fetch("/api/coupon/redeem", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const data = await result.json();
      if (!result.ok) throw new Error(data.error || "Failed");
      status.textContent = "Premium activated.";
    } catch (err) {
      status.textContent = err.message;
    }
  });
}
