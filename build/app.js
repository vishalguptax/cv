(() => {
  const $ = (s) => document.querySelector(s);
  const root = document.documentElement;
  const toast = $("#toast");
  const say = (msg) => { toast.textContent = msg; clearTimeout(say.t); say.t = setTimeout(() => (toast.textContent = ""), 4000); };

  // Theme: follows the system until the visitor picks one.
  const themeBtn = $("#theme");
  const dark = () => (root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const label = () => themeBtn.setAttribute("aria-label", dark() ? "Switch to light theme" : "Switch to dark theme");
  themeBtn.hidden = false;
  label();
  themeBtn.addEventListener("click", () => {
    root.dataset.theme = dark() ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
    label();
  });

  // Share: native share sheet where available, otherwise copy the link.
  const shareBtn = $("#share");
  shareBtn.hidden = false;
  shareBtn.addEventListener("click", async () => {
    const data = { title: document.title, url: location.origin + "/" };
    try {
      if (navigator.share) { await navigator.share(data); return; }
      await navigator.clipboard.writeText(data.url);
      say("Link copied: " + data.url);
    } catch (e) {
      if (e && e.name === "AbortError") return;
      say("Copy this link: " + data.url);
    }
  });

  // "Present" roles: show how long, counted today.
  const now = new Date();
  document.querySelectorAll(".dur[data-since]").forEach((el) => {
    const [y, m] = el.dataset.since.split("-").map(Number);
    const n = (now.getFullYear() - y) * 12 + (now.getMonth() + 1 - m) + 1;
    if (n < 1) return;
    const yr = Math.floor(n / 12), mo = n % 12;
    el.textContent = [yr && yr + (yr > 1 ? " yrs" : " yr"), mo && mo + (mo > 1 ? " mos" : " mo")].filter(Boolean).join(" ");
  });

  // PDF preview: load the viewer only when opened, and only where the browser can show PDFs inline.
  const pdf = $("#pdf");
  pdf.addEventListener("toggle", () => {
    if (!pdf.open || pdf.dataset.loaded) return;
    pdf.dataset.loaded = "1";
    if (navigator.pdfViewerEnabled === false) return;
    const f = document.createElement("iframe");
    f.src = "/resume.pdf#view=FitH";
    f.title = "Résumé PDF preview";
    $("#pdf-box").prepend(f);
  });
  if (location.hash === "#pdf") pdf.open = true;

  // Highlight the section in view in the top bar.
  const links = [...document.querySelectorAll(".bar nav a")];
  if ("IntersectionObserver" in window && links.length) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        links.forEach((a) => a.setAttribute("aria-current", String(a.hash === "#" + e.target.id)));
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    links.forEach((a) => { const s = document.querySelector(a.hash); if (s) io.observe(s); });
  }
})();
