import { useEffect, useRef } from "react";

const CHARS =
  "01アイウエオカキクケコサシスセソタチツテトABCDEFGHIJKLMNOPQRSTUVWXYZ$%#@&";
const FONT_SIZE = 16;

// Purely decorative background canvas -- skipped entirely when the user
// has prefers-reduced-motion set, rather than just slowing it down.
export default function MatrixRain() {
  const canvasRef = useRef(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    let width, height, drops;

    function resize() {
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
      const columns = Math.ceil(width / FONT_SIZE);
      drops = Array(columns).fill(1);
    }
    resize();
    window.addEventListener("resize", resize);

    function draw() {
      ctx.fillStyle = "rgba(4, 8, 6, 0.1)";
      ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = "#00ff85";
      ctx.font = `${FONT_SIZE}px "Share Tech Mono", monospace`;
      for (let i = 0; i < drops.length; i++) {
        const char = CHARS[Math.floor(Math.random() * CHARS.length)];
        ctx.fillText(char, i * FONT_SIZE, drops[i] * FONT_SIZE);
        if (drops[i] * FONT_SIZE > height && Math.random() > 0.975) {
          drops[i] = 0;
        }
        drops[i]++;
      }
    }

    const interval = setInterval(draw, 50);
    return () => {
      clearInterval(interval);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={canvasRef} className="matrix-rain" aria-hidden="true" />;
}
