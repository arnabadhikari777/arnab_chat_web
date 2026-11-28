/* Small helpers for the sign-up / login pages. The server still checks everything. */
(function () {
  "use strict";

  // Show or hide a password
  document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.togglePassword);
      input.type = input.type === "password" ? "text" : "password";
    });
  });

  // Resend button countdown
  document.querySelectorAll("[data-countdown]").forEach((btn) => {
    let left = Number(btn.dataset.countdown);
    if (!left) return;
    const tick = () => {
      if (left <= 0) {
        btn.disabled = false;
        btn.textContent = btn.dataset.labelReady;
        return;
      }
      btn.textContent = btn.dataset.labelWait.replace("{seconds}", left);
      left -= 1;
      setTimeout(tick, 1000);
    };
    tick();
  });

  // Stop double taps on submit buttons
  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("[data-busy-on-submit]");
      if (btn) btn.setAttribute("aria-busy", "true");
    });
  });
})();
