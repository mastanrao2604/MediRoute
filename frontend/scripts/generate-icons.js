/**
 * generate-icons.js
 *
 * Generates PWA icons (192x192, 512x512, 512x512-maskable) from
 * public/favicon.svg using the built-in Node.js Canvas API (Node 18+)
 * via the 'canvas' npm package.
 *
 * Run once before building:
 *   node scripts/generate-icons.js
 *
 * If 'canvas' is not installed:
 *   npm install canvas --save-dev
 *
 * ALTERNATIVELY — use any of these free online tools:
 *   https://www.pwabuilder.com/imageGenerator
 *   https://realfavicongenerator.net
 *   https://favicon.io/favicon-converter/
 *
 * Place the generated files at:
 *   frontend/public/icons/icon-192.png
 *   frontend/public/icons/icon-512.png
 *   frontend/public/icons/icon-512-maskable.png  (same as icon-512.png for now)
 */

const fs = require('fs');
const path = require('path');

const iconsDir = path.join(__dirname, '..', 'public', 'icons');
if (!fs.existsSync(iconsDir)) fs.mkdirSync(iconsDir, { recursive: true });

let canvas;
try {
  canvas = require('canvas');
} catch {
  console.error('⚠  canvas package not found. Run: npm install canvas --save-dev');
  console.error('   OR manually place PNG icons in public/icons/');
  process.exit(1);
}

const { createCanvas, loadImage } = canvas;

async function generateIcon(size, outputName) {
  const c = createCanvas(size, size);
  const ctx = c.getContext('2d');

  // Background
  ctx.fillStyle = '#2563EB';
  ctx.beginPath();
  ctx.roundRect(0, 0, size, size, size * 0.2);
  ctx.fill();

  // Try to draw the SVG logo
  try {
    const svgPath = path.join(__dirname, '..', 'public', 'favicon.svg');
    if (fs.existsSync(svgPath)) {
      const img = await loadImage(svgPath);
      const padding = size * 0.15;
      ctx.drawImage(img, padding, padding, size - padding * 2, size - padding * 2);
    }
  } catch {
    // Fallback: draw "M" text
    ctx.fillStyle = '#ffffff';
    ctx.font = `bold ${size * 0.55}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('M', size / 2, size / 2);
  }

  const outPath = path.join(iconsDir, outputName);
  const buf = c.toBuffer('image/png');
  fs.writeFileSync(outPath, buf);
  console.log(`✓  Generated ${outputName} (${size}x${size})`);
}

(async () => {
  await generateIcon(192, 'icon-192.png');
  await generateIcon(512, 'icon-512.png');
  await generateIcon(512, 'icon-512-maskable.png');
  console.log('\nIcons saved to public/icons/');
  console.log('Replace with your final branded icons before Play Store submission.');
})();
