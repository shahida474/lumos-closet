// Lumos Closet -- tiny helpers. Most logic is server-side on purpose.
document.addEventListener("DOMContentLoaded", () => {
  // Auto-dismiss flash messages after 6 seconds.
  setTimeout(() => {
    document.querySelectorAll(".flash").forEach((el) => el.remove());
  }, 6000);
});
