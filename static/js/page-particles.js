(function () {
  var canvas = document.getElementById("page-canvas");
  if (!canvas) return;
  canvas.style.cssText =
    "position:fixed;inset:0;z-index:0;" +
    "pointer-events:none;touch-action:none;";

  var ctx = canvas.getContext("2d");
  var STAR_COUNT = 110,
    MAX_DIST = 160,
    SPEED = 0.12;
  var W,
    H,
    stars = [];

  function resize() {
    W = canvas.width = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }
  function rand(a, b) {
    return a + Math.random() * (b - a);
  }
  function makeStar() {
    return {
      x: rand(0, W),
      y: rand(0, H),
      vx: rand(-SPEED, SPEED),
      vy: rand(-SPEED, SPEED),
      r: rand(0.6, 1.6),
      baseA: rand(0.25, 0.7),
      phase: rand(0, Math.PI * 2),
      speed: rand(0.004, 0.012),
    };
  }
  function init() {
    stars = [];
    for (var i = 0; i < STAR_COUNT; i++) stars.push(makeStar());
  }

  function drawConnections() {
    for (var i = 0; i < stars.length; i++) {
      for (var j = i + 1; j < stars.length; j++) {
        var dx = stars[i].x - stars[j].x;
        var dy = stars[i].y - stars[j].y;
        var d = Math.sqrt(dx * dx + dy * dy);
        if (d < MAX_DIST) {
          var alpha =
            (1 - d / MAX_DIST) * ((stars[i].baseA + stars[j].baseA) / 2) * 0.18;
          ctx.beginPath();
          ctx.moveTo(stars[i].x, stars[i].y);
          ctx.lineTo(stars[j].x, stars[j].y);
          ctx.strokeStyle = "rgba(200,191,168," + alpha + ")";
          ctx.lineWidth = 0.4;
          ctx.stroke();
        }
      }
    }
  }

  function drawStars() {
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      s.phase += s.speed;
      var a = s.baseA * (0.65 + 0.35 * Math.sin(s.phase));
      if (s.r > 1.2) {
        ctx.strokeStyle = "rgba(200,191,168," + a * 0.5 + ")";
        ctx.lineWidth = 0.4;
        var arm = s.r * 2.2;
        ctx.beginPath();
        ctx.moveTo(s.x - arm, s.y);
        ctx.lineTo(s.x + arm, s.y);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(s.x, s.y - arm);
        ctx.lineTo(s.x, s.y + arm);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(200,191,168," + a + ")";
      ctx.fill();
      s.x += s.vx;
      s.y += s.vy;
      if (s.x < -10) s.x = W + 10;
      if (s.x > W + 10) s.x = -10;
      if (s.y < -10) s.y = H + 10;
      if (s.y > H + 10) s.y = -10;
    }
  }

  function loop() {
    ctx.clearRect(0, 0, W, H);
    drawConnections();
    drawStars();
    requestAnimationFrame(loop);
  }

  window.addEventListener("resize", function () {
    resize();
    init();
  });
  resize();
  init();
  requestAnimationFrame(loop);
})();
