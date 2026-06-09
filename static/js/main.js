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

  // Handle all cover images: catalogue cover-wrap AND loan-cover
  document
    .querySelectorAll(".cover-wrap img, .loan-cover img")
    .forEach(function (img) {
      var wrap = img.closest(".cover-wrap, .loan-cover");
      var fallback = wrap ? wrap.querySelector(".book-cover-block") : null;

      function onLoad() {
        img.classList.add("loaded");
        if (fallback) fallback.style.display = "none";
        if (wrap) wrap.classList.add("cover-loaded");
      }
      function onError() {
        img.style.display = "none";
        if (fallback) {
          fallback.style.display = "flex";
          fallback.style.position = "relative";
        }
        if (wrap) wrap.classList.add("cover-loaded");
      }

      if (img.complete) {
        if (img.naturalWidth > 0) onLoad();
        else onError();
      } else {
        img.addEventListener("load", onLoad);
        img.addEventListener("error", onError);
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

// ── Toast ──────────────────────────────────────────────────────
function showToast(message, category) {
  category = category || "info";
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
  requestAnimationFrame(function () {
    el.style.animation = "slideInFlash 0.4s cubic-bezier(0.34,1.3,0.64,1) both";
  });
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
  if (drawer.classList.contains("open")) {
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

// data-close-drawer — CSP-safe drawer close
document.addEventListener("click", function (e) {
  if (e.target.closest("[data-close-drawer]")) closeDrawer();
});

// ── Form submit lock ───────────────────────────────────────────
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

(function () {
  if (document.getElementById("_lw_spin_kf")) return;
  var s = document.createElement("style");
  s.id = "_lw_spin_kf";
  s.textContent =
    "@keyframes spin{from{transform:rotate(0deg)}" +
    "to{transform:rotate(360deg)}}";
  document.head.appendChild(s);
})();

// ── CSRF ───────────────────────────────────────────────────────
function getCsrf() {
  var m = document.querySelector('meta[name="csrf-token"]');
  return m ? m.getAttribute("content") : "";
}

// ── Wishlist count live update ─────────────────────────────────
function updateWishlistCount() {
  fetch("/api/wishlist-count", {
    headers: { "X-Requested-With": "XMLHttpRequest" },
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (d) {
      var el = document.getElementById("wishlist-count-stat");
      if (el) el.textContent = d.count;
    })
    .catch(function () {});
}
