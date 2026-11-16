/* Apply the saved palette before styles load, so reloads do not flash. */
(() => {
  const system = matchMedia("(prefers-color-scheme: light)");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const valid = (value) => value === "light" || value === "dark";
  let preference;
  try {
    preference = localStorage.getItem("duplex-theme");
  } catch {}

  function apply() {
    const theme = valid(preference)
      ? preference
      : system.matches ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]').content =
      theme === "light" ? "#f2f2f3" : "#070708";
    window.dispatchEvent(new Event("duplex-theme"));
  }

  apply();
  system.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key !== "duplex-theme" && event.key !== null) return;
    preference = event.newValue;
    apply();
  });
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("theme-button").addEventListener("click", () => {
      preference = document.documentElement.dataset.theme === "dark"
        ? "light" : "dark";
      try {
        localStorage.setItem("duplex-theme", preference);
      } catch {}
      // Cross-fade between palettes instead of cutting the brightness.
      if (document.startViewTransition && !reducedMotion.matches) {
        // A quick second toggle skips the first cross-fade; that is expected, not an error.
        const transition = document.startViewTransition(apply);
        transition.ready.catch(() => {});
        transition.finished.catch(() => {});
      } else apply();
    });
  });
})();
