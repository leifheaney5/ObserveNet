// Applies the saved colour theme before first paint (kept tiny and render-blocking on purpose).
(function () {
  try {
    var saved = localStorage.getItem("leifslab.theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {
    /* storage unavailable: follow the system theme */
  }
})();
