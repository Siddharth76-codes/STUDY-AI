const authForm = document.querySelector(".auth-form");

document.querySelectorAll("[data-password-toggle]").forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const input = toggle.parentElement.querySelector("input");
    const showPassword = input.type === "password";
    input.type = showPassword ? "text" : "password";
    toggle.textContent = showPassword ? "Hide" : "Show";
    toggle.setAttribute("aria-label", `${showPassword ? "Hide" : "Show"} password`);
    toggle.setAttribute("aria-pressed", String(showPassword));
    input.focus();
  });
});

if (authForm && authForm.action.endsWith("/register")) {
  const password = authForm.querySelector("#password");
  const confirmation = authForm.querySelector("#password-confirm");
  const hint = document.getElementById("password-match-hint");

  function validatePasswordMatch() {
    const hasConfirmation = confirmation.value.length > 0;
    const matches = password.value === confirmation.value;
    confirmation.setCustomValidity(hasConfirmation && !matches ? "Passwords do not match." : "");
    hint.textContent = !hasConfirmation
      ? ""
      : matches
        ? "Passwords match."
        : "Passwords do not match yet.";
    hint.classList.toggle("is-valid", hasConfirmation && matches);
    hint.classList.toggle("is-invalid", hasConfirmation && !matches);
  }

  password.addEventListener("input", validatePasswordMatch);
  confirmation.addEventListener("input", validatePasswordMatch);
  authForm.addEventListener("submit", (event) => {
    validatePasswordMatch();
    if (!authForm.reportValidity()) event.preventDefault();
  });
}
