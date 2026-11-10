/**
 * Floating LandIS chat. The iframe is served by this same host, so the
 * portal page does not need a cross-origin API call.
 *
 * <script src="https://your-host/web/widget-iframe.js"
 *         data-chat-url="https://your-host/web/"></script>
 *
 * **Created:** 29-10-2026 (UK style).
 */
(function () {
  const script = document.currentScript;
  const chatUrl =
    (script && script.getAttribute("data-chat-url")) ||
    new URL("index.html", script.src.replace(/widget-iframe\.js.*$/, "")).href;

  const style = document.createElement("style");
  style.textContent = `
    .landis-widget-launcher, .landis-widget-panel { font-family: system-ui, sans-serif; }
    .landis-widget-launcher { position: fixed; right: 1.25rem; bottom: 1.25rem; z-index: 9999; display: flex; align-items: center; gap: 0.5rem; }
    .landis-widget-hint, .landis-widget-toggle { border: 0; cursor: pointer; }
    .landis-widget-hint { background: #fff; color: #16352b; border-radius: 999px; padding: 0.45rem 0.8rem; box-shadow: 0 8px 24px rgba(0,0,0,.15); }
    .landis-widget-toggle { width: 3.25rem; height: 3.25rem; border-radius: 50%; background: #1f4e3d; color: #fff; font-weight: 700; box-shadow: 0 8px 24px rgba(0,0,0,.2); }
    .landis-widget-panel { position: fixed; right: 1.25rem; bottom: 5.5rem; width: min(400px, calc(100vw - 2rem)); height: min(640px, calc(100vh - 7rem)); background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 16px 40px rgba(0,0,0,.2); display: none; z-index: 9999; }
    .landis-widget-panel.is-open { display: block; }
    .landis-widget-panel iframe { width: 100%; height: 100%; border: 0; }
  `;
  document.head.appendChild(style);

  const launcher = document.createElement("div");
  launcher.className = "landis-widget-launcher";

  const hint = document.createElement("button");
  hint.type = "button";
  hint.className = "landis-widget-hint";
  hint.textContent = "Ask LandIS";

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "landis-widget-toggle";
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", "landis-widget-panel");
  toggle.setAttribute("aria-label", "Open LandIS chat");
  toggle.textContent = "Li";

  const panel = document.createElement("div");
  panel.id = "landis-widget-panel";
  panel.className = "landis-widget-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "LandIS chat");

  const iframe = document.createElement("iframe");
  iframe.src = chatUrl;
  iframe.title = "LandIS assistant";
  panel.appendChild(iframe);

  function setOpen(open) {
    panel.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  toggle.addEventListener("click", () => setOpen(!panel.classList.contains("is-open")));
  hint.addEventListener("click", () => setOpen(true));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
    }
  });

  launcher.appendChild(hint);
  launcher.appendChild(toggle);
  document.body.appendChild(panel);
  document.body.appendChild(launcher);
})();
