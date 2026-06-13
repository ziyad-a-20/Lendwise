(function () {
  // No mouse repulsion on mobile/touch devices
  if (window.innerWidth < 768) return;

  const canvas = document.getElementById("auth-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let W,
    H,
    particles = [],
    mouse = { x: -9999, y: -9999 };
  const COUNT = 88,
    CONNECT_DIST = 140,
    MOUSE_REPEL = 100;

  function resize() {
    W = canvas.width = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }
  function rand(a, b) {
    return a + Math.random() * (b - a);
  }

  function Particle() {
    this.x = rand(0, W);
    this.y = rand(0, H);
    this.vx = rand(-0.18, 0.18);
    this.vy = rand(-0.18, 0.18);
    this.r = rand(0.8, 1.8);
    this.a = rand(0.15, 0.55);
  }
  Particle.prototype.update = function () {
    const dx = this.x - mouse.x;
    const dy = this.y - mouse.y;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < MOUSE_REPEL) {
      const f = ((MOUSE_REPEL - d) / MOUSE_REPEL) * 0.012;
      this.vx += (dx / d) * f;
      this.vy += (dy / d) * f;
    }
    this.vx *= 0.998;
    this.vy *= 0.998;
    this.x += this.vx;
    this.y += this.vy;
    if (this.x < 0) this.x = W;
    if (this.x > W) this.x = 0;
    if (this.y < 0) this.y = H;
    if (this.y > H) this.y = 0;
  };
  Particle.prototype.draw = function () {
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(200,191,168,${this.a})`;
    ctx.fill();
  };

  function drawConnections() {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < CONNECT_DIST) {
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = `rgba(200,191,168,${(1 - d / CONNECT_DIST) * 0.12})`;
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }
    }
  }

  function init() {
    particles = [];
    for (let i = 0; i < COUNT; i++) particles.push(new Particle());
  }
  function loop() {
    ctx.clearRect(0, 0, W, H);
    drawConnections();
    particles.forEach((p) => {
      p.update();
      p.draw();
    });
    requestAnimationFrame(loop);
  }

  window.addEventListener("resize", () => {
    resize();
    init();
  });
  window.addEventListener("mousemove", (e) => {
    mouse.x = e.clientX;
    mouse.y = e.clientY;
  });
  window.addEventListener("mouseleave", () => {
    mouse.x = -9999;
    mouse.y = -9999;
  });

  resize();
  init();
  loop();
})();
