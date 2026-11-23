/* Wires up the demo buttons on the /design page (no inline scripts: the CSP blocks them). */
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-demo]");
  if (!btn) return;
  await window.app.ready;
  const kind = btn.dataset.demo;
  if (kind === "toast") window.app.toast(window.app.t("toast.network_error"), "error");
  if (kind === "success") window.app.toast(window.app.t("common.save"), "success");
  if (kind === "confirm") {
    const yes = await window.app.confirmDialog({
      title: window.app.t("dialog.delete_message.title"),
      text: window.app.t("dialog.delete_message.text"),
      confirmLabel: window.app.t("common.delete"),
      danger: true,
    });
    window.app.toast(yes ? "Confirmed" : "Cancelled");
  }
});
