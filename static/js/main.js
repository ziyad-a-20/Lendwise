// ── Flash auto-dismiss ─────────────────────────────────────────
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".flash").forEach(function (el, i) {
    el.addEventListener("click", function () {
      dismiss(el);
    });
    setTimeout(
      function () {
        dismiss(el);
      },
      4500 + i * 200,
    );
  });

  // Cover image fade-in after load
  document
    .querySelectorAll(".cover-wrap img, .loan-cover img")
    .forEach(function (img) {
      if (img.complete && img.naturalWidth > 0) {
        img.classList.add("loaded");
      } else {
        img.addEventListener("load", function () {
          img.classList.add("loaded");
        });
      }
    });
});

function dismiss(el) {
  el.style.transition = "opacity 0.35s ease, transform 0.35s ease";
  el.style.opacity = "0";
  el.style.transform = "translateX(20px) scale(0.97)";
  setTimeout(function () {
    el.remove();
  }, 360);
}

// ── Toast notification ─────────────────────────────────────────
// Shows a flash-style toast without a page reload.
// category: 'success' | 'danger' | 'info' | 'warning'
function showToast(message, category) {
  category = category || "info";

  // Ensure the flash-stack container exists
  var stack = document.querySelector(".flash-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "flash-stack";
    document.body.appendChild(stack);
  }

  var iconMap = {
    success: "ti-circle-check",
    danger: "ti-circle-x",
    warning: "ti-alert-triangle",
    info: "ti-info-circle",
  };

  var el = document.createElement("div");
  el.className = "flash flash-" + category;
  el.innerHTML =
    '<i class="ti ' +
    (iconMap[category] || "ti-info-circle") +
    '"></i> ' +
    message;

  el.addEventListener("click", function () {
    dismiss(el);
  });
  stack.appendChild(el);

  // Trigger entrance animation (element must be in DOM first)
  requestAnimationFrame(function () {
    el.style.animation = "slideInFlash 0.4s cubic-bezier(0.34,1.3,0.64,1) both";
  });

  // Auto-dismiss after 4 seconds
  setTimeout(function () {
    dismiss(el);
  }, 4000);
}

// ── Password toggle ────────────────────────────────────────────
function togglePassword(inputId, btn) {
  var input = document.getElementById(inputId);
  var icon = btn.querySelector("i");
  if (!input) return;
  var hidden = input.type === "password";
  input.type = hidden ? "text" : "password";
  if (icon) icon.className = hidden ? "ti ti-eye-off" : "ti ti-eye";
}

// ── Mobile drawer ──────────────────────────────────────────────
function toggleDrawer() {
  var drawer = document.getElementById("nav-drawer");
  var backdrop = document.getElementById("nav-backdrop");
  var icon = document.getElementById("hamburger-icon");
  if (!drawer) return;
  var isOpen = drawer.classList.contains("open");
  if (isOpen) {
    closeDrawer();
  } else {
    drawer.classList.add("open");
    backdrop.classList.add("open");
    if (icon) icon.className = "ti ti-x";
    document.body.style.overflow = "hidden";
  }
}

function closeDrawer() {
  var drawer = document.getElementById("nav-drawer");
  var backdrop = document.getElementById("nav-backdrop");
  var icon = document.getElementById("hamburger-icon");
  if (!drawer) return;
  drawer.classList.remove("open");
  backdrop.classList.remove("open");
  if (icon) icon.className = "ti ti-menu-2";
  document.body.style.overflow = "";
}

window.addEventListener("resize", function () {
  if (window.innerWidth > 960) closeDrawer();
});

// ── Form submit lock (prevents double-submit) ──────────────────
function lockFormOnSubmit(formId, btnId) {
  var form = document.getElementById(formId);
  var btn = document.getElementById(btnId);
  if (!form || !btn) return;
  form.addEventListener("submit", function () {
    btn.disabled = true;
    btn.style.opacity = "0.6";
    btn.style.cursor = "not-allowed";
    var icon = btn.querySelector("i");
    if (icon) {
      icon.className = "ti ti-loader-2";
      icon.style.animation = "spin 0.8s linear infinite";
    }
  });
}

// Inject spin keyframe once
(function () {
  if (document.getElementById("_lw_spin_kf")) return;
  var s = document.createElement("style");
  s.id = "_lw_spin_kf";
  s.textContent =
    "@keyframes spin{from{transform:rotate(0deg)}" +
    "to{transform:rotate(360deg)}}";
  document.head.appendChild(s);
})();

// ── CSRF helper ────────────────────────────────────────────────
function getCsrf() {
  var m = document.querySelector('meta[name="csrf-token"]');
  return m ? m.getAttribute("content") : "";
}
